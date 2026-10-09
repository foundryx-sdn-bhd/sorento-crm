"""How a closed turn ENDED, read from its trace: the pure half of the chat facts (CA-1).

**No side effects.** No database library, no ORM handle, no import of one: `turn_outcome`
and `flatten` only look at the turn dict they are handed, so the live hook, the nightly
sweep and the tests all get the identical answer from the identical trace (the same
promise `turn/episode_digest.py` makes for episode summaries, and the same reason the
outcome is read from structure the engine really writes - `branch_kind`, the `apply`
record's plan, the `looked_up` stage's `facts`, a handful of kind events - never from a
rendered reply, which can say anything).

**No customer words, ever.** The `turn` dict a caller builds may carry the customer's
message; nothing here reads it. An entity travels only as its `canonical_code` (a CRM
code the parser resolved), never as the `raw` words the customer typed, and a shown row
travels only as its `code`, never its label. `tests/chatbot/test_chat_facts_outcome.py`
pins that with sentinel strings.

Input: the `memory._turn_to_digest_dict` shape plus `status`:
`{branch_kind, status, trace}` where `trace` is the list of stage records (`stage`,
`status`, `facts`, `raw`; NO `kind`) and kind events (`kind`, `at`, ...) the engine writes.
"""
from __future__ import annotations

from typing import Any

#: Outcomes that mean the bot let the customer down (the report's `failed` measure). A
#: refusal by policy, a handover and a clarifying question are the bot working as designed.
FAILED_OUTCOMES: tuple[str, ...] = ("technical", "cannot_do", "missed")

#: `branch_kind` values that are a denial by policy.
_DENIAL_BRANCHES = frozenset({"access_denied", "ai_not_allowed", "stock_denied"})

#: `branch_kind` values that ask the customer something back instead of answering.
_CLARIFY_BRANCHES = frozenset({"clarify_menu", "demand_qty", "escalate_offer", "offer_hold"})

#: The `domain_grant` event's `skipped` value when the contact lacks the reveal key.
_NOT_GRANTED = "not_granted"

#: Entity hints that become items. Customers, dates and the rest are not catalogue.
_ITEM_HINTS = ("product", "brand")


def _records(turn: dict[str, Any]) -> list[dict[str, Any]]:
    trace = turn.get("trace")
    return [r for r in trace if isinstance(r, dict)] if isinstance(trace, list) else []


def _events(turn: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [r for r in _records(turn) if r.get("kind") == kind]


def _stage(turn: dict[str, Any], name: str) -> dict[str, Any] | None:
    for record in _records(turn):
        if record.get("kind") is None and record.get("stage") == name:
            return record
    return None


def _apply(turn: dict[str, Any]) -> dict[str, Any]:
    entries = _events(turn, "apply")
    return entries[-1] if entries else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _verdict(turn: dict[str, Any]) -> dict[str, Any]:
    """The parser's verdict. The engine writes the `apply` event only from 24 Sep 2026;
    earlier live turns keep the verdict on the `understood` stage, so read that when there
    is no apply event (an apply event wins entirely)."""
    if _apply(turn):
        return _dict(_apply(turn).get("verdict"))
    understood = _stage(turn, "understood")
    facts = _dict(understood.get("facts")) if understood else {}
    return {
        "domain_hint": facts.get("domain"),
        "intent_hint": facts.get("intent"),
        "message_type": facts.get("message_type"),
    }


def _plan(turn: dict[str, Any]) -> dict[str, Any]:
    return _dict(_apply(turn).get("plan"))


def _looked_up_facts(turn: dict[str, Any]) -> dict[str, Any] | None:
    record = _stage(turn, "looked_up")
    return None if record is None else _dict(record.get("facts"))


def _asked_for_a_person(turn: dict[str, Any]) -> bool:
    """The customer confirmed, offered or named a team, or accepted the bot's offer to hand
    over. All structured: the parser's escalation fields, its message type, and the APPLY
    rule that read an accepted offer (`answer_pending_accept`)."""
    escalation = _dict(_verdict(turn).get("escalation"))
    return bool(
        escalation.get("is_escalation_confirmation") is True
        or _verdict(turn).get("message_type") == "escalation"
        or "answer_pending_accept" in (_apply(turn).get("rules_fired") or [])
        or escalation.get("named_teams")
    )


def _names_a_business(turn: dict[str, Any]) -> bool:
    """The ask points at something the CRM knows: a domain the parser hinted or the plan
    acted on, or an intent."""
    verdict = _verdict(turn)
    return bool(
        _present(verdict.get("domain_hint"))
        or _plan(turn).get("domains")
        or _present(verdict.get("intent_hint"))
    )


def _present(hint: Any) -> bool:
    """A hint the parser really gave. It sometimes writes the literal string "null" (or
    leaves it blank) for "nothing", and that names no business."""
    return bool(hint) and str(hint).strip().lower() not in ("", "null")


def _still_asking(turn: dict[str, Any]) -> bool:
    """A `required_ask` event that actually asked this turn. The low-stock and report lanes
    write `done: False` while they still need a slot filled (and `done: True` once settled);
    the engine writes `{verdict_rule, ask}` on the turn AFTER the question, when the
    customer answered it or it was dropped. Only the first is a question put to the
    customer."""
    return any(e.get("done") is False for e in _events(turn, "required_ask"))


def turn_outcome(turn: dict[str, Any]) -> tuple[str, str | None]:
    """`(outcome, failure_reason)` for one closed turn. First matching rule wins.

    The order is the contract (PLAN "Outcome rules"): a failed turn is `technical`
    whatever else it holds; noise is never a refusal; a barred escalation is a policy
    refusal even though the branch says out of scope; a clarifying ask beats a miss
    because the bot asked instead of searching. `failure_reason` equals the outcome for
    the three failed outcomes and is None otherwise.
    """
    if turn.get("status") == "failed":
        return "technical", "technical"

    branch = turn.get("branch_kind")
    plan = _plan(turn)

    if branch == "low_signal":
        return "noise", None
    if plan.get("lane") == "escalation_barred":
        return "refused_policy", None
    if branch == "out_of_scope":
        # The lane always hands the thread to staff, but only some customers ASKED for that.
        # One who asked for no person and named no business ("hello can you help me?") got
        # a handover nobody wanted: off topic, which is not a failure of the bot's.
        if _asked_for_a_person(turn) or _names_a_business(turn):
            return "handover", None
        return "off_topic", None

    if (
        branch in _DENIAL_BRANCHES
        or any(e.get("skipped") == _NOT_GRANTED for e in _events(turn, "domain_grant"))
        or (plan.get("denied") and not plan.get("fetch"))
    ):
        return "refused_policy", None

    if branch == "not_supported":
        return "cannot_do", "cannot_do"

    if branch in _CLARIFY_BRANCHES or plan.get("ask") or _still_asking(turn):
        return "clarified", None

    looked_up = _looked_up_facts(turn)
    if (looked_up or {}).get("missed") or any(
        "answered" in e and e["answered"] is None for e in _events(turn, "crossdomain")
    ):
        return "missed", "missed"

    # `sections` absent means the stage did not report it, which is not an empty answer.
    if looked_up is not None and looked_up.get("sections") == 0:
        return "answered_empty", None

    return "answered", None


def _domain(turn: dict[str, Any]) -> str | None:
    domains = _plan(turn).get("domains") or []
    if domains:
        return str(domains[0])
    hint = _verdict(turn).get("domain_hint")
    return str(hint) if _present(hint) else None


def _intent(turn: dict[str, Any]) -> str | None:
    hint = _verdict(turn).get("intent_hint")
    return str(hint) if _present(hint) else None


def _tools(turn: dict[str, Any]) -> list[str]:
    """Distinct tool names in call order. The generic fetch path writes only `{domain}`
    on its tool event, so a tool event with no `name` contributes its domain instead."""
    seen: list[str] = []
    for event in _events(turn, "tool"):
        name = event.get("name") or event.get("domain")
        if name and str(name) not in seen:
            seen.append(str(name))
    return seen


def _named_codes(turn: dict[str, Any]) -> dict[str, list[str]]:
    named: dict[str, list[str]] = {hint: [] for hint in _ITEM_HINTS}
    for entity in _verdict(turn).get("entities") or []:
        entity = _dict(entity)
        hint = entity.get("hint")
        code = entity.get("canonical_code")
        if hint not in named or not code:
            continue
        code = str(code).strip()
        if code and code not in named[hint]:
            named[hint].append(code)
    return named


def _shown_codes(turn: dict[str, Any]) -> list[str]:
    reply = _dict(_dict((_stage(turn, "replied") or {}).get("raw")).get("reply"))
    shown: list[str] = []
    for row in reply.get("result_set") or []:
        code = _dict(row).get("code")
        code = str(code).strip() if code else ""
        if code and code not in shown:
            shown.append(code)
    return shown


def _rows_offered(turn: dict[str, Any]) -> int | None:
    value = _dict((_stage(turn, "replied") or {}).get("facts")).get("rows_offered")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def flatten(turn: dict[str, Any]) -> dict[str, Any]:
    """The fact fields derivable from the trace alone. JSON-serialisable.

    `phantom_dropped` is a flag, not an outcome: a phantom answer was caught and removed
    before the customer saw it, so the ask still ended the way the rules above say.
    """
    outcome, failure_reason = turn_outcome(turn)
    return {
        "domain": _domain(turn),
        "intent": _intent(turn),
        "outcome": outcome,
        "failure_reason": failure_reason,
        "branch_kind": turn.get("branch_kind"),
        "tools": _tools(turn),
        "rows_offered": _rows_offered(turn),
        "phantom_dropped": bool(_events(turn, "phantom_answer")),
        "named_codes": _named_codes(turn),
        "shown_codes": _shown_codes(turn),
    }
