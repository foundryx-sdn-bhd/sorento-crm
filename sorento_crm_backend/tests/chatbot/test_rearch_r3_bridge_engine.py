"""R3 RED tests, engine level - a real `engine.run_turn` through `_run_turn`
(`tests/chatbot/test_outstanding_lane.py`'s harness, reused per the captain's brief;
`tests/chatbot/test_sales_report_lane.py` re-exports the same name).

UAC: AC-1683 (turn/compose reached only outside the bridge's two arms), AC-1694's
pytest half (customer picker), AC-1697/AC-1698 (promotion tier picker + multi-select),
plus the coordinator's own add-on (a one-entitled-tier promotion ask never asks at all).

**Measured, live, on this branch (post R2 merge, `7c3ada3a5`), via a throwaway debug
probe run and deleted before this file was written - not guessed:**

1. A promotion ask that needs a tier pick (`access_types` returning ONE row, "Sorento
   Dealer") answers TODAY with `turn/compose.py`'s own entitlement-BLIND tier ask:
   `'Product: SRTWT7445\\nWhich price tier applies to you?\\n1. dealer\\n2. office\\n
   3. end user'`, quick_replies `'dealer, office, end user'`, zero tool calls
   (`captured == []`), lowercase option labels, EVERY canonical tier offered
   regardless of what the contact actually holds. Production (`answer.
   access_level_choice_message`, reached through `resolve_gate.run`'s real
   `access_check` entry R2 now wires up) would ask about ONLY the one entitled tier -
   in fact would not ask at all (see point 3).
2. (Superseded by PICKER-ONE, 13 Oct 2026: every customer picker now prints through
   `turn/compose.py::compose_question`.) An ambiguous customer order ask ("orders for
   hanlim", two same-named-family customer matches) answered with `turn/compose.py`'s own
   customer ask:
   `'Which customer do you mean?\\n1. 300-H070 - no DO\\n2. 300-H071 - no DO'` - the
   resolved CODE as the label, no "Please choose:", no company-code suffix. Production
   (`gate.run_gate` + `pickers.annotate_customer`) prints the CUSTOMER NAME with its
   company suffix ("HANLIM TRADING SDN BHD (SRT)") and the "Please choose:" line.
3. **The coordinator's add-on, measured**: with exactly ONE entitled tier
   (`tier_gate.needs_tier_ask` returns `False` when `len(entitled_tiers) <= 1`,
   `tier_gate.py:148`), production does not ask at all - it recomposes access_levels
   for that one tier and proceeds straight to the fetch. Today's engine still shows
   the SAME three-option, entitlement-blind ask as point 1 (asking is not gated on
   entitlement count at all on this path) - genuinely RED, not a green control.

Every test below either spies the seam it grades (`turn.compose.compose_question`,
`turn.compose.compose`, `tail.reply_ladder.compose_reply`, `engine.run_tail`) or
computes its expected text from the same production-function chain
`test_rearch_r3_answer_bridge.py` uses, so the assertion is never a retyped guess.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.chatbot.lanes.business.tier_gate import recompose
from app.services.chatbot.tail import reply_ladder
from app.services.chatbot.turn import compose as turn_compose
from tests.chatbot._turn_helpers import make_office_access_type
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import (
    PRODUCT_CODE,
    PRODUCT_UUID,
    _ambiguous_hanlim_resolve_services,
    _run_turn,
    _seed_contact,
)


def _one_tier_resolve_services(*, access_names: list[str]) -> ResolveGateServices:
    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        match = {
            "uuid": PRODUCT_UUID,
            "entity_type": "product",
            "canonical_code": PRODUCT_CODE,
            "match_tier": "exact",
        }
        return {
            "tokens": asked,
            "resolutions": [{"raw": raw, "token": raw, "matches": [match]} for raw in asked],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": n} for n in access_names],
        resolve_entity=_resolve_entity,
        probe=lambda **_: None,
    )


def _promo_qf() -> dict[str, Any]:
    return _parser_output(
        domain_hint="promotion",
        intent_hint="check_promotion",
        entities=[
            {
                "raw": PRODUCT_CODE,
                "hint": "product",
                "canonical_code": None,
                "current_message": True,
                "confident": True,
            }
        ],
    )


# --------------------------------------------------------------------------- #
# AC-1697 / AC-1698 - the promotion tier picker
# --------------------------------------------------------------------------- #


class TestPromotionAskUsesProductionCopy:
    def test_two_entitled_tiers_reply_starts_and_ends_per_production(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-1697: 'Which access level do you need for {product}?' header, one
        stamped line per ENTITLED tier only, closing 'Reply with the number(s)...'
        line - never today's bare 'Which price tier applies to you?' listing every
        canonical tier regardless of entitlement."""
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_promo_qf(),
            text_body=f"promo for {PRODUCT_CODE}",
            msg_id="zzt-r3-promo-two-tier",
            resolve_services=_one_tier_resolve_services(
                access_names=["Sorento Dealer", "Sorento Office"]
            ),
            mcp_response={"has_result": True, "items": [{"description": "Fake promo"}]},
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith(f"Which access level do you need for {PRODUCT_CODE}?"), reply
        assert 'Reply with the number(s), e.g. "1", "1 and 2", or "all".' in reply, reply
        assert "Which price tier applies to you?" not in reply, (
            "must not be the entitlement-blind turn/compose.py tier ask"
        )
        # only the two entitled tiers, never the third (end_user)
        assert "End user" not in reply and "end user" not in reply, reply

    def test_pending_kind_tier_pick_entity_type_tier_not_access_tier(
        self, session_factory, monkeypatch
    ) -> None:
        from tests.chatbot.test_outstanding_lane import _session_of

        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_promo_qf(),
            text_body=f"promo for {PRODUCT_CODE}",
            msg_id="zzt-r3-promo-pending-shape",
            resolve_services=_one_tier_resolve_services(
                access_names=["Sorento Dealer", "Sorento Office"]
            ),
            mcp_response={"has_result": True, "items": [{"description": "Fake promo"}]},
        )
        session = _session_of(session_factory)
        open_question = session.get("open_question") or {}
        assert open_question.get("kind") == "tier_pick", open_question
        options = open_question.get("options") or []
        assert len(options) == 2, options
        assert [o.get("entity_type") for o in options] == ["tier", "tier"], (
            "an option carrying entity_type == 'access_tier' never reaches "
            "focus.tier (apply._CODE_ONLY_FIELDS only recognises 'tier')"
        )


class TestPromotionAskMultiSelect:
    """AC-1698: '1', '1 and 2' and 'all' each settle one/two/every chosen tier and
    run the promotion fetch per chosen tier. Graded on the CAPTURED tool calls, not
    on reply text. The 'single' case is a MEASURED GREEN CONTROL: today's
    entitlement-blind tier_pick already uses entity_type == 'tier' on its own
    options (it is not the bridge's mechanism, but `apply._answer_pending`'s
    generic single-position handling already works for it), so answering '1' with
    one already runs one promotion fetch.

    Captain ruling 20 Sep 2026 (second round): AC-1698 says the reply returns EACH
    chosen tier's promotions - it does not say how many MCP calls that takes.
    Production answers a multi-select with ONE promotion fetch whose `access_levels`
    argument carries every chosen tier's recomposed name(s), not one call per tier -
    so the answer-turn assertion is graded on the UNION of `access_levels` requested
    across every captured promotion call, computed via the real
    `lanes.business.tier_gate.recompose` function, never a hardcoded tier-name list.
    Measured, reading production (`turn/narrow.py:409-416`'s own `narrow_by_tier`
    "proceed" arm, `kind == "tier"`): `one = candidates[-1]` - it reads the LAST
    picked tier candidate ONLY, discarding every other one focus.tier carries, so
    `spec.filters["tier"]` (and so `_tier_gate`'s own `access_levels_recomposed`,
    `turn_runtime.py:1454-1468`) is a scalar, one tier, never several. `one-and-two`
    and `all` are therefore STILL genuinely RED: the union of requested access
    levels is missing whichever tier was not `candidates[-1]` (measured: for
    entitled `["Sorento Dealer", "Sorento Office"]`, `ASK_ORDER` puts option 1 =
    office, option 2 = dealer, so `focus.tier = ["office", "dealer"]` after either
    answer and `candidates[-1]` = "dealer" - "Sorento Office" never reaches the
    fetch at all). `single` stays the GREEN CONTROL it always was.

    Captain ruling 20 Sep 2026 (first round): the ASK turn's own `captured1` is not
    empty -
    `TestPromotionAskUsesProductionCopy`'s own green test in this same file asserts
    the ask's reply is stamped "has promotion"/"no promotion" per entitled tier, and
    that stamp can only come from `lanes/business/__init__.py`'s own tier_ask arm
    running one MCP probe (`fetch_mod.TIER_PROBE_TOOL`) per entitled tier, each
    scoped to that tier's own access level (`fetch_mod.tier_probe_plan`'s
    `probe_access_levels`). So the ask turn's own captured calls are asserted to be
    ONLY those per-tier probe calls - never zero, never anything else - and the
    ANSWER turn still fetches per chosen tier as before."""

    @pytest.mark.parametrize(
        "answer_text,answer_overrides,chosen_tiers",
        [
            ("1", {"reference_positions": [1]}, ["office"]),
            ("1 and 2", {"reference_positions": [1, 2]}, ["office", "dealer"]),
            ("all", {"broaden_axis": "all"}, ["office", "dealer"]),
        ],
        ids=["single", "one-and-two", "all"],
    )
    def test_answering_the_tier_pick_runs_the_promotion_fetch_per_chosen_tier(
        self, session_factory, monkeypatch, answer_text: str, answer_overrides: dict, chosen_tiers: list[str]
    ) -> None:
        _seed_contact(session_factory, variables={})
        resolve_services = _one_tier_resolve_services(
            access_names=["Sorento Dealer", "Sorento Office"]
        )
        _result1, captured1 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_promo_qf(),
            text_body=f"promo for {PRODUCT_CODE}",
            msg_id="zzt-r3-promo-ms-ask",
            resolve_services=resolve_services,
            mcp_response={"has_result": True, "items": [{"description": "Fake promo"}]},
        )
        # The ask turn's own captured calls must be ONLY the per-tier promotion
        # probe - one call per entitled tier ("Sorento Dealer", "Sorento Office"),
        # each scoped to that tier's own access level - never zero (the stamps in
        # TestPromotionAskUsesProductionCopy's own green test could not exist
        # without them) and never anything else (no order/outstanding/other tool).
        assert len(captured1) == 2, (
            f"the tier ask must fire exactly one promotion probe per entitled tier "
            f"(2 entitled tiers here): {captured1}"
        )
        for name, args in captured1:
            assert name == fetch_mod.TIER_PROBE_TOOL, (
                f"the ask turn's only tool calls must be the per-tier promotion probe "
                f"({fetch_mod.TIER_PROBE_TOOL!r}), got {name!r}: {captured1}"
            )
        probed_access_levels = sorted(
            tuple(args.get("access_levels") or []) for _name, args in captured1
        )
        assert probed_access_levels == [("Sorento Dealer",), ("Sorento Office",)], (
            "each per-tier probe call must be scoped to exactly that tier's own "
            f"access level: {captured1}"
        )
        # Security review, PR #952 Phase 3, finding B1 (`.claude/handoffs/
        # rearch-phase3-security.md`): `verdict["access_levels"]` is the PARSER's
        # own field, and the prompt inventory measured it EMPTY on 249/249 real
        # answer-turn captures (`documentation/plans/chatbot/
        # parser-prompt-inventory.md:106`) - feeding `entitled_names` here asserted
        # an assumption nowhere true in production and hid the bug this pin now
        # exposes: `turn_runtime._tier_gate` reads the VERDICT's `access_levels`
        # for a SETTLED tier pick, never the resolver's own entitlement, so an
        # empty (real-shaped) verdict recomposes to an empty `access_levels_
        # recomposed`, which the promotion route reads as "no filter at all"
        # (`app/api/v1/marketing/promotions.py` -> `contact_access_type_service.
        # translate_names_to_codes`) - the contact reads promotions of every tier.
        # See `test_rearch_r6_review_round.py::
        # TestTierPickFetchUsesResolverEntitlementNeverParserWords` for the
        # engine-level, real-resolver-entitlement version of this same red.
        entitled_names = ["Sorento Dealer", "Sorento Office"]
        answer_qf = _parser_output(
            domain_hint=None,
            intent_hint=None,
            entities=[],
            access_levels=[],
            **answer_overrides,
        )
        _result2, captured2 = _run_turn(
            session_factory,
            monkeypatch,
            qf=answer_qf,
            text_body=answer_text,
            msg_id="zzt-r3-promo-ms-answer",
            resolve_services=resolve_services,
            mcp_response={"has_result": True, "items": [{"description": "Fake promo"}]},
        )
        promo_calls = [c for c in captured2 if "promotion" in c[0]]
        assert promo_calls, f"{answer_text!r} must run at least one promotion fetch: {captured2}"
        # AC-1698: the reply returns EACH chosen tier's promotions - graded here on the
        # UNION of access_levels requested across every promotion call (one call or
        # several - production's own contract, not this test's guess), computed via
        # the REAL recompose() function rather than a hardcoded tier-name list.
        requested_access_levels: set[str] = set()
        for _name, args in promo_calls:
            requested_access_levels.update(args.get("access_levels") or [])
        expected_access_levels = set(recompose(chosen_tiers, [], entitled_names)["access_levels"])
        assert requested_access_levels == expected_access_levels, (
            f"{answer_text!r} (chosen tiers {chosen_tiers}) must reach the promotion "
            f"fetch scoped to exactly those tiers' recomposed access levels - got "
            f"{requested_access_levels!r}, expected {expected_access_levels!r}: "
            f"{captured2}"
        )


class TestOneEntitledTierNeverAsks:
    """Coordinator add-on: with exactly one entitled tier, production proceeds
    straight to the fetch (`tier_gate.needs_tier_ask` -> False for
    `len(entitled_tiers) <= 1`, `tier_gate.py:148`) - no tier_pick is ever opened.
    Measured RED today (see module docstring point 3): today's engine still shows
    the entitlement-blind three-option ask and makes no tool call at all."""

    def test_no_tier_pick_opens_and_the_fetch_runs_for_the_one_tier(
        self, session_factory, monkeypatch
    ) -> None:
        from tests.chatbot.test_outstanding_lane import _session_of

        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_promo_qf(),
            text_body=f"promo for {PRODUCT_CODE}",
            msg_id="zzt-r3-promo-one-tier",
            resolve_services=_one_tier_resolve_services(access_names=["Sorento Dealer"]),
            mcp_response={"has_result": True, "items": [{"description": "Fake promo"}]},
        )
        session = _session_of(session_factory)
        open_question = session.get("open_question")
        assert open_question is None or open_question.get("kind") != "tier_pick", (
            f"a single-entitled-tier promotion ask must not raise a tier picker: {open_question}"
        )
        assert len(captured) >= 1, (
            "a single-entitled-tier promotion ask must proceed straight to the fetch, "
            f"but no tool call was made: {captured}"
        )


# --------------------------------------------------------------------------- #
# AC-1694 - the ambiguous customer picker (pytest half)
# --------------------------------------------------------------------------- #


class TestCustomerAskUsesTheOneCustomerPicker:
    """PICKER-ONE (owner, 13 Oct 2026): the gate's ambiguous-customer picker, reached
    through a real turn, prints the ONE customer picker every roster prints - the
    `compose_question` header, `N. <name> - <stamp>`, no company tag when both families
    sit in one company - stamped from the gate's own probe (here: no rows, so measured
    and none has a DO)."""

    def test_reply_is_the_one_customer_picker(self, session_factory, monkeypatch) -> None:
        probe_calls: list[Any] = []

        def spy_probe(*, tool, contact_id, entities, semantic_input, user_prompt):
            probe_calls.append(tool)
            return {"items": [], "has_result": False}

        _seed_contact(session_factory, variables={})
        # V1-DOC-SCOPE slice 3: plays an office role, unlinked != customer.
        make_office_access_type(session_factory, CONTACT_ID)
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status=None,
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
            ),
            text_body="orders for hanlim",
            msg_id="zzt-r3-cust-ask",
            attributes=["sales_orders.outstanding"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply == (
            "Which one do you mean?\n"
            "1. HANLIM TRADING SDN BHD - no DO\n"
            "2. HANLIM HARDWARE SDN BHD - no DO"
        ), reply
        # One probe for the turn: the gate measured, the roster reused it.
        assert len(probe_calls) == 1, probe_calls
        assert captured == []


# --------------------------------------------------------------------------- #
# AC-1683 - turn/compose reached only OUTSIDE the bridge's two arms
# --------------------------------------------------------------------------- #


class TestComposeQuestionIsNotReachedForTheBridgedArms:
    def test_compose_question_never_called_for_the_promotion_tier_ask(
        self, session_factory, monkeypatch
    ) -> None:
        calls: list[Any] = []
        real = turn_compose.compose_question

        def _spy(pending, state=None):
            calls.append(pending)
            return real(pending, state)

        monkeypatch.setattr(turn_compose, "compose_question", _spy)
        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_promo_qf(),
            text_body=f"promo for {PRODUCT_CODE}",
            msg_id="zzt-r3-spy-compose-question",
            resolve_services=_one_tier_resolve_services(
                access_names=["Sorento Dealer", "Sorento Office"]
            ),
            mcp_response={"has_result": False, "items": []},
        )
        assert calls == [], (
            "a single-domain business turn's access_ask must be composed by the "
            f"bridge + tail/reply_ladder, never turn/compose.compose_question: {calls}"
        )

    def test_compose_reply_is_called_once_for_the_promotion_tier_ask(
        self, session_factory, monkeypatch
    ) -> None:
        calls: list[Any] = []
        real = reply_ladder.compose_reply

        def _spy(outcome):
            calls.append(outcome)
            return real(outcome)

        monkeypatch.setattr(reply_ladder, "compose_reply", _spy)
        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_promo_qf(),
            text_body=f"promo for {PRODUCT_CODE}",
            msg_id="zzt-r3-spy-compose-reply",
            resolve_services=_one_tier_resolve_services(
                access_names=["Sorento Dealer", "Sorento Office"]
            ),
            mcp_response={"has_result": False, "items": []},
        )
        assert len(calls) == 1, (
            f"the bridge must produce this turn's text via tail/reply_ladder.compose_reply "
            f"exactly once: {len(calls)} calls"
        )

    def test_control_a_multi_domain_ask_still_reaches_turn_compose_compose(
        self, session_factory, monkeypatch
    ) -> None:
        """Control (documented, may already be green): a multi-domain fetch is out
        of R3's scope (single-domain plans only) and must keep using
        `turn/compose.compose` - a bridge that swallowed every business turn would
        be the over-correction the plan's own Hazards section warns against."""
        calls: list[Any] = []
        real = turn_compose.compose

        def _spy(envelopes, state, policy, ctx):
            calls.append(envelopes)
            return real(envelopes, state, policy, ctx)

        monkeypatch.setattr(turn_compose, "compose", _spy)
        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint=None,
                intent_hint=None,
                entities=[
                    {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                asks=[{"domain": "inventory"}, {"domain": "incoming"}],
            ),
            text_body=f"stock and incoming for {PRODUCT_CODE}",
            msg_id="zzt-r3-multi-domain-control",
            mcp_response={"has_result": False, "items": []},
        )
        assert len(calls) >= 1, (
            "a multi-domain plan must still be composed by turn/compose.compose"
        )


class TestOneSessionWriter:
    def test_run_tail_is_not_entered_for_a_bridged_business_turn(
        self, session_factory, monkeypatch
    ) -> None:
        """Control (documented, likely already green): `engine.run_tail` is the
        CANNED-lane session writer (its own docstring: 'used by the canned lanes
        through engine.run_tail'); a business turn - bridged or not - has always
        gone through `_run_answer`'s own tail. Kept as a standing guard so a future
        change cannot silently route a bridged turn through the other writer."""
        calls: list[Any] = []
        real = engine_mod.run_tail

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(engine_mod, "run_tail", _spy)
        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_promo_qf(),
            text_body=f"promo for {PRODUCT_CODE}",
            msg_id="zzt-r3-spy-run-tail",
            resolve_services=_one_tier_resolve_services(
                access_names=["Sorento Dealer", "Sorento Office"]
            ),
            mcp_response={"has_result": False, "items": []},
        )
        assert calls == [], calls
