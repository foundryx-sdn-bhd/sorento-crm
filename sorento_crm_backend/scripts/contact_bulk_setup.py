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

    # 4. auto links: the clear cases (one surviving, not held, same-named customer per Excel row and
    #    company) link themselves; the rest go to out/links-review-leftover.xlsx for the review above.
    #    Dry run first (writes out/auto-links-plan.csv), then add --apply --actor-email.
    python scripts/contact_bulk_setup.py --excel /path/to/dealers.xlsx \\
        --db-url postgresql://user@localhost:5432/dbname --out /path/to/out --auto-links
    docker exec <container> python scripts/contact_bulk_setup.py --excel /tmp/<file>.xlsx \\
        --db-url "$DATABASE_URL" --out /tmp/contact-bulk --auto-links
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402
from sqlalchemy import func, text  # noqa: E402
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
    # A formula cell has no cached value in a file saved by a script; fall back to its text.
    wf = openpyxl.load_workbook(path, data_only=False, read_only=True)
    wsf = wf["working file"] if "working file" in wf.sheetnames else wf.worksheets[0]
    rows = (
        [v if v is not None else f for v, f in zip(vals, forms)]
        for vals, forms in zip(ws.iter_rows(values_only=True), wsf.iter_rows(values_only=True))
    )
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


def _find_contacts(db, phones) -> tuple[dict[str, RespondContact], set[str]]:
    """(normalised phone -> contact, ambiguous phones), matched on the digits of
    respond_contacts.phone_number. Two contacts with the same digits: neither is returned."""
    wanted = {p: re.sub(r"\D", "", p) for p in phones}
    if not wanted:
        return {}, set()
    digits = func.regexp_replace(RespondContact.phone_number, r"\D", "", "g")
    by_digits: dict[str, list[RespondContact]] = {}
    for contact, d in db.query(RespondContact, digits).options(load_only(*CONTACT_COLUMNS)).filter(
        digits.in_(set(wanted.values()))
    ).order_by(RespondContact.id):
        by_digits.setdefault(d, []).append(contact)
    found = {p: by_digits[d][0] for p, d in wanted.items() if len(by_digits.get(d, [])) == 1}
    ambiguous = {p for p, d in wanted.items() if len(by_digits.get(d, [])) > 1}
    return found, ambiguous


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
    cid = contact.id  # `contact` is plain (id, name, phone_number), never an expired entity
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
    if todo["switches"]:
        db.query(RespondContact).filter(RespondContact.id == cid).update(
            todo["switches"], synchronize_session=False
        )
    db.flush()


def run(db, path, *, rules: dict = RULES, apply: bool = False, actor_id: Optional[str] = None) -> dict:
    """Dry run unless `apply`. One savepoint (and commit) per contact; a failure rolls back that
    contact only."""
    entries, invalid = parse_workbook(path, rules=rules)
    desired = desired_by_phone(entries)
    found, ambiguous = _find_contacts(db, desired)
    world = _resolve_world(db, rules) if found else None

    missing = [
        {"phone": p, "name": d.name, "row": d.rows[0], "reason": "missing"} for p, d in desired.items() if p not in found and p not in ambiguous
    ] + [
        {"phone": p, "name": desired[p].name, "row": desired[p].rows[0], "reason": "ambiguous"} for p in sorted(ambiguous)
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
        # Plan every contact BEFORE the first commit, and hold plain values from here on: a commit
        # expires the loaded entities and touching one reloads it with every mapped column.
        plans = []
        for phone, contact in found.items():
            cid = str(contact.id)
            plain = SimpleNamespace(id=cid, name=contact.name, phone_number=contact.phone_number)
            rows, todo = _plan_contact(db, contact, snaps[cid], desired[phone], names, world, rules, media.get(cid))
            changes.extend(rows)
            plans.append((phone, plain, desired[phone], rows, todo))
        for phone, contact, d, rows, todo in plans:
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
                    entity_id=contact.id,
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
        "ambiguous": len(ambiguous),
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


def _safe(value: Any) -> Any:
    """A spreadsheet runs a cell that starts with = + - @ tab or CR as a formula: prefix a quote."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _unsafe(value: Any) -> str:
    """Undo `_safe` on a cell read back from a sheet this script wrote."""
    text = _text(value)
    return text[1:] if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@", "\t", "\r") else text


def _private(path) -> None:
    os.chmod(path, 0o600)


def write_csv(result: dict, changes_path) -> Path:
    """The changes CSV at `changes_path`, plus the missing list beside it as `<stem>_missing.csv`."""
    changes_path = Path(changes_path)
    with open(changes_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for c in result["changes"]:
            w.writerow([c["phone"], _safe(c["contact"]), ";".join(str(r) for r in c["excel_rows"]), c["facet"],
                        _safe(_cell(c["before"])), _safe(_cell(c["after"])), c["status"]])
    missing_path = changes_path.with_name(f"{changes_path.stem}_missing.csv")
    with open(missing_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(MISSING_COLUMNS)
        for m in result["missing"]:
            w.writerow([m["phone"], _safe(m["name"]), m["row"], m["reason"]])
    _private(changes_path)
    _private(missing_path)
    return missing_path


# ---------------------------------------------------------------- links

# Copied from PR #1475 (head dff1718a1), app/services/customer_merge.py `_NAME_SUFFIXES` and
# `normalise_name`, and app/services/customer_identity.py `SURVIVOR_ORDER_SQL`: that PR is not
# on main and this one file is what runs against prod, so it carries its own copy of the rules.
_NAME_SUFFIXES = frozenset(
    {
        "sdn", "bhd", "berhad", "sendirian", "plt", "llp", "m",
        "enterprise", "enterprises", "ent", "trading", "co", "company",
    }
)
CASH_CODE_PREFIXES = ("301-",)


def normalise_name(name: Optional[str]) -> str:
    """The core of a business name (PR #1475 dff1718a1 customer_merge.normalise_name)."""

    def _words(text_: str) -> list[str]:
        return re.sub(r"[^a-z0-9]+", " ", text_).split()

    raw = (name or "").lower()
    text_ = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", raw)
    text_ = re.sub(r"[\[(][^\])]*$", " ", text_)
    text_ = re.split(r"\s+-\s+", text_, maxsplit=1)[0]
    text_ = text_.split("&", 1)[0]
    words = _words(text_)
    if words and words[0] == "the":
        words = words[1:]
    while words and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return " ".join(words) or " ".join(_words(raw))


def _merge_flags(db, keys: set) -> dict[str, tuple[str, str]]:
    """customer id -> (survives_merge, held), Y/N, for every member of the (company, code) groups
    named in `keys`. One read-only query. Group = company + lower(btrim(code)); survivor order is
    SURVIVOR_ORDER_SQL: code-form integration ref, then any ref, then oldest created_at, then id."""
    keys = {k for k in keys if k[1]}
    if not keys:
        return {}
    ordered = sorted(keys)
    rows = db.execute(
        text(
            """
            SELECT c.id::text, c.company_id::text, lower(btrim(c.customer_code)), c.customer_name, c.created_at,
              EXISTS (
                SELECT 1 FROM integration_references r
                WHERE r.entity_type = 'customers' AND r.entity_id = c.id::text
                  AND lower(btrim(regexp_replace(r.source_ref, '^[^:]*:', ''))) = lower(btrim(c.customer_code))
              ),
              EXISTS (
                SELECT 1 FROM integration_references r
                WHERE r.entity_type = 'customers' AND r.entity_id = c.id::text
              )
            FROM customers c
            WHERE (c.company_id::text, lower(btrim(c.customer_code))) IN (
              SELECT * FROM unnest(CAST(:cids AS text[]), CAST(:codes AS text[])))
            """
        ),
        {"cids": [k[0] for k in ordered], "codes": [k[1] for k in ordered]},
    ).all()
    groups: dict[tuple, list] = {}
    for row in rows:
        groups.setdefault((row[1], row[2]), []).append(row)
    out: dict[str, tuple[str, str]] = {}
    for (_, code), members in groups.items():
        members.sort(key=lambda m: (not m[5], not m[6], m[4] is None, m[4] or datetime.min, m[0]))
        survivor = members[0]
        held = (
            len(members) > 1
            and not code.startswith(CASH_CODE_PREFIXES)
            and any(normalise_name(m[3]) != normalise_name(survivor[3]) for m in members[1:])
        )
        for m in members:
            out[m[0]] = ("Y" if m is survivor else "N", "Y" if held else "N")
    return out


def _row_groups(entries: list[Entry]) -> dict[int, dict]:
    groups: dict[int, dict] = {}
    for e in entries:
        g = groups.setdefault(e.row, {"company_name": e.company_name, "companies": e.companies, "phones": []})
        if e.phone not in g["phones"]:
            g["phones"].append(e.phone)
    return groups


def _score(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold().strip(), b.casefold().strip()).ratio()


REVIEW_HEADER = ["excel_row", "excel_company", "company_code", "debtor_code", "customer_name", "customer_id",
                 "reason", "link", "survives_merge", "held"]


def _candidates(db, groups: dict, found: dict) -> list[dict]:
    """Per Excel row and company, the top candidate customers with their merge flags: one dict each
    (`line` is the review-sheet row), survivors first inside each Excel row block."""
    picked: list[tuple] = []
    for row, g in sorted(groups.items()):
        contact_ids = [str(found[p].id) for p in g["phones"] if p in found]
        for code in sorted(g["companies"]):
            company = db.query(Company.id).filter(Company.code == code).first()
            if company is None:
                continue
            customers = db.query(
                Customer.id, Customer.customer_code, Customer.customer_name, Customer.company_id
            ).filter(Customer.company_id == company.id).all()
            ranked = sorted(customers, key=lambda c: (-_score(g["company_name"], c.customer_name or ""), c.customer_code or ""))
            picked.extend((row, g, code, contact_ids, cust) for cust in ranked[:MAX_CANDIDATES])
    # The merge groups of ALL candidates in one query.
    flags = _merge_flags(db, {(str(c[4].company_id), (c[4].customer_code or "").strip().lower()) for c in picked})
    blocks: dict[int, list] = {}
    for row, g, code, contact_ids, cust in picked:
        linked = 0
        if contact_ids:
            linked = db.query(RespondContactCustomer).filter(
                RespondContactCustomer.customer_id == cust.id, RespondContactCustomer.contact_id.in_(contact_ids)
            ).count()
        reason = f"similarity {_score(g['company_name'], cust.customer_name or ''):.2f}"
        if linked:
            reason = f"already linked ({linked} of {len(contact_ids)} contacts)"
        survives, held = flags.get(str(cust.id), ("Y", "N"))
        blocks.setdefault(row, []).append({
            "row": row, "code": code, "company_name": g["company_name"], "customer_id": str(cust.id),
            "customer_code": cust.customer_code, "customer_name": cust.customer_name, "company_id": cust.company_id,
            "survives": survives, "held": held, "linked": linked, "contact_ids": contact_ids,
            "line": [row, _safe(g["company_name"]), code, _safe(cust.customer_code), _safe(cust.customer_name),
                     str(cust.id), reason, None, survives, held],
        })
    out: list[dict] = []
    for row in sorted(blocks):
        out.extend(sorted(blocks[row], key=lambda c: c["survives"] != "Y"))  # survivors first, order kept otherwise
    return out


def _write_review(path, lines: list) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "links review"
    ws.append(REVIEW_HEADER)
    for line in lines:
        ws.append(line)
    wb.save(path)
    _private(path)


def links_review(db, path, out_path, *, rules: dict = RULES) -> int:
    """Write the review sheet: per Excel row and company, the top candidate customers. Links nothing."""
    entries, _ = parse_workbook(path, rules=rules)
    groups = _row_groups(entries)
    found, _ambiguous = _find_contacts(db, {p for g in groups.values() for p in g["phones"]})
    lines = [c["line"] for c in _candidates(db, groups, found)]
    _write_review(out_path, lines)
    return len(lines)


def _link_contact(db, actor, actor_id, cid: str, customer, result: dict, failure: dict) -> None:
    """Link one contact to one customer: existing pair skipped, own savepoint, one audit row."""
    if db.query(RespondContactCustomer).filter(
        RespondContactCustomer.contact_id == cid, RespondContactCustomer.customer_id == customer.id
    ).first():
        result["skipped_existing"] += 1
        return
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
            new_values={"customer": customer.customer_code},
            description="Customer linked from the contact bulk setup review sheet",
        )
        savepoint.commit()
        db.commit()
        result["linked"] += 1
    except Exception as exc:  # noqa: BLE001
        savepoint.rollback()
        result["failed"].append({**failure, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})


def auto_links(db, path, out_dir, *, apply: bool = False, actor_id: Optional[str] = None, rules: dict = RULES) -> dict:
    """Link the clear cases. In a block (Excel row, company) EVERY customer of that company whose
    normalised name equals the Excel company's, that survives the debtor-code merge and is not held,
    is linked to every found contact of the row (all customers searched, no candidate cap). A block
    whose contacts are all linked already is left alone; a block with no such customer is
    needs_human and its candidate rows (held exact matches marked 'held') land in
    links-review-leftover.xlsx. Dry run by default."""
    entries, _ = parse_workbook(path, rules=rules)
    groups = _row_groups(entries)
    found, _ambiguous = _find_contacts(db, {p for g in groups.values() for p in g["phones"]})
    names = {p: c.name or "" for p, c in found.items()}  # plain values: commits expire the entities
    ids = {p: str(c.id) for p, c in found.items()}
    blocks: dict[tuple, list] = {}
    for cand in _candidates(db, groups, found):
        blocks.setdefault((cand["row"], cand["code"]), []).append(cand)

    result: dict = {"auto": [], "already_linked": 0, "needs_human": 0, "linked": 0, "skipped_existing": 0, "failed": []}
    leftover: list = []
    company_by_code = {str(c.code): c.id for c in db.query(Company.id, Company.code)}
    customers_of: dict[str, list] = {}  # no candidate cap here: every customer of the company is searched
    exact_of: dict[tuple, list] = {}
    for row, g in sorted(groups.items()):
        wanted = normalise_name(g["company_name"])
        for code in sorted(g["companies"]):
            if code not in company_by_code:
                continue
            if code not in customers_of:
                customers_of[code] = db.query(
                    Customer.id, Customer.customer_code, Customer.customer_name, Customer.company_id
                ).filter(Customer.company_id == company_by_code[code]).all()
            exact_of[(row, code)] = [c for c in customers_of[code] if normalise_name(c.customer_name) == wanted]
    flags = _merge_flags(db, {(str(c.company_id), (c.customer_code or "").strip().lower()) for v in exact_of.values() for c in v})

    for (row, code), exact in exact_of.items():
        g = groups[row]
        contact_ids = [ids[p] for p in g["phones"] if p in found]
        if not contact_ids:
            continue
        cands = blocks.get((row, code), [])
        linked_ids = {
            str(c) for (c,) in db.query(RespondContactCustomer.contact_id).filter(
                RespondContactCustomer.contact_id.in_(contact_ids),
                RespondContactCustomer.customer_id.in_([c["customer_id"] for c in cands] + [str(c.id) for c in exact]),
            )
        }
        if linked_ids >= set(contact_ids):
            result["already_linked"] += 1
            continue
        clear = [c for c in exact if flags.get(str(c.id), ("Y", "N")) == ("Y", "N")]
        held = {str(c.id): c for c in exact if flags.get(str(c.id), ("Y", "N"))[1] == "Y"}
        if not clear:
            result["needs_human"] += 1
            shown = set()
            for c in cands:
                if c["customer_id"] in held:
                    c["line"][6] = "held"
                shown.add(c["customer_id"])
                leftover.append(c["line"])
            for cid_, c in held.items():
                if cid_ not in shown:
                    leftover.append([row, _safe(g["company_name"]), code, _safe(c.customer_code), _safe(c.customer_name),
                                     cid_, "held", None, flags[cid_][0], "Y"])
            continue
        for hit in clear:
            result["auto"].append({
                "excel_row": row, "company_code": code, "customer_id": str(hit.id), "company_id": hit.company_id,
                "debtor_code": hit.customer_code, "customer_name": hit.customer_name,
                "contacts": [p for p in g["phones"] if p in found],
            })

    actor = audit_context.AuditActor(actor_type="user", user_id=actor_id) if actor_id else None
    plan_rows = []
    for a in result["auto"]:
        customer = SimpleNamespace(id=a["customer_id"], company_id=a["company_id"], customer_code=a["debtor_code"])
        for phone in a["contacts"]:
            status = "would link"
            if apply:
                before = (result["linked"], result["skipped_existing"])
                _link_contact(db, actor, actor_id, ids[phone], customer, result,
                              {"excel_row": a["excel_row"], "debtor_code": a["debtor_code"]})
                status = "linked" if result["linked"] > before[0] else "existing" if result["skipped_existing"] > before[1] else "failed"
            plan_rows.append([a["excel_row"], a["company_code"], _safe(a["debtor_code"]), _safe(a["customer_name"]),
                              _safe(names[phone]), status])
    out_dir = Path(out_dir)
    plan_path = out_dir / "auto-links-plan.csv"
    with open(plan_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["excel_row", "company_code", "debtor_code", "customer_name", "contact", "status"])
        w.writerows(plan_rows)
    _private(plan_path)
    _write_review(out_dir / "links-review-leftover.xlsx", leftover)
    return result


def apply_links(db, path, reviewed_path, *, actor_id: Optional[str], rules: dict = RULES) -> dict:
    """Link the ticked candidates to every found contact of that Excel row. Idempotent."""
    entries, _ = parse_workbook(path, rules=rules)
    groups = _row_groups(entries)
    found, _ambiguous = _find_contacts(db, {p for g in groups.values() for p in g["phones"]})
    company_ids = {str(c.code): str(c.id) for c in db.query(Company.id, Company.code)}

    def row_company_ids(group: dict) -> set:
        return {company_ids[c] for c in group["companies"] if c in company_ids}

    contact_ids = {p: str(c.id) for p, c in found.items()}  # plain ids: commits expire the entities
    ws = openpyxl.load_workbook(reviewed_path, data_only=True).active
    header = [_text(c.value).lower() for c in ws[1]]
    idx = {h: i for i, h in enumerate(header)}
    actor = audit_context.AuditActor(actor_type="user", user_id=actor_id) if actor_id else None
    result = {"linked": 0, "skipped_existing": 0, "failed": []}
    for cells in ws.iter_rows(min_row=2, values_only=True):
        if _text(cells[idx["link"]]).upper() not in TICKS or cells[idx["excel_row"]] is None:
            continue
        row = int(cells[idx["excel_row"]])
        debtor = _unsafe(cells[idx["debtor_code"]])
        customer = None
        try:
            cust_id = str(uuid.UUID(_text(cells[idx["customer_id"]])))
            customer = db.query(Customer.id, Customer.customer_code, Customer.company_id).filter(Customer.id == cust_id).first()
        except ValueError:
            pass
        group = groups.get(row)
        problem = None
        if customer is None or group is None:
            problem = "customer or row no longer exists"
        elif str(customer.company_id) not in row_company_ids(group):
            problem = "customer belongs to a company outside this Excel row"
        elif (customer.customer_code or "") != debtor:
            problem = "debtor code does not match the customer"
        elif _unsafe(cells[idx["excel_company"]]) != group["company_name"]:
            problem = "company name does not match the Excel row"
        if problem:
            result["failed"].append({"excel_row": row, "debtor_code": debtor, "error": problem})
            continue
        for phone in groups[row]["phones"]:
            contact = found.get(phone)
            if contact is None:
                continue
            _link_contact(db, actor, actor_id, contact_ids[phone], customer, result,
                          {"excel_row": row, "debtor_code": debtor})
    return result


# ---------------------------------------------------------------- cli


def _session(db_url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # hide_parameters: an error must never echo bound phone numbers or names.
    db = sessionmaker(bind=create_engine(db_url, hide_parameters=True), expire_on_commit=False)()
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
    ap.add_argument("--out", help="directory for the CSV files (required for a dry run or --apply)")
    ap.add_argument("--links-review", metavar="OUT.xlsx")
    ap.add_argument("--apply-links", metavar="REVIEWED.xlsx")
    ap.add_argument("--auto-links", action="store_true", help="link the clear contact to customer cases (dry run unless --apply)")
    ap.add_argument("--actor-email")
    ap.add_argument("--debug", action="store_true", help="re-raise errors with the full traceback")
    args = ap.parse_args(argv)
    if not (args.links_review or args.apply_links) and not args.out:
        ap.error("--out is required for a dry run or --apply")
    if args.auto_links and not args.out:
        ap.error("--out is required with --auto-links")
    if (args.apply or args.apply_links) and not args.actor_email:
        ap.error("--actor-email is required with --apply and --apply-links")

    db = _session(args.db_url)
    try:
        if args.auto_links:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            res = auto_links(db, args.excel, out, apply=args.apply,
                             actor_id=_actor_id(db, args.actor_email) if args.apply else None)
            print(f"auto blocks   : {len(res['auto'])}")
            print(f"auto pairs    : {sum(len(a['contacts']) for a in res['auto'])}")
            print(f"already linked: {res['already_linked']}")
            print(f"needs human   : {res['needs_human']}")
            if args.apply:
                print(f"linked        : {res['linked']}  skipped_existing: {res['skipped_existing']}  failed: {len(res['failed'])}")
            else:
                print("DRY RUN - nothing written. Re-run with --apply.")
            return 1 if res["failed"] else 0
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
