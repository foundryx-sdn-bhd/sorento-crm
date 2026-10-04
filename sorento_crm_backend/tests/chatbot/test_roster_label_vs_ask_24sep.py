"""A message that names its own domain is an ask, never a roster pick by label
(PLAN-chatbot-roster-label-vs-ask-24sep.md, owner ruling 24 Sep 2026).

Evidence: prod turns 336-342, contact 900000039, 23 Sep 2026 19:48-19:50 MYT. Turns
339 ("Photo srt446-RG") and 342 ("Srt446-RG list price") were misread as a roster
LABEL PICK (`turn/decide.py::picked_positions` -> `_positions_by_label`) even though
the parser itself said `domain_in_message: true` - the message named its own
question. The fix is one condition: the label-match arm counts only when
`domain_in_message(verdict)` is not `True`. `reference_positions` and the "all"
broaden are untouched, and every kind that label-matches today (`product_pick`,
`customer_pick`, `tier_pick`, non-escalation offer kinds) reads the same way.

Engine-level through `apply()` / `route()`, same shape as
`test_rearch_handpass3_owner_17sep.py::_decide` / `_roster`; the parser is mocked via
`tests.chatbot._turn_helpers.verdict`.
"""
from __future__ import annotations

from app.services.chatbot.turn.decide import ANSWER, NEW_ASK
from tests.chatbot._turn_helpers import build_policy, entity, verdict


def _decide(v: dict, *, pending=None, focus=None):
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.route import route
    from app.services.chatbot.turn.state import Focus, Profile, State

    state = State(focus=focus or Focus(), pending=pending, profile=Profile())
    state2, plan = apply(state, v, build_policy())
    return state2, plan, route(plan)


def _roster(kind: str, options: list[dict], **payload_kw):
    from app.services.chatbot.turn.pending import ask

    return ask(kind, options=options, payload=payload_kw)


def _product_option(position: int, code: str, uuid_: str) -> dict:
    return {
        "position": position,
        "label": code,
        "code": code,
        "uuid": uuid_,
        "uuids": [uuid_],
        "payload": {"value": code},
        "entity_type": "product",
    }


def _tier_option(position: int, label: str, code: str) -> dict:
    return {
        "position": position,
        "label": label,
        "code": code,
        "uuid": None,
        "uuids": [],
        "payload": {"value": code},
        "entity_type": "tier",
    }


# The turn-339/342 roster: SRT446-RG (answered position 1 already), SRTWT5866-RG,
# SRT449-RG - a did-you-mean over "purchase cost srt466-RG" (typo).
_PRODUCT_OPTIONS = [
    _product_option(1, "SRT446-RG", "3a262365-41ce-498f-a86c-6300c6ea0cc9"),
    _product_option(2, "SRTWT5866-RG", "5cc1f200-5d56-4d22-9636-0a0b35116191"),
    _product_option(3, "SRT449-RG", "65870d70-243b-42da-b11e-609fb4f3c0aa"),
]


def _product_pick_roster() -> "object":
    return _roster(
        "product_pick",
        list(_PRODUCT_OPTIONS),
        domain="purchase_cost",
        agent="general_enquiries",
        escalate_offered=True,
        answered_positions=[1],
    )


class TestAC1860PhotoOverARosterIsAnAskForTheAttachmentDomain:
    def test_photo_srt446_rg_plans_product_attachment_not_a_label_pick(self) -> None:
        pending = _product_pick_roster()
        v = verdict(
            entities=[
                entity("srt446-RG", "product", canonical_code="SRT446-RG"),
                entity("Photo", "attachment_type", canonical_code="Photo Attachment"),
            ],
            domain_hint="product_attachment",
            domain_in_message=True,
            reference_positions=[],
        )
        state2, plan, _branch = _decide(v, pending=pending)

        assert plan.trace.decision == {"kind": NEW_ASK, "why": "domain_in_message"}, plan.trace.decision
        assert plan.domains == ["product_attachment"], plan.domains
        assert "domain_locked_by_pick" not in plan.trace.rules_fired, plan.trace.rules_fired

        assert state2.pending is not None, "the roster must stay open"
        assert state2.pending.kind == "product_pick"
        assert state2.pending.answered_positions == [1], (
            "the ask must not re-answer the roster - "
            f"got {state2.pending.answered_positions!r}"
        )


class TestAC1861ListPriceOverTheSameRosterPlansMasterProducts:
    def test_srt446_rg_list_price_plans_master_products(self) -> None:
        pending = _product_pick_roster()
        v = verdict(
            entities=[entity("Srt446-RG", "product", canonical_code="SRT446-RG")],
            domain_hint="master_products",
            domain_in_message=True,
            requested_attributes=["price"],
            reference_positions=[],
        )
        state2, plan, _branch = _decide(v, pending=pending)

        assert plan.trace.decision == {"kind": NEW_ASK, "why": "domain_in_message"}, plan.trace.decision
        assert plan.domains == ["master_products"], plan.domains
        assert "domain_locked_by_pick" not in plan.trace.rules_fired, plan.trace.rules_fired
        assert state2.pending is not None and state2.pending.kind == "product_pick"


class TestAC1862TheBareCodeIsStillAPickByLabel:
    def test_bare_srt446_rg_still_answers_by_label_match(self) -> None:
        pending = _product_pick_roster()
        v = verdict(
            entities=[entity("SRT446-RG", "product", canonical_code="SRT446-RG")],
            domain_hint=None,
            domain_in_message=False,
            reference_positions=[],
        )
        state2, plan, _branch = _decide(v, pending=pending)

        assert plan.trace.decision == {"kind": ANSWER, "why": "label_match"}, plan.trace.decision
        assert plan.domains == ["purchase_cost"], plan.domains
        # Issue #1352: the lock that ignored domain_hint under ANY pick is retired; a
        # bare pick (no domain word of its own) answers in the roster's domain.
        assert "pick_in_roster_domain" in plan.trace.rules_fired, plan.trace.rules_fired
        assert "domain_locked_by_pick" not in plan.trace.rules_fired, plan.trace.rules_fired
        assert state2.pending is not None
        assert 1 in state2.pending.answered_positions, state2.pending.answered_positions


class TestAC1863ABarePositionIsStillAPick:
    def test_bare_2_still_answers_by_position(self) -> None:
        pending = _product_pick_roster()
        v = verdict(
            entities=[],
            domain_hint=None,
            domain_in_message=False,
            reference_positions=[2],
        )
        state2, plan, _branch = _decide(v, pending=pending)

        assert plan.trace.decision == {"kind": ANSWER, "why": "positions"}, plan.trace.decision
        assert plan.domains == ["purchase_cost"], plan.domains
        # Issue #1352: the lock that ignored domain_hint under ANY pick is retired; a
        # bare pick (no domain word of its own) answers in the roster's domain.
        assert "pick_in_roster_domain" in plan.trace.rules_fired, plan.trace.rules_fired
        assert "domain_locked_by_pick" not in plan.trace.rules_fired, plan.trace.rules_fired
        assert any(
            (p.get("canonical_code") or p.get("raw")) == "SRTWT5866-RG"
            for p in state2.focus.products
        ), state2.focus.products
        assert state2.pending is not None
        assert 2 in state2.pending.answered_positions, state2.pending.answered_positions


class TestAC1864SameDomainRestatementIsStillANewAsk:
    def test_purchase_cost_srt446_rg_is_a_new_ask_not_a_pick(self) -> None:
        pending = _product_pick_roster()
        v = verdict(
            entities=[entity("SRT446-RG", "product", canonical_code="SRT446-RG")],
            domain_hint="purchase_cost",
            domain_in_message=True,
            reference_positions=[],
        )
        state2, plan, _branch = _decide(v, pending=pending)

        assert plan.trace.decision == {"kind": NEW_ASK, "why": "domain_in_message"}, plan.trace.decision
        assert plan.domains == ["purchase_cost"], plan.domains
        assert "domain_locked_by_pick" not in plan.trace.rules_fired, plan.trace.rules_fired
        assert state2.pending is not None and state2.pending.kind == "product_pick"


class TestAC1865TheRuleIsKindAgnosticOverATierPickToo:
    def _tier_pick_roster(self):
        options = [
            _tier_option(1, "Office", "office"),
            _tier_option(2, "Dealer", "dealer"),
            _tier_option(3, "End user", "end_user"),
        ]
        return _roster("tier_pick", options, domain="promotion")

    def test_dealer_promo_srt446_is_a_new_ask_not_a_tier_pick(self) -> None:
        pending = self._tier_pick_roster()
        v = verdict(
            entities=[
                entity("Dealer", "tier", canonical_code="Dealer"),
                entity("SRT446", "product", canonical_code="SRT446"),
            ],
            domain_hint="promotion",
            domain_in_message=True,
            reference_positions=[],
        )
        state2, plan, _branch = _decide(v, pending=pending)

        assert plan.trace.decision == {"kind": NEW_ASK, "why": "domain_in_message"}, plan.trace.decision
        assert plan.domains == ["promotion"], plan.domains
        assert "domain_locked_by_pick" not in plan.trace.rules_fired, plan.trace.rules_fired
        # The tier landed on the focus as THIS message's own entity, never as a pick of
        # the roster. PR #1353 fix round 1 closed the seam this pin used to record:
        # `_roster_is_about` now reads the code-only tier axis, so the roster is still
        # about the subject ("Dealer" is one of its options) and stays stored underneath,
        # as a product roster does when a new ask names one of its options (AC-PK015).
        assert "Dealer" in state2.focus.tier, state2.focus.tier
        assert state2.pending is not None and state2.pending.kind == "tier_pick"
        assert "new_ask_closes_stale_roster" not in plan.trace.rules_fired, plan.trace.rules_fired

    def test_bare_dealer_still_picks_position_2(self) -> None:
        pending = self._tier_pick_roster()
        v = verdict(
            entities=[entity("Dealer", "tier", canonical_code="Dealer")],
            domain_hint=None,
            domain_in_message=False,
            reference_positions=[],
        )
        state2, plan, _branch = _decide(v, pending=pending)

        assert plan.trace.decision == {"kind": ANSWER, "why": "label_match"}, plan.trace.decision
        assert plan.domains == ["promotion"], plan.domains
        # Issue #1352: the lock that ignored domain_hint under ANY pick is retired; a
        # bare pick (no domain word of its own) answers in the roster's domain.
        assert "pick_in_roster_domain" in plan.trace.rules_fired, plan.trace.rules_fired
        assert "domain_locked_by_pick" not in plan.trace.rules_fired, plan.trace.rules_fired
        assert state2.pending is not None
        assert 2 in state2.pending.answered_positions, state2.pending.answered_positions
