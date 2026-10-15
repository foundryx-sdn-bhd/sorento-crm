"""PV2 -> v1 bridge (CONTRACT-PV2-13oct.md section 5, Lookup): the 59-key v1 verdict built
from the HELD subject with RESOLVED codes, exactly as if the user had typed the codes, and
the v1 `Focus` it runs against. The engine's existing stages (turn_apply -> turn_route ->
run_fetch -> compose -> tail) then answer with today's formatters, so a PV2 reply matches
today's by construction.

Pure: no DB, no text inspection. Every key the v1 schema declares is present; a key PV2
does not set carries its "said nothing" value, derived from the v1 schema itself so the
two cannot drift.
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import date
from typing import Any

from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA
from app.services.chatbot.pv2.state import ConversationState, Held, Lookup, ResolvedRef
from app.services.chatbot.turn.policy_rows import DEFAULT_DOMAIN_ROWS
from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS
from app.services.chatbot.turn.state import EXTRA_KIND_ALIASES, KIND_FIELD_MAP, Focus

#: Domains whose replies tonight's gate covers (section 5); any other domain still runs
#: through the same bridge and is marked `pv2_ungated_domain` in the trace.
GATED_DOMAINS: frozenset[str] = frozenset({"inventory", "product_attachment", "sales"})

#: A domain's v1 intent: the first intent of its `chatbot_domains` seed row (data).
INTENT_BY_DOMAIN: dict[str, str] = {
    row["name"]: row["intents"][0] for row in DEFAULT_DOMAIN_ROWS if row.get("intents")
}

#: PV2 entity kind -> the v1 entity hint the v1 resolver reads.
V1_HINT: dict[str, str] = {
    "product": "product",
    "customer": "customer",
    "warehouse": "warehouse",
    "brand": "brand",
    "product_type": "product_type",
    "order": "order",
    "attachment": "attachment",
    "inbound_shipment": "inbound_shipment",
}


# --------------------------------------------------------------------------- #
# The blank v1 verdict
# --------------------------------------------------------------------------- #


def _blank(schema: dict[str, Any]) -> Any:
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if "null" in kinds:
        return None
    if "array" in kinds:
        return []
    if "boolean" in kinds:
        return False
    if "object" in kinds:
        return {k: _blank(v) for k, v in (schema.get("properties") or {}).items()}
    if "string" in kinds:
        return ""
    return None


def blank_verdict() -> dict[str, Any]:
    """Every declared v1 key at its "said nothing" value (nullable -> None, list -> [],
    bool -> False, object -> its members blank)."""
    props = PARSE_OUTPUT_JSON_SCHEMA["properties"]
    verdict = {key: _blank(props[key]) for key in PARSE_OUTPUT_JSON_SCHEMA["required"]}
    verdict["message_type"] = "business_query"
    verdict["match_mode"] = "and"
    verdict["entity_op"] = "replace_combine"
    return verdict


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #


def period_dates(period: Any, today: date) -> tuple[date | None, date | None]:
    """A PV2 Period as (date_from, date_to): the ONE reading `reports.period_dates` gives
    (explicit dates win over the preset), so a report and a lookup never read a period
    two ways. (None, None) when it names no period."""
    from app.services.chatbot.pv2 import reports

    span = reports.period_dates(period, today) if period is not None else None
    return span if span is not None else (None, None)


# --------------------------------------------------------------------------- #
# Verdict + focus
# --------------------------------------------------------------------------- #


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _is_current(action: Lookup, ref: ResolvedRef) -> bool:
    """A held ref is typed NOW only when this ask names its kind; a refine that names none
    carries the subject, as v1's own carry does (v1 then answers over it directly)."""
    if action.ask is None or action.picked is not None or action.pending_kind is not None:
        return True
    return ref.kind in {e.kind for e in action.ask.entities}


def _v1_entity(ref: ResolvedRef, *, current: bool = True) -> dict[str, Any]:
    if ref.kind == "customer" and _is_uuid(ref.key):
        # v1's carried-pick shape: the label as the typed words, the customer id beside it.
        return {
            "raw": ref.label,
            "hint": "customer",
            "canonical_code": None,
            "uuid": ref.key,
            "current_message": False,
            "confident": True,
            "hint_confident": True,
            "quantity": None,
            "spec_key": None,
            "spec_value": None,
            "account": None,
        }
    return {
        "raw": ref.key,
        "hint": V1_HINT.get(ref.kind, ref.kind),
        "canonical_code": ref.key,
        "current_message": current,
        "confident": True,
        "hint_confident": True,
        "quantity": ref.quantity.number if ref.quantity is not None else None,
        "spec_key": None,
        "spec_value": None,
        "account": None,
    }


def _spec_entity(spec: Any, *, current: bool) -> dict[str, Any]:
    value = spec.value
    raw = f"{value}{(' ' + spec.unit) if spec.unit else ''}"
    return {
        "raw": raw,
        "hint": "specification",
        "canonical_code": None,
        "current_message": current,
        "confident": True,
        "hint_confident": True,
        "quantity": None,
        "spec_key": spec.attribute,
        "spec_value": value,
        "account": None,
    }


def _open_question_answer(action: Lookup) -> dict[str, Any] | None:
    """A v1 open question answered through PV2 (section 5: `v1_open_question` picks pass
    through as `open_question_answer`). Option keys are the v1 positions ("1", "2") or
    "yes" / "no" / "all" (`turn.v1_pending_options`)."""
    if action.pending_kind == "v1_open_question" and action.pick_all:
        # v1's own stock pick refuses "all" for availability-only dealers; other picks take every position.
        mode = "all" if (action.pending_v1_kind or _v1_kind(None, action)) == "product_pick" else "pick"
        return {"mode": mode, "picked": [] if mode == "all" else [int(k) for k in action.pick_all if k.isdigit()],
                "items": [], "qty_for_all": None}  # `broaden_axis: "all"` rides on the verdict (below)
    if action.pending_kind != "v1_open_question" or action.picked is None:
        return None
    key = action.picked.key
    answer = {"mode": None, "picked": [], "items": [], "qty_for_all": None}
    if key in ("yes", "no", "all"):
        answer["mode"] = key
    elif key.isdigit():
        answer["mode"] = "pick"
        answer["picked"] = [int(key)]
    return answer


def to_v1_verdict(state: ConversationState, action: Lookup, *, today: date) -> dict[str, Any]:
    """The 59-key v1 verdict for one Lookup: the held subject's resolved codes named as
    this message's entities, its domain and intent, its period as dates."""
    held = action.held
    verdict = blank_verdict()
    domain = action.domain or held.domain
    verdict["domain_hint"] = domain
    verdict["intent_hint"] = INTENT_BY_DOMAIN.get(domain or "")
    entities: list[dict[str, Any]] = []
    seen_docs: set[str] = set()
    for kind in sorted(held.refs):
        for r in held.refs[kind]:
            if kind == "order" or EXTRA_KIND_ALIASES.get(kind) == "order":
                # One document is one v1 entity whichever order-axis kind named it.
                if r.key in seen_docs:
                    continue
                seen_docs.add(r.key)
            entities.append(_v1_entity(r, current=_is_current(action, r)))
    # A held attachment kind rides only where the domain takes one (a count after a photo is the stock
    # ask, RG-4).
    kinds = held.attachment_types or ([held.attachment_type] if held.attachment_type else [])
    for att in kinds if takes_attachment_kinds(domain) else []:
        entities.append(_v1_entity(ResolvedRef(kind="attachment_type", key=att, label=att)))
        if domain == "resource_attachment":
            # The entity v1's own parser sends for a catalogue / brochure request.
            entities.append(_v1_entity(ResolvedRef(kind="attachment", key=att, label=att)))
    # A spec this ask did not name is CARRIED: grounding reads a current-message descriptor
    # against the message's own words and would demote a carried "p_trap" on "stock?".
    named_specs = bool(action.ask is not None and action.ask.specs)
    entities.extend(_spec_entity(s, current=named_specs) for s in held.specs)
    for miss in (m for m in action.misses if m.phrase):
        # The typed token, unresolved, exactly as v1's parser sends it: v1's own reply
        # says it is not found.
        entities.append({
            **_v1_entity(ResolvedRef(kind=miss.kind, key=str(miss.phrase), label=str(miss.phrase))),
            "canonical_code": None, "confident": False, "hint_confident": False,
        })
    ask = action.ask
    verdict["status"] = held.status or (ask.status if ask is not None else None)
    # A portal form is this turn's subject only while the turn's domain is portal_link; a
    # form held from an earlier turn never strips the product of a later "stock?".
    on_portal = domain == "portal_link"
    verdict["portal_form"] = (held.portal_form or (ask.portal_form if ask is not None else None)) if on_portal else None
    if verdict["portal_form"]:
        # The form is the subject: a product, customer or brand beside it would turn v1's
        # found portal link into a "could not find" miss.
        entities = [e for e in entities if e["hint"] not in {V1_HINT.get(k, k) for k in ("product", "customer", "brand")}]
    verdict["entities"] = entities
    date_from, date_to = period_dates(held.period, today)
    verdict["date_filter_start"] = date_from.isoformat() if date_from else None
    verdict["date_filter_end"] = date_to.isoformat() if date_to else None
    low_stock = action.ask.low_stock if action.ask is not None else None
    if low_stock is not None:
        # The low stock report is its own ask group (contract s.1), answered by the v1
        # `low_stock_ask` lane: its intent and filter object, categories from the held
        # product types, brands from the held brands.
        verdict["intent_hint"] = "low_stock_report"
        verdict["low_stock"] = {
            "group_by": low_stock.group_by,
            "categories": [r.label for r in held.refs.get("product_type", [])],
            "all_categories": low_stock.all_categories,
            "brands": [r.key for r in held.refs.get("brand", [])],
            "suppliers": [],
        }
    answer = _open_question_answer(action)
    if answer is None and action.pending_kind == "v1_open_question" and action.free_value and action.free_kind:
        # R5-4: a typed answer to a v1 roster is this message's one entity (the held subject is carried), as
        # v1's parser sends it; v1's own roster matcher (the option's exact label) decides which position it names.
        for e in verdict["entities"]:
            e["current_message"] = False
        free = action.free_value
        verdict["entities"].append({
            **_v1_entity(ResolvedRef(kind=action.free_kind, key=free, label=free)),
            "canonical_code": None, "confident": False, "hint_confident": False,
        })
    if action.requantify:
        # A bare quantity under an open v1 stock pick: v1's requantify rule reads it as the
        # turn's demand and asks the pick again; the carried products state no quantity.
        held_qty = {r.quantity.number for r in held.refs.get("product", []) if r.quantity is not None}
        if len(held_qty) == 1:
            verdict["demand_qty"] = held_qty.pop()
        for e in verdict["entities"]:
            e["current_message"] = False
            if e["hint"] == "product":
                e["quantity"] = None
    if answer is not None and answer["mode"] == "all" and action.pick_all:
        # v1 reads "all" over a numbered menu off `broaden_axis` (decide.broadens_the_roster):
        # staff get every position, an availability-only dealer is refused by `_stock_pick_refuses_all`.
        verdict["broaden_axis"] = "all"
    if answer is not None:
        # A pick stays a business query: v1 routes "clarification" to its own clarify menu.
        verdict["open_question_answer"] = answer
        verdict["entity_op"] = "reuse"
        for e in verdict["entities"]:
            e["current_message"] = False
        # A quantity held across the pick rides as the answer's own qty_for_all; v1 refuses
        # to stamp it when a carried entity states one.
        held_qty = {r.quantity.number for r in held.refs.get("product", []) if r.quantity is not None}
        if len(held_qty) == 1:
            answer["qty_for_all"] = held_qty.pop()
            for e in verdict["entities"]:
                if e["hint"] == "product":
                    e["quantity"] = None
        if answer["mode"] in ("yes", "no"):
            verdict["is_affirmative"] = answer["mode"] == "yes"
        if answer["mode"] == "no":
            verdict["entities"] = []  # the declined offer is the turn; the subject rides on the focus
        if _v1_kind(state, action) in ESCALATION_OFFER_KINDS:
            # An offer is accepted only by an explicit "yes" or a real option pick, and
            # declined by "no"; any other reply sets neither flag.
            accepted = answer["mode"] in ("yes", "pick")
            if accepted:
                verdict["is_affirmative"] = True
            if accepted or answer["mode"] == "no":
                verdict["escalation"] = {**verdict["escalation"], "is_escalation_confirmation": accepted}
    return verdict


def _v1_kind(state: ConversationState, action: Lookup) -> str | None:
    picked = action.picked
    return (picked.payload.get("v1_kind") if picked is not None else None) or None


def _focus_row(ref: ResolvedRef) -> dict[str, Any]:
    row = _v1_entity(ref, current=False)
    row["name"] = ref.label
    return row


def focus_from_v2(held: Held, base: Focus | None = None) -> Focus:
    """The v1 Focus the engine's stages run against: the held subject (every row CARRIED,
    as `focus_from_wire` marks them) laid over the v1 focus the turn loaded, so v1-only
    axes (tier, tasks, required_ask, a ranking frame) survive the turn."""
    focus = dataclasses.replace(base) if base is not None else Focus()
    focus.products, focus.customers, focus.warehouse, focus.brands = [], [], [], []
    focus.extra = {}
    for kind, refs in held.refs.items():
        if held.domain == "portal_link" and held.portal_form and kind in ("product", "customer", "brand"):
            continue  # the form is the subject; the typed codes were never a form the user filled in
        if kind == "brand":
            focus.brands = [r.key for r in refs]
            continue
        field_name = KIND_FIELD_MAP.get(kind)
        rows = [_focus_row(r) for r in refs]
        if field_name:
            setattr(focus, field_name, rows)
        else:
            focus.extra[EXTRA_KIND_ALIASES.get(kind, kind)] = rows
    if held.domain:
        focus.domains = [held.domain]
    return focus


def is_gated(domain: str | None) -> bool:
    return domain in GATED_DOMAINS


def takes_attachment_kinds(domain: str | None) -> bool:
    """Whether v1's gate row for `domain` lets an attachment kind through (a domain with no row passes
    everything, as the gate does)."""
    from app.services.chatbot.lanes.business.gate import ALLOWED

    allowed = ALLOWED.get(domain or "")
    return allowed is None or "attachment_type" in allowed
