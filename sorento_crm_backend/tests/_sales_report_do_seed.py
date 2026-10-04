"""Seed helpers for the delivered-basis sales report (lane SALES-REPORT, PR #1401).

Not a test module (leading underscore). Shared by `test_sales_report_delivered.py` and
`test_sales_report.py` so the two cannot disagree about what a delivery order looks like.

A DO is an `orders` row joined to `order_lines`. `source_book` NULL is the legacy import (it
repeats the DOC total on EVERY line's `total`); `source_book == "db1"` is AutoCount (each line
carries its own total). Every row is seeded here: CI's database has none.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from app.models.access import MarketSegment, StockVisibilityPolicy
from app.models.order import Order, OrderLine, SalesOrder
from app.models.sales_agent import SalesAgent
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._mc_lookup_seed import customer, product, warehouse
from tests._pg_fixture import unique_code
from tests.test_top_selling_report import (  # noqa: F401  (fixtures and helpers used by name)
    _as_contact,
    _contact,
    _link,
    client,
    db,
)

BASE = "/api/v1/order-management/sales-report"


def money(v) -> Decimal:
    """A money figure as a 2dp Decimal, whether the body carries a number or a string."""
    return Decimal(str(v)).quantize(Decimal("0.01"))


def seed_segment(db, *, code: str) -> MarketSegment:
    row = MarketSegment(id=str(uuid.uuid4()), code=code, name=code, is_active=True)
    db.add(row)
    db.flush()
    return row


def seed_customer(db, *, name: Optional[str] = None, segment_code: Optional[str] = None,
                  company_id: str = DEFAULT_COMPANY_ID):
    row = customer(db, company_id=company_id, name=name or unique_code("Cust"))
    if segment_code is not None:
        row.market_segment_code = segment_code
        db.flush()
    return row


def seed_agent(db, *, code: str) -> SalesAgent:
    row = SalesAgent(id=str(uuid.uuid4()), sales_agent=code, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def seed_so(db, *, customer_id, agent_id=None, number: Optional[str] = None,
            company_id: str = DEFAULT_COMPANY_ID) -> SalesOrder:
    row = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=number or unique_code("SO"),
        customer_id=customer_id,
        sales_agent_id=agent_id,
        status="open",
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


def line(product_id, warehouse_id, qty, *, price=None, discount=0, total=None) -> dict:
    """One DO line spec for `seed_do`. `discount` is a FRACTION 0..1."""
    return {
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "qty": qty,
        "price": price,
        "discount": discount,
        "total": total,
    }


def seed_do(
    db,
    *,
    customer_id,
    order_date: Optional[date],
    lines: list[dict],
    number: Optional[str] = None,
    source_book: Optional[str] = None,
    is_cancelled: bool = False,
    deleted: bool = False,
    sales_order_id: Optional[str] = None,
    company_id: str = DEFAULT_COMPANY_ID,
) -> Order:
    """One delivery order. `source_book=None` is a legacy DO, `"db1"` an AutoCount one."""
    from datetime import datetime

    row = Order(
        id=str(uuid.uuid4()),
        order_number=number or unique_code("DO"),
        order_date=order_date,
        customer_id=customer_id,
        company_id=company_id,
        source_book=source_book,
        is_cancelled=is_cancelled,
        deleted_at=datetime(2026, 1, 1) if deleted else None,
        sales_order_id=sales_order_id,
    )
    db.add(row)
    db.flush()
    for seq, spec in enumerate(lines, start=1):
        db.add(
            OrderLine(
                id=str(uuid.uuid4()),
                order_id=row.id,
                line_sequence=seq,
                product_id=spec["product_id"],
                warehouse_id=spec["warehouse_id"],
                quantity=spec["qty"],
                unit_price=spec.get("price"),
                discount=spec.get("discount", 0),
                total=spec.get("total"),
                company_id=company_id,
            )
        )
    db.flush()
    return row


def seed_policy(db, contact, *, warehouse_ids=None, excluded_warehouse_ids=None) -> StockVisibilityPolicy:
    """The per-contact stock visibility override (`stock_visibility.resolve_policy` reads it)."""
    row = StockVisibilityPolicy(
        id=str(uuid.uuid4()),
        contact_id=contact.id,
        mode="detailed",
        warehouse_ids=warehouse_ids,
        excluded_warehouse_ids=excluded_warehouse_ids,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "BASE", "money", "seed_segment", "seed_customer", "seed_agent", "seed_so", "line", "seed_do",
    "seed_policy", "product", "warehouse", "customer", "client", "db", "_as_contact", "_contact", "_link",
]
