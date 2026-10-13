"""MULTI-ASK-PLAN slice 1 (owner, 9 Oct 2026; PROD turn 8 Oct 19:50, six asks about
SRTWT7445): the three fixes that need no parser change.

* R8: the order summary's customer COUNT is labelled as a count (`客户数`), never `客户`.
* R5: the header date line is printed only under a tool that takes a window.
R3 (exact codes) is not here: the owner's Q2 ruling (9 Oct) makes it a parser-driven
ask-back ("SRTWT7445 has 8 variants: just this one, or all 8?"), built with the parser
slice.

Card: documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.chatbot import label_catalog, turn_runtime
from app.services.chatbot.label_catalog import Localizer


def _presenters():
    mcp_root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    from sorento_crm_mcp import presenters

    return presenters


# ---------------------------------------------------------------- R8: a count is a count


def test_the_summary_customer_count_is_labelled_customer_count():
    p = _presenters()
    items = p.summary_items(
        {"products": [{"product_code": "SRTWT7445", "customer_count": 33, "so_count": 4}]}
    )
    first = items[0]["fields"][0]
    assert first == {"key": "customers", "label": "Customer count", "value": 33}


@pytest.mark.parametrize("lang, expected", [("zh", "客户数"), ("ms", "Bil. pelanggan")])
def test_the_customer_count_label_translates_as_a_count(lang, expected):
    loc = Localizer(lang, label_catalog.defaults(lang))
    assert loc.label({"key": "customers", "label": "Customer count", "value": 33}) == expected
    # The old key, whose stored translation memory row says 客户, is no longer printed.
    assert "Customers" not in label_catalog.LABELS


# ---------------------------------------------------------------- R5: the date line


WINDOW = {"date_filter_start": "2026-01-01", "date_filter_end": "2026-12-31"}


def test_a_tool_that_takes_a_window_states_it():
    line = turn_runtime._date_line(WINDOW, "crm_outstanding_report")
    assert line and line.startswith("Order date: ")


@pytest.mark.parametrize(
    "tool",
    [
        "crm_procurement_po_last_cost_list",
        "crm_procurement_spo_allocations_last_receipt_list",
        "crm_inventory_stock_balance_list",
    ],
)
def test_a_tool_that_takes_no_window_never_states_one(tool):
    assert turn_runtime._date_line(WINDOW, tool) is None


def test_an_unknown_tool_keeps_the_line_as_before():
    assert turn_runtime._date_line(WINDOW, None) is not None
