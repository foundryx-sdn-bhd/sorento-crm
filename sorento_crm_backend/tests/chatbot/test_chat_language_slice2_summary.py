"""CHAT-LANGUAGE slice 2: the order summary block labels and the truncation notice."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.lanes.business import fetch

TS = "2026-10-02T09:15:30"


def _presenters():
    mcp_root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp import presenters
    except ImportError:  # pragma: no cover
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return presenters


SUMMARY = {
    "row_count": 3,
    "groups_truncated": True,
    "products": [
        {
            "product_code": "SRTSWT3001",
            "customer_count": 2,
            "so_count": 4,
            "so_date_from": "2026-09-01",
            "so_date_to": "2026-09-20",
            "so_ordered_qty": 100,
            "so_transferred_qty": 60,
            "so_outstanding_qty": 40,
            "order_count": 3,
            "order_date_from": "2026-09-05",
            "order_date_to": "2026-09-25",
            "delivered_quantity": 60,
            "delivered_from": "2026-09-06",
            "delivered_to": "2026-09-26",
            "pending_quantity": 12,
        }
    ],
    "groups": [
        {
            "product_code": "SRTSWT3001",
            "customer": "ACME TRADING",
            "so_count": 2,
            "so_ordered_qty": 50,
            "so_outstanding_qty": 20,
            "order_count": 1,
            "delivered_quantity": 30,
        }
    ],
}


def _envelope() -> dict:
    p = _presenters()
    items = p.summary_items(SUMMARY)
    intro = p.summary_intro(SUMMARY, 1)
    assert items and intro
    return {
        "result_type": "orders",
        "intro": intro,
        "items": [
            {
                "title": "SO-1",
                "fields": [{"key": "order_number", "label": "Order Number", "value": "DO-0001"}],
                "flags": {},
            }
        ],
        "summary_items": items,
        "has_result": True,
        "last_updated_at": TS,
    }


def _render(lang: str | None) -> str:
    ctx = {"semantic_input": {}, "tool": "crm_order_management_orders_list"}
    if lang:
        ctx["localizer"] = Localizer(lang, label_catalog.defaults(lang))
    return fetch.output_structurer(_envelope(), ctx)["response"]


def _field_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        if line.startswith("*") and ":* " in line:
            label, _, value = line[1:].partition(":* ")
            out.append((label, value))
    return out


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_every_summary_label_is_translated_and_every_value_is_identical(lang):
    en = _field_lines(_render(None))
    tr = _field_lines(_render(lang))
    assert [v for _l, v in tr] == [v for _l, v in en]
    english_labels = {l for l, _v in en}
    assert {"SO Date", "Ordered", "Transferred to DO", "SO Outstanding", "DO Date", "Delivered",
            "Delivery Date", "DO Outstanding", "Customers", "Product Code"} <= english_labels
    for (el, _ev), (tl, _tv) in zip(en, tr):
        if el in ("SO", "DO"):
            assert tl == el  # SO and DO stay as printed
        else:
            assert tl == label_catalog.LABELS[el][lang], (el, tl)


def test_the_truncation_notice_is_catalogued_and_translated():
    p = _presenters()
    notice = p.summary_intro(SUMMARY, 1)
    assert notice in label_catalog.LABELS
    ms = _render("ms")
    assert label_catalog.LABELS[notice]["ms"] in ms
    assert notice not in ms
    assert label_catalog.LABELS[notice]["zh"] in _render("zh")
