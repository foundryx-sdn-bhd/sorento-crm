"""Profile facts: the "About this contact" slice (chatbot memory lane A, contract
section 4 / PLAN-chatbot-memory-26sep.md section 4).

One entry per vocabulary key, stored in `respond_contacts.chatbot_profile.facts`.
Four sources, in precedence order: staff beats stated beats crm beats tallied. `crm`
facts are a LIVE join, never stored - `crm_view` reads them fresh on every call.
Every write here (`apply_statement`, `set_staff_fact`, `delete_fact`, `tally`) takes
its own fresh `SELECT ... FOR UPDATE` of the contact row at write time, mutates the
`facts` array in Python and writes the whole array back inside the same lock, so a
staff fact saved while a turn is between intake and tail can never be clobbered by
a stale read taken before it landed (AC-MEM040).

No figures are computed here and no LLM is called - a raw entity or a validated
choice, nothing derived.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.access import RespondContactCustomer
from app.models.conversation_frame import ConversationFrame
from app.models.order import Customer
from app.models.product import Brand
from app.models.inventory import Warehouse
from app.models.sales_agent import SalesAgent

#: How many closed episodes a tally looks over (Q7 ruling - a count, never a date).
TALLY_WINDOW = 10
#: An entity has to show up in at least this many of the window's episodes to become
#: a fact - one mention is a one-off, not a habit.
TALLY_MIN_COUNT = 2
#: The vocabulary caps every multi-value fact at 3 (contract section 4's own table).
TALLY_TOP_N = 3

#: `conversation_frames.entities` bucket key -> the fact key it tallies into. The
#: bucket names are `narrow.py`/`turn/state.py`'s own entity kinds
#: (`KIND_FIELD_MAP`), not invented here.
_TALLY_ENTITY_TO_FACT: dict[str, str] = {
    "product": "usual_products",
    "brand": "usual_brands",
    "warehouse": "usual_sites",
}

#: staff > stated > crm > tallied (contract section 4). `crm` never reaches a write
#: path (it is a live read), listed only so the ranking reads complete.
_PRECEDENCE: dict[str, int] = {"staff": 3, "stated": 2, "crm": 1, "tallied": 0}

_LANGUAGE_CHOICES: tuple[str, ...] = ("en", "ms", "zh")
_LANGUAGE_LABELS: dict[str, str] = {"en": "English", "ms": "Malay", "zh": "Chinese"}

_ROLE_CHOICES: tuple[str, ...] = ("purchaser", "owner", "sales", "site_supervisor", "other")
_ROLE_LABELS: dict[str, str] = {
    "purchaser": "Purchaser",
    "owner": "Owner",
    "sales": "Sales",
    "site_supervisor": "Site supervisor",
    "other": "Other",
}

_SEGMENT_CHOICES: tuple[str, ...] = ("dealer", "project", "end_user")
_SEGMENT_LABELS: dict[str, str] = {"dealer": "Dealer", "project": "Project", "end_user": "End user"}

_CHOICE_LABELS: dict[str, dict[str, str]] = {
    "language": _LANGUAGE_LABELS,
    "role": _ROLE_LABELS,
    "segment": _SEGMENT_LABELS,
}


@dataclass(frozen=True)
class FactSpec:
    """One row of the contract's vocabulary table."""

    label: str
    kind: str  # "text" | "choice" | "multi"
    #: May a staff PUT (`set_staff_fact`) write this key.
    allow_staff: bool
    #: May a parser `profile_statement` (`apply_statement`) write this key.
    allow_stated: bool
    #: Is this key also filled in live from the CRM (`crm_view`).
    crm: bool = False
    #: Is this key ALSO written by `tally`.
    tallied: bool = False
    choices: tuple[str, ...] | None = None
    max_length: int | None = None
    max_items: int | None = None


#: Ordered exactly as the contract's own table (section 4) - `list(VOCABULARY)` is the
#: display and vocabulary order everywhere this is read.
VOCABULARY: dict[str, FactSpec] = {
    "customer": FactSpec(label="Customer", kind="text", allow_staff=False, allow_stated=False, crm=True),
    "segment": FactSpec(
        label="Segment", kind="choice", allow_staff=True, allow_stated=False, crm=True,
        choices=_SEGMENT_CHOICES,
    ),
    "salesperson": FactSpec(label="Salesperson", kind="text", allow_staff=False, allow_stated=False, crm=True),
    "language": FactSpec(
        label="Language", kind="choice", allow_staff=True, allow_stated=True, choices=_LANGUAGE_CHOICES,
    ),
    "role": FactSpec(label="Role", kind="choice", allow_staff=True, allow_stated=True, choices=_ROLE_CHOICES),
    "usual_products": FactSpec(
        label="Usual products", kind="multi", allow_staff=True, allow_stated=False, tallied=True,
        max_items=TALLY_TOP_N,
    ),
    "usual_brands": FactSpec(
        label="Usual brands", kind="multi", allow_staff=True, allow_stated=True, tallied=True,
        max_items=TALLY_TOP_N,
    ),
    "usual_sites": FactSpec(
        label="Usual sites", kind="multi", allow_staff=True, allow_stated=True, tallied=True,
        max_items=TALLY_TOP_N,
    ),
    "project": FactSpec(label="Project", kind="text", allow_staff=True, allow_stated=True, max_length=60),
    "about": FactSpec(label="About", kind="text", allow_staff=True, allow_stated=True, max_length=120, max_items=3),
    "note": FactSpec(label="Note", kind="text", allow_staff=True, allow_stated=False, max_length=200),
}


def _today() -> str:
    return date.today().isoformat()


def _as_date_str(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _today()


def _collapse_whitespace(value: Any) -> str:
    """Newlines and repeated whitespace folded to single spaces (contract: "project",
    "about", "note" are all rendered as one line)."""
    return " ".join(str(value).split())


# --------------------------------------------------------------------------- #
# Locking and storage
# --------------------------------------------------------------------------- #


def _lock_row(
    db: Session, *, respond_id: str | None = None, pk: str | None = None
) -> tuple[str, dict[str, Any]] | None:
    """`SELECT ... FOR UPDATE` of the contact row, taken fresh at write time - never
    a snapshot carried from intake (AC-MEM040)."""
    if respond_id is not None:
        row = db.execute(
            text(
                "SELECT id, chatbot_profile FROM respond_contacts "
                "WHERE respond_io_id = :c FOR UPDATE"
            ),
            {"c": respond_id},
        ).first()
    else:
        row = db.execute(
            text("SELECT id, chatbot_profile FROM respond_contacts WHERE id = :i FOR UPDATE"),
            {"i": pk},
        ).first()
    if row is None:
        return None
    return row[0], dict(row[1] or {})


def _persist_facts(db: Session, contact_pk: str, facts: list[dict[str, Any]]) -> None:
    db.execute(
        text(
            "UPDATE respond_contacts SET chatbot_profile = jsonb_set("
            "coalesce(chatbot_profile, '{}'::jsonb), '{facts}', CAST(:facts AS jsonb)) "
            "WHERE id = :i"
        ),
        {"facts": json.dumps(facts), "i": contact_pk},
    )
    db.commit()


def _write_fact(
    db: Session,
    *,
    by_respond_id: str | None = None,
    by_pk: str | None = None,
    key: str,
    value: Any,
    source: str,
    source_ref: str | None,
    set_by: str | None,
    seen_count: int | None = None,
    first_seen: str | None = None,
) -> dict[str, Any] | None:
    """The one write path every source funnels through - precedence decided HERE,
    under the lock, never by the caller guessing what is already stored."""
    locked = _lock_row(db, respond_id=by_respond_id, pk=by_pk)
    if locked is None:
        return None
    contact_pk, profile = locked
    facts = list(profile.get("facts") or [])
    existing = next((f for f in facts if f.get("key") == key), None)
    # A tombstone (a staff delete of a learned or said fact) blocks only a TALLY of
    # the values it removed; a newer statement replaces it (reviewer pass at d89110c0,
    # S10). Its `removed` list rides on whatever replaces it, so the same values stay
    # blocked for the next tally too.
    removed = list(existing.get("removed") or []) if existing is not None and existing.get("value") is None else None
    if removed is not None and source == "tallied":
        value = [v for v in (value if isinstance(value, list) else [value]) if v not in removed]
        if not value:
            return None
    if removed is None and existing is not None and _PRECEDENCE.get(existing.get("source"), -1) > _PRECEDENCE.get(source, -1):
        # A stated write never replaces a staff entry; a tally never replaces a
        # staff or stated entry (contract section 4) - including a tombstone stub
        # (`value: null`), which still carries `source: "staff"`.
        return None
    same_source = existing is not None and existing.get("source") == source
    entry = {
        "key": key,
        "value": value,
        "source": source,
        "source_ref": source_ref,
        "first_seen": first_seen or (existing.get("first_seen") if same_source else _today()),
        "last_seen": _today(),
        "seen_count": (
            seen_count
            if seen_count is not None
            else ((existing.get("seen_count") or 0) + 1 if same_source else 1)
        ),
        "set_by": set_by,
    }
    if removed:
        entry["removed"] = removed
    new_facts = [f for f in facts if f.get("key") != key]
    new_facts.append(entry)
    _persist_facts(db, contact_pk, new_facts)
    return entry


# --------------------------------------------------------------------------- #
# AC-MEM031: crm_view - a LIVE read, never persisted
# --------------------------------------------------------------------------- #


def primary_customer(db: Session, contact_pk: str) -> tuple[Any, Any] | None:
    """`(Customer, SalesAgent | None)` off the contact's primary customer link, read
    live, or None when the contact is linked to no customer. The graceful fallback
    names both (S4: example 6's offer, a commercial handover's salesperson)."""
    return (
        db.query(Customer, SalesAgent)
        .join(RespondContactCustomer, RespondContactCustomer.customer_id == Customer.id)
        .outerjoin(SalesAgent, SalesAgent.id == Customer.sales_agent_id)
        .filter(
            RespondContactCustomer.contact_id == contact_pk,
            RespondContactCustomer.is_primary.is_(True),
        )
        # Earliest-linked primary first: one primary per company, so a contact tied to two
        # companies needs a fixed answer, not whichever row Postgres returns.
        .order_by(RespondContactCustomer.created_at, RespondContactCustomer.id)
        .first()
    )


def salesperson_name(agent: Any) -> str | None:
    """The name a dealer knows the salesperson by: the person label, else the
    AutoCount agent code. None for an inactive or missing agent."""
    if agent is None or getattr(agent, "is_active", True) is False:
        return None
    return (getattr(agent, "person_label", None) or getattr(agent, "sales_agent", None) or "").strip() or None


def crm_view(db: Session, contact: Any) -> list[dict[str, Any]]:
    """`customer`, `segment`, `salesperson` off the primary linked customer, read
    fresh every call - a changed salesperson shows up on the very next read, and
    none of these three is ever written into `chatbot_profile.facts`."""
    row = primary_customer(db, contact.id)
    if row is None:
        return []
    customer, agent = row
    out: list[dict[str, Any]] = [
        {
            "key": "customer",
            "value": (
                f"{customer.customer_name} ({customer.customer_code})"
                if customer.customer_code
                else customer.customer_name
            ),
            "source": "crm",
            "last_seen": None,
            # AC-MEM043 (reviewer pass at d89110c0, S11): the row links to the customer.
            "link": f"/order-management/customers/{customer.id}",
        }
    ]
    if customer.market_segment_code:
        out.append({"key": "segment", "value": customer.market_segment_code, "source": "crm", "last_seen": None})
    if agent is not None and agent.sales_agent:
        out.append({"key": "salesperson", "value": agent.sales_agent, "source": "crm", "last_seen": None})
    return out


def merged_facts_for_display(db: Session, contact: Any) -> list[dict[str, Any]]:
    """The stored facts with the live CRM ones filled in around them (AC-MEM041) -
    a stored STAFF entry for a key CRM also names (`segment`) wins; `customer` and
    `salesperson` have no stored counterpart at all, so the two lists simply
    concatenate."""
    stored = list((getattr(contact, "chatbot_profile", None) or {}).get("facts") or [])
    staff_keys = {f.get("key") for f in stored if f.get("source") == "staff"}
    merged = list(stored)
    for fact in crm_view(db, contact):
        if fact["key"] in staff_keys:
            continue
        merged.append(fact)
    return merged


# --------------------------------------------------------------------------- #
# AC-MEM032 (Q7): tally over the last TALLY_WINDOW closed episodes
# --------------------------------------------------------------------------- #


def tally(
    db: Session, contact_respond_id: str, *, is_test: bool = False, contact_pk: str | None = None
) -> list[dict[str, Any]]:
    """Recompute the tallied facts over the newest `TALLY_WINDOW` closed episodes -
    an entity in at least `TALLY_MIN_COUNT` of them, top `TALLY_TOP_N` by count then
    recency, survives; every other value (including one that just aged out of the
    window) is dropped on the next call. A key a staff or stated entry already owns
    is left alone entirely, in the RETURN value too - a caller asking what changed
    should never be told about a key it did not touch.

    `contact_pk` locks the write by the `respond_contacts.id` the CALLER already
    resolved safely (security review 26 Sep 2026, B1) - `contact_respond_id` alone
    is not unique across workspaces. `None` keeps the old respond_io_id lookup, for
    a direct/unit-test caller that has not gone through intake's resolution."""
    frames = (
        db.query(ConversationFrame)
        .filter(
            ConversationFrame.contact_respond_id == contact_respond_id,
            ConversationFrame.status == "closed",
            ConversationFrame.is_test.is_(is_test),
        )
        .order_by(ConversationFrame.started_at.desc())
        .limit(TALLY_WINDOW)
        .all()
    )
    known_brands = _known_brand_names(db) if frames else set()
    known_warehouses = _known_warehouse_names(db) if frames else set()
    out: list[dict[str, Any]] = []
    for entity_kind, fact_key in _TALLY_ENTITY_TO_FACT.items():
        known = (
            known_brands
            if entity_kind == "brand"
            else known_warehouses
            if entity_kind == "warehouse"
            else None
        )
        counts: dict[str, dict[str, Any]] = {}
        for frame in frames:
            entities = frame.entities if isinstance(frame.entities, dict) else {}
            values = entities.get(entity_kind) or []
            when = frame.started_at
            for raw_value in values:
                value = str(raw_value)
                # S2: a tallied brand/site goes through the SAME master-list check a
                # stated or staff write already does - a decommissioned or misspelled
                # value from old frame data never becomes a fact.
                if known is not None and value.lower() not in known:
                    continue
                bucket = counts.setdefault(
                    value, {"count": 0, "first": when, "last": when, "frame_id": frame.id}
                )
                bucket["count"] += 1
                if when is not None and (bucket["first"] is None or when < bucket["first"]):
                    bucket["first"] = when
                if when is not None and (bucket["last"] is None or when > bucket["last"]):
                    bucket["last"] = when
                    bucket["frame_id"] = frame.id
        qualifying = [(value, bucket) for value, bucket in counts.items() if bucket["count"] >= TALLY_MIN_COUNT]
        if not qualifying:
            continue
        qualifying.sort(key=lambda pair: (pair[1]["count"], pair[1]["last"]), reverse=True)
        top = qualifying[:TALLY_TOP_N]
        top_values = [value for value, _ in top]
        first_seen = _as_date_str(min((bucket["first"] for _, bucket in top), default=None))
        entry = _write_fact(
            db,
            by_respond_id=contact_respond_id if contact_pk is None else None,
            by_pk=contact_pk,
            key=fact_key,
            value=top_values,
            source="tallied",
            source_ref=top[0][1]["frame_id"],
            set_by=None,
            seen_count=top[0][1]["count"],
            first_seen=first_seen,
        )
        if entry is not None:
            out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# AC-MEM033 (Q6): stated facts (a parser `profile_statement`)
# --------------------------------------------------------------------------- #


def _normalize_choice(value: Any, choices: tuple[str, ...]) -> str | None:
    normalized = str(value).strip().lower()
    return normalized if normalized in choices else None


def _normalize_master_list(value: Any, known: set[str], max_items: int) -> list[str] | None:
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        candidate = _collapse_whitespace(item)
        if not candidate or candidate.lower() not in known:
            return None
        if candidate not in out:
            out.append(candidate)
    return out[:max_items] if out else None


def _known_brand_names(db: Session) -> set[str]:
    return {name.strip().lower() for (name,) in db.query(Brand.brand_name).all() if name}


def _known_warehouse_names(db: Session) -> set[str]:
    return {name.strip().lower() for (name,) in db.query(Warehouse.warehouse_name).all() if name}


#: S4: a staff `usual_products` item is free text (no master list backs it), so it
#: is capped by length rather than validated - the same 60-char ceiling `project`
#: gets, one item.
_USUAL_PRODUCT_MAX_LENGTH = 60


def _normalize_usual_products(value: Any, max_items: int) -> list[str] | None:
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        candidate = _collapse_whitespace(item)[:_USUAL_PRODUCT_MAX_LENGTH]
        if not candidate:
            continue
        if candidate not in out:
            out.append(candidate)
    return out[:max_items] if out else None


def _normalize_for_stated(key: str, spec: FactSpec, value: Any, db: Session) -> Any:
    if key == "language":
        return _normalize_choice(value, spec.choices or ())
    if key == "role":
        return _normalize_choice(value, spec.choices or ())
    if key == "usual_brands":
        return _normalize_master_list(value, _known_brand_names(db), spec.max_items or TALLY_TOP_N)
    if key == "usual_sites":
        return _normalize_master_list(value, _known_warehouse_names(db), spec.max_items or TALLY_TOP_N)
    if key == "project":
        # A stated project is CLIPPED, not rejected - the parser's own free-text
        # extraction is imprecise, and a customer's real project name should not
        # vanish for running long.
        cleaned = _collapse_whitespace(value)
        return cleaned[: spec.max_length] if spec.max_length else cleaned
    if key == "about":
        # Round 3 (AC-MEM033): CUT, not rejected - matching `project`'s own
        # cut-not-reject rule, since a parser's free-text "about" is just as
        # imprecise as its "project" extraction.
        cleaned = _collapse_whitespace(value)
        return cleaned[: spec.max_length] if spec.max_length else cleaned
    return None


def apply_statement(
    db: Session,
    contact_respond_id: str,
    key: str,
    value: Any,
    *,
    turn_id: str,
    contact_pk: str | None = None,
) -> dict[str, Any] | None:
    """A parser `profile_statement` (contract section 4, Q6 ruling). Rejects a key
    the vocabulary does not allow a stated write for, or a value that fails that
    key's own validation - never raises, since a rejected statement is simply not
    learned, not a failed turn.

    `contact_pk` locks the write by the primary key the caller already resolved
    (security review 26 Sep 2026, B1); `None` keeps the old respond_io_id lookup
    for a direct/unit-test caller."""
    spec = VOCABULARY.get(key)
    if spec is None or not spec.allow_stated:
        return None
    normalized = _normalize_for_stated(key, spec, value, db)
    if normalized is None:
        return None
    return _write_fact(
        db,
        by_respond_id=contact_respond_id if contact_pk is None else None,
        by_pk=contact_pk,
        key=key,
        value=normalized,
        source="stated",
        source_ref=turn_id,
        set_by=None,
    )


# --------------------------------------------------------------------------- #
# AC-MEM034: staff facts and delete (with tombstone)
# --------------------------------------------------------------------------- #


def _normalize_for_staff(key: str, spec: FactSpec, value: Any, db: Session) -> Any:
    # N4: a list is only a value for a list key, and more brands or sites than the
    # key keeps is a 422, not a silent cut (`usual_products` is capped, by ruling).
    if spec.kind == "multi":
        if key in ("usual_brands", "usual_sites") and isinstance(value, list) and len(value) > (
            spec.max_items or TALLY_TOP_N
        ):
            return None
    elif not isinstance(value, str):
        return None
    if spec.choices:
        return _normalize_choice(value, spec.choices)
    if key == "usual_brands":
        return _normalize_master_list(value, _known_brand_names(db), spec.max_items or TALLY_TOP_N)
    if key == "usual_sites":
        return _normalize_master_list(value, _known_warehouse_names(db), spec.max_items or TALLY_TOP_N)
    if key == "usual_products":
        return _normalize_usual_products(value, spec.max_items or TALLY_TOP_N)
    if spec.kind == "text":
        cleaned = _collapse_whitespace(value)
        if not cleaned or (spec.max_length and len(cleaned) > spec.max_length):
            return None
        return cleaned
    return None


def set_staff_fact(db: Session, contact_pk: str, key: str, value: Any, *, user_id: str) -> dict[str, Any] | None:
    """A staff PUT of one fact (contract section 5) - always wins over whatever was
    there, learned or said, because `source="staff"` outranks everything in
    `_write_fact`'s own precedence check."""
    spec = VOCABULARY.get(key)
    if spec is None or not spec.allow_staff:
        return None
    normalized = _normalize_for_staff(key, spec, value, db)
    if normalized is None:
        return None
    return _write_fact(
        db, by_pk=contact_pk, key=key, value=normalized, source="staff", source_ref=None, set_by=user_id,
    )


def delete_fact(db: Session, contact_pk: str, key: str) -> bool:
    """A staff delete (contract section 4). A staff fact is removed clean, no
    tombstone. A learned or said fact is removed AND replaced by a tombstone stub
    (`source: "staff"`, `value: null`, its old values under `removed`) so the very
    next tally or statement about the same key does not bring it straight back."""
    if key not in VOCABULARY:
        return False
    locked = _lock_row(db, pk=contact_pk)
    if locked is None:
        return False
    contact_pk_id, profile = locked
    facts = list(profile.get("facts") or [])
    existing = next((f for f in facts if f.get("key") == key), None)
    if existing is not None and existing.get("value") is None:
        # Already a tombstone: deleting it again must not let the removed values be
        # re-learned (reviewer pass at d89110c0, S10).
        return True
    new_facts = [f for f in facts if f.get("key") != key]
    if existing is not None and (existing.get("source") != "staff" or existing.get("removed")):
        # The removed values accumulate across replacements: a staff entry that
        # replaced a tombstone keeps blocking what the tombstone blocked.
        prior = list(existing.get("removed") or [])
        raw_value = existing.get("value") if existing.get("source") != "staff" else None
        values = raw_value if isinstance(raw_value, list) else ([raw_value] if raw_value is not None else [])
        removed = prior + [v for v in values if v not in prior]
        new_facts.append(
            {
                "key": key,
                "value": None,
                "source": "staff",
                "source_ref": None,
                "first_seen": existing.get("first_seen"),
                "last_seen": _today(),
                "seen_count": existing.get("seen_count") or 0,
                "set_by": None,
                "removed": removed,
            }
        )
    _persist_facts(db, contact_pk_id, new_facts)
    return True


# --------------------------------------------------------------------------- #
# Owner ruling, hand pass 10 (21 Sep 2026) supersedes AC-MEM037: a fresh
# promotion ask that names no access level of its own must re-open the tier
# roster rather than settle on a previously picked tier. Security review
# 26 Sep 2026 removed the chat-side tier write this contradicted (`set_tier`
# and its engine call site) rather than special-casing it - a resolved tier is
# never persisted onto `chatbot_profile`, and `system_settings.chatbot_tier_
# order` stays read only by `turn/policy.py::load_policy`, its original reader.
# --------------------------------------------------------------------------- #
# AC-MEM035: no expiry - the parser's own read of the profile slice
# --------------------------------------------------------------------------- #


def _display_value(key: str, value: Any) -> str | None:
    if value is None:
        return None
    labels = _CHOICE_LABELS.get(key)
    if labels and isinstance(value, str):
        return labels.get(value, value)
    if isinstance(value, list):
        return ", ".join(_display_value(key, v) or str(v) for v in value)
    return str(value)


def fact_for_display(key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """One row of the `GET .../chatbot/memory` `facts` array (contract section 5)."""
    spec = VOCABULARY.get(key)
    return {
        "key": key,
        "label": spec.label if spec else key,
        "value": entry.get("value"),
        "display": _display_value(key, entry.get("value")),
        "source": entry.get("source"),
        "last_seen": entry.get("last_seen"),
        "editable": bool(spec and spec.allow_staff),
        "link": entry.get("link"),
    }


def vocabulary_entry(key: str) -> dict[str, Any]:
    spec = VOCABULARY[key]
    options = None
    if spec.choices:
        labels = _CHOICE_LABELS.get(key, {})
        options = [{"value": choice, "label": labels.get(choice, choice)} for choice in spec.choices]
    return {
        "key": key,
        "label": spec.label,
        "kind": spec.kind,
        "options": options,
        "max_length": spec.max_length,
    }


def _raw_line_value(value: Any) -> str:
    """The RAW stored value, not its pretty display label - the parser reads its
    own vocabulary's own codes (`purchaser`, `ms`), the same values `apply_statement`
    accepted back, never the grid's human label."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _merge_by_vocabulary(facts: list[dict[str, Any]], crm: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key = {f.get("key"): f for f in facts if f.get("value") is not None}
    for fact in crm:
        by_key.setdefault(fact["key"], fact)
    return by_key


def structured_slice(facts: list[dict[str, Any]], crm: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same merge `parser_slice` renders as a flat string, kept STRUCTURED -
    `turn/context.py::_render_l5` builds "About this contact:" straight from this
    (security review 26 Sep 2026, S2), never by re-splitting a joined string on
    `;`, which corrupts the moment a free-text value contains one itself. One
    `{"key", "value"}` entry per vocabulary key that has a value, in vocabulary
    order - the same order `_L5_DROP_ORDER` drops by."""
    by_key = _merge_by_vocabulary(facts, crm)
    out: list[dict[str, Any]] = []
    for key in VOCABULARY:
        entry = by_key.get(key)
        if entry is None or entry.get("value") is None:
            continue
        out.append({"key": key, "value": entry.get("value")})
    return out


def parser_slice(facts: list[dict[str, Any]], crm: list[dict[str, Any]]) -> str:
    """The raw `"key value; key value"` segments (legacy shape - `turn/context.py`
    no longer reads this for L5, `structured_slice` above does; this stays for the
    parser's own direct callers, e.g. `AC-MEM035`'s no-expiry contract, which have
    no need for the structured form). No header here, no newlines. No expiry (Q7
    ruling) - a fact from 400 days ago is rendered exactly like one from today; the
    DATE is what tells the reader it is old, never a cutoff that drops it."""
    by_key = _merge_by_vocabulary(facts, crm)
    segments: list[str] = []
    for key in VOCABULARY:
        entry = by_key.get(key)
        if entry is None or entry.get("value") is None:
            continue
        segments.append(f"{key.replace('_', ' ')} {_raw_line_value(entry.get('value'))}")
    return "; ".join(segments)
