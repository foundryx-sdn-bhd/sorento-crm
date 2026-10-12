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
    return _exact_verdict(**{"ranking_drill": "scoped", "group_by": "product", **kw})


def test_a12_with_the_drill_offer_open_a_scoped_product_reading_still_narrows(console, monkeypatch) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    text, _calls = console.say(_scoped_exact(), "only this exact model")
    sent = [a for a in _sent(seen) if a.get("product_code_exact")]
    assert sent, (seen, text)
    assert sent[0].get("product_code") == EXACT and not sent[0].get("group_by"), sent[0]
    assert EXACT in text and "No sales found" in text, text


def test_a13_guard_a_pick_of_by_product_is_still_the_drill(console, monkeypatch) -> None:
    seen = _hold_with_offer(console, monkeypatch)
    console.say(_position(2), "2")
    drilled = [a for a in _sent(seen) if a.get("group_by") == "product"]
    assert drilled and not drilled[0].get("product_code_exact"), seen


def test_a14_unit_the_exact_verdict_narrows_but_a_pick_or_another_axis_does_not() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_scoped_exact()) is True
    assert _narrows_held_sales_report(_scoped_exact(group_by="customer")) is False
    assert _narrows_held_sales_report(_scoped_exact(reference_positions=[2])) is False


# ================================================================================ B

BRAND_PROJECT: dict[str, Any] = dict(
    domain_hint="order", intent_hint="check_order", order_status="sales_ranking", domain_in_message=True,
    group_by="product", top_n=40, sales_channel="project", rank_group="item", rank_by="quantity", measure="qty",
    ranking_refine=False, date_filter_start=None, date_filter_end=None, basis=None,
)


def _brand_ranking(**kw: Any) -> dict[str, Any]:
    return _parser_output(**{**BRAND_PROJECT, "entities": [_e("Sorento", "brand")], **kw})


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
