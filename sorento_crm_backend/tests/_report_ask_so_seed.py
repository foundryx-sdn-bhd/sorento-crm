"""Sales order line seed for the report ask (lane REPORT-ENGINE, owner direction 4 Oct 2026).

Not a test module (leading underscore). Every report-ask basis (delivered, ordered, outstanding)
reads SALES ORDER lines with the SO's own sales agent, so a test seeds one SO per call with one
line on it. Every row is seeded here: CI's database has none.

A line's figures, by the one definition top selling uses (`sales_report_service`):

* ordered     = `qty_ordered` / `line_total`
* delivered   = `least(qty_delivered, qty_ordered)` / `line_total` pro-rated by that qty
* outstanding = ordered minus delivered while the SO status AND the line status are both `open`
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from app.models.order import SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._pg_fixture import unique_code

_ALL = object()


def seed_so_line(
    db,
    *,
    cust,
    prod,
    qty,
    total,
    agent=None,
    wh=None,
    order_date: Optional[date] = date(2026, 9, 10),
    required_date: Optional[date] = None,
    delivered=_ALL,
    so_status: str = "open",
    line_status: str = "open",
    demand_class: Optional[str] = None,
    company_id: str = DEFAULT_COMPANY_ID,
) -> SalesOrder:
    """One sales order (`agent` None = an SO with no agent) carrying one line.

    `delivered` defaults to the whole `qty` (a fully delivered line); pass a number for a partial
    delivery. `required_date` None means the line is dated by its SO's `order_date`."""
    so = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=unique_code("SO"),
        customer_id=cust.id,
        sales_agent_id=agent.id if agent is not None else None,
        order_date=order_date,
        status=so_status,
        demand_class=demand_class,
        company_id=company_id,
    )
    db.add(so)
    db.flush()
    db.add(
        SalesOrderLine(
            id=str(uuid.uuid4()),
            sales_order_id=so.id,
            product_id=prod.id,
            warehouse_id=wh.id if wh is not None else None,
            qty_ordered=qty,
            qty_delivered=qty if delivered is _ALL else delivered,
            line_total=Decimal(str(total)),
            line_status=line_status,
            required_date=required_date,
            company_id=company_id,
        )
    )
    db.flush()
    return so


__all__ = ["seed_so_line"]
