"""Phase 2 RED tests - REPORTS-STEP2 S4: `crm_report` renders each `report_kind` with the presenter
that kind uses today, so the reply text is byte-for-byte the old tool's (AC-19).

Bodies are small hand-made payloads; what is pinned is only that the new tool and the old tool
produce the SAME envelope for the same body, and that it is not the ranking presenter's.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sorento_crm_mcp.presenters import present_response  # noqa: E402

BODIES = {
    "outstanding": (
        "crm_outstanding_report",
        {"product_code": "ZZT-1", "scope": "both",
         "so": {"ordered_qty": 10, "transferred_qty": 3, "outstanding_qty": 7, "so_count": 1,
                "order_date_min": None, "order_date_max": None}},
    ),
    "sales_report": (
        "crm_sales_report",
        {"total": {"qty": 12, "amount": 120.0}, "periods": [], "options": []},
    ),
    "sales_analysis": (
        "crm_sales_analysis",
        {"status": "clarify", "message": "Which company, Sorento or Mocha?"},
    ),
    "low_stock": (
        "crm_low_stock_report",
        {"status": "busy", "message": "Too many low stock reports right now."},
    ),
}


def _env(tool, body):
    return json.loads(present_response(tool, json.dumps(body)))


@pytest.mark.parametrize("kind", sorted(BODIES))
def test_ac19_crm_report_renders_a_kind_like_its_old_tool(kind):
    old_tool, body = BODIES[kind]
    old = _env(old_tool, body)
    new = _env("crm_report", {**body, "report_kind": kind})
    assert new == old, (kind, new, old)
    assert new.get("result_type") != "report_ask", new


def test_ac19_a_ranking_body_still_renders_as_a_report_ask():
    body = {
        "status": "ok", "message": None, "basis": "delivered", "basis_label": "delivered sales",
        "measure": "amount", "group_by": None, "group_label": None, "date_from": "2026-09-01",
        "date_to": "2026-09-30", "rows": [], "more": 0, "total_count": 0,
        "total": {"qty": 3, "amount": 30.0}, "filters": [], "sort": "desc",
    }
    assert _env("crm_report", {**body, "report_kind": "ranking"}) == _env("crm_report_ask", body)
    assert _env("crm_report", body) == _env("crm_report_ask", body)


def test_ac23_the_description_names_the_new_keys():
    from sorento_crm_mcp.catalog import CATALOG

    spec = next((s for s in CATALOG if s.name == "crm_report"), None)
    assert spec is not None
    for key in ("order_outstanding", "delivered_sales", "sales_value", "low_stock", "so_count"):
        assert key in spec.description, key
