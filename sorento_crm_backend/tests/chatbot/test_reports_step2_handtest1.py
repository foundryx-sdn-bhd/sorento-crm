"""REPORTS-STEP2 hand test 1 (head 5a3f6f27), step 3 and step 14.

(h1/h2) "bottom 5" after a printed sales ranking is a refine of that ranking, not a top selling ask;
(h3/h4) a fresh top selling restatement with the same filters keeps the held metric, basis and period.

Parser output fields only, never message text; the live `open_question_answer` shape on every verdict.
"""
from __future__ import annotations

from typing import Any

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _parser_output, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_gate_run7 import LIVE_NO_ANSWER, _live
from tests.chatbot.test_reports_step2_lane import _s2_harness, _t1  # noqa: F401  (autouse fixture by name)
from tests.chatbot.test_top_selling_round5 import _ask, _answer


def _names(log):
    return [n for n, _a in log]


# ================================================================================ step 3


def test_h1_bottom_5_after_a_held_ranking_refines_it(console) -> None:
    _text, first = _t1(console)
    (before,) = first
    qf = _live(_answer(rank_direction="bottom", top_n=5, domain_in_message=False))
    text, calls = console.say(qf, "bottom 5")
    (after,) = calls
    assert after["sort"] == "asc" and after["top_n"] == 5, after
    assert after["brand_ids"] == before["brand_ids"] and after["date_from"] == before["date_from"], after
    assert PERIOD_Q not in text, text


def test_h2_guard_a_top_selling_ask_naming_its_domain_stays_top_selling(console, monkeypatch) -> None:
    _t1(console)
    text, log = lane._say_all(console, monkeypatch, _live(_ask(top_n=10)), "top 10 selling items")
    assert "crm_report" not in _names(log), log
    assert "By quantity or by amount?" in text or "crm_top_selling_report" in _names(log), (text, log)


# ================================================================================ step 14

from tests.chatbot.test_reports_step2_gate_run7 import _brand_ranking  # noqa: E402


def _ts_calls(log):
    return [a for n, a in log if n == "crm_top_selling_report"]


def _ran_top_20(console, monkeypatch):
    """T1 the project brand ranking (relabelled top selling, metric asked), T2 the metric, T3 the basis."""
    text, log = lane._say_all(console, monkeypatch, _brand_ranking(top_n=20, rank_by=None, measure=None),
                              "top 20 project sales Sorento products")
    assert "By quantity or by amount?" in text and not _ts_calls(log), (text, log)
    text, log = lane._say_all(console, monkeypatch, _live(_answer(rank_by="quantity")), "quantity")
    if not _ts_calls(log):
        text, log = lane._say_all(console, monkeypatch, _live(_answer(basis="delivered")), "delivered")
    (args,) = _ts_calls(log)
    assert args["n"] == 20 and args["rank_by"] == "quantity", args
    return args


def test_h3_a_restated_top_selling_ask_with_the_same_filters_keeps_the_held_metric(console, monkeypatch) -> None:
    held = _ran_top_20(console, monkeypatch)
    text, log = lane._say_all(console, monkeypatch, _brand_ranking(top_n=40, rank_by=None, measure=None),
                              "project sales top 40 Sorento products")
    assert "By quantity or by amount?" not in text, text
    (args,) = _ts_calls(log)
    assert args["n"] == 40 and args["rank_by"] == "quantity" and args.get("channel") == "project", args
    assert args.get("brand_ids") == held.get("brand_ids"), (args, held)
    assert args.get("basis") == held.get("basis"), (args, held)


def test_h4_guard_a_different_brand_is_a_fresh_ask_and_the_metric_is_asked(console, monkeypatch) -> None:
    _ran_top_20(console, monkeypatch)
    qf = _brand_ranking(top_n=40, rank_by=None, measure=None)
    qf["entities"] = [_e("ZZT CATEGORY", "category")]
    text, log = lane._say_all(console, monkeypatch, qf, "project sales top 40 ZZT CATEGORY products")
    assert "By quantity or by amount?" in text and not _ts_calls(log), (text, log)
