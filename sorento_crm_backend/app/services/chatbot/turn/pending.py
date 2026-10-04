# Pending: one object, one constructor (PLAN-chatbot-turn-rearch.md "APPLY contract",
# AC-1521). Every `Pending(` call site lives here; every resolver lives in apply.py.
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# The lane's eight offer/roster kinds plus the reconciliation-only ninth, and the sales
# report's own detail offer (PLAN-chatbot-sales-report.md S4 wiring point 7): a KIND is
# added, an arm is not - every site that used to test the literal `"outstanding_detail"`
# tests membership in `contracts.DETAIL_OFFER_KINDS` instead, so the two share one
# mechanism rather than one being a copy of the other.
PENDING_KINDS: tuple[str, ...] = (
    "product_pick",
    "customer_pick",
    "tier_pick",
    "team_pick",
    "company_pick",
    "member_offer",
    "outstanding_scope",
    "outstanding_detail",
    "sales_report_detail",
    "kind_pick",
    "top_selling_pick",
)

# Stay alive, tracked by `answered_positions`, after their own pick.
#
# `tier_pick` (hand pass 9, D3, owner ruling 20 Sep 2026): main's fresh-typed picker
# gate (`tail/compile_state.py`) draws no kind-based distinction at all - a
# `require_specific` roster stays answerable across as many picks as the customer
# makes, and only a message that TYPES a fresh entity retires it (that same gate). The
# tier ask is the identical shape (a numbered list, one axis), so treating it as a one-off
# offer that clears on its own pick was the gap: a SECOND, different tier position over
# the same still-open roster re-printed the FIRST pick's own reply instead of running a
# fresh fetch for the tier just named (live turn 25dcef1d-decc-407b-a6eb-08d8112ee814).
# `turn/apply.py::_answer_pending` already rebuilds `focus.tier`/`access_levels` fresh
# from EACH turn's own matched option (never accumulated across picks), so a roster kind
# here does not need a parallel "replace, don't merge" rule - the existing pick-handling
# code already replaces the tier filter every time, the same way a product roster's
# second pick answers with only that pick's own product.
#
# `top_selling_pick` (owner, PR #1258, 26 Sep 2026 05:32Z): the top X ranking is a
# numbered list of choices and must behave like the customer and product pickers, so it
# is one of them rather than a detail offer that clears on its first answer. A "2" or a
# typed code / category name resolves through the same `decide.picked_positions`, and it
# closes under the same two rules (every row picked, or a new ask about something else).
ROSTER_KINDS: frozenset[str] = frozenset(
    {"product_pick", "customer_pick", "kind_pick", "tier_pick", "top_selling_pick"}
)


def is_roster(kind: str) -> bool:
    """Does this pending stay alive after its own pick (contract 36)?

    Not a list lookup, because the list cannot be complete. `narrow.decide` mints the
    ask's kind as `f"{entity_kind}_pick"` / `f"{entity_kind}_ask"` straight off
    `chatbot_entity_kinds`, an OPERATOR-editable table: measured on the seeded rows,
    twelve kinds exist and only `product`, `customer`, `tier` and `attachment_type` carry
    a narrowing policy today, so the Chatbot Domains screen can mint `brand_pick`,
    `warehouse_pick` or `order_pick` the moment somebody sets one. Those read as "not a
    roster" against the nine literals and lost contract 36's sticky roster silently.

    The remaining eight named kinds keep their own classification (`team_pick` and
    `company_pick` end in `_pick` and are one-off offers, not rosters); anything the
    narrower minted outside them is a candidate list, and a candidate list is a roster.
    """
    if kind in ROSTER_KINDS:
        return True
    if kind in PENDING_KINDS:
        return False
    return kind.endswith("_pick") or kind.endswith("_ask")


@dataclass(frozen=True)
class Pending:
    kind: str
    expects: str | None
    options: list[dict[str, Any]]
    team: str | None
    payload: dict[str, Any]
    asked_at_turn: int | None

    @property
    def answered_positions(self) -> list[int]:
        return list(self.payload.get("answered_positions") or [])


def ask(
    kind: str,
    options: list[dict[str, Any]],
    *,
    team: str | None = None,
    asked_at_turn: int | None = None,
    expects: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Pending:
    return Pending(
        kind=kind,
        expects=expects,
        options=options,
        team=team,
        payload=dict(payload or {}),
        asked_at_turn=asked_at_turn,
    )


def top_selling_pick(
    result_set: list[dict[str, Any]] | None,
    *,
    asked_at_turn: int | None,
    filters: dict[str, Any],
) -> Pending | None:
    """The top X ranking's printed lines as a sticky pick list (`top_selling_pick`).

    `result_set` is `presenters._top_selling_envelope`'s own, one `{idx, label, code,
    entity_type}` row per printed line (no name: owner ruling 26 Sep ~07:40Z, so the
    option's `name` stays None), `idx` the printed rank. The option shape is
    the roster one `turn/narrow.py::_options` builds, so every reader of a customer or
    product roster reads this one unchanged. `status` / `domain` send a pick back to the
    ranking that printed the list (AC-1704's carry), `filters` is what re-runs it. The
    how-many reply and a miss print no list and arm nothing.
    """
    options = [
        {
            "position": int(row["idx"]),
            "label": row.get("label"),
            "code": row.get("code"),
            "name": row.get("name"),
            "entity_type": row.get("entity_type") or "product",
            "payload": {},
        }
        for row in (result_set or [])
        if isinstance(row, dict) and isinstance(row.get("idx"), int) and row.get("label")
    ]
    if not options:
        return None
    return ask(
        "top_selling_pick",
        options,
        asked_at_turn=asked_at_turn,
        payload={"domain": "order", "status": "top_selling", "filters": dict(filters)},
    )


def with_answered_positions(pending: Pending, positions: list[int]) -> Pending:
    # A roster kind's own re-ask: the SAME pending, carrying which positions this
    # turn's pick answered, layered onto whatever was already answered before.
    merged = sorted(set(pending.answered_positions) | set(positions))
    payload = dict(pending.payload)
    payload["answered_positions"] = merged
    return Pending(
        kind=pending.kind,
        expects=pending.expects,
        options=pending.options,
        team=pending.team,
        payload=payload,
        asked_at_turn=pending.asked_at_turn,
    )


# Offer kinds clear when they are answered; the roster kinds above stay alive.
OFFER_KINDS: frozenset[str] = frozenset(PENDING_KINDS) - ROSTER_KINDS

# The three offers the ESCALATION lane owns, both halves of them. `route()` sends the
# ASK to `escalate_offer` off this set, and `apply._answer_pending` sends the ANSWER
# back to the escalation lane off the same one - the arm that asks and the arm that
# answers must not be able to disagree about which questions are escalation questions.
# The other three offer kinds are BUSINESS questions (`tier_pick` is contract 15's price
# tier, `outstanding_scope` and `outstanding_detail` are contract 38 and 39): accepting
# one of those is a fetch, not a handover.
ESCALATION_OFFER_KINDS: frozenset[str] = frozenset({"team_pick", "member_offer", "company_pick"})


def offered_companies(pending: Pending | None) -> list[dict[str, Any]]:
    """The companies an open escalation offer put in front of the customer, in order.

    #865 round 6 (n8n `miss-company-routing` (A): "POOL = the companies actually
    OFFERED"). A member picker names its pool on its own payload (`roster_plan`, the
    companies whose rosters it printed); a team offer or a company clarify names one
    company per option. One row per company, `{company_id, company_name, brand_code}`,
    the `routing_roster_plan` row shape; `[]` for an offer that names no company.
    """
    if pending is None or pending.kind not in ESCALATION_OFFER_KINDS:
        return []
    plan = pending.payload.get("roster_plan")
    if isinstance(plan, list) and plan:
        source = [
            {
                "company_id": row.get("company_id"),
                "company": row.get("company_name"),
                "brand_code": row.get("brand_code"),
            }
            for row in plan
            if isinstance(row, dict)
        ]
    else:
        source = [
            o.get("payload") for o in pending.options if isinstance(o, dict) and isinstance(o.get("payload"), dict)
        ]
    rows: list[dict[str, Any]] = []
    for entry in source:
        name = entry.get("company")
        if not isinstance(name, str) or not name.strip() or any(r["company_name"] == name for r in rows):
            continue
        rows.append(
            {
                "company_id": entry.get("company_id") or None,
                "company_name": name,
                "brand_code": entry.get("brand_code") or None,
            }
        )
    return rows


def quick_replies_suppressed(kind: str | None) -> bool:
    """Does this pending's own options get withheld from `quick_replies`?

    Owner ruling 23 Sep 2026: a `member_offer` (the CS member picker) already prints
    its numbered text list ("Please choose who to route to (reply with the number):
    1. Maryam Ariffin ..."), and that is enough - the same names must NOT also go out
    as WhatsApp quick-reply buttons (a wall of up to a dozen taps). `result_set` stays
    populated (a numbered reply still resolves through it); only `quick_replies` is
    suppressed, and only for this one kind - `team_pick`, `company_pick` and every
    roster kind keep theirs. The top X ranking (`top_selling_pick`) is the second
    (owner retest of top selling, 27 Sep 2026: about 100 product chips under a Top 100
    answer); its one "Reply with a rank number" line is the offer.

    One predicate, called from every seam that turns a pending's options into the
    `quick_replies` string, so the rule lives in one place rather than being
    reimplemented (and risking drift) at each site.
    """
    return kind in ("member_offer", "top_selling_pick")


#: WhatsApp shows at most three reply buttons (owner retest of top selling, 27 Sep
#: 2026: "the whatsapp will explode"). A question with more options than this goes out
#: as its numbered text list alone, never as a wall of chips.
MAX_QUICK_REPLIES = 3


def quick_replies(kind: str | None, labels: list[str]) -> str | None:
    """n8n's `quick_replies` for one question: the option labels comma joined, or None
    when the kind withholds them (`quick_replies_suppressed`) or there are more than
    `MAX_QUICK_REPLIES` of them. The one seam every caller turns options into chips."""
    kept = [str(label) for label in labels if label]
    if quick_replies_suppressed(kind) or not kept or len(kept) > MAX_QUICK_REPLIES:
        return None
    return ", ".join(kept)


#: AC-816 rule 1: how many turns an unanswered escalation offer stays on the customer's
#: screen. Three, the did-you-mean offer's own lifetime, because "unanswered" is not a
#: licence to live forever - an offer the customer simply ignored used to be re-armed on
#: every later turn, so a bare "yes" about something else three or twenty turns on still
#: read as an escalation confirmation and assigned a human to a conversation nobody had
#: asked to escalate. A constant, not a setting: `system_settings` carries no chatbot TTL
#: column today (checked), and one preference does not need a table.
OFFER_TTL = 3


def tick(pending: Pending | None) -> Pending | None:
    """One more turn has begun: the open offer's clock ticks, and at zero it is gone.

    Read at the LOAD seam (`turn_runtime.load_state`), so an expired offer is simply not
    there - APPLY, the router, the parser's option list and the tail all agree about it
    without any of them having to know the rule. The remaining count rides on the marker
    itself (AC-816: "rather than a session key of its own, because the marker is already
    what says the offer is open and a second key could disagree with it"), and the write
    is automatic: the tail stores the pending the turn CARRIED, which is this ticked one.
    A marker with no count - written by n8n, or before this rule shipped - reads as open
    and starts its clock on this turn.

    Only the three ESCALATION offers have a clock. The business questions do not: R22
    ruled `outstanding_scope` / `outstanding_detail` sticky on purpose ("a TTL would
    close it behind a customer who is still reading it"), and the roster kinds are
    contract 36's sticky roster by definition. What expires is the offer whose stale
    acceptance hands a human a conversation nobody asked to escalate, and that is
    `_answer_offer`'s own set.

    A turn that re-prints the question or narrows it still SPENDS one turn against the
    clock - or the narrow arm becomes the unbounded carry again (AC-816 rule 1's own
    words).
    """
    if pending is None or pending.kind not in ESCALATION_OFFER_KINDS:
        return pending
    carried = pending.payload.get("ttl")
    remaining = (carried if isinstance(carried, int) else OFFER_TTL) - 1
    if remaining <= 0:
        return None
    payload = dict(pending.payload)
    payload["ttl"] = remaining
    return replace(pending, payload=payload)


def to_wire(pending: Pending | None) -> dict[str, Any] | None:
    """`session_vars.open_question` - the ONE open question, as stored (AC-1504)."""
    if pending is None:
        return None
    return {
        "kind": pending.kind,
        "expects": pending.expects,
        "options": list(pending.options),
        "team": pending.team,
        "asked_at_turn": pending.asked_at_turn,
        "payload": dict(pending.payload),
    }


def from_wire(raw: Any) -> Pending | None:
    """The inverse. An `open_question` written by an older build carries only `kind` and
    `options`; the missing members read as "not stated", never as a failed turn."""
    if not isinstance(raw, dict) or not raw.get("kind"):
        return None
    options = raw.get("options")
    payload = raw.get("payload")
    return Pending(
        kind=str(raw["kind"]),
        expects=raw.get("expects"),
        options=[o for o in options if isinstance(o, dict)] if isinstance(options, list) else [],
        team=raw.get("team"),
        payload=dict(payload) if isinstance(payload, dict) else {},
        asked_at_turn=raw.get("asked_at_turn"),
    )


def without_escalation(question: Any) -> tuple[Any, list[dict[str, Any]] | None]:
    """ESCALATION-CONTROL: `question` with every escalation part taken out, and the
    options that went (`escalation_control.strip_text`, `apply.apply`'s barred entry).

    An escalation offer kind goes whole; a roster keeps its business options and loses
    its member (routing) options, its `escalate_offered` stamp and its team."""
    if question is None:
        return None, None
    if question.kind in ESCALATION_OFFER_KINDS:
        return None, [o for o in (question.options or []) if isinstance(o, dict)]
    members = [o for o in (question.options or []) if isinstance(o, dict) and o.get("entity_type") == "member"]
    payload = dict(question.payload or {})
    offered = payload.pop("escalate_offered", None) is True
    if not members and not offered:
        return question, None
    kept = [o for o in (question.options or []) if not (isinstance(o, dict) and o.get("entity_type") == "member")]
    if not kept:
        return None, members
    return replace(question, options=kept, payload=payload, team=None), members
