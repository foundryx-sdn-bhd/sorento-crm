"""Phase 2 RED tests - REPORTS-STEP2 gate run 6 (12 Oct 2026).

(A1) a narrowed sales report with no sales keeps the presenter's header and "No sales found.";
(A2) the parser reading "only <model>" as a sales_analysis refinement still narrows the held report;
(B)  a sales_ranking verdict that answers an open top selling question is an answer, not a new ask.

Parser output fields only, never message text.
"""
from __future__ import annotations

from typing import Any

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import _parser_output, console  # noqa: F401
from tests.chatbot.test_reports_step2_gate_run4 import EMPTY_REPORT, EXACT, _hold, _present, _route_calls  # noqa: F401
from tests.chatbot.test_reports_step2_gate_run5 import _exact_verdict, _sent
from tests.chatbot.test_reports_step2_lane import _s2_harness  # noqa: F401  (autouse fixture by name)
from tests.chatbot.test_top_selling_round5 import _answer, _ask, _calls, _e as _ts_e
from tests.chatbot.test_top_selling_round7 import console_factory  # noqa: F401  (the fixture)

# ================================================================================ A1


def test_a6_a_narrowed_report_with_no_sales_keeps_its_header_and_has_no_offer(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    text, _calls_ = console.say(_exact_verdict(), "only this exact model")
    assert EXACT in text, text
    assert "01/09/2026 to 30/09/2026" in text, text
    assert "No sales found" in text, text
    assert "escalate" not in text.lower(), text


def test_a7_guard_a_fresh_named_product_report_with_no_sales_keeps_the_miss_lane(console, monkeypatch) -> None:
    _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    text, _calls_ = console.say(_exact_verdict(), "sales of this model")
    assert "No sales found" in text, text
    assert "escalate" in text.lower(), text


# ================================================================================ A2


def test_a8_a_sales_analysis_refinement_narrows_the_held_report(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    text, _calls_ = console.say(_exact_verdict(order_status="sales_analysis", entity_op="replace"), "only this model")
    (sent, *_rest) = _sent(seen) or [{}]
    assert sent.get("product_code") == EXACT and sent.get("product_code_exact") is True, (sent, text)
    assert sent.get("date_from") == "2026-09-01" and sent.get("date_to") == "2026-09-30", sent
    assert sent.get("customer_ids") == [console.ids["customer"]], sent


def test_a9_guard_a_sales_analysis_naming_the_domain_is_untouched(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    console.say(_exact_verdict(order_status="sales_analysis", domain_in_message=True), "sales analysis of this model")
    for sent in _sent(seen):
        assert not sent.get("product_code_exact"), sent


def test_a10_guard_a_sales_analysis_naming_another_basis_is_not_narrowed(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    console.say(_exact_verdict(order_status="sales_analysis", sales_basis="invoiced"), "invoiced sales of this model")
    for sent in _sent(seen):
        assert not sent.get("product_code_exact"), sent


# ================================================================================ B

YEAR = {"date_filter_start": "2026-01-01", "date_filter_end": "2026-12-31"}
CAT = "water closet"


def _t1(c) -> str:
    text, captured = c.say(
        _ask(top_n=20, group_by="product", sales_channel="project", rank_group="item", basis=None,
             entities=[_ts_e(CAT, "category")]),
        "top 20 water closet products for project sales",
    )
    assert "By quantity or by amount?" in text and not _calls(captured), (text, captured)
    return text


def _t2(c) -> str:
    text, captured = c.say(_answer(ranking_refine=True, measure="qty", domain_in_message=False), "quantity")
    assert not _calls(captured), captured
    return text


def _t3_flipped(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=False,
        group_by=None, top_n=20, ranking_refine=True, ranking_drill="global", measure="qty",
        sales_channel="project", entities=[], entity_op="reuse", rank_by=None, basis=None, rank_group=None,
        rank_direction=None, **YEAR,
    )
    base.update(kw)
    return _parser_output(**base)


def test_b9_a_flipped_sales_ranking_verdict_answers_the_open_top_selling_ask(console_factory) -> None:
    c = console_factory()
    _t1(c)
    _t2(c)
    text, captured = c.say(_t3_flipped(), "2026")
    assert "Which period" not in text, text
    assert c.slot, "the top selling ask was dropped"
    if not _calls(captured):
        text, captured = c.say(_answer(basis="delivered"), "delivered")
    (args,) = _calls(captured)
    assert args["n"] == 20 and args["rank_by"] == "quantity", args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args
    assert args.get("channel") == "project", args
    assert "Which period" not in text, text


def test_b10_a_sales_ranking_naming_its_domain_is_a_new_ask(console_factory) -> None:
    c = console_factory()
    _t1(c)
    _t2(c)
    c.say(_t3_flipped(), "2026")
    text, captured = c.say(
        _parser_output(
            domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=True,
            group_by="product", top_n=40, sales_channel="project", rank_group="item", basis="ordered",
            measure="qty", ranking_refine=False, entities=[_ts_e(CAT, "category")], **YEAR,
        ),
        "project sales top 40 water closet products",
    )
    sent = [a for n, a in captured if n in ("crm_report", "crm_top_selling_report")]
    assert sent, (text, captured)
    assert sent[-1]["top_n"] == 40 and sent[-1]["date_from"] == "2026-01-01", sent[-1]


def test_b11_guard_a_sales_ranking_with_its_own_axis_still_leaves_top_selling(console_factory) -> None:
    c = console_factory()
    _t1(c)
    c.say(
        _parser_output(
            domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=False,
            group_by="sales_agent", top_n=5, ranking_refine=False, entities=[], **YEAR,
        ),
        "top 5 sales agents",
    )
    assert not c.slot, c.slot


def test_b12_a_flipped_verdict_echoing_the_held_axis_still_answers_the_open_ask(console_factory) -> None:
    c = console_factory()
    _t1(c)
    _t2(c)
    text, captured = c.say(_t3_flipped(group_by="product", message_type="business_query", topic_reset=False), "2026")
    assert "Which period" not in text, text
    if not _calls(captured):
        text, captured = c.say(_answer(basis="delivered"), "delivered")
    (args,) = _calls(captured)
    assert args["n"] == 20 and args["rank_by"] == "quantity", args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args
    assert args.get("channel") == "project", args
    assert "Which period" not in text, text


def test_b13_guard_a_flipped_verdict_naming_another_axis_leaves_top_selling(console_factory) -> None:
    c = console_factory()
    _t1(c)
    _t2(c)
    c.say(_t3_flipped(group_by="customer"), "2026")
    assert not c.slot, c.slot


def _flipped_answer(**kw: Any) -> dict[str, Any]:
    return _t3_flipped(top_n=None, date_filter_start=None, date_filter_end=None, sales_channel=None, **kw)


def test_b14_a_flipped_metric_answer_sets_rank_by_and_moves_to_the_basis_question(console_factory) -> None:
    c = console_factory()
    _t1(c)
    assert c.slot.get("asked") == "metric", c.slot
    text, captured = c.say(_flipped_answer(), "quantity")
    assert c.slot.get("rank_by") == "quantity", c.slot
    assert "Delivered" in text and "ordered" in text, text
    assert "By quantity or by amount?" not in text, text


def test_b15_an_echoed_axis_under_the_group_question_is_the_answer(console_factory) -> None:
    c = console_factory()
    text, captured = c.say(
        _ask(top_n=20, rank_group="unclear", rank_by="quantity", basis="delivered", entities=[]),
        "top 20 selling by category",
    )
    assert c.slot.get("asked") == "group", (c.slot, text)
    c.say(_flipped_answer(group_by="category"), "categories")
    assert c.slot.get("rank_group") == "category", c.slot


def test_b16_guard_an_explicit_not_a_refine_echo_is_a_new_ask(console_factory) -> None:
    c = console_factory()
    _t1(c)
    _t2(c)
    c.say(_t3_flipped(group_by="product", ranking_refine=False), "2026")
    assert not c.slot, c.slot
