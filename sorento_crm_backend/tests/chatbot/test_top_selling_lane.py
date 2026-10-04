"""Top X hot selling S4: the parser keys and the lane wiring (#1171).

`documentation/plans/chatbot/PLAN-chatbot-top-x-hot-selling-24sep.md` "Parser (S4)",
"Lane wiring (S4)" and the UAC's AC-1950 onward, as amended by the owner's rulings
folded on PR #1258 and PR #1263:

* no paging ever; the header states the full count;
* no N named: the bot states the total and asks how many (no length rule, n8n chunks
  long messages), except a single row, which is sent;
* the ranked list is a sticky pick list (`top_selling_pick`, the roster mechanism);
* rows show the code only;
* `rank_by` required (asked when absent), basis delivered | ordered, group item |
  category (asked when unclear);
* a dealer caller is forced to its own customers by the route and a named other
  customer gets the refusal line;
* no date = the current calendar year, set by the route and stated in the header.

Every turn below is one real `engine.run_turn` through the SAME harness the sales
report lane uses (`tests/chatbot/test_outstanding_lane.py::_run_turn`): parser,
access and resolver faked, the MCP call answered by `_fake_route` below, which builds
the route's own body (`TopSellingResponse`, as built on PR #1263) from the lane's args
and renders it through the REAL presenter, the way `view=render` does in production.

Postgres only (`session_factory`, blank schema). Every row seeded here.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot.lanes.business.services import FetchServices
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot import test_outstanding_lane as outstanding_lane
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import (
    CUSTOMER_NAME,
    CUSTOMER_UUID,
    HANLIM_UUID_1,
    _all_pick_parser_output,
    _ambiguous_hanlim_resolve_services,
    _present_response,
    _run_turn,
    _seed_contact,
    _session_of,
)

TOOL = "crm_top_selling_report"
GRANT = "sales_orders.sales_report"
DENIAL = "Sales report is not enabled for your account."
ASK_METRIC = "By quantity or by amount?"
ASK_GROUP = "Do you want the top items inside one category, or the categories ranked against each other?"
ASK_BASIS = "Delivered (transferred to DO) or ordered?"
REFUSED = "Sorry, I can only share sales figures for your own account."
ITEM_OFFER = "Reply with a rank number to see that item's customers and months."

ITEM_CODES = ["SRTWT7445", "SRTKT39SS", "SRTBS1020", "SRTSH2201", "SRTWC5501", "SRTWT7446", "SRTWT8120"]
CATEGORY_CODES = ["KS", "WC", "BM"]


def _rows(codes: list[str]) -> list[dict[str, Any]]:
    return [
        {"rank": i, "code": code, "quantity": 100 - i, "amount": 1000.0 - i}
        for i, code in enumerate(codes, start=1)
    ]


def _fake_route(args: dict[str, Any], *, codes: list[str] | None = None, refuse: str | None = None) -> dict:
    """The body `GET /api/v1/order-management/top-selling` answers `args` with, in
    the shape PR #1263 ships. `refuse` answers with the AppException body instead."""
    if refuse:
        return {"message": "refused", "detail": None, "code": refuse}
    group = args.get("group") or "item"
    all_rows = _rows(CATEGORY_CODES if group == "category" else (ITEM_CODES if codes is None else codes))
    body: dict[str, Any] = {
        "rank_by": args.get("rank_by"),
        "basis": args.get("basis") or "delivered",
        "group": group,
        "n": None,
        "date_from": args.get("date_from") or "2026-01-01",
        "date_to": args.get("date_to") or "2026-12-31",
        "filters": {
            "customer_name": CUSTOMER_NAME if args.get("customer_ids") else None,
            "category_name": "KITCHEN SINK" if args.get("category_ids") else None,
            "sales_agent": None,
            "channel": args.get("channel"),
            "dealer_scoped": False,
        },
        "total_count": len(all_rows),
        "rows": all_rows,
        "totals": {"quantity": sum(r["quantity"] for r in all_rows), "amount": sum(r["amount"] for r in all_rows)},
        "sales_agent_fill_rate": None,
        "detail": None,
    }
    if args.get("detail_code"):
        hit = [r for r in all_rows if r["code"] == args["detail_code"]]
        body["rows"] = [{**r, "rank": 1} for r in hit]
        body["total_count"] = len(hit)
        body["detail"] = (
            {
                "code": hit[0]["code"],
                "name": f"Name of {hit[0]['code']}",
                "by_customer": [{"customer_name": CUSTOMER_NAME, "quantity": 5, "amount": 50.0}],
                "by_month": [{"month": "2026-03", "quantity": 5, "amount": 50.0}],
            }
            if hit
            else None
        )
    elif args.get("count_only") in (True, "true"):
        if len(all_rows) > 1:
            body["rows"] = []
    elif args.get("n"):
        body["n"] = int(args["n"])
        body["rows"] = all_rows[: int(args["n"])]
    return body


@pytest.fixture
def route(monkeypatch):
    """Swap the harness's MCP double for one that answers `crm_top_selling_report` with
    `_fake_route` rendered through the real presenter. `route.codes` / `route.refuse`
    shape the next answer; each turn gets its own `(name, args)` capture list."""

    class _Route:
        codes: list[str] | None = None
        refuse: str | None = None
        captured: list[tuple[str, dict[str, Any]]] = []

    state = _Route()
    state.captured = []
    present = _present_response()

    def _factory(_response: Any = None):
        captured: list[tuple[str, dict[str, Any]]] = []
        state.captured = captured

        def _call(name: str, args: dict[str, Any]) -> Any:
            captured.append((name, dict(args)))
            if name != TOOL:
                return json.dumps({"has_result": False, "items": []})
            return present(name, json.dumps(_fake_route(args, codes=state.codes, refuse=state.refuse)))

        return _call, captured

    monkeypatch.setattr(outstanding_lane, "_capturing_mcp", _factory)
    return state


def _ts(**overrides: Any) -> dict[str, Any]:
    """A top selling ask as the parser emits it (S4 keys: `rank_by`, `basis`,
    `rank_group`; `top_n` unchanged)."""
    base: dict[str, Any] = dict(
        domain_hint="order",
        intent_hint="check_order",
        order_status="top_selling",
        entities=[],
        rank_by="quantity",
        basis=None,
        rank_group=None,
        top_n=5,
    )
    base.update(overrides)
    return _parser_output(**base)


def _pick(position: int) -> dict[str, Any]:
    return _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        reference_positions=[position], order_status=None,
    )


def _turn(session_factory, monkeypatch, qf, body: str, *, attributes=(GRANT,), **kw):
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body,
        msg_id=f"ZZT-top-selling-{uuid.uuid4().hex[:10]}", attributes=list(attributes), **kw,
    )
    return (result.reply or {}).get("text") or "", captured


def _calls(captured) -> list[dict[str, Any]]:
    return [args for name, args in captured if name == TOOL]


def _open_question(session_factory) -> dict[str, Any]:
    return _session_of(session_factory).get("open_question") or {}


# --------------------------------------------------------------------------- #
# AC-1950 - tool pick
# --------------------------------------------------------------------------- #


class TestToolPick:
    def _payload(self, *, order_status: str, entities: list[dict[str, Any]]):
        return {
            "gate": {"compatible_entities": entities},
            "tier_gate": None,
            "ctx": {
                "parse": {
                    "output": _parser_output(
                        domain_hint="order", intent_hint="check_order", order_status=order_status,
                        entities=[], top_selling={"rank_by": "quantity", "top_n": 5},
                    )
                },
                "contact": {"id": 900000008},
                "access": {"attributes": [GRANT]},
            },
        }

    @pytest.mark.parametrize(
        "entities",
        [[], [{"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}]],
        ids=["no_subject", "customer"],
    )
    def test_tool_pick_top_selling_vs_reports(self, session_factory, entities) -> None:
        """AC-1950: domain `order` + `order_status: "top_selling"` picks the ranking
        with or without a resolved subject."""
        from app.services.chatbot.lanes.business import run_fetch

        calls: list[tuple[str, dict[str, Any]]] = []

        def _call(name, args):
            calls.append((name, args))
            return json.dumps({"has_result": False, "response": "x", "result_set": []})

        run_fetch(self._payload(order_status="top_selling", entities=entities), services=FetchServices(mcp_call=_call))
        assert [name for name, _ in calls] == [TOOL]

    def test_sales_report_still_picks_the_sales_report(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        calls: list[str] = []
        run_fetch(
            self._payload(
                order_status="sales_report",
                entities=[{"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}],
            ),
            services=FetchServices(mcp_call=lambda name, args: calls.append(name) or json.dumps({})),
        )
        assert calls == ["crm_sales_report"]

    def test_the_tool_is_a_date_param_tool(self) -> None:
        from app.services.chatbot.lanes.business import fetch as fetch_mod

        assert fetch_mod.DATE_PARAMS[TOOL] == ("date_from", "date_to")

    def test_the_tool_is_in_the_policy_date_param_tools(self) -> None:
        """Reviewer N2 (PR #1273), plan S4 point 2: the policy's `DATE_PARAM_TOOLS`
        mirrors `fetch.DATE_PARAMS`, so the order domain's `takes_date_filter` counts it."""
        from app.services.chatbot.turn import policy_rows

        assert TOOL in policy_rows.DATE_PARAM_TOOLS


# --------------------------------------------------------------------------- #
# AC-1951 - no grant: the denial line, before any fetch and before any picker
# --------------------------------------------------------------------------- #


class TestNoGrant:
    def test_no_grant_denies_before_fetch_and_picker(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(), "top 5 selling items by quantity", attributes=())
        assert reply.strip() == DENIAL
        assert _calls(captured) == []
        assert _open_question(session_factory).get("kind") != "top_selling_pick"

    def test_no_grant_never_renders_the_customer_picker(self, session_factory, monkeypatch, route) -> None:
        probed: list[Any] = []
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(entities=[{"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]),
            "top 5 for hanlim by quantity", attributes=(),
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **kw: probed.append(kw)),
        )
        assert reply.strip() == DENIAL
        assert "Which customer" not in reply
        assert _calls(captured) == []


# --------------------------------------------------------------------------- #
# AC-1952 / AC-1953 - the args, off the parser's own fields only
# --------------------------------------------------------------------------- #


class TestArgs:
    def test_rank_by_and_top_n_from_parser_only(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(rank_by="amount", top_n=3), "top 3 by quantity")
        (args,) = _calls(captured)
        # the parser said amount; the message text says quantity and is never read
        assert args["rank_by"] == "amount"
        assert args["n"] == 3
        assert "count_only" not in args
        assert reply.startswith("*Top 3 selling items*\nRanked by: Amount\n")
        assert "\n1. SRTWT7445: Qty 99, RM 999.00\n" in reply
        assert "Name of" not in reply, "rows print the code only"
        assert reply.endswith(ITEM_OFFER)

    def test_named_n_past_one_hundred_is_not_capped(self, session_factory, monkeypatch, route) -> None:
        """TOP-N-UNCAP (owner, 30 Sep 2026): "100, 200" - the named N goes out as named."""
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(top_n=250), "top 250 by quantity")
        assert _calls(captured)[0]["n"] == 250
        assert "at most" not in reply

    def test_named_n_past_the_ceiling_asks_the_ceiling_and_says_so(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(top_n=1500), "top 1500 by quantity")
        assert _calls(captured)[0]["n"] == 1000
        assert reply.startswith(
            "I can list at most the top 1,000 in one reply.\n\n"
        ), reply[:200]

    def test_param_mapping_and_date_default(self, session_factory, monkeypatch, route) -> None:
        """AC-1953 as built: dates, the resolved customer, the channel, the basis and the
        contact identity (review N2: always both ids) all travel; no date sends none (the
        ROUTE defaults to the current calendar year and echoes it into the header)."""
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(
                entities=[{"raw": "dealer a", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
                date_filter_start="2026-03-01", date_filter_end="2026-03-31",
                sales_channel="project", basis="ordered",
            ),
            "top 5 for dealer a in march, project, ordered, by quantity",
            matches={"dealer a": {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}},
        )
        (args,) = _calls(captured)
        assert args["customer_ids"] == [CUSTOMER_UUID]
        assert args["date_from"] == "2026-03-01" and args["date_to"] == "2026-03-31"
        assert args["channel"] == "project"
        assert args["basis"] == "ordered"
        assert args["contact_id"] and args["space_id"]
        assert "product_ids" not in args and "warehouse_ids" not in args
        assert "Basis: Ordered" in reply

        _seed_contact_again(session_factory)
        reply, captured = _turn(session_factory, monkeypatch, _ts(), "top 5 by quantity")
        (args,) = _calls(captured)
        assert "date_from" not in args and "date_to" not in args
        assert "\nDelivery date: 01/01/2026 to 31/12/2026\n" in reply
        assert "basis" not in args, "no basis word = the route's default (delivered)"


def _seed_contact_again(session_factory) -> None:
    """A fresh conversation for the second half of a test: the same contact, no memory."""
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb)"),
        {"sv": json.dumps({"variables": {}})},
    )
    db.commit()


# --------------------------------------------------------------------------- #
# AC-1954 - a category word under `order` is a category, never a customer
# --------------------------------------------------------------------------- #


class TestCategory:
    def _seed_category(self, session_factory) -> str:
        from app.models.product import ProductCategory

        db = session_factory()
        row = ProductCategory(category_code="KS", category_name="KITCHEN SINK", company_id=DEFAULT_COMPANY_ID)
        db.add(row)
        db.commit()
        return str(row.id)

    def test_category_entity_resolves_under_order(self, session_factory, monkeypatch, route) -> None:
        category_id = self._seed_category(session_factory)
        asked: list[list[str]] = []
        services = outstanding_lane._resolve_services({})
        inner = services.resolve_entity

        def _spy(body):
            asked.append(list(body.get("tokens") or []))
            return inner(body)

        services = services.__class__(access_types=services.access_types, resolve_entity=_spy, probe=services.probe)
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(
                top_n=3,
                entities=[{"raw": "kitchen sink", "hint": "category", "canonical_code": None, "current_message": True, "confident": True, "hint_confident": True}],
            ),
            "top 3 kitchen sinks by quantity", resolve_services=services,
        )
        (args,) = _calls(captured)
        assert args["category_ids"] == [category_id]
        assert "customer_ids" not in args
        assert all("kitchen sink" not in tokens for tokens in asked), (
            f"the generic resolver re-types a category token under `order` as a customer "
            f"(_DOMAIN_HINT_EXPANSIONS); it must never be asked about it: {asked}"
        )
        assert "Which customer" not in reply
        assert "Category: KITCHEN SINK" in reply

    @staticmethod
    def _categories(db, *rows: tuple[str, str]) -> dict[str, str]:
        from app.models.product import ProductCategory

        made = [ProductCategory(category_code=c, category_name=n, company_id=DEFAULT_COMPANY_ID) for c, n in rows]
        db.add_all(made)
        db.commit()
        return {r.category_name: str(r.id) for r in made}

    @pytest.mark.parametrize(
        "word, expected",
        [
            ("kitchen sinks", []),  # reviewer S1: KIT is inside the word, it is not the word
            ("basin mixers", ["BASIN MIXER"]),  # reviewer S1: BASIN must not ride along
            ("basin", ["BASIN"]),  # an exact name wins alone
            ("BM", ["BASIN MIXER"]),  # an exact code wins alone
            ("mixer", ["BASIN MIXER", "SHOWER MIXER"]),  # whole word, several: the lane asks
            ("mix", []),  # part of a word is not a word
        ],
    )
    def test_category_word_matches_whole_words_never_reverse_containment(
        self, session_factory, word, expected
    ) -> None:
        """Reviewer S1, PR #1273: `resolve_category_token` matched every category whose
        name the word CONTAINS ("kitchen sinks" took KIT; "basin mixers" took BASIN too),
        a silent widening. A category matches on its exact code or name, else when the
        word is a whole-word run of its name (a plural folds to its singular)."""
        from app.services.chatbot.lanes.business import services as business_services

        db = session_factory()
        self._categories(db, ("KT", "KIT"), ("BA", "BASIN"), ("BM", "BASIN MIXER"), ("SM", "SHOWER MIXER"))
        got = sorted(name for _id, _code, name in business_services.resolve_category_token(db, word))
        assert got == expected

    def test_a_plural_word_finds_its_category(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import services as business_services

        db = session_factory()
        ids = self._categories(db, ("KT", "KIT"), ("KS", "KITCHEN SINK"))
        got = business_services.resolve_category_token(db, "kitchen sinks")
        assert [row[0] for row in got] == [ids["KITCHEN SINK"]]

    def test_a_word_matching_several_categories_is_asked(self, session_factory, monkeypatch, route) -> None:
        """Reviewer S1: several categories match, so the bot asks which one (owner: no
        silent defaults, clarify), listing the codes (owner: code only), and fetches
        nothing. The code typed next runs the ask inside that one category."""
        ids = self._categories(session_factory(), ("BM", "BASIN MIXER"), ("SM", "SHOWER MIXER"))
        _seed_contact(session_factory, variables={})
        mixer = {"raw": "mixers", "hint": "category", "canonical_code": None, "current_message": True, "confident": True, "hint_confident": True}
        reply, captured = _turn(session_factory, monkeypatch, _ts(top_n=3, entities=[mixer]), "top 3 mixers by quantity")
        assert _calls(captured) == []
        assert reply.strip() == "Which category do you mean? Reply with one code: BM, SM"

        answer = {"raw": "SM", "hint": "category", "canonical_code": None, "current_message": True, "confident": True, "hint_confident": True}
        _reply, captured = _turn(
            session_factory, monkeypatch,
            _parser_output(message_type="business_query", intent_hint=None, domain_hint=None, order_status=None, entities=[answer]),
            "SM",
        )
        (args,) = _calls(captured)
        assert args["category_ids"] == [ids["SHOWER MIXER"]]
        assert args["rank_by"] == "quantity" and args["n"] == 3


# --------------------------------------------------------------------------- #
# AC-1955 - an ambiguous customer takes the existing picker, and the pick continues
# --------------------------------------------------------------------------- #


class TestCustomerPicker:
    def test_customer_picker_continues_ask(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(
                rank_by="amount", top_n=4, sales_channel="dealer",
                entities=[{"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
            ),
            "top 4 for hanlim dealer by amount",
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False}),
        )
        assert _calls(captured) == []
        assert "Which customer do you mean?" in reply
        assert " - has DO" not in reply and " - no DO" not in reply

        reply, captured = _turn(
            session_factory, monkeypatch,
            _parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1], reference_target="dym", entity_op="reuse", order_status=None,
            ),
            "1",
        )
        (args,) = _calls(captured)
        assert args["customer_ids"] == [HANLIM_UUID_1]
        assert args["rank_by"] == "amount" and args["n"] == 4
        assert args["channel"] == "dealer"

    def test_pick_all_continues_ask(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        _turn(
            session_factory, monkeypatch,
            _ts(entities=[{"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]),
            "top 5 for hanlim by quantity",
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False}),
        )
        _reply, captured = _turn(session_factory, monkeypatch, _all_pick_parser_output(), "all")
        (args,) = _calls(captured)
        assert len(args["customer_ids"]) == 2
        assert args["rank_by"] == "quantity" and args["n"] == 5


# --------------------------------------------------------------------------- #
# AC-1956 - a follow-up that only changes the metric keeps the filters
# --------------------------------------------------------------------------- #


class TestFollowUp:
    def test_follow_up_by_amount_keeps_filters(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        matches = {"dealer a": {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}}
        _turn(
            session_factory, monkeypatch,
            _ts(
                entities=[{"raw": "dealer a", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
                date_filter_start="2026-03-01", date_filter_end="2026-03-31",
            ),
            "top 5 for dealer a in march by quantity", matches=matches,
        )
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(rank_by="amount", top_n=None, domain_hint=None, message_type="business_query"),
            "by amount",
        )
        (args,) = _calls(captured)
        assert args["rank_by"] == "amount"
        assert args["n"] == 5, "the carried N stays"
        assert args["customer_ids"] == [CUSTOMER_UUID]
        assert args["date_from"] == "2026-03-01"
        assert "Ranked by: Amount" in reply

    def test_ordered_follow_up_reruns_on_the_ordered_basis(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(), "top 5 by quantity")
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(rank_by=None, top_n=None, basis="ordered"),
            "ordered",
        )
        (args,) = _calls(captured)
        assert args["basis"] == "ordered"
        assert args["rank_by"] == "quantity" and args["n"] == 5
        assert "Basis: Ordered" in reply

    def test_a_new_top_selling_ask_does_not_inherit_the_old_metric(self, session_factory, monkeypatch, route) -> None:
        """A fresh ask states its own metric: the carried one only answers a question
        the bot asked, or a follow-up that names no ask of its own."""
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(rank_by="amount"), "top 5 by amount")
        reply, captured = _turn(
            session_factory, monkeypatch, _ts(rank_by=None, top_n=10, domain_in_message=True), "top 10 selling items",
        )
        assert _calls(captured) == []
        assert reply.strip() == ASK_METRIC

    def test_a_fresh_ask_after_a_single_row_reply_drops_the_old_category(
        self, session_factory, monkeypatch, route
    ) -> None:
        """Reviewer B2 (a), PR #1273: a single row is sent as is and asks nothing, so the
        next ask that names the ranking itself is FRESH. It must not rank inside the
        category the last ask named (owner: never assume a filter nobody named)."""
        from app.models.product import ProductCategory

        db = session_factory()
        db.add(ProductCategory(category_code="KS", category_name="KITCHEN SINK", company_id=DEFAULT_COMPANY_ID))
        db.commit()
        route.codes = ["SRTWT7445"]
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(
                top_n=None,
                entities=[{"raw": "kitchen sinks", "hint": "category", "canonical_code": None, "current_message": True, "confident": True, "hint_confident": True}],
            ),
            "top selling kitchen sinks by quantity",
        )
        (args,) = _calls(captured)
        assert args["category_ids"], "the first ask is inside the category"
        assert "How many" not in reply

        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(rank_by="amount", top_n=5, domain_in_message=True),
            "top 5 selling items by amount",
        )
        (args,) = _calls(captured)
        assert "category_ids" not in args or not args["category_ids"]
        assert args["rank_by"] == "amount" and args["n"] == 5
        assert "\nCategory: all\n" in reply

    def test_a_fresh_ask_after_an_unanswered_how_many_asks_the_metric(
        self, session_factory, monkeypatch, route
    ) -> None:
        """Reviewer B2 (b), PR #1273: the how-many reply is left unanswered and the
        customer asks the ranking again with no metric. That is a fresh ask, so the
        metric is asked (owner: metric required, no default), never carried."""
        _seed_contact(session_factory, variables={})
        reply, _captured = _turn(session_factory, monkeypatch, _ts(top_n=None), "top selling items by quantity")
        assert "How many items do you want to see?" in reply

        reply, captured = _turn(
            session_factory, monkeypatch, _ts(rank_by=None, top_n=10, domain_in_message=True), "top 10 selling items",
        )
        assert _calls(captured) == []
        assert reply.strip() == ASK_METRIC

    def test_a_fresh_ask_after_a_single_row_reply_drops_the_old_customer(
        self, session_factory, monkeypatch, route
    ) -> None:
        """B2, the customer axis: a fresh ask that names no customer ranks every
        customer the contact may see, never the one the last ask named."""
        route.codes = ["SRTWT7445"]
        _seed_contact(session_factory, variables={})
        matches = {"dealer a": {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}}
        _turn(
            session_factory, monkeypatch,
            _ts(
                top_n=None,
                entities=[{"raw": "dealer a", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
            ),
            "top selling for dealer a by quantity", matches=matches,
        )
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(rank_by="amount", top_n=5, domain_in_message=True),
            "top 5 selling items by amount",
        )
        (args,) = _calls(captured)
        assert "customer_ids" not in args or not args["customer_ids"]
        assert "\nCustomer: all\n" in reply

    def test_the_metric_answer_restating_the_ask_completes_it(self, session_factory, monkeypatch, route) -> None:
        """The other side of B2: the bot asked the metric, and "top 5 by amount" names
        the ranking AND answers the question. It completes the asked ask (its
        category stays) rather than starting over."""
        from app.models.product import ProductCategory

        db = session_factory()
        db.add(ProductCategory(category_code="KS", category_name="KITCHEN SINK", company_id=DEFAULT_COMPANY_ID))
        db.commit()
        _seed_contact(session_factory, variables={})
        reply, _captured = _turn(
            session_factory, monkeypatch,
            _ts(
                rank_by=None, top_n=5,
                entities=[{"raw": "kitchen sink", "hint": "category", "canonical_code": None, "current_message": True, "confident": True, "hint_confident": True}],
            ),
            "top 5 kitchen sink",
        )
        assert reply.strip() == ASK_METRIC
        _reply, captured = _turn(
            session_factory, monkeypatch, _ts(rank_by="amount", top_n=5, domain_in_message=True), "top 5 by amount",
        )
        (args,) = _calls(captured)
        assert args["rank_by"] == "amount"
        assert args["category_ids"]

    def test_another_domain_leaves_the_ranking(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(), "top 5 by quantity")
        _reply, captured = _turn(
            session_factory, monkeypatch,
            _parser_output(
                domain_hint="inventory", intent_hint="check_stock", domain_in_message=True,
                entities=[{"raw": "SRTWT7445", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
            ),
            "stock for SRTWT7445",
            matches={"SRTWT7445": {"uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "entity_type": "product", "canonical_code": "SRTWT7445"}},
        )
        assert _calls(captured) == []
        focus = _session_of(session_factory).get("focus") or {}
        assert focus.get("status") != "top_selling"


# --------------------------------------------------------------------------- #
# AC-1957 / AC-1958 - miss and header
# --------------------------------------------------------------------------- #


class TestMissAndHeader:
    def test_miss_says_no_sales_without_escalating(self, session_factory, monkeypatch, route) -> None:
        """AC-1957 as amended by the owner's retest (27 Sep 2026, PR #1273): a top
        selling ask never falls into "Could not find order" and never opens the
        customer service routing picker. A ranking with no sales is its own answer:
        the header and "No sales found.", no escalate offer."""
        route.codes = []
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(), "top 5 by quantity")
        assert _calls(captured)
        assert "No sales found." in reply
        assert "escalate" not in reply.lower()
        assert _open_question(session_factory).get("kind") != "top_selling_pick"

    def test_header_skipped(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, _captured = _turn(session_factory, monkeypatch, _ts(), "top 5 by quantity")
        assert reply.startswith("*Top 5 selling items*\n"), reply
        assert "Dates: all dates" not in reply


# --------------------------------------------------------------------------- #
# AC-1961 / AC-1962 - clarify seams, no fetch, then the answer runs the carried ask
# --------------------------------------------------------------------------- #


class TestClarify:
    def test_metric_clarify_then_answer(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        matches = {"dealer a": {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}}
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(
                rank_by=None, top_n=5,
                entities=[{"raw": "dealer a", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}],
            ),
            "top 5 selling items for dealer a", matches=matches,
        )
        assert reply.strip() == ASK_METRIC
        assert _calls(captured) == []

        reply, captured = _turn(session_factory, monkeypatch, _ts(rank_by="amount", top_n=None), "amount")
        (args,) = _calls(captured)
        assert args["rank_by"] == "amount"
        assert args["n"] == 5
        assert args["customer_ids"] == [CUSTOMER_UUID]
        assert reply.startswith("*Top 5 selling items*\nRanked by: Amount\n")

    def test_group_clarify_then_answer(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(rank_group="unclear", top_n=3), "top selling by category")
        assert reply.strip() == ASK_GROUP
        assert _calls(captured) == []

        reply, captured = _turn(
            session_factory, monkeypatch, _ts(rank_group="category", rank_by=None, top_n=None), "the categories",
        )
        (args,) = _calls(captured)
        assert args["group"] == "category"
        assert args["n"] == 3
        assert reply.startswith("*Top 3 selling categories*\n")
        assert "\n1. KS: Qty 99, RM 999.00\n" in reply

    def test_basis_clarify(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(basis="unclear"), "top orders")
        assert reply.strip() == ASK_BASIS
        assert _calls(captured) == []
        _reply, captured = _turn(session_factory, monkeypatch, _ts(basis="delivered", rank_by=None, top_n=None), "delivered")
        (args,) = _calls(captured)
        assert args["basis"] == "delivered" and args["rank_by"] == "quantity" and args["n"] == 5

    def test_the_fixed_lines_are_the_presenters_own(self) -> None:
        """One literal each (plan, "The reply"): the backend carries its own copy because
        the backend container does not ship `sorento_crm_mcp`, and this pins the two."""
        from app.services.chatbot.lanes.business import (
            TOP_SELLING_ASK_BASIS,
            TOP_SELLING_ASK_GROUP,
            TOP_SELLING_ASK_METRIC,
        )

        _present_response()
        from sorento_crm_mcp import presenters

        assert TOP_SELLING_ASK_METRIC == presenters.TOP_SELLING_ASK_METRIC
        assert TOP_SELLING_ASK_GROUP == presenters.TOP_SELLING_ASK_GROUP
        assert TOP_SELLING_ASK_BASIS == presenters.TOP_SELLING_ASK_BASIS
        from app.services.chatbot.lanes.business import TOP_SELLING_REFUSED_OTHER_CUSTOMER

        assert TOP_SELLING_REFUSED_OTHER_CUSTOMER == presenters.TOP_SELLING_REFUSED_OTHER_CUSTOMER


# --------------------------------------------------------------------------- #
# AC-1963 - a dealer naming another customer is refused
# --------------------------------------------------------------------------- #


class TestDealer:
    def test_dealer_other_customer_refused(self, session_factory, monkeypatch, route) -> None:
        """The route forces a dealer contact to its own ledgers and answers 403
        `customer_not_permitted` for any other customer (and for a name that matches
        nobody, so it is no name oracle). The reply is the refusal line and no figures."""
        route.refuse = "customer_not_permitted"
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(entities=[{"raw": "dealer a", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]),
            "top 5 for dealer a by quantity",
            matches={"dealer a": {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}},
        )
        assert _calls(captured), "the route is the check"
        assert reply.strip() == REFUSED
        assert "escalate" not in reply.lower()
        assert _open_question(session_factory).get("kind") != "top_selling_pick"

    @staticmethod
    def _link_dealer(session_factory, name: str = "ZZT OWN DEALER SDN BHD") -> str:
        """Link the harness contact to one customer of its own: a dealer contact."""
        from tests._mc_lookup_seed import customer

        db = session_factory()
        own = customer(db, company_id=DEFAULT_COMPANY_ID, name=name)
        db.execute(
            text(
                "INSERT INTO respond_contact_customers (id, contact_id, customer_id, company_id) "
                "SELECT gen_random_uuid(), id, :cust, :company FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"cust": str(own.id), "company": DEFAULT_COMPANY_ID, "cid": str(outstanding_lane.CONTACT_ID)},
        )
        db.commit()
        return str(own.id)

    def test_dealer_naming_another_customer_sees_no_names(self, session_factory, monkeypatch, route) -> None:
        """Reviewer S2, PR #1273 (owner: a dealer sees only its own customers; ruling
        pending owner confirmation): a linked dealer contact gets no customer picker. A
        customer word that matches none of its own ledgers is refused with the plain
        line before any lookup, so "hanlim" never lists HANLIM TRADING / HANLIM
        HARDWARE, and nothing is fetched."""
        _seed_contact(session_factory, variables={})
        self._link_dealer(session_factory)
        looked_up: list[Any] = []
        services = _ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False})
        inner = services.resolve_entity

        def _spy(body):
            looked_up.append(list(body.get("tokens") or []))
            return inner(body)

        services = services.__class__(access_types=services.access_types, resolve_entity=_spy, probe=services.probe)
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(entities=[{"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]),
            "top selling for HANLIM by quantity", resolve_services=services,
        )
        assert "HANLIM TRADING" not in reply and "HANLIM HARDWARE" not in reply
        assert "Which customer" not in reply
        assert reply.strip() == REFUSED
        assert _calls(captured) == []
        assert all("hanlim" not in tokens for tokens in looked_up), looked_up

    def test_dealer_naming_its_own_customer_runs_on_its_own_ledger(self, session_factory, monkeypatch, route) -> None:
        """The other side: a word that matches the dealer's own ledger runs the ranking
        on that ledger, with no picker and no lookup of anyone else's."""
        _seed_contact(session_factory, variables={})
        own_id = self._link_dealer(session_factory)
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(entities=[{"raw": "own dealer", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]),
            "top 5 for own dealer by quantity",
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False}),
        )
        (args,) = _calls(captured)
        assert args["customer_ids"] == [own_id]
        assert "Which customer" not in reply

    @staticmethod
    def _give_access_type(session_factory, name: str, *, active: bool = True) -> None:
        db = session_factory()
        code = f"zzt_at_{uuid.uuid4().hex[:12]}"
        db.execute(
            text("INSERT INTO contact_access_types (code, name, is_active) VALUES (:code, :name, :active)"),
            {"code": code, "name": name, "active": active},
        )
        db.execute(
            text(
                "INSERT INTO respond_contact_access_types (contact_id, access_type_code) "
                "SELECT id, :code FROM respond_contacts WHERE respond_io_id = :cid"
            ),
            {"code": code, "cid": str(outstanding_lane.CONTACT_ID)},
        )
        db.commit()

    def test_linked_contact_with_an_active_office_type_is_staff(self, session_factory, monkeypatch, route) -> None:
        """Fix round 3 (owner hand test, 27 Sep 2026, Mr Loo): the lane agrees with the
        route. A contact holding an active office type is staff even when it also holds
        dealer types and a customer link, so a customer word goes to the normal
        resolver and picker, never the dealer refusal."""
        _seed_contact(session_factory, variables={})
        self._link_dealer(session_factory)
        for name in ("Sorento Office", "Mocha Office", "Cabana Office", "Sorento Dealer", "End User"):
            self._give_access_type(session_factory, name)
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(entities=[{"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]),
            "top selling for HANLIM by quantity",
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False}),
        )
        assert reply.strip() != REFUSED
        assert "Which customer do you mean?" in reply

    def test_linked_contact_with_only_an_inactive_office_type_stays_a_dealer(
        self, session_factory, monkeypatch, route
    ) -> None:
        """An inactive office type is no office type: the link still scopes the lane."""
        _seed_contact(session_factory, variables={})
        self._link_dealer(session_factory)
        self._give_access_type(session_factory, "Sorento Office", active=False)
        self._give_access_type(session_factory, "Sorento Dealer")
        reply, captured = _turn(
            session_factory, monkeypatch,
            _ts(entities=[{"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]),
            "top selling for HANLIM by quantity",
            resolve_services=_ambiguous_hanlim_resolve_services(lambda **_: {"items": [], "has_result": False}),
        )
        assert reply.strip() == REFUSED
        assert _calls(captured) == []


# --------------------------------------------------------------------------- #
# AC-1964 / AC-1966 - the ranked list is a sticky pick list
# --------------------------------------------------------------------------- #


class TestPickList:
    def test_rank_number_opens_detail(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(sales_channel="dealer"), "top 5 dealer by quantity")
        question = _open_question(session_factory)
        assert question.get("kind") == "top_selling_pick"
        assert [o.get("label") for o in question.get("options") or []] == ITEM_CODES[:5]

        reply, captured = _turn(session_factory, monkeypatch, _pick(2), "2")
        (args,) = _calls(captured)
        assert args["detail_code"] == "SRTKT39SS"
        assert args["rank_by"] == "quantity" and args["channel"] == "dealer"
        assert "n" not in args and "count_only" not in args
        assert reply.startswith("*SRTKT39SS: customers and months*\n")
        assert "*_By customer_*" in reply and "*_By month_*" in reply

        # sticky: the list stays open, and a later number answers against it
        question = _open_question(session_factory)
        assert question.get("kind") == "top_selling_pick"
        reply, captured = _turn(session_factory, monkeypatch, _pick(4), "4")
        (args,) = _calls(captured)
        assert args["detail_code"] == "SRTSH2201"

        # the detail is one shot: a follow-up re-runs the ranking itself
        _reply, captured = _turn(session_factory, monkeypatch, _ts(rank_by="amount", top_n=None), "by amount")
        (args,) = _calls(captured)
        assert "detail_code" not in args
        assert args["rank_by"] == "amount" and args["n"] == 5

    def test_typed_code_opens_detail(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(), "top 5 by quantity")
        _reply, captured = _turn(
            session_factory, monkeypatch,
            _parser_output(
                message_type="business_query", intent_hint=None, domain_hint=None, order_status=None,
                entities=[{"raw": "srtbs1020", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}],
            ),
            "srtbs1020",
        )
        (args,) = _calls(captured)
        assert args["detail_code"] == "SRTBS1020"

    def test_number_past_the_last_row_fetches_nothing(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(), "top 5 by quantity")
        _reply, captured = _turn(session_factory, monkeypatch, _pick(9), "9")
        # Owner ruling, retest of round 4 (27 Sep 2026, R7): a number past the end is a
        # new message read against the list's filters, never a pick: the ranking runs
        # again and no row's detail is opened.
        assert all("detail_code" not in args for args in _calls(captured))
        assert _open_question(session_factory).get("kind") == "top_selling_pick"

    def test_category_pick_runs_that_categorys_items(self, session_factory, monkeypatch, route) -> None:
        from app.models.product import ProductCategory

        db = session_factory()
        category = ProductCategory(category_code="WC", category_name="WATER CLOSET", company_id=DEFAULT_COMPANY_ID)
        db.add(category)
        db.commit()
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(rank_group="category", top_n=3), "which category sells most by quantity")
        assert [o.get("label") for o in _open_question(session_factory).get("options") or []] == CATEGORY_CODES

        _reply, captured = _turn(session_factory, monkeypatch, _pick(2), "2")
        (args,) = _calls(captured)
        assert args.get("group") in (None, "item")
        assert args["category_ids"] == [str(category.id)]
        assert "detail_code" not in args


# --------------------------------------------------------------------------- #
# AC-1965 - no N named: state the total and ask how many; the number runs it
# --------------------------------------------------------------------------- #


class TestHowMany:
    def test_how_many_then_number(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        reply, captured = _turn(session_factory, monkeypatch, _ts(top_n=None), "top selling items by quantity")
        (args,) = _calls(captured)
        assert args["count_only"] is True
        assert "n" not in args
        assert "\nItems with sales: 7\n" in reply
        assert reply.endswith("How many items do you want to see? Reply with a number from 1 to 7.")
        assert _open_question(session_factory).get("kind") != "top_selling_pick"

        reply, captured = _turn(session_factory, monkeypatch, _ts(top_n=4, rank_by=None), "4")
        (args,) = _calls(captured)
        assert args["n"] == 4 and args["rank_by"] == "quantity"
        assert "count_only" not in args
        assert reply.startswith("*Top 4 selling items*\n")

    def test_a_bare_number_under_the_how_many_question_is_the_count(self, session_factory, monkeypatch, route) -> None:
        """The parser may read a bare "20" as a position. With no list open and the bot
        waiting on how many, the number is the N (the deterministic side reads the
        parser's own `reference_positions`, never the text)."""
        _seed_contact(session_factory, variables={})
        _turn(session_factory, monkeypatch, _ts(top_n=None), "top selling items by quantity")
        _reply, captured = _turn(session_factory, monkeypatch, _pick(6), "6")
        (args,) = _calls(captured)
        assert args["n"] == 6

    def test_one_row_is_sent_as_is(self, session_factory, monkeypatch, route) -> None:
        route.codes = ["SRTWT7445"]
        _seed_contact(session_factory, variables={})
        reply, _captured = _turn(session_factory, monkeypatch, _ts(top_n=None), "top selling items by quantity")
        assert "\n1. SRTWT7445: Qty 99, RM 999.00\n" in reply
        assert "How many" not in reply


# --------------------------------------------------------------------------- #
# AC-1959 - the parser keys and the prompt
# --------------------------------------------------------------------------- #


class TestParser:
    @pytest.mark.parametrize("key", ["rank_by", "basis", "rank_group"])
    def test_parser_schema_key(self, key: str) -> None:
        from app.services.chatbot.head import parser as parser_mod

        schema = parser_mod.PARSE_OUTPUT_JSON_SCHEMA
        assert key in schema["properties"]
        assert key in schema["required"], "strict mode rejects a property absent from required"
        assert key in parser_mod.TOLERATED_ABSENT, "recorded emissions predate the key"

    def test_parser_enums(self) -> None:
        from app.services.chatbot.head import parser as parser_mod

        props = parser_mod.PARSE_OUTPUT_JSON_SCHEMA["properties"]
        assert props["rank_by"]["enum"] == ["quantity", "amount", None]
        assert props["basis"]["enum"] == ["delivered", "ordered", "unclear", None]
        assert props["rank_group"]["enum"] == ["item", "category", "unclear", None]

    def test_parser_prompt_teaches_top_selling(self) -> None:
        from app.services import chatbot_parser_prompt as prompt_mod

        text = prompt_mod.TOP_SELLING_ADDENDUM
        for needle in ('order_status "top_selling"', '"rank_by"', '"basis"', '"rank_group"', "top_n"):
            assert needle in text, needle
        assert prompt_mod.SEMANTIC_PARSER_PROMPT.removesuffix(
            prompt_mod.MEMORY_ADDENDUM
        ).removesuffix(
            prompt_mod.ACCOUNT_LEDGER_ADDENDUM
        ).removesuffix(
            prompt_mod.PO_SPO_WAREHOUSE_ADDENDUM
        ).removesuffix(
            prompt_mod.ESCALATION_CONFIRMATION_ADDENDUM
        ).removesuffix(prompt_mod.SELF_REFERENCE_ADDENDUM).endswith(text)
        for banned in (chr(0x2014), chr(0x2013)):
            assert banned not in text

    def test_the_migration_publishes_the_prompt(self) -> None:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "chatbot_top_selling_vocab.py"
        spec = importlib.util.spec_from_file_location("chatbot_top_selling_vocab", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        assert module._full_text() == SEMANTIC_PARSER_PROMPT
        assert module.down_revision == "chatbot_top_selling_tool"
        assert len(module.revision) <= 32

    def test_focus_carries_the_top_selling_slot(self) -> None:
        from app.services.chatbot import contracts as contracts_mod
        from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_to_wire

        assert "top_selling" in contracts_mod.Focus.model_fields
        focus = Focus(status="top_selling", top_selling={"rank_by": "amount", "top_n": 5})
        assert focus_from_wire(focus_to_wire(focus)).top_selling == {"rank_by": "amount", "top_n": 5}


# --------------------------------------------------------------------------- #
# TOP-N-UNCAP (owner, 30 Sep 2026): a long ranking leaves as ordered WhatsApp parts
# --------------------------------------------------------------------------- #


class TestLongRankingParts:
    def _send_texts(self, result) -> list[str]:
        return [
            a["text"] for a in result.as_dict()["actions"] if isinstance(a, dict) and a.get("kind") == "send_message"
        ]

    def test_top_200_is_every_row_in_ordered_parts(self, session_factory, monkeypatch, route) -> None:
        import re

        route.codes = [f"SRTWC{i:04d}" for i in range(1, 201)]
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory, monkeypatch, qf=_ts(top_n=200), text_body="top 200 by quantity",
            msg_id=f"ZZT-top-selling-{uuid.uuid4().hex[:10]}", attributes=[GRANT],
        )
        assert _calls(captured)[0]["n"] == 200
        texts = self._send_texts(result)
        assert len(texts) > 1, texts
        for k, text in enumerate(texts, start=1):
            assert len(text) <= 4096, (k, len(text))
            assert f"({k}/{len(texts)})" in text.split("\n")[:3], text[:80]
        rows = [line for t in texts for line in t.split("\n") if re.match(r"^\d+\. SRTWC\d{4}: ", line)]
        assert [int(r.split(".", 1)[0]) for r in rows] == list(range(1, 201))
        assert texts[-1].rstrip().endswith(ITEM_OFFER)
        # The parts ARE the reply: nothing lost, nothing added, and the whole text is
        # still what the turn records.
        assert "\n\n".join(texts) == (result.reply or {}).get("text")
        actions = [a for a in result.as_dict()["actions"] if a.get("kind") == "send_message"]
        assert all(a["result_set"] is None and a["quick_replies"] is None for a in actions[:-1])

    def test_past_the_ceiling_the_note_opens_part_one(self, session_factory, monkeypatch, route) -> None:
        route.codes = [f"SRTWC{i:04d}" for i in range(1, 1201)]
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory, monkeypatch, qf=_ts(top_n=1500), text_body="top 1500 by quantity",
            msg_id=f"ZZT-top-selling-{uuid.uuid4().hex[:10]}", attributes=[GRANT],
        )
        assert _calls(captured)[0]["n"] == 1000
        texts = self._send_texts(result)
        assert len(texts) > 1
        assert texts[0].startswith(
            f"I can list at most the top 1,000 in one reply.\n\n(1/{len(texts)})\n"
        ), texts[0][:200]
        assert all(len(t) <= 4096 for t in texts)
        assert "\n1000. SRTWC1000: " in texts[-1] and "SRTWC1001" not in texts[-1]

    def test_top_5_is_one_message_as_before(self, session_factory, monkeypatch, route) -> None:
        _seed_contact(session_factory, variables={})
        result, _captured = _run_turn(
            session_factory, monkeypatch, qf=_ts(top_n=5), text_body="top 5 by quantity",
            msg_id=f"ZZT-top-selling-{uuid.uuid4().hex[:10]}", attributes=[GRANT],
        )
        texts = self._send_texts(result)
        assert len(texts) == 1
        assert texts[0] == (result.reply or {}).get("text")
        assert not texts[0].startswith("(1/")


class TestSplitMarkedMessage:
    def test_a_complete_run_splits_and_a_note_rides_part_one(self) -> None:
        from app.services.chatbot.engine import split_marked_message

        text = "A note.\n\n(1/2)\n*Top*\n1. A\n\n(2/2)\n2. B\nReply."
        assert split_marked_message(text) == ["A note.\n\n(1/2)\n*Top*\n1. A", "(2/2)\n2. B\nReply."]

    @pytest.mark.parametrize(
        "text",
        [
            "plain reply",
            "no first marker\n\n(2/2)\nrest",
            "(1/3)\nfirst\n\n(2/3)\nsecond",  # a part missing
            "(1/2)\nfirst\n\n(3/2)\nwrong",
        ],
    )
    def test_anything_else_goes_out_whole(self, text: str) -> None:
        from app.services.chatbot.engine import split_marked_message, split_send_actions

        assert split_marked_message(text) == [text]
        action = {"kind": "send_message", "text": text, "quick_replies": "a,b", "result_set": [1]}
        assert split_send_actions([action]) == [action]

    def test_quick_replies_and_result_set_ride_the_last_part(self) -> None:
        from app.services.chatbot.engine import split_send_actions

        attach = {"kind": "send_attachments", "attachments_src": []}
        action = {"kind": "send_message", "text": "(1/2)\na\n\n(2/2)\nb", "quick_replies": "x", "result_set": [1], "dry_run": False}
        out = split_send_actions([action, attach])
        assert out == [
            {"kind": "send_message", "text": "(1/2)\na", "quick_replies": None, "result_set": None, "dry_run": False},
            {"kind": "send_message", "text": "(2/2)\nb", "quick_replies": "x", "result_set": [1], "dry_run": False},
            attach,
        ]


def test_complete_result_splits_its_send_actions_too() -> None:
    """`/complete` serialises through `CompleteResult`, the other path n8n executes."""
    from app.services.chatbot.engine import CompleteResult

    action = {"kind": "send_message", "text": "(1/2)\na\n\n(2/2)\nb", "quick_replies": None, "result_set": None}
    out = CompleteResult(turn_id="t", actions=[action]).as_dict()["actions"]
    assert [a["text"] for a in out] == ["(1/2)\na", "(2/2)\nb"]


class TestConsoleShowsTheParts:
    """The console shows what the customer gets: a split reply is one bubble PER PART,
    in order, never the whole text once (TOP-N-UNCAP, 30 Sep 2026)."""

    def test_a_split_reply_is_one_bubble_per_part(self) -> None:
        from app.services.chatbot.console_service import _customer_texts

        whole = "note\n\n(1/2)\n*Top*\n1. A\n\n(2/2)\n2. B\nReply."
        body = {
            "reply": {"text": whole},
            "actions": [
                {"kind": "send_message", "text": "note\n\n(1/2)\n*Top*\n1. A"},
                {"kind": "send_message", "text": "(2/2)\n2. B\nReply."},
            ],
        }
        assert _customer_texts(body) == ("note\n\n(1/2)\n*Top*\n1. A", ["(2/2)\n2. B\nReply."])

    def test_an_unsplit_reply_is_unchanged(self) -> None:
        from app.services.chatbot.console_service import _customer_texts

        body = {"reply": {"text": "one"}, "actions": [{"kind": "send_message", "text": "one"}, {"kind": "send_message", "text": "two"}]}
        assert _customer_texts(body) == ("one", ["two"])
