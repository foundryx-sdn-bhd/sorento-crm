"""AC-CONSIGN-CRM round 2, UAC-06: `so_doc_type` on the Order Inquiry worklist row.

Harness = tests/test_order_inquiry_worklist.py (blank schema, route). `response_model` drops
undeclared fields, so this reads the field off the ROUTE response. Authored rows (no core SO)
carry null.
"""
from __future__ import annotations

from app.models.order import DOC_TYPE_CONSIGNMENT

from .test_order_inquiry_worklist import LIST, api  # noqa: F401 - api is a fixture


def test_worklist_row_carries_so_doc_type_for_a_consignment_core_so(api):
    client, db, _company_id, seeded = api
    seeded["core"].doc_type = DOC_TYPE_CONSIGNMENT
    db.commit()
    body = client.get(LIST).json()
    adopted = next(r for r in body["data"] if r["id"] == seeded["adopted_row"].id)
    assert adopted.get("so_doc_type") == "consignment", adopted


def test_worklist_row_reads_sales_order_for_a_plain_core_so_and_null_without_one(api):
    client, _db, _company_id, seeded = api
    body = client.get(LIST).json()
    adopted = next(r for r in body["data"] if r["id"] == seeded["adopted_row"].id)
    authored = next(r for r in body["data"] if r["id"] == seeded["authored_row"].id)
    assert "so_doc_type" in adopted, adopted
    assert adopted["so_doc_type"] == "sales_order", adopted
    assert "so_doc_type" in authored, authored
    assert authored["so_doc_type"] is None, authored
