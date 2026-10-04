"""Top selling fix lane round 4: the owner's retest of 27 Sep 2026 (PR #1273).

The owner's PR comment "Owner ruling from the retest of top selling (27 Sep 11:27 to
11:35 MYT, :3083 console at 9ab7f89d, contact Mr Loo, parser v34)" is the work list.
Every test below types the owner's OWN message as the turn's text; the parser verdict
beside it is what the round 4 prompt addendum teaches for that message
(`chatbot_parser_prompt.TOP_SELLING_ADDENDUM`), or, where the fix is on the lane's side,
what the live parser actually emitted (a bare "2" read as a position).

* F1 no per-product quick replies on a ranked list, and never more than three chips;
* F2 a sales agent word sets the Sales agent filter, never the Customer filter;
* F3 a correction clears the customer and sets the agent;
* F4 the metric question takes "1" / "2", "qty", "amt" and an answer carrying filters;
* F5 a category word matches the catalogue class vocabulary, an unknown word is said
  and the ranking kept, and a ranking never falls into "Could not find order";
* F6 a brand word sets a Brand filter, never a customer;
* F7 cold / least sold ranks ascending;
* F8 no snake_case and no dash in any reply.

The last class replays the owner's transcript in order.

Same harness as `test_top_selling_lane.py` (one real `engine.run_turn` per message,
the parser, access and MCP faked, the presenter real). Postgres only, every row
seeded here.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot import test_outstanding_lane as outstanding_lane
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _present_response, _run_turn, _seed_contact, _session_of

TOOL = "crm_top_selling_report"
GRANT = "sales_orders.sales_report"
ASK_METRIC = "By quantity or by amount?"
ITEM_OFFER = "Reply with a rank number to see that item's customers and months."
ITEM_CODES = ["SRTWC5501", "SRTWC5502", "SRTTP1001", "CBNWC2001", "SRTWC5503", "SRTTP1002", "CBNWC2002"]
NO_SALE_LINE = "Items with no sale in this period are not ranked."


# --------------------------------------------------------------------------- #
# Seeds and the route double
# --------------------------------------------------------------------------- #


class Catalogue:
    """The rows the owner's words name: the sales agent CONTACTZ, the customer SAMPLE -
    CONTACTZ X (the one "sold by contactz" was wrongly read as), the brands Sorento and
    Cabana, and the Water Closet / Tap categories. Category names are copies of the
    codes (as on every live row, `ProductCategory.class_label`'s comment); the words
    customers use live in `class_label` and `search_synonyms`."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.fanny_agent = ""
        self.fanny_customer = ""
        self.sorento = ""
        self.cabana = ""
        self.water_closet: list[str] = []
        self.tap: list[str] = []


def _seed_catalogue(session_factory, *, with_fanny_customer: bool = False) -> Catalogue:
    from app.models.product import Brand, ProductCategory
    from app.models.sales_agent import SalesAgent
    from app.services.product_class_signal import CLASS_SYNONYMS
    from tests._mc_lookup_seed import customer

    cat = Catalogue()
    db = session_factory()
    agent = SalesAgent(id=str(uuid.uuid4()), sales_agent="CONTACTZ", person_label="Contactz X", company_id=DEFAULT_COMPANY_ID)
    sorento = Brand(id=str(uuid.uuid4()), brand_code="SRT", brand_name="Sorento", company_id=DEFAULT_COMPANY_ID)
    cabana = Brand(id=str(uuid.uuid4()), brand_code="CBN", brand_name="Cabana", company_id=DEFAULT_COMPANY_ID)
    categories = [
        ProductCategory(
            id=str(uuid.uuid4()), category_code=code, category_name=code, class_label=label,
            search_synonyms=CLASS_SYNONYMS[label], company_id=DEFAULT_COMPANY_ID,
        )
        for code, label in (("SRT-WC", "Water Closet"), ("CBN-WC", "Water Closet"), ("SRT-TP", "Tap"))
    ]
    db.add_all([agent, sorento, cabana, *categories])
    db.flush()
    if with_fanny_customer:
        cat.fanny_customer = str(customer(db, company_id=DEFAULT_COMPANY_ID, name="SAMPLE - CONTACTZ X").id)
        cat.names[cat.fanny_customer] = "SAMPLE - CONTACTZ X"
    db.commit()
    cat.fanny_agent, cat.sorento, cat.cabana = str(agent.id), str(sorento.id), str(cabana.id)
    cat.names.update({cat.fanny_agent: "CONTACTZ", cat.sorento: "Sorento", cat.cabana: "Cabana"})
    cat.water_closet = [str(c.id) for c in categories[:2]]
    cat.tap = [str(categories[2].id)]
    for cid in cat.water_closet:
        cat.names[cid] = "Water Closet"
    cat.names[cat.tap[0]] = "Tap"
    return cat


def _echo(ids: Any, names: dict[str, str]) -> str | None:
    ids = ids if isinstance(ids, list) else [i for i in str(ids or "").split(",") if i]
    got = []
    for i in ids:
        name = names.get(str(i), str(i))
        if name not in got:
            got.append(name)
    return ", ".join(got) or None


def _fake_route(args: dict[str, Any], names: dict[str, str], codes: list[str]) -> dict[str, Any]:
    """The body the real route answers `args` with (shape `TopSellingResponse`): the
    rows it ranks and the filters it echoes (the real echo is pinned route side in
    `tests/test_top_selling_report.py`)."""
    bottom = args.get("direction") == "bottom"
    rows = [
        {"rank": i, "code": code, "quantity": 100 - i, "amount": 1000.0 - i}
        for i, code in enumerate(codes, start=1)
    ]
    if bottom:
        rows = [{**r, "rank": i} for i, r in enumerate(reversed(rows), start=1)]
    body: dict[str, Any] = {
        "rank_by": args.get("rank_by"),
        "basis": args.get("basis") or "delivered",
        "group": args.get("group") or "item",
        "direction": "bottom" if bottom else "top",
        "n": None,
        "date_from": "2026-01-01",
        "date_to": "2026-12-31",
        "filters": {
            "customer_name": _echo(args.get("customer_ids"), names) or args.get("customer_query"),
            "category_name": _echo(args.get("category_ids"), names),
            "brand_name": _echo(args.get("brand_ids"), names),
            "sales_agent": _echo(args.get("sales_agent_ids"), names),
            "channel": args.get("channel"),
            "dealer_scoped": False,
        },
        "total_count": len(rows),
        "rows": rows,
        "totals": {"quantity": sum(r["quantity"] for r in rows), "amount": sum(r["amount"] for r in rows)},
        "sales_agent_fill_rate": 1.0 if args.get("sales_agent_ids") else None,
        "detail": None,
    }
    if args.get("detail_code"):
        hit = [r for r in rows if r["code"] == args["detail_code"]]
        body["rows"] = [{**r, "rank": 1} for r in hit]
        body["total_count"] = len(hit)
        body["detail"] = (
            {
                "code": hit[0]["code"], "name": "A product",
                "by_customer": [{"customer_name": "SOME DEALER", "quantity": 5, "amount": 50.0}],
                "by_month": [{"month": "2026-03", "quantity": 5, "amount": 50.0}],
            }
            if hit
            else None
        )
    elif args.get("count_only") in (True, "true"):
        if len(rows) > 1:
            body["rows"] = []
    elif args.get("n"):
        body["n"] = int(args["n"])
        body["rows"] = rows[: int(args["n"])]
    return body


@pytest.fixture
def route(monkeypatch):
    """The harness MCP double, answering `crm_top_selling_report` through the REAL
    presenter. `route.names` is the seeded id -> name echo, `route.codes` the rows."""

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
            if name != TOOL:
                return json.dumps({"has_result": False, "items": []})
            return present(name, json.dumps(_fake_route(args, state.names, state.codes)))

        return _call, captured

    monkeypatch.setattr(outstanding_lane, "_capturing_mcp", _factory)
    return state


# --------------------------------------------------------------------------- #
# Parser verdicts
# --------------------------------------------------------------------------- #


def _e(raw: str, hint: str) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True, "hint_confident": True}


def _ask(**overrides: Any) -> dict[str, Any]:
    """A message that names the ranking itself ("top 100 hot selling item"): the
    parser's `domain_in_message` is true."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="top_selling", entities=[],
        rank_by=None, basis=None, rank_group=None, rank_direction=None, top_n=None,
        domain_in_message=True,
    )
    base.update(overrides)
    return _parser_output(**base)


def _answer(**overrides: Any) -> dict[str, Any]:
    """The answer to the bot's own top selling question, or a follow-up that changes
    one axis ("amt", "by amount"): order_status kept, only the key it answers."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="top_selling", entities=[],
        rank_by=None, basis=None, rank_group=None, rank_direction=None, top_n=None,
        domain_in_message=None,
    )
    base.update(overrides)
    return _parser_output(**base)


def _narrow(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """A message that only narrows the ranking on screen ("sold by contactz", "water
    closet only"): a refinement, no ask of its own (the SALES REPORT addendum's
    "narrowing reply" rule, which the round 4 addendum extends to the ranking)."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint=None, intent_hint=None, order_status=None,
        entities=list(entities), domain_in_message=None,
    )
    base.update(overrides)
    return _parser_output(**base)


def _position(n: int) -> dict[str, Any]:
    return _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        reference_positions=[n], order_status=None,
    )


def _turn(session_factory, monkeypatch, qf, body: str, **kw) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body,
        msg_id=f"ZZT-top-selling-r4-{uuid.uuid4().hex[:10]}", attributes=[GRANT], **kw,
    )
    return dict(result.reply or {}), [args for name, args in captured if name == TOOL]


def _text(reply: dict[str, Any]) -> str:
    return str(reply.get("text") or "")


def _chips(reply: dict[str, Any]) -> list[str]:
    raw = reply.get("quick_replies")
    return [c for c in str(raw).split(", ") if c] if raw else []


def _open_question(session_factory) -> dict[str, Any]:
    return _session_of(session_factory).get("open_question") or {}


def _ranked(session_factory, monkeypatch) -> None:
    """The owner's opening two turns: a ranking of the whole book by quantity."""
    _turn(session_factory, monkeypatch, _ask(top_n=100), "top 100 hot selling item")
    _turn(session_factory, monkeypatch, _answer(rank_by="quantity"), "qty")


@pytest.fixture
def cat(session_factory, route) -> Catalogue:
    _seed_contact(session_factory, variables={})
    seeded = _seed_catalogue(session_factory)
    route.names = seeded.names
    return seeded


# --------------------------------------------------------------------------- #
# F1 quick replies
# --------------------------------------------------------------------------- #


class TestF1QuickReplies:
    def test_a_ranked_list_offers_no_chip_per_product(self, session_factory, monkeypatch, cat) -> None:
        """Transcript item 1: the Top 100 answer sent one chip per product code (about
        100). The ranking keeps the one "Reply with a rank number" line and no chips."""
        _turn(session_factory, monkeypatch, _ask(top_n=100), "top 100 hot selling item")
        reply, calls = _turn(session_factory, monkeypatch, _answer(rank_by="quantity"), "qty")
        assert calls and calls[0]["n"] == 100
        assert _text(reply).endswith(ITEM_OFFER)
        assert _chips(reply) == []

    def test_the_rank_number_typed_still_opens_the_row(self, session_factory, monkeypatch, cat) -> None:
        _ranked(session_factory, monkeypatch)
        assert _open_question(session_factory).get("kind") == "top_selling_pick"
        reply, calls = _turn(session_factory, monkeypatch, _position(3), "3")
        assert calls[0]["detail_code"] == ITEM_CODES[2]
        assert _text(reply).startswith(f"*{ITEM_CODES[2]}: customers and months*")
        assert _chips(reply) == []

    @pytest.mark.parametrize("count, expected", [(2, 2), (3, 3), (4, 0), (12, 0)])
    def test_never_more_than_three_chips(self, count, expected) -> None:
        """WhatsApp shows at most three reply buttons; a question with more options is
        answered by its numbered list alone, never a wall of chips."""
        from app.services.chatbot.turn import pending as turn_pending

        labels = [f"Option {i}" for i in range(1, count + 1)]
        chips = turn_pending.quick_replies("customer_pick", labels)
        assert len(chips.split(", ") if chips else []) == expected
        assert turn_pending.quick_replies("top_selling_pick", labels[:2]) is None


# --------------------------------------------------------------------------- #
# F2 narrowing by sales agent
# --------------------------------------------------------------------------- #


class TestF2SalesAgent:
    @pytest.mark.parametrize("message", ["sold by contactz", "sales agent is contactz", "by agent contactz"])
    def test_an_agent_word_sets_the_sales_agent_filter(self, session_factory, monkeypatch, cat, message) -> None:
        """Transcript item 2: "sold by contactz" answered Customer: SAMPLE - CONTACTZ X."""
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(session_factory, monkeypatch, _narrow(_e("contactz", "sales_agent")), message)
        (args,) = calls
        assert args["sales_agent_ids"] == [cat.fanny_agent]
        assert "customer_ids" not in args and "customer_query" not in args
        assert args["rank_by"] == "quantity" and args["n"] == 100
        assert "\nSales agent: CONTACTZ\n" in _text(reply)
        assert "\nCustomer: all\n" in _text(reply)

    def test_a_customer_hinted_word_that_names_only_an_agent_is_the_agent(
        self, session_factory, monkeypatch, cat
    ) -> None:
        _ranked(session_factory, monkeypatch)
        _reply, calls = _turn(session_factory, monkeypatch, _narrow(_e("contactz", "customer")), "sold by contactz")
        (args,) = calls
        assert args["sales_agent_ids"] == [cat.fanny_agent]
        assert "customer_ids" not in args

    def test_a_word_naming_a_customer_and_an_agent_is_asked(self, session_factory, monkeypatch, route) -> None:
        """Both a customer and an agent could be meant: one short question, nothing
        fetched, and "2" takes the agent."""
        _seed_contact(session_factory, variables={})
        cat = _seed_catalogue(session_factory, with_fanny_customer=True)
        route.names = cat.names
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(session_factory, monkeypatch, _narrow(_e("contactz", "customer")), "for contactz")
        assert calls == []
        assert _text(reply) == (
            "Do you mean customer SAMPLE - CONTACTZ X or sales agent CONTACTZ? "
            "Reply 1 for the customer, 2 for the sales agent."
        )
        _reply, calls = _turn(session_factory, monkeypatch, _position(2), "2")
        (args,) = calls
        assert args["sales_agent_ids"] == [cat.fanny_agent] and "customer_ids" not in args

    def test_answering_one_to_the_question_takes_the_customer(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        cat = _seed_catalogue(session_factory, with_fanny_customer=True)
        route.names = cat.names
        _ranked(session_factory, monkeypatch)
        _turn(session_factory, monkeypatch, _narrow(_e("contactz", "customer")), "for contactz")
        reply, calls = _turn(session_factory, monkeypatch, _position(1), "1")
        (args,) = calls
        assert args["customer_ids"] == [cat.fanny_customer] and "sales_agent_ids" not in args
        assert "\nCustomer: SAMPLE - CONTACTZ X\n" in _text(reply)

    def test_an_agent_word_matching_no_agent_is_said_and_the_ranking_kept(
        self, session_factory, monkeypatch, cat
    ) -> None:
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(session_factory, monkeypatch, _narrow(_e("zelda", "sales_agent")), "sold by zelda")
        (args,) = calls
        assert "sales_agent_ids" not in args
        assert _text(reply).startswith("I don't know 'zelda' as a sales agent.\n\n*Top 100 selling items*")


# --------------------------------------------------------------------------- #
# F3 corrections
# --------------------------------------------------------------------------- #


class TestF3Correction:
    def _customer_fanny(self, session_factory, monkeypatch, route) -> Catalogue:
        """The state the owner corrected: the ranking filtered to the customer SAMPLE -
        CONTACTZ X."""
        _seed_contact(session_factory, variables={})
        cat = _seed_catalogue(session_factory, with_fanny_customer=True)
        route.names = cat.names
        _ranked(session_factory, monkeypatch)
        _turn(session_factory, monkeypatch, _narrow(_e("contactz", "customer")), "for contactz")
        _reply, calls = _turn(session_factory, monkeypatch, _position(1), "1")
        assert calls[0]["customer_ids"] == [cat.fanny_customer]
        return cat

    def test_customer_is_everyone_but_agent_is_fanny(self, session_factory, monkeypatch, route) -> None:
        """Transcript item 3: the correction left the customer and never set the agent,
        re-running the previous filter unchanged."""
        cat = self._customer_fanny(session_factory, monkeypatch, route)
        reply, calls = _turn(
            session_factory, monkeypatch,
            _narrow(_e("contactz", "sales_agent"), correction=True, broaden_axis="customer", broaden_to="all"),
            "hmm no, customer is everyone, but saless agent is contactz",
        )
        (args,) = calls
        assert "customer_ids" not in args and "customer_query" not in args
        assert args["sales_agent_ids"] == [cat.fanny_agent]
        assert "\nCustomer: all\n" in _text(reply) and "\nSales agent: CONTACTZ\n" in _text(reply)

    @pytest.mark.parametrize("message", ["everyone", "all customers", "any customer"])
    def test_everyone_clears_the_customer(self, session_factory, monkeypatch, route, message) -> None:
        self._customer_fanny(session_factory, monkeypatch, route)
        reply, calls = _turn(
            session_factory, monkeypatch, _narrow(broaden_axis="customer", broaden_to="all"), message,
        )
        (args,) = calls
        assert "customer_ids" not in args and "customer_query" not in args
        assert "\nCustomer: all\n" in _text(reply)


# --------------------------------------------------------------------------- #
# F4 the metric question
# --------------------------------------------------------------------------- #


class TestF4Metric:
    @pytest.mark.parametrize("typed, metric", [("1", "quantity"), ("2", "amount")])
    def test_the_listed_order_answers_the_metric(self, session_factory, monkeypatch, cat, typed, metric) -> None:
        """Transcript item 4: "2" under "By quantity or by amount?" asked again. The
        parser reads a bare number as a position; under the metric question it is the
        option in the order the question lists."""
        reply, _calls = _turn(session_factory, monkeypatch, _ask(top_n=100), "top 100 hot selling item")
        assert _text(reply) == ASK_METRIC
        _reply, calls = _turn(session_factory, monkeypatch, _position(int(typed)), typed)
        (args,) = calls
        assert args["rank_by"] == metric and args["n"] == 100

    @pytest.mark.parametrize(
        "typed, metric", [("qty", "quantity"), ("quantity", "quantity"), ("amt", "amount"), ("amount", "amount")]
    )
    def test_the_metric_words(self, session_factory, monkeypatch, cat, typed, metric) -> None:
        _turn(session_factory, monkeypatch, _ask(top_n=100), "top 100 hot selling item")
        _reply, calls = _turn(session_factory, monkeypatch, _answer(rank_by=metric), typed)
        (args,) = calls
        assert args["rank_by"] == metric

    def test_the_prompt_teaches_the_answers(self) -> None:
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        for word in ('"1"', '"2"', '"qty"', '"quantity"', '"amt"', '"amount"'):
            assert word in TOP_SELLING_ADDENDUM, word

    def test_an_answer_carrying_filters_is_applied_whole(self, session_factory, monkeypatch, cat) -> None:
        _turn(session_factory, monkeypatch, _ask(top_n=5), "top 5 hot selling item")
        reply, calls = _turn(
            session_factory, monkeypatch,
            _answer(rank_by="amount", top_n=100, entities=[_e("water closet", "category")]),
            "amount, top 100, water closet",
        )
        (args,) = calls
        assert args["rank_by"] == "amount" and args["n"] == 100
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        assert "\nCategory: Water Closet\n" in _text(reply)


# --------------------------------------------------------------------------- #
# F5 category narrowing
# --------------------------------------------------------------------------- #


class TestF5Category:
    def test_which_is_water_closet(self, session_factory, monkeypatch, cat) -> None:
        """Transcript item 4: the ask named water closet, "amt" answered the metric and
        the ranking fell into "Could not find order" plus the routing picker."""
        reply, calls = _turn(
            session_factory, monkeypatch,
            _ask(top_n=100, entities=[_e("water closet", "category")]),
            "top 100 hot selling item which is water closet",
        )
        assert _text(reply) == ASK_METRIC and calls == []
        reply, calls = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amt")
        (args,) = calls
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        text = _text(reply)
        assert "Could not find order" not in text and "escalate" not in text.lower()
        assert "\nCategory: Water Closet\n" in text
        assert _open_question(session_factory).get("kind") not in ("member_offer", "team_pick")

    @pytest.mark.parametrize(
        "message, word, category",
        [
            ("water closet only", "water closet", "Water Closet"),
            ("i mean product category is water closet", "water closet", "Water Closet"),
            ("toilet only", "toilet", "Water Closet"),
            ("for water tap", "water tap", "Tap"),
        ],
    )
    def test_a_narrowing_category_word(self, session_factory, monkeypatch, cat, message, word, category) -> None:
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(session_factory, monkeypatch, _narrow(_e(word, "category")), message)
        (args,) = calls
        expected = cat.water_closet if category == "Water Closet" else cat.tap
        assert sorted(args["category_ids"]) == sorted(expected)
        assert f"\nCategory: {category}\n" in _text(reply)
        assert args["rank_by"] == "quantity" and args["n"] == 100

    def test_a_narrowing_that_says_it_asks_keeps_the_ranking(self, session_factory, monkeypatch, cat) -> None:
        """"water closet only" read with `domain_in_message` but no ask word of its own
        must not leave the ranking for the order lane."""
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(
            session_factory, monkeypatch,
            _narrow(_e("water closet", "category"), domain_hint="order", domain_in_message=True),
            "water closet only",
        )
        (args,) = calls
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        assert "Could not find order" not in _text(reply)

    def test_an_unknown_category_word_is_said_and_the_ranking_kept(self, session_factory, monkeypatch, cat) -> None:
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(session_factory, monkeypatch, _narrow(_e("marble", "category")), "marble only")
        (args,) = calls
        assert "category_ids" not in args
        assert _text(reply).startswith("I don't know 'marble' as a category.\n\n*Top 100 selling items*")
        assert "\nCategory: all\n" in _text(reply)
        # said once: the next turn does not repeat it
        reply, _calls = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "by amount")
        assert "I don't know" not in _text(reply)

    def test_no_sales_is_said_without_the_escalation_offer(self, session_factory, monkeypatch, cat, route) -> None:
        route.codes = []
        _turn(session_factory, monkeypatch, _ask(top_n=100), "top 100 hot selling item")
        reply, _calls = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amt")
        text = _text(reply)
        assert "No sales found." in text
        assert "Could not find order" not in text and "escalate" not in text.lower()
        assert _open_question(session_factory).get("kind") not in ("member_offer", "team_pick")


# --------------------------------------------------------------------------- #
# F6 brand narrowing
# --------------------------------------------------------------------------- #


class TestF6Brand:
    def test_for_sorento_brand(self, session_factory, monkeypatch, cat) -> None:
        """Transcript item 5: "Which customer do you mean?" with SORENTO customers."""
        reply, calls = _turn(
            session_factory, monkeypatch,
            _ask(top_n=100, entities=[_e("sorento", "brand")]),
            "top 100 hot selling item for sorento brand",
        )
        assert _text(reply) == ASK_METRIC and calls == []
        reply, calls = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amount")
        (args,) = calls
        assert args["brand_ids"] == [cat.sorento]
        assert "customer_ids" not in args and "customer_query" not in args
        assert "\nBrand: Sorento\n" in _text(reply)

    @pytest.mark.parametrize(
        "message, raw, hint, brand",
        [
            ("sorento only", "sorento", "customer", "Sorento"),
            ("cabana", "cabana", "brand", "Cabana"),
            ("cabana", "cabana", "customer", "Cabana"),
        ],
    )
    def test_a_brand_word_is_never_a_customer(self, session_factory, monkeypatch, cat, message, raw, hint, brand) -> None:
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(session_factory, monkeypatch, _narrow(_e(raw, hint)), message)
        (args,) = calls
        assert args["brand_ids"] == [cat.sorento if brand == "Sorento" else cat.cabana]
        assert "customer_ids" not in args
        assert f"\nBrand: {brand}\n" in _text(reply)
        assert "Which customer" not in _text(reply)

    def test_the_resolver_is_never_asked_about_a_brand(self, session_factory, monkeypatch, cat) -> None:
        asked: list[list[str]] = []
        services = outstanding_lane._resolve_services({})
        inner = services.resolve_entity

        def _spy(body):
            asked.append(list(body.get("tokens") or []))
            return inner(body)

        services = services.__class__(access_types=services.access_types, resolve_entity=_spy, probe=services.probe)
        _ranked(session_factory, monkeypatch)
        _turn(session_factory, monkeypatch, _narrow(_e("sorento", "brand")), "sorento only", resolve_services=services)
        assert all("sorento" not in [t.lower() for t in tokens] for tokens in asked), asked


# --------------------------------------------------------------------------- #
# F7 direction
# --------------------------------------------------------------------------- #


class TestF7Direction:
    def test_top_10_cold_selling(self, session_factory, monkeypatch, cat) -> None:
        """Transcript item 6: the cold selling ask answered the Top 10, descending."""
        reply, calls = _turn(
            session_factory, monkeypatch, _ask(top_n=10, rank_direction="bottom"), "how about top 10 cold selling item",
        )
        assert _text(reply) == ASK_METRIC and calls == []
        reply, calls = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amount")
        (args,) = calls
        assert args["direction"] == "bottom" and args["n"] == 10
        text = _text(reply)
        assert text.startswith("*Bottom 10 selling items*\n")
        assert NO_SALE_LINE in text
        assert f"\n1. {ITEM_CODES[-1]}: " in text

    def test_least_sold_item(self, session_factory, monkeypatch, cat) -> None:
        _turn(session_factory, monkeypatch, _ask(rank_direction="bottom"), "least sold item")
        reply, calls = _turn(session_factory, monkeypatch, _answer(rank_by="amount"), "amount")
        (args,) = calls
        assert args["direction"] == "bottom" and args["count_only"] is True
        assert _text(reply).startswith("*Least sold items*\n")

    def test_a_top_ask_after_a_bottom_one_ranks_descending(self, session_factory, monkeypatch, cat) -> None:
        _turn(session_factory, monkeypatch, _ask(top_n=10, rank_direction="bottom", rank_by="amount"), "bottom 10 by amount")
        _reply, calls = _turn(session_factory, monkeypatch, _ask(top_n=10, rank_by="amount"), "top 10 by amount")
        (args,) = calls
        assert "direction" not in args

    def test_the_parser_key_and_words(self) -> None:
        from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA, TOLERATED_ABSENT
        from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

        prop = PARSE_OUTPUT_JSON_SCHEMA["properties"]["rank_direction"]
        assert prop["enum"] == ["top", "bottom", None]
        assert "rank_direction" in PARSE_OUTPUT_JSON_SCHEMA["required"]
        assert "rank_direction" in TOLERATED_ABSENT
        for word in ("cold selling", "least sold", "worst selling", "bottom 10", "slowest"):
            assert word in TOP_SELLING_ADDENDUM, word


# --------------------------------------------------------------------------- #
# The owner's transcript, in order
# --------------------------------------------------------------------------- #

_SNAKE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")


class TestOwnerTranscript:
    def test_replay(self, session_factory, monkeypatch, cat) -> None:
        """Every message of the retest (27 Sep 11:27 to 11:35 MYT), in order, with what
        the bot must do now. Each reply: at most three chips, no snake_case, no dash
        (F1, F8)."""
        replies: list[str] = []

        def say(qf, body):
            reply, calls = _turn(session_factory, monkeypatch, qf, body)
            text = _text(reply)
            replies.append(text)
            assert len(_chips(reply)) <= 3, (body, _chips(reply))
            assert not _SNAKE.search(text), (body, _SNAKE.search(text))
            assert "\u2014" not in text and "\u2013" not in text, body
            assert "Could not find order" not in text, body
            return text, calls

        # 1. the ranking, no chip per product (F1)
        text, calls = say(_ask(top_n=100), "top 100 hot selling item")
        assert text == ASK_METRIC
        text, (args,) = say(_answer(rank_by="quantity"), "qty")
        assert args["rank_by"] == "quantity" and args["n"] == 100 and text.endswith(ITEM_OFFER)
        # 2. the agent (F2)
        text, (args,) = say(_narrow(_e("contactz", "sales_agent")), "sold by contactz")
        assert args["sales_agent_ids"] == [cat.fanny_agent] and "customer_ids" not in args
        # 3. the correction (F3)
        text, (args,) = say(
            _narrow(_e("contactz", "sales_agent"), correction=True, broaden_axis="customer", broaden_to="all"),
            "hmm no, customer is everyone, but saless agent is contactz",
        )
        assert args["sales_agent_ids"] == [cat.fanny_agent] and "customer_ids" not in args
        assert "\nCustomer: all\n" in text
        # 4. the category (F5) and the metric answers (F4)
        text, calls = say(
            _ask(top_n=100, entities=[_e("water closet", "category")]),
            "top 100 hot selling item which is water closet",
        )
        assert text == ASK_METRIC and calls == []
        text, (args,) = say(_position(2), "2")
        assert args["rank_by"] == "amount" and sorted(args["category_ids"]) == sorted(cat.water_closet)
        assert "sales_agent_ids" not in args, "a fresh ask states its own filters"
        text, (args,) = say(_answer(rank_by="amount"), "amt")
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        text, (args,) = say(_narrow(_e("water closet", "category")), "water closet only")
        assert "\nCategory: Water Closet\n" in text
        text, (args,) = say(_narrow(_e("water closet", "category"), correction=True), "i mean product category is water closet")
        assert sorted(args["category_ids"]) == sorted(cat.water_closet)
        text, (args,) = say(
            _answer(rank_by="amount", top_n=100, entities=[_e("water closet", "category")]),
            "amount, top 100, water closet",
        )
        assert args["n"] == 100 and args["rank_by"] == "amount"
        text, calls = say(_ask(top_n=100, entities=[_e("water tap", "category")]), "top 100 hot selling item for water tap")
        assert text == ASK_METRIC and calls == []
        text, (args,) = say(_answer(rank_by="amount"), "amount")
        assert args["category_ids"] == cat.tap and "\nCategory: Tap\n" in text
        # 5. the brand (F6)
        text, calls = say(_ask(top_n=100, entities=[_e("sorento", "brand")]), "top 100 hot selling item for sorento brand")
        assert text == ASK_METRIC and calls == []
        text, (args,) = say(_answer(rank_by="amount"), "amount")
        assert args["brand_ids"] == [cat.sorento] and "\nBrand: Sorento\n" in text
        assert "category_ids" not in args
        # 6. the direction (F7)
        text, calls = say(_ask(top_n=10, rank_direction="bottom"), "how about top 10 cold selling item")
        assert text == ASK_METRIC
        text, (args,) = say(_answer(rank_by="amount"), "amount")
        assert args["direction"] == "bottom" and text.startswith("*Bottom 10 selling items*")
        text, calls = say(_ask(rank_direction="bottom"), "least sold item")
        assert text == ASK_METRIC
        text, (args,) = say(_answer(rank_by="amount"), "amount")
        assert args["direction"] == "bottom" and text.startswith("*Least sold items*")
        # 7. still working today: the count, a metric follow-up and the dealer channel
        text, (args,) = say(_answer(top_n=10), "just show me top 10")
        assert args["n"] == 10 and args["direction"] == "bottom"
        text, (args,) = say(_answer(rank_by="amount"), "what if by amount")
        assert args["rank_by"] == "amount" and args["n"] == 10
        text, (args,) = say(_narrow(sales_channel="dealer"), "only for dealer")
        assert args["channel"] == "dealer" and "\nChannel: Dealer\n" in text
        assert len(replies) == 21


class TestNarrowingNeverRestarts:
    def test_a_narrowing_marked_as_naming_the_ask_keeps_the_metric_and_count(
        self, session_factory, monkeypatch, cat
    ) -> None:
        """"sold by contactz" read with order_status top_selling AND `domain_in_message`
        true still narrows the ranking on screen: no metric question, the count kept."""
        _ranked(session_factory, monkeypatch)
        reply, calls = _turn(
            session_factory, monkeypatch,
            _narrow(_e("contactz", "sales_agent"), order_status="top_selling", domain_hint="order", domain_in_message=True),
            "sold by contactz",
        )
        (args,) = calls
        assert args["sales_agent_ids"] == [cat.fanny_agent]
        assert args["rank_by"] == "quantity" and args["n"] == 100
        assert _text(reply) != ASK_METRIC
