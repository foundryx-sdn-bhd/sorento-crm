"""Top selling fix lane round 7: the owner's retest of round 5, part 2 (27 Sep 2026 21:05
to 21:10 MYT, console :3083, parser v39, PR #1273).

The owner's PR comment "Owner retest of top selling round 5, part 2" is the work list.
Every message is replayed here in the owner's order through `console_service.
run_console_turn`, the way the console runs them (dry run, same contact, each turn sent
the `session_vars` the last one returned), with the REAL resolver on seeded rows named
like the owner's: customers HANLIM TRADING, SAMPLE JAYDEN, SHOPEE - 260818MRNUPWTS (MCH),
NEWTON BUILDMATE SDN BHD (PROJECT) (SRT); agents FANNY, JAYDEN and WT, each an I / III /
IV account. The parser is faked with the readings that reproduce each wrong line (the
diagnosis in the PR comment names them); the presenter is real.

* R1 a continuation into another report carries the resolved entities, never the raw
  words: a carried agent word never reaches the customer resolver, and customer names
  match whole words only ("wt" never hits "...MRNUPWTS" or "NEWTON");
* R2 a year, month or quarter alone over an open ranking re-runs the ranking; ranking
  words ("top 10 hot selling") keep a message in the ranking whatever the parser's
  status; an unknown agent word is said in one line; neither the routing picker nor the
  escalate offer is reachable inside a ranking or outstanding conversation unless the
  user asks for a person;
* R3 "sales order?", "based on sales order", "by SO", "SO basis", "ordered" switch the
  ranking to Basis: Ordered; "delivered", "by DO" switch back; never a rank pick;
* R4 a sales agent's "Also known as" names match whole words and cover every account of
  that person ("sold by william" gives WT I, WT III, WT IV).

Postgres only, every row seeded here.
"""
from __future__ import annotations

import copy
import json
import uuid
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot._turn_helpers import do_window
from tests.chatbot import test_outstanding_lane as outstanding_lane
from tests.chatbot.test_engine import CONTACT_ID
from tests.chatbot.test_outstanding_lane import (
    REPORT_HIT,
    REPORT_MISS,
    AnswerServices,
    FetchServices,
    _present_response,
    _report_route_body,
    _seed_contact,
)
from tests.chatbot.test_top_selling_round4 import ITEM_CODES, _fake_route
from tests.chatbot.test_top_selling_round5 import (
    ASK_METRIC,
    GRANTS,
    ORDERS,
    ORDERS_BODY,
    OUTSTANDING,
    TOOL,
    _RANK_NULLS,
    _SNAKE,
    _answer,
    _ask,
    _calls,
    _e,
    _narrow,
    _parser_output,
    _position,
    _report,
    _year,
)
from tests.chatbot.test_top_selling_round6 import _seed_borrowable_envelope

AGENTS = ("FANNY I", "FANNY III", "FANNY IV", "JAYDEN I", "JAYDEN III", "WT I", "WT III", "WT IV")
HANLIM = ("HANLIM TRADING", "HANLIM TRADING (SRT)")
CUSTOMERS = (*HANLIM, "SAMPLE JAYDEN", "SHOPEE - 260818MRNUPWTS (MCH)", "NEWTON BUILDMATE SDN BHD (PROJECT) (SRT)")
WT = "WT I, WT III, WT IV"
Q1 = "01/01/2026 to 31/03/2026"
#: Never in any reply of these conversations (the owner's wrong lines).
NEVER = (
    "SAMPLE JAYDEN", "SHOPEE", "NEWTON", "Which customer do you mean", "Could not find order",
    "escalate", "Please choose who to route to", "Here are the orders I found",
)
OQA_NONE = {"mode": None, "picked": [], "items": [], "qty_for_all": None}


# --------------------------------------------------------------------------- #
# Seeds, the route double and the console
# --------------------------------------------------------------------------- #


class Seeded:
    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def agents(self, prefix: str) -> list[str]:
        return sorted(v for k, v in self.ids.items() if k in AGENTS and k.split()[0] == prefix)

    def customers(self, *names: str) -> list[str]:
        return sorted(self.ids[n] for n in names)


def _seed(session_factory, *, wt_alias: str | None = None) -> Seeded:
    from app.models.product import ProductCategory
    from app.models.sales_agent import SalesAgent
    from app.services.product_class_signal import CLASS_SYNONYMS
    from tests._mc_lookup_seed import customer

    seeded = Seeded()
    db = session_factory()
    try:
        for code in AGENTS:
            agent = SalesAgent(
                id=str(uuid.uuid4()), sales_agent=code, person_label=None, company_id=DEFAULT_COMPANY_ID,
                # The owner types the alias on ONE account; it covers the person's others.
                **({"aliases": wt_alias} if wt_alias and code == "WT I" else {}),
            )
            db.add(agent)
            seeded.ids[code] = str(agent.id)
        category = ProductCategory(
            id=str(uuid.uuid4()), category_code="SRT-WC", category_name="SRT-WC", class_label="Water Closet",
            search_synonyms=CLASS_SYNONYMS["Water Closet"], company_id=DEFAULT_COMPANY_ID,
        )
        db.add(category)
        db.flush()
        seeded.ids["Water Closet"] = str(category.id)
        for name in CUSTOMERS:
            seeded.ids[name] = str(customer(db, company_id=DEFAULT_COMPANY_ID, name=name).id)
        db.commit()
    finally:
        db.close()
    return seeded


class Console:
    """One console turn at a time, the session carried as the page carries it."""

    def __init__(self, session_factory, monkeypatch, seeded: Seeded) -> None:
        self.session_factory, self.monkeypatch, self.seeded = session_factory, monkeypatch, seeded
        self.session_vars: dict[str, Any] = {}
        self.tokens: list[list[str]] = []
        self.codes: list[str] = list(ITEM_CODES)
        self.report: dict[str, Any] = REPORT_HIT

    def _call(self, captured):
        names = {v: k for k, v in self.seeded.ids.items()}
        present = _present_response()

        def _mcp(name: str, args: dict[str, Any]) -> Any:
            captured.append((name, dict(args)))
            if name == TOOL:
                body = _fake_route(args, names, self.codes)
                body["date_from"] = args.get("date_from") or body["date_from"]
                body["date_to"] = args.get("date_to") or body["date_to"]
                return present(name, json.dumps(body))
            if name == OUTSTANDING:
                return present(name, json.dumps(_report_route_body(self.report, args)))
            if name == ORDERS:
                return present(name, json.dumps(ORDERS_BODY))
            return json.dumps({"has_result": False, "items": []})

        return _mcp

    def say(self, qf: dict[str, Any], body: str) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
        from app.services.chatbot import console_service
        from app.services.chatbot.head import parser as parser_mod

        self.monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
        captured: list[tuple[str, dict[str, Any]]] = []
        call = self._call(captured)
        self.monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=call))
        self.monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: AnswerServices(
                mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
            ),
        )
        self.tokens.clear()
        db = self.session_factory()
        try:
            result = console_service.run_console_turn(
                db, contact_respond_id=str(CONTACT_ID), text=body, session_vars=copy.deepcopy(self.session_vars),
                run_id="zzt-top-selling-r7",
            )
        finally:
            db.close()
        self.session_vars = result.session_vars or {}
        text = result.reply_text or ""
        assert not _SNAKE.search(text), (body, text)
        assert "\u2014" not in text and "\u2013" not in text, body
        self.last = result
        return text, captured

    @property
    def open_question(self) -> dict[str, Any]:
        return self.session_vars.get("open_question") or {}

    @property
    def slot(self) -> dict[str, Any]:
        return ((self.session_vars.get("focus") or {}).get("top_selling")) or {}

    def asked_tokens(self) -> list[str]:
        return [t for batch in self.tokens for t in batch]


@pytest.fixture
def console_factory(session_factory, monkeypatch):
    from app.services.chatbot import console_service
    from app.services.chatbot.head import parser as parser_mod
    from app.services.chatbot.lanes.business import services as business_services

    _seed_contact(session_factory, variables={})
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
    consoles: list[Console] = []
    real = business_services.production_services

    def _production(db, *, space_id=None):
        # The REAL resolver, with every token it is asked about recorded.
        services = real(db, space_id=space_id)
        resolve = services.resolve_entity

        def _resolve(body):
            if consoles:
                consoles[-1].tokens.append(list(body.get("tokens") or []))
            return resolve(body)

        return services.__class__(access_types=services.access_types, resolve_entity=_resolve, probe=services.probe)

    monkeypatch.setattr(engine_mod.business_services, "production_services", _production)

    def _make(*, wt_alias: str | None = None) -> Console:
        console = Console(session_factory, monkeypatch, _seed(session_factory, wt_alias=wt_alias))
        consoles.append(console)
        return console

    return _make


def _clean(text: str, body: str) -> None:
    for bad in NEVER:
        assert bad not in text, (body, bad, text)


def _line(text: str, label: str, value: str) -> None:
    assert f"\n{label}: {value}\n" in f"\n{text}\n", (label, value, text)


# --------------------------------------------------------------------------- #
# The owner's readings (each reproduces a wrong line on bb7a7b0c)
# --------------------------------------------------------------------------- #


def _carried(raw: str, hint: str) -> dict[str, Any]:
    """The parser echoing an earlier turn's word (`current_message: false`)."""
    return {**_e(raw, hint), "current_message": False}


def _outstanding_carrying(raw: str) -> dict[str, Any]:
    """"any outstanding?" read as the report with the last turn's agent word carried."""
    return _report("outstanding", continuation=True, entities=[_carried(raw, "sales_agent")])


def _outstanding_v3() -> dict[str, Any]:
    """The same ask in the v3 key only: `status: outstanding`, no `order_status`."""
    return _report(None, continuation=True, status="outstanding")


def _pick_first() -> dict[str, Any]:
    """"sales order?" read as the open list's first row (`open_question_answer`)."""
    return _parser_output(
        message_type="casual", domain_hint=None, intent_hint=None, order_status=None, entities=[],
        open_question_answer={"mode": "pick", "picked": [1], "items": [], "qty_for_all": None},
    )


def _so_document(**overrides: Any) -> dict[str, Any]:
    """"sales order?" read as the sales order list."""
    base: dict[str, Any] = dict(document=["SO"], domain_in_message=True)
    base.update(overrides)
    return _report(None, **base)


def _year_as_order(year: int) -> dict[str, Any]:
    """"2025?" read as an order list for that year."""
    return _year(year, order_status="all", domain_in_message=True)


def _william_as_order() -> dict[str, Any]:
    """"top 10 hot selling item by william in q1 2026" read as an order ask."""
    return _parser_output(
        message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="all",
        entities=[_e("William", "sales_agent")], date_filter_start="2026-01-01", date_filter_end="2026-03-31",
        domain_in_message=True, **_RANK_NULLS,
    )


def _ranked_by_jayden(c: Console) -> None:
    c.say(_ask(top_n=100, rank_by="amount"), "top 100 hot selling item")
    c.say(_narrow(_e("fanny", "sales_agent")), "sold by fanny")
    text, captured = c.say(_narrow(_e("Jayden", "sales_agent"), _e("hanlim", "customer")), "sold by Jayden to hanlim")
    (args,) = _calls(captured)
    assert sorted(args["customer_ids"]) == c.seeded.customers(*HANLIM), args
    assert sorted(args["sales_agent_ids"]) == c.seeded.agents("JAYDEN"), args


def _bottom_water_closet(c: Console) -> None:
    c.say(
        _ask(top_n=100, rank_direction="bottom", entities=[_e("water closet", "category")]),
        "worst 100 selling water closet",
    )
    text, captured = c.say(_answer(rank_by="quantity"), "quantity")
    assert text.startswith("*Bottom 100 selling items*"), text


# --------------------------------------------------------------------------- #
# R1: the continuation carries the resolved entities, never the raw words
# --------------------------------------------------------------------------- #


OUTSTANDING_READINGS = {
    "plain": lambda raw: _report("outstanding", continuation=True),
    "agent_word_carried": _outstanding_carrying,
    "agent_word_carried_as_customer": lambda raw: _report(
        "outstanding", continuation=True, entities=[_carried(raw, "customer")]
    ),
    "v3_status_only": lambda raw: _outstanding_v3(),
}


class TestR1CarriedEntities:
    @pytest.mark.parametrize("reading", list(OUTSTANDING_READINGS))
    def test_any_outstanding_keeps_hanlim_and_drops_the_agent(self, console_factory, reading) -> None:
        """"sold by Jayden to hanlim" then "any outstanding?" then "1": Customer HANLIM
        TRADING, the agent dropped with the one line note, "Jayden" never resolved as a
        customer (on bb7a7b0c: Customer: SAMPLE JAYDEN)."""
        c = console_factory()
        _ranked_by_jayden(c)
        text, captured = c.say(OUTSTANDING_READINGS[reading]("Jayden"), "any outstanding?")
        _clean(text, "any outstanding?")
        assert "Jayden" not in c.asked_tokens(), c.tokens
        assert "Outstanding for which document?" in text, text
        assert "Customer: HANLIM TRADING" in text, text
        text, captured = c.say(_position(1), "1")
        _clean(text, "1")
        (args,) = _calls(captured, OUTSTANDING)
        assert args["scope"] == "so", args
        assert sorted(args["customer_ids"]) == c.seeded.customers(*HANLIM), args
        # Fix lane round 8: the agent is simply not carried, with no line about it.
        assert "is not a filter for" not in text and "Filters from the ranking" not in text, text

    @pytest.mark.parametrize("reading", list(OUTSTANDING_READINGS))
    def test_can_see_its_outstanding_after_sold_by_wt(self, console_factory, reading) -> None:
        """"sold by wt" then "can see its outstiandg?": the outstanding report for the
        same filters (agent WT dropped with the note, Q1 2026 kept), never "Which
        customer do you mean? 1. SHOPEE - 260818MRNUPWTS (MCH) 2. NEWTON ...". """
        c = console_factory()
        c.say(_ask(top_n=10, rank_by="quantity", date_filter_start="2026-01-01", date_filter_end="2026-03-31"),
              "top 10 hot selling item in q1 2026")
        text, captured = c.say(_narrow(_e("wt", "sales_agent")), "sold by wt")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), args
        text, captured = c.say(OUTSTANDING_READINGS[reading]("wt"), "can see its outstiandg?")
        _clean(text, "can see its outstiandg?")
        assert "wt" not in [t.lower() for t in c.asked_tokens()], c.tokens
        assert c.open_question.get("kind") != "customer_pick", c.open_question
        # Fix lane round 8: the ordinary outstanding question for the same filters (the
        # ranked codes, every customer, Q1 2026), the agent silently not carried; never
        # the order list, whose delivery date window found nothing.
        assert _calls(captured, ORDERS) == [], captured
        assert text.startswith(
            f"Product: {', '.join(ITEM_CODES)}\nCustomer: all\nLocation: all\nOrder date: {Q1}\n"
            "Outstanding for which document?"
        ), text
        assert "is not a filter for" not in text and "Filters from the ranking" not in text, text

    def test_a_carried_word_is_never_re_resolved_under_another_kind(self, console_factory) -> None:
        """The same carry on the plain order list ("show me the orders"): the agent word
        is dropped with its note, never looked up as a customer."""
        c = console_factory()
        _ranked_by_jayden(c)
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        text, captured = c.say(
            _report(None, domain_in_message=True, entities=[_carried("Jayden", "sales_agent")], **do_window()), "show me the orders"
        )
        _clean(text.replace("Here are the orders I found", ""), "show me the orders")
        assert "Jayden" not in c.asked_tokens(), c.tokens
        (args,) = _calls(captured, ORDERS)
        assert sorted(args.get("customer_ids") or []) == c.seeded.customers(*HANLIM), args


class TestR1CustomerWholeWord:
    """The generic resolver's customer name match (`entity_resolver._probe_customer`)."""

    def _probe(self, session_factory, token: str) -> list[str]:
        from app.services.entity_resolver import _probe_customer

        db = session_factory()
        try:
            return sorted(r.display["customer_name"] for r in _probe_customer(db, [token])[token])
        finally:
            db.close()

    def test_wt_matches_no_customer_by_substring(self, session_factory) -> None:
        _seed(session_factory)
        assert self._probe(session_factory, "wt") == []
        assert self._probe(session_factory, "WT") == []

    def test_whole_words_still_match(self, session_factory) -> None:
        _seed(session_factory)
        assert self._probe(session_factory, "hanlim") == sorted(HANLIM)
        assert self._probe(session_factory, "hanlim trading") == sorted(HANLIM)
        assert self._probe(session_factory, "jayden") == ["SAMPLE JAYDEN"]
        assert self._probe(session_factory, "newton buildmate") == ["NEWTON BUILDMATE SDN BHD (PROJECT) (SRT)"]
        # A name typed without its spaces is still the same words.
        assert self._probe(session_factory, "hanlimtrading") == sorted(HANLIM)

    def test_a_part_of_a_word_is_not_a_match(self, session_factory) -> None:
        _seed(session_factory)
        assert self._probe(session_factory, "hanl") == []
        assert self._probe(session_factory, "ewton") == []
        assert self._probe(session_factory, "mrnupwt") == []


# --------------------------------------------------------------------------- #
# R2: a period over an open ranking; ranking words; no picker, no offer
# --------------------------------------------------------------------------- #


PERIOD_READINGS = {
    "order_list_for_the_year": lambda: _year_as_order(2025),
    "no_status": lambda: _year(2025),
    "delivery_orders": lambda: _year(2025, document=["DO"], domain_in_message=True),
    "v3_delivered": lambda: _year(2025, status="delivered", domain_in_message=True),
}


class TestR2PeriodOverARanking:
    @pytest.mark.parametrize("reading", list(PERIOD_READINGS))
    @pytest.mark.parametrize("listed", [True, False], ids=["list_open", "no_sales"])
    def test_2025_re_runs_the_ranking_for_the_year(self, console_factory, reading, listed) -> None:
        """"worst 100 selling water closet", "quantity", "2025?" -> the same ranking for
        2025 (on bb7a7b0c: "Could not find order from 2025-01-01 to 2025-12-31" and the
        routing picker). `no_sales`: the ranking on screen found nothing, so no rank list
        is open to read "2025" off."""
        c = console_factory()
        if not listed:
            c.codes = []
        _bottom_water_closet(c)
        text, captured = c.say(PERIOD_READINGS[reading](), "2025?")
        _clean(text, "2025?")
        assert _calls(captured, ORDERS) == [], captured
        (args,) = _calls(captured)
        assert (args.get("date_from"), args.get("date_to")) == ("2025-01-01", "2025-12-31"), args
        assert args["direction"] == "bottom" and args["rank_by"] == "quantity", args
        assert args["category_ids"] == [c.seeded.ids["Water Closet"]], args
        assert c.open_question.get("kind") not in ("team_pick", "member_offer"), c.open_question

    @pytest.mark.parametrize(
        "body,start,end",
        [("july", "2026-07-01", "2026-07-31"), ("q1 2026", "2026-01-01", "2026-03-31"), ("in 2024", "2024-01-01", "2024-12-31")],
    )
    def test_a_month_or_a_quarter_alone(self, console_factory, body, start, end) -> None:
        c = console_factory()
        c.codes = []
        _bottom_water_closet(c)
        qf = _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="all",
            entities=[], date_filter_start=start, date_filter_end=end, domain_in_message=True,
        )
        text, captured = c.say(qf, body)
        _clean(text, body)
        (args,) = _calls(captured)
        assert (args.get("date_from"), args.get("date_to")) == (start, end), args
        assert args["direction"] == "bottom", args

    def test_a_period_with_a_report_word_is_still_the_report(self, console_factory) -> None:
        """"outstanding in 2025" names its own report: round 5's hop, unchanged."""
        c = console_factory()
        _bottom_water_closet(c)
        text, captured = c.say(_year(2025, order_status="outstanding", domain_in_message=True), "outstanding in 2025")
        assert _calls(captured) == [], captured
        # Fix lane round 8: the ordinary outstanding question over the ranking's codes.
        assert "Order date: 01/01/2025 to 31/12/2025\nOutstanding for which document?" in text, text
        assert "Filters from the ranking" not in text, text


RANKING_WORD_READINGS = {
    "order_list": _william_as_order,
    "no_status": lambda: {**_william_as_order(), "order_status": None, "domain_in_message": None},
    "delivered": lambda: {**_william_as_order(), "order_status": "delivered"},
}


class TestR2RankingWords:
    @pytest.mark.parametrize("reading", list(RANKING_WORD_READINGS))
    def test_top_10_by_an_unknown_agent_in_q1_stays_a_ranking(self, console_factory, reading) -> None:
        """"top 10 hot selling item by william in q1 2026" with no alias yet: the unknown
        agent said once, the ranking asked (a fresh ask states its own metric, owner
        ruling), never the order lane, never the picker (on bb7a7b0c: "Could not find
        order from 2026-01-01 to 2026-03-31" and the routing picker)."""
        c = console_factory()
        _bottom_water_closet(c)
        text, captured = c.say(RANKING_WORD_READINGS[reading](), "top 10 hot selling item by william in q1 2026")
        _clean(text, "top 10 ...")
        assert captured == [], captured
        assert "William" not in c.asked_tokens(), c.tokens
        assert text == "I don't know 'William' as a sales agent.\n\n" + ASK_METRIC, text
        assert c.slot.get("top_n") == 10 and "rank_direction" not in c.slot, c.slot
        text, captured = c.say(_answer(rank_by="quantity"), "quantity")
        _clean(text, "quantity")
        (args,) = _calls(captured)
        assert args["n"] == 10 and args.get("direction") != "bottom", args
        assert (args.get("date_from"), args.get("date_to")) == ("2026-01-01", "2026-03-31"), args
        assert "sales_agent_ids" not in args and "category_ids" not in args, args
        assert text.startswith("*Top 10 selling items*"), text

    @pytest.mark.parametrize(
        "body,top_n,direction",
        [("top 10 hot selling item", 10, None), ("worst 20 selling bathtub", 20, "bottom"),
         ("best sellers", None, None), ("top sold items", None, None)],
    )
    def test_ranking_words_claim_the_message(self, console_factory, body, top_n, direction) -> None:
        c = console_factory()
        qf = _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="all",
            entities=[], domain_in_message=True, **_RANK_NULLS,
        )
        text, captured = c.say(qf, body)
        assert captured == [], captured
        assert text == ASK_METRIC, text
        assert c.slot.get("top_n") == top_n, c.slot
        assert c.slot.get("rank_direction") == direction, c.slot

    @pytest.mark.parametrize("body", ["what did we sell to hanlim last month", "sold to hanlim", "show me the orders"])
    def test_other_sales_words_are_not_claimed(self, console_factory, body) -> None:
        c = console_factory()
        qf = _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="all",
            entities=[], domain_in_message=True, **_RANK_NULLS,
        )
        text, captured = c.say(qf, body)
        assert c.slot == {}, c.slot
        assert text != ASK_METRIC


class TestR2NoPickerNoOffer:
    def test_no_escalate_offer_on_the_outstanding_report_after_a_ranking(self, console_factory) -> None:
        """"any outstanding?" then "1" with no open sales order: "No open sales order."
        and nothing else (on bb7a7b0c: "Would you like me to escalate to customer service
        team?")."""
        c = console_factory()
        _ranked_by_jayden(c)
        c.report = REPORT_MISS
        c.say(_report("outstanding", continuation=True), "any outstanding?")
        text, captured = c.say(_position(1), "1")
        _clean(text, "1")
        assert "No open sales order." in text, text
        assert c.open_question.get("kind") not in ("team_pick", "member_offer", "company_pick"), c.open_question

    def test_a_miss_inside_a_ranking_has_no_offer(self, console_factory) -> None:
        """A word the ranking hands the order lane anyway (a product it cannot place)
        answers without the offer."""
        c = console_factory()
        _bottom_water_closet(c)
        text, _ = c.say(
            _parser_output(
                message_type="business_query", domain_hint="order", intent_hint="check_order", order_status=None,
                entities=[_e("zzqx", "product")], domain_in_message=None,
            ),
            "zzqx",
        )
        assert "escalate" not in text and "Please choose who to route to" not in text, text
        assert c.open_question.get("kind") not in ("team_pick", "member_offer", "company_pick"), c.open_question

    def test_asking_for_a_person_still_escalates(self, console_factory) -> None:
        c = console_factory()
        _bottom_water_closet(c)
        text, _ = c.say(
            _parser_output(
                message_type="request_for_help", domain_hint=None, intent_hint=None, order_status=None, entities=[],
                routing={"suggested_team": "customer_service", "suggested_agent": None},
            ),
            "can i talk to a person",
        )
        # The person ask goes where it always went (the routing lane), never the ranking.
        assert c.last.branch_kind == "out_of_scope", (text, c.last.branch_kind)


# --------------------------------------------------------------------------- #
# R3: basis words switch the ranking's basis
# --------------------------------------------------------------------------- #


BASIS_READINGS = {
    "first_row_pick": _pick_first,
    "sales_order_list": _so_document,
    "position_one": lambda: _so_document(domain_in_message=False, reference_positions=[1]),
    "affirmative": lambda: _so_document(domain_in_message=False, is_affirmative=True),
}


class TestR3BasisWords:
    @pytest.mark.parametrize("reading", list(BASIS_READINGS))
    @pytest.mark.parametrize(
        "body", ["sales order?", "based on sales order", "by SO", "SO basis", "ordered", "by sales order"]
    )
    def test_the_sales_order_words_switch_to_ordered(self, console_factory, reading, body) -> None:
        c = console_factory()
        _bottom_water_closet(c)
        text, captured = c.say(BASIS_READINGS[reading](), body)
        _clean(text, body)
        (args,) = _calls(captured)
        assert args["basis"] == "ordered", args
        assert "detail_code" not in args, args
        assert args["direction"] == "bottom" and args["rank_by"] == "quantity", args
        assert "Basis: Ordered" in text, text

    @pytest.mark.parametrize("body", ["delivered", "by DO", "DO basis", "based on delivery order"])
    def test_the_delivery_words_switch_back(self, console_factory, body) -> None:
        c = console_factory()
        _bottom_water_closet(c)
        c.say(_so_document(), "based on sales order")
        text, captured = c.say(_report(None, document=["DO"], domain_in_message=True), body)
        _clean(text, body)
        (args,) = _calls(captured)
        assert args.get("basis") in (None, "delivered"), args
        assert "Basis: Delivered (transferred to DO)" in text, text

    def test_can_show_me_the_do_is_still_the_report(self, console_factory) -> None:
        """Round 5 R5: "can show me the DO" asks for the delivery orders, not a basis."""
        c = console_factory()
        _bottom_water_closet(c)
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        text, captured = c.say(_report(None, document=["DO"], domain_in_message=True, **do_window()), "can show me the DO")
        assert _calls(captured) == [], captured
        (args,) = _calls(captured, ORDERS)
        assert "category_ids" not in args, args
        assert "is not a filter for" not in text, text

    def test_a_bare_rank_is_still_a_pick(self, console_factory) -> None:
        c = console_factory()
        _bottom_water_closet(c)
        text, captured = c.say(_position(1), "1")
        (args,) = _calls(captured)
        assert args["detail_code"] == ITEM_CODES[-1], args


# --------------------------------------------------------------------------- #
# R4: sales agent aliases
# --------------------------------------------------------------------------- #


class TestR4Aliases:
    def test_the_alias_names_every_account_of_the_person(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import services as business_services

        seeded = _seed(session_factory, wt_alias="William, Will")
        db = session_factory()
        try:
            for word in ("william", "William", "will", "wt", "WT"):
                got = sorted(i for i, _ in business_services.resolve_sales_agent_token(db, word))
                assert got == seeded.agents("WT"), word
            assert business_services.resolve_sales_agent_token(db, "willi") == []
            assert business_services.resolve_sales_agent_token(db, "liam") == []
        finally:
            db.close()

    def test_sold_by_william_with_the_alias(self, console_factory) -> None:
        c = console_factory(wt_alias="William")
        c.say(_ask(top_n=10, rank_by="quantity"), "top 10 hot selling item")
        text, captured = c.say(_narrow(_e("william", "sales_agent")), "sold by william")
        _clean(text, "sold by william")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), args
        _line(text, "Sales agent", WT)
        assert "I don't know" not in text, text

    def test_top_10_by_william_with_the_alias(self, console_factory) -> None:
        c = console_factory(wt_alias="William")
        _bottom_water_closet(c)
        text, captured = c.say(_william_as_order(), "top 10 hot selling item by william in q1 2026")
        assert text == ASK_METRIC, text
        text, captured = c.say(_answer(rank_by="quantity"), "quantity")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), args
        _line(text, "Sales agent", WT)
        _line(text, "Delivery date", Q1)


class TestR4MasterRecord:
    def test_the_annotation_writes_and_reads_the_aliases(self, session_factory) -> None:
        from app.models.sales_agent import SalesAgent
        from app.schemas.autocount_mirror import SalesAgentAnnotationUpdate, SalesAgentResponse
        from app.services.scm import sales_agent_service

        seeded = _seed(session_factory)
        db = session_factory()
        try:
            agent = db.get(SalesAgent, seeded.ids["WT I"])
            payload = SalesAgentAnnotationUpdate(aliases=" William ,  Will,, ")
            sales_agent_service.annotate(
                db, agent, aliases=payload.aliases, write_aliases="aliases" in payload.model_fields_set
            )
            assert agent.aliases == "William, Will"
            assert SalesAgentResponse.model_validate(agent).aliases == "William, Will"
            sales_agent_service.annotate(db, agent, aliases="  ", write_aliases=True)
            assert agent.aliases is None
            sales_agent_service.annotate(db, agent, person_label="Wong", write_person_label=True)
            assert agent.aliases is None and agent.person_label == "Wong"
        finally:
            db.close()

    def test_the_route_saves_them(self, session_factory) -> None:
        """PATCH /master-data/sales-agents/{id}/annotation carries `aliases`."""
        from app.api.v1.master_data import sales_agents as route_mod

        assert "aliases" in route_mod.SalesAgentAnnotationUpdate.model_fields
        import inspect

        assert "write_aliases" in inspect.getsource(route_mod.annotate_sales_agent)


# --------------------------------------------------------------------------- #
# R5: the owner's transcript, in order
# --------------------------------------------------------------------------- #


class TestOwnerTranscript:
    @pytest.mark.parametrize("alias", [None, "William"], ids=["no_alias_yet", "wt_is_william"])
    def test_replay(self, console_factory, alias) -> None:
        c = console_factory(wt_alias=alias)
        _ranked_by_jayden(c)

        text, _ = c.say(_outstanding_carrying("Jayden"), "any outstanding?")
        _clean(text, "any outstanding?")
        assert "Customer: HANLIM TRADING" in text, text

        c.report = REPORT_MISS
        text, captured = c.say(_position(1), "1")
        _clean(text, "1")
        (args,) = _calls(captured, OUTSTANDING)
        assert sorted(args["customer_ids"]) == c.seeded.customers(*HANLIM), args
        c.report = REPORT_HIT

        _bottom_water_closet(c)

        text, captured = c.say(_year_as_order(2025), "2025?")
        _clean(text, "2025?")
        (args,) = _calls(captured)
        assert args["date_from"] == "2025-01-01", args

        text, captured = c.say(
            _parser_output(
                message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="top_selling",
                entities=[], date_filter_start="2025-07-01", date_filter_end="2025-07-31", **_RANK_NULLS,
            ),
            "july",
        )
        _clean(text, "july")
        (args,) = _calls(captured)
        assert (args["date_from"], args["date_to"]) == ("2025-07-01", "2025-07-31"), args

        text, captured = c.say(_pick_first(), "sales order?")
        _clean(text, "sales order?")
        (args,) = _calls(captured)
        assert args["basis"] == "ordered" and "detail_code" not in args, args

        text, captured = c.say(_william_as_order(), "top 10 hot selling item by william in q1 2026")
        _clean(text, "top 10 ...")
        assert captured == [], captured
        assert text.endswith(ASK_METRIC), text
        assert ("I don't know 'William' as a sales agent." in text) == (alias is None), text

        text, captured = c.say(_so_document(), "based on sales order")
        _clean(text, "based on sales order")
        assert _calls(captured, ORDERS) == [], captured
        assert c.slot.get("basis") == "ordered", c.slot
        assert text == ASK_METRIC, text
        text, captured = c.say(_answer(rank_by="quantity"), "quantity")
        (args,) = _calls(captured)
        assert args["basis"] == "ordered" and args["n"] == 10, args
        assert "Basis: Ordered" in text, text

        text, captured = c.say(_narrow(_e("william", "sales_agent")), "sold by william")
        _clean(text, "sold by william")
        (args,) = _calls(captured)
        if alias:
            assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), args
        else:
            assert text.startswith("I don't know 'william' as a sales agent."), text

        text, captured = c.say(_narrow(_e("wt", "sales_agent")), "sold by wt")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), args
        _line(text, "Sales agent", WT)

        text, captured = c.say(_outstanding_carrying("wt"), "can see its outstiandg?")
        _clean(text, "can see its outstiandg?")
        assert "wt" not in [t.lower() for t in c.asked_tokens()], c.tokens
        # Fix lane round 8: the ordinary outstanding question, no ranking header.
        assert f"Location: all\nOrder date: {Q1}\nOutstanding for which document?" in text, text
        assert "Filters from the ranking" not in text and "is not a filter for" not in text, text
