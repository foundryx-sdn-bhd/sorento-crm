"""CONTACT-TAGS S3: apply tag settings (preview and write).

Expected / current / conflict come from `contact_tag_settings.evaluate` (S2); this module makes
current equal expected for the keys that differ. Conflicts are never written, a section the actor
may not write is listed under `not_applied`, each contact is written inside its own savepoint, and
only differing keys are touched (so a second apply changes nothing). Writers are plain ORM / Core
writes with no commit (the services the contact page calls commit and would end the savepoint);
nothing here sends a message or calls respond.io.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import contact_tag_settings as cts
from app.services.contact_tag_settings import ALL, Catalog, SECTIONS
from app.services.error_handler import handle_unprocessable

logger = logging.getLogger(__name__)

MAX_CONTACTS = 500
SAVE_FAILED = "Could not save this contact; nothing was changed."

_GATE_WORDS = {
    "agents": "chatbot agents",
    "agent_fields": "agent field access",
    "stock_visibility": "stock visibility",
    "outbound_enabled": "messaging",
    "companies": "companies",
}


def _can_write(section: str, can: Callable[[str], bool], is_superadmin: Callable[[], bool]) -> bool:
    if section in cts._GATES:
        slug = cts._GATES[section]
        return is_superadmin() if slug is None else can(slug)
    return can("user_management.contacts.edit")


# ------------------------------------------------------------------ writers


def _w_pivot_codes(db: Session, contact, items, actor_id) -> None:
    from app.models.access import (
        ContactAttachmentType,
        respond_contact_access_types,
        respond_contact_market_segments,
    )
    from app.models.company import RespondContactCompany

    cid = str(contact.id)
    for item in items:
        want = set(item["_raw"]["expected"])
        section = item["section"]
        if section in ("access_types", "market_segments"):
            t, col = (
                (respond_contact_access_types, "access_type_code")
                if section == "access_types"
                else (respond_contact_market_segments, "segment_code")
            )
            db.execute(t.delete().where(t.c.contact_id == cid))
            if want:
                db.execute(t.insert(), [{"contact_id": cid, col: v} for v in sorted(want)])
            db.expire(contact, [section])
        elif section == "companies":
            db.query(RespondContactCompany).filter(RespondContactCompany.respond_contact_id == cid).delete(
                synchronize_session=False
            )
            for v in sorted(want):
                db.add(RespondContactCompany(respond_contact_id=cid, company_id=v))
        else:  # attachment_types
            db.query(ContactAttachmentType).filter(ContactAttachmentType.contact_id == cid).delete(
                synchronize_session=False
            )
            for v in sorted(want):
                db.add(ContactAttachmentType(contact_id=cid, attachment_type_id=v, created_by=actor_id))


def _w_columns(db: Session, contact, items, actor_id) -> None:
    for item in items:
        section, key, want = item["section"], item["key"], item["_raw"]["expected"]
        if section == "brands":
            contact.brand_ids = None if want == ALL else sorted(want)
        elif section == "regions":
            contact.regions = [r for r in cts.REGIONS if r in want]
        elif section in ("requires_registered_project", "outbound_enabled"):
            setattr(contact, section, bool(want))
        elif section == "chatbot":
            if key in cts.CHATBOT_BOOLS:
                setattr(contact, key, bool(want))
            elif key == "memory_level":
                contact.chatbot_memory_level = want
            else:  # tier: ONLY the tier key of the profile; language, facts, ledgers stay
                db.flush()
                db.execute(
                    text(
                        "UPDATE respond_contacts SET chatbot_profile = "
                        "jsonb_set(COALESCE(chatbot_profile, CAST('{}' AS jsonb)), '{tier}', to_jsonb(CAST(:t AS text))) "
                        "WHERE id = :i"
                    ),
                    {"t": want, "i": str(contact.id)},
                )
                db.expire(contact, ["chatbot_profile"])
    db.flush()


def _w_portal_forms(db: Session, contact, items, actor_id) -> None:
    from app.models.price_tag import ContactPortalFormOverride as O

    for item in items:  # only kinds whose resolved visibility differs reach here
        row = db.query(O).filter(O.contact_id == str(contact.id), O.form_type == item["key"]).first()
        if row is None:
            db.add(O(contact_id=str(contact.id), form_type=item["key"], is_enabled=bool(item["_raw"]["expected"])))
        else:
            row.is_enabled = bool(item["_raw"]["expected"])


def _w_field_reveals(db: Session, contact, items, actor_id) -> None:
    from app.models.access import ContactFieldReveal as R

    for item in items:
        want = bool(item["_raw"]["expected"])
        row = db.query(R).filter(R.respond_contact_id == str(contact.id), R.field_key == item["key"]).first()
        if row is None:
            db.add(R(respond_contact_id=str(contact.id), field_key=item["key"], granted=want, created_by=actor_id))
        else:
            row.granted = want


def _w_agents(db: Session, contact, items, actor_id) -> None:
    from sqlalchemy import or_

    from app.models.access import ContactAgentAccess as A

    cid = str(contact.id)
    for item in items:
        want = bool(item["_raw"]["expected"])
        rows = (
            db.query(A)
            .filter(
                A.agent_id == item["key"],
                or_(A.respond_contact_id == cid, A.respond_contact_id.is_(None) & (A.respond_contact_phone == contact.phone_number)),
            )
            .order_by(A.created_at, A.id)
            .all()
        )
        if rows:  # the oldest row is the grant; a phone-only legacy row is adopted, duplicates removed
            row = rows[0]
            for extra in rows[1:]:
                db.delete(extra)
            row.respond_contact_id = cid
            row.is_allowed = want
            row.valid_from = None
            row.valid_to = None
        else:
            db.add(
                A(
                    respond_contact_id=cid,
                    respond_contact_phone=contact.phone_number,
                    respond_contact_name=contact.name,
                    agent_id=item["key"],
                    is_allowed=want,
                    created_by=actor_id,
                )
            )


def _w_agent_fields(db: Session, contact, items, actor_id) -> None:
    from app.models.access import AgentFieldAccess as F
    from app.services.field_access import GATED_FIELDS

    for item in items:
        code, resource, field_key = item["key"].split(".", 2)
        if GATED_FIELDS.get(resource, {}).get(field_key) != code:
            raise ValueError(f"{resource}.{field_key} is not a gated field of {code}")
        want = bool(item["_raw"]["expected"])
        row = (
            db.query(F)
            .filter(F.agent_code == code, F.resource == resource, F.field_key == field_key, F.contact_id == str(contact.id))
            .first()
        )
        if row is None:
            db.add(
                F(agent_code=code, resource=resource, field_key=field_key, contact_id=str(contact.id),
                  is_allowed=want, created_by=actor_id, updated_by=actor_id)
            )
        else:
            row.is_allowed = want
            row.updated_by = actor_id


def _w_media(db: Session, contact, items, actor_id) -> None:
    from app.services import media_access_service as m

    by_mod: dict[str, dict] = {}
    for item in items:
        modality, field = item["key"].split(".", 1)
        by_mod.setdefault(modality, {})[field] = item["_raw"]["expected"]
    cid = str(contact.id)
    for modality, vals in by_mod.items():
        row = m.get_limit_row(db, cid, modality)
        m.upsert_access(
            db,
            cid,
            modality,
            is_allowed=bool(vals["is_allowed"]) if "is_allowed" in vals else bool(row and row.is_allowed),
            monthly_limit=vals["monthly_limit"] if "monthly_limit" in vals else (row.monthly_limit if row else None),
            max_clip_seconds=(
                vals["max_clip_seconds"] if "max_clip_seconds" in vals else (row.max_clip_seconds if row else None)
            ),
            updated_by=actor_id,
        )
        if "languages" in vals:
            fresh = m.get_limit_row(db, cid, modality)
            fresh.languages = sorted(vals["languages"])
    db.flush()


def _w_policy(db: Session, contact, items, actor_id) -> None:
    """Stock / spec: the contact's own row, from the expected keys over what the row already holds."""
    from app.models.access import SpecVisibilityPolicy, StockVisibilityPolicy
    from app.services import stock_visibility as sv

    cid = str(contact.id)
    stock = items[0]["section"] == "stock_visibility"
    model = StockVisibilityPolicy if stock else SpecVisibilityPolicy
    inc, exc = ("warehouse_ids", "excluded_warehouse_ids") if stock else ("spec_keys", "excluded_spec_keys")
    row = db.query(model).filter(model.contact_id == cid).first()
    if row is None:
        # Seeded from what the contact RESOLVES to now, so keys the tags do not set keep their
        # inherited values (access type / segment / default) instead of resetting.
        row = model(contact_id=cid)
        if stock:
            eff = sv.resolve_policy(db, cid) or sv.default_policy(db)
            row.mode = eff.mode
            row.hide_zero_locations = bool(eff.hide_zero_locations)
            row.warehouse_ids = sorted(eff.warehouse_ids) if eff.warehouse_ids is not None else None
            row.excluded_warehouse_ids = (
                sorted(eff.excluded_warehouse_ids) if eff.excluded_warehouse_ids is not None else None
            )
        else:
            from app.services import spec_visibility as sp

            eff = sp.resolve_policy(db, cid)
            row.spec_keys = sorted(eff.spec_keys) if eff.spec_keys is not None else None
            row.excluded_spec_keys = sorted(eff.excluded_spec_keys) if eff.excluded_spec_keys is not None else None
        db.add(row)
    want = {i["key"]: i["_raw"]["expected"] for i in items}
    for key, value in want.items():
        if key in ("warehouse_ids", "excluded_warehouse_ids", "spec_keys", "excluded_spec_keys"):
            setattr(row, key, sorted(value))
        elif key == "hide_zero_locations":
            row.hide_zero_locations = bool(value)
        else:
            row.mode = value
    # One rule per row (CHECK): setting one list clears its sibling unless the tags set both.
    if inc in want and exc in want:
        raise ValueError("a contact row cannot hold both an include list and an exclude list")
    if inc in want:
        setattr(row, exc, None)
    if exc in want:
        setattr(row, inc, None)
    db.flush()


_WRITERS: dict[str, Callable] = {
    "access_types": _w_pivot_codes, "market_segments": _w_pivot_codes, "companies": _w_pivot_codes,
    "attachment_types": _w_pivot_codes,
    "brands": _w_columns, "regions": _w_columns, "chatbot": _w_columns,
    "requires_registered_project": _w_columns, "outbound_enabled": _w_columns,
    "portal_forms": _w_portal_forms, "field_reveals": _w_field_reveals, "agents": _w_agents,
    "agent_fields": _w_agent_fields, "media": _w_media,
    "stock_visibility": _w_policy, "spec_visibility": _w_policy,
}


# ------------------------------------------------------------------ rows


def _change(cat: Catalog, item: dict) -> dict:
    raw = item["_raw"]
    before, after = raw["current"], raw["expected"]
    added: list = []
    removed: list = []
    if isinstance(after, frozenset) and isinstance(before, frozenset) and not item.get("redacted"):
        added = cts._fmt(cat, item["section"], item["key"], after - before)
        removed = cts._fmt(cat, item["section"], item["key"], before - after)
    return {
        "section": item["section"],
        "key": item["key"],
        "label": item["label"],
        "before": item["current"],
        "after": item["expected"],
        "decided_by": item["decided_by"] or [],
        "added": added,
        "removed": removed,
    }


_SIBLINGS = {
    ("stock_visibility", "warehouse_ids"): "excluded_warehouse_ids",
    ("stock_visibility", "excluded_warehouse_ids"): "warehouse_ids",
    ("spec_visibility", "spec_keys"): "excluded_spec_keys",
    ("spec_visibility", "excluded_spec_keys"): "spec_keys",
}


def _cleared_sibling_changes(db: Session, contact, cat: Catalog, items: list[dict], todo: list[dict]) -> list[dict]:
    """The one-rule CHECK clears the sibling list when a tag sets the other one (the writer does it), so
    the preview must list that clearing too: the contact's inherited list goes away (R2)."""
    present = {(i["section"], i["key"]) for i in items}
    reader = None
    out: list[dict] = []
    for item in todo:
        sibling = _SIBLINGS.get((item["section"], item["key"]))
        if sibling is None or (item["section"], sibling) in present:
            continue
        if item.get("redacted") or not item["_raw"]["expected"]:
            continue  # an empty list sets nothing, so nothing is cleared
        if reader is None:
            reader = cts._Reader(db, [contact])
        before = reader.current(str(contact.id), item["section"], sibling, cat)
        if isinstance(before, cts._AllSet) or not before:
            continue  # nothing held on that side
        out.append(
            {
                "section": item["section"],
                "key": sibling,
                "label": cts._label(cat, item["section"], sibling),
                "before": cts._display(cat, item["section"], sibling, before),
                "after": "All" if sibling in ("warehouse_ids", "spec_keys") else [],
                "decided_by": item["decided_by"] or [],
                "added": [],
                "removed": cts._fmt(cat, item["section"], sibling, before),
            }
        )
    return out


def _inherits_both_lists(db: Session, contact, section: str, items: list[dict]) -> bool:
    from app.models.access import SpecVisibilityPolicy, StockVisibilityPolicy
    from app.services import spec_visibility as sp
    from app.services import stock_visibility as sv

    stock = section == "stock_visibility"
    inc, exc = ("warehouse_ids", "excluded_warehouse_ids") if stock else ("spec_keys", "excluded_spec_keys")
    if any(i["section"] == section and i["key"] in (inc, exc) for i in items):
        return False  # the tags set a side, so the other is cleared on write
    model = StockVisibilityPolicy if stock else SpecVisibilityPolicy
    if db.query(model.id).filter(model.contact_id == str(contact.id)).first() is not None:
        return False  # an own row exists and keeps its (valid) lists
    eff = (sv.resolve_policy(db, str(contact.id)) or sv.default_policy(db)) if stock else sp.resolve_policy(db, str(contact.id))
    a, b = (eff.warehouse_ids, eff.excluded_warehouse_ids) if stock else (eff.spec_keys, eff.excluded_spec_keys)
    return a is not None and b is not None


def _row(contact_id: str, label: Optional[str], status: str, changes=None, conflicts=None, not_applied=None, error=None):
    return {
        "contact_id": contact_id, "label": label, "status": status, "changes": changes or [],
        "conflicts": conflicts or [], "not_applied": not_applied or [], "error": error,
    }


def apply_settings(
    db: Session,
    contact_ids: list[str],
    *,
    dry_run: bool,
    actor_id: Optional[str],
    can: Callable[[str], bool],
    is_superadmin: Callable[[], bool],
    only_tags: Optional[set[str]] = None,
) -> dict:
    """One row per contact in request order. The caller commits (`get_db` never does)."""
    from app.models.access import RespondContact
    from app.services.contact_access_copy_service import contact_label

    ids = list(dict.fromkeys(str(c) for c in contact_ids))
    found = {str(c.id): c for c in db.query(RespondContact).filter(RespondContact.id.in_(ids)).all()}
    # The same redaction as the tag-settings read: a section the actor cannot read keeps its generic label
    # and no values or names (conflict tags included).
    redact = cts.unreadable_sections(can, is_superadmin)
    evaluated = cts.evaluate(db, list(found.values()), only_tags=only_tags, redact=redact)
    cat = Catalog(db)
    results: list[dict] = []
    for cid in ids:
        contact = found.get(cid)
        if contact is None:
            results.append(_row(cid, None, "failed", error="Contact not found"))
            continue
        label = contact_label(contact)
        items = evaluated[cid]["items"]
        if not items:
            results.append(_row(cid, label, "skipped"))
            continue
        conflicts = [
            {"label": i["label"], "tags": i["conflict_tags"]} for i in items if i["conflict"]
        ]
        todo, not_applied = [], []
        # Stock / spec are one row per contact: a conflict inside the section holds the whole section back.
        held_back = {i["section"] for i in items if i["conflict"] and i["section"] in ("stock_visibility", "spec_visibility")}
        for i in items:
            if i.get("blocked_reason"):
                not_applied.append({"label": i["label"], "reason": i["blocked_reason"]})
                continue
            if i["conflict"] or not i["differs"]:
                continue
            if i["section"] in held_back:
                not_applied.append({"label": i["label"], "reason": "Held back: another setting in this group has a conflict."})
                continue
            if _can_write(i["section"], can, is_superadmin):
                todo.append(i)
            else:
                title = cts.SECTION_TITLES[i["section"]]
                not_applied.append({"label": title, "reason": f"You may not change {title}."})
        # A contact with no policy row of its own, whose RESOLVED policy already holds both an include and an
        # exclude list (merged across access types / segments), cannot be seeded: hold that section back.
        for section in ("stock_visibility", "spec_visibility"):
            group = [i for i in todo if i["section"] == section]
            if group and _inherits_both_lists(db, contact, section, items):
                for i in group:
                    todo.remove(i)
                title = cts.SECTION_TITLES[section]
                not_applied.append(
                    {
                        "label": title,
                        "reason": "Inherited settings hold both an include and an exclude list; set one list on the tag.",
                    }
                )
        changes = [_change(cat, i) for i in todo]
        changes += _cleared_sibling_changes(db, contact, cat, items, todo)
        if not todo:
            results.append(_row(cid, label, "unchanged", conflicts=conflicts, not_applied=not_applied))
            continue
        if not dry_run:
            savepoint = db.begin_nested()
            try:
                for section in SECTIONS:
                    group = [i for i in todo if i["section"] == section]
                    if group:
                        _WRITERS[section](db, contact, group, actor_id)
                _audit(db, cid, actor_id, todo)
                savepoint.commit()
            except Exception as exc:  # noqa: BLE001 - every failure becomes that contact's row
                savepoint.rollback()
                logger.warning("apply tag settings to contact %s failed: %s", cid, exc, exc_info=True)
                results.append(_row(cid, label, "failed", changes, conflicts, not_applied, SAVE_FAILED))
                continue
        results.append(_row(cid, label, "changed", changes, conflicts, not_applied))

    counts = {k: 0 for k in ("changed", "unchanged", "skipped", "failed")}
    for r in results:
        counts[r["status"]] += 1
    counts["conflicts"] = sum(len(r["conflicts"]) for r in results)
    return {"dry_run": dry_run, "results": results, "counts": counts}


def _audit(db: Session, contact_id: str, actor_id: Optional[str], todo: list[dict]) -> None:
    """One row per changed contact: several writers here are invisible to the ORM listener."""
    from app.services.audit_service import log_audit

    tags = sorted({t for i in todo for t in i["_raw"]["decided_by"]})
    labels = [i["label"] for i in todo]
    log_audit(
        db,
        "respond_contacts",
        contact_id,
        "UPDATE",
        user_id=actor_id,
        description=f"Tag settings applied from {', '.join(tags)}: {', '.join(labels)}",
        new_values={i["label"]: i["_raw"]["expected_text"] for i in todo},
        old_values={i["label"]: i["_raw"]["current_text"] for i in todo},
    )


def validate_ids(contact_ids: Iterable[str]) -> None:
    ids = list(dict.fromkeys(contact_ids))
    if not ids:
        raise handle_unprocessable("Pick at least one contact.")
    if len(ids) > MAX_CONTACTS:
        raise handle_unprocessable(f"At most {MAX_CONTACTS} contacts per request ({len(ids)} given).")
