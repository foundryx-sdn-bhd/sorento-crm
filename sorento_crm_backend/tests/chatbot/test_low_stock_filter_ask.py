"""LOWSTOCK-SEMANTIC (and LOWSTOCK-FILTER-ASK before it): a chat low stock ask settles its
product category before any run, from the PARSER's reading alone.

`documentation/plans/chatbot/lowstock-semantic-behaviour-card.md`. Owner, 4 Oct 2026:
"remove the rules entirely, this is hard coded". The parser's `low_stock` key says which
words are categories, brands and suppliers and how the report is grouped; the lane only
resolves them against the master data:

* the product category is the ONE required field: the parser's category words, narrowed by
  its brands, or a brand alone, or `all_categories`; else asked, and nothing runs until it
  is settled;
* a supplier name that resolves to no supplier is said and asked once; a second miss runs
  with no supplier filter and says so (crew ruling Q3);
* a grouping the workbook cannot split by (brand, warehouse) is asked (crew ruling Q2);
* the open question captures only a reply the parser reads as answering it (crew ruling Q4);
* the settled filters persist, so a refinement narrows the report just shown.

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


def _ls(categories=(), brands=(), suppliers=(), group_by=None, all_categories=None) -> dict[str, Any]:
    """The parser's `low_stock` key (LOW_STOCK_FILTERS_ADDENDUM)."""
    return {"group_by": group_by, "categories": list(categories), "all_categories": all_categories,
            "brands": list(brands), "suppliers": list(suppliers)}


def _ask(*entities: dict[str, Any], low_stock: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """The parser's reading of a fresh low stock ask: the ask names itself."""
    base = dict(domain_hint="inventory", intent_hint="low_stock_report", entities=list(entities),
                low_stock=low_stock or _ls(), domain_in_message=True)
    base.update(overrides)
    return _parser_output(**base)


def _refine(**ls: Any) -> dict[str, Any]:
    """A refinement of the report just shown ("taiyang only"): the same ask, no ask word."""
    return _ask(low_stock=_ls(**ls), domain_in_message=False)


def _answer(mode: str | None, *picked: int, intent: str | None = None, **ls: Any) -> dict[str, Any]:
    """The parser's reading of a reply to the open low stock question."""
    return _parser_output(
        message_type="business_query", domain_hint="inventory" if intent else None, intent_hint=intent,
        entities=[], low_stock=_ls(**ls),
        open_question_answer={"mode": mode, "picked": list(picked), "items": [], "qty_for_all": None},
    )


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
# Taken from the parser's reading: no question
# --------------------------------------------------------------------------- #


class TestReadByTheParser:
    def test_the_owner_case_runs_at_once_scoped_to_the_brand_and_category(self, console) -> None:
        """Owner, 4 Oct 2026: "low stock report for sorento water tap"."""
        text, calls = console.say(
            _ask(low_stock=_ls(categories=["water tap"], brands=["sorento"])), "low stock report for sorento water tap"
        )
        (args,) = calls
        assert args.get("categories") == ["SRT-FT"], args
        assert not args.get("suppliers"), args
        assert args.get("split", "none") == "none", args
        assert QUESTION not in text
        assert _filter_line(text) == "sorento water tap, all suppliers, no grouping"
        assert "Low: 3 of 10 planned products" in text

    def test_a_category_alone_takes_every_brand(self, console) -> None:
        _text, calls = console.say(_ask(low_stock=_ls(categories=["water tap"])), "water tap low stock")
        (args,) = calls
        assert sorted(args["categories"]) == ["CB-FT", "SRT-FT"], args

    def test_a_brand_alone_settles_the_category_as_that_brand(self, console) -> None:
        _text, calls = console.say(_ask(low_stock=_ls(brands=["Sorento"])), "Sorento low stock")
        (args,) = calls
        assert sorted(args["categories"]) == ["SRT-FT", "SRT-WC"], args

    def test_all_categories_runs_the_whole_book(self, console) -> None:
        text, calls = console.say(_ask(low_stock=_ls(all_categories=True)), "low stock report semua kategori")
        (args,) = calls
        assert not args.get("categories"), args
        assert _filter_line(text) == "all categories, all suppliers, no grouping"

    def test_a_supplier_and_a_grouping_are_taken(self, console) -> None:
        text, calls = console.say(
            _ask(low_stock=_ls(categories=["water tap"], suppliers=["jinbaichuan hardware"], group_by="supplier")),
            "low stock water tap from jinbaichuan hardware, per vendor",
        )
        (args,) = calls
        assert args.get("suppliers") == ["JINBAICHUAN HARDWARE"], args
        assert args.get("split") == "supplier", args
        assert _filter_line(text) == "water tap, supplier JINBAICHUAN HARDWARE, by supplier"

    @pytest.mark.parametrize("group_by,split", [
        ("supplier", "supplier"), ("category", "category"), ("supplier_category", "supplier_category"),
        ("none", "none"),
    ])
    def test_every_grouping_the_workbook_has(self, console, group_by, split) -> None:
        _text, calls = console.say(_ask(low_stock=_ls(categories=["water closet"], group_by=group_by)), "x")
        (args,) = calls
        assert args.get("split", "none") == split, args

    def test_two_categories_take_both(self, console) -> None:
        _text, calls = console.say(_ask(low_stock=_ls(categories=["water tap", "water closet"], brands=["sorento"])),
                                   "low stock sorento tap and toilet")
        (args,) = calls
        assert sorted(args["categories"]) == ["SRT-FT", "SRT-WC"], args

    def test_a_product_code_ask_names_its_own_scope(self, console) -> None:
        _text, calls = console.say(_ask(_e("SRTWT7408", "product")), "low stock for SRTWT7408")
        (args,) = calls
        assert args.get("product_codes") == ["SRTWT7408"], args
        assert not args.get("categories"), "a product-code ask is scoped by its code; no category"

    def test_a_word_placed_in_low_stock_and_as_a_product_is_the_category(self, console) -> None:
        """Two readings of one word: the structured field wins, the entity goes."""
        _text, calls = console.say(
            _ask(_e("water tap", "product"), low_stock=_ls(categories=["water tap"])), "water tap low stock"
        )
        (args,) = calls
        assert sorted(args["categories"]) == ["CB-FT", "SRT-FT"], args
        assert not args.get("product_codes"), args

    def test_a_translated_category_is_taken_though_the_text_never_says_it(self, console) -> None:
        """Kill test for the old `_in_text` rule: "水龙头" is read as "water tap" by the
        parser; the message text never contains "water tap", and that is fine."""
        _text, calls = console.say(_ask(low_stock=_ls(categories=["water tap"], brands=["sorento"])),
                                   "sorento 水龙头 低库存")
        (args,) = calls
        assert args.get("categories") == ["SRT-FT"], args


# --------------------------------------------------------------------------- #
# Kill tests: the hard-coded rules are gone
# --------------------------------------------------------------------------- #


class TestTheRulesAreGone:
    def test_grouping_and_supplier_words_beside_a_parsed_category_are_never_read(self, console) -> None:
        """Reviewer kill test: the category IS settled (parser), so a re-added grouping
        regex or leftover-word supplier search would change this run."""
        _text, calls = console.say(_ask(low_stock=_ls(categories=["water closet"])),
                                   "low stock water closet jinbaichuan trading by supplier per category")
        (args,) = calls
        assert not args.get("suppliers"), args
        assert args.get("split", "none") == "none", args

    def test_a_product_type_the_parser_left_a_product_is_never_relabelled(self, console) -> None:
        """Reviewer kill test for the digit-shape rule: "water tap" resolves to seeded
        categories, so a relabel would run a category-scoped report."""
        _text, calls = console.say(_ask(_e("water tap", "product")), "low stock water tap")
        assert not any(c.get("categories") for c in calls), calls

    def test_grouping_and_supplier_words_in_the_text_alone_are_never_read(self, console) -> None:
        """The message names a category, a supplier and a grouping, but the parser placed
        nothing: no regex and no leftover-word search reads them, the category is asked."""
        text, calls = console.say(_ask(), "low stock water closet jinbaichuan trading by supplier all categories")
        assert calls == [] and text == QUESTION

    def test_a_product_without_digits_is_never_turned_into_a_category(self, console) -> None:
        """The old digit-shape rule re-labelled {raw: "water tap", hint: "product"} as a
        category. Now nothing reads its shape: it is a product, as the parser said."""
        _text, calls = console.say(_ask(_e("Lucia basin mixer", "product")), "low stock Lucia basin mixer")
        assert not any(c.get("categories") for c in calls), calls

    def test_a_category_with_digits_is_still_a_category(self, console) -> None:
        _text, calls = console.say(_ask(low_stock=_ls(categories=["SRT-FT"])), "low stock SRT-FT")
        (args,) = calls
        assert args.get("categories") == ["SRT-FT"], args

    @pytest.mark.parametrize("name", [
        "split_from", "says_all_categories", "leftover_words", "supplier_word", "take_words", "_in_text",
        "_SPLIT_PATTERNS", "_ALL_CATEGORIES", "_ASK_WORDS", "MAX_LEFTOVER_WORDS",
    ])
    def test_the_rule_is_not_in_the_module(self, name) -> None:
        from app.services.chatbot.lanes.business import low_stock_ask

        assert not hasattr(low_stock_ask, name), name

    def test_the_module_reads_no_text(self) -> None:
        import inspect

        from app.services.chatbot.lanes.business import low_stock_ask

        source = inspect.getsource(low_stock_ask)
        assert not re.search(r"^import re$", source, re.M) and "re.compile" not in source and "isdigit" not in source
        assert "low_stock_text" not in source


# --------------------------------------------------------------------------- #
# Asked: nothing runs until the category is settled
# --------------------------------------------------------------------------- #


class TestAsked:
    def test_a_bare_ask_asks_the_category_and_runs_nothing(self, console) -> None:
        text, calls = console.say(_ask(), "low stock report")
        assert text == QUESTION
        assert calls == [], "no reorder run before the category is settled"

    def test_the_parsers_answer_settles_it_and_runs(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_answer("fill", categories=["water closet"]), "tandas")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args
        assert _filter_line(text) == "water closet, all suppliers, no grouping"

    def test_a_bare_answer_the_parser_placed_nothing_in_is_resolved_as_the_answer(self, console) -> None:
        console.say(_ask(), "low stock report")
        _text, calls = console.say(_answer("fill"), "water closet")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args

    def test_an_answer_naming_more_settles_every_field(self, console) -> None:
        console.say(_ask(), "low stock report")
        _text, calls = console.say(
            _answer("fill", intent="low_stock_report", categories=["water tap"], group_by="supplier"),
            "water tap by supplier",
        )
        (args,) = calls
        assert sorted(args["categories"]) == ["CB-FT", "SRT-FT"] and args.get("split") == "supplier", args

    def test_all_runs_the_whole_book(self, console) -> None:
        console.say(_ask(), "low stock report")
        _text, calls = console.say(_answer("all"), "semua")
        (args,) = calls
        assert not args.get("categories"), args

    def test_a_grouping_from_the_first_message_survives_the_question(self, console) -> None:
        console.say(_ask(low_stock=_ls(group_by="supplier")), "low stock report by supplier")
        _text, calls = console.say(_answer("fill", categories=["water closet"]), "water closet")
        (args,) = calls
        assert args.get("split") == "supplier", args

    def test_an_unknown_answer_is_said_and_asked_again(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_answer("fill", categories=["spaceship"]), "spaceship")
        assert calls == []
        assert text == f"I don't know 'spaceship' as a category.\n\n{QUESTION}"

    def test_two_misses_end_the_ask_and_free_the_next_message(self, console) -> None:
        console.say(_ask(), "low stock report")
        console.say(_answer("fill", categories=["spaceship"]), "spaceship")
        text, calls = console.say(_answer("fill", categories=["rocket"]), "rocket")
        assert calls == []
        assert text == (
            "I still can't place 'rocket'. Ask for the low stock report again with a category or \"all\"."
        )
        _text, calls = console.say(_answer("all"), "all")
        assert calls == [], "the ask ended; a later 'all' must not start a run"

    def test_cancel_declared_by_the_parser(self, console) -> None:
        console.say(_ask(), "low stock report")
        text, calls = console.say(_answer("cancel"), "tak payah lah")
        assert calls == [] and text == "Low stock report cancelled."

    def test_a_new_question_wins(self, console) -> None:
        """Crew ruling Q4 / STUCK-QTY-LOOP: the open question captures only an answer."""
        console.say(_ask(), "low stock report")
        text, calls = console.say(_stock_ask(), "CB100 BRW")
        assert calls == []
        assert QUESTION not in text and "as a category" not in text
        _text, calls = console.say(_answer("all"), "all")
        assert calls == [], "the question was dropped; 'all' must not start a run"

    def test_a_new_low_stock_ask_over_the_supplier_question_runs_its_own_words(self, console) -> None:
        """Reviewer must-fix 2 (reproduced): it ran the OLD category and quoted the whole
        message as a supplier."""
        console.say(_ask(low_stock=_ls(categories=["water tap"], suppliers=["acme"])), "low stock water tap from acme")
        text, calls = console.say(
            _ask(low_stock=_ls(categories=["water closet"], group_by="category"),
                 open_question_answer={"mode": None, "picked": [], "items": [], "qty_for_all": None}),
            "low stock report water closet by category",
        )
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"] and args.get("split") == "category", args
        assert _filter_line(text) == "water closet, all suppliers, by category"

    def test_clear_resets(self, console) -> None:
        console.say(_ask(), "low stock report")
        reset = _parser_output(message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                               topic_reset=True)
        text, calls = console.say(reset, "clear")
        assert calls == [] and "as a category" not in text
        _text, calls = console.say(_answer("all"), "all")
        assert calls == []

    def test_a_category_unknown_in_the_first_message_is_said_and_asked(self, console) -> None:
        text, calls = console.say(_ask(low_stock=_ls(categories=["spaceship"])), "spaceship low stock")
        assert calls == []
        assert text == f"I don't know 'spaceship' as a category.\n\n{QUESTION}"


# --------------------------------------------------------------------------- #
# Supplier: numbered pick, and an unknown name said and asked once (crew Q3)
# --------------------------------------------------------------------------- #

TAIYANG = "XIAMEN TAIYANG TECHNOLOGY CO.,LTD"
SUPPLIER_QUESTION = 'Which supplier? Reply with a supplier name or "all".'


class TestSupplier:
    def test_several_suppliers_are_listed_and_a_pick_takes_one(self, console) -> None:
        text, calls = console.say(_ask(low_stock=_ls(categories=["water tap"], suppliers=["jinbaichuan"])),
                                  "low stock water tap jinbaichuan")
        assert calls == []
        assert text == (
            'Which supplier do you mean? Reply with a number or "all":\n1. JINBAICHUAN HARDWARE\n2. JINBAICHUAN TRADING'
        )
        _text, calls = console.say(_answer("pick", 2), "the second one")
        (args,) = calls
        assert args.get("suppliers") == ["JINBAICHUAN TRADING"], args
        assert sorted(args.get("categories")) == ["CB-FT", "SRT-FT"], args

    def test_a_typed_number_still_picks(self, console) -> None:
        console.say(_ask(low_stock=_ls(categories=["water tap"], suppliers=["jinbaichuan"])), "x")
        _text, calls = console.say(_answer("fill"), "1")
        (args,) = calls
        assert args.get("suppliers") == ["JINBAICHUAN HARDWARE"], args

    def test_all_on_the_supplier_pick_runs_without_a_supplier(self, console) -> None:
        console.say(_ask(low_stock=_ls(categories=["water tap"], suppliers=["jinbaichuan"])), "x")
        _text, calls = console.say(_answer("all"), "all")
        (args,) = calls
        assert not args.get("suppliers"), args

    def test_the_owner_taiyang_case(self, console) -> None:
        """Turn 3dec9b68: "low stock report water closet taiyang"; the new prompt places
        "taiyang" as a supplier. Two TAIYANG codes carry one name: one supplier."""
        text, calls = console.say(
            _ask(low_stock=_ls(categories=["water closet"], suppliers=["taiyang"])),
            "low stock report water closet taiyang",
        )
        (args,) = calls
        assert args.get("suppliers") == [TAIYANG], args
        assert args.get("categories") == ["SRT-WC"], args
        assert _filter_line(text) == f"water closet, supplier {TAIYANG}, no grouping"

    def test_an_unknown_supplier_is_said_and_asked(self, console) -> None:
        text, calls = console.say(_ask(low_stock=_ls(categories=["water tap"], suppliers=["acme"])),
                                  "low stock water tap from acme")
        assert calls == []
        assert text == f"I don't know 'acme' as a supplier.\n\n{SUPPLIER_QUESTION}"
        _text, calls = console.say(_answer("fill", suppliers=["taiyang"]), "taiyang")
        (args,) = calls
        assert args.get("suppliers") == [TAIYANG], args

    def test_a_second_miss_runs_without_a_supplier_and_says_so(self, console) -> None:
        console.say(_ask(low_stock=_ls(categories=["water tap"], suppliers=["acme"])), "x")
        text, calls = console.say(_answer("fill", suppliers=["bolt"]), "bolt")
        (args,) = calls
        assert not args.get("suppliers"), args
        assert _filter_line(text) == "water tap, all suppliers, no supplier 'bolt' found, no grouping"


# --------------------------------------------------------------------------- #
# Grouping the workbook cannot split by (crew Q2)
# --------------------------------------------------------------------------- #

GROUPING_PICK = (
    "I can group the low stock report by supplier, by category, or both. Which one? Reply with a number:\n"
    "1. Supplier\n2. Category\n3. Supplier x category\n4. No grouping"
)


class TestUnsupportedGrouping:
    @pytest.mark.parametrize("group_by", ["brand", "warehouse"])
    def test_is_asked_and_runs_nothing(self, console, group_by) -> None:
        text, calls = console.say(_ask(low_stock=_ls(categories=["water tap"], group_by=group_by)), "split by brand")
        assert calls == [] and text == GROUPING_PICK

    def test_the_pick_runs_it(self, console) -> None:
        console.say(_ask(low_stock=_ls(categories=["water tap"], group_by="brand")), "split by brand")
        _text, calls = console.say(_answer("pick", 2), "category")
        (args,) = calls
        assert args.get("split") == "category", args

    def test_the_parsers_grouping_answers_it(self, console) -> None:
        console.say(_ask(low_stock=_ls(categories=["water tap"], group_by="brand")), "split by brand")
        _text, calls = console.say(_answer("fill", group_by="supplier"), "ikut pembekal")
        (args,) = calls
        assert args.get("split") == "supplier", args

    def test_a_supplier_pick_after_the_key_was_revoked_runs_downgraded(self, session_factory, monkeypatch) -> None:
        """Reviewer should-fix 3: the pick was offered with the key held."""
        grants = [GRANT, SUPPLIER_KEY]
        console = _console(session_factory, monkeypatch, grants=grants)
        console.say(_ask(low_stock=_ls(categories=["water tap"], group_by="brand")), "split by brand")
        grants.remove(SUPPLIER_KEY)
        _text, calls = console.say(_answer("pick", 1), "1")
        (args,) = calls
        assert args.get("split", "none") == "none", args

    def test_without_the_supplier_key_only_category_and_none_are_offered(self, console_no_supplier_key) -> None:
        text, _calls = console_no_supplier_key.say(
            _ask(low_stock=_ls(categories=["water tap"], group_by="brand")), "split by brand"
        )
        assert text.endswith("1. Category\n2. No grouping"), text


# --------------------------------------------------------------------------- #
# The settled filters persist: a refinement narrows the report (crew root cause, 4 Oct)
# --------------------------------------------------------------------------- #


class TestRefinement:
    def _report(self, console) -> None:
        console.say(_ask(low_stock=_ls(categories=["water closet"], brands=["sorento"])),
                    "low stock sorento water closet")

    def test_a_supplier_only_narrows_the_report_just_shown(self, console) -> None:
        """Crew root cause (4 Oct 2026): "taiyang only" lost the category and re-asked."""
        self._report(console)
        text, calls = console.say(_refine(suppliers=["taiyang"]), "taiyang only")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args
        assert args.get("suppliers") == [TAIYANG], args
        assert _filter_line(text) == f"sorento water closet, supplier {TAIYANG}, no grouping"

    def test_a_grouping_only_keeps_the_category(self, console) -> None:
        self._report(console)
        _text, calls = console.say(_refine(group_by="supplier"), "by supplier")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"] and args.get("split") == "supplier", args

    def test_a_brand_only_narrows_the_categories(self, console) -> None:
        console.say(_ask(low_stock=_ls(categories=["water tap"])), "low stock water tap")
        text, calls = console.say(_refine(brands=["cabana"]), "cabana only")
        (args,) = calls
        assert args.get("categories") == ["CB-FT"], args
        assert _filter_line(text).startswith("cabana water tap"), text

    def test_a_refinement_keeps_the_supplier_and_takes_a_new_grouping(self, console) -> None:
        self._report(console)
        console.say(_refine(suppliers=["taiyang"]), "taiyang only")
        _text, calls = console.say(_refine(group_by="category"), "per category")
        (args,) = calls
        assert args.get("suppliers") == [TAIYANG] and args.get("split") == "category", args

    def test_the_frame_dies_with_a_turn_that_ran_no_report(self, console) -> None:
        """Reviewer should-fix 2: a turn that is not the filtered report clears it."""
        self._report(console)
        console.say(_stock_ask(), "CB100 BRW")
        text, calls = console.say(_refine(suppliers=["taiyang"]), "taiyang only")
        # (A carried "Couldn't find CB100." may trail the question: STUCK-QTY-LOOP's
        # stale-entity fix, not this lane's.)
        assert calls == [] and text.startswith(QUESTION)

    def test_a_new_ask_never_inherits(self, console) -> None:
        self._report(console)
        text, calls = console.say(_ask(), "low stock report")
        assert calls == [] and text == QUESTION

    def test_a_category_answered_through_the_question_persists_too(self, console) -> None:
        console.say(_ask(), "low stock report")
        console.say(_answer("fill", categories=["water closet"]), "water closet")
        _text, calls = console.say(_refine(group_by="supplier"), "by supplier")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"] and args.get("split") == "supplier", args


# --------------------------------------------------------------------------- #
# Kept from #1445: brands, location, schema, dry run, security, owner hand test
# --------------------------------------------------------------------------- #


class TestKept:
    def test_a_brand_no_category_carries_never_blocks_the_answer(self, console) -> None:
        text, calls = console.say(_ask(low_stock=_ls(brands=["Moen"])), "Moen low stock")
        assert calls == [] and text == QUESTION
        _text, calls = console.say(_answer("fill", categories=["water closet"]), "water closet")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args

    def test_the_location_from_the_first_message_survives_the_question(self, console) -> None:
        console.say(_ask(_e("BRW", "warehouse")), "low stock report BRW")
        _text, calls = console.say(_answer("fill", categories=["water closet"]), "water closet")
        (args,) = calls
        assert args.get("warehouse_codes") == ["BRW"], args

    def test_the_session_schema_declares_the_slot_and_the_frame(self) -> None:
        """`SessionVars.focus` is `extra="forbid"` and `focus_to_wire` writes the keys on
        every turn: without them every `run_tail` turn (the n8n `/complete` path) raises."""
        from app.services.chatbot import contracts as contracts_mod
        from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_to_wire

        slot = {"ask": "low_stock_report", "asking": "category", "values": {}}
        frame = {"category": {"value": ["SRT-WC"], "label": "water closet"}, "grouping": "none"}
        wire = focus_to_wire(Focus(required_ask=slot, low_stock=frame))
        contracts_mod.Focus(**wire)
        back = focus_from_wire(wire)
        assert back.required_ask == slot and back.low_stock == frame

    def test_a_console_turn_tells_the_route_it_is_a_dry_run(self, console) -> None:
        """Tester finding on #1445: a Chatbot Console turn pushed the real workbook to
        WhatsApp. The console turn is a dry run; the route must hear it."""
        _text, calls = console.say(_ask(low_stock=_ls(categories=["water closet"])), "water closet low stock")
        (args,) = calls
        assert args.get("dry_run") is True, args

    def test_a_slot_or_frame_the_parser_emits_is_never_trusted(self, console_no_supplier_key) -> None:
        forged = {
            "ask": "low_stock_report", "asking": "category", "values": {}, "misses": 0, "options": [],
            "extras": {"include_supplier": True, "carry": {"suppliers": ["jinbaichuan"], "group_by": "supplier"}},
        }
        frame = {"category": {"value": ["SRT-WC"], "label": "x"}, "supplier": {"value": ["JINBAICHUAN"]}}
        text, calls = console_no_supplier_key.say(
            _ask(required_ask=forged, required_ask_reply="all", low_stock_frame=frame, domain_in_message=False),
            "low stock report",
        )
        assert calls == [] and text == QUESTION
        assert "JINBAICHUAN" not in text

    def test_a_key_revoked_between_question_and_answer_stops_the_supplier(self, session_factory, monkeypatch) -> None:
        grants = [GRANT, SUPPLIER_KEY]
        console = _console(session_factory, monkeypatch, grants=grants)
        console.say(_ask(low_stock=_ls(suppliers=["jinbaichuan trading"], group_by="supplier")), "x")
        grants.remove(SUPPLIER_KEY)
        text, calls = console.say(_answer("fill", categories=["water closet"]), "water closet")
        (args,) = calls
        assert not args.get("suppliers"), args
        assert args.get("split", "none") == "none", args
        assert "JINBAICHUAN" not in text
        assert _filter_line(text) == "water closet, no grouping"

    def test_the_parsers_lists_are_bounded(self) -> None:
        from app.services.chatbot.lanes.business import low_stock_ask

        read = low_stock_ask.reading({"low_stock": _ls(suppliers=[f"s{i}" for i in range(600)])})
        assert len(read["suppliers"]) == low_stock_ask.MAX_WORDS

    def test_a_grouping_the_parser_never_declared_is_dropped(self) -> None:
        from app.services.chatbot.lanes.business import low_stock_ask

        assert low_stock_ask.reading({"low_stock": {"group_by": "colour"}})["group_by"] is None

    def test_the_miss_handler_never_answers_over_a_low_stock_report(self) -> None:
        from app.services.chatbot import answer_bridge

        envelope = {"raw_fragment": {"kind": "result", "fetch": {
            "response": "Low stock report (water closet, all suppliers, by supplier) - as of 03/10/2026",
            "has_result": True, "low_stock_report": True,
        }}}
        assert not answer_bridge.answers_a_miss({"_exit_kind": "not_found"}, envelope)

    def test_the_pending_reply_states_the_filters_too(self, console) -> None:
        console.route_body = {"status": "pending", "run_id": "r", "download_id": "d", "dry_run": True}
        text, calls = console.say(
            _ask(low_stock=_ls(categories=["water closet"], suppliers=["taiyang"])), "low stock water closet taiyang"
        )
        assert len(calls) == 1
        assert _filter_line(text) == f"water closet, supplier {TAIYANG}, no grouping"
        assert "nothing is sent to WhatsApp" in text, text

    def test_the_busy_reply_states_the_filters_too(self, console) -> None:
        console.route_body = {"status": "busy", "reason": "in_flight"}
        text, _calls = console.say(_ask(low_stock=_ls(categories=["water closet"])), "low stock water closet")
        assert _filter_line(text) == "water closet, all suppliers, no grouping"


class TestWithoutTheSupplierKey:
    def test_supplier_words_are_not_taken(self, console_no_supplier_key) -> None:
        text, calls = console_no_supplier_key.say(
            _ask(low_stock=_ls(categories=["water tap"], suppliers=["jinbaichuan hardware"],
                               group_by="supplier_category")),
            "x",
        )
        (args,) = calls
        assert not args.get("suppliers"), args
        assert args.get("split") == "category", args
        assert "JINBAICHUAN" not in text
        assert _filter_line(text) == "water tap, by category"


class TestTheOpenQuestion:
    def test_the_pending_low_stock_question_outranks_a_stale_stock_task(self) -> None:
        """STUCK-QTY-LOOP (4 Oct 2026): the low stock question is the newer one."""
        from app.services.chatbot.turn import question

        slot = {"ask": "low_stock_report", "asking": "category", "values": {}, "options": []}
        stale = [{"kind": "stock", "status": "asking", "slots": [{"code": "X", "qty": None}]}]
        obj = question.open_question(None, stale, slot)
        assert obj == {"kind": "free", "about": "low_stock_report", "question": QUESTION, "owed": ["category"]}

    def test_it_outranks_a_carried_roster_pick(self) -> None:
        """Reviewer should-fix 1: the slot lives one turn, so it is the newest question."""
        from app.services.chatbot.turn import question
        from app.services.chatbot.turn.pending import Pending

        slot = {"ask": "low_stock_report", "asking": "category", "values": {}, "options": []}
        roster = Pending(kind="customer_pick", expects="position", options=[{"position": 1, "code": "A", "label": "A"}],
                         team=None, payload={}, asked_at_turn=None)
        assert question.open_question(roster, [], slot)["about"] == "low_stock_report"

    def test_a_pick_is_stated_with_its_options(self) -> None:
        from app.services.chatbot.turn import question

        slot = {"ask": "low_stock_report", "asking": "supplier", "values": {},
                "options": [[["A"], "A CO"], [["B"], "B CO"]]}
        obj = question.open_question(None, [], slot)
        assert obj["kind"] == "pick_one" and obj["owed"] == ["supplier"]
        assert obj["options"] == [{"position": 1, "code": "A CO"}, {"position": 2, "code": "B CO"}]
