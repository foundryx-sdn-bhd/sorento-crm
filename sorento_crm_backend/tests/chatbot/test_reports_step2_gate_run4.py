"""Phase 2 RED tests - REPORTS-STEP2 gate run 4 (11 Oct 2026).

(a) narrowing a held plain sales report to one exact product code applies to that report;
(b) a ranking waiting on the metric question keeps its pending ask when the next parse has no metric.

Parser output fields only, never message text.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _parser_output, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_lane import _s2_harness, _wire  # noqa: F401  (autouse fixture by name)

EXACT = "SRTWT7408"


def _sales(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_report",
        entities=[lane._hit(lane.CUSTOMER_NAME, "customer")], **SEP,
    )
    base.update(kw)
    return _parser_output(**base)


def test_a_a_narrowing_a_held_sales_report_applies_to_that_report(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _sales(), "sales report for the customer")
    assert [n for n, _ in log] == ["crm_sales_report"], log
    assert "all products" in text.lower(), text  # the held report is the all-products one
    narrow = _parser_output(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", ranking_drill="scoped",
        entities=[lane._hit(EXACT, "product")], group_by=None, **SEP,
    )
    text, log = lane._say_all(console, monkeypatch, narrow, "only this one")
    assert "I don't have a ranking" not in text, text
    sent = [a for n, a in log if n in ("crm_sales_report", "crm_report")]
    assert sent, (log, text)
    assert sent[0].get("product_code") == EXACT, sent[0]
    assert sent[0].get("customer_ids") == [console.ids["customer"]], sent[0]  # the held report's own filter stays
    assert "No sales found" in text, text
    assert "all products" not in text.lower(), text  # the held total is never shown under the exact code


# ================================================================================ (b)

METRIC_Q = "By quantity or by amount?"


def _metricless(**kw: Any) -> dict[str, Any]:
    return _rank(_e("Sorento", "brand"), top_n=10, measure=None, basis="delivered", **{**SEP, **kw})


def _no_metric_reading(**kw: Any) -> dict[str, Any]:
    """A one-word answer the parser misread as a ranking ask with a stray, unconfident word and no metric."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
        entities=[{"raw": "qty", "hint": "other", "canonical_code": None, "current_message": True, "confident": False}],
        group_by=None, top_n=None, measure=None, basis=None, ranking_refine=False,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(kw)
    return _parser_output(**base)


def test_b_a_a_reply_with_no_metric_keeps_the_pending_ask_and_re_asks_the_metric(console) -> None:
    text, calls = console.say(_metricless(), "top 10 sorento")
    assert text.strip() == METRIC_Q and calls == [], (text, calls)
    text, calls = console.say(_no_metric_reading(), "qty")
    assert calls == [], calls
    assert lane.CATALOGUE_LINE not in text and "I captured" not in text, text
    assert METRIC_Q in text, text
    pending = _wire(console).get("required_ask") or {}
    assert pending.get("asking") == "metric", pending


def test_b_b_the_kept_ask_runs_with_its_held_values_on_the_next_answer(console) -> None:
    console.say(_metricless(), "top 10 sorento")
    console.say(_no_metric_reading(), "qty")
    _text, calls = console.say(_no_metric_reading(measure="qty", entities=[]), "quantity")
    (args,) = calls
    assert args["measure"] == "delivered_qty" and args["top_n"] == 10, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-09-01", args


def test_b_c_the_re_ask_is_bounded_by_the_existing_give_up(console) -> None:
    console.say(_metricless(), "top 10 sorento")
    console.say(_no_metric_reading(), "qty")
    text, calls = console.say(_no_metric_reading(), "qty")
    assert calls == [], calls
    assert METRIC_Q not in text and lane.CATALOGUE_LINE not in text and "I captured" not in text, text
    assert "I still can't read" in text, text


def test_b_d_the_open_metric_question_reaches_the_parser_with_its_options(console) -> None:
    """`turn/question.of_required_ask` builds the `Open question:` object the parser answers."""
    from app.services.chatbot.turn import question

    console.say(_metricless(), "top 10 sorento")
    slot = _wire(console).get("required_ask")
    assert slot and slot.get("asking") == "metric", slot
    obj = question.of_required_ask(slot)
    assert obj is not None, "the sales ranking's open question is never stated to the parser"
    assert obj["owed"] == ["metric"], obj
    assert {o["code"] for o in obj.get("options") or []} == {"qty", "amount"}, obj
