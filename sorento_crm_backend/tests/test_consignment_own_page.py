"""AC-CONSIGN-CRM UAC-18: consignments leave the Sales Orders list for their own page.

`GET /api/v1/scm/sales-orders` lists sales orders only unless `doc_type=consignment`; an unknown
doc_type is a 422; detail by id still answers for a consignment. Harness =
tests/test_consignment_round2_api.py (superadmin client over a blank schema); every row is fake.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.models.order import DOC_TYPE_CONSIGNMENT, SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._mc_lookup_seed import customer, product
from tests._pg_fixture import unique_code
from tests.test_outstanding_report_brand_filter import client, db  # noqa: F401

LIST = "/api/v1/scm/sales-orders"


def _pair(db):
    """One plain SO and one consignment sharing a customer and a number stem."""
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=unique_code("ZZT Own Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTOWN"))
    stem = unique_code("ZZTOWN")
    out = {}
    for kind, number in (("so", f"{stem}-SO"), ("cg", f"{stem}-CG")):
        so = SalesOrder(id=str(uuid.uuid4()), so_number=number, customer_id=cust.id,
                        order_date=date(2026, 10, 1), status="open",
                        company_id=DEFAULT_COMPANY_ID)
        if kind == "cg":
            so.doc_type = DOC_TYPE_CONSIGNMENT
        db.add(so)
        db.flush()
        line = SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id, product_id=prod.id,
                              qty_ordered=10, qty_delivered=3, line_status="open",
                              company_id=DEFAULT_COMPANY_ID)
        if kind == "cg":
            line.qty_returned = 2
        db.add(line)
        out[kind] = so
    db.commit()
    return out, stem


def _numbers(res):
    assert res.status_code == 200, res.text
    return [r["so_number"] for r in res.json()["data"]]


def test_default_list_has_the_sales_order_and_not_the_consignment(client, db):
    rows, stem = _pair(db)
    res = client.get(LIST, params={"query": stem, "limit": 100})
    assert _numbers(res) == [rows["so"].so_number], res.json()["data"]
    assert res.json()["pagination"]["total"] == 1


def test_searching_a_consignment_number_on_the_sales_orders_list_finds_nothing(client, db):
    rows, _stem = _pair(db)
    res = client.get(LIST, params={"query": rows["cg"].so_number})
    assert _numbers(res) == []
    assert res.json()["pagination"]["total"] == 0


def test_doc_type_consignment_lists_only_the_consignment_with_its_line_quantities(client, db):
    rows, stem = _pair(db)
    res = client.get(LIST, params={"query": stem, "doc_type": "consignment", "limit": 100})
    assert _numbers(res) == [rows["cg"].so_number], res.json()
    line = res.json()["data"][0]["lines"][0]
    assert float(line["qty_ordered"]) == 10 and float(line["qty_delivered"]) == 3
    assert float(line.get("qty_returned", -1)) == 2, line


def test_doc_type_sales_order_behaves_like_the_default(client, db):
    rows, stem = _pair(db)
    res = client.get(LIST, params={"query": stem, "doc_type": "sales_order", "limit": 100})
    assert _numbers(res) == [rows["so"].so_number]


def test_an_unknown_doc_type_is_422(client, db):
    _pair(db)
    assert client.get(LIST, params={"doc_type": "nonsense"}).status_code == 422


def test_detail_by_id_still_answers_for_a_consignment(client, db):
    rows, _stem = _pair(db)
    res = client.get(f"{LIST}/{rows['cg'].id}")
    assert res.status_code == 200, res.text
    assert res.json()["doc_type"] == "consignment"
