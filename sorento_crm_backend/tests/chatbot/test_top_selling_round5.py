"""Top selling fix lane round 5: the owner's retest of round 4 (27 Sep 2026, PR #1273).

The owner's PR comment "Owner ruling from the retest of top selling round 4 (27 Sep,
about 11:30 to 14:10 MYT, :3083)" is the work list: five transcripts and nine rulings.
The retest ran the console with parser version 34 (round 3 words) on every turn, so each
message is replayed here twice where it matters: with the verdict the round 4 words
(version 38, `chatbot_parser_prompt.TOP_SELLING_ADDENDUM`) teach for it, and with the
verdict the owner's own trace shows version 34 produced. Both must land the same way.

* R1 "1" / "2" under "customer or sales agent?" binds to that question only;
* R2 the same question answered in words ("yeah sales agent", "fanny sales agent");
* R3 "neither" / "customer is everyone" clears the customer, never picks every row;
* R4 a noisy token ("fanny water closet", "bathtub by sean") splits into its parts;
* R5 "outstanding" / "the DO" / "the orders" after a ranking carries its filters;
* R6 "worst 100 hot selling" is a least sold ranking, never out of scope;
* R7 over a ranked list only a bare 1..N is a rank pick; "2025?" is the year;
* R8 no clarify menu longer than 5 options;
* R9 short plain replies, no snake_case.

Same harness as `test_top_selling_round4.py` (one real `engine.run_turn` per message,
the parser, access and MCP faked, the presenter real). Postgres only, every row seeded
here.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot._turn_helpers import do_window
from tests.chatbot import test_outstanding_lane as outstanding_lane
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import (
    REPORT_HIT,
    _present_response,
    _report_route_body,
    _resolve_services,
    _run_turn,
    _seed_contact,
    _session_of,
)
from tests.chatbot.test_top_selling_round4 import ITEM_CODES, _fake_route

TOOL = "crm_top_selling_report"
OUTSTANDING = "crm_outstanding_report"
ORDERS = "crm_order_management_orders_list"
GRANTS = ["sales_orders.sales_report", "sales_orders.outstanding"]
ASK_METRIC = "By quantity or by amount?"
FANNY_WHO = (
    "Do you mean customer SAMPLE - FANNY NG or sales agent FANNY I, FANNY III, FANNY IV? "
    "Reply 1 for the customer, 2 for the sales agent."
)
SEAN_WHO = (
    "Do you mean a customer named 'sean' or sales agent SEAN I, SEAN III, SEAN IV? "
    "Reply 1 for the customer, 2 for the sales agent."
)
STAFF_CUSTOMERS = [
    "K AND K ENTERPRISE AND HARDWARE", "SAMPLE - SEAN", "STAAFF PURCHASE - SEAN", "STAFF PURCHASE - SEAN",
]


# --------------------------------------------------------------------------- #
# Seeds and the route double
# --------------------------------------------------------------------------- #


class Catalogue:
    """The rows the owner's transcripts name: the agents FANNY I / III / IV and SEAN I /
    III / IV, the customers SAMPLE - FANNY NG, SAMPLE - SEAN and the staff purchase
    ledgers, and the Water Closet / Bathtub categories (names are copies of the codes,
    the words live in `class_label` and `search_synonyms`, as on every live row)."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.fanny_agents: list[str] = []
        self.sean_agents: list[str] = []
        self.customers: dict[str, str] = {}
        self.water_closet: list[str] = []
        self.bathtub: list[str] = []
        self.sorento = ""


def _seed(session_factory) -> Catalogue:
    from app.models.product import Brand, ProductCategory
    from app.models.sales_agent import SalesAgent
    from app.services.product_class_signal import CLASS_SYNONYMS
    from tests._mc_lookup_seed import customer

    cat = Catalogue()
    db = session_factory()
    agents = {
        code: SalesAgent(id=str(uuid.uuid4()), sales_agent=code, person_label=None, company_id=DEFAULT_COMPANY_ID)
        for code in ("FANNY I", "FANNY III", "FANNY IV", "SEAN I", "SEAN III", "SEAN IV")
    }
    sorento = Brand(id=str(uuid.uuid4()), brand_code="SRT", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    categories = [
        ProductCategory(
            id=str(uuid.uuid4()), category_code=code, category_name=code, class_label=label,
            search_synonyms=CLASS_SYNONYMS[label], company_id=DEFAULT_COMPANY_ID,
        )
        for code, label in (("SRT-WC", "Water Closet"), ("CBN-WC", "Water Closet"), ("SRT-BT", "Bathtub"))
    ]
    db.add_all([*agents.values(), sorento, *categories])
    db.flush()
    for name in ("SAMPLE - FANNY NG", *STAFF_CUSTOMERS):
        cid = str(customer(db, company_id=DEFAULT_COMPANY_ID, name=name).id)
        cat.customers[name] = cid
        cat.names[cid] = name
    db.commit()
    for code, agent in agents.items():
        cat.names[str(agent.id)] = code
        (cat.fanny_agents if code.startswith("FANNY") else cat.sean_agents).append(str(agent.id))
    cat.sorento = str(sorento.id)
    cat.names[cat.sorento] = "Sorento"
    cat.water_closet = [str(c.id) for c in categories[:2]]
    cat.bathtub = [str(categories[2].id)]
    for cid in cat.water_closet:
        cat.names[cid] = "Water Closet"
    cat.names[cat.bathtub[0]] = "Bathtub"
    return cat


ORDERS_BODY = {
    "data": [
        {"order_number": "DO-26-0001", "debtor_name": "SAMPLE - SEAN", "order_date": "2026-03-01", "order_status": "Delivered"},
    ],
    "total": 1,
    "page": 1,
    "limit": 20,
}


@pytest.fixture
def route(monkeypatch):
    """The harness MCP double: the ranking through `_fake_route`, the outstanding report
    and the order list through the REAL presenter."""

    class _Route:
        names: dict[str, str] = {}
        codes: list[str] = list(ITEM_CODES)
        captured: list[tuple[str, dict[str, Any]]] = []

    state = _Route()
    present = _present_response()

    def _factory(_response: Any = None):
        captured: list[tuple[str, dict[str, Any]]] = []
        state.captured = captured

        def _call(name: str, args: dict[str, Any]) -> Any:
            captured.append((name, dict(args)))
            if name == TOOL:
                body = _fake_route(args, state.names, state.codes)
                body["date_from"] = args.get("date_from") or body["date_from"]
                body["date_to"] = args.get("date_to") or body["date_to"]
                return present(name, json.dumps(body))
            if name == OUTSTANDING:
                return present(name, json.dumps(_report_route_body(REPORT_HIT, args)))
            if name == ORDERS:
                return present(name, json.dumps(ORDERS_BODY))
            return json.dumps({"has_result": False, "items": []})

        return _call, captured

    monkeypatch.setattr(outstanding_lane, "_capturing_mcp", _factory)
    return state


@pytest.fixture
def cat(session_factory, route) -> Catalogue:
    _seed_contact(session_factory, variables={})
    seeded = _seed(session_factory)
    route.names = seeded.names
    return seeded


# --------------------------------------------------------------------------- #
# Parser verdicts
# --------------------------------------------------------------------------- #


def _e(raw: str, hint: str) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True, "hint_confident": True}


_RANK_NULLS = dict(rank_by=None, basis=None, rank_group=None, rank_direction=None, top_n=None)


def _ask(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="top_selling", entities=[],
        domain_in_message=True, **_RANK_NULLS,
    )
    base.update(overrides)
    return _parser_output(**base)


def _answer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="top_selling", entities=[],
        domain_in_message=None, **_RANK_NULLS,
    )
    base.update(overrides)
    return _parser_output(**base)


def _narrow(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The round 4 words' NARROWING A TOP SELLING ASK verdict."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        order_status="top_selling", entities=list(entities), domain_in_message=False, **_RANK_NULLS,
    )
    base.update(overrides)
    return _parser_output(**base)


def _position(n: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        reference_positions=[n], order_status=None,
    )
    base.update(overrides)
    return _parser_output(**base)


def _low_signal() -> dict[str, Any]:
    return _parser_output(
        message_type="low_signal", intent_hint=None, domain_hint=None, entities=[], order_status=None,
    )


def _report(order_status: str | None, **overrides: Any) -> dict[str, Any]:
    """"outstanding", "can show me the DO", "show me the orders": an order report ask
    naming no filter of its own."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        order_status=order_status, entities=[], domain_in_message=True,
    )
    base.update(overrides)
    return _parser_output(**base)


def _out_of_scope() -> dict[str, Any]:
    return _parser_output(
        message_type="out_of_scope", intent_hint=None, domain_hint=None, entities=[], order_status=None,
    )


def _year(year: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order", order_status=None,
        entities=[], date_filter_start=f"{year}-01-01", date_filter_end=f"{year}-12-31", domain_in_message=None,
    )
    base.update(overrides)
    return _parser_output(**base)


# --------------------------------------------------------------------------- #
# The turn
# --------------------------------------------------------------------------- #


_SNAKE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")


def _turn(session_factory, monkeypatch, qf, body: str, *, matches=None, services=None) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body,
        msg_id=f"ZZT-top-selling-r5-{uuid.uuid4().hex[:10]}", attributes=GRANTS,
        resolve_services=services or _resolve_services(matches or {}),
    )
    reply = dict(result.reply or {})
    text = str(reply.get("text") or "")
    assert not _SNAKE.search(text), (body, _SNAKE.search(text))
    assert "\u2014" not in text and "\u2013" not in text, body
    return text, list(captured)


def _calls(captured, name: str = TOOL) -> list[dict[str, Any]]:
    return [args for n, args in captured if n == name]


def _open_question(session_factory) -> dict[str, Any]:
    return _session_of(session_factory).get("open_question") or {}


def _ranked(session_factory, monkeypatch) -> None:
    _turn(session_factory, monkeypatch, _ask(top_n=100), "top 100 hot selling item")
    _turn(session_factory, monkeypatch, _answer(rank_by="quantity"), "qty")


def _staff_picker(customers: dict[str, str]):
    """The resolver double for a customer word naming the four staff ledgers (the
    picker transcript 3 opens on)."""
    def _resolve(body):
        asked = list(body.get("tokens") or [])
        return {
            "tokens": asked,
            "resolutions": [
                {
                    "raw": raw, "token": raw,
                    "matches": [
                        {"entity_type": "customer", "canonical_code": name, "uuid": customers[name], "match_tier": "fuzzy"}
                        for name in STAFF_CUSTOMERS
                    ],
                }
                for raw in asked if raw == "staff"
            ],
            "unresolved_tokens": [raw for raw in asked if raw != "staff"],
        }

    services = _resolve_services({})
    from tests.chatbot.test_outstanding_lane import validating_resolve_entity

    return services.__class__(access_types=services.access_types, resolve_entity=validating_resolve_entity(_resolve), probe=services.probe)




def _line(label: str, value: str) -> str:
    return f"\n{label}: {value}\n"


def _header(text: str, **axes: str) -> None:
    """The ranking's filter lines: `Customer="all"` asserts "\\nCustomer: all\\n"."""
    labels = {"customer": "Customer", "category": "Category", "brand": "Brand", "agent": "Sales agent", "date": "Delivery date"}
    for key, value in axes.items():
        assert _line(labels[key], value) in text, (labels[key], value, text)


FANNY = "FANNY I, FANNY III, FANNY IV"
SEAN = "SEAN I, SEAN III, SEAN IV"
YEAR = "01/01/2026 to 31/12/2026"
NEVER = ("Could not find", "Couldn't find", "escalate", "Which one do you mean?", "Hi! How can I help", "out of the scope")


# --------------------------------------------------------------------------- #
# The owner's five transcripts, in order
# --------------------------------------------------------------------------- #


class Replay:
    """Runs a transcript: each step is (verdict, message, check). With
    `ROUND5_REPORT=1` in the environment a failing check is recorded and the replay
    goes on (how the before-fix column of the PR table was measured); otherwise the
    first failure fails the test."""

    def __init__(self, session_factory, monkeypatch, **kw) -> None:
        import os

        self.session_factory, self.monkeypatch, self.kw = session_factory, monkeypatch, kw
        self.report = os.environ.get("ROUND5_REPORT") == "1"
        self.results: list[tuple[str, str]] = []

    def say(self, qf, body: str, check=None, *, setup: bool = False):
        text, captured = _turn(self.session_factory, self.monkeypatch, qf, body, **self.kw)
        if check is None:
            return text, captured
        try:
            for bad in NEVER:
                assert bad not in text, (body, bad, text)
            check(text, captured)
            outcome = "pass"
        except (AssertionError, ValueError) as exc:
            if not self.report:
                raise
            outcome = f"FAIL {str(exc)[:160]!r}"
        if not setup:
            self.results.append((body, outcome))
            print(f"REPLAY {body!r}: {outcome}")
        return text, captured


class TestOwnerTranscripts:
    def test_transcript_1(self, session_factory, monkeypatch, cat) -> None:
        """"product category is water closet, product is everything, agent is fanny" then
        "2" then "outstanding". The ask says "agent" next to the name: the agent, no
        question (R2), the category kept; "2" is then rank 2 of the list on screen (R7);
        "outstanding" carries the filters (R5)."""
        r = Replay(session_factory, monkeypatch)
        r.say(_ask(top_n=100), "top 100 hot selling item")
        r.say(_answer(rank_by="quantity"), "qty")

        def ask(text, captured):
            (args,) = _calls(captured)
            assert sorted(args["sales_agent_ids"]) == sorted(cat.fanny_agents)
            assert sorted(args["category_ids"]) == sorted(cat.water_closet)
            assert "customer_ids" not in args
            _header(text, customer="all", category="Water Closet", agent=FANNY, date=YEAR)

        # version 34 hinted "fanny" a customer; the round 4 words hint it an agent
        r.say(
            _narrow(_e("water closet", "category"), _e("fanny", "customer")),
            "product category is water closet, product is everything, agent is fanny", ask,
        )

        def two(text, captured):
            (args,) = _calls(captured)
            assert args["detail_code"] == ITEM_CODES[1]
            assert sorted(args["sales_agent_ids"]) == sorted(cat.fanny_agents)
            assert text.startswith(f"*{ITEM_CODES[1]}: customers and months*")

        r.say(_position(2), "2", two)

        def outstanding(text, captured):
            # Fix lane round 8: the ordinary outstanding question over the ranked codes.
            assert captured == [], captured
            assert text.startswith(f"Product: {', '.join(ITEM_CODES)}\nCustomer: all\nLocation: all\n"), text
            assert "Outstanding for which document?" in text
            assert "Filters from the ranking" not in text and "is not a filter for" not in text

        r.say(_report("outstanding"), "outstanding", outstanding)

    def test_transcript_2(self, session_factory, monkeypatch, cat) -> None:
        r = Replay(session_factory, monkeypatch)

        def who(text, captured):
            assert text == SEAN_WHO and not _calls(captured)

        r.say(
            _ask(top_n=100, entities=[_e("bathtub", "category"), _e("sean", "customer")]),
            "top 100 hot selling bathtub by sean", who,
        )

        def agent_set(text, captured):
            assert text == ASK_METRIC and not _calls(captured)

        # both versions read "yeah sales agent" as low_signal (the owner's trace)
        r.say(_low_signal(), "yeah sales agent", agent_set)

        def repeat(text, captured):
            assert text == ASK_METRIC, "the agent is set, only the metric is asked"

        r.say(
            _ask(top_n=100, entities=[_e("bathtub", "category"), _e("sean", "customer")]),
            "top 100 hot selling bathtub by sean salea agent", repeat,
        )

        def ranked(text, captured):
            (args,) = _calls(captured)
            assert sorted(args["sales_agent_ids"]) == sorted(cat.sean_agents)
            assert "customer_ids" not in args and args["category_ids"] == cat.bathtub
            _header(text, customer="all", category="Bathtub", agent=SEAN)

        r.say(_answer(rank_by="amount"), "amount", ranked)

    def test_transcript_3_and_4(self, session_factory, monkeypatch, cat) -> None:
        r = Replay(session_factory, monkeypatch, services=_staff_picker(cat.customers))
        text, _ = r.say(
            _ask(top_n=100, rank_by="quantity", entities=[_e("bathtub", "category"), _e("staff", "customer")]),
            "top 100 hot selling bathtub by quantity for staff",
        )
        assert text.startswith("Which customer do you mean?")

        def neither(text, captured):
            (args,) = _calls(captured)
            assert "customer_ids" not in args and "customer_query" not in args
            assert sorted(args["sales_agent_ids"]) == sorted(cat.sean_agents)
            _header(text, customer="all", category="Bathtub", agent=SEAN)

        # version 34 hinted "sean" a customer, the round 4 words an agent
        r.say(_narrow(_e("sean", "customer"), correction=True), "neither, i mean sales agent sean", neither)
        r.say(_narrow(correction=True, broaden_axis="customer", broaden_to="all"), "customer is everyone", neither)

        def do(text, captured):
            assert _calls(captured, ORDERS) and not _calls(captured)
            (args,) = _calls(captured, ORDERS)
            assert "customer_ids" not in args
            assert args["actual_delivery_date_from"] == "2026-01-01" and args["actual_delivery_date_to"] == "2026-01-31"
            # Fix lane round 8: the filters carry silently.
            assert "Filters from the ranking" not in text and "is not a filter for" not in text
            assert "Dates: 01/01/2026 to 31/01/2026" in text

        # Dated: the carried calendar year is over the 31-day cap and a dateless DO list ask asks
        # which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026), so this step names January.
        r.say(_report(None, document=["DO"], date_filter_start="2026-01-01", date_filter_end="2026-01-31"), "can show me the DO", do)

        def worst(text, captured):
            assert text == ASK_METRIC

        r.say(
            _ask(top_n=100, rank_direction="bottom", entities=[_e("bathtub", "category")]),
            "worsr 100 hot selling bathtub", worst,
        )

        def bottom(text, captured):
            (args,) = _calls(captured)
            assert args["direction"] == "bottom" and args["n"] == 100 and args["category_ids"] == cat.bathtub
            assert text.startswith("*Bottom 100 selling items*")

        r.say(_answer(rank_by="amount"), "amount", bottom)

    def test_transcript_5(self, session_factory, monkeypatch, cat) -> None:
        r = Replay(session_factory, monkeypatch)
        r.say(
            _ask(top_n=100, rank_direction="bottom", entities=[_e("water closet", "category")]),
            "worst 100 hot selling water closet",
        )

        def amount(text, captured):
            (args,) = _calls(captured)
            assert args["direction"] == "bottom" and args["rank_by"] == "amount"
            assert text.startswith("*Bottom 100 selling items*")
            _header(text, category="Water Closet", date=YEAR)

        r.say(_answer(rank_by="amount"), "amount", amount)

        def year(text, captured):
            (args,) = _calls(captured)
            assert args["date_from"] == "2025-01-01" and args["date_to"] == "2025-12-31"
            assert "detail_code" not in args and args["direction"] == "bottom" and args["n"] == 100
            _header(text, category="Water Closet", date="01/01/2025 to 31/12/2025")
            assert text.startswith("*Bottom 100 selling items*")

        # a bare number after a ranked list is a position under both versions' words
        r.say(_position(2025), "2025?", year)
        r.say(_year(2025), "i mena in year 2025", year)


# --------------------------------------------------------------------------- #
# R1 / R2 the answer to "customer or sales agent?"
# --------------------------------------------------------------------------- #


def _asked_who(session_factory, monkeypatch, *, rank_by: str | None = "amount") -> str:
    """"top 100 hot selling water closet by fanny": the customer or agent question, the
    ask already naming the category, the count and (by default) the metric."""
    text, captured = _turn(
        session_factory, monkeypatch,
        _ask(top_n=100, rank_by=rank_by, entities=[_e("water closet", "category"), _e("fanny", "customer")]),
        "top 100 hot selling water closet by fanny",
    )
    assert text == FANNY_WHO and not _calls(captured)
    return text


_PROMOTION_MISREAD = _parser_output(
    message_type="business_query", domain_hint="promotion", intent_hint="check_promotion",
    entities=[_e("fanny water closet", "promotion")], order_status=None,
)


class TestR1TheAnswerBindsToTheQuestion:
    @pytest.mark.parametrize(
        "qf",
        [_position(2), _PROMOTION_MISREAD, _low_signal()],
        ids=["round 4 words: a position", "version 34: a promotion lookup", "a low signal reading"],
    )
    def test_two_sets_the_agent_and_runs_the_ranking(self, session_factory, monkeypatch, cat, qf) -> None:
        """Transcript 1: "2" picked both the agent and the customer, then drifted to a
        promotion lookup of "fanny water closet"."""
        _asked_who(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, qf, "2")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == sorted(cat.fanny_agents)
        assert "customer_ids" not in args and "customer_query" not in args
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        assert args["n"] == 100 and args["rank_by"] == "amount"
        _header(text, customer="all", category="Water Closet", agent=FANNY)
        assert not any(bad in text for bad in NEVER), text

    def test_one_sets_the_customer_and_clears_the_agent(self, session_factory, monkeypatch, cat) -> None:
        _asked_who(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, _position(1), "1")
        (args,) = _calls(captured)
        assert args["customer_ids"] == [cat.customers["SAMPLE - FANNY NG"]]
        assert "sales_agent_ids" not in args
        _header(text, customer="SAMPLE - FANNY NG", category="Water Closet", agent="all")

    def test_with_no_metric_the_answer_leads_to_the_metric_question(self, session_factory, monkeypatch, cat) -> None:
        _asked_who(session_factory, monkeypatch, rank_by=None)
        text, captured = _turn(session_factory, monkeypatch, _position(2), "2")
        assert text == ASK_METRIC and not _calls(captured)
        text, captured = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amount")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == sorted(cat.fanny_agents) and "customer_ids" not in args

    def test_the_question_is_answered_once(self, session_factory, monkeypatch, cat) -> None:
        """After the answer a "2" is rank 2 of the list the ranking printed."""
        _asked_who(session_factory, monkeypatch)
        _turn(session_factory, monkeypatch, _position(2), "2")
        text, captured = _turn(session_factory, monkeypatch, _position(2), "2")
        (args,) = _calls(captured)
        assert args["detail_code"] == ITEM_CODES[1]


class TestR2AnswersInWords:
    @pytest.mark.parametrize(
        "message, qf",
        [
            ("yeah sales agent", _low_signal()),
            ("sales agent", _low_signal()),
            ("i mean sales agent sean", _narrow(_e("sean", "sales_agent"), correction=True)),
            ("neither, i mean sales agent sean", _narrow(_e("sean", "customer"), correction=True, is_affirmative=False)),
            ("fanny sales agent", _narrow(_e("fanny sales agent", "customer"))),
            ("yeah sales agent", _position(2, message_type="business_query", order_status="top_selling", domain_hint="order")),
        ],
    )
    def test_words_answer_like_two(self, session_factory, monkeypatch, cat, message, qf) -> None:
        _asked_who(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, qf, message)
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == sorted(cat.fanny_agents)
        assert "customer_ids" not in args
        _header(text, customer="all", category="Water Closet", agent=FANNY)
        assert "Reply 1 for the customer" not in text

    def test_customer_in_words_answers_like_one(self, session_factory, monkeypatch, cat) -> None:
        _asked_who(session_factory, monkeypatch)
        _text, captured = _turn(session_factory, monkeypatch, _low_signal(), "the customer")
        (args,) = _calls(captured)
        assert args["customer_ids"] == [cat.customers["SAMPLE - FANNY NG"]] and "sales_agent_ids" not in args

    @pytest.mark.parametrize(
        "message",
        ["top 100 hot selling bathtub by sean salea agent", "top 100 hot selling bathtub by sean sales agent",
         "top 100 hot selling bathtub by sean agent"],
    )
    def test_agent_next_to_the_name_sets_the_agent_and_asks_nothing(self, session_factory, monkeypatch, cat, message) -> None:
        text, captured = _turn(
            session_factory, monkeypatch,
            _ask(top_n=100, rank_by="amount", entities=[_e("bathtub", "category"), _e("sean", "customer")]),
            message,
        )
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == sorted(cat.sean_agents) and "customer_ids" not in args
        _header(text, customer="all", category="Bathtub", agent=SEAN)

    def test_the_prompt_teaches_the_answers(self) -> None:
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        for words in ('"yeah sales agent"', '"neither,\n    i mean sales agent sean"', '"fanny sales agent"',
                      "reference_positions [2]", "NEVER low_signal", '"by sean salea agent"'):
            assert words in TOP_SELLING_ADDENDUM, words


# --------------------------------------------------------------------------- #
# R3 corrections
# --------------------------------------------------------------------------- #


class TestR3Corrections:
    def _picker(self, session_factory, monkeypatch, cat):
        services = _staff_picker(cat.customers)
        text, _ = _turn(
            session_factory, monkeypatch,
            _ask(top_n=100, rank_by="quantity", entities=[_e("bathtub", "category"), _e("staff", "customer")]),
            "top 100 hot selling bathtub by quantity for staff", services=services,
        )
        assert text.startswith("Which customer do you mean?")
        return services

    @pytest.mark.parametrize(
        "message, qf",
        [
            ("customer is everyone", _narrow(correction=True, broaden_axis="customer", broaden_to="all")),
            ("all customers", _narrow(broaden_axis="customer", broaden_to="all")),
            ("customer is everyone", _parser_output(message_type="casual", entities=[], broaden_axis="all", broaden_to="all", entity_op="clear")),
            ("neither", _parser_output(message_type="casual", entities=[], is_affirmative=False)),
        ],
    )
    def test_everyone_over_the_picker_clears_the_customer(self, session_factory, monkeypatch, cat, message, qf) -> None:
        """Transcript 3: "customer is everyone" set all four picker rows as the filter."""
        services = self._picker(session_factory, monkeypatch, cat)
        text, captured = _turn(session_factory, monkeypatch, qf, message, services=services)
        (args,) = _calls(captured)
        assert "customer_ids" not in args and "customer_query" not in args
        _header(text, customer="all", category="Bathtub")
        assert _open_question(session_factory).get("kind") != "customer_pick"

    def test_version_34_neither_then_everyone(self, session_factory, monkeypatch, cat) -> None:
        """The owner's order: "neither, i mean sales agent sean" (read as a customer
        word by version 34) set Customer SAMPLE - SEAN; "customer is everyone" then set
        all four. Now: the agent, and the customer stays all."""
        services = self._picker(session_factory, monkeypatch, cat)
        text, captured = _turn(
            session_factory, monkeypatch, _narrow(_e("sean", "customer"), correction=True),
            "neither, i mean sales agent sean", services=services,
        )
        (args,) = _calls(captured)
        assert "customer_ids" not in args and sorted(args["sales_agent_ids"]) == sorted(cat.sean_agents)
        text, captured = _turn(
            session_factory, monkeypatch, _narrow(broaden_axis="customer", broaden_to="all"),
            "customer is everyone", services=services,
        )
        (args,) = _calls(captured)
        assert "customer_ids" not in args and sorted(args["sales_agent_ids"]) == sorted(cat.sean_agents)
        _header(text, customer="all", agent=SEAN)

    def test_neither_under_the_who_question_sets_neither(self, session_factory, monkeypatch, cat) -> None:
        _asked_who(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, _parser_output(message_type="casual", entities=[], is_affirmative=False), "neither")
        (args,) = _calls(captured)
        assert "customer_ids" not in args and "sales_agent_ids" not in args
        _header(text, customer="all", agent="all", category="Water Closet")


# --------------------------------------------------------------------------- #
# R4 noisy tokens
# --------------------------------------------------------------------------- #


class TestR4NoisyTokens:
    @pytest.mark.parametrize("hint", ["category", "customer", "promotion", "sales_agent"])
    def test_fanny_water_closet_splits(self, session_factory, monkeypatch, cat, hint) -> None:
        _ranked(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, _narrow(_e("fanny water closet", hint)), "fanny water closet")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == sorted(cat.fanny_agents)
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        assert "customer_ids" not in args
        _header(text, customer="all", category="Water Closet", agent=FANNY)
        assert not any(bad in text for bad in NEVER), text

    @pytest.mark.parametrize("message, raw", [("bathtub by sean", "bathtub by sean"), ("sean bathtub", "sean bathtub")])
    def test_bathtub_and_sean(self, session_factory, monkeypatch, cat, message, raw) -> None:
        _ranked(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, _narrow(_e(raw, "customer")), message)
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == sorted(cat.sean_agents)
        assert args["category_ids"] == cat.bathtub and "customer_ids" not in args
        _header(text, customer="all", category="Bathtub", agent=SEAN)

    def test_one_unknown_leftover_is_said_once_and_the_ranking_runs(self, session_factory, monkeypatch, cat) -> None:
        _ranked(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, _narrow(_e("fanny marble water closet", "category")), "fanny marble water closet")
        (args,) = _calls(captured)
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        assert text.startswith("I don't know 'marble'.\n\n*Top 100 selling items*")
        text, _ = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "by amount")
        assert "I don't know" not in text

    def test_a_whole_name_is_not_split(self, session_factory, monkeypatch, cat) -> None:
        """"SAMPLE - FANNY NG" is one customer and "water tap" one class, never split."""
        _ranked(session_factory, monkeypatch)
        _text, captured = _turn(session_factory, monkeypatch, _narrow(_e("SAMPLE - FANNY NG", "customer")), "for SAMPLE - FANNY NG")
        assert not any("sales_agent_ids" in a for a in _calls(captured))

    def test_the_prompt_teaches_the_split(self) -> None:
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        assert '"fanny water closet" -> {raw: "fanny", hint: "sales_agent"}' in TOP_SELLING_ADDENDUM


# --------------------------------------------------------------------------- #
# R5 continuity across report kinds
# --------------------------------------------------------------------------- #


def _ranked_with_filters(session_factory, monkeypatch, cat, **window: str) -> None:
    """A ranking narrowed by agent FANNY, category Water Closet and brand Sorento. `window`
    names the ranking's own dates; the default is the calendar year the lane injects."""
    _turn(
        session_factory, monkeypatch,
        _ask(top_n=100, rank_by="amount", entities=[_e("water closet", "category"), _e("fanny", "sales_agent"), _e("sorento", "brand")], **window),
        "top 100 hot selling water closet sold by fanny sorento brand by amount",
    )


class TestR5Continuity:
    """Superseded in part by fix lane round 8 (owner hand test, 28 Sep 2026): a report
    after a ranking carries the ranking's filters silently (no "Filters from the
    ranking" header, no "... is not a filter for ..." line), and an OUTSTANDING ask is
    the ordinary outstanding question over the ranked codes (`test_top_selling_round8`)."""

    @pytest.mark.parametrize(
        "message, qf",
        [
            ("outstanding", _report("outstanding")),
            ("the outstanding", _report("outstanding")),
            ("show me outstanding", _report("outstanding")),
        ],
    )
    def test_an_outstanding_ask_after_a_ranking_is_the_ordinary_question(self, session_factory, monkeypatch, cat, message, qf) -> None:
        _ranked_with_filters(session_factory, monkeypatch, cat)
        text, captured = _turn(session_factory, monkeypatch, qf, message)
        assert captured == [], captured
        assert text.startswith(
            f"Product: {', '.join(ITEM_CODES)}\nCustomer: all\nLocation: all\nOrder date: 01/01/2026 to 31/12/2026\n"
            "Outstanding for which document?"
        ), text
        assert "Filters from the ranking" not in text and "is not a filter for" not in text, text
        assert not any(bad in text for bad in NEVER), text
        assert _open_question(session_factory).get("kind") == "outstanding_scope"

    @pytest.mark.parametrize(
        "message, qf",
        [
            ("can show me the DO", _report(None, document=["DO"])),
            ("the DO", _report(None, document=["DO"])),
            ("show me the orders", _report(None)),
        ],
    )
    def test_a_report_after_a_ranking_carries_its_filters(self, session_factory, monkeypatch, cat, message, qf) -> None:
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        # A ranking over a whole year would carry a range over the 31-day cap and be refused.
        _ranked_with_filters(session_factory, monkeypatch, cat, date_filter_start="2026-01-01", date_filter_end="2026-01-31")
        text, captured = _turn(session_factory, monkeypatch, qf, message)
        assert not _calls(captured)
        (args,) = _calls(captured, ORDERS)
        assert args["actual_delivery_date_from"] == "2026-01-01"
        assert "category_ids" not in args and "sales_agent_ids" not in args and "brand_ids" not in args, args
        assert "Filters from the ranking" not in text and "is not a filter for" not in text, text
        assert not any(bad in text for bad in NEVER), text
        assert _open_question(session_factory).get("kind") not in ("member_offer", "team_pick")

    def test_the_ranking_brand_is_not_the_order_brand_carry(self, session_factory, monkeypatch, cat) -> None:
        # Main merge (#1262 brand carry): the ranking's "sorento brand" narrows the
        # ranking only (`top_selling.brand_ids`); it never lands on the outstanding
        # report's own brand carry, where it outlived the ranking.
        _ranked_with_filters(session_factory, monkeypatch, cat)
        focus = _session_of(session_factory).get("focus") or {}
        assert (focus.get("top_selling") or {}).get("brand_ids"), focus
        assert not focus.get("outstanding_brand_ids"), focus

    def test_the_customer_carries(self, session_factory, monkeypatch, cat) -> None:
        _asked_who(session_factory, monkeypatch)
        _turn(session_factory, monkeypatch, _position(1), "1")
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        text, captured = _turn(session_factory, monkeypatch, _report(None, document=["DO"], **do_window()), "can show me the DO")
        (args,) = _calls(captured, ORDERS)
        assert args["customer_ids"] == [cat.customers["SAMPLE - FANNY NG"]]
        assert "Filters from the ranking" not in text

    def test_a_named_month_in_a_ranking_carries_to_the_next_report(self, session_factory, monkeypatch, cat) -> None:
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        # January 2025, not the whole year: a range over the 31-day cap is refused, not carried.
        _turn(session_factory, monkeypatch, _ask(top_n=10, rank_by="amount", date_filter_start="2025-01-01", date_filter_end="2025-01-31"), "top 10 hot selling in 2025 by amount")
        text, captured = _turn(session_factory, monkeypatch, _report(None, document=["DO"]), "can show me the DO")
        (args,) = _calls(captured, ORDERS)
        assert args["actual_delivery_date_from"] == "2025-01-01" and args["actual_delivery_date_to"] == "2025-01-31"
        assert "Filters from the ranking" not in text

    def test_the_next_report_carries_the_same_and_a_new_ranking_starts_over(self, session_factory, monkeypatch, cat) -> None:
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        _ranked_with_filters(session_factory, monkeypatch, cat, date_filter_start="2026-01-01", date_filter_end="2026-01-31")
        _turn(session_factory, monkeypatch, _report(None), "show me the orders")
        text, captured = _turn(session_factory, monkeypatch, _report(None, document=["DO"]), "can show me the DO")
        (args,) = _calls(captured, ORDERS)
        assert args["actual_delivery_date_from"] == "2026-01-01" and "category_ids" not in args, args
        assert "is not a filter for" not in text
        text, captured = _turn(session_factory, monkeypatch, _ask(top_n=10, rank_by="amount"), "top 10 hot selling by amount")
        (args,) = _calls(captured)
        assert "category_ids" not in args and "sales_agent_ids" not in args and "Filters from the ranking" not in text

    def test_the_prompt_teaches_the_hop(self) -> None:
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        for words in ('"can show me the DO"', '"show me the orders"', '"the outstanding"', "FROM A RANKING TO ANOTHER ORDER REPORT"):
            assert words in TOP_SELLING_ADDENDUM, words


# --------------------------------------------------------------------------- #
# R6 worst selling, never out of scope
# --------------------------------------------------------------------------- #


class TestR6WorstSelling:
    @pytest.mark.parametrize("message", ["worst 100 hot selling bathtub", "worse 100 hot selling", "bottom 100"])
    def test_worst_is_the_least_sold_ranking(self, session_factory, monkeypatch, cat, message) -> None:
        _turn(session_factory, monkeypatch, _ask(top_n=100, rank_direction="bottom"), message)
        text, captured = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amount")
        (args,) = _calls(captured)
        assert args["direction"] == "bottom" and args["n"] == 100
        assert text.startswith("*Bottom 100 selling items*")

    def test_out_of_scope_inside_a_ranking_asks_one_short_question(self, session_factory, monkeypatch, cat) -> None:
        """Transcript 4: version 34 read "worsr 100 hot selling bathtub" out of scope and
        routed it to customer service."""
        _ranked(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, _out_of_scope(), "worsr 100 hot selling bathtub")
        assert text == "Sorry, I didn't get that. What would you like to change in the ranking?"
        assert not _calls(captured)
        assert _open_question(session_factory).get("kind") not in ("member_offer", "team_pick")
        # the ranking is still there for the next message
        _text, captured = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amount")
        (args,) = _calls(captured)
        assert args["rank_by"] == "amount" and args["n"] == 100

    def test_the_prompt_teaches_worst(self) -> None:
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        for words in ('"worst 100 hot selling bathtub"', '"worse 100 hot selling"', '"bottom\n    100"', "NEVER out_of_scope"):
            assert words in TOP_SELLING_ADDENDUM, words


# --------------------------------------------------------------------------- #
# R7 rank picks, R8 short menus, R9 plain replies
# --------------------------------------------------------------------------- #


class TestR7RankPicks:
    @pytest.mark.parametrize(
        "message, qf",
        [
            ("2025?", _position(2025)),
            ("2025", _position(2025)),
            ("2025?", _parser_output(message_type="clarification", entities=[], reference_positions=[2025])),
            ("in 2025", _year(2025)),
            ("what about 2025", _year(2025)),
        ],
    )
    def test_a_year_changes_the_period(self, session_factory, monkeypatch, cat, message, qf) -> None:
        _ranked(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, qf, message)
        (args,) = _calls(captured)
        assert args["date_from"] == "2025-01-01" and args["date_to"] == "2025-12-31"
        assert "detail_code" not in args and args["n"] == 100 and args["rank_by"] == "quantity"
        _header(text, date="01/01/2025 to 31/12/2025")
        assert "Which one do you mean?" not in text

    @pytest.mark.parametrize("message, n", [("8", 8), ("150", 150), ("2?", 2), ("show 3 please", 3)])
    def test_only_a_bare_number_within_the_list_is_a_pick(self, session_factory, monkeypatch, cat, message, n) -> None:
        """The list has seven rows: "8", "150", "2?" and "show 3 please" are not picks."""
        _ranked(session_factory, monkeypatch)
        text, captured = _turn(session_factory, monkeypatch, _position(n), message)
        assert all("detail_code" not in a for a in _calls(captured))
        assert "Which one do you mean?" not in text
        assert len([ln for ln in text.split("\n") if re.match(r"^\d+\. ", ln)]) <= len(ITEM_CODES)

    def test_a_bare_number_within_the_list_is_a_pick(self, session_factory, monkeypatch, cat) -> None:
        _ranked(session_factory, monkeypatch)
        _text, captured = _turn(session_factory, monkeypatch, _position(7), "7")
        (args,) = _calls(captured)
        assert args["detail_code"] == ITEM_CODES[6]

    def test_the_prompt_teaches_the_year(self) -> None:
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        assert '"2025?"' in TOP_SELLING_ADDENDUM and "NEVER reference_positions" in TOP_SELLING_ADDENDUM


class TestR8ShortMenus:
    def test_a_ranked_list_is_never_reprinted_whole(self) -> None:
        from app.services.chatbot.turn import compose, pending as turn_pending
        from app.services.chatbot.turn.state import Focus, State

        rows = [{"idx": i, "label": f"CODE{i}", "code": f"CODE{i}"} for i in range(1, 101)]
        pending = turn_pending.top_selling_pick(rows, asked_at_turn=1, filters={})
        state = State(focus=Focus(status="top_selling", top_selling={"rank_by": "amount"}), pending=pending)
        answer = compose.compose_question(pending, state)
        assert answer.text == "Which item do you mean? Reply with a rank number from 1 to 100."
        assert answer.actions[0]["result_set"] == pending.options

    def test_a_ranking_picker_lists_at_most_five(self, session_factory, monkeypatch, cat) -> None:
        many = [f"SEAN TRADING {i}" for i in range(1, 9)]

        def _resolve(body):
            asked = list(body.get("tokens") or [])
            return {
                "tokens": asked,
                "resolutions": [
                    {"raw": raw, "token": raw, "matches": [
                        {"entity_type": "customer", "canonical_code": n, "uuid": str(uuid.uuid4()), "match_tier": "fuzzy"} for n in many
                    ]}
                    for raw in asked if raw == "trading"
                ],
                "unresolved_tokens": [raw for raw in asked if raw != "trading"],
            }

        from tests.chatbot.test_outstanding_lane import validating_resolve_entity

        base = _resolve_services({})
        services = base.__class__(access_types=base.access_types, resolve_entity=validating_resolve_entity(_resolve), probe=base.probe)
        text, _ = _turn(
            session_factory, monkeypatch, _ask(top_n=10, rank_by="amount", entities=[_e("trading", "customer")]),
            "top 10 hot selling for trading by amount", services=services,
        )
        numbered = [ln for ln in text.split("\n") if re.match(r"^\d+\. ", ln)]
        assert 0 < len(numbered) <= 5, text


class TestR9PlainReplies:
    def test_no_snake_case_in_the_new_lines(self) -> None:
        from app.services.chatbot.lanes.business import TOP_SELLING_UNCLEAR
        from app.services.chatbot.lanes.business import fetch

        # Fix lane round 8: the hop's header and dropped filter lines are gone.
        assert not hasattr(fetch, "top_selling_hop_lines")
        lines: list[str] = []
        for text in [*lines, TOP_SELLING_UNCLEAR]:
            assert not _SNAKE.search(text) and "\u2014" not in text and "\u2013" not in text
