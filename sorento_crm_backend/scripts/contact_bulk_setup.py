#!/usr/bin/env python3
"""Give every approved dealer person in the owner's Excel the AI access of the example contact.

Lane CONTACT-BULK-SETUP. UAC: documentation/plans/contacts/contact-bulk-setup-4oct-acceptance-criteria.md.

Reads the sheet (rows whose APPROVAL cell is YES, up to 11 Name / Contact Number / Access Level
triples per row), matches people to EXISTING contacts by the digits of their phone number (a contact
is never created) and ADDS what they lack: access types, companies, market segment, image media,
agent grants. Three things are SET rather than added: the stock visibility override (mode and
include list become exactly the rule's locations), the escalation switch (OFF) and a disallowed
image media row (turned allowed). An existing agent grant is never touched. Nothing is ever removed.
Nothing is linked to a customer by --apply: links go through the review sheet below.

The target database is always explicit (--db-url); the script never falls back to the app settings.
Run from sorento_crm_backend/. Stdout carries counts only; names and phones go to the CSV files.

    # 1. dry run (default): writes nothing, produces the changes CSV + the missing-contacts CSV
    python scripts/contact_bulk_setup.py --excel /path/to/dealers.xlsx \\
        --db-url postgresql://user@localhost:5432/dbname --out /path/to/out

    # 2. apply (one transaction per contact, one audit row per changed contact)
    python scripts/contact_bulk_setup.py --excel /path/to/dealers.xlsx \\
        --db-url postgresql://user@localhost:5432/dbname --out /path/to/out \\
        --apply --actor-email someone@example.com

    # 3. customer links: write a review sheet (candidates per Excel row, empty Link column) ...
    python scripts/contact_bulk_setup.py --excel /path/to/dealers.xlsx \\
        --db-url postgresql://user@localhost:5432/dbname --links-review /path/to/review.xlsx
    #    ... CS ticks the Link cell (Y/YES/X/TRUE/1) on the right candidates, then:
    python scripts/contact_bulk_setup.py --excel /path/to/dealers.xlsx \\
        --db-url postgresql://user@localhost:5432/dbname \\
        --apply-links /path/to/reviewed.xlsx --actor-email someone@example.com
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import load_only  # noqa: E402

from app import audit_context  # noqa: E402
from app.models.access import (  # noqa: E402
    AccessAgent,
    ContactAgentAccess,
    RespondContact,
    RespondContactCustomer,
    StockVisibilityPolicy,
    respond_contact_access_types,
    respond_contact_market_segments,
)
from app.models.base import set_company_scope  # noqa: E402
from app.models.company import Company, RespondContactCompany  # noqa: E402
from app.models.inventory import Warehouse  # noqa: E402
from app.models.media import ContactMediaLimit  # noqa: E402
from app.models.order import Customer  # noqa: E402
from app.services import audit_service  # noqa: E402
from app.services import company_scope as _company_scope  # noqa: E402,F401 - registers the scope listeners
from app.services.contact_access_copy_service import (  # noqa: E402
    Names,
    StockOverride,
    locations_text,
    names_for,
    snapshots,
)

# The owner's rules (4 Oct 2026). Codes, not data.
RULES: dict[str, Any] = {
    "companies": {"Sorento Sdn Bhd": "SRT", "Mocha Sdn Bhd": "MCH"},
    "owner_types": {"SRT": "dealer", "MCH": "mocha_dealer"},
    "base_type": "end_user",
    "locations": {"SRT": ["BRW", "MWH"], "MCH": ["HQ", "MOCHA-WH"]},
    "stock_mode": "availability",
    "segment": "retail",
    "media_modality": "image",
    "agents": ["general_enquiries", "order_enquiries", "incoming_stock_enquiries"],
    "agent_valid_from": datetime(2026, 1, 1),
    "agent_valid_to": datetime(2026, 12, 31),
    "switches_on": ["chatbot_stock_allowed", "chatbot_eta_offset_applied"],
    "switches_off": ["escalation_allowed"],
}

CSV_COLUMNS = ["phone", "contact", "excel_rows", "facet", "before", "after", "status"]
MISSING_COLUMNS = ["phone", "name", "excel_row", "reason"]
TICKS = {"Y", "YES", "X", "TRUE", "1"}
MAX_CANDIDATES = 5
TRIPLES = 11
# Only the contact columns this script reads: prod can lag main, and a column the script never
# uses must not make it fail.
CONTACT_COLUMNS = (
    RespondContact.id, RespondContact.name, RespondContact.phone_number, RespondContact.chatbot_profile,
    RespondContact.chatbot_stock_allowed, RespondContact.notify_salesman, RespondContact.packing_list_allowed,
    RespondContact.chatbot_eta_offset_applied, RespondContact.escalation_allowed,
)


@dataclass
class Entry:
    row: int
    company_name: str
    companies: frozenset
    name: str
    phone: str
    level: str  # owner | employee


@dataclass
class Desired:
    phone: str
    name: str
    rows: list = field(default_factory=list)
    companies: set = field(default_factory=set)
    owner_companies: set = field(default_factory=set)


# ---------------------------------------------------------------- parse


def normalise_phone(value: Any) -> Optional[str]:
    """`+601` + 8 or 9 digits, or None when the value is not a Malaysian mobile number."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    digits = re.sub(r"\D", "", str(value))
    if digits.startswith("60"):
        pass
    elif digits.startswith("0"):
        digits = "6" + digits
    elif digits.startswith("1"):
        digits = "60" + digits  # a spreadsheet dropped the leading zero
    else:
        return None
    return "+" + digits if re.fullmatch(r"601\d{8,9}", digits) else None


def _mask(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "*" * max(len(text) - 3, 0) + text[-3:]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_workbook(path, *, rules: dict = RULES) -> tuple[list[Entry], list[dict]]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["working file"] if "working file" in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [_text(h).lower() for h in next(rows)]

    def col(pred) -> Optional[int]:
        return next((i for i, h in enumerate(header) if pred(h)), None)

    approval = col(lambda h: h.startswith("approval"))
    under = col(lambda h: h == "dealer registered under")
    company = col(lambda h: h == "company name")
    if approval is None or under is None:
        raise SystemExit("The sheet needs an APPROVAL column and a 'Dealer Registered Under' column.")
    triples = []
    for i, h in enumerate(header):
        if re.fullmatch(r"name( \d+)?", h):
            ok = i + 2 < len(header) and header[i + 1].startswith("contact") and header[i + 2].startswith("access level")
            if not ok:
                raise SystemExit(f"Column {i + 1}: a Name column is not followed by Contact and Access Level columns.")
            triples.append((i, i + 1, i + 2))

    by_name = {k.strip().lower(): v for k, v in rules["companies"].items()}
    entries: list[Entry] = []
    invalid: list[dict] = []
    for number, cells in enumerate(rows, start=2):
        cells = list(cells) + [None] * (len(header) - len(cells))
        if _text(cells[approval]).upper() != "YES":
            continue
        codes = set()
        for part in _text(cells[under]).split(","):
            if part.strip():
                if part.strip().lower() not in by_name:
                    raise SystemExit(f"Row {number}: unknown company name in 'Dealer Registered Under'.")
                codes.add(by_name[part.strip().lower()])
        if not codes:
            raise SystemExit(f"Row {number}: 'Dealer Registered Under' is empty.")
        for ni, pi, li in triples:
            name, raw_phone, level_text = _text(cells[ni]), cells[pi], _text(cells[li])
            if not name and _text(raw_phone) == "" and not level_text:
                continue
            phone = normalise_phone(raw_phone)
            lowered = level_text.lower()
            level = "owner" if lowered.startswith("business owner") else "employee" if lowered.startswith("employee") else None
            reason = None if phone and level else ("not a Malaysian mobile number" if not phone else "unknown access level")
            if reason:
                invalid.append({"row": number, "name": name, "raw_phone_masked": _mask(raw_phone), "reason": reason})
                continue
            entries.append(Entry(number, _text(cells[company]) if company is not None else "", frozenset(codes), name, phone, level))
    return entries, invalid


def desired_by_phone(entries: list[Entry]) -> dict[str, Desired]:
    out: dict[str, Desired] = {}
    for e in entries:
        d = out.setdefault(e.phone, Desired(e.phone, e.name))
        if e.row not in d.rows:
            d.rows.append(e.row)
        d.companies |= e.companies
        if e.level == "owner":
            d.owner_companies |= e.companies
    return out


# ---------------------------------------------------------------- read the DB


def _find_contacts(db, phones) -> dict[str, RespondContact]:
    """normalised phone -> contact, matched on the digits of respond_contacts.phone_number."""
    wanted = {p: re.sub(r"\D", "", p) for p in phones}
    if not wanted:
        return {}
    digits = func.regexp_replace(RespondContact.phone_number, r"\D", "", "g")
    by_digits: dict[str, RespondContact] = {}
    for contact, d in db.query(RespondContact, digits).options(load_only(*CONTACT_COLUMNS)).filter(digits.in_(set(wanted.values()))).order_by(RespondContact.id):
        by_digits.setdefault(d, contact)
    return {p: by_digits[d] for p, d in wanted.items() if d in by_digits}


def _resolve_world(db, rules: dict) -> dict:
    codes = set(rules["companies"].values())
    companies = {
        c.code: c for c in db.query(Company.id, Company.code).filter(Company.code.in_(codes))
    }
    if set(companies) != codes:
        raise SystemExit("A company in the rules is missing from the database.")
    warehouses: dict[str, dict[str, Warehouse]] = {}
    for code, wanted in rules["locations"].items():
        rows = db.query(Warehouse.id, Warehouse.warehouse_code).filter(
            Warehouse.company_id == companies[code].id, Warehouse.warehouse_code.in_(wanted)
        ).all()
        if {w.warehouse_code for w in rows} != set(wanted):
            raise SystemExit(f"A stock location of company {code} in the rules is missing from the database.")
        warehouses[code] = {w.warehouse_code: w for w in rows}
    agents = {a.code: a for a in db.query(AccessAgent.id, AccessAgent.code).filter(AccessAgent.code.in_(rules["agents"]))}
    if set(agents) != set(rules["agents"]):
        raise SystemExit("An agent in the rules is missing from the database.")
    return {"companies": companies, "warehouses": warehouses, "agents": agents}


# ---------------------------------------------------------------- plan


def _change(contact, d: Desired, facet, before, after, status, changed) -> dict:
    return {
        "contact_id": str(contact.id), "phone": d.phone, "contact": contact.name or "",
        "excel_rows": d.rows, "facet": facet, "before": before, "after": after,
        "status": status, "changed": changed,
    }


def _plan_contact(db, contact, snap, d: Desired, names: Names, world: dict, rules: dict, media_row) -> tuple[list[dict], dict]:
    """The change rows for one contact and the write instructions the apply step follows."""
    out: list[dict] = []
    todo: dict[str, Any] = {}
    company_code_of = {str(c.id): code for code, c in world["companies"].items()}

    def company_label(cid: str) -> str:
        return company_code_of.get(cid) or names.companies.get(cid, (None, None))[0] or "other company"

    # access types
    want_types = {rules["base_type"], *(rules["owner_types"][c] for c in d.owner_companies)}
    missing_types = sorted(want_types - set(snap.access_types))
    out.append(_change(contact, d, "access_types", list(snap.access_types), sorted(want_types | set(snap.access_types)),
                       "add" if missing_types else "already", bool(missing_types)))
    todo["types"] = missing_types

    # companies
    want_ids = {str(world["companies"][c].id) for c in d.companies}
    missing_ids = sorted(want_ids - set(snap.companies))
    out.append(_change(contact, d, "companies", sorted(company_label(c) for c in snap.companies),
                       sorted(company_label(c) for c in want_ids | set(snap.companies)),
                       "add" if missing_ids else "already", bool(missing_ids)))
    todo["companies"] = missing_ids

    # stock override: SET
    include_ids = sorted(str(world["warehouses"][c][code].id) for c in d.companies for code in rules["locations"][c])
    code_of = {str(w.id): w.warehouse_code for per in world["warehouses"].values() for w in per.values()}
    shown = Names(warehouses={**names.warehouses, **code_of})
    cur = snap.stock
    hide = cur.hide_zero if cur else False
    mode_changed = cur is None or cur.mode != rules["stock_mode"]
    loc_changed = cur is None or cur.include != tuple(include_ids) or cur.exclude is not None
    out.append(_change(contact, d, "stock_mode", cur.mode if cur else None, rules["stock_mode"],
                       "set" if mode_changed else "already", mode_changed))
    out.append(_change(contact, d, "stock_locations", locations_text(cur, shown),
                       locations_text(StockOverride(rules["stock_mode"], tuple(include_ids), None, hide), shown),
                       "set" if loc_changed else "already", loc_changed))
    todo["stock"] = (mode_changed or loc_changed, include_ids, hide)

    # market segment
    seg = rules["segment"]
    seg_missing = seg not in snap.segments
    out.append(_change(contact, d, "market_segments", list(snap.segments), sorted({*snap.segments, seg}),
                       "add" if seg_missing else "already", seg_missing))
    todo["segment"] = seg if seg_missing else None

    # media
    modality = rules["media_modality"]
    if media_row is None:
        out.append(_change(contact, d, "media", None, f"{modality} allowed", "add", True))
        todo["media"] = "create"
    elif not media_row.is_allowed:
        out.append(_change(contact, d, "media", f"{modality} not allowed", f"{modality} allowed", "set", True))
        todo["media"] = "allow"
    else:
        out.append(_change(contact, d, "media", f"{modality} allowed", f"{modality} allowed", "already", False))
        todo["media"] = None

    # agents: add the missing, never touch an existing grant
    adds, kept, held = [], [], []
    for code in rules["agents"]:
        agent = world["agents"][code]
        grant = snap.agents.get(str(agent.id))
        if grant is None:
            adds.append(agent)
            continue
        same = (grant.is_allowed, grant.valid_from, grant.valid_to) == (True, rules["agent_valid_from"], rules["agent_valid_to"])
        (held if same else kept).append(code)
    before_agents = sorted(g.agent_code for g in snap.agents.values())
    after_agents = sorted({*before_agents, *(a.code for a in adds)})
    status = "kept" if kept else "add" if adds else "already"
    out.append(_change(contact, d, "agent_access", before_agents,
                       after_agents + [f"kept as is: {c}" for c in sorted(kept)], status, bool(adds)))
    todo["agents"] = adds

    # switches
    todo["switches"] = {}
    for col_name, value in [*((c, True) for c in rules["switches_on"]), *((c, False) for c in rules["switches_off"])]:
        current = bool(getattr(contact, col_name))
        differs = current != value
        out.append(_change(contact, d, col_name, current, value, "set" if differs else "already", differs))
        if differs:
            todo["switches"][col_name] = value
    return out, todo


def _apply_contact(db, contact, d: Desired, todo: dict, rules: dict, actor_id: Optional[str]) -> None:
    cid = str(contact.id)
    if todo["types"]:
        db.execute(respond_contact_access_types.insert(), [{"contact_id": cid, "access_type_code": c} for c in todo["types"]])
    for company_id in todo["companies"]:
        db.add(RespondContactCompany(respond_contact_id=cid, company_id=company_id))
    changed, include_ids, hide = todo["stock"]
    if changed:
        row = db.query(StockVisibilityPolicy).filter(StockVisibilityPolicy.contact_id == cid).first()
        if row is None:
            row = StockVisibilityPolicy(contact_id=cid)
            db.add(row)
        row.mode = rules["stock_mode"]
        row.warehouse_ids = list(include_ids)
        row.excluded_warehouse_ids = None
        row.hide_zero_locations = hide
    if todo["segment"]:
        db.execute(respond_contact_market_segments.insert(), [{"contact_id": cid, "segment_code": todo["segment"]}])
    if todo["media"] == "create":
        db.add(ContactMediaLimit(contact_id=cid, modality=rules["media_modality"], is_allowed=True, updated_by=actor_id))
    elif todo["media"] == "allow":
        db.query(ContactMediaLimit).filter(
            ContactMediaLimit.contact_id == cid, ContactMediaLimit.modality == rules["media_modality"]
        ).update({"is_allowed": True, "updated_by": actor_id}, synchronize_session=False)
    for agent in todo["agents"]:
        db.add(ContactAgentAccess(
            respond_contact_id=cid, respond_contact_phone=contact.phone_number, respond_contact_name=contact.name,
            agent_id=str(agent.id), is_allowed=True, valid_from=rules["agent_valid_from"],
            valid_to=rules["agent_valid_to"], created_by=actor_id,
        ))
    for col_name, value in todo["switches"].items():
        setattr(contact, col_name, value)
    db.flush()


def run(db, path, *, rules: dict = RULES, apply: bool = False, actor_id: Optional[str] = None) -> dict:
    """Dry run unless `apply`. One savepoint (and commit) per contact; a failure rolls back that
    contact only."""
    entries, invalid = parse_workbook(path, rules=rules)
    desired = desired_by_phone(entries)
    found = _find_contacts(db, desired)
    world = _resolve_world(db, rules) if found else None

    missing = [
        {"phone": p, "name": d.name, "row": d.rows[0], "reason": "missing"} for p, d in desired.items() if p not in found
    ] + [{"phone": i["raw_phone_masked"], "name": i["name"], "row": i["row"], "reason": "invalid"} for i in invalid]

    changes: list[dict] = []
    failed: list[dict] = []
    changed_contacts = 0
    if found:
        contacts = list(found.values())
        snaps = snapshots(db, contacts)
        names = names_for(db, snaps.values())
        ids = [str(c.id) for c in contacts]
        media = {
            str(m.contact_id): m for m in db.query(ContactMediaLimit.contact_id, ContactMediaLimit.is_allowed).filter(
                ContactMediaLimit.contact_id.in_(ids), ContactMediaLimit.modality == rules["media_modality"])
        }
        actor = audit_context.AuditActor(actor_type="user", user_id=actor_id) if actor_id else None
        for phone, contact in found.items():
            d = desired[phone]
            rows, todo = _plan_contact(db, contact, snaps[str(contact.id)], d, names, world, rules, media.get(str(contact.id)))
            changes.extend(rows)
            touched = [r for r in rows if r["changed"]]
            if not apply or not touched:
                continue
            savepoint = db.begin_nested()
            try:
                if actor:
                    audit_context.stamp_actor(actor, db=db)
                _apply_contact(db, contact, d, todo, rules, actor_id)
                audit_service.record(
                    db,
                    event="contact.bulk_setup",
                    entity_type="respond_contacts",
                    entity_id=str(contact.id),
                    old_values={r["facet"]: r["before"] for r in touched},
                    new_values={r["facet"]: r["after"] for r in touched},
                    description="Contact bulk setup from the dealer Excel",
                )
                savepoint.commit()
                db.commit()
                changed_contacts += 1
            except Exception as exc:  # noqa: BLE001 - every failure becomes that contact's row
                savepoint.rollback()
                failed.append({"phone": phone, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                for r in rows:
                    r["status"] = "failed" if r["changed"] else r["status"]
    if not apply:
        changed_contacts = len({r["contact_id"] for r in changes if r["changed"]})
    counts = {
        "rows_yes": len({e.row for e in entries} | {i["row"] for i in invalid}),
        "people": len(entries) + len(invalid),
        "unique_phones": len(desired),
        "found": len(found),
        "missing": len([m for m in missing if m["reason"] == "missing"]),
        "invalid": len(invalid),
        "changed": changed_contacts,
        "failed": len(failed),
    }
    return {"apply": apply, "changes": changes, "missing": missing, "failed": failed, "counts": counts}


# ---------------------------------------------------------------- csv


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def write_csv(result: dict, changes_path) -> Path:
    """The changes CSV at `changes_path`, plus the missing list beside it as `<stem>_missing.csv`."""
    changes_path = Path(changes_path)
    with open(changes_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for c in result["changes"]:
            w.writerow([c["phone"], c["contact"], ";".join(str(r) for r in c["excel_rows"]), c["facet"],
                        _cell(c["before"]), _cell(c["after"]), c["status"]])
    missing_path = changes_path.with_name(f"{changes_path.stem}_missing.csv")
    with open(missing_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(MISSING_COLUMNS)
        for m in result["missing"]:
            w.writerow([m["phone"], m["name"], m["row"], m["reason"]])
    return missing_path


# ---------------------------------------------------------------- links


def _row_groups(entries: list[Entry]) -> dict[int, dict]:
    groups: dict[int, dict] = {}
    for e in entries:
        g = groups.setdefault(e.row, {"company_name": e.company_name, "companies": e.companies, "phones": []})
        if e.phone not in g["phones"]:
            g["phones"].append(e.phone)
    return groups


def _score(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold().strip(), b.casefold().strip()).ratio()


def links_review(db, path, out_path, *, rules: dict = RULES) -> int:
    """Write the review sheet: per Excel row and company, the top candidate customers. Links nothing."""
    entries, _ = parse_workbook(path, rules=rules)
    groups = _row_groups(entries)
    found = _find_contacts(db, {p for g in groups.values() for p in g["phones"]})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "links review"
    ws.append(["excel_row", "excel_company", "company_code", "debtor_code", "customer_name", "customer_id", "reason", "link"])
    written = 0
    for row, g in sorted(groups.items()):
        contact_ids = [str(found[p].id) for p in g["phones"] if p in found]
        for code in sorted(g["companies"]):
            company = db.query(Company.id).filter(Company.code == code).first()
            if company is None:
                continue
            customers = db.query(
                Customer.id, Customer.customer_code, Customer.customer_name
            ).filter(Customer.company_id == company.id).all()
            ranked = sorted(customers, key=lambda c: (-_score(g["company_name"], c.customer_name or ""), c.customer_code or ""))
            for cust in ranked[:MAX_CANDIDATES]:
                linked = 0
                if contact_ids:
                    linked = db.query(RespondContactCustomer).filter(
                        RespondContactCustomer.customer_id == cust.id, RespondContactCustomer.contact_id.in_(contact_ids)
                    ).count()
                reason = f"similarity {_score(g['company_name'], cust.customer_name or ''):.2f}"
                if linked:
                    reason = f"already linked ({linked} of {len(contact_ids)} contacts)"
                ws.append([row, g["company_name"], code, cust.customer_code, cust.customer_name, str(cust.id), reason, None])
                written += 1
    wb.save(out_path)
    return written


def apply_links(db, path, reviewed_path, *, actor_id: Optional[str], rules: dict = RULES) -> dict:
    """Link the ticked candidates to every found contact of that Excel row. Idempotent."""
    entries, _ = parse_workbook(path, rules=rules)
    groups = _row_groups(entries)
    found = _find_contacts(db, {p for g in groups.values() for p in g["phones"]})
    ws = openpyxl.load_workbook(reviewed_path, data_only=True).active
    header = [_text(c.value).lower() for c in ws[1]]
    idx = {h: i for i, h in enumerate(header)}
    actor = audit_context.AuditActor(actor_type="user", user_id=actor_id) if actor_id else None
    result = {"linked": 0, "skipped_existing": 0, "failed": []}
    for cells in ws.iter_rows(min_row=2, values_only=True):
        if _text(cells[idx["link"]]).upper() not in TICKS or cells[idx["excel_row"]] is None:
            continue
        row = int(cells[idx["excel_row"]])
        debtor = _text(cells[idx["debtor_code"]])
        customer = (
            db.query(Customer.id, Customer.company_id).filter(Customer.id == _text(cells[idx["customer_id"]])).first()
            if cells[idx["customer_id"]] else None
        )
        if customer is None or row not in groups:
            result["failed"].append({"excel_row": row, "debtor_code": debtor, "error": "customer or row no longer exists"})
            continue
        for phone in groups[row]["phones"]:
            contact = found.get(phone)
            if contact is None:
                continue
            cid = str(contact.id)
            if db.query(RespondContactCustomer).filter(
                RespondContactCustomer.contact_id == cid, RespondContactCustomer.customer_id == customer.id
            ).first():
                result["skipped_existing"] += 1
                continue
            savepoint = db.begin_nested()
            try:
                if actor:
                    audit_context.stamp_actor(actor, db=db)
                db.add(RespondContactCustomer(
                    contact_id=cid, customer_id=customer.id, company_id=customer.company_id,
                    is_primary=False, source="bulk_setup", linked_by=actor_id,
                ))
                db.flush()
                audit_service.record(
                    db,
                    event="contact.customer_linked_bulk",
                    entity_type="respond_contacts",
                    entity_id=cid,
                    old_values={"customer": None},
                    new_values={"customer": debtor},
                    description="Customer linked from the contact bulk setup review sheet",
                )
                savepoint.commit()
                db.commit()
                result["linked"] += 1
            except Exception as exc:  # noqa: BLE001
                savepoint.rollback()
                result["failed"].append({"excel_row": row, "debtor_code": debtor, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return result


# ---------------------------------------------------------------- cli


def _session(db_url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # hide_parameters: an error must never echo bound phone numbers or names.
    db = sessionmaker(bind=create_engine(db_url, hide_parameters=True))()
    set_company_scope(db, None)  # an admin tool: reads every company
    return db


def _actor_id(db, email: Optional[str]) -> str:
    from app.models.user import User  # noqa: F401

    if not email:
        raise SystemExit("--actor-email is required with --apply and --apply-links.")
    user = db.query(User.id).filter(func.lower(User.email) == email.strip().lower()).first()
    if user is None:
        raise SystemExit("No user has that --actor-email.")
    return str(user.id)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Contact bulk setup from the dealer Excel (see the module docstring).")
    ap.add_argument("--excel", required=True)
    ap.add_argument("--db-url", required=True, help="explicit target database; never defaulted")
    ap.add_argument("--apply", action="store_true", help="write; omit for a dry run")
    ap.add_argument("--out", default=".", help="directory for the CSV files (default: current directory)")
    ap.add_argument("--links-review", metavar="OUT.xlsx")
    ap.add_argument("--apply-links", metavar="REVIEWED.xlsx")
    ap.add_argument("--actor-email")
    ap.add_argument("--debug", action="store_true", help="re-raise errors with the full traceback")
    args = ap.parse_args(argv)

    db = _session(args.db_url)
    try:
        if args.links_review:
            n = links_review(db, args.excel, args.links_review)
            print(f"candidate rows written: {n}")
            return 0
        if args.apply_links:
            res = apply_links(db, args.excel, args.apply_links, actor_id=_actor_id(db, args.actor_email))
            print(f"linked: {res['linked']}  skipped_existing: {res['skipped_existing']}  failed: {len(res['failed'])}")
            return 1 if res["failed"] else 0
        actor_id = _actor_id(db, args.actor_email) if args.apply else None
        res = run(db, args.excel, apply=args.apply, actor_id=actor_id)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        write_csv(res, out / f"contact_bulk_setup_{'apply' if args.apply else 'dryrun'}_{stamp}.csv")
        for key, value in res["counts"].items():
            print(f"{key:14}: {value}")
        print("APPLIED" if args.apply else "DRY RUN - nothing written. Re-run with --apply.")
        return 1 if res["failed"] else 0
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        if args.debug:
            raise
        # Class and a short message only: SQL text and bound values can carry names and phones.
        msg = str(getattr(exc, "orig", None) or exc).splitlines()[0][:160] if str(exc) else ""
        print(f"error: {type(exc).__name__}: {msg}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
