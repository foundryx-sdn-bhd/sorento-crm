"""Top selling fix lane round 6: the owner's retest of round 5 (27 Sep 2026 20:04 MYT,
console :3083, parser v39, PR #1273).

The owner typed "top 100 sold item", got "By quantity or by amount?", typed "amount" and
got the order list. Both turns ran in the console (dry run, same contact).

Diagnosis (replayed here the way the console runs them: `console_service.
run_console_turn`, the second turn sent the `session_vars` the first returned, as
`useChatbotConsole` does):

* The console DOES remember the ask between dry runs. `remembered: written false` is the
  dry run's no-write; the page carries the turn's `session_patch` itself, and after turn
  1 it holds `focus.status: "top_selling"` and `focus.top_selling.asked: "metric"`.
* No code asks the metric question without the parser's `order_status: "top_selling"`
  (`lanes/business/__init__.py`, the top selling override is the only asker), so v39
  did read "top 100 sold item" as the ranking and the ask was already in progress.
* The parser then read "amount" as a NEW order ask (`domain_in_message: true`, no
  `rank_by`), and `turn/apply._top_selling_rules` left the ranking for it. That reading
  alone reproduces the owner's screen ("Dates: all dates", the order list).

Fix: the answer to the bot's own ranking question binds in code
(`engine._top_selling_question_answer`), whatever the parser made of it, in the console
and on WhatsApp alike; and the parser words learn "sold item", "top sellers", "highest
selling". Harness as round 5 (the parser, access and MCP faked, the presenter real).
Postgres only, every row seeded here.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from tests.chatbot._turn_helpers import do_window
from tests.chatbot import test_outstanding_lane as outstanding_lane
from tests.chatbot.test_engine import CONTACT_ID, _envelope
from tests.chatbot.test_outstanding_lane import _resolve_services, _wire_business_services
from tests.chatbot.test_top_selling_round5 import (  # noqa: F401 - fixtures
    ASK_METRIC,
    GRANTS,
    ORDERS,
    OUTSTANDING,
    TOOL,
    _RANK_NULLS,
    _answer,
    _ask,
    _calls,
    _parser_output,
    _report,
    _SNAKE,
    _turn,
    cat,
    route,
)

ASK_GROUP = "Do you want the top items inside one category, or the categories ranked against each other?"
ASK_BASIS = "Delivered (transferred to DO) or ordered?"


# --------------------------------------------------------------------------- #
# The console, as the page runs it
# --------------------------------------------------------------------------- #


def _seed_borrowable_envelope(session_factory) -> None:
    """The contact's last real inbound envelope, the one `console_service.
    _borrow_envelope` reads (a console turn cannot run without one). Same seed as PR
    #1300 round 3's console replay."""
    from app.models.chatbot_turn import ChatbotTurn

    db = session_factory()
    try:
        db.add(
            ChatbotTurn(
                contact_respond_id=str(CONTACT_ID),
                message_id="ZZT-top-selling-r6-seed",
                ingress="webhook",
                envelope=json.loads(_envelope().model_dump_json()),
                is_test=False,
                status="done",
                stage="sent",
                branch_kind="business_query",
            )
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture
def console(session_factory, monkeypatch, cat):
    """One console turn: `console_service.run_console_turn` (dry run, `is_test`,
    `ingress=console`), the parser answering `qf`. Returns `(result, captured)`."""
    from app.services.chatbot import console_service
    from app.services.chatbot.head import parser as parser_mod

    _seed_borrowable_envelope(session_factory)
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True, "decision": "allow", "agent_name": "General",
            "attributes": GRANTS, "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
    monkeypatch.setattr(
        parser_mod,
        "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=39, provider="openai", model="gpt-test", api_key="sk-test",
        ),
    )

    def _run(qf: dict[str, Any], body: str, session_vars: dict[str, Any] | None):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
        call, captured = outstanding_lane._capturing_mcp()
        _wire_business_services(monkeypatch, resolve_services=_resolve_services({}), mcp_call=call)
        db = session_factory()
        try:
            result = console_service.run_console_turn(
                db, contact_respond_id=str(CONTACT_ID), text=body, session_vars=session_vars, run_id="zzt-top-selling-r6",
            )
        finally:
            db.close()
        assert not _SNAKE.search(result.reply_text or ""), (body, result.reply_text)
        assert "\u2014" not in (result.reply_text or "") and "\u2013" not in (result.reply_text or ""), body
        return result, list(captured)

    return _run


def _fresh_order(order_status: str | None = "all", **overrides: Any) -> dict[str, Any]:
    """"amount" read as a NEW order ask: the reading that reproduces the owner's screen
    (`domain_in_message: true`, no `rank_by`, a status outside the report hops)."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        order_status=order_status, entities=[], domain_in_message=True, **_RANK_NULLS,
    )
    base.update(overrides)
    return _parser_output(**base)


#: Every reading of "amount" the parser can plausibly make under the metric question:
#: the answer the words teach, the owner's (a new order ask), a new ask with no status
#: (which used to hop to the order report), and a casual one.
AMOUNT_READINGS = {
    "the_answer": lambda: _answer(rank_by="amount"),
    "owner_v39_new_order_ask": lambda: _fresh_order("all"),
    "new_ask_no_status": lambda: _fresh_order(None),
    "new_ask_pending": lambda: _fresh_order("pending"),
    "casual": lambda: _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[], order_status=None, **_RANK_NULLS,
    ),
}


def _assert_ranking(text: str, captured, *, rank_by: str, top_n: int | None = 100) -> dict[str, Any]:
    calls = _calls(captured)
    assert len(calls) == 1, (text, captured)
    assert _calls(captured, ORDERS) == [], "never the order list"
    assert calls[0]["rank_by"] == rank_by, calls[0]
    if top_n is not None:
        assert calls[0].get("n") == top_n, calls[0]
        assert text.startswith(f"*Top {top_n} selling items*"), text
    label = {"amount": "Amount", "quantity": "Quantity"}[rank_by]
    assert f"Ranked by: {label}" in text, text
    assert "Here are the orders I found" not in text, text
    return calls[0]


# --------------------------------------------------------------------------- #
# The owner's two messages, in the console
# --------------------------------------------------------------------------- #


class TestTheOwnersConsoleRetest:
    def test_turn_one_sets_the_ranking_as_the_ask_in_progress(self, console) -> None:
        """"top 100 sold item" asks the metric, and the reply the console hands the next
        turn carries the ask and the question (the console's own carry)."""
        turn1, captured = console(_ask(top_n=100), "top 100 sold item", {})
        assert turn1.reply_text == ASK_METRIC
        assert captured == []
        focus = (turn1.session_vars or {}).get("focus") or {}
        assert focus.get("status") == "top_selling", focus
        assert focus.get("top_selling") == {"top_n": 100, "asked": "metric"}, focus

    @pytest.mark.parametrize("reading", list(AMOUNT_READINGS))
    def test_top_100_sold_item_then_amount_is_the_ranking_by_amount(self, console, reading) -> None:
        """The owner's exact two messages. On d59e1dcf the "owner_v39_new_order_ask"
        reading answered "Customer: all customers / Product: all products / Dates: all
        dates / Here are the orders I found."; "new_ask_no_status" the same list under
        the ranking's filters line."""
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        turn2, captured = console(AMOUNT_READINGS[reading](), "amount", turn1.session_vars)
        _assert_ranking(turn2.reply_text, captured, rank_by="amount")

    def test_the_same_two_messages_on_whatsapp(self, session_factory, monkeypatch, cat) -> None:
        """The live path (the contact's stored session) lands the same way."""
        text, _ = _turn(session_factory, monkeypatch, _ask(top_n=100), "top 100 sold item")
        assert text == ASK_METRIC
        text, captured = _turn(session_factory, monkeypatch, _fresh_order("all"), "amount")
        _assert_ranking(text, captured, rank_by="amount")


# --------------------------------------------------------------------------- #
# R1: the words; each taught phrase, replayed with the owner's second message
# --------------------------------------------------------------------------- #


#: The phrases the owner listed, and the top N each names.
SOLD_PHRASES = {
    "top 100 sold item": 100,
    "top 100 sold items": 100,
    "most sold item": None,
    "best selling": None,
    "top selling": None,
    "top 100 hot selling item": 100,
    "highest selling": None,
    "top sellers": None,
}


class TestR1TheSoldWords:
    def test_the_prompt_teaches_every_phrase(self) -> None:
        from app.services.chatbot_parser_prompt import (
            ESCALATION_CONFIRMATION_ADDENDUM,
            ACCOUNT_LEDGER_ADDENDUM,
            MEMORY_ADDENDUM,
            PO_SPO_WAREHOUSE_ADDENDUM,
            SELF_REFERENCE_ADDENDUM,
            SEMANTIC_PARSER_PROMPT,
            TOP_SELLING_ADDENDUM,
        )

        for phrase in SOLD_PHRASES:
            assert f'"{phrase}"' in TOP_SELLING_ADDENDUM, phrase
        # The round 5 words are the base, untouched.
        for words in ('"best selling", "hot selling", "top items", "most sold"', '"worst 100 hot selling bathtub"',
                      '"fanny water closet" -> {raw: "fanny", hint: "sales_agent"}', '"can show me the DO"'):
            assert words in TOP_SELLING_ADDENDUM, words
        assert SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM).removesuffix(ACCOUNT_LEDGER_ADDENDUM).removesuffix(PO_SPO_WAREHOUSE_ADDENDUM).removesuffix(
            ESCALATION_CONFIRMATION_ADDENDUM
        ).removesuffix(SELF_REFERENCE_ADDENDUM).endswith(
            TOP_SELLING_ADDENDUM
        )

    def test_the_prompt_says_an_answer_is_never_a_new_order_ask(self) -> None:
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        for words in ('domain_in_message false', "never an order list", '"sold" or "sold items" is the ranking'):
            assert words in TOP_SELLING_ADDENDUM, words

    @pytest.mark.parametrize("phrase", list(SOLD_PHRASES))
    def test_each_phrase_then_amount_ranks_by_amount(self, console, phrase) -> None:
        top_n = SOLD_PHRASES[phrase]
        turn1, _ = console(_ask(top_n=top_n), phrase, {})
        assert turn1.reply_text == ASK_METRIC, phrase
        turn2, captured = console(_fresh_order("all"), "amount", turn1.session_vars)
        call = _assert_ranking(turn2.reply_text, captured, rank_by="amount", top_n=top_n)
        if top_n is None:
            assert "n" not in call, call


# --------------------------------------------------------------------------- #
# R2: every question the ranking lane asks is remembered into the next turn
# --------------------------------------------------------------------------- #


class TestR2TheMetricAnswer:
    @pytest.mark.parametrize(
        "body,rank_by",
        [("amount", "amount"), ("amt", "amount"), ("by amount", "amount"), ("Amount.", "amount"),
         ("qty", "quantity"), ("quantity", "quantity"), ("by qty please", "quantity"),
         ("1", "quantity"), ("2", "amount")],
    )
    def test_the_answer_runs_the_ranking_whatever_the_parser_read(self, console, body, rank_by) -> None:
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        turn2, captured = console(_fresh_order("all"), body, turn1.session_vars)
        _assert_ranking(turn2.reply_text, captured, rank_by=rank_by)

    def test_a_position_the_parser_read_is_the_option(self, console) -> None:
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        position = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[], reference_positions=[2],
            order_status=None, **_RANK_NULLS,
        )
        turn2, captured = console(position, "2", turn1.session_vars)
        _assert_ranking(turn2.reply_text, captured, rank_by="amount")

    def test_an_answer_carrying_more_keeps_the_parser_keys(self, console) -> None:
        """"amount, top 20" under the question: the metric binds, the count the parser
        read is kept."""
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        turn2, captured = console(_answer(rank_by="amount", top_n=20), "amount, top 20", turn1.session_vars)
        _assert_ranking(turn2.reply_text, captured, rank_by="amount", top_n=20)

    def test_a_report_ask_under_the_question_is_not_bound(self, console) -> None:
        """"outstanding" names no option, so it is not read as the metric: the ranking
        does not run and the question is not asked again (round 5's hop decides the
        rest, unchanged)."""
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        turn2, captured = console(_report("outstanding"), "outstanding", turn1.session_vars)
        assert _calls(captured) == [], captured
        assert turn2.reply_text != ASK_METRIC

    def test_a_new_ask_naming_no_option_still_leaves(self, console) -> None:
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        turn2, captured = console({**_fresh_order("all"), **do_window()}, "show me the orders of last week please", turn1.session_vars)
        assert _calls(captured) == []
        assert _calls(captured, ORDERS), captured

    def test_the_question_is_answered_once(self, console) -> None:
        """After the ranking ran, a later "amount" is not an answer to a question no one
        asked any more: the ranking on screen is re-run by amount only through the words
        (the parser's `rank_by`), never re-bound by the text."""
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        turn2, _ = console(_fresh_order("all"), "amount", turn1.session_vars)
        focus = (turn2.session_vars or {}).get("focus") or {}
        assert "asked" not in (focus.get("top_selling") or {}), focus


class TestR2EveryOtherQuestion:
    def test_the_grain_question(self, console) -> None:
        turn1, _ = console(_ask(top_n=10, rank_by="amount", rank_group="unclear"), "top 10 selling by category", {})
        assert turn1.reply_text == ASK_GROUP
        turn2, captured = console(_fresh_order("all"), "categories", turn1.session_vars)
        call = _calls(captured)
        assert len(call) == 1 and call[0].get("group") == "category", captured
        assert _calls(captured, ORDERS) == []

    def test_the_grain_question_by_items(self, console) -> None:
        turn1, _ = console(_ask(top_n=10, rank_by="amount", rank_group="unclear"), "top 10 selling by category", {})
        turn2, captured = console(_fresh_order("all"), "items", turn1.session_vars)
        call = _calls(captured)
        assert len(call) == 1 and "group" not in call[0], captured

    def test_the_basis_question(self, console) -> None:
        turn1, _ = console(_ask(top_n=10, rank_by="amount", basis="unclear"), "top 10 orders", {})
        assert turn1.reply_text == ASK_BASIS
        turn2, captured = console(_fresh_order("all"), "ordered", turn1.session_vars)
        call = _calls(captured)
        assert len(call) == 1 and call[0].get("basis") == "ordered", captured
        assert _calls(captured, ORDERS) == []

    def test_the_basis_question_by_number(self, console) -> None:
        turn1, _ = console(_ask(top_n=10, rank_by="amount", basis="unclear"), "top 10 orders", {})
        turn2, captured = console(_fresh_order("all"), "1", turn1.session_vars)
        call = _calls(captured)
        assert len(call) == 1 and call[0].get("basis") == "delivered", captured

    @pytest.mark.parametrize("body", ["20", "top 20"])
    def test_the_how_many_question(self, console, body) -> None:
        """The count question the route asks (`top_selling_how_many`), carried the same
        way: seeded as the console holds it after that reply."""
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        carried = copy.deepcopy(turn1.session_vars)
        carried["focus"]["top_selling"] = {"rank_by": "amount", "asked": "how_many"}
        turn2, captured = console(_fresh_order("all"), body, carried)
        _assert_ranking(turn2.reply_text, captured, rank_by="amount", top_n=20)

    def test_the_category_question(self, console, cat) -> None:
        """"Which category do you mean? Reply with one code: ..." answered with a code."""
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        carried = copy.deepcopy(turn1.session_vars)
        carried["focus"]["top_selling"] = {"rank_by": "amount", "top_n": 100, "asked": "category", "category_words": ["wc"]}
        turn2, captured = console(_fresh_order("all"), "SRT-WC", carried)
        call = _assert_ranking(turn2.reply_text, captured, rank_by="amount")
        assert call.get("category_ids") == [cat.water_closet[0]], call

    def test_the_who_question(self, console, cat) -> None:
        """Round 5's who binding, now pinned in the console too."""
        turn1, _ = console(_ask(top_n=100), "top 100 sold item", {})
        carried = copy.deepcopy(turn1.session_vars)
        carried["focus"]["top_selling"] = {
            "rank_by": "amount", "top_n": 100, "asked": "who",
            "who": {"word": "sean", "agent_ids": list(cat.sean_agents), "agent_label": "SEAN I, SEAN III, SEAN IV"},
        }
        turn2, captured = console(_fresh_order("all"), "2", carried)
        call = _assert_ranking(turn2.reply_text, captured, rank_by="amount")
        assert sorted(call.get("sales_agent_ids") or []) == sorted(cat.sean_agents), call
