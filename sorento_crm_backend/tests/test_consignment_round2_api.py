"""AC-CONSIGN-CRM round 2, UAC-06/12: `doc_type` and `qty_returned` reach the API.

Postgres only, every row seeded here. Harness = tests/test_outstanding_report_brand_filter.py's
(superadmin client over a blank schema).
"""
from __future__ import annotations

import uuid
from datetime import date

from app.models.order import DOC_TYPE_CONSIGNMENT, SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._mc_lookup_seed import customer, product
from tests._pg_fixture import unique_code
from tests.test_outstanding_report_brand_filter import client, db  # noqa: F401


def _seed(db, *, consignment: bool, qty_returned=0, so_number=None):
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=unique_code("ZZT CG Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTCGP"))
    so = SalesOrder(id=str(uuid.uuid4()), so_number=so_number or unique_code("ZZTCG"),
                    customer_id=cust.id, order_date=date(2026, 10, 1), status="open",
                    company_id=DEFAULT_COMPANY_ID)
    if consignment:
        so.doc_type = DOC_TYPE_CONSIGNMENT
    db.add(so)
    db.flush()
    line = SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id, product_id=prod.id,
                          qty_ordered=10, qty_delivered=0, line_status="open",
                          company_id=DEFAULT_COMPANY_ID)
    if consignment:
        line.qty_returned = qty_returned
    db.add(line)
    db.commit()
    return so, cust, prod


def test_so_list_carries_doc_type_for_consignment_and_plain(client, db):
    cg, _c1, _p1 = _seed(db, consignment=True)
    so, _c2, _p2 = _seed(db, consignment=False)
    res = client.get("/api/v1/scm/sales-orders", params={"limit": 100})
    assert res.status_code == 200, res.text
    by_no = {r["so_number"]: r for r in res.json()["data"]}
    assert by_no[cg.so_number].get("doc_type") == "consignment", by_no[cg.so_number]
    assert by_no[so.so_number].get("doc_type") == "sales_order", by_no[so.so_number]


def test_so_detail_carries_doc_type(client, db):
    cg, _c1, _p1 = _seed(db, consignment=True)
    so, _c2, _p2 = _seed(db, consignment=False)
    assert client.get(f"/api/v1/scm/sales-orders/{cg.id}").json().get("doc_type") == "consignment"
    assert client.get(f"/api/v1/scm/sales-orders/{so.id}").json().get("doc_type") == "sales_order"


def test_so_detail_line_carries_qty_returned(client, db):
    cg, _c, _p = _seed(db, consignment=True, qty_returned=4)
    res = client.get(f"/api/v1/scm/sales-orders/{cg.id}")
    assert res.status_code == 200, res.text
    lines = res.json()["lines"]
    assert len(lines) == 1
    assert float(lines[0].get("qty_returned", -1)) == 4.0, lines[0]


def test_outstanding_report_so_row_carries_doc_type(client, db):
    cg, cust, prod = _seed(db, consignment=True)
    res = client.get("/api/v1/order-management/outstanding-report",
                     params={"product_code": prod.product_code, "scope": "so"})
    assert res.status_code == 200, res.text
    rows = res.json()["so_rows"]
    assert [r["so_number"] for r in rows] == [cg.so_number], rows
    assert rows[0].get("doc_type") == "consignment", rows[0]
