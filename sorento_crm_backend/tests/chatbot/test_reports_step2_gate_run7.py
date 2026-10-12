"""Phase 2 RED tests - REPORTS-STEP2 gate run 7 (12 Oct 2026), found by a full stack reproduction.

(A) with the drill offer open, the parser reads "only <code>" as a scoped ranking on group_by product;
    that is still the narrowing of the held report, unless the verdict is a pick of the offer;
(B) the parser reads a project/brand product ranking as `sales_ranking` with `rank_group`; that is the
    top selling ask;
(C) a reply that misses the asked field keeps the other fields it did carry.

Parser output fields only, never message text.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot import required_fields as rf
from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _parser_output, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_gate_run4 import EMPTY_REPORT, EXACT, _present, _route_calls  # noqa: F401
from tests.chatbot.test_reports_step2_gate_run5 import _exact_verdict, _sales, _sent
from tests.chatbot.test_reports_step2_lane import _s2_harness  # noqa: F401  (autouse fixture by name)
from tests.chatbot.test_top_selling_round5 import _position

# ================================================================================ A

#: The parser schema REQUIRES this object on every emission (head/parser.py), nothing answered:
#: the shared `_parser_output` stub omits it, so the run 7 builders set the live shape.
LIVE_NO_ANSWER = {"mode": None, "items": [], "picked": [], "qty_for_all": None}


def _live(qf: dict[str, Any]) -> dict[str, Any]:
    return {**qf, "open_question_answer": dict(LIVE_NO_ANSWER)}


FAMILY: dict[str, Any] = {
    **EMPTY_REPORT,
    "product_code": None, "product_codes": [],
    "total": {"qty": 10, "amount": 100.0},
    "periods": [{"from": "2026-09-01", "to": "2026-09-30", "qty": 10, "amount": 100.0}],
    "options": [
        {"key": "customer", "label": "By customer"},
        {"key": "product", "label": "By product"},
        {"key": "delivery_order", "label": "Delivery orders"},
    ],
}


def _answer(args: dict[str, Any]) -> str:
    return _present(EMPTY_REPORT if args.get("product_code_exact") else FAMILY)


def _hold_with_offer(console, monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    seen = _route_calls(console, monkeypatch, _answer)
    console.say(_sales(), "sales report for the customer")
    assert (console.session_vars.get("open_question") or {}).get("kind") == "sales_report_detail", console.session_vars
    return seen


def _scoped_exact(**kw: Any) -> dict[str, Any]:
    return _live(_exact_verdict(**{"ranking_drill": "scoped", "group_by": "product", **kw}))


def test_a12_with_the_drill_offer_open_a_scoped_product_reading_still_narrows(console, monkeypatch) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    text, _calls = console.say(_scoped_exact(), "only this exact model")
    sent = [a for a in _sent(seen) if a.get("product_code_exact")]
    assert sent, (seen, text)
    assert sent[0].get("product_code") == EXACT and not sent[0].get("group_by"), sent[0]
    assert EXACT in text and "No sales found" in text, text


def test_a13_guard_a_pick_of_by_product_is_still_the_drill(console, monkeypatch) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    console.say(_live(_position(2)), "2")
    drilled = [a for a in _sent(seen) if a.get("group_by") == "product"]
    assert drilled and not drilled[0].get("product_code_exact"), seen


def test_a14_unit_the_exact_verdict_narrows_but_a_pick_or_another_axis_does_not() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_scoped_exact()) is True
    assert _narrows_held_sales_report(_scoped_exact(group_by="customer")) is False


# ================================================================================ B

BRAND_PROJECT: dict[str, Any] = dict(
    domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=True,
    group_by="product", top_n=40, sales_channel="project", rank_group="item", rank_by="quantity", measure="qty",
    ranking_refine=False, date_filter_start=None, date_filter_end=None, basis=None,
)


def _brand_ranking(**kw: Any) -> dict[str, Any]:
    return _live(_parser_output(**{**BRAND_PROJECT, "entities": [_e("Sorento", "brand")], **kw}))


def _names(log):
    return [n for n, _a in log]


def test_b17_a_project_brand_product_ranking_is_the_top_selling_ask(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _brand_ranking(), "project sales top 40 Sorento products")
    assert "crm_report" not in _names(log), (text, log)
    assert PERIOD_Q not in text, text
    top = [a for n, a in log if n == "crm_top_selling_report"]
    if top:
        assert top[0]["n"] == 40 and top[0].get("channel") == "project", top[0]
        assert console.ids["brand"] in (top[0].get("brand_ids") or []), top[0]
    else:
        slot = ((console.session_vars.get("focus") or {}).get("top_selling")) or {}
        assert slot.get("top_n") == 40 and slot.get("asked"), (text, console.session_vars)


def test_b18_guard_without_rank_group_the_ranking_lane_keeps_it(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _brand_ranking(rank_group=None), "project sales top 40 Sorento products")
    assert "crm_top_selling_report" not in _names(log), log
    assert PERIOD_Q in text or "crm_report" in _names(log), (text, log)


def test_b19_guard_a_scoped_drill_after_a_held_ranking_stays_a_drill(console, monkeypatch) -> None:
    console.say(_rank(_e("Sorento", "brand"), group_by="sales_agent", top_n=3), "top 3 agents for Sorento")
    text, log = lane._say_all(
        console, monkeypatch,
        _rank(group_by="product", top_n=5, ranking_drill="scoped", rank_group="item", rank_by="quantity"),
        "top 5 products of these agents",
    )
    assert "crm_top_selling_report" not in _names(log), (text, log)


# ================================================================================ C


def test_c1_a_miss_on_the_asked_field_keeps_the_other_fields_the_reply_carried() -> None:
    from app.services.chatbot.lanes.business import report_ask as ra

    fresh = dict(order_status="sales_ranking", group_by="product", top_n=10, rank_direction="top", basis="delivered",
                 measure=None, entities=[], date_filter_start=None, date_filter_end=None, ranking_refine=False)
    first, _line = ra.settle(None, fresh, [])
    assert first.slot["asking"] == "period", first
    reply = dict(fresh, required_ask=first.slot, required_ask_reply="quantity", measure="qty", group_by=None, top_n=None)
    second, _line = ra.settle(None, reply, [])
    assert second.slot and second.slot["asking"] == "period", second
    assert (second.slot["values"].get("metric") or {}).get("value") == "qty", second.slot


# ================================================================================ review round


def test_a15_a_typed_label_of_the_open_offer_is_a_pick_not_a_narrowing(console, monkeypatch) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    console.say(_live(_exact_verdict(lane._hit("By product", "product"), group_by="product")), "By product")
    sent = _sent(seen)
    assert not any(a.get("product_code_exact") or a.get("product_code") == "By product" for a in sent), sent
    assert any(a.get("group_by") == "product" for a in sent), sent


COMPLETE = {"date_filter_start": "2026-01-01", "date_filter_end": "2026-12-31", "basis": "delivered"}


def test_b20_guard_a_location_keeps_the_ranking_on_the_ranking_lane(console, monkeypatch) -> None:
    qf = _brand_ranking(**COMPLETE)
    qf["entities"] = [_e("Sorento", "brand"), _e("BRW", "warehouse")]
    text, log = lane._say_all(console, monkeypatch, qf, "project sales top 40 Sorento products at BRW")
    assert "crm_top_selling_report" not in _names(log), log
    (ranking,) = [a for n, a in log if n == "crm_report"]
    assert ranking.get("warehouse_codes") == ["BRW"], ranking


def test_b21_guard_a_basis_top_selling_cannot_count_keeps_the_ranking_lane(console, monkeypatch) -> None:
    qf = _brand_ranking(**{**COMPLETE, "basis": "outstanding"})
    text, log = lane._say_all(console, monkeypatch, qf, "outstanding top 40 Sorento products")
    assert "crm_top_selling_report" not in _names(log), (text, log)
    (ranking,) = [a for n, a in log if n == "crm_report"]
    assert "outstanding" in str(ranking.get("measure")), ranking


def test_n1_a_two_word_category_survives_the_relabel(console, monkeypatch) -> None:
    qf = _brand_ranking()
    qf["entities"] = [_e("ZZT CATEGORY", "category")]
    text, log = lane._say_all(console, monkeypatch, qf, "project sales top 40 ZZT CATEGORY products")
    top = [a for n, a in log if n == "crm_top_selling_report"]
    slot = ((console.session_vars.get("focus") or {}).get("top_selling")) or {}
    kept = (top and console.ids["category"] in (top[0].get("category_ids") or [])) or slot.get("category_words") == ["ZZT CATEGORY"]
    assert kept, (text, log, slot)


@pytest.mark.parametrize("axis", [None, "product"])
def test_a18_a_declared_pick_with_no_position_is_not_a_pick_the_code_narrows(console, monkeypatch, axis) -> None:
    """`decide.picked_positions` is the engine's one pick test. The live parser declares a pick
    (`picked: [2]`) beside the code it typed, with no `reference_positions`: the engine does not
    treat that as a pick, so the typed code narrows the held report."""
    seen = _hold_with_offer(console, monkeypatch)
    qf = _live(_exact_verdict(group_by=axis, reference_positions=[]))
    qf["open_question_answer"] = {"mode": "pick", "picked": [2], "items": [], "qty_for_all": None}
    console.say(qf, "only this exact model")
    sent = [a for a in _sent(seen) if a.get("product_code_exact")]
    assert sent and sent[0].get("product_code") == EXACT and not sent[0].get("group_by"), [
        {k: v for k, v in a.items() if k not in ("_diagnostics", "view", "date_mode", "contact_id", "space_id")} for a in _sent(seen)
    ]


def test_a16_a_narrowed_empty_report_closes_the_drill_offer(console, monkeypatch) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    text, _calls = console.say(_live(_exact_verdict()), "only this exact model")
    assert any(a.get("product_code_exact") for a in _sent(seen)) and "No sales found" in text, (seen, text)
    assert (console.session_vars.get("open_question") or {}).get("kind") != "sales_report_detail", console.session_vars


# ================================================================================ D


def _analysis_ranking(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_analysis", domain_in_message=True,
        top_n=20, sales_channel="project", group_by=None, entities=[_e("Sorento", "brand")],
        date_filter_start=None, date_filter_end=None,
    )
    base.update(kw)
    return _live(_parser_output(**base))


def test_b22_a_subjectless_sales_analysis_with_a_count_is_the_top_selling_ask(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _analysis_ranking(), "top 20 project sales Sorento products")
    assert "crm_sales_analysis" not in _names(log), log
    assert "By quantity or by amount?" in text, text
    assert ((console.session_vars.get("focus") or {}).get("top_selling")), console.session_vars


def test_b23_guard_a_sales_analysis_with_no_count_stays_a_sales_analysis(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _analysis_ranking(top_n=None), "project sales of Sorento")
    assert "By quantity or by amount?" not in text, text
    assert not ((console.session_vars.get("focus") or {}).get("top_selling")), console.session_vars


def test_a15b_a_label_entity_is_a_pick_even_without_the_live_answer_object(console, monkeypatch) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    console.say(_exact_verdict(lane._hit("By product", "product"), group_by="product"), "By product")
    sent = _sent(seen)
    assert not any(a.get("product_code_exact") or a.get("product_code") == "By product" for a in sent), sent
    assert any(a.get("group_by") == "product" for a in sent), sent


# ================================================================================ restated ranking answers the open question


def _basis_open(console) -> None:
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), group_by="product", top_n=20, basis=None, measure="qty", **SEP), "top 20 products"
    )
    assert calls == [] and "Delivered" in text, (text, calls)


def _restated_answer(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=False,
        ranking_refine=True, group_by=None, top_n=40, basis="ordered", measure=None, entities=[],
        date_filter_start=None, date_filter_end=None,
    )
    base.update(kw)
    return _live(_parser_output(**base))


def test_b24_a_restated_count_that_answers_the_open_question_wins_over_the_held_one(console) -> None:
    _basis_open(console)
    _text, calls = console.say(_restated_answer(), "ordered, top 40")
    (args,) = calls
    assert args["top_n"] == 40, args


def test_b25_guard_an_answer_with_no_count_keeps_the_held_one(console) -> None:
    _basis_open(console)
    _text, calls = console.say(_restated_answer(top_n=None), "ordered")
    (args,) = calls
    assert args["top_n"] == 20, args


def test_b26_guard_a_month_ranking_of_sales_stays_a_sales_analysis(console, monkeypatch) -> None:
    qf = _analysis_ranking(top_n=3, group_by="month", sales_channel="dealer", entities=[])
    text, log = lane._say_all(console, monkeypatch, qf, "top 3 months of dealer sales this year")
    assert "By quantity or by amount?" not in text, text
    assert not ((console.session_vars.get("focus") or {}).get("top_selling")), console.session_vars


def test_b27_guard_an_invoiced_company_analysis_stays_a_sales_analysis(console, monkeypatch) -> None:
    qf = _analysis_ranking(top_n=5, sales_basis="invoiced", sales_company="Mocha", sales_channel=None, entities=[])
    text, log = lane._say_all(console, monkeypatch, qf, "top 5 Mocha invoiced sales")
    assert "By quantity or by amount?" not in text, text
    assert not ((console.session_vars.get("focus") or {}).get("top_selling")), console.session_vars


# ================================================================================ live shapes, round 3


def test_b28_a_brand_tagged_as_the_company_is_still_the_top_selling_ask(console, monkeypatch) -> None:
    qf = _analysis_ranking(sales_company="Sorento")
    text, log = lane._say_all(console, monkeypatch, qf, "top 20 project sales Sorento products")
    assert "crm_sales_analysis" not in _names(log), log
    assert "By quantity or by amount?" in text, text


def test_b29_a_company_equal_to_a_brand_entity_is_not_forwarded_to_the_analysis(console, monkeypatch) -> None:
    qf = _analysis_ranking(top_n=None, group_by="month", sales_company="Sorento", sales_channel=None)
    _text, log = lane._say_all(console, monkeypatch, qf, "Sorento sales this year")
    sent = [a for n, a in log if n == "crm_sales_analysis"]
    assert sent and not sent[0].get("company"), sent


def test_b30_a_fresh_ask_with_a_declared_answer_is_not_swallowed_as_the_basis_reply(console) -> None:
    _basis_open(console)
    qf = _live(_parser_output(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=True,
        ranking_refine=False, group_by="product", top_n=40, sales_channel="project", rank_group="item",
        rank_by="quantity", measure="qty", basis=None, entities=[_e("Sorento", "brand")],
        date_filter_start=None, date_filter_end=None,
    ))
    qf["open_question_answer"] = {"mode": "pick", "picked": [2], "items": [], "qty_for_all": None}
    text, _calls = console.say(qf, "project sales top 40 Sorento products")
    assert "as a basis" not in text, text


def test_b31_guard_a_plain_reply_with_a_declared_pick_still_answers_the_basis_question(console) -> None:
    _basis_open(console)
    qf = _restated_answer(top_n=None, basis="delivered")
    qf["open_question_answer"] = {"mode": "pick", "picked": [1], "items": [], "qty_for_all": None}
    _text, calls = console.say(qf, "1")
    (args,) = calls
    assert args["top_n"] == 20 and "delivered" in str(args.get("measure")), args


# ================================================================================ review round 4


def _declared_pick(picked: int, **kw: Any) -> dict[str, Any]:
    """A worded pick the parser is taught to declare in `open_question_answer`: no positions, no entities."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_report", entities=[],
        domain_in_message=False, reference_positions=[], group_by=None, **SEP,
    )
    base.update(kw)
    qf = _live(_parser_output(**base))
    qf["open_question_answer"] = {"mode": "pick", "picked": [picked], "items": [], "qty_for_all": None}
    return qf


@pytest.mark.parametrize("said, picked, axis, want", [
    ("the second one", 2, None, "product"),
    ("kedua", 2, None, "product"),
    ("by customer", 1, "customer", "customer"),
])
def test_a19_a_worded_declared_pick_still_drills(console, monkeypatch, said, picked, axis, want) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    console.say(_declared_pick(picked, group_by=axis), said)
    assert any(a.get("group_by") == want for a in _sent(seen)), [
        {k: v for k, v in a.items() if k in ("group_by", "product_code", "product_code_exact")} for a in _sent(seen)
    ]


def test_b32_guard_a_period_answer_with_a_stray_count_keeps_the_held_count(console) -> None:
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), group_by="customer", top_n=10, date_filter_start=None, date_filter_end=None),
        "top 10 customers for Sorento",
    )
    assert calls == [] and PERIOD_Q in text, (text, calls)
    qf = _live(_parser_output(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=False,
        ranking_refine=None, group_by=None, top_n=3, basis=None, measure=None, entities=[], **SEP,
    ))
    _text, calls = console.say(qf, "last 3 months")
    (args,) = calls
    assert args["top_n"] == 10, args
