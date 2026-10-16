"""AC-14 .. AC-17 (pure half): which areas the parser's own output says it needed.

Red now: `app.services.chatbot.head.prompt_diet` does not exist.
"""
from __future__ import annotations

import pytest


def _m():
    from app.services.chatbot.head import prompt_diet

    return prompt_diet


def test_needed_areas_reads_domain_hint_and_every_ask_domain() -> None:
    m = _m()
    parsed = {
        "domain_hint": "inventory",
        "asks": [{"domain": "promotion", "intent": "x"}, {"domain": "order", "intent": "y"}],
        "order_status": None,
    }
    assert m.needed_areas(parsed) == {"inventory", "promotion", "order"}


def test_needed_areas_tolerates_null_keys() -> None:
    m = _m()
    assert m.needed_areas({"domain_hint": None, "asks": None, "order_status": None}) == set()
    assert m.needed_areas({}) == set()


@pytest.mark.parametrize("status", ["sales_report", "sales_analysis", "top_selling", "sales_ranking"])
def test_ac15_sales_order_status_needs_the_sales_card(status: str) -> None:
    m = _m()
    assert "sales" in m.needed_areas({"domain_hint": None, "asks": [], "order_status": status})


def test_ac15_other_order_status_does_not_need_sales() -> None:
    m = _m()
    assert "sales" not in m.needed_areas({"domain_hint": None, "asks": [], "order_status": "outstanding"})


def test_ac14_missing_is_needed_and_a_card_and_not_loaded() -> None:
    m = _m()
    parsed = {"domain_hint": "promotion", "asks": [{"domain": "order"}], "order_status": None}
    assert m.missing(parsed, {"orders"}) == {"product"}
    assert m.missing(parsed, {"orders", "product"}) == set()


def test_ac17_an_area_without_a_card_is_never_missing() -> None:
    m = _m()
    parsed = {"domain_hint": "a_domain_with_no_card", "asks": [], "order_status": None}
    assert m.missing(parsed, set()) == set()


def test_ac15_a_sales_status_needs_the_orders_bundle() -> None:
    m = _m()
    parsed = {"domain_hint": "inventory", "asks": [], "order_status": "sales_report"}
    assert m.missing(parsed, {"stock"}) == {"orders"}


def test_ac17_every_needed_card_loaded_means_nothing_missing() -> None:
    m = _m()
    parsed = {"domain_hint": "inventory", "asks": [], "order_status": "sales_report"}
    assert m.missing(parsed, {"stock", "orders"}) == set()
