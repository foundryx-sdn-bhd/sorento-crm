"""Phase 2 RED tests - the MCP `crm_report` ToolSpec (REPORTS-STEP2 S1, AC-7 and AC-9).

The backend registry lives in the backend package, which this package cannot import, so the six S1
keys are a literal here (the same pinning `test_report_ask_catalog_ceiling.py` uses).
"""
from __future__ import annotations

import sys
from pathlib import Path

# The venv's installed `sorento_crm_mcp` may point at another checkout: use this one.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sorento_crm_mcp.catalog import CATALOG  # noqa: E402

S1 = (
    "ordered_amount", "ordered_qty", "delivered_amount", "delivered_qty",
    "outstanding_amount", "outstanding_qty",
)


def _spec(name="crm_report"):
    return next((s for s in CATALOG if s.name == name), None)


def test_ac9_crm_report_is_in_the_catalogue():
    assert _spec() is not None, "no crm_report ToolSpec"


def test_ac9_crm_report_is_read_only_and_points_at_the_report_route():
    spec = _spec()
    assert spec is not None
    assert spec.path == "/api/v1/order-management/report"
    assert spec.method == "GET" or spec.read_only


def test_ac9_crm_report_carries_the_contact_pair_and_the_filters():
    spec = _spec()
    assert spec is not None
    for p in (
        "measure", "group_by", "top_n", "sort", "date_from", "date_to", "product_ids", "brand_ids",
        "category_ids", "sales_agent_ids", "customer_ids", "customer_agent_ids", "warehouse_codes",
        "channel", "product_code", "contact_id", "space_id",
    ):
        assert p in spec.query_params, p
    assert "basis" in spec.query_params


def test_ac9_crm_report_restricts_like_crm_report_ask():
    spec, old = _spec(), _spec("crm_report_ask")
    assert spec is not None and old is not None
    # `crm_report` serves the sales measures only; the chat dataset (CA-3) stays on crm_report_ask.
    sales_only = tuple(f for f in old.restricted_fields if f[0] != "chatbot.analytics")
    assert spec.restricted_fields == sales_only


def test_ac9_the_description_names_every_registry_key():
    spec = _spec()
    assert spec is not None
    for key in S1:
        assert key in spec.description, key


def test_ac9_the_old_report_ask_tool_stays():
    assert _spec("crm_report_ask") is not None


def test_ac9_crm_report_is_rendered_by_the_report_ask_presenter():
    import json

    from sorento_crm_mcp import presenters

    body = {
        "status": "ok", "message": None, "basis": "delivered", "basis_label": "delivered sales",
        "measure": "amount", "group_by": None, "group_label": None, "date_from": "2026-09-01",
        "date_to": "2026-09-30", "rows": [], "more": 0, "total_count": 0,
        "total": {"qty": 3, "amount": 30.0}, "filters": [], "sort": "desc",
    }
    new = json.loads(presenters.present_response("crm_report", json.dumps(body)))
    old = json.loads(presenters.present_response("crm_report_ask", json.dumps(body)))
    assert new.get("result_type") == "report_ask", new
    assert new == old
