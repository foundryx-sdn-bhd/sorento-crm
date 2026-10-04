"""Issue #1323, second recorded case: a company named as a FILTER is not the company pick.

Owner hand test on the Samantha copy (PR #1301 head 9f9bbb97, parser v40), 28 Sep 2026
11:42 MYT, console contact 900000039 (owner, verbatim): "the escalation hit again, you
see i am just trying to search with bmocha brand, and it triggers escalation".

1. "delivery order brand sorento cheng huat sentul" answered the Sorento DO list and
   ended "Mocha: no orders records found for customer CHENG HUAT HARDWARE (SENTUL) SDN
   BHD. Would you like me to escalate to Mocha customer service team?" (pending
   `team_pick`, team customer_service, expects yes_no, one option "Mocha").
2. "mocha brand" was handed to customer service (`out_of_scope`). Recorded verdict:
   `message_type: casual`, `entities: []`, `is_affirmative: null`, `escalation:
   {is_escalation_confirmation: true, company_pick: "Mocha"}`, `open_question_answer:
   {mode: pick, picked: [1]}`.

That verdict carries nothing but the pick, so no engine rule can tell it from a bare
"Mocha" without reading the message text: the fix for the recorded turn is the prompt
(section C, and the live-only eval case (d) in
`console_cases/2026-09-28-escalation-confirmation.yaml`). The engine half (section B) is
the guard for a parser that half-slips: a `company_pick` beside a domain, an entity or a
brand filter of the same message is refused, flag and pick together.

Harness: `test_escalation_round6_n8n_behaviour.Conversation` (the REAL `run_turn` over a
seeded Mocha + Sorento chain, the orders tool stubbed, the escalation lane dry), with
Mocha's customer service roster empty so the offer is the recorded single-option
`team_pick` rather than a member picker.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.decide import ANSWER, decide
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.chatbot.turn.state import Focus, Profile, State
from tests.chatbot._turn_helpers import build_policy, entity, verdict
from tests.chatbot.test_engine import _parser_output, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_round6_n8n_behaviour import (
    SORENTO,
    Conversation,
    _order_ask,
    _reply,
    _stub_rosters,
    _yes,
)
from tests.chatbot.test_rearch_r11_multi_company import _order_row, _said

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

TURN_1 = "delivery order brand sorento cheng huat sentul"


def _sorento_hit_mocha_miss(conv: Conversation) -> list[dict[str, Any]]:
    return [_order_row(SORENTO, "ZZTS2609-1323"), _order_row(SORENTO, "ZZTS2609-1324")]


def _brand_mocha_entity() -> dict[str, Any]:
    return {
        "raw": "mocha",
        "hint": "brand",
        "canonical_code": "MOCHA",
        "current_message": True,
        "confident": True,
    }


def _mocha_brand_read_right() -> dict[str, Any]:
    """"mocha brand" as the narrowed prompt reads it: the DO ask again, Brand MOCHA."""
    return _parser_output(
        message_type="business_query",
        intent_hint="check_order",
        domain_hint="order",
        domain_in_message=False,
        entities=[_brand_mocha_entity()],
        user_goal="the same delivery orders, for the mocha brand",
        routing={"suggested_team": "customer_service", "suggested_agent": "order_enquiries", "team_source": None},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )


def _mocha_brand_half_slipped(**overrides: Any) -> dict[str, Any]:
    """A parser that read the brand filter AND still kept the recorded pick."""
    base: dict[str, Any] = {
        "message_type": "casual",
        "intent_hint": "check_order",
        "domain_hint": "order",
        "entities": [_brand_mocha_entity()],
        "user_goal": "the delivery orders for the mocha brand",
        "routing": {"suggested_team": None, "suggested_agent": None, "team_source": None},
        "escalation": {"is_escalation_confirmation": True, "company_pick": "Mocha"},
    }
    base.update(overrides)
    return _parser_output(**base)


@pytest.fixture
def samantha(session_factory, monkeypatch, stub_parser, stub_access, system_settings_row):
    """Turn 1 of the owner's sequence, run through the engine: the Sorento DO list and
    the single-company offer to escalate to Mocha."""
    conv = Conversation(
        session_factory, monkeypatch, stub_parser, stub_access, system_settings_row, rows=_sorento_hit_mocha_miss
    )
    import tests.chatbot.test_escalation_round6_n8n_behaviour as r6

    monkeypatch.setattr(r6, "MOCHA_MEMBERS", [])
    conv.rosters = _stub_rosters(monkeypatch, conv.ids)
    said = _said(conv.say(TURN_1, _order_ask()))
    assert "no orders records found for customer" in said, said
    assert "escalate to *Mocha* customer service team?" in said, said
    question = conv.open_question()
    assert question.get("kind") == "team_pick", question
    assert question.get("expects") == "yes_no", question
    assert question.get("team") == "customer_service", question
    return conv


# =============================================================================== #
# A. The owner's two-turn sequence through the engine
# =============================================================================== #


class TestSamanthaSequence:
    def test_mocha_brand_read_as_the_brand_filter_is_answered_and_hands_nobody_over(self, samantha):
        result = samantha.say("mocha brand", _mocha_brand_read_right())
        assert samantha.last_lane == [], "a brand filter is a new ask, never a handover"
        assert result.branch_kind == "business_query", result.branch_kind

    def test_a_half_slipped_pick_beside_the_brand_filter_is_refused(self, samantha):
        """RED before the guard: the flag and the pick came through with the brand
        entity and the order domain beside them, and the person was handed over."""
        result = samantha.say("mocha brand", _mocha_brand_half_slipped())
        assert samantha.last_lane == [], "a company pick beside a filter is not a pick"
        assert result.branch_kind != "out_of_scope", result.branch_kind

    @pytest.mark.parametrize(
        "text, overrides",
        [
            ("how about mocha", {"entities": [], "intent_hint": None}),
            (
                "mocha water closet",
                {"entities": [entity("water closet", hint="product")], "domain_hint": None, "intent_hint": None},
            ),
            ("any mocha incoming", {"entities": [], "intent_hint": "check_incoming", "domain_hint": "incoming_stock"}),
            ("mocha brand", {"entities": [], "intent_hint": None, "domain_hint": None, "query_brands": ["mocha"]}),
        ],
    )
    def test_a_pick_with_any_subject_of_its_own_is_refused(self, samantha, text, overrides):
        result = samantha.say(text, _mocha_brand_half_slipped(**overrides))
        assert result.branch_kind != "out_of_scope", (text, "a company pick beside a subject is not a pick")
        handed_over = [r for _c, _i, r in samantha.last_lane if not (r.get("pending") or {}).get("kind")]
        assert handed_over == [], (text, handed_over)

    def test_a_bare_mocha_still_picks_mocha(self, samantha):
        samantha.say("Mocha", _reply(escalation={"is_escalation_confirmation": True, "company_pick": "Mocha"}))
        assert samantha.company() == samantha.ids["mocha"]

    def test_a_1_still_picks_mocha(self, samantha):
        samantha.say(
            "1",
            _reply(
                reference_positions=[1],
                escalation={"is_escalation_confirmation": True, "company_pick": "Mocha"},
            ),
        )
        assert samantha.company() == samantha.ids["mocha"]

    def test_a_plain_yes_still_routes_to_mocha(self, samantha):
        samantha.say("yes", _yes())
        assert samantha.company() == samantha.ids["mocha"]


# =============================================================================== #
# B. The engine guard, at apply(): the pick is refused with the flag
# =============================================================================== #


def _offer():
    return pending_ask(
        "team_pick",
        [{"position": 1, "label": "Mocha", "entity_type": "company", "payload": {"team": "customer_service"}}],
        team="customer_service",
        asked_at_turn=1,
        expects="yes_no",
    )


def _state() -> State:
    return State(focus=Focus(domains=["order"]), pending=_offer(), profile=Profile())


def _pick(**overrides: Any) -> dict[str, Any]:
    base = verdict(
        message_type="casual",
        entities=[],
        escalation={"is_escalation_confirmation": True, "escalation_declined": None, "company_pick": "Mocha"},
    )
    base.update(overrides)
    return base


class TestGuard:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"entities": [entity("mocha", hint="brand", canonical_code="MOCHA")]},
            {"domain_hint": "order"},
            {"intent_hint": "check_order"},
            {"query_brands": ["mocha"]},
        ],
        ids=["brand_entity", "domain", "intent", "query_brands"],
    )
    def test_a_pick_beside_a_subject_is_refused(self, overrides) -> None:
        _new, plan = apply(_state(), _pick(**overrides), build_policy())
        assert "company_pick_refused_by_a_named_ask" in plan.trace.rules_fired
        assert plan.trace.lane != "escalation", plan.trace.rules_fired
        assert plan.trace.decision.get("kind") != ANSWER, plan.trace.decision

    def test_a_bare_pick_still_hands_over(self) -> None:
        _new, plan = apply(_state(), _pick(), build_policy())
        assert "company_pick_refused_by_a_named_ask" not in plan.trace.rules_fired
        assert plan.trace.lane == "escalation", plan.trace.rules_fired
        assert plan.trace.decision == {"kind": ANSWER, "why": "escalation_confirmation"}

    def test_a_carried_entity_does_not_refuse_the_pick(self) -> None:
        """The previous turn's customer riding along is not this message's subject."""
        carried = entity("CHENG HUAT HARDWARE (SENTUL) SDN BHD", hint="customer", current_message=False)
        _new, plan = apply(_state(), _pick(entities=[carried]), build_policy())
        assert plan.trace.lane == "escalation", plan.trace.rules_fired

    def test_the_recorded_verdict_is_indistinguishable_from_a_bare_pick_at_decide(self) -> None:
        """The recorded turn-2 verdict names no subject at all: the engine obeys it, so
        the prompt is the fix for that turn (section C)."""
        recorded = _pick(open_question_answer={"mode": "pick", "picked": [1], "items": [], "qty_for_all": None})
        assert decide(recorded, Focus(domains=["order"]), _offer()).kind == ANSWER


# =============================================================================== #
# C. The prompt: a company name is the pick only as the whole answer
# =============================================================================== #


class TestPrompt:
    def _addendum(self) -> str:
        from app.services import chatbot_parser_prompt as prompt

        return prompt.ESCALATION_CONFIRMATION_ADDENDUM

    def test_a_company_name_is_the_pick_only_when_it_is_the_answer(self) -> None:
        text = self._addendum()
        assert "ONLY when the message IS the answer to the offer" in text
        assert "the offered option's number, or a plain yes" in text

    def test_a_company_name_with_a_filter_is_a_new_ask(self) -> None:
        text = self._addendum()
        for example in ("mocha brand", "how about mocha", "mocha water closet", "any mocha incoming"):
            assert f'"{example}"' in text, example
        assert "is_escalation_confirmation false and company_pick null" in text

    def test_it_outranks_the_earlier_whatever_the_message_type_line(self) -> None:
        text = self._addendum()
        assert 'including its "whatever the message_type" line' in text

    def test_the_owners_sequence_is_the_worked_example(self) -> None:
        text = self._addendum()
        assert "Would you like me\nto escalate to Mocha customer service team?" in text
        assert 'the reply "mocha brand" is the delivery\norder ask again with Brand MOCHA' in text

    def test_no_em_or_en_dashes(self) -> None:
        text = self._addendum()
        assert EM_DASH not in text and EN_DASH not in text


# =============================================================================== #
# D. The live-only parser eval case (d): shape pinned here, graded by hand
# =============================================================================== #


def test_eval_case_d_expects_the_filter_as_a_new_ask_and_the_bare_name_as_the_pick() -> None:
    import importlib.util
    import pathlib

    import yaml

    here = pathlib.Path(__file__).resolve()
    fixture = here.parent / "console_cases" / "2026-09-28-escalation-company-filter.yaml"
    cases = {c["text"]: c for c in yaml.safe_load(fixture.read_text(encoding="utf-8"))["cases"]}
    for case in cases.values():
        assert case["live_only"] is True
        offer = case["previous_conversation_state"]["open_question"]
        assert (offer["kind"], offer["expects"], offer["team"]) == ("team_pick", "yes_no", "customer_service")
        assert [o["label"] for o in offer["options"]] == ["Mocha"]

    spec = importlib.util.spec_from_file_location(
        "_console_check_1323d", here.parents[2] / "scripts" / "chatbot_console_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    grade = module._grade_parser

    recorded = {
        "message_type": "casual",
        "entities": [],
        "is_affirmative": None,
        "escalation": {"is_escalation_confirmation": True, "company_pick": "Mocha"},
    }
    filtered = {
        "escalation": {"is_escalation_confirmation": False, "company_pick": None},
        "entities": [{"raw": "mocha", "hint": "brand", "canonical_code": "MOCHA", "current_message": True}],
    }
    brand_case = cases["mocha brand"]["expect"]["parser"]
    assert grade(brand_case, recorded), "the recorded turn-2 verdict must fail case (d)"
    assert grade(brand_case, filtered) == []
    assert grade(cases["Mocha"]["expect"]["parser"], recorded) == []
    assert grade(cases["1"]["expect"]["parser"], recorded) == []


def test_the_round_1_migration_publishes_the_narrowed_rule() -> None:
    """`chatbot_esc_confirm_1323` is idempotent by template, so the changed words make
    it publish one new unlabelled version carrying them, and a second run adds nothing."""
    from app.models.ai_prompt import AIPromptVersion
    from tests._pg_fixture import blank_session
    from tests.chatbot.test_escalation_confirmation_semantic_1323 import _load_migration

    module = _load_migration()
    with blank_session() as db:
        bind = db.connection()
        module.publish(bind)
        rows = db.query(AIPromptVersion).filter(AIPromptVersion.name == "chatbot_semantic_parser").all()
        newest = max(rows, key=lambda r: r.version)
        assert (newest.config_json or {}).get("chatbot_esc_confirm_1323") is True
        assert "ONLY when the message IS the answer to the offer" in newest.template
        assert '"mocha brand"' in newest.template
        module.publish(bind)
        assert db.query(AIPromptVersion).filter(AIPromptVersion.name == "chatbot_semantic_parser").count() == len(rows)
