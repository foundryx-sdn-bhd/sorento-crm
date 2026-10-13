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

import calendar
from datetime import date, timedelta
from typing import Any

from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA
from app.services.chatbot.pv2.state import ConversationState, Held, Lookup, ResolvedRef
from app.services.chatbot.turn.policy_rows import DEFAULT_DOMAIN_ROWS
from app.services.chatbot.turn.state import EXTRA_KIND_ALIASES, KIND_FIELD_MAP, Focus

#: Domains whose replies tonight's gate covers (section 5); any other domain still runs
#: through the same bridge and is marked `pv2_ungated_domain` in the trace.
GATED_DOMAINS: frozenset[str] = frozenset({"inventory", "product_attachment", "sales_ranking", "sales"})

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


def _add_months(day: date, months: int) -> date:
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def period_dates(period: Any, today: date) -> tuple[date | None, date | None]:
    """A PV2 Period as (date_from, date_to). Explicit dates win over the preset."""
    if period is None:
        return None, None
    if period.date_from or period.date_to:
        return period.date_from, period.date_to
    preset, n = period.preset, period.n
    month_start = today.replace(day=1)
    if preset == "today":
        return today, today
    if preset == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if preset == "this_week":
        return today - timedelta(days=today.weekday()), today
    if preset == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6)
    if preset == "this_month":
        return month_start, today
    if preset == "last_month":
        end = month_start - timedelta(days=1)
        return end.replace(day=1), end
    if preset == "this_quarter":
        return _quarter_start(today), today
    if preset == "last_quarter":
        end = _quarter_start(today) - timedelta(days=1)
        return _quarter_start(end), end
    if preset in ("this_year", "ytd"):
        return date(today.year, 1, 1), today
    if preset == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if preset == "last_n_days" and n:
        return today - timedelta(days=n - 1), today
    if preset == "last_n_months" and n:
        return _add_months(today, -n) + timedelta(days=1), today
    return None, None


# --------------------------------------------------------------------------- #
# Verdict + focus
# --------------------------------------------------------------------------- #


def _v1_entity(ref: ResolvedRef, *, current: bool = True) -> dict[str, Any]:
    return {
        "raw": ref.key,
        "hint": V1_HINT.get(ref.kind, ref.kind),
        "canonical_code": ref.key,
        "current_message": current,
        "confident": True,
        "hint_confident": True,
        "quantity": None,
        "spec_key": None,
        "spec_value": None,
        "account": None,
    }


def _spec_entity(spec: Any) -> dict[str, Any]:
    value = spec.value
    raw = f"{value}{(' ' + spec.unit) if spec.unit else ''}"
    return {
        "raw": raw,
        "hint": "specification",
        "canonical_code": None,
        "current_message": True,
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
    for kind in sorted(held.refs):
        entities.extend(_v1_entity(r) for r in held.refs[kind])
    if held.attachment_type:
        entities.append(
            _v1_entity(ResolvedRef(kind="attachment_type", key=held.attachment_type, label=held.attachment_type))
        )
    entities.extend(_spec_entity(s) for s in held.specs)
    verdict["entities"] = entities
    date_from, date_to = period_dates(held.period, today)
    verdict["date_filter_start"] = date_from.isoformat() if date_from else None
    verdict["date_filter_end"] = date_to.isoformat() if date_to else None
    answer = _open_question_answer(action)
    if answer is not None:
        verdict["message_type"] = "clarification"
        verdict["open_question_answer"] = answer
        verdict["entity_op"] = "reuse"
        if answer["mode"] in ("yes", "no"):
            verdict["is_affirmative"] = answer["mode"] == "yes"
    return verdict


def _focus_row(ref: ResolvedRef) -> dict[str, Any]:
    row = _v1_entity(ref, current=False)
    row["name"] = ref.label
    return row


def focus_from_v2(held: Held) -> Focus:
    """The v1 Focus the engine's stages run against: the held subject only (every row
    CARRIED, as `focus_from_wire` marks them), nothing v1-only carried over."""
    focus = Focus()
    for kind, refs in held.refs.items():
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
