"""AC-CONSIGN-CRM Q5 a: a consignment line never counts in an SO-based sales figure (sales
report, top selling, the Reports `sales_order_lines` dataset): the sale counts where the DO /
invoice already does. Postgres, every row seeded here.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.base import set_company_scope
from app.models.order import SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services import sales_report_service
from app.services.company_scope import DEFAULT_COMPANY_ID

from ._pg_fixture import blank_session, unique_code


@pytest.fixture
def seeded():
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        category = ProductCategory(id=str(uuid.uuid4()), category_code=unique_code("C")[:50],
                                   category_name="ZZT")
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("U")[:20], uom_name="u")
        db.add_all([category, uom])
        db.flush()
        code = unique_code("P")[:50]
        product = Product(id=str(uuid.uuid4()), product_code=code, product_name=code,
                          category_id=category.id, base_uom_id=uom.id, list_price=10,
                          company_id=DEFAULT_COMPANY_ID)
        db.add(product)
        db.flush()
        for doc_type, qty in (("sales_order", 7), ("consignment", 5)):
            so = SalesOrder(so_number=unique_code("SO")[:40], company_id=DEFAULT_COMPANY_ID,
                            order_date=date(2026, 10, 5), status="open", doc_type=doc_type)
            db.add(so)
            db.flush()
            db.add(SalesOrderLine(sales_order_id=so.id, product_id=product.id, qty_ordered=qty,
                                  qty_delivered=qty, line_total=Decimal(qty * 10),
                                  company_id=DEFAULT_COMPANY_ID))
        db.flush()
        yield db, product


def test_top_selling_leaves_a_consignment_out(seeded):
    db, product = seeded
    for basis in ("ordered", "delivered"):
        out = sales_report_service.top_selling(
            db, rank_by="quantity", basis=basis, date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31), detail_code=product.product_code)
        rows = [r for r in out["rows"] if r.get("code") == product.product_code]
        assert rows and rows[0]["quantity"] == 7, (basis, out["rows"])


def test_the_common_sales_filter_names_the_doc_type():
    filters = sales_report_service._common_filters(
        product_ids=None, customer_query=None, customer_ids=None, channel=None,
        warehouse_ids=None, date_from=None, date_to=None, bucket_expr=SalesOrder.order_date)
    assert any("doc_type" in str(f) for f in filters)
