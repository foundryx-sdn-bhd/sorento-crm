"""Phase 2 RED tests - REPORT-ENGINE: rankings CLARIFY, never default (owner rulings, 4 Oct 2026).

1. `crm_report_ask` / `sales_ranking`: when the ask names no metric the bot asks
   `By quantity or by amount?`; when it names no basis it asks
   `Delivered, ordered or outstanding?`. Both are required fields of the same ask as the
   period and how-many, asked AFTER them, metric before basis. The metric is NOT asked when
   there is no ranking (a plain total, or a month trend: both qty and amount are shown); the
   basis is always asked when not given.
2. Top selling: a null (or `unclear`) basis is asked (`Delivered (transferred to DO) or
   ordered?`) after the metric question, and a `crm_top_selling_report` fetch ALWAYS carries an
   explicit `basis`.
3. ONE shared function decides the next question for BOTH lanes, metric then basis. Pinned
   here as `app.services.chatbot.lanes.business.ranking_ask.next_question(*, metric_settled,
   basis_settled, basis_line) -> (axis, line) | None` (axis `"metric"` | `"basis"`), with the
   three lines as module constants. The NAMES are the tester's pin (the owner named none);
   the behaviour is the owner's.
4. Answer binding is SEMANTIC: the ranking reads the parser's keys on the answering turn
   (metric: `measure` qty|amount, or the top selling key `rank_by` quantity|amount; basis:
   `basis`), never the message text.

Whole console turns (real engine, lane and MCP presenter; parser and MCP transport doubles),
the way `test_report_ask_lane.py` and `test_top_selling_lane.py` drive them. The answering-turn
parser outputs below are what the parser emits for a bare reply: "qty" under the metric
question is `order_status "top_selling"`, `rank_by "quantity"`, everything else null.
"""
from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_report_ask_lane import (  # noqa: F401 - fixtures + helpers
    DIMENSION_REFUSAL,
    GIVE_UP,
    OWNER_MESSAGE,
    PERIOD_Q,
    SEP,
    SEP_RANGE,
    THIS_YEAR,
    TOPN_Q,
    _e,
    _hit,
    _refine,
    _rank,
    _reply,
    _say_all,
    _seed_wt,
    _t1,
    console,
    dealer,
)
from tests.chatbot.test_top_selling_lane import (  # noqa: F401 - fixtures + helpers
    ASK_BASIS as TS_BASIS_Q,
    ASK_METRIC as TS_METRIC_Q,
    _calls as ts_calls,
    _turn as ts_turn,
    _ts,
    route,
)

METRIC_Q = "By quantity or by amount?"
BASIS_Q = "Delivered, ordered or outstanding?"
TOP_SELLING = "crm_top_selling_report"
SENTINEL_METRIC = "SENTINEL SHARED METRIC LINE"
SENTINEL_BASIS = "SENTINEL SHARED BASIS LINE"


# --------------------------------------------------------------------------- #
# Parser outputs
# --------------------------------------------------------------------------- #


def _fresh(*entities: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The owner example, "top 3 salesman for sorento water closet this year", with the
    metric and the basis UNSAID (the parser emits null for both)."""
    base: dict[str, Any] = dict(measure=None, basis=None, **THIS_YEAR)
    base.update(overrides)
    return _rank(*entities, **base)


def _sorento_wc(**overrides: Any) -> dict[str, Any]:
    return _fresh(_hit("sorento", "brand"), _hit("water closet", "category"), **overrides)


def _answer_keys(**keys: Any) -> dict[str, Any]:
    """The parser's reading of a bare reply under a ranking question: only the key it
    answers is filled, everything else null, entities []."""
    status = "sales_ranking" if "measure" in keys and "rank_by" not in keys else "top_selling"
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        order_status=status, domain_in_message=False, entities=[],
        rank_by=None, basis=None, measure=None, rank_group=None, rank_direction=None,
        top_n=None, group_by=None, ranking_refine=None,
    )
    base.update(keys)
    return _parser_output(**base)


def _run_args(calls: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(calls) == 1, calls
    return calls[0]


def _assert_no_top_selling(log: list[tuple[str, dict[str, Any]]], text: str) -> None:
    assert TOP_SELLING not in [n for n, _a in log], (log, text)
    assert "selling items" not in text, text


# --------------------------------------------------------------------------- #
# Ranking lane (a) to (c): the owner example, step by step
# --------------------------------------------------------------------------- #


def test_a_no_metric_and_no_basis_asks_the_metric_and_runs_nothing(console) -> None:
    _seed_wt(console)
    text, calls = console.say(_sorento_wc(), OWNER_MESSAGE)
    assert text.strip() == METRIC_Q, text
    assert calls == [], calls


def TOOL_NAMES(log: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [n for n, _a in log]


def test_b_the_metric_answer_qty_asks_the_basis_and_runs_nothing(console, monkeypatch) -> None:
    """Parser output for "qty": order_status top_selling, rank_by quantity. It binds to the OPEN
    ranking question: no top selling ask starts and the question is not dropped."""
    _seed_wt(console)
    console.say(_sorento_wc(), OWNER_MESSAGE)
    text, log = _say_all(console, monkeypatch, _answer_keys(rank_by="quantity"), "qty")
    assert text.strip() == BASIS_Q, text
    assert TOOL_NAMES(log) == [], log


def test_c_the_basis_answer_outstanding_runs_with_the_first_messages_ask(console, monkeypatch) -> None:
    wt = _seed_wt(console)
    console.say(_sorento_wc(), OWNER_MESSAGE)
    console.say(_answer_keys(rank_by="quantity"), "qty")
    text, log = _say_all(console, monkeypatch, _answer_keys(basis="outstanding"), "outstanding")
    _assert_no_top_selling(log, text)
    args = _run_args([a for n, a in log if n == "crm_report_ask"])
    assert args["measure"] == "qty" and args["basis"] == "outstanding", args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["category_ids"] == [wt["category"]], args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args


@pytest.mark.parametrize(
    "keys, measure",
    [
        ({"rank_by": "quantity"}, "qty"),
        ({"rank_by": "amount"}, "amount"),
        ({"measure": "qty"}, "qty"),
        ({"measure": "amount"}, "amount"),
    ],
    ids=["rank_by_quantity", "rank_by_amount", "measure_qty", "measure_amount"],
)
def test_the_metric_binds_from_either_parser_key(console, keys, measure) -> None:
    _seed_wt(console)
    console.say(_sorento_wc(), OWNER_MESSAGE)
    text, calls = console.say(_answer_keys(**keys), "x")
    assert text.strip() == BASIS_Q and calls == [], (text, calls)
    _text, calls = console.say(_answer_keys(basis="delivered"), "delivered")
    assert _run_args(calls)["measure"] == measure, calls


@pytest.mark.parametrize("basis", ["delivered", "ordered", "outstanding"])
def test_the_basis_binds_from_the_parsers_basis_key(console, basis) -> None:
    _seed_wt(console)
    console.say(_sorento_wc(), OWNER_MESSAGE)
    console.say(_answer_keys(rank_by="amount"), "amount")
    _text, calls = console.say(_answer_keys(basis=basis), basis)
    args = _run_args(calls)
    assert args["basis"] == basis and args["measure"] == "amount", args


def test_the_answer_text_is_never_read_only_the_parsers_keys(console) -> None:
    """Owner rule: the parser decides. The text says "qty" but the parser gave no key: a miss."""
    _seed_wt(console)
    console.say(_sorento_wc(), OWNER_MESSAGE)
    text, calls = console.say(_reply(), "qty")
    assert calls == [] and METRIC_Q in text, (text, calls)


# --------------------------------------------------------------------------- #
# (d), (e): answered in one message
# --------------------------------------------------------------------------- #


def test_d_one_reply_carrying_the_metric_and_the_basis_runs_at_once(console) -> None:
    _seed_wt(console)
    console.say(_sorento_wc(), OWNER_MESSAGE)
    text, calls = console.say(_answer_keys(rank_by="quantity", basis="delivered"), "qty delivered")
    args = _run_args(calls)
    assert args["measure"] == "qty" and args["basis"] == "delivered", args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert BASIS_Q not in text and METRIC_Q not in text, text


def test_e_a_first_message_naming_everything_runs_at_once(console) -> None:
    wt = _seed_wt(console)
    text, calls = console.say(
        _sorento_wc(measure="qty", basis="delivered"), OWNER_MESSAGE + " by qty delivered"
    )
    args = _run_args(calls)
    assert args["measure"] == "qty" and args["basis"] == "delivered", args
    assert args["category_ids"] == [wt["category"]], args
    assert BASIS_Q not in text and METRIC_Q not in text, text


@pytest.mark.parametrize(
    "named, asks",
    [({"measure": "amount"}, BASIS_Q), ({"basis": "ordered"}, METRIC_Q), ({"basis": "outstanding"}, METRIC_Q)],
    ids=["metric_given_asks_basis", "ordered_given_asks_metric", "outstanding_given_asks_metric"],
)
def test_a_first_message_naming_one_of_the_two_asks_only_the_other(console, named, asks) -> None:
    _seed_wt(console)
    text, calls = console.say(_sorento_wc(**named), OWNER_MESSAGE)
    assert text.strip() == asks and calls == [], (text, calls)


# --------------------------------------------------------------------------- #
# Order among the questions: period, how many, metric, basis
# --------------------------------------------------------------------------- #


def test_the_questions_come_period_then_how_many_then_metric_then_basis(console) -> None:
    text, calls = console.say(
        _fresh(_hit("sorento", "brand"), top_n=None, date_filter_start=None, date_filter_end=None),
        "top salesman for sorento",
    )
    assert text.strip() == PERIOD_Q and calls == [], text
    text, calls = console.say(_reply(**SEP), "last month")
    assert text.strip() == TOPN_Q and calls == [], text
    text, calls = console.say(_reply(top_n=5), "5")
    assert text.strip() == METRIC_Q and calls == [], text
    text, calls = console.say(_answer_keys(rank_by="amount"), "amount")
    assert text.strip() == BASIS_Q and calls == [], text
    _text, calls = console.say(_answer_keys(basis="ordered"), "ordered")
    args = _run_args(calls)
    assert args["top_n"] == 5 and args["measure"] == "amount" and args["basis"] == "ordered", args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert args["brand_ids"] == [console.ids["brand"]], args


def test_the_metric_is_never_asked_before_the_period(console) -> None:
    text, calls = console.say(
        _fresh(_hit("sorento", "brand"), date_filter_start=None, date_filter_end=None), "top 3 salesman for sorento"
    )
    assert text.strip() == PERIOD_Q and calls == [], text


# --------------------------------------------------------------------------- #
# (f): no ranking, no metric question
# --------------------------------------------------------------------------- #


def test_f_a_plain_total_asks_only_the_basis(console) -> None:
    text, calls = console.say(
        _fresh(_hit("sorento", "brand"), group_by=None, top_n=None), "how much did we sell of sorento"
    )
    assert text.strip() == BASIS_Q and calls == [], (text, calls)
    _text, calls = console.say(_answer_keys(basis="delivered"), "delivered")
    args = _run_args(calls)
    assert not args.get("group_by") and "top_n" not in args and args["basis"] == "delivered", args


def test_f_a_month_trend_asks_only_the_basis(console) -> None:
    text, calls = console.say(
        _fresh(_hit("sorento", "brand"), group_by="month", top_n=None), "sorento sales by month this year"
    )
    assert text.strip() == BASIS_Q and calls == [], (text, calls)
    _text, calls = console.say(_answer_keys(basis="ordered"), "ordered")
    args = _run_args(calls)
    assert args["group_by"] == "month" and args["basis"] == "ordered", args


@pytest.mark.parametrize("group_by", [None, "month"])
def test_f_a_total_or_trend_with_the_basis_named_runs_at_once(console, group_by) -> None:
    _text, calls = console.say(
        _fresh(_hit("sorento", "brand"), group_by=group_by, top_n=None, basis="ordered"), "sorento sales ordered"
    )
    assert _run_args(calls)["basis"] == "ordered", calls


# --------------------------------------------------------------------------- #
# (g): a refine never asks
# --------------------------------------------------------------------------- #


def test_g_a_refine_never_asks_the_held_frame_has_both(console) -> None:
    _t1(console)
    text, calls = console.say(_refine(top_n=5), "5")
    args = _run_args(calls)
    assert args["top_n"] == 5 and args["measure"] == "amount" and args["basis"] == "delivered", args
    assert METRIC_Q not in text and BASIS_Q not in text, text


def test_g_a_ranking_settled_by_answers_is_refined_without_asking(console) -> None:
    console.say(_sorento_wc(), OWNER_MESSAGE)
    console.say(_answer_keys(rank_by="quantity"), "qty")
    _text, calls = console.say(_answer_keys(basis="outstanding"), "outstanding")
    assert calls, "setup: the ranking did not run"
    text, calls = console.say(_refine(top_n=5), "5")
    args = _run_args(calls)
    assert args["measure"] == "qty" and args["basis"] == "outstanding" and args["top_n"] == 5, args
    assert METRIC_Q not in text and BASIS_Q not in text, text


# --------------------------------------------------------------------------- #
# (h): a reply the parser gives no key for is a miss: ask again once, then the helper's give-up
# --------------------------------------------------------------------------- #


def test_h_a_metric_reply_with_no_key_asks_again_then_gives_up(console) -> None:
    console.say(_sorento_wc(), OWNER_MESSAGE)
    text, calls = console.say(_reply(), "whatever")
    assert calls == [] and METRIC_Q in text, (text, calls)
    text, calls = console.say(_reply(), "huh")
    assert calls == [], calls
    assert text.strip() == GIVE_UP.format(word="huh"), text


def test_h_a_basis_reply_with_no_key_asks_again_then_gives_up(console) -> None:
    console.say(_sorento_wc(), OWNER_MESSAGE)
    console.say(_answer_keys(rank_by="quantity"), "qty")
    text, calls = console.say(_reply(), "whatever")
    assert calls == [] and BASIS_Q in text, (text, calls)
    text, calls = console.say(_reply(), "huh")
    assert calls == [], calls
    assert text.strip() == GIVE_UP.format(word="huh"), text


def test_h_a_miss_then_an_answer_still_runs(console) -> None:
    console.say(_sorento_wc(), OWNER_MESSAGE)
    text, calls = console.say(_reply(), "whatever")
    assert METRIC_Q in text and calls == []
    text, calls = console.say(_answer_keys(rank_by="amount"), "amount")
    assert text.strip() == BASIS_Q and calls == [], (text, calls)


def test_h_the_default_top_10_is_not_what_a_metric_miss_falls_back_to(console) -> None:
    """The count falls back to top 10 (crew ruling); the metric and the basis have no default."""
    console.say(_sorento_wc(), OWNER_MESSAGE)
    console.say(_reply(), "x")
    _text, calls = console.say(_reply(), "y")
    assert calls == [], calls


# --------------------------------------------------------------------------- #
# (i): a dealer gets the same questions, the refusals are unchanged
# --------------------------------------------------------------------------- #


def test_i_a_dealer_is_asked_the_same_questions(dealer) -> None:
    text, calls = dealer.say(_fresh(_e("Sorento", "brand"), group_by="product"), "top 3 products for Sorento")
    assert text.strip() == METRIC_Q and calls == [], (text, calls)
    text, calls = dealer.say(_answer_keys(rank_by="quantity"), "qty")
    assert text.strip() == BASIS_Q and calls == [], (text, calls)
    _text, calls = dealer.say(_answer_keys(basis="delivered"), "delivered")
    args = _run_args(calls)
    assert args["measure"] == "qty" and args["basis"] == "delivered" and args["group_by"] == "product", args
    assert args["brand_ids"] == [dealer.ids["brand"]], args


def test_i_a_dealers_staff_breakdown_is_refused_before_any_question(dealer) -> None:
    text, calls = dealer.say(_fresh(_e("Sorento", "brand"), group_by="sales_agent"), "top 3 salesman for Sorento")
    assert text.strip() == DIMENSION_REFUSAL and calls == [], (text, calls)


def test_i_a_dealers_unknown_word_is_still_said_before_any_question(dealer) -> None:
    text, calls = dealer.say(_fresh(_e("Zzz", "brand"), group_by="product"), "top products for Zzz brand")
    assert text.strip() == "I don't know 'Zzz' as a brand.", text
    assert calls == []


# --------------------------------------------------------------------------- #
# (j): no silent default anywhere
# --------------------------------------------------------------------------- #


def test_j_a_null_null_verdict_settles_to_the_metric_question_with_no_default_in_the_args() -> None:
    from app.services.chatbot.lanes.business import report_ask

    outcome, line = report_ask.settle(None, _fresh(), [])
    assert line is None and outcome is not None
    assert not outcome.done and outcome.reply == METRIC_Q, outcome
    args = (outcome.extras or {}).get("args") or {}
    assert args.get("measure") != "amount" and args.get("basis") != "delivered", args
    assert "measure" not in args and "basis" not in args, args


def test_j_fresh_args_holds_no_amount_or_delivered_fallback() -> None:
    from app.services.chatbot.lanes.business import report_ask

    source = inspect.getsource(report_ask._fresh_args)
    assert not re.search(r'else\s+"(amount|delivered)"', source), source
    args, line = report_ask._fresh_args(None, _fresh(), [])
    assert line is None and args is not None
    assert "measure" not in args and "basis" not in args, args


def test_j_an_unsettled_basis_word_is_not_carried_as_delivered() -> None:
    from app.services.chatbot.lanes.business import report_ask

    for basis in (None, "unclear", "invoiced"):
        args, _line = report_ask._fresh_args(None, _fresh(basis=basis, measure="qty"), [])
        assert args is not None and args.get("basis") != "delivered", (basis, args)


# --------------------------------------------------------------------------- #
# Top selling (k) to (n)
# --------------------------------------------------------------------------- #


def _top_selling(session_factory, monkeypatch, qf: dict[str, Any], body: str):
    return ts_turn(session_factory, monkeypatch, qf, body)


def test_k_top_selling_with_no_basis_asks_the_basis_and_does_not_fetch(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis=None, top_n=10), "top 10 selling sorento")
    assert reply.strip() == TS_BASIS_Q, reply
    assert ts_calls(captured) == [], captured


def test_k_an_unclear_basis_asks_the_basis_too(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis="unclear", top_n=10), "top orders")
    assert reply.strip() == TS_BASIS_Q and ts_calls(captured) == [], (reply, captured)


def test_l_both_null_asks_the_metric_first_then_the_basis(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = _top_selling(session_factory, monkeypatch, _ts(rank_by=None, basis=None, top_n=10), "top 10 selling sorento")
    assert reply.strip() == TS_METRIC_Q and ts_calls(captured) == [], reply
    reply, captured = _top_selling(session_factory, monkeypatch, _answer_keys(rank_by="quantity"), "qty")
    assert reply.strip() == TS_BASIS_Q and ts_calls(captured) == [], reply
    _reply, captured = _top_selling(session_factory, monkeypatch, _answer_keys(basis="ordered"), "ordered")
    args = ts_calls(captured)[0]
    assert args["rank_by"] == "quantity" and args["basis"] == "ordered" and args["n"] == 10, args


def test_m_the_basis_answer_ordered_runs_with_ordered_sent_explicitly(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    reply, _captured = _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis=None, top_n=10), "top 10 selling sorento")
    assert reply.strip() == TS_BASIS_Q
    _reply, captured = _top_selling(session_factory, monkeypatch, _answer_keys(basis="ordered"), "ordered")
    (args,) = ts_calls(captured)
    assert args["basis"] == "ordered" and args["rank_by"] == "amount" and args["n"] == 10, args


def test_n_the_basis_answer_delivered_runs_with_delivered_sent_explicitly(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis=None, top_n=10), "top 10 selling sorento")
    _reply, captured = _top_selling(session_factory, monkeypatch, _answer_keys(basis="delivered"), "delivered")
    (args,) = ts_calls(captured)
    assert "basis" in args and args["basis"] == "delivered", args


def test_n_a_first_message_naming_delivered_sends_it_explicitly(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    _reply, captured = _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis="delivered", top_n=10), "top 10 delivered by amount")
    (args,) = ts_calls(captured)
    assert args["basis"] == "delivered", args


def test_n_a_fetch_with_no_settled_basis_makes_no_call(session_factory) -> None:
    """`fetch.py` sends `basis` only when set today; the route then defaults to delivered. A
    fetch for the ranking must never run without an explicit basis."""
    import json

    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    def payload(slot: dict[str, Any]) -> dict[str, Any]:
        return {
            "gate": {"compatible_entities": []},
            "tier_gate": None,
            "ctx": {
                "parse": {
                    "output": _parser_output(
                        domain_hint="order", intent_hint="check_order", order_status="top_selling",
                        entities=[], top_selling=slot,
                    )
                },
                "contact": {"id": 437264483},
                "access": {"attributes": ["sales_orders.sales_report"]},
            },
        }

    calls: list[tuple[str, dict[str, Any]]] = []

    def _call(name, args):
        calls.append((name, dict(args)))
        return json.dumps({"has_result": False, "response": "x", "result_set": []})

    run_fetch(payload({"rank_by": "quantity", "top_n": 5}), services=FetchServices(mcp_call=_call))
    assert calls == [], f"ran a top selling fetch with no basis: {calls}"
    run_fetch(payload({"rank_by": "quantity", "top_n": 5, "basis": "delivered"}), services=FetchServices(mcp_call=_call))
    (name, args), = calls
    assert name == TOP_SELLING and args["basis"] == "delivered", calls


def test_top_selling_question_order_is_group_metric_then_basis() -> None:
    from app.services.chatbot.lanes.business import _top_selling_question

    axis, _line = _top_selling_question({"rank_group": "unclear", "rank_by": None, "basis": None})
    assert axis == "group"
    assert _top_selling_question({"rank_by": None, "basis": None}) == ("metric", TS_METRIC_Q)
    assert _top_selling_question({"rank_by": "amount", "basis": None}) == ("basis", TS_BASIS_Q)
    assert _top_selling_question({"rank_by": "amount", "basis": "unclear"}) == ("basis", TS_BASIS_Q)
    assert _top_selling_question({"rank_by": "amount", "basis": "ordered"}) is None
    assert _top_selling_question({"rank_by": "quantity", "basis": "delivered"}) is None
    assert _top_selling_question({"detail_code": "SRTWT7445"}) is None


# --------------------------------------------------------------------------- #
# One shared ask path (ruling 3)
# --------------------------------------------------------------------------- #


def test_the_shared_function_decides_metric_then_basis() -> None:
    from app.services.chatbot.lanes.business import ranking_ask

    assert ranking_ask.next_question(metric_settled=False, basis_settled=False, basis_line="B") == (
        "metric", METRIC_Q,
    )
    assert ranking_ask.next_question(metric_settled=False, basis_settled=True, basis_line="B") == (
        "metric", METRIC_Q,
    )
    assert ranking_ask.next_question(metric_settled=True, basis_settled=False, basis_line="B") == ("basis", "B")
    assert ranking_ask.next_question(metric_settled=True, basis_settled=True, basis_line="B") is None


def test_both_lanes_use_the_shared_lines() -> None:
    from app.services.chatbot.lanes.business import (
        SALES_RANKING_ASK_BASIS,
        TOP_SELLING_ASK_BASIS,
        TOP_SELLING_ASK_METRIC,
        ranking_ask,
    )

    assert TOP_SELLING_ASK_METRIC == ranking_ask.METRIC_LINE == METRIC_Q
    assert TOP_SELLING_ASK_BASIS == ranking_ask.TOP_SELLING_BASIS_LINE == TS_BASIS_Q
    assert SALES_RANKING_ASK_BASIS == ranking_ask.RANKING_BASIS_LINE == BASIS_Q


def _sentinel(*_args: Any, **kw: Any):
    if not kw.get("metric_settled"):
        return "metric", SENTINEL_METRIC
    if not kw.get("basis_settled"):
        return "basis", SENTINEL_BASIS
    return None


def test_kill_the_ranking_lane_asks_through_the_shared_function(console, monkeypatch) -> None:
    from app.services.chatbot.lanes.business import ranking_ask

    monkeypatch.setattr(ranking_ask, "next_question", _sentinel)
    text, calls = console.say(_fresh(_hit("sorento", "brand")), "top 3 salesman for sorento this year")
    assert text.strip() == SENTINEL_METRIC and calls == [], (text, calls)


def test_kill_the_ranking_lanes_basis_line_comes_from_the_shared_function(console, monkeypatch) -> None:
    from app.services.chatbot.lanes.business import ranking_ask

    monkeypatch.setattr(ranking_ask, "next_question", _sentinel)
    text, calls = console.say(_fresh(_hit("sorento", "brand"), measure="qty"), "top 3 salesman for sorento by qty")
    assert text.strip() == SENTINEL_BASIS and calls == [], (text, calls)


def test_kill_top_selling_asks_through_the_shared_function(session_factory, monkeypatch, route) -> None:
    from app.services.chatbot.lanes.business import ranking_ask

    monkeypatch.setattr(ranking_ask, "next_question", _sentinel)
    _seed_contact(session_factory, variables={})
    reply, captured = _top_selling(session_factory, monkeypatch, _ts(rank_by=None, basis=None, top_n=10), "top 10 selling sorento")
    assert reply.strip() == SENTINEL_METRIC and ts_calls(captured) == [], reply


def test_kill_top_sellings_basis_line_comes_from_the_shared_function(session_factory, monkeypatch, route) -> None:
    from app.services.chatbot.lanes.business import ranking_ask

    monkeypatch.setattr(ranking_ask, "next_question", _sentinel)
    _seed_contact(session_factory, variables={})
    reply, captured = _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis=None, top_n=10), "top 10 selling sorento")
    assert reply.strip() == SENTINEL_BASIS and ts_calls(captured) == [], reply


def test_the_same_unsettled_state_asks_the_metric_line_first_in_both_lanes() -> None:
    """The cross-lane contract is pinned by the per-lane turn tests above (`test_a_...` and
    `test_l_...` both expect the metric line, `test_b_...` / `test_l_...` each lane's own basis
    line) plus the shared lines test: one metric line, two basis lines."""
    from app.services.chatbot.lanes.business import ranking_ask

    assert ranking_ask.next_question(metric_settled=False, basis_settled=False, basis_line=BASIS_Q)[1] == METRIC_Q
    assert ranking_ask.next_question(metric_settled=False, basis_settled=False, basis_line=TS_BASIS_Q)[1] == METRIC_Q
    assert ranking_ask.next_question(metric_settled=True, basis_settled=False, basis_line=BASIS_Q)[1] == BASIS_Q
    assert ranking_ask.next_question(metric_settled=True, basis_settled=False, basis_line=TS_BASIS_Q)[1] == TS_BASIS_Q


# --------------------------------------------------------------------------- #
# (o), (p): the parser prompt
# --------------------------------------------------------------------------- #


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_o_the_top_selling_addendum_no_longer_defaults_a_missing_basis_to_delivered() -> None:
    from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

    flat = _flat(TOP_SELLING_ADDENDUM)
    assert "(the default, delivered)" not in flat
    assert '"selling" / "sold" with no basis word -> null (the bot asks' in flat
    assert "default" not in flat.split("== BASIS: which sale is counted ==", 1)[1].split("== RANK_GROUP", 1)[0].lower()


def test_o_the_own_question_section_maps_delivered_as_well_as_ordered() -> None:
    from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM

    flat = _flat(TOP_SELLING_ADDENDUM)
    anchor = 'after "Delivered (transferred to DO) or ordered?"'
    assert anchor in flat
    bullet = flat.split(anchor, 1)[1].split(" - after ", 1)[0]
    assert '"ordered" -> basis "ordered"' in bullet, bullet
    assert '"delivered" -> basis "delivered"' in bullet, bullet


def test_p_the_report_ask_addendum_teaches_the_basis_and_metric_answers() -> None:
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM, SEMANTIC_PARSER_PROMPT

    assert REPORT_ASK_ADDENDUM in SEMANTIC_PARSER_PROMPT
    flat = _flat(REPORT_ASK_ADDENDUM)
    basis_anchor = 'after "Delivered, ordered or outstanding?"'
    assert basis_anchor in flat
    near = flat.split(basis_anchor, 1)[1][:260]
    for needle in ('basis "delivered"', 'basis "ordered"', 'basis "outstanding"'):
        assert needle in near, (needle, near)
    metric_anchor = 'after "By quantity or by amount?" inside a sales ranking'
    assert metric_anchor in flat
    near = flat.split(metric_anchor, 1)[1][:260]
    for needle in ('measure "qty"', 'measure "amount"', 'order_status "sales_ranking"'):
        assert needle in near, (needle, near)


# --------------------------------------------------------------------------- #
# Review S2: a top selling slot carrying basis "outstanding" never runs delivered silently
# --------------------------------------------------------------------------- #


def test_s2_outstanding_is_not_an_answer_to_the_top_selling_basis_question() -> None:
    from app.services.chatbot.turn.apply import _top_selling_waiting

    assert _top_selling_waiting("basis", {"basis": "delivered"}) is True
    assert _top_selling_waiting("basis", {"basis": "ordered"}) is True
    assert _top_selling_waiting("basis", {"basis": "outstanding"}) is False
    assert _top_selling_waiting("basis", {"basis": None}) is False


def test_s2_the_question_helper_asks_the_basis_for_outstanding() -> None:
    from app.services.chatbot.lanes.business import _top_selling_question

    assert _top_selling_question({"rank_by": "amount", "basis": "outstanding"}) == ("basis", TS_BASIS_Q)


def test_s2_a_top_selling_verdict_with_basis_outstanding_asks_the_basis_and_fetches_nothing(
    session_factory, monkeypatch, route
) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis="outstanding", top_n=10), "top 10 selling outstanding")
    assert reply.strip() == TS_BASIS_Q, reply
    assert ts_calls(captured) == [], captured


def test_s2_an_outstanding_answer_to_the_top_selling_basis_question_asks_again(
    session_factory, monkeypatch, route
) -> None:
    _seed_contact(session_factory, variables={})
    reply, _captured = _top_selling(session_factory, monkeypatch, _ts(rank_by="amount", basis=None, top_n=10), "top 10 selling sorento")
    assert reply.strip() == TS_BASIS_Q
    reply, captured = _top_selling(session_factory, monkeypatch, _answer_keys(basis="outstanding"), "outstanding")
    assert reply.strip() == TS_BASIS_Q, reply
    assert ts_calls(captured) == [], captured
    _reply, captured = _top_selling(session_factory, monkeypatch, _answer_keys(basis="delivered"), "delivered")
    assert ts_calls(captured)[0]["basis"] == "delivered", captured


# --------------------------------------------------------------------------- #
# Review S6: a category word that resolves to no ids and no unknown/ambiguous entry is said
# --------------------------------------------------------------------------- #


def test_s6_a_category_word_resolving_to_nothing_is_said_never_dropped() -> None:
    """`db is None` makes `_top_selling_category_ids` return `([], True, [], [])`: the word was
    named, nothing matched, and nothing is flagged unknown. The filter must not vanish quietly."""
    from app.services.chatbot.lanes.business import report_ask

    verdict = _fresh(measure="qty", basis="delivered", report_ask_words={"category": ["water closet"]})
    args, line = report_ask._fresh_args(None, verdict, [])
    assert args is None, args
    assert line == "I don't know 'water closet' as a category.", line


def test_s6_the_lane_says_so_and_runs_nothing_when_the_resolver_finds_no_ids(console, monkeypatch) -> None:
    from app.services.chatbot.lanes import business as business_pkg

    monkeypatch.setattr(business_pkg, "_top_selling_category_ids", lambda slot, *, db: ([], True, [], []))
    text, calls = console.say(
        _fresh(_hit("water closet", "category"), measure="qty", basis="delivered"), "top 3 salesman for water closet"
    )
    assert text.strip() == "I don't know 'water closet' as a category.", text
    assert calls == [], calls
