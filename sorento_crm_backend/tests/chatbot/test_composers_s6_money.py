"""CHAT-COMPOSER S6 red tests (money): `MYR 1,234.00` everywhere a price is printed (R13, Q3 amends nothing here).

Goldens are literals captured from main code on 17 Oct. Print-time only: the presenter keeps `MYR 1234.00`.
"""
from __future__ import annotations

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.lanes.business import fetch
from tests.chatbot.test_composers_stock_xd import _set_gate


def test_money():
    from app.services.chatbot.composers import format as fmt

    assert fmt.money(1234) == "MYR 1,234.00"
    assert fmt.money(275) == "MYR 275.00"
    assert fmt.money("1234567.5") == "MYR 1,234,567.50"
    assert fmt.money(-1234.5) == "MYR -1,234.50"
    assert fmt.money(12345.6, "CNY") == "CNY 12,345.60"


def test_money_field():
    from app.services.chatbot.composers import format as fmt

    assert fmt.money_field({"label": "List Price", "value": "MYR 1234.00"}) == "MYR 1,234.00"
    assert fmt.money_field({"label": "Selling Price", "value": "MYR 1050.00"}) == "MYR 1,050.00"
    assert fmt.money_field({"key": "unit_cost", "label": "Unit Cost", "value": "CNY 12345.60"}) == "CNY 12,345.60"
    assert fmt.money_field({"key": "discount_per_unit", "label": "Discount", "value": "CNY 1234.50"}) == "CNY 1,234.50"
    assert fmt.money_field({"label": "List Price", "value": "MYR 275.00"}) == "MYR 275.00"
    for unchanged in ("Not defined", "MYR abc", "1234.00"):
        assert fmt.money_field({"label": "List Price", "value": unchanged}) == unchanged
    assert fmt.money_field({"label": "Description", "value": "MYR 1234.00"}) == "MYR 1234.00"


def test_present_and_noun_and_missing_line():
    from app.services.chatbot.composers import format as fmt

    for empty in (None, "", "  ", "Not defined"):
        assert fmt.present(empty) is False
    assert fmt.present("MYR 1.00") is True
    assert fmt.noun("List Price") == "list price"
    assert fmt.noun("Weight") == "weight"
    assert fmt.noun("WELS rating") == "WELS rating"
    assert fmt.noun("Seat cover material") == "seat cover material"
    assert fmt.missing_line("weight", "SRTWC286-SH") == "No weight on record for SRTWC286-SH."


def _out(items, tool):
    env = {"items": items, "answers": [{"text": "x"}], "has_result": True}
    return fetch.output_structurer(env, {"tool": tool, "semantic_input": {}})["response"]


def _f(label, value, key=None):
    d = {"label": label, "value": value}
    if key:
        d["key"] = key
    return d


def test_item_line_money(monkeypatch):
    item = {"fields": [_f("Product Code", "SRT1"), _f("List Price", "MYR 1234.00")]}
    _set_gate(monkeypatch, False)
    assert _out([item], "crm_master_products_list") == "Here are the results.\n\n1. *Product Code:* SRT1\n*List Price:* MYR 1234.00"
    _set_gate(monkeypatch, True)
    assert _out([item], "crm_master_products_list") == "Here are the results.\n\n1. *Product Code:* SRT1\n*List Price:* MYR 1,234.00"


def test_promotion_products_money(monkeypatch):
    item = {"fields": [_f("Product Code", "SRT1"), _f("Selling Price", "MYR 1050.00"), _f("List Price", "MYR 1234.00")]}
    _set_gate(monkeypatch, False)
    assert "*Selling Price:* MYR 1050.00\n*List Price:* MYR 1234.00" in _out([item], "crm_promotions_list")
    _set_gate(monkeypatch, True)
    assert "*Selling Price:* MYR 1,050.00\n*List Price:* MYR 1,234.00" in _out([item], "crm_promotions_list")


def test_po_last_cost_money(monkeypatch):
    item = {
        "fields": [
            _f("Product Code", "SRT1"),
            _f("Unit Cost", "CNY 12345.60", "unit_cost"),
            _f("Discount", "CNY 1234.50", "discount_per_unit"),
            _f("Cost After Discount", "CNY 11111.10", "unit_cost_after_discount"),
        ]
    }
    tool = "crm_procurement_po_last_cost_list"
    _set_gate(monkeypatch, False)
    off = _out([item], tool)
    assert "CNY 12345.60" in off and "CNY 1234.50" in off and "CNY 11111.10" in off
    _set_gate(monkeypatch, True)
    on = _out([item], tool)
    assert "*Unit Cost:* CNY 12,345.60" in on and "*Discount:* CNY 1,234.50" in on and "CNY 11,111.10" in on


@pytest.mark.parametrize("code", ["", "MYR", "RM"])
def test_sub_asks_currency(monkeypatch, code):
    from app.services.chatbot.turn import sub_asks

    _set_gate(monkeypatch, False)
    assert sub_asks._currency(code) == "RM"
    _set_gate(monkeypatch, True)
    assert sub_asks._currency(code) == "MYR"


def test_sub_asks_currency_other_unchanged(monkeypatch):
    from app.services.chatbot.turn import sub_asks

    for gate in (False, True):
        _set_gate(monkeypatch, gate)
        assert sub_asks._currency("cny") == "CNY"
