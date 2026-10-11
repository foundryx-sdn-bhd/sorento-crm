"""Phase 2 RED tests - REPORTS-STEP2 S2 AC-15: the presenter marks carried facts " (from earlier)".

`carried` is a list of route param names (pinned by the tester): `date_from` / `date_to` for the
Period line, `<key>_ids` for a filter line (brand -> `brand_ids`, customer -> `customer_ids`, ...).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sorento_crm_mcp.presenters import _report_ask  # noqa: E402

EARLIER = " (from earlier)"


def _body(**over):
    body = {
        "status": "ok", "message": None, "basis": "delivered", "basis_label": "delivered sales",
        "measure": "amount", "measure_key": "delivered_amount", "group_by": "sales_agent",
        "group_label": "Sales agent", "sort": "desc", "date_from": "2026-09-01", "date_to": "2026-09-30",
        "filters": [
            {"key": "brand", "label": "Brand", "values": ["Sorento"]},
            {"key": "category", "label": "Category", "values": ["Water closet"]},
        ],
        "rows": [{"rank": 1, "name": "AGENT A", "qty": 3, "amount": 30.0, "keys": []}],
        "more": 0, "total_count": 1, "total": {"qty": 3, "amount": 30.0},
    }
    body.update(over)
    return body


def _lines(text):
    return {l.split(":")[0]: l for l in text.splitlines() if ":" in l and not l[0].isdigit()}


def test_ac15_no_carried_means_no_marks():
    text = _report_ask(_body())
    assert EARLIER not in text


def test_ac15_a_carried_period_and_brand_are_marked_and_the_category_is_not():
    lines = _lines(_report_ask(_body(carried=["date_from", "date_to", "brand_ids"])))
    assert lines["Period"].endswith(EARLIER), lines
    assert lines["Brand"].endswith(EARLIER), lines
    assert not lines["Category"].endswith(EARLIER), lines


def test_ac15_a_period_said_now_is_not_marked():
    lines = _lines(_report_ask(_body(carried=["brand_ids", "category_ids"])))
    assert not lines["Period"].endswith(EARLIER), lines
    assert lines["Brand"].endswith(EARLIER) and lines["Category"].endswith(EARLIER), lines


def test_ac15_one_fact_per_line_with_no_dot_joins():
    text = _report_ask(_body(carried=["date_from", "date_to", "brand_ids"]))
    assert " · " not in text
    assert "Brand: Sorento" in text and "Period: 2026-09-01 to 2026-09-30" in text
