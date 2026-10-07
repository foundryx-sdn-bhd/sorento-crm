"""REPORT-ENGINE round 6 (AC-HT8-16 to 18): red tests. Open-state and parser keys only."""
from __future__ import annotations

import re

import pytest

from tests.chatbot._turn_helpers import build_policy, verdict

ZH = re.compile(r"[一-鿿]")

FULL = dict(order_status="sales_ranking", domain_hint="order", intent_hint="check_order", group_by="sales_agent",
            top_n=3, basis="delivered", measure="amount", ranking_refine=False,
            date_filter_start="2026-01-01", date_filter_end="2026-12-31")

# what the ask is missing -> the verdict keys that answer it
ANSWERS = {
    "period": ({"date_filter_start": None, "date_filter_end": None},
               {"date_filter_start": "2026-01-01", "date_filter_end": "2026-12-31"}),
    "top_n": ({"top_n": None}, {"top_n": 5}),
    "metric": ({"measure": None}, {"measure": "qty"}),
    "basis": ({"basis": None}, {"basis": "ordered"}),
}


def _open_slot(missing, **held):
    from app.services.chatbot.lanes.business import report_ask as ra

    fresh = verdict(**{**FULL, **ANSWERS[missing][0], **held})
    outcome, line = ra.settle(None, fresh, [])
    assert line is None and outcome is not None and outcome.slot, (missing, outcome)
    assert outcome.slot["asking"] == missing
    return outcome.slot


def _answer(missing, slot, text="x", **extra):
    """The reply turn as the engine builds it: `required_fields.reply_verdict`, then `settle`."""
    from app.services.chatbot import required_fields
    from app.services.chatbot.lanes.business import report_ask as ra

    v = verdict(order_status="sales_ranking", domain_hint="order", intent_hint="check_order",
                ranking_refine=False, **ANSWERS[missing][1], **extra)
    rerouted, rule = required_fields.reply_verdict(v, slot, text)
    assert rule == "required_ask_answer", rule
    return rerouted, ra


@pytest.mark.parametrize("missing", list(ANSWERS))
def test_ranking_answer_ignores_a_channel_the_parser_put_on_it(missing):
    slot = _open_slot(missing)
    rerouted, ra = _answer(missing, slot, sales_channel="dealer")
    assert not rerouted.get("sales_channel")
    outcome, line = ra.settle(None, rerouted, [])
    assert line is None and outcome is not None
    args = ra.route_args(outcome)
    assert args.get("channel") is None


def test_ranking_answer_ignores_a_brand_word_riding_on_it():
    slot = _open_slot("period")
    rerouted, _ra = _answer("period", slot, report_ask_words={"brand": ["cabana"]})
    assert not (rerouted.get("report_ask_words") or {}).get("brand")


def test_period_answer_fills_the_period():
    slot = _open_slot("period")
    rerouted, ra = _answer("period", slot, sales_channel="dealer")
    outcome, _ = ra.settle(None, rerouted, [])
    args = ra.route_args(outcome)
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31"


def test_guard_held_channel_survives_the_answer():
    slot = _open_slot("period", sales_channel="dealer")
    rerouted, ra = _answer("period", slot, sales_channel=None)
    outcome, _ = ra.settle(None, rerouted, [])
    assert ra.route_args(outcome).get("channel") == "dealer"


def test_guard_refine_with_no_open_ask_still_adds_the_channel():
    from tests.chatbot.test_report_engine_owner_retest import HELD, _args, _refine_out

    assert _args(_refine_out(sales_channel="dealer", sales_ranking_frame=dict(HELD))).get("channel") == "dealer"


# AC-HT8-16 top selling
def _top_selling_state(**slot):
    from app.services.chatbot.turn.state import Focus, Profile, State

    return State(focus=Focus(status="top_selling", top_selling={"rank_by": "quantity", **slot}),
                 pending=None, profile=Profile())


def test_top_selling_basis_answer_carries_no_channel():
    from app.services.chatbot.turn.apply import apply

    state = _top_selling_state(asked="basis")
    v = verdict(order_status="top_selling", domain_hint="order", basis="ordered", sales_channel="dealer",
                ranking_refine=True)
    state2, _plan = apply(state, v, build_policy())
    assert not state2.focus.sales_channel
    assert state2.focus.top_selling["basis"] == "ordered"


def test_top_selling_without_an_open_question_still_takes_a_named_channel():
    from app.services.chatbot.turn.apply import apply

    v = verdict(order_status="top_selling", domain_hint="order", sales_channel="dealer", domain_in_message=True)
    state2, _plan = apply(_top_selling_state(), v, build_policy())
    assert state2.focus.sales_channel == "dealer"


# AC-HT8-17
def test_chinese_basis_words_in_the_ranking_addendum():
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM as A

    for s in ('已送货 -> basis "delivered"', '已订购 -> basis "ordered"', '未交 -> basis "outstanding"',
              'an answer to an open basis question is never order_status "delivered"'):
        assert s in A, s


def test_chinese_basis_words_in_the_top_selling_addendum():
    from app.services.chatbot_parser_prompt import TOP_SELLING_ADDENDUM as T

    for s in ('已送货 -> basis "delivered"', '已订购 -> basis "ordered"',
              'an answer to an open basis question is never order_status "delivered"'):
        assert s in T, s
    assert '未交 -> basis "outstanding"' not in T


def _engine_lane_input(state, v):
    """What the engine does at engine.py:~4862: apply, then lane_parse_output(raw verdict, focus=state_out.focus)."""
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn_runtime import lane_parse_output

    state2, _plan = apply(state, v, build_policy())
    return state2, lane_parse_output(v, focus=state2.focus, pending=state.pending)


def test_top_selling_basis_answer_lane_input_carries_no_channel():
    v = verdict(order_status="top_selling", domain_hint="order", basis="ordered", sales_channel="dealer",
                ranking_refine=True)
    _state2, out = _engine_lane_input(_top_selling_state(asked="basis"), v)
    assert not out.get("sales_channel")


def test_top_selling_metric_question_dealer_only_stores_and_sends_the_channel():
    v = verdict(order_status="top_selling", domain_hint="order", sales_channel="dealer", ranking_refine=True)
    state2, out = _engine_lane_input(_top_selling_state(asked="metric"), v)
    assert state2.focus.sales_channel == "dealer"
    assert out.get("sales_channel") == "dealer"


# AC-HT8-18
def test_ranking_basis_question_is_catalogued_in_chinese_and_malay():
    from app.services.chatbot import label_catalog as lc
    from app.services.chatbot.lanes.business import ranking_ask

    row = lc.LABELS.get(ranking_ask.RANKING_BASIS_LINE, {})
    assert ZH.search(row.get("zh", "")), ranking_ask.RANKING_BASIS_LINE
    assert row.get("ms"), ranking_ask.RANKING_BASIS_LINE
