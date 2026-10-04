"""R10 RED test, PR #952 chatbot turn engine re-architecture - hand pass 10 (owner
ruling, 21 Sep 2026): a promotion ask answers straight away ONLY when the message
itself names the access level (parser `access_levels` non-empty); otherwise the tier
question is asked EVERY new ask. Tier persists only across the roster's own answering
turns (pick/refine) - never across a fresh NEW_ASK.

Recorded from the clone `chatbot.turns` (contact 900000008, 21 Sep 04:57-05:13 MYT,
`sorento_ai_automation_rearch`, read-only, no live turn run here):
`42c2da52-6202-4bd2-b529-a09a8b4da3fa` "promo for srtwc286" (tier ask) ->
`a5dc8ded-a521-499d-9536-35649aef2126` "2" (Dealer files) ->
`ac576e8c-cfb7-437d-bdc9-d469e16bc245` "3" (End user files, sticky roster, kept here
as CONTROL) -> `6bf3bf2b-c4db-4eb6-b0e0-74a5b2893cfe` "hi" (low_signal/idle) ->
`3854f23a-050b-45f8-92c0-d4930e1b5bfe` "promo for srtwc286" = DEFECT: answered Dealer
straight (the memory event shows `focus.after.tier: ["dealer"]` and the parser's own
`access_levels` empty on that turn).

**Root cause, measured directly (not guessed).** `turn/apply.py::_focus_rules`'s
NEW_ASK eviction loop ("new ask starts from that domain's defaults: every carried kind
this message did not name goes") walks `state.KIND_FIELD_MAP` only - `{"product":
"products", "customer": "customers", "warehouse": "warehouse", "brand": "brands"}`
(`app/services/chatbot/turn/state.py:32`). `tier` is not a member (it is a
`_CODE_ONLY_FIELDS` slot, `turn/apply.py:107`, written by `_set_kind_field` but never
read by the eviction loop), so a NEW_ASK never clears `focus.tier`.
`turn/narrow.py`'s own `narrow_by_tier` policy branch (`kind == "tier"`, lines
~437-457) then reads that STALE, non-empty `focus.tier` as an ALREADY-SETTLED tier and
narrows straight to it (`NarrowOutcome(None, [], [], values[...])`) instead of ever
reaching `NarrowOutcome("tier_pick", [], [], None)` again - the fresh ask never gets a
chance to raise the roster.

**Test shape**: engine-level, `test_rearch_r6_review_round.py`'s own
`_run_turn_engine_real` harness (real resolver/gate/narrower/tier-gate over seeded
Postgres rows, doubling only the MCP boundary), matching
`TestPromotionTierRosterStaysAnswerableAcrossMultiplePicks` (same file) and
`TestTierPickFetchUsesResolverEntitlementNeverParserWords` (`test_rearch_r6_review_
round.py`) precedent. Postgres only (`session_factory`, blank schema,
`tests/_pg_fixture.py`); every row seeded fresh per test.
"""
from __future__ import annotations

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_outstanding_lane import _session_of
from tests.chatbot.test_rearch_r5_production_decides import _seed_contact_and_get
from tests.chatbot.test_rearch_r6_review_round import (
    _grant_access_type,
    _mark_workspace_default,
    _run_turn_engine_real,
    _seed_access_type,
)


def _seed_three_tiers_all_entitled(session_factory) -> None:
    """The same three access tiers `test_rearch_r6_review_round.py::
    _seed_three_tiers_two_entitled` seeds, but the contact is entitled to ALL three -
    the shape the owner's live transcript replays (a tier ask that offers
    Office/Dealer/End user, `answer.py::ASK_ORDER`'s own display order)."""
    _seed_access_type(session_factory, code="sorento_dealer", name="Sorento Dealer")
    _seed_access_type(session_factory, code="sorento_office", name="Sorento Office")
    _seed_access_type(session_factory, code="end_user", name="End User")
    _grant_access_type(session_factory, code="sorento_dealer")
    _grant_access_type(session_factory, code="sorento_office")
    _grant_access_type(session_factory, code="end_user")


def _position_of_tier(options: list[dict], label: str):
    return next(
        (o.get("position") for o in options if str(o.get("label") or "") == label),
        None,
    )


class TestHandPass10PromotionAskRepeatsAfterATierPick:
    """Hand pass 10, owner ruling 21 Sep 2026: `decide.py`'s NEW_ASK must drop the
    carried tier the same way it already drops every other non-subject axis."""

    def test_a_fresh_promotion_ask_after_a_tier_pick_reopens_the_roster(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        _seed_three_tiers_all_entitled(session_factory)
        code = unique_code("ZZTHP10TIER")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        qf1 = _parser_output(
            domain_hint="promotion", intent_hint="check_promotion", domain_in_message=True,
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True}
            ],
        )
        result1, _c1 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"promo for {code}",
            msg_id="zzt-hp10-tier-ask", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        open_question1 = _session_of(session_factory).get("open_question") or {}
        assert open_question1.get("kind") == "tier_pick", (
            f"test setup sanity: a contact entitled to all 3 tiers must raise the "
            f"tier picker: {(result1.reply or {}).get('text')!r}"
        )
        options1 = open_question1.get("options") or []
        assert len(options1) == 3, f"test setup sanity: 3 entitled options: {options1!r}"
        assert {str(o.get("label")) for o in options1} == {"Office", "Dealer", "End user"}, options1
        dealer_position = _position_of_tier(options1, "Dealer")
        end_user_position = _position_of_tier(options1, "End user")
        assert dealer_position is not None and end_user_position is not None, options1

        # "2" - Dealer (live turn a5dc8ded-...).
        qf2 = _parser_output(
            domain_hint=None, intent_hint=None, entities=[], access_levels=[],
            reference_positions=[dealer_position],
        )
        result2, _c2 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf2, text_body=str(dealer_position),
            msg_id="zzt-hp10-tier-pick-dealer", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        assert result2.status == "done", result2.error

        # "3" - End user, a SECOND, different pick over the SAME still-open roster
        # (live turn ac576e8c-..., already-fixed sticky-roster behaviour per hand pass
        # 9's D3 - kept here as CONTROL, not the scenario under test).
        qf3 = _parser_output(
            domain_hint=None, intent_hint=None, entities=[], access_levels=[],
            reference_positions=[end_user_position],
        )
        result3, _c3 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf3, text_body=str(end_user_position),
            msg_id="zzt-hp10-tier-pick-enduser", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        assert result3.status == "done", result3.error

        # "hi" - idle chat (live turn 6bf3bf2b-...): names nothing, asks nothing,
        # plans nothing (S6 cluster 4). The focus must be untouched.
        qf4 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            user_goal="greeting", access_levels=[],
        )
        result4, _c4 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf4, text_body="hi",
            msg_id="zzt-hp10-hi", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        assert result4.status == "done", result4.error

        # The SAME ask again (live turn 3854f23a-...), naming its own subject fresh -
        # a NEW_ASK, not a pick and not a refinement. The message itself names NO
        # access level (`access_levels: []`, the measured real-capture shape, 249/249
        # - `test_rearch_r6_review_round.py`'s own docstring).
        qf5 = _parser_output(
            domain_hint="promotion", intent_hint="check_promotion", domain_in_message=True,
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True}
            ],
            access_levels=[],
        )
        result5, _c5 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf5, text_body=f"promo for {code}",
            msg_id="zzt-hp10-tier-ask-2", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        reply5 = (result5.reply or {}).get("text") or ""
        open_question5 = _session_of(session_factory).get("open_question") or {}
        assert open_question5.get("kind") == "tier_pick", (
            f"hand pass 10 DEFECT (live turn 3854f23a-050b-45f8-92c0-d4930e1b5bfe): a "
            f"fresh promotion ask that names no access level of its own must re-open "
            f"the tier roster, not settle on the LAST PICKED tier - measured directly: "
            f"`_session_of(session_factory)['focus']['tier']` is still `['end_user']` "
            f"(the second pick, turn 3) going into this turn, and the narrower reads "
            f"that as an already-settled tier instead of raising the roster again: "
            f"{reply5!r}"
        )
        options5 = open_question5.get("options") or []
        assert {str(o.get("label")) for o in options5} == {"Office", "Dealer", "End user"}, options5

        focus5 = _session_of(session_factory).get("focus") or {}
        assert focus5.get("tier") == [], (
            f"a NEW_ASK must drop the carried tier the same way it drops every other "
            f"non-subject axis - `turn/apply.py::_focus_rules`'s eviction loop never "
            f"walks `Focus.tier` (a code-only slot outside `KIND_FIELD_MAP`): {focus5!r}"
        )

    def test_control_a_message_that_names_the_level_answers_straight(
        self, session_factory, monkeypatch
    ) -> None:
        """Control: when the MESSAGE ITSELF names the access level, the tier question
        is never asked - `tier_gate.needs_tier_ask` returns False whenever
        `tier_stated` is non-empty. This must stay green after the fix above; it is
        what stops "the tier ask fires on EVERY new ask, even one that already
        answers it" from being the fix's own regression."""
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        _seed_three_tiers_all_entitled(session_factory)
        code = unique_code("ZZTHP10TIERCTL")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        qf = _parser_output(
            domain_hint="promotion", intent_hint="check_promotion", domain_in_message=True,
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None,
                 "current_message": True, "confident": True}
            ],
            access_levels=["dealer"],
        )
        result, _c = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf, text_body=f"dealer promo for {code}",
            msg_id="zzt-hp10-tier-named", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        assert result.status == "done", result.error
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "tier_pick", (
            f"a message that names the access level itself must never raise the tier "
            f"picker: {(result.reply or {}).get('text')!r} {open_question!r}"
        )


class TestHandPass10NewAskDropsFocusExtra:
    """Hand pass 10's second assertion: `focus.extra.*` (a non-subject slot outside
    `KIND_FIELD_MAP` and outside `_CODE_ONLY_FIELDS`, e.g. `attachment_type`) must also
    be dropped by a NEW_ASK in another domain - the same eviction rule this file's
    tier scenario needs, at a different slot. Pure `apply()`-level, matching
    `test_rearch_s2_focus_rules.py`'s own convention (no engine, no DB) - `focus.extra`
    is a `turn/apply.py::_focus_rules` output, not something a fetch/render step could
    mask or launder."""

    def test_a_new_ask_in_another_domain_drops_the_carried_attachment_type(self) -> None:
        from tests.chatbot._turn_helpers import build_policy, entity, verdict

        from app.services.chatbot.turn.apply import apply
        from app.services.chatbot.turn.state import Focus, Profile, State

        focus = Focus(
            products=[entity("SRTWC8517")],
            extra={"attachment_type": [entity("photo", hint="attachment_type")]},
        )
        state = State(focus=focus, pending=None, profile=Profile())
        # A NEW_ASK in a DIFFERENT domain ("order"), naming its own subject (a
        # customer) - `domain_in_message: True` is the parser's own "this message
        # carries a domain word of its own" flag (`turn/decide.py::domain_in_message`),
        # the same discriminator hand pass 10's promotion scenario (this file's other
        # test class) needs to reach `decision.starts_fresh`.
        v = verdict(
            domain_hint="order", domain_in_message=True,
            entities=[entity("ABC", hint="customer")],
        )

        state2, _plan = apply(state, v, build_policy())

        assert state2.focus.extra.get("attachment_type") in (None, []), (
            f"hand pass 10 (owner ruling, 21 Sep 2026): a NEW_ASK in another domain "
            f"must drop every non-subject slot it did not itself name, "
            f"`focus.extra.*` included - `turn/apply.py::_focus_rules`'s eviction "
            f"loop only ever walks `KIND_FIELD_MAP` "
            f"(product/customer/warehouse/brand), never `focus.extra`, so a carried "
            f"attachment_type from an earlier product_attachment ask survives every "
            f"later NEW_ASK in a different domain: {state2.focus.extra!r}"
        )
