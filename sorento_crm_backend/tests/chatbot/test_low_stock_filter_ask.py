"""LOWSTOCK-FILTER-ASK: a chat low stock ask settles its product category before any run.

`documentation/plans/chatbot/lowstock-filter-ask-behaviour-card.md`, "Ruled behaviour":

* the product category is the ONE required field: taken from the message (a category word,
  narrowed by a brand word, or a brand alone), else asked, and nothing runs until it is
  settled ("all" settles it as the whole book);
* supplier and group-by are never asked: taken in when the message names them, otherwise
  no supplier filter and no grouping;
* every failure ends in a clear line (unknown twice ends the ask; "cancel" cancels).

Full console turns: the REAL engine, lane and MCP presenter; only the parser and the MCP
transport are doubles. Each `say` returns the reply text and every tool call it made, so
"no reorder run before the category is settled" is pinned as "no `crm_low_stock_report`
call on that turn".
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_outstanding_lane import (
    AnswerServices,
    FetchServices,
    _present_response,
    _seed_contact,
)
from tests.chatbot.test_top_selling_round6 import _seed_borrowable_envelope

TOOL = "crm_low_stock_report"
GRANT = "scm.low_stock_report"
SUPPLIER_KEY = "purchase_orders.supplier"
QUESTION = 'Which product category? Reply with a category (e.g. water tap) or "all".'
_SNAKE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")

READY = {
    "status": "ready",
    "low_count": 3,
    "all_count": 10,
    "as_of": "2026-10-02",
    "attachments": [{
        "url": "https://cdn.example.com/exports/low-stock/x/low-stock-02102026.xlsx",
        "filename": "low-stock-02102026.xlsx",
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "attachmentType": "file",
    }],
}


def _e(raw: str, hint: str) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}


def _ask(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The parser's reading of a low stock ask (LOW_STOCK_ADDENDUM)."""
    return _parser_output(
        domain_hint="inventory", intent_hint="low_stock_report", entities=list(entities), **overrides
    )


def _reply(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The parser's reading of a short reply: whatever it makes of the bare words."""
    base = dict(message_type="business_query", domain_hint="master_products", intent_hint="check_product",
                entities=list(entities))
    base.update(overrides)
    return _parser_output(**base)


def _stock_ask() -> dict[str, Any]:
    return _parser_output(domain_hint="inventory", intent_hint="check_stock",
                          entities=[_e("CB100", "product"), _e("BRW", "warehouse")])


class Console:
    def __init__(self, session_factory, monkeypatch) -> None:
        self.session_factory, self.monkeypatch = session_factory, monkeypatch
        self.session_vars: dict[str, Any] = {}
        self.route_body: dict[str, Any] = READY

    def say(self, qf: dict[str, Any], body: str) -> tuple[str, list[dict[str, Any]]]:
        from app.services.chatbot import console_service
        from app.services.chatbot.head import parser as parser_mod

        self.monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
        captured: list[tuple[str, dict[str, Any]]] = []
        present = _present_response()

        def _mcp(name: str, args: dict[str, Any]) -> Any:
            captured.append((name, dict(args)))
            if name == TOOL:
                return present(name, json.dumps(self.route_body))
            return json.dumps({"has_result": False, "items": []})

        self.monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp))
        self.monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: AnswerServices(
                mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
            ),
        )
        db = self.session_factory()
        try:
            result = console_service.run_console_turn(
                db, contact_respond_id=str(CONTACT_ID), text=body,
                session_vars=copy.deepcopy(self.session_vars), run_id="zzt-lowstock-filter-ask",
            )
        finally:
            db.close()
        self.session_vars = result.session_vars or {}
        text = result.reply_text or ""
        assert not _SNAKE.search(text), (body, text)
        assert "\u2014" not in text and "\u2013" not in text, body
        return text, [args for name, args in captured if name == TOOL]


def _seed(session_factory) -> None:
    from app.models.procurement import Supplier
    from app.models.product import ProductCategory
    from app.services.product_class_signal import CLASS_SYNONYMS
    from tests._mc_lookup_seed import product, warehouse

    db = session_factory()
    try:
        product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWT7408")
        warehouse(db, company_id=DEFAULT_COMPANY_ID, code="BRW")
        for code, label, brand in (
            ("SRT-FT", "Tap", "Sorento"),
            ("CB-FT", "Tap", "Cabana"),
            ("SRT-WC", "Water Closet", "Sorento"),
        ):
            db.add(ProductCategory(
                id=str(uuid.uuid4()), category_code=code, category_name=code, class_label=label,
                brand_hint=brand, search_synonyms=CLASS_SYNONYMS[label], company_id=DEFAULT_COMPANY_ID,
            ))
        for code, name in (
            ("JBC", "JINBAICHUAN TRADING"),
            ("JBCH", "JINBAICHUAN HARDWARE"),
            ("400-X006", "XIAMEN TAIYANG TECHNOLOGY CO.,LTD"),
            ("400-X008", "XIAMEN TAIYANG TECHNOLOGY CO.,LTD"),
        ):
            db.add(Supplier(id=str(uuid.uuid4()), supplier_code=code, supplier_name=name,
                            company_id=DEFAULT_COMPANY_ID, is_active=True))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def console(session_factory, monkeypatch):
    return _console(session_factory, monkeypatch, grants=[GRANT, SUPPLIER_KEY])


@pytest.fixture
def console_no_supplier_key(session_factory, monkeypatch):
    return _console(session_factory, monkeypatch, grants=[GRANT])


def _console(session_factory, monkeypatch, *, grants: list[str]) -> Console:
    from app.services.chatbot import console_service
    from app.services.chatbot.head import parser as parser_mod

    _seed_contact(session_factory, variables={})
    _seed_borrowable_envelope(session_factory)
    _seed(session_factory)
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True, "decision": "allow", "agent_name": "General",
            "attributes": grants, "all_attributes_allowed": None,
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
    return Console(session_factory, monkeypatch)


def _filter_line(text: str) -> str:
    """The applied filters, as the reply's FIRST line states them (owner hand test, 3 Oct
    2026: every low stock reply, pending included, opens "Low stock report (<filters>)")."""
    first = text.splitlines()[0] if text else ""
    m = re.match(r"^Low stock report \((.*)\)", first)
    assert m, text
    return m.group(1)


# --------------------------------------------------------------------------- #
# Taken from the message: no question
# --------------------------------------------------------------------------- #


class TestNamedInTheMessage:
    def test_the_owner_example_runs_at_once_scoped_to_the_brand_and_category(self, console) -> None:
        text, calls = console.say(
            _ask(_e("Sorento", "brand"), _e("water tap", "category")), "Sorento water tap low stock list"
        )
        (args,) = calls
        assert args.get("categories") == ["SRT-FT"], args
        assert not args.get("suppliers"), args
        assert args.get("split", "none") == "none", args
        assert QUESTION not in text
        assert _filter_line(text) == "Sorento water tap, all suppliers, no grouping"
        assert "Low: 3 of 10 planned products" in text

    def test_a_category_word_alone_takes_every_brand(self, console) -> None:
        _text, calls = console.say(_ask(_e("water tap", "category")), "water tap low stock")
        (args,) = calls
        assert sorted(args["categories"]) == ["CB-FT", "SRT-FT"], args

    def test_a_brand_alone_settles_the_category_as_that_brand(self, console) -> None:
        _text, calls = console.say(_ask(_e("Sorento", "brand")), "Sorento low stock")
        (args,) = calls
        assert sorted(args["categories"]) == ["SRT-FT", "SRT-WC"], args

    def test_all_categories_in_the_message_runs_the_whole_book(self, console) -> None:
        text, calls = console.say(_ask(), "low stock report all categories")
        (args,) = calls
        assert not args.get("categories"), args
        assert _filter_line(text) == "all categories, all suppliers, no grouping"

    def test_a_supplier_and_a_grouping_in_the_message_are_taken(self, console) -> None:
        text, calls = console.say(
            _ask(_e("water tap", "category")), "low stock water tap jinbaichuan hardware by supplier"
        )
        (args,) = calls
        assert args.get("suppliers") == ["JINBAICHUAN HARDWARE"], args
        assert args.get("split") == "supplier", args
        assert _filter_line(text) == "water tap, supplier JINBAICHUAN HARDWARE, by supplier"

    @pytest.mark.parametrize("words,split", [
        ("by category", "category"),
        ("group by category", "category"),
        ("by supplier and category", "supplier_category"),
        ("by supplier x category", "supplier_category"),
    ])
    def test_grouping_words(self, console, words, split) -> None:
        _text, calls = console.say(_ask(_e("water closet", "category")), f"low stock water closet {words}")
        (args,) = calls
        assert args.get("split") == split, args

    def test_a_product_type_the_parser_hinted_as_a_product_is_the_category(self, console) -> None:
        _text, calls = console.say(_ask(_e("water tap", "product")), "water tap low stock")
        (args,) = calls
        assert sorted(args["categories"]) == ["CB-FT", "SRT-FT"], args
        assert not args.get("product_codes"), args

    def test_a_product_code_ask_names_its_own_scope(self, console) -> None:
        _text, calls = console.say(_ask(_e("SRTWT7408", "product")), "low stock for SRTWT7408")
        (args,) = calls
        assert args.get("product_codes") == ["SRTWT7408"], args
        assert not args.get("categories"), "a product-code ask is scoped by its code; no category"


# --------------------------------------------------------------------------- #
# Asked: nothing runs until the category is settled
# --------------------------------------------------------------------------- #


class TestAsked:
    def test_a_bare_ask_asks_the_category_and_runs_nothing(self, console) -> None:
        text, calls = console.say(_ask(), "low stock report")
        assert text == QUESTION
        assert calls == [], "no reorder run before the category is settled"

    def test_the_reply_settles_it_and_runs(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_reply(_e("water closet", "product")), "water closet")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args
        assert _filter_line(text) == "water closet, all suppliers, no grouping"

    def test_all_runs_the_whole_book(self, console) -> None:
        console.say(_ask(), "low stock report")
        _text, calls = console.say(_reply(intent_hint=None, message_type="casual"), "all")
        (args,) = calls
        assert not args.get("categories"), args

    def test_a_grouping_from_the_first_message_survives_the_question(self, console) -> None:
        console.say(_ask(), "low stock report by supplier")
        _text, calls = console.say(_reply(_e("water closet", "product")), "water closet")
        (args,) = calls
        assert args.get("split") == "supplier", args

    def test_an_unknown_reply_is_said_and_asked_again(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_reply(_e("spaceship", "product")), "spaceship")
        assert calls == []
        assert text == f"I don't know 'spaceship' as a category.\n\n{QUESTION}"

    def test_two_misses_end_the_ask_and_free_the_next_message(self, console) -> None:
        console.say(_ask(), "low stock report")
        console.say(_reply(_e("spaceship", "product")), "spaceship")
        text, calls = console.say(_reply(_e("rocket", "product")), "rocket")
        assert calls == []
        assert text == (
            "I still can't place 'rocket'. Ask for the low stock report again with a category or \"all\"."
        )
        _text, calls = console.say(_reply(intent_hint=None, message_type="casual"), "all")
        assert calls == [], "the ask ended; a later 'all' must not start a run"

    def test_cancel(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_reply(intent_hint=None, message_type="casual"), "cancel")
        assert calls == [] and text == "Low stock report cancelled."

    def test_a_different_ask_drops_the_question(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_stock_ask(), "how many CB100 in BRW")
        assert calls == []
        assert QUESTION not in text
        _text, calls = console.say(_reply(intent_hint=None, message_type="casual"), "all")
        assert calls == [], "the question was dropped; 'all' must not start a run"

    def test_a_category_word_unknown_in_the_message_is_said_and_asked(self, console) -> None:
        text, calls = console.say(_ask(_e("spaceship", "category")), "spaceship low stock")
        assert calls == []
        assert text == f"I don't know 'spaceship' as a category.\n\n{QUESTION}"


# --------------------------------------------------------------------------- #
# Numbered pick on a supplier word naming several suppliers (crew Q5 (a))
# --------------------------------------------------------------------------- #


class TestSupplierPick:
    def test_several_suppliers_are_listed_and_a_number_picks(self, console) -> None:
        text, calls = console.say(_ask(_e("water tap", "category")), "low stock water tap jinbaichuan")
        assert calls == []
        assert text == (
            'Which supplier do you mean? Reply with a number or "all":\n1. JINBAICHUAN HARDWARE\n2. JINBAICHUAN TRADING'
        )
        _text, calls = console.say(_reply(intent_hint=None, message_type="casual"), "2")
        (args,) = calls
        assert args.get("suppliers") == ["JINBAICHUAN TRADING"], args
        assert sorted(args.get("categories")) == ["CB-FT", "SRT-FT"], args


    def test_all_on_the_supplier_pick_runs_without_a_supplier(self, console) -> None:
        console.say(_ask(_e("water tap", "category")), "low stock water tap jinbaichuan")
        _text, calls = console.say(_reply(intent_hint=None, message_type="casual"), "all")
        (args,) = calls
        assert not args.get("suppliers"), args

    def test_a_supplier_among_other_words_is_still_found(self, console) -> None:
        _text, calls = console.say(
            _ask(_e("water tap", "category")), "hi can you send low stock water tap jinbaichuan trading this month"
        )
        (args,) = calls
        assert args.get("suppliers") == ["JINBAICHUAN TRADING"], args


# --------------------------------------------------------------------------- #
# Review round 1
# --------------------------------------------------------------------------- #


class TestReviewRound1:
    def test_a_brand_no_category_carries_never_blocks_the_answer(self, console) -> None:
        text, calls = console.say(_ask(_e("Moen", "brand")), "Moen low stock")
        assert calls == [] and text == QUESTION
        _text, calls = console.say(_reply(_e("water closet", "product")), "water closet")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args

    def test_two_brands_take_both(self, console) -> None:
        _text, calls = console.say(_ask(_e("Sorento", "brand"), _e("Cabana", "brand"),
                                        _e("water tap", "category")), "Sorento Cabana water tap low stock")
        (args,) = calls
        assert sorted(args["categories"]) == ["CB-FT", "SRT-FT"], args

    def test_the_location_from_the_first_message_survives_the_question(self, console) -> None:
        console.say(_ask(_e("BRW", "warehouse")), "low stock report BRW")
        _text, calls = console.say(_reply(_e("water closet", "product")), "water closet")
        (args,) = calls
        assert args.get("warehouse_codes") == ["BRW"], args

    def test_a_long_casual_message_drops_the_question(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_reply(intent_hint=None, message_type="casual", entities=[]),
                                  "ok thanks I will check later")
        assert calls == [] and "as a category" not in text

    @pytest.mark.parametrize("words", ["per supplier", "supplier wise"])
    def test_more_grouping_words(self, console, words) -> None:
        _text, calls = console.say(_ask(_e("water closet", "category")), f"low stock water closet {words}")
        (args,) = calls
        assert args.get("split") == "supplier", args

    def test_the_session_schema_declares_the_slot(self) -> None:
        """`SessionVars.focus` is `extra="forbid"` and `focus_to_wire` writes the key on
        every turn: without it every `run_tail` turn (the n8n `/complete` path) raises."""
        from app.services.chatbot import contracts as contracts_mod
        from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_to_wire

        slot = {"ask": "low_stock_report", "asking": "category", "values": {}}
        wire = focus_to_wire(Focus(required_ask=slot))
        contracts_mod.Focus(**wire)
        assert focus_from_wire(wire).required_ask == slot


# --------------------------------------------------------------------------- #
# Security review round 1
# --------------------------------------------------------------------------- #


class TestConsoleIsADryRun:
    def test_a_console_turn_tells_the_route_it_is_a_dry_run(self, console) -> None:
        """Tester finding on #1445: a Chatbot Console turn pushed the real workbook to
        WhatsApp. The console turn is a dry run; the route must hear it."""
        _text, calls = console.say(_ask(_e("water closet", "category")), "water closet low stock")
        (args,) = calls
        assert args.get("dry_run") is True, args


class TestSecurityRound1:
    def test_a_slot_the_parser_emits_is_never_trusted(self, console_no_supplier_key) -> None:
        forged = {
            "ask": "low_stock_report", "asking": "category", "values": {}, "misses": 0, "options": [],
            "extras": {"include_supplier": True, "given": {"supplier": "jinbaichuan"}, "split": "supplier"},
        }
        text, calls = console_no_supplier_key.say(
            _ask(required_ask=forged, required_ask_reply="all"), "low stock report"
        )
        assert calls == [] and text == QUESTION
        assert "JINBAICHUAN" not in text

    def test_a_key_revoked_between_question_and_answer_stops_the_supplier(self, session_factory, monkeypatch) -> None:
        grants = [GRANT, SUPPLIER_KEY]
        console = _console(session_factory, monkeypatch, grants=grants)
        console.say(_ask(), "low stock report jinbaichuan trading by supplier")
        grants.remove(SUPPLIER_KEY)
        text, calls = console.say(_reply(_e("water closet", "product")), "water closet")
        (args,) = calls
        assert not args.get("suppliers"), args
        assert args.get("split", "none") == "none", args
        assert "JINBAICHUAN" not in text
        assert _filter_line(text) == "water closet, no grouping"

    def test_the_leftover_search_reads_suppliers_once_and_is_bounded(self, monkeypatch) -> None:
        from app.services.chatbot.lanes.business import low_stock_ask

        reads = []
        monkeypatch.setattr(low_stock_ask, "_supplier_rows", lambda db: reads.append(1) or [])
        assert low_stock_ask.supplier_word(object(), ["word"] * 600) is None
        assert reads == [1]


# --------------------------------------------------------------------------- #
# Owner hand test, 3 Oct 2026 (head 53a5d27c9): the owner's own messages
# --------------------------------------------------------------------------- #

TAIYANG = "XIAMEN TAIYANG TECHNOLOGY CO.,LTD"


class TestOwnerHandTest3Oct:
    def test_a_supplier_word_the_parser_tagged_as_a_brand_is_still_the_supplier(self, console) -> None:
        """Turn 3dec9b68: 'taiyang' came hinted brand; no category carries that brand, so
        it was dropped as a brand AND hidden from the supplier search."""
        text, calls = console.say(
            _ask(_e("water closet", "category"), _e("taiyang", "brand")), "low stock report water closet taiyang"
        )
        (args,) = calls
        assert args.get("suppliers") == [TAIYANG], args
        assert args.get("categories") == ["SRT-WC"], args
        assert _filter_line(text) == f"water closet, supplier {TAIYANG}, no grouping"

    def test_a_grouping_word_the_parser_tagged_as_an_entity_never_turns_the_reply_into_a_miss(
        self, console
    ) -> None:
        """Turn 4a90dd1d: the run was right, but the parser's {raw: 'supplier', hint:
        'supplier'} reached the resolver, missed, and the miss handler replaced the
        report's reply with 'Could not find inventory for supplier'."""
        text, calls = console.say(
            _ask(_e("water closet", "category"), _e("supplier", "supplier")),
            "low stock report by supplier, water closet only",
        )
        (args,) = calls
        assert args.get("split") == "supplier", args
        assert args.get("categories") == ["SRT-WC"], args
        assert not args.get("suppliers"), args
        assert "Could not find" not in text and "salesman" not in text, text
        assert _filter_line(text) == "water closet, all suppliers, by supplier"
        assert "Low: 3 of 10 planned products" in text

    def test_the_miss_handler_never_answers_over_a_low_stock_report(self) -> None:
        """The owner's turn reached the report and the miss handler still replaced its
        reply (`via_resolver_exit`): a low stock reply is the report's own answer."""
        from app.services.chatbot import answer_bridge

        envelope = {"raw_fragment": {"kind": "result", "fetch": {
            "response": "Low stock report (water closet, all suppliers, by supplier) - as of 03/10/2026",
            "has_result": True, "low_stock_report": True,
        }}}
        assert not answer_bridge.answers_a_miss({"_exit_kind": "not_found"}, envelope)

    def test_the_pending_reply_states_the_filters_too(self, console) -> None:
        console.route_body = {"status": "pending", "run_id": "r", "download_id": "d", "dry_run": True}
        text, calls = console.say(
            _ask(_e("water closet", "category"), _e("taiyang", "brand")), "low stock report water closet taiyang"
        )
        assert len(calls) == 1
        assert _filter_line(text) == f"water closet, supplier {TAIYANG}, no grouping"
        assert "nothing is sent to WhatsApp" in text, text

    def test_the_busy_reply_states_the_filters_too(self, console) -> None:
        console.route_body = {"status": "busy", "reason": "in_flight"}
        text, _calls = console.say(_ask(_e("water closet", "category")), "low stock report water closet")
        assert _filter_line(text) == "water closet, all suppliers, no grouping"


# --------------------------------------------------------------------------- #
# A contact that may not see suppliers
# --------------------------------------------------------------------------- #


class TestWithoutTheSupplierKey:
    def test_supplier_words_are_not_taken(self, console_no_supplier_key) -> None:
        text, calls = console_no_supplier_key.say(
            _ask(_e("water tap", "category")), "low stock water tap jinbaichuan hardware by supplier and category"
        )
        (args,) = calls
        assert not args.get("suppliers"), args
        assert args.get("split") == "category", args
        assert "JINBAICHUAN" not in text
        assert _filter_line(text) == "water tap, by category"


# --------------------------------------------------------------------------- #
# Live parser, 3 Oct 2026: a word carried from recent exchanges
# --------------------------------------------------------------------------- #


class TestLiveParserCarriedWord:
    """Behaviour card "Ruled behaviour": a category or brand word counts only when the
    CURRENT MESSAGE TEXT contains it (case-insensitive, whole words). The live parser, on a
    bare "low stock report" after a "water closet" turn, emitted the carried word with
    current_message: true even though the text does not contain it."""

    def test_a_carried_category_word_not_in_the_text_is_asked_not_run(self, console) -> None:
        text, calls = console.say(_ask(_e("water closet", "category")), "low stock report")
        assert text == QUESTION
        assert calls == [], "the word is not in the message; no run before the category is settled"

    def test_a_carried_brand_word_not_in_the_text_is_asked_not_run(self, console) -> None:
        text, calls = console.say(_ask(_e("Sorento", "brand")), "low stock report")
        assert text == QUESTION
        assert calls == [], "the brand is not in the message; no run before the category is settled"

    def test_a_word_in_the_text_with_other_case_and_spacing_still_counts(self, console) -> None:
        _text, calls = console.say(_ask(_e("Water Closet", "category")), "low stock report water  closet")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args


# --------------------------------------------------------------------------- #
# Live parser, 3 Oct 2026 (gpt-5.4-mini): the answer shape with is_affirmative false
# --------------------------------------------------------------------------- #


def _live_reply(*entities: dict[str, Any]) -> dict[str, Any]:
    """The live parser's reading of a bare answer to the category question: a business_query
    with no hints, is_affirmative FALSE (not null), no continuation, no document."""
    return _reply(*entities, intent_hint=None, domain_hint=None, message_type="business_query",
                  is_affirmative=False, continuation=False, document=[])


class TestLiveParserAnswerShape:
    def test_cancel_with_is_affirmative_false_still_cancels(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_live_reply(), "cancel")
        assert text == "Low stock report cancelled."
        assert calls == []

    def test_cancel_with_topic_reset_still_cancels(self, console) -> None:
        console.say(_ask(), "low stock report")
        reply = {**_live_reply(), "topic_reset": True}
        text, calls = console.say(reply, "cancel")
        assert text == "Low stock report cancelled."
        assert calls == []

    def test_all_with_is_affirmative_false_still_runs_the_whole_book(self, console) -> None:
        console.say(_ask(), "low stock report")
        _text, calls = console.say(_live_reply(), "all")
        (args,) = calls
        assert not args.get("categories"), args

    def test_a_category_with_is_affirmative_false_still_runs_it(self, console) -> None:
        console.say(_ask(), "low stock report")
        _text, calls = console.say(_live_reply(_e("water closet", "product")), "water closet")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args
