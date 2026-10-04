"""The five-key session shape, read from wherever a contact's row actually holds it.

`respond_contacts.session_vars` is `{focus, open_question, ideation, access_levels,
contains_flyer}` (AC-1504). A contact mid-conversation at deploy still carries the
pre-rearch nest, `{"variables": {...}}`, written by n8n's outer loop - so every read goes
through here and accepts both, and every write goes through `turn/tail.py::persist`,
which only ever writes the five keys at the top level.

This module is the ONE place that knows the legacy nest exists. Nothing else reads
`session_vars.variables`.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot import jsc
from app.services.chatbot.label_catalog import LANGUAGES
from app.services.chatbot.turn.narrow import ledger_family_key
from app.services.chatbot.turn.pending import OFFER_KINDS, Pending, from_wire
from app.services.chatbot.turn.state import KIND_FIELD_MAP

# `reply_language` is the sixth (CHAT-LANGUAGE); the name stays for the callers.
FIVE_KEYS = ("focus", "open_question", "ideation", "access_levels", "contains_flyer", "reply_language")

#: The pre-rearch marker kinds (`contracts.PENDING_KINDS`), by the name the turn package
#: gives the SAME question (`turn/pending.PENDING_KINDS`). n8n's outer loop still writes
#: the left-hand names, so a contact who was mid-question at deploy carries one of them.
_LEGACY_PENDING_KIND: dict[str, str] = {
    "escalation_offer": "team_pick",
    "team_clarify": "team_pick",
    "company_clarify": "company_pick",
    "tier_ask": "tier_pick",
    "member_offer": "member_offer",
    "outstanding_scope": "outstanding_scope",
    "outstanding_detail": "outstanding_detail",
}

#: The legacy `order_status` bucket, as the document/status pair the focus now carries.
#: Same table `conversation_variables_service` reads a stored `focus.order_status`
#: through; spelled here because THIS module is the one that knows the flat nest.
_LEGACY_ORDER_STATUS: dict[str, tuple[list[str], str]] = {
    "outstanding": ([], "outstanding"),
    "so_outstanding": (["SO"], "outstanding"),
    "do_outstanding": (["DO"], "outstanding"),
    "outstanding_both": (["SO", "DO"], "outstanding"),
}


def five_keys(session_block: Any) -> dict[str, Any]:
    """The five keys off `get-session-vars`' own body (`{"session_vars": {...}}`), and the
    reply language when it is one the bot speaks (anything else reads as none)."""
    read = _five_keys(session_block)
    if read.get("reply_language") not in LANGUAGES:
        read["reply_language"] = None
    return read


def _five_keys(session_block: Any) -> dict[str, Any]:
    state = jsc.get(session_block, "session_vars")
    state = state if isinstance(state, dict) else {}
    if any(key in state for key in FIVE_KEYS):
        return {key: state.get(key) for key in FIVE_KEYS}
    legacy = state.get("variables")
    legacy = legacy if isinstance(legacy, dict) else {}
    read = {key: legacy.get(key) for key in FIVE_KEYS}
    if read["focus"] is None and read["open_question"] is None and legacy:
        # AC-1504's real case, and the one the first cut missed: a row written by n8n's
        # outer loop has NEITHER new key - the loop never wrote `focus` or
        # `open_question`, it wrote the flat shape below (`contracts.LegacyVariables`).
        # Reading the new names out of the old nest therefore answered None for every
        # contact who was mid-question at deploy, and they lost the question and the
        # subject on their very next message. Projected here, once, on the way in; the
        # tail overwrites the whole column with the five keys, so a row migrates the
        # first time its owner speaks and never comes back through this door.
        read["open_question"] = _legacy_open_question(legacy)
        read["focus"] = _legacy_focus(legacy)
    return read


def _legacy_option(
    row: Any, kind: str, position: int, families: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """One `last_result_set` row as a `Pending` option.

    The legacy roster has two shapes and this reads both: the outstanding questions'
    `{idx, label, value}` and the pickers' `{idx, label, uuid(s), entity_type}`. The
    value lands on the option's payload, which is where `turn/compose._lane_question`
    already puts it for a question asked under the new shape.

    `families` is the flat shape's `picker_families` - `{trading name key: [every ledger
    id]}`, written by the gate when it built the roster. A picker LINE stands for the whole
    account family (contract 103, and `gate.py`'s own "a picked CUSTOMER selects its whole
    ACCOUNT FAMILY" re-seat), and under the new shape that family IS the option's `uuids`;
    a legacy row carries only its own `uuid`, so without this a contact mid-picker at
    deploy picked one ledger of three and the report answered for a third of their orders
    under a header naming all of them (AC-1165/R19b).
    """
    if not isinstance(row, dict):
        return None
    option: dict[str, Any] = {
        "position": int(row.get("idx") or position),
        "label": row.get("label"),
        "entity_type": row.get("entity_type") or kind,
        "payload": {},
    }
    for key in ("code", "uuid", "uuids", "name", "stamp"):
        if row.get(key) is not None:
            option[key] = row[key]
    for key in ("value", "team"):
        if row.get(key) is not None:
            option["payload"][key] = row[key]
    if not option.get("uuids") and option.get("uuid") and families:
        family = families.get(ledger_family_key(jsc.js_string(row.get("label") or "")))
        members = [u for u in (family or []) if u]
        if option["uuid"] in members:
            option["uuids"] = members
    return option


def _legacy_open_question(legacy: dict[str, Any]) -> dict[str, Any] | None:
    marker = legacy.get("pending")
    marker = marker if isinstance(marker, dict) else {}
    rows = legacy.get("last_result_set")
    rows = rows if isinstance(rows, list) else []
    if marker.get("kind"):
        raw_kind = str(marker["kind"])
        kind = _LEGACY_PENDING_KIND.get(raw_kind, raw_kind)
    else:
        # The flat shape only ever wrote a `pending` MARKER for the offer kinds
        # (`tail/pending.derive`); an open PICKER - the ambiguous-customer roster, a
        # product family - was persisted as `selection_context` plus the roster it
        # offered, and nothing else. Under the new shape both are the same thing: one
        # open question. Without this a contact who was looking at a picker when the
        # rearch deployed had no question at all on their next message, and their "1"
        # resolved against nothing.
        context = jsc.js_string(
            legacy.get("selection_context") or legacy.get("picker_selection_context") or ""
        ).strip()
        if not context or not rows:
            return None
        entity_kind = next(
            (
                str(r["entity_type"])
                for r in rows
                if isinstance(r, dict) and r.get("entity_type")
            ),
            None,
        )
        if not entity_kind:
            return None
        kind = f"{entity_kind}_pick"
    families = legacy.get("picker_families")
    families = families if isinstance(families, dict) else None
    options = [
        o
        for o in (_legacy_option(r, kind, i + 1, families) for i, r in enumerate(rows))
        if o
    ]
    payload: dict[str, Any] = {
        "domain": marker.get("domain") or legacy.get("picker_domain") or legacy.get("domain_hint")
    }
    filters = legacy.get("outstanding_filters")
    if isinstance(filters, dict):
        # The question's own carried payload, as `turn/compose._lane_question` writes it:
        # what the lane resolved for the ask (the offer's verbatim text, the scope it
        # ran for). The SUBJECT itself goes on the focus below, not here.
        payload["filters"] = dict(filters)
    if marker.get("reprinted") is True:
        payload["reprinted"] = True
    if isinstance(marker.get("ttl"), int):
        payload["ttl"] = marker["ttl"]
    return {
        "kind": kind,
        "expects": "pick",
        "options": marker.get("options") if isinstance(marker.get("options"), list) else options,
        "team": marker.get("team"),
        "asked_at_turn": None,
        "payload": payload,
    }


def _legacy_focus(legacy: dict[str, Any]) -> dict[str, Any]:
    """The flat nest's own carries, as the focus wire shape (`turn/state.focus_to_wire`).

    Only the axes the flat shape actually held: the carried entities on their own kind's
    slot, the domain, the order-status bucket as `document` + `status`, the date window,
    and - when an outstanding question was open - the subject that question was asked
    about, which the flat shape kept in `outstanding_filters` rather than in `entities`.
    """
    wire: dict[str, Any] = {name: [] for name in ("products", "customers", "warehouse", "brands")}
    for entity in legacy.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        attr = KIND_FIELD_MAP.get(str(entity.get("hint") or entity.get("entity_type") or ""))
        if attr and attr in wire:
            wire[attr].append(entity)

    domain = legacy.get("domain_hint")
    wire["domains"] = [domain] if isinstance(domain, str) and domain else []
    document, status = _LEGACY_ORDER_STATUS.get(
        jsc.js_string(legacy.get("order_status") or "").strip(), ([], None)
    )
    wire["document"] = list(document)
    wire["status"] = status
    start, end = legacy.get("date_filter_start"), legacy.get("date_filter_end")

    filters = legacy.get("outstanding_filters")
    if isinstance(filters, dict):
        code = filters.get("product_code")
        if code and not wire["products"]:
            wire["products"] = [
                {"raw": code, "hint": "product", "canonical_code": code, "current_message": False}
            ]
        if not wire["customers"]:
            wire["customers"] = [
                {"uuid": uid, "hint": "customer", "current_message": False}
                for uid in (filters.get("customer_ids") or [])
                if uid
            ]
        token = filters.get("location_token")
        codes = [c for c in (filters.get("warehouse_codes") or []) if c]
        if (token or codes) and not wire["warehouse"]:
            wire["warehouse"] = [
                {
                    "raw": token,
                    "hint": "warehouse",
                    "canonical_code": token,
                    # The codes the ASKING turn already resolved the word to. A focus
                    # slot holds what the conversation is about, and for a location that
                    # is both the word the customer said and the warehouses it names -
                    # re-resolving the word is fine, losing the codes is not.
                    "warehouse_codes": codes,
                    "current_message": False,
                }
            ]
        start = start or filters.get("date_filter_start")
        end = end or filters.get("date_filter_end")

    if start or end:
        wire["date_window"] = {"mode": legacy.get("date_mode"), "start": start, "end": end}
    return wire


def pending_of(session_block: Any) -> Pending | None:
    """The one open question this contact is carrying, or None."""
    return from_wire(five_keys(session_block).get("open_question"))


def offer_is_open(session_block: Any) -> bool:
    """Is an escalation offer open? (contract 43, R3/AC-106.)

    The marker, never the previous reply's wording: the phrase-matching regex this
    replaces was `head/output_exchange.offer_is_open`, retired with that module. An offer
    kind is any pending that is not a roster - `team_pick` and `member_offer` are what
    reach here in practice, and both are `OFFER_KINDS` members by construction.
    """
    pending = pending_of(session_block)
    return pending is not None and pending.kind in OFFER_KINDS
