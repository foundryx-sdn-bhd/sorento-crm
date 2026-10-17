"""Phase 2 RED tests - REPORT-ENGINE slice 1b: the `sales_ranking` lane and its engine seam.

`documentation/plans/chatbot/PLAN-report-engine.md` section 11 (binding) and the UAC
refinements AC-RE-18a to 18d.

Full console turns, the way `test_low_stock_filter_ask.py` drives them: the REAL engine, lane and
MCP presenter; only the parser and the MCP transport are doubles. `Console.say` returns the reply
text and every `crm_report_ask` call that turn made, so "nothing runs before the period and the
number are settled" is pinned as "no `crm_report_ask` call on that turn".

Ambiguities the tester did NOT resolve by guessing (flagged to the captain):
* the exact reply after a MISS on `top_n` / `period` (the helper's own `I don't know '<word>' as a
  <noun>.` line has a noun section 11 does not name): only "no call and the question is asked
  again" is pinned for a first miss, and the exact give-up line for the second;
* a route refusal arrives from the MCP as the http error envelope (`status_code` 403, the route's
  own `message` / `code`); the reply must be that `message`.
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

TOOL = "crm_report_ask"
GRANT = "sales_orders.sales_report"
DENIAL = "Sales report is not enabled for your account."
PERIOD_Q = "Which period? For example this month, September, 2026, or 1 to 15 Sep."
TOPN_Q = "How many? For example top 5."
CANCELLED = "Sales ranking cancelled."
CATALOGUE_LINE = (
    "I can rank sales by customer, product, brand, category, sales agent, location, channel or month."
)
GIVE_UP = (
    "I still can't read '{word}'. Ask again in one message, "
    "e.g. top 5 sales agents for Sorento this month by amount, delivered."
)
DIMENSION_REFUSAL = "That breakdown is not available for your account."
_SNAKE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")
SEP = {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}
PRODUCT_CODE = "SRTWT7408"
CUSTOMER_NAME = "ZZT RANK CUSTOMER SDN BHD"


def _e(raw: str, hint: str) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}


def _rank(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The parser's reading of "top 3 salesman for Sorento brand last month" (section 11).

    Owner ruling 4 Oct 2026: a ranking never runs on a silent default, so this reading NAMES its
    measure and basis ("by amount, delivered"); a test of the clarify questions overrides both to
    None (`tests/chatbot/test_report_ask_clarify.py`)."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
        entities=list(entities), group_by="sales_agent", top_n=3,
        rank_direction="top", rank_by=None, basis="delivered", measure="amount", ranking_refine=False, **SEP,
    )
    base.update(overrides)
    return _parser_output(**base)


def _reply(**overrides: Any) -> dict[str, Any]:
    """The parser's reading of a short reply: whatever it makes of the bare words."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="master_products", intent_hint="check_product", entities=[],
    )
    base.update(overrides)
    return _parser_output(**base)


def _fake_body(args: dict[str, Any]) -> dict[str, Any]:
    """The route's own body (`ReportAskResponse`) for the lane's args."""
    group_by = args.get("group_by")
    rows = [{"rank": 1, "name": "AGENT A", "qty": 30, "amount": 1200.0}] if group_by else []
    return {
        "status": "ok", "message": None,
        "basis": args.get("basis", "delivered"),
        "basis_label": "ordered sales" if args.get("basis") == "ordered" else "delivered sales",
        "measure": args.get("measure", "amount"), "sort": args.get("sort", "desc"),
        "group_by": group_by, "group_label": (group_by or "").replace("_", " ").title() or None,
        "date_from": args.get("date_from"), "date_to": args.get("date_to"),
        "filters": [], "rows": rows, "more": 0, "total_count": len(rows),
        "total": {"qty": 30, "amount": 1200.0},
    }


class Console:
    def __init__(self, session_factory, monkeypatch) -> None:
        self.session_factory, self.monkeypatch = session_factory, monkeypatch
        self.session_vars: dict[str, Any] = {}
        self.refuse: dict[str, Any] | None = None

    def say(self, qf: dict[str, Any], body: str) -> tuple[str, list[dict[str, Any]]]:
        from app.services.chatbot import console_service
        from app.services.chatbot.head import parser as parser_mod

        self.monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
        captured: list[tuple[str, dict[str, Any]]] = []
        present = _present_response()

        def _mcp(name: str, args: dict[str, Any]) -> Any:
            captured.append((name, dict(args)))
            if name != TOOL:
                return json.dumps({"has_result": False, "items": []})
            if self.refuse is not None:
                from sorento_crm_mcp.http_client import http_error_body

                raw = http_error_body(
                    json.dumps(self.refuse), status_code=403,
                    path="/api/v1/order-management/report-ask", method="GET",
                )
                return present(name, raw)
            return present(name, json.dumps(_fake_body(args)))

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
                session_vars=copy.deepcopy(self.session_vars), run_id="zzt-report-ask-lane",
            )
        finally:
            db.close()
        self.session_vars = result.session_vars or {}
        text = result.reply_text or ""
        assert not _SNAKE.search(text), (body, text)
        assert chr(0x2014) not in text and chr(0x2013) not in text, body
        return text, [args for name, args in captured if name == TOOL]


def _seed(session_factory) -> dict[str, str]:
    from app.models.product import Brand, ProductCategory
    from app.models.sales_agent import SalesAgent
    from tests._mc_lookup_seed import customer, product, warehouse

    db = session_factory()
    try:
        ids: dict[str, str] = {}
        brand = Brand(id=str(uuid.uuid4()), brand_code="ZZTSOR", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
        agent = SalesAgent(id=str(uuid.uuid4()), sales_agent="AGENT A", company_id=DEFAULT_COMPANY_ID)
        category = ProductCategory(
            id=str(uuid.uuid4()), category_code="ZZTCAT", category_name="ZZT CATEGORY", company_id=DEFAULT_COMPANY_ID
        )
        db.add_all([brand, agent, category])
        db.flush()
        ids.update(brand=brand.id, agent=agent.id, category=category.id)
        ids["product"] = product(db, company_id=DEFAULT_COMPANY_ID, code=PRODUCT_CODE).id
        ids["customer"] = customer(db, company_id=DEFAULT_COMPANY_ID, name=CUSTOMER_NAME).id
        warehouse(db, company_id=DEFAULT_COMPANY_ID, code="BRW")
        db.commit()
        return {k: str(v) for k, v in ids.items()}
    finally:
        db.close()


def _console(session_factory, monkeypatch, *, grants: list[str]) -> Console:
    from app.services.chatbot import console_service
    from app.services.chatbot.head import parser as parser_mod

    _seed_contact(session_factory, variables={})
    _seed_borrowable_envelope(session_factory)
    console = Console(session_factory, monkeypatch)
    console.ids = _seed(session_factory)
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
    return console


@pytest.fixture
def console(session_factory, monkeypatch):
    return _console(session_factory, monkeypatch, grants=[GRANT])


@pytest.fixture
def console_no_grant(session_factory, monkeypatch):
    return _console(session_factory, monkeypatch, grants=[])


# --------------------------------------------------------------------------- #
# Grant
# --------------------------------------------------------------------------- #


def test_no_grant_says_the_sales_report_is_not_enabled_and_calls_nothing(console_no_grant) -> None:
    text, calls = console_no_grant.say(_rank(_e("Sorento", "brand")), "top 3 salesman for Sorento brand last month")
    assert text.strip() == DENIAL, text
    assert calls == []


# --------------------------------------------------------------------------- #
# Args: the fresh ask runs at once
# --------------------------------------------------------------------------- #


def test_the_owner_example_runs_crm_report_ask_with_the_resolved_args(console) -> None:
    text, calls = console.say(_rank(_e("Sorento", "brand")), "top 3 salesman for Sorento brand last month")
    (args,) = calls
    assert args["group_by"] == "sales_agent", args
    assert args["top_n"] == 3, args
    assert args["sort"] == "desc" and args["measure"] == "amount" and args["basis"] == "delivered", args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert args["contact_id"] and args["space_id"], args
    assert "AGENT A" in text, text
    assert PERIOD_Q not in text and TOPN_Q not in text


def test_bottom_is_sort_asc(console) -> None:
    _text, calls = console.say(_rank(_e("Sorento", "brand"), rank_direction="bottom"), "bottom 3 salesman for Sorento")
    (args,) = calls
    assert args["sort"] == "asc", args


def test_rank_by_quantity_is_measure_qty(console) -> None:
    _text, calls = console.say(_rank(_e("Sorento", "brand"), measure="qty"), "top 3 salesman by qty for Sorento")
    (args,) = calls
    assert args["measure"] == "qty", args


def test_basis_ordered_passes_through(console) -> None:
    _text, calls = console.say(_rank(_e("Sorento", "brand"), basis="ordered"), "top 3 salesman ordered for Sorento")
    (args,) = calls
    assert args["basis"] == "ordered", args


def test_an_unclear_basis_is_asked_never_delivered(console) -> None:
    """Owner ruling 4 Oct 2026 (was: an unclear basis ran delivered)."""
    text, calls = console.say(_rank(_e("Sorento", "brand"), basis="unclear"), "top 3 salesman for Sorento")
    assert calls == [], calls
    assert text.strip() == "Delivered, ordered or outstanding?", text


def test_group_by_warehouse_is_location(console) -> None:
    _text, calls = console.say(_rank(_e("Sorento", "brand"), group_by="warehouse"), "top 3 locations for Sorento")
    (args,) = calls
    assert args["group_by"] == "location", args


@pytest.mark.parametrize("group_by", ["customer", "product", "brand", "category", "channel"])
def test_the_other_catalogue_dimensions_pass_through(console, group_by) -> None:
    _text, calls = console.say(_rank(group_by=group_by), f"top 3 {group_by}")
    (args,) = calls
    assert args["group_by"] == group_by, args


def test_a_product_entity_becomes_product_ids(console) -> None:
    # REPORT-ENGINE 1b code review S4: a named product travels as its code (prefix rule), never product_ids.
    _text, calls = console.say(_rank(_e(PRODUCT_CODE, "product")), f"top 3 salesman for {PRODUCT_CODE}")
    (args,) = calls
    assert args["product_code"] == PRODUCT_CODE and "product_ids" not in args, args


def test_a_customer_entity_becomes_customer_ids(console) -> None:
    _text, calls = console.say(
        _rank(_e(CUSTOMER_NAME, "customer"), group_by="product"), f"top 3 products for {CUSTOMER_NAME}"
    )
    (args,) = calls
    assert args["customer_ids"] == [console.ids["customer"]], args


def test_a_warehouse_entity_becomes_warehouse_codes(console) -> None:
    _text, calls = console.say(_rank(_e("BRW", "warehouse")), "top 3 salesman in BRW")
    (args,) = calls
    assert args["warehouse_codes"] == ["BRW"], args


def test_a_sales_agent_and_a_category_word_become_ids(console) -> None:
    _text, calls = console.say(
        _rank(_e("AGENT A", "sales_agent"), _e("ZZT CATEGORY", "category"), group_by="month"),
        "sales by month for AGENT A ZZT CATEGORY",
    )
    (args,) = calls
    assert args["sales_agent_ids"] == [console.ids["agent"]], args
    assert args["category_ids"] == [console.ids["category"]], args


def test_no_group_by_is_a_total_that_runs_without_top_n(console) -> None:
    _text, calls = console.say(_rank(_e("Sorento", "brand"), group_by=None, top_n=None), "how much did we sell of Sorento")
    (args,) = calls
    assert not args.get("group_by") and "top_n" not in args, args
    assert args["date_from"] == "2026-09-01", args


# --------------------------------------------------------------------------- #
# Words that name nothing, dimensions outside the catalogue
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "hint, noun", [("brand", "brand"), ("sales_agent", "sales agent"), ("category", "category")]
)
def test_an_unknown_word_is_said_and_nothing_runs(console, hint, noun) -> None:
    text, calls = console.say(_rank(_e("Zzz", hint)), f"top 3 salesman for Zzz {noun}")
    assert calls == []
    assert text.strip() == f"I don't know 'Zzz' as a {noun}.", text


def test_group_by_date_is_outside_the_catalogue(console) -> None:
    text, calls = console.say(_rank(_e("Sorento", "brand"), group_by="date"), "top 3 days for Sorento")
    assert calls == []
    assert text.strip() == CATALOGUE_LINE, text


# --------------------------------------------------------------------------- #
# Asking back (AC-RE-18a to 18d)
# --------------------------------------------------------------------------- #


def test_ac_re_18a_no_period_asks_which_period_then_the_reply_runs_the_first_ask(console) -> None:
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), date_filter_start=None, date_filter_end=None), "top 3 salesman for Sorento"
    )
    assert text.strip() == PERIOD_Q, text
    assert calls == []
    text, calls = console.say(_reply(**SEP), "last month")
    (args,) = calls
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert "AGENT A" in text, text


def test_ac_re_18a_a_period_reply_without_a_date_is_a_miss_and_asks_again(console) -> None:
    console.say(_rank(date_filter_start=None, date_filter_end=None), "top 3 salesman")
    text, calls = console.say(_reply(), "whenever")
    assert calls == []
    assert PERIOD_Q in text, text


def test_ac_re_18b_no_number_asks_how_many_then_the_reply_runs(console) -> None:
    text, calls = console.say(_rank(_e("Sorento", "brand"), top_n=None), "top salesman for Sorento last month")
    assert text.strip() == TOPN_Q, text
    assert calls == []
    _text, calls = console.say(_reply(top_n=5), "5")
    (args,) = calls
    assert args["top_n"] == 5, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


DEFAULT_NOTE = "I couldn't read how many, so here is the top 10."


def test_ac_re_18b_a_non_number_reply_is_a_miss_and_a_second_miss_runs_the_default_top_10(console) -> None:
    """Crew ruling: the parser's top_n for the reply turn is the only reader. First miss asks again;
    the second runs top 10 and says so on one line above the ranking."""
    console.say(_rank(_e("Sorento", "brand"), top_n=None), "top salesman for Sorento last month")
    text, calls = console.say(_reply(top_n=None), "lots")
    assert calls == []
    assert TOPN_Q in text, text
    text, calls = console.say(_reply(top_n=None), "plenty")
    (args,) = calls
    assert args["top_n"] == 10, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert DEFAULT_NOTE in text, text
    assert text.index(DEFAULT_NOTE) < text.index("AGENT A"), "the note sits above the ranking"


def test_ac_re_18b_a_number_outside_1_to_the_ceiling_is_a_miss(console) -> None:
    console.say(_rank(top_n=None), "top salesman last month")
    text, calls = console.say(_reply(top_n=1001), "1001")
    assert calls == []
    assert "I can list at most the top 1,000 in one reply." in text, text
    assert TOPN_Q in text, text


def test_ac_re_18c_neither_asks_the_period_first_then_how_many_then_runs(console) -> None:
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), top_n=None, date_filter_start=None, date_filter_end=None), "top salesman for Sorento"
    )
    assert text.strip() == PERIOD_Q and calls == [], text
    text, calls = console.say(_reply(**SEP), "last month")
    assert text.strip() == TOPN_Q and calls == [], text
    _text, calls = console.say(_reply(top_n=5), "5")
    (args,) = calls
    assert args["top_n"] == 5 and args["date_from"] == "2026-09-01", args
    assert args["brand_ids"] == [console.ids["brand"]], args


def test_ac_re_18d_cancel(console) -> None:
    console.say(_rank(date_filter_start=None, date_filter_end=None), "top 3 salesman")
    text, calls = console.say(_reply(message_type="casual", intent_hint=None), "cancel")
    assert text.strip() == CANCELLED, text
    assert calls == []


# --------------------------------------------------------------------------- #
# Route refusals are said as the route's own message
# --------------------------------------------------------------------------- #


def test_a_route_refusal_is_said_as_its_message(console) -> None:
    console.refuse = {"message": DIMENSION_REFUSAL, "detail": None, "code": "report_dimension_not_allowed"}
    text, calls = console.say(_rank(_e("Sorento", "brand")), "top 3 salesman for Sorento")
    assert calls, "the route is the audience check"
    assert text.strip() == DIMENSION_REFUSAL, text


# --------------------------------------------------------------------------- #
# Engine seam: report_ask.take_words and ENGINE_KEYS
# --------------------------------------------------------------------------- #


def test_take_words_moves_brand_agent_category_onto_report_ask_words() -> None:
    from app.services.chatbot.lanes.business import report_ask

    verdict = _rank(
        _e("Sorento", "brand"), _e("AGENT A", "sales_agent"), _e("water tap", "category"),
        _e(PRODUCT_CODE, "product"), _e(CUSTOMER_NAME, "customer"), _e("BRW", "warehouse"),
    )
    out = report_ask.take_words(verdict, "top 3 salesman for Sorento")
    assert out["report_ask_words"] == {"brand": ["Sorento"], "sales_agent": ["AGENT A"], "category": ["water tap"]}, out
    assert [e["hint"] for e in out["entities"]] == ["product", "customer", "warehouse"], out["entities"]


def test_take_words_leaves_other_asks_alone() -> None:
    from app.services.chatbot.lanes.business import report_ask

    verdict = _parser_output(domain_hint="order", intent_hint="check_order", order_status="sales_report",
                             entities=[_e("Sorento", "brand")])
    assert report_ask.take_words(verdict, "sales report for Sorento") == verdict


def test_report_ask_words_is_an_engine_key() -> None:
    from app.services.chatbot import required_fields as rf

    assert "report_ask_words" in rf.ENGINE_KEYS


def test_a_forged_report_ask_words_from_the_parser_is_stripped(console) -> None:
    """The parser can never forge the engine's own key: a forged unknown brand word must not
    stop the run, and must not become a brand filter."""
    forged = _rank(report_ask_words={"brand": ["Zzz"], "sales_agent": [], "category": []})
    text, calls = console.say(forged, "top 3 salesman last month")
    (args,) = calls
    assert not args.get("brand_ids"), args
    assert "I don't know" not in text, text


# --------------------------------------------------------------------------- #
# 1b security fix round: F1 (a dealer never probes agent or location words), F2
# --------------------------------------------------------------------------- #

from sqlalchemy import text as _sql  # noqa: E402


def _link_dealer(console, name: str = "ZZT OWN DEALER SDN BHD") -> str:
    """Link the harness contact to one customer of its own: a customer-scoped (dealer) contact."""
    from tests._mc_lookup_seed import customer

    db = console.session_factory()
    try:
        own = customer(db, company_id=DEFAULT_COMPANY_ID, name=name)
        db.execute(
            _sql(
                "INSERT INTO respond_contact_customers (id, contact_id, customer_id, company_id) "
                "SELECT gen_random_uuid(), id, :cust, :company FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"cust": str(own.id), "company": DEFAULT_COMPANY_ID, "cid": str(CONTACT_ID)},
        )
        db.commit()
        return str(own.id)
    finally:
        db.close()


def _other_customer(console, name: str = "ZZT OTHER DEALER SDN BHD") -> str:
    from tests._mc_lookup_seed import customer

    db = console.session_factory()
    try:
        row = customer(db, company_id=DEFAULT_COMPANY_ID, name=name)
        db.commit()
        return str(row.id)
    finally:
        db.close()


@pytest.fixture
def dealer(console):
    console.own_customer = _link_dealer(console)
    return console


@pytest.mark.parametrize("word", ["Zzz", "AGENT A"], ids=["names_no_agent", "names_an_agent"])
def test_f1_a_dealer_naming_a_sales_agent_gets_the_refusal_and_no_probe(dealer, word) -> None:
    text, calls = dealer.say(_rank(_e(word, "sales_agent"), group_by="product"), f"top products for agent {word}")
    assert text.strip() == DIMENSION_REFUSAL, text
    assert calls == []


def test_f1_a_dealer_naming_a_location_gets_the_refusal(dealer) -> None:
    text, calls = dealer.say(_rank(_e("BRW", "warehouse"), group_by="product"), "top products in BRW")
    assert text.strip() == DIMENSION_REFUSAL, text
    assert calls == []


def test_f1_a_dealer_naming_an_unknown_location_gets_the_refusal_not_a_probe(dealer) -> None:
    text, calls = dealer.say(_rank(_e("ZZTNOWHERE", "warehouse"), group_by="product"), "top products in ZZTNOWHERE")
    assert text.strip() == DIMENSION_REFUSAL, text
    assert calls == []


def test_f1_a_dealer_grouping_by_sales_agent_gets_the_refusal(dealer) -> None:
    text, calls = dealer.say(_rank(group_by="sales_agent"), "top 3 salesman")
    assert text.strip() == DIMENSION_REFUSAL, text
    assert calls == []


def test_f1_a_dealer_with_an_unknown_brand_still_gets_the_unknown_word_line(dealer) -> None:
    text, calls = dealer.say(_rank(_e("Zzz", "brand"), group_by="product"), "top products for Zzz brand")
    assert text.strip() == "I don't know 'Zzz' as a brand.", text
    assert calls == []


def test_f1_a_dealer_with_a_known_brand_runs(dealer) -> None:
    _text, calls = dealer.say(_rank(_e("Sorento", "brand"), group_by="product"), "top products for Sorento")
    (args,) = calls
    assert args["brand_ids"] == [dealer.ids["brand"]], args


def test_f2a_a_dealer_naming_another_customer_never_sends_that_customer(dealer) -> None:
    other = _other_customer(dealer)
    _text, calls = dealer.say(
        _rank(_e("ZZT OTHER DEALER SDN BHD", "customer"), group_by="product"), "top products for ZZT OTHER DEALER SDN BHD"
    )
    for args in calls:
        assert other not in (args.get("customer_ids") or []), args


def test_f2b_an_answering_turn_runs_only_the_carried_args(console) -> None:
    """A customer or sales agent entity on the reply turn never reaches the request."""
    other = _other_customer(console)
    console.say(
        _rank(_e("Sorento", "brand"), date_filter_start=None, date_filter_end=None), "top 3 salesman for Sorento"
    )
    _text, calls = console.say(
        _reply(entities=[_e("ZZT OTHER DEALER SDN BHD", "customer"), _e("AGENT A", "sales_agent")], **SEP),
        "last month",
    )
    (args,) = calls
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert other not in (args.get("customer_ids") or []), args
    assert not args.get("customer_ids"), args
    assert not args.get("sales_agent_ids"), args


def _forge(node: Any, forged: dict[str, Any]) -> int:
    """Put `forged` into every open `sales_ranking` slot found in `node` (its extras, and the
    args map inside them if any). Returns how many slots were found."""
    found = 0
    if isinstance(node, dict):
        if node.get("ask") == "sales_ranking":
            extras = node.setdefault("extras", {})
            extras.update(forged)
            for key in ("args", "route_args"):
                if isinstance(extras.get(key), dict):
                    extras[key].update(forged)
            found += 1
        for value in list(node.values()):
            found += _forge(value, forged)
    elif isinstance(node, list):
        for value in node:
            found += _forge(value, forged)
    return found


def test_f2c_the_request_uses_the_turns_own_contact_not_the_carried_one(console) -> None:
    console.say(
        _rank(_e("Sorento", "brand"), date_filter_start=None, date_filter_end=None), "top 3 salesman for Sorento"
    )
    forged = {"contact_id": "ZZT-FORGED-CONTACT", "space_id": "ZZT-FORGED-SPACE"}
    assert _forge(console.session_vars, forged) >= 1, "the open sales_ranking slot was not found in the session"
    _text, calls = console.say(_reply(**SEP), "last month")
    (args,) = calls
    assert args["contact_id"] and args["space_id"], args
    assert args["contact_id"] != "ZZT-FORGED-CONTACT" and args["space_id"] != "ZZT-FORGED-SPACE", args


# --------------------------------------------------------------------------- #
# 1b code-review fix round: S2 to S5, N2, N3
# --------------------------------------------------------------------------- #


def _seed_more(console) -> None:
    from app.models.product import Brand, ProductCategory
    from app.models.sales_agent import SalesAgent

    db = console.session_factory()
    try:
        for code, name in (("ZZTFC1", "ZZT FAUCET CHROME"), ("ZZTFC2", "ZZT FAUCET BLACK"),
                           ("ZZTSK1", "ZZT SINK"), ("ZZTSK2", "ZZT SINK DEEP")):
            db.add(ProductCategory(id=str(uuid.uuid4()), category_code=code, category_name=name,
                                   company_id=DEFAULT_COMPANY_ID))
        for code, name in (("ZZTAL", "ZZTALPHA"), ("ZZTBE", "ZZTBETA")):
            db.add(Brand(id=str(uuid.uuid4()), brand_code=code, brand_name=name, company_id=DEFAULT_COMPANY_ID))
        pats = [SalesAgent(id=str(uuid.uuid4()), sales_agent=f"ZZTPAT {n}", company_id=DEFAULT_COMPANY_ID)
                for n in ("I", "II")]
        db.add_all(pats)
        db.flush()
        console.ids["pats"] = sorted(p.id for p in pats)
        sink = db.query(ProductCategory).filter(ProductCategory.category_code == "ZZTSK1").one()
        console.ids["sink"] = str(sink.id)
        db.commit()
    finally:
        db.close()


def test_s2_a_month_breakdown_never_asks_how_many(console) -> None:
    text, calls = console.say(_rank(group_by="month", top_n=None), "sales by month for Sorento last month")
    (args,) = calls
    assert args["group_by"] == "month" and args["top_n"] == 100, args
    assert TOPN_Q not in text, text


def test_s3_several_categories_matching_a_word_run_nothing_and_name_them(console) -> None:
    _seed_more(console)
    text, calls = console.say(_rank(_e("faucet", "category"), group_by="product"), "top products for faucet")
    assert calls == []
    assert text.strip() == "'faucet' matches several categories: ZZTFC1, ZZTFC2. Ask again naming one.", text


def test_s3_several_brands_matching_a_word_run_nothing_and_name_them(console) -> None:
    _seed_more(console)
    text, calls = console.say(_rank(_e("ZZTALPHA ZZTBETA", "brand")), "top 3 salesman for ZZTALPHA ZZTBETA")
    assert calls == []
    assert text.strip() == "'ZZTALPHA ZZTBETA' matches several brands: ZZTALPHA, ZZTBETA. Ask again naming one.", text


def test_s3_an_exact_category_name_wins_alone_among_partial_matches(console) -> None:
    _seed_more(console)
    _text, calls = console.say(_rank(_e("ZZT SINK", "category"), group_by="product"), "top products for ZZT SINK")
    (args,) = calls
    assert args["category_ids"] == [console.ids["sink"]], args


def test_s3_a_sales_agent_word_naming_two_rows_is_one_person_and_runs_with_both(console) -> None:
    _seed_more(console)
    _text, calls = console.say(_rank(_e("ZZTPAT", "sales_agent"), group_by="month"), "sales by month for ZZTPAT")
    (args,) = calls
    assert sorted(args["sales_agent_ids"]) == console.ids["pats"], args


def test_s4_a_named_product_goes_as_product_code_not_ids(console) -> None:
    _text, calls = console.say(_rank(_e(PRODUCT_CODE, "product")), f"top 3 salesman for {PRODUCT_CODE}")
    (args,) = calls
    assert args["product_code"] == PRODUCT_CODE, args
    assert "product_ids" not in args, args


def _order_ask(*entities: dict[str, Any]) -> dict[str, Any]:
    return _parser_output(domain_hint="order", intent_hint="check_order", order_status=None,
                          entities=list(entities))


def test_s5_a_customer_carried_from_an_earlier_message_is_not_a_filter(console) -> None:
    console.say(_order_ask(_e(CUSTOMER_NAME, "customer")), f"orders for {CUSTOMER_NAME}")
    carried = dict(_e(CUSTOMER_NAME, "customer"), current_message=False)
    _text, calls = console.say(_rank(carried), "top 3 salesman last month")
    (args,) = calls
    assert not args.get("customer_ids"), args


def test_s5_a_product_carried_from_an_earlier_message_is_not_a_filter(console) -> None:
    console.say(_order_ask(_e(PRODUCT_CODE, "product")), f"orders for {PRODUCT_CODE}")
    carried = dict(_e(PRODUCT_CODE, "product"), current_message=False)
    _text, calls = console.say(_rank(carried), "top 3 salesman last month")
    (args,) = calls
    assert "product_code" not in args and not args.get("product_ids"), args


def test_n2_a_start_date_alone_runs_to_today_malaysia(console) -> None:
    from app.services.reports.registry import today_malaysia

    _text, calls = console.say(
        _rank(date_filter_start="2026-09-01", date_filter_end=None), "top 3 salesman since September"
    )
    (args,) = calls
    assert args["date_from"] == "2026-09-01", args
    assert args["date_to"] == today_malaysia().isoformat(), args


def test_n2_an_end_date_alone_is_a_miss_and_the_period_is_asked(console) -> None:
    text, calls = console.say(
        _rank(date_filter_start=None, date_filter_end="2026-09-30"), "top 3 salesman until September"
    )
    assert calls == []
    assert text.strip() == PERIOD_Q, text


def test_n3_a_dealers_request_adds_no_customer_ids_the_route_forces_the_links(dealer) -> None:
    _text, calls = dealer.say(_rank(_e("Sorento", "brand"), group_by="product"), "top products for Sorento")
    (args,) = calls
    assert not args.get("customer_ids"), args


# --------------------------------------------------------------------------- #
# REPORT-ENGINE: "remove the cap" (owner, 30 Sep 2026). One ceiling,
# `sales_report_service.TOP_SELLING_N_CEILING`, shared with the route; the lane no longer
# stops at 100. The constant is imported so a second limit cannot creep back.
# --------------------------------------------------------------------------- #


def test_a_top_n_of_200_is_accepted_not_a_miss(console) -> None:
    text, calls = console.say(_rank(_e("Sorento", "brand"), top_n=200), "top 200 salesman for Sorento last month")
    assert calls, f"treated as a miss, no run: {text!r}"
    (args,) = calls
    assert args["top_n"] == 200, args
    assert TOPN_Q not in text, text


def test_a_top_n_at_the_ceiling_is_accepted(console) -> None:
    from app.services.sales_report_service import TOP_SELLING_N_CEILING

    text, calls = console.say(
        _rank(_e("Sorento", "brand"), top_n=TOP_SELLING_N_CEILING), "top salesman for Sorento last month"
    )
    assert calls, f"treated as a miss, no run: {text!r}"
    (args,) = calls
    assert args["top_n"] == TOP_SELLING_N_CEILING, args


@pytest.mark.parametrize("reply_text", ["200", "top 200"])
def test_the_how_many_answer_200_is_accepted(console, reply_text) -> None:
    text, calls = console.say(_rank(_e("Sorento", "brand"), top_n=None), "top salesman for Sorento last month")
    assert text.strip() == TOPN_Q and calls == [], text
    text, calls = console.say(_reply(top_n=200), reply_text)
    assert calls, f"the reply was read as a miss: {text!r}"
    (args,) = calls
    assert args["top_n"] == 200, args
    assert args["brand_ids"] == [console.ids["brand"]], args


# --------------------------------------------------------------------------- #
# REPORT-ENGINE: owner hand-test fail, "who's the top 3 salesman for sorento water closet
# this year". Expected: the top 3 SALES AGENTS for brand Sorento + category Water Closet this
# year, no sales agent filter. Dev answered the old top selling list ("Top 3 selling items",
# "Sales agent: WT I, WT III, WT IV") because the dev parser prompt is the older version without
# REPORT_ASK_ADDENDUM, so the parser said order_status "top_selling".
# --------------------------------------------------------------------------- #

OWNER_MESSAGE = "who's the top 3 salesman for sorento water closet this year"
THIS_YEAR = {"date_filter_start": "2026-01-01", "date_filter_end": "2026-12-31"}
_REAL_FETCH_SERVICES = FetchServices  # the class, before any test wraps it


def _seed_wt(console) -> dict[str, Any]:
    """The dev shape: a category WATER CLOSET and three accounts of one person (WT I, WT III, WT IV),
    one of which carries the alias "water closet" (fake, but it reproduces the resolver hit that
    expands to every account sharing the code stem)."""
    from app.models.product import ProductCategory
    from app.models.sales_agent import SalesAgent

    db = console.session_factory()
    try:
        category = ProductCategory(
            id=str(uuid.uuid4()), category_code="ZZTWCL", category_name="WATER CLOSET", company_id=DEFAULT_COMPANY_ID
        )
        agents = [
            SalesAgent(
                id=str(uuid.uuid4()), sales_agent=code, person_label="WT", company_id=DEFAULT_COMPANY_ID,
                aliases="water closet" if code == "WT I" else None,
            )
            for code in ("WT I", "WT III", "WT IV")
        ]
        db.add(category)
        db.add_all(agents)
        db.commit()
        return {"category": str(category.id), "agents": sorted(str(a.id) for a in agents)}
    finally:
        db.close()


def _say_all(console, monkeypatch, qf: dict[str, Any], body: str) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """`console.say`, plus EVERY MCP tool call of the turn (not only `crm_report_ask`)."""
    import sys

    log: list[tuple[str, dict[str, Any]]] = []
    module = sys.modules[__name__]

    def _factory(*, mcp_call):
        def _spy(name: str, args: dict[str, Any]) -> Any:
            log.append((name, dict(args)))
            return mcp_call(name, args)

        return _REAL_FETCH_SERVICES(mcp_call=_spy)

    monkeypatch.setattr(module, "FetchServices", _factory)
    text, _calls = console.say(qf, body)
    return text, log


def _hit(raw: str, hint: str) -> dict[str, Any]:
    return {**_e(raw, hint), "hint_confident": True}


def _old(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """What the dev (production-labelled, no REPORT_ASK_ADDENDUM) parser emits for a ranking."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="top_selling", entities=list(entities),
        domain_in_message=True, rank_by="amount", basis="delivered", rank_group="item", rank_direction="top",
        top_n=3, group_by=None, **THIS_YEAR,
    )
    base.update(overrides)
    return _parser_output(**base)


def _names(log: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _args in log]


# C1: the new prompt's reading of the owner's message ---------------------- #


def test_c1_the_owner_message_new_reading_ranks_sales_agents_for_the_brand_and_category(console) -> None:
    wt = _seed_wt(console)
    verdict = _rank(_hit("sorento", "brand"), _hit("water closet", "category"), **THIS_YEAR)
    text, calls = console.say(verdict, OWNER_MESSAGE)
    (args,) = calls
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["category_ids"] == [wt["category"]], args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args
    assert not args.get("sales_agent_ids"), f"a sales agent filter crept in: {args}"
    assert "Top 3 selling items" not in text, text


# C2: top_selling stays top_selling (the sales ranking vocabulary is the parser's, see test_report_ask_parser.py)


def test_c2_top_selling_items_stays_top_selling(console, monkeypatch) -> None:
    """No regression: a ranking of ITEMS is the old top selling path, never the new lane."""
    text, log = _say_all(
        console, monkeypatch, _old(_hit("sorento", "brand")), "top 3 selling items for sorento"
    )
    assert "crm_top_selling_report" in _names(log), (_names(log), text)
    assert TOOL not in _names(log), (_names(log), text)


def test_c2_top_selling_items_by_an_agent_stays_top_selling_with_the_agent_filter(console, monkeypatch) -> None:
    wt = _seed_wt(console)
    text, log = _say_all(
        console, monkeypatch, _old(_hit("WT", "sales_agent"), top_n=10), "top selling items sold by agent WT"
    )
    assert TOOL not in _names(log), (_names(log), text)
    (args,) = [a for n, a in log if n == "crm_top_selling_report"]
    assert sorted(args.get("sales_agent_ids") or []) == wt["agents"], args



# Measure: the lane takes `measure` from the parser's own key and reads no message text.


@pytest.mark.parametrize("body", ["top 3 salesman for sorento this year by quantity", "top 3 salesman for sorento this year"])
def test_the_lane_takes_measure_qty_from_the_parsers_measure_key(console, body) -> None:
    _text, calls = console.say(_rank(_e("sorento", "brand"), measure="qty", **THIS_YEAR), body)
    (args,) = calls
    assert args["measure"] == "qty", (body, args)


@pytest.mark.parametrize("body", ["top 3 salesman for sorento this year by quantity", "top 3 salesman for sorento this year"])
def test_a_null_measure_is_asked_whatever_the_words_say(console, body) -> None:
    """Owner rule 4 Oct: no regex over the text, and (ruling 4 Oct) no silent amount either. measure
    null asks the metric question, even when the words say quantity."""
    text, calls = console.say(_rank(_e("sorento", "brand"), measure=None, **THIS_YEAR), body)
    assert calls == [], (body, calls)
    assert text.strip() == "By quantity or by amount?", (body, text)


def test_the_lane_takes_measure_amount_from_the_parsers_measure_key(console) -> None:
    _text, calls = console.say(_rank(_e("sorento", "brand"), measure="amount", **THIS_YEAR), "top 3 salesman by amount")
    (args,) = calls
    assert args["measure"] == "amount", args


def test_a_ranking_noun_entity_is_not_filtered_by_the_lane_it_is_the_parsers_to_omit(console) -> None:
    """Counterpart of the removed noun-drop: the lane does not special-case "salesman"; an
    unknown sales_agent word is the plain unknown-word line (the new prompt never emits it)."""
    text, calls = console.say(_rank(_e("salesman", "sales_agent"), _e("Sorento", "brand")), "top 3 salesman for sorento")
    assert calls == []
    assert text.strip() == "I don't know 'salesman' as a sales agent.", text


@pytest.mark.parametrize("body", ["top 3 salesman for sorento this year", "top 3 sales agents for sorento this year", OWNER_MESSAGE])
def test_no_reroute_from_top_selling(console, monkeypatch, body) -> None:
    """The published prompt parses sales rankings itself (the parser emits sales_ranking for these
    messages: pinned by test_report_ask_parser.py and the live console case): a top_selling verdict stays top selling."""
    text, log = _say_all(console, monkeypatch, _old(_hit("sorento", "brand"), top_n=3), body)
    assert TOOL not in _names(log), (body, _names(log), text)
    assert "crm_top_selling_report" in _names(log), (body, _names(log), text)




# F2 (kept): a real agent name next to a customer ranking is still a filter. The ranked noun is the
# parser's to drop now (see test_report_ask_parser.py), not a code word list.


def test_f2_a_real_agent_name_next_to_a_customer_ranking_is_still_a_filter(console) -> None:
    verdict = _rank(_e("AGENT A", "sales_agent"), _e("Sorento", "brand"), group_by="customer", **THIS_YEAR)
    text, calls = console.say(verdict, "top 3 customers of AGENT A for sorento this year")
    (args,) = calls
    assert args["group_by"] == "customer", args
    assert args["sales_agent_ids"] == [console.ids["agent"]], args


# C3: resolver hardening --------------------------------------------------- #


def test_c3_a_category_wins_over_a_sales_agent_alias(console) -> None:
    from app.models.base import set_company_scope
    from app.services.chatbot import engine as engine_mod_

    _seed_wt(console)
    db = console.session_factory()
    try:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        assert engine_mod_._classify_word_group(db, "water closet") == "category"
    finally:
        db.close()


def test_c3_an_exact_brand_wins_over_a_sales_agent_alias(console) -> None:
    from app.models.sales_agent import SalesAgent
    from app.services.chatbot import engine as engine_mod_

    db = console.session_factory()
    try:
        db.add(SalesAgent(id=str(uuid.uuid4()), sales_agent="ZZT AGENT B", person_label="ZZT AGENT B",
                          company_id=DEFAULT_COMPANY_ID, aliases="sorento"))
        db.commit()
        assert engine_mod_._classify_word_group(db, "sorento") == "brand"
    finally:
        db.close()


def test_c3_a_noisy_token_splits_into_brand_and_category_with_no_sales_agent(console) -> None:
    from app.services.chatbot import engine as engine_mod_

    wt = _seed_wt(console)
    db = console.session_factory()
    try:
        split = engine_mod_._split_noisy_token(db, "sorento water closet")
    finally:
        db.close()
    assert split is not None
    parts, _leftover = split
    assert {(p["raw"].lower(), p["hint"]) for p in parts} == {("sorento", "brand"), ("water closet", "category")}, parts
    assert "sales_agent" not in {p["hint"] for p in parts}, parts
    assert wt["category"]


def test_c3_a_real_agent_word_is_still_a_sales_agent(console) -> None:
    """No regression: a word that names only an agent (the alias nobody else claims) stays sales_agent."""
    from app.models.sales_agent import SalesAgent
    from app.services.chatbot import engine as engine_mod_

    db = console.session_factory()
    try:
        db.add(SalesAgent(id=str(uuid.uuid4()), sales_agent="ZZT AGENT C", person_label="ZZT AGENT C",
                          company_id=DEFAULT_COMPANY_ID, aliases="zzthandle"))
        db.commit()
        assert engine_mod_._classify_word_group(db, "zzthandle") == "sales_agent"
    finally:
        db.close()



# --------------------------------------------------------------------------- #
# Kill tests (owner rule 4 Oct, SEMANTIC ONLY): the code no longer reads the message text.
# --------------------------------------------------------------------------- #


def test_kill_the_message_words_do_not_set_the_measure(console) -> None:
    """The message WORDS never set the measure, only the parser's keys do: measure and rank_by
    both null with quantity words in the text asks the metric; rank_by quantity runs as qty."""
    text, calls = console.say(
        _rank(_e("sorento", "brand"), measure=None, rank_by=None, **THIS_YEAR),
        "top 3 salesman for sorento this year by quantity",
    )
    assert calls == [] and text.strip() == "By quantity or by amount?", (text, calls)
    _text, calls = console.say(
        _rank(_e("sorento", "brand"), measure=None, rank_by="quantity", **THIS_YEAR),
        "top 3 salesman for sorento this year",
    )
    (args,) = calls
    assert args["measure"] == "qty", args


def test_kill_a_top_selling_verdict_for_the_owner_message_is_not_rerouted(console, monkeypatch) -> None:
    # The published prompt makes the parser emit sales_ranking for this message (pinned by the
    # parser test + live console case); the code never reroutes on the text.
    text, log = _say_all(console, monkeypatch, _old(_hit("sorento", "brand"), _hit("water closet", "category")), OWNER_MESSAGE)
    assert "crm_top_selling_report" in _names(log), (_names(log), text)
    assert TOOL not in _names(log), (_names(log), text)


def test_kill_a_person_noun_looking_sales_agent_entity_is_trusted_as_a_filter(console) -> None:
    from app.models.sales_agent import SalesAgent

    agent_id = str(uuid.uuid4())
    db = console.session_factory()
    try:
        db.add(SalesAgent(id=agent_id, sales_agent="SA", person_label="SA", company_id=DEFAULT_COMPANY_ID))
        db.commit()
    finally:
        db.close()
    _text, calls = console.say(
        _rank(_e("SA", "sales_agent"), _e("Sorento", "brand"), group_by="customer", **THIS_YEAR),
        "top 3 customers of SA for sorento this year",
    )
    (args,) = calls
    assert args["sales_agent_ids"] == [agent_id], args


def test_kill_the_text_rule_code_is_gone() -> None:
    from app.services.chatbot import engine
    from app.services.chatbot.lanes.business import report_ask

    for name in ("_ranked_who", "_RANKED_NOUN_RE", "_sales_ranking_verdict"):
        assert not hasattr(engine, name), name
    assert not hasattr(report_ask, "_QUANTITY_WORD_RE")


# --------------------------------------------------------------------------- #
# REPORT-ENGINE: the multi-turn defects seen with the LIVE parser (browser pass, 3 Oct 2026),
# restubbed with the verdicts the NEW prompt teaches (`ranking_refine`, `measure`).
# T2/T3 (Q5), D2 (bare number after a ranking), D3 (Q3), D4 (period-only follow-up).
# --------------------------------------------------------------------------- #

CABANA_MSG = "top 3 salesman for Cabana brand last month"
SEP_RANGE = {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}


def _seed_cabana(console) -> str:
    from app.models.product import Brand

    db = console.session_factory()
    try:
        brand = Brand(id=str(uuid.uuid4()), brand_code="ZZTCAB", brand_name="Cabana", company_id=DEFAULT_COMPANY_ID)
        db.add(brand)
        db.commit()
        return str(brand.id)
    finally:
        db.close()


def _t1(console) -> tuple[str, list[dict[str, Any]]]:
    console.cabana = _seed_cabana(console)
    return console.say(_rank(_e("Cabana", "brand"), top_n=3, **SEP_RANGE), CABANA_MSG)


def _refine(**overrides: Any) -> dict[str, Any]:
    """The new prompt's verdict for a message that ONLY changes one key of the ranking on screen:
    only the changed key is filled, group_by null, entities []."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", ranking_refine=True,
        entities=[], group_by=None, top_n=None, measure=None, basis=None,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(overrides)
    return _parser_output(**base)


def test_t1_the_cabana_ranking_runs(console) -> None:
    _text, calls = _t1(console)
    (args,) = calls
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [console.cabana], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_t2_a_fresh_ask_with_no_count_asks_how_many(console) -> None:
    """Q5: the new prompt emits top_n null for "top salesman" (ranking_refine false)."""
    _t1(console)
    text, calls = console.say(
        _rank(_e("Cabana", "brand"), top_n=None, **SEP_RANGE), "top salesman for cabana last month"
    )
    assert calls == [], calls
    assert text.strip() == TOPN_Q, text


def test_t3_the_number_reply_after_the_how_many_question_runs_top_5(console) -> None:
    _t1(console)
    console.say(_rank(_e("Cabana", "brand"), top_n=None, **SEP_RANGE), "top salesman for cabana last month")
    _text, calls = console.say(_reply(top_n=5), "5")
    (args,) = calls
    assert args["top_n"] == 5, args
    assert args["group_by"] == "sales_agent" and args["brand_ids"] == [console.cabana], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


@pytest.mark.parametrize("reply, n", [("5", 5), ("top 10", 10)])
def test_d2_a_count_refine_reruns_the_held_ranking_with_the_new_top_n(console, reply, n) -> None:
    _t1(console)
    _text, calls = console.say(_refine(top_n=n), reply)
    (args,) = calls
    assert args["group_by"] == "sales_agent", f"ranking lost, ran a total: {args}"
    assert args["brand_ids"] == [console.cabana], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert args["top_n"] == n, args
    assert args["measure"] == "amount" and args["basis"] == "delivered", args


def test_d2_a_measure_refine_reruns_the_held_ranking_by_qty(console) -> None:
    _t1(console)
    _text, calls = console.say(_refine(measure="qty"), "by quantity")
    (args,) = calls
    assert args["measure"] == "qty" and args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [console.cabana], args


def test_d2_a_basis_refine_reruns_the_held_ranking_ordered(console) -> None:
    _t1(console)
    _text, calls = console.say(_refine(basis="ordered"), "ordered")
    (args,) = calls
    assert args["basis"] == "ordered" and args["group_by"] == "sales_agent" and args["top_n"] == 3, args


def test_a_parser_emitted_sales_ranking_frame_is_stripped(console) -> None:
    """The frame is the engine's: a refine carrying a forged one, with none held, is a fresh ask."""
    forged = {"group_by": "sales_agent", "brand_ids": ["x"], "date_from": "2026-01-01", "date_to": "2026-12-31",
              "top_n": 3, "basis": "delivered", "measure": "amount"}
    text, calls = console.say(_refine(top_n=5, sales_ranking_frame=forged), "5")
    assert calls == [], (text, calls)
    assert text.strip() == PERIOD_Q, text


def test_a_dealer_refine_over_a_held_staff_frame_is_refused_locally(console) -> None:
    """Defence in depth: the held frame groups by sales agent, outside DEALER_KEYS; no route call."""
    _t1(console)
    console.own_customer = _link_dealer(console)
    text, calls = console.say(_refine(top_n=5), "5")
    assert text.strip() == DIMENSION_REFUSAL, text
    assert calls == []


def test_a_frame_does_not_survive_another_ask(console) -> None:
    """ranking, then an order ask, then a refine: the old ranking is gone, so it is a fresh ask."""
    # V1-DOC-SCOPE slice 3: the middle turn is an ORDER ask naming a customer, which an
    # unlinked contact holding only the report grant may not make; this one plays office.
    from tests.chatbot._turn_helpers import make_office_access_type

    make_office_access_type(console.session_factory, CONTACT_ID)
    _t1(console)
    console.say(_order_ask(_e(CUSTOMER_NAME, "customer")), f"orders for {CUSTOMER_NAME}")
    text, calls = console.say(_refine(top_n=5), "5")
    assert calls == [], (text, calls)
    assert text.strip() == PERIOD_Q, text


def test_d3_a_fresh_ranking_that_names_its_own_subject_never_borrows_the_previous_period(console) -> None:
    """Q3: ranking_refine false, no dates -> asks the period, September held or not."""
    _t1(console)
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), top_n=3, date_filter_start=None, date_filter_end=None),
        "top 3 salesman for sorento",
    )
    assert calls == [], calls
    assert text.strip() == PERIOD_Q, text


@pytest.mark.parametrize(
    "start, end", [("2026-01-01", "2026-12-31"), ("2025-01-01", "2025-12-31"), ("2026-08-01", "2026-08-31")],
    ids=["this_year", "2025", "last_month"],
)
def test_d4_a_period_refine_reruns_the_held_ranking_for_that_period(console, start, end) -> None:
    _t1(console)
    text, calls = console.say(_refine(date_filter_start=start, date_filter_end=end), "this year")
    (args,) = calls
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [console.cabana], args
    assert args["date_from"] == start and args["date_to"] == end, args
    assert "Could not find order" not in text, text


def test_a_refine_with_no_held_ranking_is_a_fresh_ask_and_never_a_total(console) -> None:
    """ranking_refine true in a fresh conversation: nothing to refine, so ask what is missing."""
    text, calls = console.say(_refine(top_n=5), "5")
    assert calls == [], (text, calls)
    assert text.strip() == PERIOD_Q, text


def test_a_period_refine_with_no_held_ranking_asks_and_runs_no_report(console) -> None:
    text, calls = console.say(
        _refine(date_filter_start="2026-01-01", date_filter_end="2026-12-31"), "this year"
    )
    assert calls == [], (text, calls)


def test_a_new_intent_wins_over_the_held_ranking(console) -> None:
    """After a ranking, an order ask runs no crm_report_ask."""
    _t1(console)
    text, calls = console.say(_order_ask(_e(CUSTOMER_NAME, "customer")), f"orders for {CUSTOMER_NAME}")
    assert calls == [], (text, calls)


def test_a_new_intent_wins_a_stock_ask_after_a_ranking_is_not_a_ranking(console) -> None:
    """After a ranking, a stock ask runs no crm_report_ask."""
    _t1(console)
    text, calls = console.say(_parser_output(), "stock for SRTWC8517")
    assert calls == [], (text, calls)


# --------------------------------------------------------------------------- #
# Crew ruling: no regex in code, even for value extraction. The answer to "How many?" is the
# PARSER's top_n on the reply turn (as the period answer is its parsed dates).
# --------------------------------------------------------------------------- #


def _ask_how_many(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=None), "top salesman for Sorento last month")


def test_kill_the_how_many_reply_text_is_not_read_for_digits(console) -> None:
    """Reply text "5" but the parser's top_n is None: not a top 5 run, the question is asked again."""
    _ask_how_many(console)
    text, calls = console.say(_reply(top_n=None), "5")
    assert calls == [], calls
    assert TOPN_Q in text, text


def test_the_parsers_top_n_wins_over_the_reply_text(console) -> None:
    _ask_how_many(console)
    _text, calls = console.say(_reply(top_n=7), "the usual amount")
    (args,) = calls
    assert args["top_n"] == 7, args
    assert args["brand_ids"] == [console.ids["brand"]], args


def test_a_reply_verdict_top_n_5_with_text_5_runs_top_5(console) -> None:
    _ask_how_many(console)
    _text, calls = console.say(_reply(top_n=5), "5")
    (args,) = calls
    assert args["top_n"] == 5, args


def test_two_misses_run_the_default_top_10_and_say_so(console) -> None:
    _ask_how_many(console)
    text, calls = console.say(_reply(top_n=None), "hmm")
    assert calls == [] and TOPN_Q in text, text
    text, calls = console.say(_reply(top_n=None), "dunno")
    (args,) = calls
    assert args["top_n"] == 10, args
    assert DEFAULT_NOTE in text, text
    assert chr(0x2014) not in text and chr(0x2013) not in text


def test_kill_report_ask_reads_no_regex_for_the_reply() -> None:
    import inspect

    from app.services.chatbot.lanes.business import report_ask

    assert "re.findall" not in inspect.getsource(report_ask)
    resolve = inspect.getsource(report_ask._resolve_top_n)
    assert "re." not in resolve and "findall" not in resolve, resolve


# --------------------------------------------------------------------------- #
# Held-state rule (owner): a new intent always wins. A parser-declared sales_ranking verdict is a
# new ask and is never taken by the open top selling question ("By quantity or by amount?").
# --------------------------------------------------------------------------- #

ASK_METRIC = "By quantity or by amount?"


def _ask_top_selling_metric(console) -> None:
    text, _calls = console.say(
        _old(_hit("sorento", "brand"), rank_by=None, top_n=10), "top 10 sales items for sorento"
    )
    assert ASK_METRIC in text, f"setup: the top selling question was not asked: {text!r}"


def test_a_sales_ranking_verdict_is_never_taken_by_the_open_top_selling_question(console) -> None:
    _ask_top_selling_metric(console)
    text, calls = console.say(
        _rank(_hit("sorento", "brand"), group_by="customer", top_n=1000, **THIS_YEAR),
        "top 1000 customers for sorento this year",
    )
    assert ASK_METRIC not in text, text
    (args,) = calls
    assert args["group_by"] == "customer" and args["top_n"] == 1000, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args


def test_guard_a_genuine_answer_still_answers_the_open_top_selling_question(console, monkeypatch) -> None:
    _ask_top_selling_metric(console)
    text, log = _say_all(
        console, monkeypatch, _old(rank_by="amount", entities=[], top_n=None), "amount"
    )
    assert "crm_top_selling_report" in _names(log), (_names(log), text)
    assert TOOL not in _names(log), (_names(log), text)


# --------------------------------------------------------------------------- #
# Live chain (browser pass on 36d5d47e), parser fields only.
# R1: a sales_ranking verdict with group_by null and no filter entity is not a valid ask.
# R2: an open required-field question is dropped by a NEW ask (not a refine) that names its own
#     axis or subject.
# --------------------------------------------------------------------------- #


def _bare_number_verdict() -> dict[str, Any]:
    """Live verdict for a bare "3" after a ranking: sales_ranking, nothing else, not a refine."""
    return _rank(
        group_by=None, top_n=None, ranking_refine=False, date_filter_start=None, date_filter_end=None
    )


def _t1_sorento_top1(console) -> None:
    _text, calls = console.say(_rank(_e("Sorento", "brand"), top_n=1, **SEP_RANGE), "top salesman for Sorento last month")
    assert calls, "setup: the first ranking did not run"


def test_r1_a_bare_number_verdict_with_no_axis_and_no_filter_says_the_catalogue_and_opens_nothing(console) -> None:
    _t1_sorento_top1(console)
    text, calls = console.say(_bare_number_verdict(), "3")
    assert calls == [], calls
    assert text.strip() == CATALOGUE_LINE, text
    # No question is pending: a period-only reply now answers nothing.
    text, calls = console.say(_reply(**SEP), "last month")
    assert calls == [], (text, calls)


def test_r1_the_live_chain_t1_to_t4_asks_cabana_its_period_then_runs_cabana(console) -> None:
    _t1_sorento_top1(console)
    cabana = _seed_cabana(console)
    text, calls = console.say(_bare_number_verdict(), "3")
    assert calls == [] and text.strip() == CATALOGUE_LINE, (text, calls)
    text, calls = console.say(
        _rank(_e("Cabana", "brand"), top_n=3, date_filter_start=None, date_filter_end=None),
        "top 3 salesman for cabana",
    )
    assert calls == [], calls
    assert text.strip() == PERIOD_Q, text
    assert "as a period" not in text, text
    _text, calls = console.say(_rank(ranking_refine=True, top_n=3, **THIS_YEAR), "this year")
    (args,) = calls
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [cabana], f"not Cabana (a brand-less total?): {args}"
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args


def test_r2_a_new_ask_drops_the_open_period_question_and_runs_with_its_own_dates(console) -> None:
    cabana = _seed_cabana(console)
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), date_filter_start=None, date_filter_end=None), "top 3 salesman for sorento"
    )
    assert text.strip() == PERIOD_Q and calls == [], (text, calls)
    text, calls = console.say(
        _rank(_e("Cabana", "brand"), top_n=3, ranking_refine=False, **SEP_RANGE), "top 3 salesman for cabana last month"
    )
    assert "as a period" not in text, text
    (args,) = calls
    assert args["brand_ids"] == [cabana], args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_r2_guard_an_open_period_question_is_still_answered_by_a_period_only_verdict(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), date_filter_start=None, date_filter_end=None), "top 3 salesman for sorento")
    _text, calls = console.say(_reply(**SEP), "last month")
    (args,) = calls
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_r1_guard_a_total_with_a_filter_is_still_a_valid_ask(console) -> None:
    cabana = _seed_cabana(console)
    text, calls = console.say(
        _rank(_e("Cabana", "brand"), group_by=None, top_n=None, ranking_refine=False,
              date_filter_start="2026-08-01", date_filter_end="2026-08-31"),
        "how much did we sell of Cabana in August",
    )
    (args,) = calls
    assert not args.get("group_by") and args["brand_ids"] == [cabana], args
    assert args["date_from"] == "2026-08-01" and args["date_to"] == "2026-08-31", args
    assert CATALOGUE_LINE not in text, text


# --------------------------------------------------------------------------- #
# Review round 8 should-fixes: S9 (an echoed axis answers the open question), S10 (a dealer's
# plain total is a valid ask).
# --------------------------------------------------------------------------- #


def _open_period_for_sorento(console) -> None:
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), top_n=3, date_filter_start=None, date_filter_end=None),
        "top 3 salesman for sorento",
    )
    assert text.strip() == PERIOD_Q and calls == [], (text, calls)


def test_s9_a_period_reply_that_echoes_the_axis_is_a_genuine_answer(console) -> None:
    _open_period_for_sorento(console)
    text, calls = console.say(
        _rank(entities=[], group_by="sales_agent", top_n=None, ranking_refine=False, **SEP_RANGE), "last month"
    )
    assert TOPN_Q not in text, text
    (args,) = calls
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_s9_guard_a_reply_naming_a_different_axis_is_a_new_ask(console) -> None:
    _open_period_for_sorento(console)
    text, calls = console.say(
        _rank(entities=[], group_by="customer", top_n=None, ranking_refine=False, **SEP_RANGE), "customers last month"
    )
    # A new ask: the open question is dropped, so the old Sorento ask never runs with the new dates.
    for args in calls:
        assert args.get("group_by") != "sales_agent" or args.get("brand_ids") != [console.ids["brand"]], args


def _plain_total() -> dict[str, Any]:
    return _rank(entities=[], group_by=None, top_n=None, ranking_refine=False, **SEP_RANGE)


def test_s10_a_dealers_plain_total_runs_as_a_total(dealer) -> None:
    text, calls = dealer.say(_plain_total(), "how much did I buy last month")
    assert CATALOGUE_LINE not in text, text
    (args,) = calls
    assert not args.get("group_by"), args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_s10_guard_a_staff_contact_with_the_same_verdict_gets_the_catalogue_line(console) -> None:
    text, calls = console.say(_plain_total(), "how much did we sell last month")
    assert calls == [], calls
    assert text.strip() == CATALOGUE_LINE, text


# --------------------------------------------------------------------------- #
# REPORT-ENGINE, owner direction 4 Oct 2026
#   C. a category word resolves through `_top_selling_category_ids`, the ONE function top selling
#      calls (`resolve_category_token`, then the class vocabulary `resolve_category_class`)
#   D. the lane passes the basis `outstanding` through, fresh and on a refine
#   E. top selling never takes `outstanding` as its basis
# --------------------------------------------------------------------------- #


def _seed_class_categories(console, *codes_and_labels: tuple[str, str]) -> dict[str, str]:
    """Categories named by CODE only (name = code, the live shape) carrying a class label."""
    from app.models.product import ProductCategory

    db = console.session_factory()
    try:
        out: dict[str, str] = {}
        for code, label in codes_and_labels:
            row = ProductCategory(
                id=str(uuid.uuid4()), category_code=code, category_name=code, class_label=label,
                company_id=DEFAULT_COMPANY_ID,
            )
            db.add(row)
            db.flush()
            out[code] = str(row.id)
        db.commit()
        return out
    finally:
        db.close()


def _top_selling_ids(console, words: list[str]) -> tuple[Any, ...]:
    """What top selling's own function says for these category words (the reference answer)."""
    from app.models.base import set_company_scope
    from app.services.chatbot.lanes import business as business_pkg

    db = console.session_factory()
    try:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        return business_pkg._top_selling_category_ids({"category_words": words}, db=db)
    finally:
        db.close()


def test_c_water_closet_resolves_to_every_category_of_the_class(console) -> None:
    """Two categories named by code only, one class label: "water closet" names BOTH ids."""
    ids = _seed_class_categories(console, ("SRT-WC", "Water Closet"), ("CB-WC", "Water Closet"))
    text, calls = console.say(
        _rank(_e("water closet", "category"), group_by="sales_agent", **THIS_YEAR), "top 3 salesman for water closet"
    )
    assert "I don't know" not in text, text
    (args,) = calls
    assert sorted(args["category_ids"]) == sorted(ids.values()), args


def test_c_the_lane_gets_exactly_the_ids_top_selling_gets(console) -> None:
    _seed_class_categories(console, ("SRT-WC", "Water Closet"), ("CB-WC", "Water Closet"))
    reference_ids, named, ambiguous, unknown = _top_selling_ids(console, ["water closet"])
    assert named and not ambiguous and not unknown and len(reference_ids) >= 2, (reference_ids, ambiguous, unknown)
    text, calls = console.say(
        _rank(_e("water closet", "category"), group_by="product", **THIS_YEAR), "top 5 products for water closet"
    )
    assert calls, text
    (args,) = calls
    assert sorted(args["category_ids"]) == sorted(reference_ids), (args, reference_ids)


def test_c_kill_the_lane_calls_top_sellings_function_not_a_copy(console, monkeypatch) -> None:
    """The lane's category words go through `business._top_selling_category_ids`: whatever it says
    is what the ask is sent with, and it is asked about the typed word."""
    from app.services.chatbot.lanes import business as business_pkg

    seen: list[Any] = []

    def _stub(slot, *, db):
        seen.append((dict(slot), db))
        return [console.ids["category"]], True, [], []

    monkeypatch.setattr(business_pkg, "_top_selling_category_ids", _stub)
    _text, calls = console.say(
        _rank(_e("zzt unheard word", "category"), group_by="sales_agent", **THIS_YEAR), "top 3 salesman for it"
    )
    assert seen, "the lane never called _top_selling_category_ids"
    assert any("zzt unheard word" in " ".join(slot.get("category_words") or []) for slot, _db in seen), seen
    (args,) = calls
    assert args["category_ids"] == [console.ids["category"]], args


def test_c_kill_an_ambiguous_answer_of_top_sellings_function_is_the_several_line(console, monkeypatch) -> None:
    from app.services.chatbot.lanes import business as business_pkg

    monkeypatch.setattr(
        business_pkg, "_top_selling_category_ids", lambda slot, *, db: ([], True, ["CAT ONE", "CAT TWO"], [])
    )
    text, calls = console.say(_rank(_e("cat", "category"), group_by="product"), "top 3 products for cat")
    assert calls == []
    assert text.strip() == "'cat' matches several categories: CAT ONE, CAT TWO. Ask again naming one.", text


def test_c_kill_an_unknown_answer_of_top_sellings_function_is_the_unknown_line(console, monkeypatch) -> None:
    from app.services.chatbot.lanes import business as business_pkg

    monkeypatch.setattr(business_pkg, "_top_selling_category_ids", lambda slot, *, db: ([], True, [], ["zzword"]))
    text, calls = console.say(_rank(_e("zzword", "category"), group_by="product"), "top 3 products for zzword")
    assert calls == []
    assert text.strip() == "I don't know 'zzword' as a category.", text


def test_c_one_word_matching_several_class_labels_is_the_several_line(console) -> None:
    """"basin tap" holds the whole-word runs of two class labels (Basin, Tap): never a silent
    widening, the lane asks. The reference (top selling's own function) says the same."""
    _seed_class_categories(console, ("SRT-BA", "Basin"), ("SRT-TP", "Tap"))
    _ids, named, ambiguous, _unknown = _top_selling_ids(console, ["basin tap"])
    assert named and sorted(ambiguous) == ["Basin", "Tap"], ambiguous
    text, calls = console.say(_rank(_e("basin tap", "category"), group_by="product"), "top 3 products for basin tap")
    assert calls == [], calls
    assert text.strip() == "'basin tap' matches several categories: Basin, Tap. Ask again naming one.", text


def test_c_an_unknown_category_word_still_says_it_does_not_know_it(console) -> None:
    _seed_class_categories(console, ("SRT-WC", "Water Closet"))
    text, calls = console.say(_rank(_e("zzzunheard", "category"), group_by="product"), "top 3 products for zzzunheard")
    assert calls == []
    assert text.strip() == "I don't know 'zzzunheard' as a category.", text


def test_c_a_category_named_by_its_own_name_still_resolves_alone(console) -> None:
    """No regression: the exact name wins through `resolve_category_token`, the class is not consulted."""
    _text, calls = console.say(_rank(_e("ZZT CATEGORY", "category"), group_by="month"), "sales by month for ZZT CATEGORY")
    (args,) = calls
    assert args["category_ids"] == [console.ids["category"]], args


# D ----------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "parser_basis, route_basis",
    [("outstanding", "outstanding"), ("ordered", "ordered"), ("delivered", "delivered")],
)
def test_d_the_parsers_basis_maps_onto_the_routes(console, parser_basis, route_basis) -> None:
    _text, calls = console.say(
        _rank(_e("Sorento", "brand"), basis=parser_basis, **THIS_YEAR), "top 3 salesman for sorento by outstanding"
    )
    (args,) = calls
    assert args["basis"] == route_basis, (parser_basis, args)
    assert args["group_by"] == "sales_agent" and args["brand_ids"] == [console.ids["brand"]], args


@pytest.mark.parametrize("parser_basis", ["unclear", None, "invoiced"])
def test_d_a_basis_the_parser_did_not_settle_is_asked_never_delivered(console, parser_basis) -> None:
    """Owner ruling 4 Oct 2026 (was: unclear, null and an unknown word all ran delivered)."""
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), basis=parser_basis, **THIS_YEAR), "top 3 salesman for sorento"
    )
    assert calls == [], (parser_basis, calls)
    assert text.strip() == "Delivered, ordered or outstanding?", (parser_basis, text)


def test_d_a_basis_refine_to_outstanding_reruns_the_held_ranking(console) -> None:
    _t1(console)
    _text, calls = console.say(_refine(basis="outstanding"), "by outstanding")
    (args,) = calls
    assert args["basis"] == "outstanding", args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [console.cabana], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert args["measure"] == "amount", args


def test_d_a_refine_that_names_no_basis_keeps_the_held_outstanding_basis(console) -> None:
    console.cabana = _seed_cabana(console)
    console.say(_rank(_e("Cabana", "brand"), basis="outstanding", top_n=3, **SEP_RANGE), CABANA_MSG)
    _text, calls = console.say(_refine(top_n=5), "top 5")
    (args,) = calls
    assert args["basis"] == "outstanding" and args["top_n"] == 5, args


def test_d_an_unknown_basis_on_a_refine_leaves_the_held_basis(console) -> None:
    _t1(console)
    _text, calls = console.say(_refine(basis="invoiced", top_n=5), "invoiced top 5")
    (args,) = calls
    assert args["basis"] == "delivered" and args["top_n"] == 5, args


# E ----------------------------------------------------------------------- #


def test_e_a_top_selling_verdict_with_basis_outstanding_is_not_an_outstanding_top_selling(
    console, monkeypatch
) -> None:
    """Top selling has two bases (delivered, ordered). A parser slip of `outstanding` on a
    top_selling verdict neither reroutes to the ranking lane nor reaches the top selling route
    (review S2: never run delivered silently): the basis question is asked and nothing is fetched."""
    text, log = _say_all(
        console, monkeypatch, _old(_hit("sorento", "brand"), basis="outstanding"), "top 3 selling items outstanding"
    )
    assert TOOL not in _names(log), (_names(log), text)
    assert "crm_top_selling_report" not in _names(log), (_names(log), text)
    assert text.strip() == "Delivered (transferred to DO) or ordered?", text


def test_e_the_schema_lets_a_sales_ranking_emit_outstanding() -> None:
    from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

    enum = PARSE_OUTPUT_JSON_SCHEMA["properties"]["basis"]["enum"]
    assert "outstanding" in enum, enum
    assert {"delivered", "ordered", "unclear", None} <= set(enum), enum


def test_e_a_sales_ranking_basis_outstanding_validates_against_the_schema() -> None:
    """The provider's strict schema is the only enum check: `outstanding` on a sales ranking must pass it."""
    jsonschema = pytest.importorskip("jsonschema")
    from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

    emission = _rank(_e("Sorento", "brand"), basis="outstanding", measure="amount", **THIS_YEAR)
    assert emission["order_status"] == "sales_ranking" and emission["basis"] == "outstanding"
    only_basis = {"type": "object", "properties": {"basis": PARSE_OUTPUT_JSON_SCHEMA["properties"]["basis"]}}
    jsonschema.validate({"basis": emission["basis"]}, only_basis)


def test_e_parse_returns_basis_outstanding_untouched(monkeypatch) -> None:
    """`parse` keeps what the provider emitted: an `outstanding` basis reaches the engine as sent."""
    from types import SimpleNamespace

    from app.services.chatbot import llm_call
    from app.services.chatbot.head import parser as parser_mod

    emission = _rank(_e("Sorento", "brand"), basis="outstanding", **THIS_YEAR)
    emission = {k: emission.get(k) for k in parser_mod.DECLARED_KEYS}
    monkeypatch.setattr(
        llm_call, "chat",
        lambda *a, **k: SimpleNamespace(content=json.dumps(emission), prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    config = parser_mod.ParserConfig(
        system_prompt="stub", prompt_version=39, provider="openai", model="gpt-test", api_key="sk-test"
    )
    parsed = parser_mod.parse(config, "top 3 salesman for sorento by outstanding")
    assert parsed["basis"] == "outstanding", parsed
    assert parsed["order_status"] == "sales_ranking", parsed


def test_e_the_prompt_teaches_outstanding_as_a_sales_ranking_basis() -> None:
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM, SEMANTIC_PARSER_PROMPT

    assert REPORT_ASK_ADDENDUM in SEMANTIC_PARSER_PROMPT
    lowered = " ".join(REPORT_ASK_ADDENDUM.lower().split())
    assert 'basis "outstanding"' in lowered, 'REPORT_ASK_ADDENDUM never maps a phrase to basis "outstanding"'
    for phrase in ("by outstanding", "not yet delivered"):
        assert phrase in lowered, phrase


def test_e_the_top_selling_addendum_does_not_teach_outstanding_as_its_basis() -> None:
    from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

    basis_section = TOP_SELLING_ADDENDUM.split("== BASIS: which sale is counted ==", 1)[1].split("== RANK_GROUP", 1)[0]
    assert "outstanding" not in basis_section.lower(), basis_section
