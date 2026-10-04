# route: Plan in, one branch kind out - and nothing else (PLAN-chatbot-turn-rearch.md
# "APPLY contract", AC-1528). Stage D of the seven.
#
# Thirteen branch kinds, but only six DECISIONS (the plan's own stage D: "ask | business
# | escalation | casual | canned | denied"); the kinds are what the lanes, the trace
# screen and n8n's own arm names have always been called, so the mapping stays visible
# here rather than being renamed underneath them.
#
# Two kinds are deliberately absent: `access_denied` from a refused access agent, and
# `stock_denied` / `demand_qty`, which are decided from the CONTACT's own record before a
# plan exists at all (contract 58, 61, 62). The engine settles those before it routes.
from __future__ import annotations

from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS
from app.services.chatbot.turn.plan import Plan

_LANE_BRANCH: dict[str, str] = {
    "escalation": "out_of_scope",
    # ESCALATION-CONTROL: a barred contact's forced hand-off keeps the escalation arm's
    # branch kind (the wire contract has no new kind); the engine answers it with the
    # salesperson referral before `_run_escalation_arm` could hand anything over.
    "escalation_barred": "out_of_scope",
    "escalation_declined": "escalation_declined",
    # A decline over a NON-escalation offer (a did-you-mean roster's own attached
    # escalate sentence, a detail offer) finishes under the SAME branch kind as an
    # actual escalation decline (AC-1703's tail) - `apply.py` stamps
    # `trace.lane = "offer_declined"` only so the trace screen shows which of the two
    # this turn actually was; `escalate_catalog` reads that distinction back off
    # `ctx.parse.output` (a flag `lane_parse_output` sets), not off a second branch
    # kind, per the captain's ruling (20 Sep 2026): main itself has no `branch_kind`
    # wire concept, so this is a copy-key choice inside one existing arm, not new scope.
    "offer_declined": "escalation_declined",
    "not_supported": "not_supported",
    # #865 round 6 (R5): junk over an open multi-company escalation offer re-asks the
    # company (`apply._holds_the_offer`), on the canned lane n8n named `offer_hold`.
    "offer_hold": "offer_hold",
    "clarification": "clarify_menu",
    "casual": "low_signal",
    # S3 (chatbot media-into-turn): bare entities with no domain and no carried
    # focus still answer as a business question (a deterministic one, `apply.py`'s
    # own `entities_only` arm - not the generic fetch/ask machinery), never the
    # `casual` lane's LLM clarifier.
    "entities_only": "business_query",
}


def _domain_branch(domains: list[str]) -> str:
    """Which arm a turn with a resolved domain belongs to.

    `ideate` and `promotion` have arms of their own (`contracts.BRANCH_KINDS`); every
    other domain answers on the generic business arm. `check_promotion` is the promotion
    domain's own name for it - `trace.py` labels it "Business query: promotion" and
    `contracts.BUSINESS_BRANCH_KINDS` already carries it beside `business_query`, so a
    promotion turn that came back as `business_query` was landing on an arm that is not
    its own (measured: 261 real `check_promotion` turns in the 0915 copy, none of which
    this router could produce).
    """
    if "ideate" in domains:
        return "ideate"
    if "promotion" in domains:
        return "check_promotion"
    return "business_query"


def route(plan: Plan) -> str:
    # A question outranks everything: a turn that cannot say WHAT it is about has nothing
    # to fetch and nobody to escalate to yet (contract 111 - did-you-mean before the team
    # question).
    if plan.ask is not None:
        # A pending kind the turn is ASKING becomes the arm that owns that question, and
        # the three escalation offers are the escalation arm's (`pending.py`, the same
        # set `apply` routes their ANSWER by).
        if plan.ask.kind in ESCALATION_OFFER_KINDS:
            return "escalate_offer"
        # A NARROWING question belongs to the arm that asked it, not to `clarify_menu`.
        # `clarify_menu` is contract 50's DOMAIN menu - "I see you are trying to X, are
        # you asking about any of these? - Product - Stock ..." - a turn with no domain
        # at all. A product roster, a customer picker, a did-you-mean or a scope question
        # is the business lane mid-question, and it is a business turn: measured on the
        # 0915 copy, 413 "Please choose", 178 "Did you mean" and 195 "Which ... do you
        # mean" turns are all recorded `business_query`, while every one of the 36 real
        # `clarify_menu` turns is the domain menu. It also matters downstream:
        # `contracts.TAG_ONLY_BRANCH_KINDS` carries `clarify_menu`, so an arm that emits
        # the branch kind and nothing else would strip the very roster it is asking.
        if not plan.domains:
            return "clarify_menu"
        return _domain_branch(plan.domains)

    if plan.denied and not plan.fetch:
        return "access_denied"

    lane = plan.trace.lane
    if lane in _LANE_BRANCH:
        return _LANE_BRANCH[lane]

    if "ideate" in plan.domains:
        return "ideate"

    if plan.fetch:
        return _domain_branch(plan.domains)

    if plan.trace.task_question:
        # Ported from PR #1118 (not merged) for chatbot-stock-ask-v2 S3: a task
        # RESUMED with nothing new fetches nothing and asks no roster - the task's own
        # question is the whole turn - but it is still a question about that task's
        # domain, not a low-signal aside.
        return _domain_branch(plan.domains)

    return "low_signal"
