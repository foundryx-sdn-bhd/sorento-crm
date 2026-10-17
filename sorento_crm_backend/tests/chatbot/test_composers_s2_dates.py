"""CHAT-COMPOSER S2 red tests: dates as dd/mm/yyyy on every tool (R13, lead decision OI-1 (a)),
one "Container" and one "ETA" label (OI-4), dealer ETA line unchanged. Pure, no database.

Goldens are literals captured from main code on 17 Oct (S1 landed, gate off).
"""
from __future__ import annotations

import re

import pytest

from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.lanes.business.fetch import output_structurer
from tests.chatbot.test_composers_stock_xd import _set_gate

ISO_ON_LABEL_LINE = re.compile(r"^.*\*[^*\n]+:\*\s+\d{4}-\d{2}-\d{2}\s*$", re.MULTILINE)


def _f(key, label, value):
    return {"key": key, "label": label, "value": value}


def _out(items, tool, loc=None):
    ctx = {"tool": tool, "semantic_input": {}}
    if loc:
        ctx["localizer"] = Localizer(loc, defaults(loc))
    env = {"items": items, "answers": [{"text": "x"}], "has_result": True}
    return output_structurer(env, ctx)["response"]


def _incoming_row(eta="2026-09-27", delay="2026-09-25", container="SEGU4008631"):
    return {
        "fields": [
            _f("company_name", "Company", "Sorento"),
            _f("product_code", "Product Code", "SRTWC286"),
            _f("shipping_container_number", "Container", container),
            _f("estimated_arrival_date", "ETA", eta),
            _f("eta_delay_date", "ETA Delay", delay),
            _f("remaining_incoming_quantity", "Incoming Quantity", 40),
        ],
        "flags": {"unallocated": True},
    }


def _by_product_row():
    return {
        "fields": [
            _f("company_name", "Company", "Sorento"),
            _f("product_code", "Product Code", "SRTWC286"),
            _f("shipping_container_number", "Shipment Container", "DFSU7408507"),
            _f("estimated_arrival_date", "Estimated Arrival Date", "2026-09-27"),
            _f("batch_number", "Batch", "2026-09-27B"),
            _f("remaining_incoming_quantity", "Incoming Quantity", 40),
        ]
    }


def _shipment_row():
    # The presenter's unkeyed shape: no `key`, so no relabel by key.
    return {
        "fields": [
            _f("company_name", "Company", "Sorento"),
            {"label": "Shipment", "value": "SH-001"},
            {"label": "Container", "value": "DFSU7408507"},
            {"label": "Estimated Arrival Date", "value": "2026-09-27"},
            {"label": "Distinct Products", "value": 3},
        ]
    }


LIST_OFF = (
    "Here are the results.\n\n1. *Company:* Sorento\n*Product Code:* SRTWC286\n*Container:* SEGU4008631"
    "\n*ETA:* 2026-09-27\n*ETA Delay:* 2026-09-25\n*Incoming Quantity:* 40\n\U0001f6a9  *(PENDING ALLOCATION)*"
)
BYP_OFF = (
    "Here are the results.\n\n1. *Company:* Sorento\n*Product Code:* SRTWC286\n*Shipment Container:* DFSU7408507"
    "\n*Estimated Arrival Date:* 2026-09-27\n*Batch:* 2026-09-27B\n*Incoming Quantity:* 40"
)
SHIP_OFF = (
    "Here are the results.\n\n1. *Company:* Sorento\n*Shipment:* SH-001\n*Container:* DFSU7408507"
    "\n*Estimated Arrival Date:* 2026-09-27\n*Distinct Products:* 3"
)
DEALER_LINE = "SRTMCB6088-BL: ✅ ETA 23/10/2026"


# --------------------------------------------------------------------------- #
# Helpers in composers/format.py
# --------------------------------------------------------------------------- #


def test_date_matrix():
    from app.services.chatbot.composers import format as fmt

    assert fmt.date("2026-09-27") == "27/09/2026"
    assert fmt.date("2026-09-27T10:11:12") == "27/09/2026"
    assert fmt.date("2026-09-27 10:11") == "27/09/2026"
    assert fmt.date("27/09/2026") == "27/09/2026"
    for bad in (None, "", "x", "2026-9-7", "20260927", 20260927):
        assert fmt.date(bad) is None, bad


def test_value_date_only_gate_off_unchanged(monkeypatch):
    from app.services.chatbot.composers import format as fmt

    _set_gate(monkeypatch, False)
    assert fmt.value("2026-09-27") == "2026-09-27"
    assert fmt.value("2026-09-27T00:00:00") == "27/09/2026"
    assert fmt.value("2026-09-27T10:11:12") == "27/09/2026 10:11:12"


def test_value_date_only_gate_on(monkeypatch):
    from app.services.chatbot.composers import format as fmt

    _set_gate(monkeypatch, True)
    assert fmt.value("2026-09-27") == "27/09/2026"
    assert fmt.value(["2026-09-27", "2026-10-07"]) == "27/09/2026, 07/10/2026"
    assert fmt.value("2026-09-27T00:00:00") == "27/09/2026"
    assert fmt.value("2026-09-27T10:11:12") == "27/09/2026 10:11:12"
    for untouched in ("2026-09-27 to 2026-10-01", "SRT-2026-09-27", "2026-09-27x", "2026-09-27B"):
        assert fmt.value(untouched) == untouched


def test_label_by_key():
    from app.services.chatbot.composers import format as fmt

    assert fmt.label({"key": "shipping_container_number", "label": "Shipment Container"}) == "Container"
    assert fmt.label({"key": "estimated_arrival_date", "label": "Estimated Arrival Date"}) == "ETA"
    assert fmt.label({"key": "batch_number", "label": "Batch"}) == "Batch"
    assert fmt.label({"label": "Estimated Arrival Date"}) == "Estimated Arrival Date"


# --------------------------------------------------------------------------- #
# Main path, every incoming tool
# --------------------------------------------------------------------------- #


def test_incoming_list_dates_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _out([_incoming_row()], "crm_incoming_stock_list")
    assert "*ETA:* 27/09/2026" in text and "*ETA Delay:* 25/09/2026" in text
    assert not ISO_ON_LABEL_LINE.search(text), text


def test_incoming_list_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _out([_incoming_row()], "crm_incoming_stock_list") == LIST_OFF


def test_incoming_rows_order_unchanged_gate_on(monkeypatch):
    rows = [_incoming_row("2026-10-07", "2026-10-05", "C2"), _incoming_row("2026-09-27", "2026-09-25", "C1")]
    _set_gate(monkeypatch, False)
    off = _out(rows, "crm_incoming_stock_list")
    _set_gate(monkeypatch, True)
    on = _out(rows, "crm_incoming_stock_list")
    labels = lambda t: re.findall(r"^\*([^*\n]+):\*", t, re.MULTILINE)  # noqa: E731
    assert labels(on) == labels(off)
    assert on.index("C2") < on.index("C1")
    assert on.index("07/10/2026") < on.index("27/09/2026")


def test_by_product_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _out([_by_product_row()], "crm_incoming_stock_by_product") == BYP_OFF


def test_by_product_labels_and_dates_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _out([_by_product_row()], "crm_incoming_stock_by_product")
    assert "*Container:* DFSU7408507" in text
    assert "*ETA:* 27/09/2026" in text
    assert "Shipment Container" not in text and "Estimated Arrival Date" not in text
    # a Batch value is not a date: it never changes
    assert "*Batch:* 2026-09-27B" in text


def test_shipments_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _out([_shipment_row()], "crm_incoming_stock_shipments") == SHIP_OFF


def test_shipments_date_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _out([_shipment_row()], "crm_incoming_stock_shipments")
    assert "27/09/2026" in text and "2026-09-27" not in text
    assert "*Container:* DFSU7408507" in text and "*Shipment:* SH-001" in text


@pytest.mark.parametrize("loc,container", [("ms", "Kontena"), ("zh", "货柜")])
def test_container_label_localized_gate_on(monkeypatch, loc, container):
    _set_gate(monkeypatch, True)
    text = _out([_by_product_row()], "crm_incoming_stock_by_product", loc)
    assert f"*{container}:* DFSU7408507" in text
    assert "Shipment Container" not in text


@pytest.mark.parametrize("gate", [False, True])
def test_dealer_eta_line_unchanged(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    dealer = {"title": DEALER_LINE, "fields": [], "flags": {"dealer_view": True}}
    assert _out([dealer], "crm_incoming_stock_list") == f"Here are the results.\n\n1. {DEALER_LINE}"


# --------------------------------------------------------------------------- #
# Option (a): every tool (lead decision OI-1)
# --------------------------------------------------------------------------- #


def test_non_incoming_dates_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    order = {"fields": [_f("order_date", "Order Date", "2026-08-10"), _f("order_no", "Order No", "SO1")]}
    promo = {"fields": [_f("start_date", "Start Date", "2026-09-25"), _f("end_date", "End Date", "2026-10-31")]}
    assert _out([order], "crm_orders_list") == "Here are the results.\n\n1. *Order Date:* 2026-08-10\n*Order No:* SO1"
    assert _out([promo], "crm_promotions_list") == (
        "Here are the results.\n\n1. *Start Date:* 2026-09-25\n*End Date:* 2026-10-31"
    )


def test_non_incoming_dates_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    order = {"fields": [_f("order_date", "Order Date", "2026-08-10"), _f("order_no", "Order No", "SO1")]}
    promo = {"fields": [_f("start_date", "Start Date", "2026-09-25"), _f("end_date", "End Date", "2026-10-31")]}
    assert _out([order], "crm_orders_list") == "Here are the results.\n\n1. *Order Date:* 10/08/2026\n*Order No:* SO1"
    assert _out([promo], "crm_promotions_list") == (
        "Here are the results.\n\n1. *Start Date:* 25/09/2026\n*End Date:* 31/10/2026"
    )


NON_DATES = [
    ("Batch", "2026-09-27B"),
    ("Batch", "B2026-09-27"),
    ("Shipment", "SH-2026-09-27"),
    ("Container", "DFSU7408507"),
    ("Container", "2026-09-27 to 2026-10-01"),
    ("Order No", "SO-2026-0927"),
    ("Product Code", "SRT-2026-09-27"),
]


@pytest.mark.parametrize("label,val", NON_DATES, ids=[f"{a}={b}" for a, b in NON_DATES])
def test_no_non_date_value_changes_gate_on(monkeypatch, label, val):
    item = {"fields": [{"label": label, "value": val}]}
    _set_gate(monkeypatch, False)
    off = _out([item], "crm_incoming_stock_list")
    _set_gate(monkeypatch, True)
    assert _out([item], "crm_incoming_stock_list") == off == f"Here are the results.\n\n1. *{label}:* {val}"


def test_s2_main_path_output_clean_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    texts = [
        _out([_incoming_row()], "crm_incoming_stock_list"),
        _out([_by_product_row()], "crm_incoming_stock_by_product"),
        _out([_shipment_row()], "crm_incoming_stock_shipments"),
    ]
    for text in texts:
        assert " · " not in text
        assert "Shipment Container" not in text
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
        for line in text.splitlines():
            if re.match(r"^(\d+\. )?\*[^*]+:\*", line) and not line.startswith(("*Batch", "1. *Batch")):
                assert not re.search(r":\*\s+\d{4}-\d{2}-\d{2}\s*$", line), line
