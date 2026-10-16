"""AC-CONSIGN-CRM round 2, UAC-06: `so_doc_type` on the Fulfilment Planning row.

Harness = tests/test_project_so_reconciliation_routes.py (blank schema, route).
"""
from __future__ import annotations

from app.models.order import DOC_TYPE_CONSIGNMENT, SalesOrder

from .test_project_so_reconciliation_routes import BASE, api  # noqa: F401 - api is a fixture


def test_fulfilment_planning_row_carries_so_doc_type_for_a_consignment_core_so(api):
    client, db, _project, linked, awaiting, _owner = api
    db.get(SalesOrder, linked.so_id).doc_type = DOC_TYPE_CONSIGNMENT
    db.commit()
    rows = {r["id"]: r for r in client.get(f"{BASE}/fulfilment-planning").json()["data"]}
    assert rows[linked.id].get("so_doc_type") == "consignment", rows[linked.id]
    # an order with no core SO yet has nothing to be a consignment of
    assert "so_doc_type" in rows[awaiting.id], rows[awaiting.id]
    assert rows[awaiting.id]["so_doc_type"] is None, rows[awaiting.id]


def test_fulfilment_planning_row_reads_sales_order_for_a_plain_core_so(api):
    client, _db, _project, linked, _awaiting, _owner = api
    rows = {r["id"]: r for r in client.get(f"{BASE}/fulfilment-planning").json()["data"]}
    assert rows[linked.id].get("so_doc_type") == "sales_order", rows[linked.id]
