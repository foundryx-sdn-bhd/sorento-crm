"""Phase 2 RED tests - REPORT-DRILLDOWN, the lane half: a follow-up ranking OF the rows the last
ranking listed.

`documentation/plans/chatbot/PLAN-report-drilldown-08oct.md` (design 4 to 6) and the approved
card `report-drilldown-behaviour-card.md` revision 3 (owner answers 8 Oct 2026):

* the parser reads a follow-up ranking on a new axis as `ranking_drill` "scoped" (the rows
  above), "global" (its own ask) or "unsure" (one question, nothing runs);
* scoped keeps every turn-1 filter, the period, the basis and the measure, and adds the
  declared link to exactly the rows turn 1 printed: sales agent -> customer is the customer
  CARD link (`customer_agent_ids`), every other pair the source row's own filter;
* no count named: "How many?"; a channel or month list is not a drill source; no ranking held
  is said, never guessed.

Full console turns through the real engine, lane and MCP presenter (the harness of
`test_report_ask_lane.py`); the parser and the route body are doubles.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot import test_report_ask_lane as lane_tests
from tests.chatbot.test_report_ask_lane import (  # noqa: F401  (console is a fixture)
    PERIOD_Q,
    PRODUCT_CODE,
    SEP,
    TOPN_Q,
    _e,
    _parser_output,
    _rank,
    _reply,
    console,
)

AG1, AG2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
CU1, CU2 = "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444"

ROWS: dict[str, list[dict[str, Any]]] = {
    "sales_agent": [
        {"rank": 1, "name": "AGENT ONE", "qty": 30, "amount": 3000.0, "keys": [AG1]},
        {"rank": 2, "name": "AGENT TWO", "qty": 20, "amount": 2000.0, "keys": [AG2]},
        {"rank": 3, "name": "(no agent)", "qty": 1, "amount": 10.0, "keys": ["none"]},
    ],
    "customer": [
        {"rank": 1, "name": "CUSTOMER ONE", "qty": 9, "amount": 900.0, "keys": [CU1]},
        {"rank": 2, "name": "CUSTOMER TWO", "qty": 8, "amount": 800.0, "keys": [CU2]},
    ],
    "product": [{"rank": 1, "name": PRODUCT_CODE, "qty": 5, "amount": 500.0, "keys": ["p-id"]}],
    "month": [{"rank": 1, "name": "2026-09", "qty": 5, "amount": 500.0, "keys": []}],
    "channel": [{"rank": 1, "name": "Dealer", "qty": 5, "amount": 500.0, "keys": []}],
}


def _body(args: dict[str, Any]) -> dict[str, Any]:
    group_by = args.get("group_by")
    rows = ROWS.get(group_by, []) if group_by else []
    return {
        "status": "ok", "message": None, "basis": args.get("basis", "delivered"),
        "basis_label": "delivered sales", "measure": args.get("measure", "amount"),
        "sort": args.get("sort", "desc"), "group_by": group_by,
        "group_label": (group_by or "").replace("_", " ").title() or None,
        "date_from": args.get("date_from"), "date_to": args.get("date_to"),
        "filters": [], "rows": rows, "more": 0, "total_count": len(rows),
        "total": {"qty": 50, "amount": 5000.0},
    }


@pytest.fixture(autouse=True)
def _route_body(monkeypatch):
    monkeypatch.setattr(lane_tests, "_fake_body", _body)


def _turn1(console, **overrides) -> None:
    kw = dict(top_n=3)
    kw.update(overrides)
    _text, calls = console.say(_rank(_e(PRODUCT_CODE, "product"), **kw), f"top 3 salesman for {PRODUCT_CODE}")
    assert len(calls) == 1, calls


def _drill(**overrides: Any) -> dict[str, Any]:
    """The parser's reading of "top 10 customers for these sales agents" (card rule 1)."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", entities=[],
        group_by="customer", top_n=10, rank_direction="top", rank_by=None, basis=None, measure=None,
        ranking_refine=False, ranking_drill="scoped", date_filter_start=None, date_filter_end=None,
    )
    base.update(overrides)
    return _parser_output(**base)


# ============================================================================ scoped


def test_scoped_customers_of_these_agents_keep_every_filter_and_use_the_card_link(console) -> None:
    _turn1(console)
    text, calls = console.say(_drill(), "top 10 customers for these sales agents")
    (args,) = calls
    assert args["group_by"] == "customer" and args["top_n"] == 10, args
    assert args["customer_agent_ids"] == [AG1, AG2, "none"], args
    assert "sales_agent_ids" not in args, args
    assert args["product_code"] == PRODUCT_CODE, args
    assert args["date_from"] == SEP["date_filter_start"] and args["date_to"] == SEP["date_filter_end"], args
    assert args["basis"] == "delivered" and args["measure"] == "amount" and args["sort"] == "desc", args
    assert "shown" not in args and "drill_rows" not in args, args
    assert PERIOD_Q not in text and TOPN_Q not in text, text


def test_scoped_with_no_count_asks_how_many_then_runs_the_drill(console) -> None:
    _turn1(console)
    text, calls = console.say(_drill(top_n=None), "who are the customers for these sales agents")
    assert text.strip() == TOPN_Q and calls == [], text
    _text, calls = console.say(_reply(top_n=5), "5")
    (args,) = calls
    assert args["top_n"] == 5 and args["group_by"] == "customer", args
    assert args["customer_agent_ids"] == [AG1, AG2, "none"] and args["product_code"] == PRODUCT_CODE, args


def test_scoped_agents_to_another_axis_uses_the_so_agent(console) -> None:
    _turn1(console)
    _text, calls = console.say(_drill(group_by="brand", top_n=5), "which brands do these agents sell")
    (args,) = calls
    assert args["group_by"] == "brand" and args["sales_agent_ids"] == [AG1, AG2, "none"], args
    assert "customer_agent_ids" not in args and args["product_code"] == PRODUCT_CODE, args


def test_scoped_customers_to_agents_uses_the_customer_rows(console) -> None:
    _turn1(console, group_by="customer")
    _text, calls = console.say(_drill(group_by="sales_agent", top_n=5), "who are the agents of these customers")
    (args,) = calls
    assert args["group_by"] == "sales_agent" and args["customer_ids"] == [CU1, CU2], args


def test_the_message_own_basis_measure_and_period_replace_the_held_ones(console) -> None:
    _turn1(console)
    _text, calls = console.say(
        _drill(basis="ordered", measure="qty", date_filter_start="2026-01-01", date_filter_end="2026-06-30",
               rank_direction="bottom"),
        "bottom 10 customers of these agents by quantity, ordered, first half",
    )
    (args,) = calls
    assert args["basis"] == "ordered" and args["measure"] == "qty" and args["sort"] == "asc", args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-06-30", args
    assert args["customer_agent_ids"] == [AG1, AG2, "none"], args


def test_a_drill_of_a_drill_keeps_the_first_link(console) -> None:
    _turn1(console)
    console.say(_drill(), "top 10 customers for these sales agents")
    _text, calls = console.say(_drill(group_by="product", top_n=5), "what do these customers buy")
    (args,) = calls
    assert args["group_by"] == "product" and args["customer_ids"] == [CU1, CU2], args
    assert args["customer_agent_ids"] == [AG1, AG2, "none"], args


def test_the_same_axis_scoped_ranks_within_the_rows_printed(console) -> None:
    """Review S1: "bottom 2 of these agents" ranks only the agents turn 1 printed (card rule 3)."""
    _turn1(console)
    _text, calls = console.say(_drill(group_by="sales_agent", top_n=2, rank_direction="bottom"),
                               "bottom 2 of these agents")
    (args,) = calls
    assert args["group_by"] == "sales_agent" and args["top_n"] == 2 and args["sort"] == "asc", args
    assert args["sales_agent_ids"] == [AG1, AG2, "none"] and "customer_agent_ids" not in args, args
    assert args["product_code"] == PRODUCT_CODE, args


# ============================================================================ global / unsure


def test_global_is_a_fresh_ask_and_carries_nothing(console) -> None:
    _turn1(console)
    text, calls = console.say(_drill(ranking_drill="global"), "top 10 customers overall")
    assert calls == [] and text.strip() == PERIOD_Q, text


def test_unsure_asks_one_question_and_runs_nothing(console) -> None:
    _turn1(console)
    text, calls = console.say(_drill(ranking_drill="unsure"), "top 10 customers")
    assert calls == [], calls
    assert text.strip() == "Top 10 customers of the 3 sales agents above, or across all customers?", text


def test_unsure_with_no_count_names_no_count(console) -> None:
    _turn1(console)
    text, _calls = console.say(_drill(ranking_drill="unsure", top_n=None), "customers")
    assert text.strip() == "Customers of the 3 sales agents above, or across all customers?", text


def test_the_reply_to_unsure_drills_the_rows_still_held(console) -> None:
    _turn1(console)
    console.say(_drill(ranking_drill="unsure"), "top 10 customers")
    _text, calls = console.say(_drill(), "the agents above")
    (args,) = calls
    assert args["customer_agent_ids"] == [AG1, AG2, "none"] and args["product_code"] == PRODUCT_CODE, args


# ============================================================================ never a guess


NO_RANKING = (
    "I don't have a ranking to take those from. "
    "Ask the ranking first, for example top 5 sales agents for a product this year."
)


def test_scoped_with_no_ranking_held_is_said_and_nothing_runs(console) -> None:
    text, calls = console.say(_drill(), "top 10 customers for these sales agents")
    assert calls == [] and text.strip() == NO_RANKING, text


def test_unsure_with_no_ranking_held_is_the_fresh_ask(console) -> None:
    """Review S3: with nothing to scope to, the global reading is the only one."""
    text, calls = console.say(_drill(ranking_drill="unsure"), "top 10 customers")
    assert calls == [] and text.strip() == PERIOD_Q, text


def test_unsure_after_a_month_list_is_the_fresh_ask(console) -> None:
    _turn1(console, group_by="month")
    text, calls = console.say(_drill(ranking_drill="unsure"), "top 10 customers")
    assert calls == [] and text.strip() == PERIOD_Q, text


@pytest.mark.parametrize(
    "group_by, line",
    [
        ("month", "I can't narrow by the months above. Name the period instead, for example September."),
        ("channel", "I can't narrow by the channels above. Name the channel instead, for example dealer only."),
    ],
)
def test_a_month_or_channel_list_is_not_a_drill_source(console, group_by, line) -> None:
    _turn1(console, group_by=group_by)
    text, calls = console.say(_drill(), "top 10 customers for those")
    assert calls == [] and text.strip() == line, text


def test_more_than_50_keys_is_said(console, monkeypatch) -> None:
    many = [{"rank": i, "name": f"A{i}", "qty": 1, "amount": 1.0,
             "keys": [f"{i:08d}-0000-4000-8000-000000000000"]} for i in range(1, 52)]
    monkeypatch.setitem(ROWS, "sales_agent", many)
    _turn1(console, top_n=51)
    text, calls = console.say(_drill(), "top 10 customers for these sales agents")
    assert calls == [], calls
    assert text.strip() == "That is more than 50 sales agents to narrow by. Ask for the top 50 or fewer first.", text


def test_a_refine_never_sends_the_shown_rows(console) -> None:
    _turn1(console)
    _text, calls = console.say(_reply(order_status="sales_ranking", ranking_refine=True, top_n=5,
                                      domain_hint="order", intent_hint="check_order"), "5")
    (args,) = calls
    assert args["top_n"] == 5 and "shown" not in args and "drill_rows" not in args, args


# ============================================================================ review round 1


def test_a_printed_row_with_no_keys_is_said_and_nothing_runs(console, monkeypatch) -> None:
    """Review S2: a "(blank)" row (no brand) names nothing to narrow by; never silently dropped."""
    monkeypatch.setitem(ROWS, "brand", [
        {"rank": 1, "name": "SORENTO", "qty": 5, "amount": 500.0, "keys": ["b-id"]},
        {"rank": 2, "name": "(blank)", "qty": 1, "amount": 10.0, "keys": []},
    ])
    _turn1(console, group_by="brand")
    text, calls = console.say(_drill(), "top 10 customers for those brands")
    assert calls == [], calls
    assert text.strip() == "I can't narrow by (blank) above. Ask again naming the brands you want.", text


def test_a_brand_word_in_the_drill_replaces_the_held_brand(console) -> None:
    _turn1(console)
    _text, calls = console.say(_drill(entities=[_e("Sorento", "brand")]), "top 10 customers of these agents for Sorento")
    (args,) = calls
    assert args["brand_ids"] == [console.ids["brand"]] and args["customer_agent_ids"] == [AG1, AG2, "none"], args


def test_a_channel_in_the_drill_is_applied(console) -> None:
    _turn1(console)
    _text, calls = console.say(_drill(sales_channel="dealer"), "top 10 dealer customers of these agents")
    (args,) = calls
    assert args["channel"] == "dealer", args


def test_a_product_in_the_drill_replaces_the_held_product(console) -> None:
    """Review S5 / card rule 2: the drill message's own product replaces the held one."""
    _turn1(console, entities=[_e("Sorento", "brand")])
    _text, calls = console.say(_drill(entities=[_e(PRODUCT_CODE, "product")]), f"top 10 customers of these agents for {PRODUCT_CODE}")
    (args,) = calls
    assert args["product_code"] == PRODUCT_CODE and args["brand_ids"] == [console.ids["brand"]], args


def test_a_dealer_drill_to_a_staff_axis_is_refused() -> None:
    from app.services.chatbot.lanes.business import report_ask

    frame = {"group_by": "product", "basis": "delivered", "measure": "amount", "date_from": "2026-09-01",
             "date_to": "2026-09-30", "shown": {"group_by": "product", "rows": [{"name": "X", "keys": ["p"]}]}}
    verdict = {"order_status": "sales_ranking", "group_by": "customer", "ranking_drill": "scoped", "top_n": 5,
               "sales_ranking_frame": frame}
    outcome, line = report_ask.settle(None, verdict, [], dealer=True)
    assert outcome is None and line == report_ask.DEALER_REFUSAL, (outcome, line)


def test_the_frame_keeps_shown_rows_only_for_its_own_axis() -> None:
    from app.services.chatbot.lanes.business.fetch import _report_ask_output

    rows = {"group_by": "customer", "rows": [{"name": "X", "keys": ["c"]}]}
    args = {"group_by": "sales_agent", "basis": "delivered"}
    out = _report_ask_output({"response": "Top 1 sales agent", "drill_rows": rows}, {"semantic_input": {"report_ask_args": args}})
    assert "shown" not in out["sales_ranking_frame"], out["sales_ranking_frame"]
    same = {**rows, "group_by": "sales_agent"}
    out = _report_ask_output({"response": "Top 1 sales agent", "drill_rows": same}, {"semantic_input": {"report_ask_args": args}})
    assert out["sales_ranking_frame"]["shown"] == same, out


# ============================================================================ review round 2


def test_the_same_axis_keeps_the_held_count_and_direction(console) -> None:
    _turn1(console, rank_direction="bottom")
    _text, calls = console.say(_drill(group_by="sales_agent", top_n=None, rank_direction=None, measure="qty"),
                               "these agents by quantity")
    (args,) = calls
    assert args["top_n"] == 3 and args["sort"] == "asc" and args["measure"] == "qty", args
    assert args["sales_agent_ids"] == [AG1, AG2, "none"], args


def test_a_message_filter_on_the_link_param_narrows_within_the_rows(console, monkeypatch) -> None:
    """Review round 2: "top brands of these agents, AGENT A only" keeps AGENT A when listed,
    never silently drops it."""
    monkeypatch.setitem(ROWS, "sales_agent", [
        {"rank": 1, "name": "AGENT A", "qty": 5, "amount": 500.0, "keys": [console.ids["agent"]]},
        {"rank": 2, "name": "AGENT TWO", "qty": 4, "amount": 400.0, "keys": [AG2]},
    ])
    _turn1(console)
    _text, calls = console.say(_drill(group_by="brand", top_n=5, entities=[_e("AGENT A", "sales_agent")]),
                               "top 5 brands of these agents, AGENT A only")
    (args,) = calls
    assert args["sales_agent_ids"] == [console.ids["agent"]], args


def test_a_message_filter_outside_the_rows_is_said(console) -> None:
    _turn1(console)
    text, calls = console.say(_drill(group_by="brand", top_n=5, entities=[_e("AGENT A", "sales_agent")]),
                              "top 5 brands of these agents, AGENT A only")
    assert calls == [], calls
    assert text.strip() == "AGENT A is not one of the sales agents above.", text
