"""AC-CONSIGN-CRM round 2, UAC-06: `so_doc_type` on the Order Inquiry header row.

Harness = tests/test_oi_header_list.py (blank schema, route).
"""
from __future__ import annotations

from app.models.order import DOC_TYPE_CONSIGNMENT

from .test_oi_header_list import HEADERS, _header, api  # noqa: F401 - api is a fixture


def test_header_row_carries_so_doc_type_consignment_and_sales_order(api):
    client, db, company_id = api
    cg = _header(db, company_id)
    plain = _header(db, company_id)
    cg["core"].doc_type = DOC_TYPE_CONSIGNMENT
    db.commit()
    res = client.get(HEADERS, params={"state": "all"})
    assert res.status_code == 200, res.text
    by_id = {r["id"]: r for r in res.json()["data"]}
    assert by_id[cg["inquiry"].id].get("so_doc_type") == "consignment", by_id[cg["inquiry"].id]
    assert by_id[plain["inquiry"].id].get("so_doc_type") == "sales_order", by_id[plain["inquiry"].id]
