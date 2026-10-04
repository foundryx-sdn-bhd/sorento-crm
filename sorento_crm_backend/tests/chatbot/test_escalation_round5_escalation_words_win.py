"""#865 fix round 5: escalation words win over product words (owner hand test of round 4,
28 Sep 00:05 to 00:07 MYT, console :3082).

The owner's messages, in his order, on one console conversation:

    check spec srtwc286                                              spec answer (right)
    please esclate to marketing team                                 marketing_product (right)
    ok                                                               low_signal (right)
    pelase escalate to marketing team MWc-SC8609-)PP water closet    the SRTWC286 spec answer again
    pelase escalate to marketing team MWc-SC8609-PP water closet     "Which one do you mean? 1. water
                                                                     closet (promotion) 2. water closet
                                                                     (attachment_type)"
    1                                                                a product miss offering "escalate
                                                                     to customer service team?"

The merged rules this is reconciled with:

* The parser prompt's MESSAGE TYPE rule 1: a message that asks for a specific team or to
  ESCALATE is `request_for_help`, and "it takes priority over business_query,
  clarification, and casual ... even if they also mention a product or order". The
  fourth message broke it (the parser read a product query); `user_goal` kept the words.
* #706 (H69): an explicit team beats a pending offer.
* #1142 (R6 to R9): the team follows the question's domain; the flat `customer_service`
  literal is only the last rung, never the answer when a team is known.
* #952 hand pass 10 (contract 111, the #866 port): an escalation naming an unrecognised
  product code asks the product did-you-mean before anything else.

The fix: an escalate word plus a named team (read off the parser's `user_goal`, as round
4's `_named_teams` already does) is a help request whatever product code, typo or class
word follows; such a turn plans no fetch and asks no narrowing or kind question; its
product words are the escalation's focus; and a typo is resolved INSIDE the escalation by
the resolver's own trigram did-you-mean, one question, answered by a number, after which
the escalation goes out with the picked product's brand and team.

Every test drives the real engine through `console_service.run_console_turn` (dry run,
one contact, `session_vars` carried turn to turn) with the parser stubbed.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot._turn_helpers import build_policy, entity, verdict
from tests.chatbot.test_engine import CONTACT_ID, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_escalation_agent_carry import (
    _capture_real_next_assignee,
    _capture_sla,
    _seed_contact,
)
from tests.chatbot.test_escalation_brand_from_focus import (
    MOCHA_COMPANY_ID,
    _escalation_verdict,
    _looked_up_routing,
    _product_verdict,
    _seed_borrowable_envelope,
    _seed_marketing_product_team,
    _seed_mocha_company_item,
    _seed_owners_catalogue,
    _stub_any_product_tool,
)
from tests.chatbot.test_rearch_s3_attribute_first import _link_contact_company

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")

ROUTED_TO_MARKETING = (
    "This inquiry has been routed to the respective person-in-charge (PIC) from "
    "marketing product team. We will get back to you soon. Thanks for your patience."
)
DID_YOU_MEAN = (
    'Couldn\'t find "MWc-SC8609-)PP". Did you mean:\n'
    "1. MWC-SC8609-PP\n"
    "2. MWC-SC8606-PP\n"
    "3. MWC-SC86-PP\n"
    "Reply with the number and I will pass it to the marketing product team."
)


def _typo_verdict() -> dict[str, Any]:
    """Message 4 as the parser misread it: a master_products business query, the typo'd
    code unsure, the class word beside it, the domain's team (purchasing). `user_goal`,
    the parser's own reading of the message, still says what the customer asked."""
    return verdict(
        message_type="business_query",
        domain_hint="master_products",
        intent_hint="check_product",
        user_goal="trying to escalate MWc-SC8609-)PP water closet to the marketing team",
        entities=[
            entity("MWc-SC8609-)PP", hint="product", confident=False),
            entity("water closet", hint="product_type"),
        ],
        routing={"suggested_team": "purchasing", "suggested_agent": "general_enquiries"},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )


def _fixed_code_verdict() -> dict[str, Any]:
    """Message 5: a help request naming the code and the class word, no domain of its
    own - the shape `_help_request_is_an_ask` used to turn into a product ask."""
    return verdict(
        message_type="request_for_help",
        domain_hint=None,
        intent_hint=None,
        user_goal="trying to escalate MWc-SC8609-PP water closet to the marketing team",
        entities=[
            entity("MWc-SC8609-PP", hint="product", confident=True),
            entity("water closet", hint="product_type"),
        ],
        routing={"suggested_team": "marketing_product", "suggested_agent": "general_enquiries"},
        escalation={"is_escalation_confirmation": False, "company_pick": None},
    )


def _position(position: int) -> dict[str, Any]:
    return verdict(
        message_type="casual",
        reference_positions=[position],
        routing={"suggested_team": None, "suggested_agent": None},
    )


OWNERS_28SEP_ROUND5: list[tuple[str, dict[str, Any]]] = [
    ("check spec srtwc286", _product_verdict("master_products", "check_product", "srtwc286")),
    (
        "please esclate to marketing team",
        _escalation_verdict(user_goal="trying to escalate to the marketing team", team="marketing_product"),
    ),
    ("ok", verdict(message_type="casual", is_affirmative=True)),
    ("pelase escalate to marketing team MWc-SC8609-)PP water closet", _typo_verdict()),
    ("pelase escalate to marketing team MWc-SC8609-PP water closet", _fixed_code_verdict()),
    ("1", _position(1)),
]


def _seed_round5_catalogue(session_factory) -> None:
    """Round 4's catalogue plus the two Mocha siblings the owner's did-you-mean named, and
    the contact linked to the Mocha company too: his did-you-mean offered Mocha codes, so
    his contact sees them."""
    _seed_owners_catalogue(session_factory)
    _seed_mocha_company_item(session_factory, code="MWC-SC8606-PP")
    _seed_mocha_company_item(session_factory, code="MWC-SC86-PP")


def _two_kinds_for_water_closet(monkeypatch) -> None:
    """The owner's fifth message asked "Which one do you mean? water closet (promotion) /
    water closet (attachment_type)": the resolver placed the class word in two kinds.
    Reproduced at the resolver seam, so whatever reaches `apply()` sees that same map."""
    import dataclasses

    from app.services.chatbot import turn_runtime

    real = turn_runtime.resolve_kinds

    def resolve_kinds(*args: Any, **kwargs: Any):
        outcome = real(*args, **kwargs)
        entities = (((kwargs.get("ctx") or {}).get("parse") or {}).get("output") or {}).get("entities") or []
        if not any(isinstance(e, dict) and e.get("raw") == "water closet" for e in entities):
            return outcome
        kinds = dict(outcome.resolved_kinds or {})
        kinds["water closet"] = {"promotion": 1, "attachment_type": 1}
        return dataclasses.replace(outcome, resolved_kinds=kinds)

    monkeypatch.setattr(turn_runtime, "resolve_kinds", resolve_kinds)


def _replay(session_factory, monkeypatch, stub_parser, stub_access, messages) -> list[tuple[Any, list]]:
    from app.services.chatbot import console_service

    _seed_round5_catalogue(session_factory)
    _seed_contact(session_factory, phone="+60900000055")
    _link_contact_company(session_factory, company_id=MOCHA_COMPANY_ID)
    _seed_borrowable_envelope(session_factory)
    _seed_marketing_product_team(session_factory)
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)
    stub_access()
    _stub_any_product_tool(monkeypatch)
    _two_kinds_for_water_closet(monkeypatch)
    calls = _capture_real_next_assignee(monkeypatch)
    sla = _capture_sla(monkeypatch)

    results: list[tuple[Any, list]] = []
    session_vars: dict[str, Any] = {}
    db = session_factory()
    try:
        for text_, parsed in messages:
            stub_parser(parsed)
            before = len(calls)
            turn = console_service.run_console_turn(
                db,
                contact_respond_id=str(CONTACT_ID),
                text=text_,
                session_vars=session_vars,
                run_id="zzt-865-r5",
            )
            results.append((turn, list(calls[before:])))
            session_vars = turn.session_vars or {}
    finally:
        db.close()
    assert sla == [], "a console turn is a dry run: no SLA row"
    return results


def _assert_escalated(session_factory, turn, calls, *, brand: str | None, assignee: str, company: str | None):
    assert turn.branch_kind == "out_of_scope", (turn.branch_kind, turn.send_messages)
    assert len(calls) == 1, calls
    body = calls[0]["body"]
    assert (body["team_code"], body["brand_code"]) == ("marketing_product", brand), body
    assert calls[0]["response"].get("assignee_name") == assignee, calls[0]["response"]
    routing = _looked_up_routing(session_factory, turn.turn_id)
    assert routing["team_code"] == "marketing_product", routing
    assert routing["brand_code"] == brand, routing
    assert routing.get("product_company") == company, routing
    assert turn.send_messages[-1] == ROUTED_TO_MARKETING, turn.send_messages


def _no_customer_service_offer(turn) -> None:
    text_ = " ".join([turn.reply_text or "", *(turn.send_messages or [])]).lower()
    assert "customer service" not in text_, (turn.reply_text, turn.send_messages)
    assert "which one do you mean" not in text_, (turn.reply_text, turn.send_messages)


class TestTheOwnersRound4HandTestReplayed:
    def test_the_six_messages_in_one_console_conversation(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R1 to R4: every turn's branch kind, team, brand and reply."""
        results = _replay(session_factory, monkeypatch, stub_parser, stub_access, OWNERS_28SEP_ROUND5)
        (spec, spec_calls), second, (ok, ok_calls), (typo, typo_calls), fixed, (one, one_calls) = results

        assert spec.branch_kind == "business_query", spec.branch_kind
        assert spec_calls == []

        _assert_escalated(session_factory, *second, brand="sorento", assignee="ZZT Tay Zhi Yang", company="Sorento")

        assert ok.branch_kind == "low_signal", ok.branch_kind
        assert ok_calls == []

        # The typo: an escalation, not a spec answer. One question, inside the escalation:
        # the resolver's own did-you-mean, answered by a number, naming the team it goes to.
        assert typo.branch_kind == "out_of_scope", (typo.branch_kind, typo.send_messages)
        assert typo_calls == [], "nothing is drawn until the product is settled"
        assert typo.send_messages == [DID_YOU_MEAN], typo.send_messages
        assert typo.trace_summary["routing_line"] == (
            "Routing: team marketing_product, brand none, source none, "
            "product MWC-SC8609-)PP not found in any company, assignee none"
        ), typo.trace_summary
        _no_customer_service_offer(typo)

        # The corrected code: an escalation, never the kind menu.
        _assert_escalated(session_factory, *fixed, brand="mocha", assignee="ZZT Kia Yee", company="Mocha")
        assert fixed[0].trace_summary["routing_line"] == (
            "Routing: team marketing_product, brand mocha, source focus_product, "
            "found in Mocha, assignee ZZT Kia Yee"
        ), fixed[0].trace_summary
        _no_customer_service_offer(fixed[0])

        # "1" after the escalation went out: the fifth message closed the did-you-mean,
        # so the number picks nothing and hands nobody over. With nothing open it is the
        # bare number it always was (the product in focus answered again), and it offers
        # no customer service.
        assert one.branch_kind == "business_query", (one.branch_kind, one.reply_text)
        assert one_calls == []
        assert one.session_vars.get("open_question") is None, one.session_vars
        _no_customer_service_offer(one)

    def test_the_typo_then_its_number_escalates_with_the_picked_products_brand(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R2: the did-you-mean answered straight away sends the escalation out, to the
        team the customer named, with the PICKED product's brand and company."""
        messages = [OWNERS_28SEP_ROUND5[0], OWNERS_28SEP_ROUND5[3], ("1", _position(1))]
        (_spec, _), (typo, typo_calls), (one, one_calls) = _replay(
            session_factory, monkeypatch, stub_parser, stub_access, messages
        )
        assert typo.send_messages == [DID_YOU_MEAN], typo.send_messages
        assert typo_calls == []
        # The question is an escalation offer over the three products, each carrying the
        # team the customer named.
        question = typo.session_vars["open_question"]
        assert question["kind"] == "team_pick", question
        assert [(o["label"], o["entity_type"], o["payload"]["team"]) for o in question["options"]] == [
            ("MWC-SC8609-PP", "product", "marketing_product"),
            ("MWC-SC8606-PP", "product", "marketing_product"),
            ("MWC-SC86-PP", "product", "marketing_product"),
        ], question
        _assert_escalated(session_factory, one, one_calls, brand="mocha", assignee="ZZT Kia Yee", company="Mocha")
        routing = _looked_up_routing(session_factory, one.turn_id)
        assert routing["routing_source"] == "focus_product", routing
        assert one.trace_summary["routing_line"] == (
            "Routing: team marketing_product, brand mocha, source focus_product, "
            "found in Mocha, assignee ZZT Kia Yee"
        ), one.trace_summary
        _no_customer_service_offer(one)

    def test_a_bare_number_with_nothing_open_and_a_product_in_focus_is_the_product_again(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """RELEASE-HOTFIX-0930B: the sixth message on its own. "1" typed casual with only
        a position, nothing open and SRTWC286 in focus is "that product again" (owner
        ruling, round 4): the phantom-answer guard (`turn/question.without_phantom_answer`,
        #1391) must leave the position alone, since it is what keeps the number out of
        idle chat, and the turn carries the focus domain to `business_query` instead of
        `casual` -> `low_signal` (release run 36675584567)."""
        from app.models.chatbot_turn import ChatbotTurn

        messages = [OWNERS_28SEP_ROUND5[0], ("1", _position(1))]
        (spec, _), (one, one_calls) = _replay(session_factory, monkeypatch, stub_parser, stub_access, messages)
        assert spec.branch_kind == "business_query", spec.branch_kind
        assert (spec.session_vars.get("focus") or {}).get("products"), spec.session_vars
        assert spec.session_vars.get("open_question") is None, spec.session_vars

        assert one.branch_kind == "business_query", (one.branch_kind, one.reply_text)
        assert one_calls == []
        assert one.session_vars.get("open_question") is None, one.session_vars
        _no_customer_service_offer(one)
        db = session_factory()
        try:
            row = db.query(ChatbotTurn).filter(ChatbotTurn.id == one.turn_id).first()
            trace = list(row.trace or []) if row is not None else []
        finally:
            db.close()
        understood = next(r for r in trace if r.get("stage") == "understood")
        derived = (understood.get("raw") or {}).get("derived") or {}
        assert derived.get("reference_positions") == [1], derived
        assert [r for r in trace if r.get("kind") == "phantom_answer"] == [], trace

    @pytest.mark.parametrize(
        "message_type", ["business_query", "request_for_help", "clarification", "casual"]
    )
    def test_an_escalate_word_and_a_team_escalate_whatever_the_parser_typed_the_message(
        self, session_factory, stub_parser, stub_access, monkeypatch, message_type
    ) -> None:
        """R1: the message type the parser chose does not decide it; the escalate word
        and the team in its own reading of the message do."""
        parsed = {**_fixed_code_verdict(), "message_type": message_type, "domain_hint": "master_products"}
        [(turn, calls)] = _replay(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            [("pelase escalate to marketing team MWc-SC8609-PP water closet", parsed)],
        )
        _assert_escalated(session_factory, turn, calls, brand="mocha", assignee="ZZT Kia Yee", company="Mocha")

    def test_a_product_ask_that_mentions_a_team_in_passing_is_still_a_product_ask(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """R5: no escalate word, no escalation. "the marketing team's catalogue for X" is
        a question about X."""
        parsed = _product_verdict(
            "master_products",
            "check_product",
            "srtwc286",
            user_goal="trying to get the spec of SRTWC286 for the marketing team",
        )
        [(turn, calls)] = _replay(
            session_factory,
            monkeypatch,
            stub_parser,
            stub_access,
            [("spec srtwc286 for marketing team", parsed)],
        )
        assert turn.branch_kind == "business_query", turn.branch_kind
        assert calls == []


class TestTheEscalateWordAndTeamReader:
    @pytest.mark.parametrize(
        ("goal", "expected"),
        [
            ("trying to escalate MWc-SC8609-)PP water closet to the marketing team", True),
            ("trying to escalate to the marketing team", True),
            ("wants to pass this to the purchasing team", True),
            ("wants to talk to customer service about SRT1", True),
            ("trying to get the spec of SRTWC286 for the marketing team", False),
            ("trying to escalate this", False),
            ("trying to get the marketing flyer", False),
            (None, False),
        ],
        ids=["typo-and-class-word", "team-only", "pass-to", "talk-to", "no-escalate-word", "no-team", "neither", "no-goal"],
    )
    def test_an_escalate_word_and_a_named_team_both_count(self, goal, expected) -> None:
        from app.services.chatbot.lanes.escalation import asks_for_a_named_team

        assert asks_for_a_named_team({"user_goal": goal}) is expected

    def test_the_verdict_is_retyped_and_names_the_teams(self) -> None:
        from app.services.chatbot.turn_runtime import with_named_team_escalation

        out = with_named_team_escalation(_typo_verdict())
        assert out["message_type"] == "request_for_help", out
        assert out["escalation"]["named_teams"] == ["marketing_product", "marketing_form", "marketing_promotion"]
        assert out["entities"] == _typo_verdict()["entities"], "the product words stay as the focus"

    @pytest.mark.parametrize("domain", ["portal_link", "ideate"])
    def test_an_answered_domain_is_left_alone(self, domain) -> None:
        """Contract 21, 22: these domains answer a request for help rather than escalate."""
        from app.services.chatbot.turn_runtime import with_named_team_escalation

        parsed = {**_typo_verdict(), "domain_hint": domain}
        assert with_named_team_escalation(parsed) is parsed

    def test_a_message_with_no_escalate_word_is_left_alone(self) -> None:
        from app.services.chatbot.turn_runtime import with_named_team_escalation

        parsed = _product_verdict("master_products", "check_product", "srtwc286", user_goal="spec for the marketing team")
        assert with_named_team_escalation(parsed) is parsed


class TestApplyPlansTheEscalationOnly:
    """R1 at `apply()`: the named-team turn is the escalation lane's, with no fetch, no
    narrowing or kind question, and an open offer for another team is not accepted."""

    def _run(self, parsed, *, pending=None, resolved=None):
        from app.services.chatbot.turn.apply import apply
        from app.services.chatbot.turn.state import Focus, Profile, State
        from app.services.chatbot.turn_runtime import with_named_team_escalation

        state = State(focus=Focus(), pending=pending, profile=Profile(), turn_no=3)
        return apply(state, with_named_team_escalation(parsed), build_policy(), resolved)

    def test_the_kind_menu_is_never_asked(self) -> None:
        _state, plan = self._run(
            _fixed_code_verdict(), resolved={"water closet": {"promotion": 1, "attachment_type": 1}}
        )
        assert plan.ask is None, plan.ask
        assert plan.fetch == []
        assert plan.trace.lane == "escalation"
        assert "named_team_asks_no_kind" in plan.trace.rules_fired

    def test_the_typo_is_the_focus_and_nothing_is_fetched(self) -> None:
        state, plan = self._run(_typo_verdict())
        assert plan.trace.lane == "escalation"
        assert plan.fetch == [] and plan.ask is None
        assert [p["raw"] for p in state.focus.products] == ["MWc-SC8609-)PP"]

    def test_an_offer_for_another_team_is_not_accepted(self) -> None:
        """#706: "escalate to marketing team X" over an open warehouse offer is a new
        request to marketing, and the offer closes."""
        from app.services.chatbot.turn.pending import ask

        offer = ask(
            "team_pick",
            [{"position": 1, "label": "Warehouse", "entity_type": "team", "payload": {"team": "warehouse"}}],
            team="warehouse",
            expects="yes_no",
        )
        parsed = {**_fixed_code_verdict(), "is_affirmative": True}
        state, plan = self._run(parsed, pending=offer)
        assert plan.trace.lane == "escalation"
        assert plan.trace.team is None, "the warehouse offer was accepted"
        assert "named_team_is_a_new_request" in plan.trace.rules_fired
        assert state.pending is None

    def test_an_offer_for_the_named_team_is_still_accepted(self) -> None:
        """#1108: accepting an offer made for the team the customer named keeps the
        offer's carried agent and brand, so it stays an acceptance."""
        from app.services.chatbot.turn.pending import ask

        offer = ask(
            "team_pick",
            [{"position": 1, "label": "Marketing", "entity_type": "team", "payload": {"team": "marketing_product"}}],
            team="marketing_product",
            expects="yes_no",
            payload={"agent": "general_enquiries", "brand_code": "mocha"},
        )
        # #1323: an escalation offer is accepted on the parser's semantic verdict only;
        # asking for the handover to the offered team is that verdict (the prompt's
        # ESCALATION CONFIRMATION section), whatever product the message names.
        parsed = {
            **_fixed_code_verdict(),
            "is_affirmative": True,
            "escalation": {"is_escalation_confirmation": True, "company_pick": None},
        }
        _state, plan = self._run(parsed, pending=offer)
        assert plan.trace.lane == "escalation"
        assert plan.trace.team == "marketing_product"
        assert "answer_pending_accept" in plan.trace.rules_fired

    def test_a_bare_yes_over_the_product_did_you_mean_picks_nothing(self) -> None:
        """Only a number answers the did-you-mean when it offered several products."""
        from app.services.chatbot.turn.pending import ask

        offer = ask(
            "team_pick",
            [
                {"position": i, "label": c, "uuid": None, "uuids": [], "entity_type": "product",
                 "payload": {"team": "marketing_product", "product_code": c}}
                for i, c in enumerate(["MWC-SC8609-PP", "MWC-SC8606-PP"], start=1)
            ],
            team="marketing_product",
            expects="pick",
        )
        _state, plan = self._run(verdict(message_type="casual", is_affirmative=True), pending=offer)
        assert plan.trace.lane != "escalation", plan.trace.rules_fired
        assert "escalation_product_picked" not in plan.trace.rules_fired


class TestTheLanesOwnDidYouMean:
    def _item(self, raw: str) -> dict[str, Any]:
        return {
            "team": "marketing_product",
            "focus_products": [{"raw": raw, "hint": "product", "canonical_code": raw}],
            "product_not_found": [raw.upper()],
        }

    def _bundle(self, codes):
        from types import SimpleNamespace

        return SimpleNamespace(product_suggestions=lambda code: list(codes))

    def test_no_suggestion_means_the_escalation_goes_out(self) -> None:
        from app.services.chatbot.lanes.escalation import _product_clarify

        assert _product_clarify(self._item("ZZTNOPE99"), None, "marketing_product", self._bundle([])) is None

    def test_a_class_word_is_never_asked_about(self) -> None:
        from app.services.chatbot.lanes.escalation import _product_clarify

        item = self._item("water closet")
        assert _product_clarify(item, None, "marketing_product", self._bundle(["WC1"])) is None

    def test_a_team_clarify_asks_first(self) -> None:
        from app.services.chatbot.lanes.escalation import _product_clarify

        routed = {"kind": "clarify", "text": "Which team", "options": [], "option_pairs": []}
        assert _product_clarify(self._item("MWC-X1"), routed, None, self._bundle(["MWC-X2"])) is None

    def test_a_named_person_is_never_asked_about_a_product(self) -> None:
        from app.services.chatbot.lanes.escalation import _product_clarify

        routed = {"kind": "assign", "team": "marketing_product", "assignee": "user-1"}
        assert _product_clarify(self._item("MWC-X1"), routed, None, self._bundle(["MWC-X2"])) is None

    def test_the_question_names_the_routed_team(self) -> None:
        from app.services.chatbot.lanes.escalation import _product_clarify

        routed = {"kind": "assign", "team": "warehouse", "assignee": None}
        asked = _product_clarify(self._item("SRT-X1"), routed, "purchasing", self._bundle(["SRT-X2"]))
        assert asked["text"] == (
            'Couldn\'t find "SRT-X1". Did you mean:\n1. SRT-X2\n'
            "Reply with the number and I will pass it to the warehouse team."
        )
        assert asked["option_pairs"] == [
            {"position": 1, "label": "SRT-X2", "product_code": "SRT-X2", "team": "warehouse"}
        ]
