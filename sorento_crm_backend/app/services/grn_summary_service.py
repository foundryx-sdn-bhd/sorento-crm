"""Received quantity in a window and the last receipt, per product (MULTI-ASK-PLAN S3).

The chatbot's `received_qty` and `last_receipt` sub-asks ("2026 入库了多少", "最后一次进库是
什么时候") read ONE call of this: for each asked product, the sum of
`picking_lines.quantity_picked` over approved, not cancelled, `goods_received` headers
whose `picking_date` falls in the window, and the newest such header of ALL time with its
quantity summed over that date. Received is counted the way
`procurement_service.py:1068-1080` already counts it for a shipment.

A receipt booked through the ESB-stated path has a `spo_allocations.quantity_received`
and no GRN, so it is not counted here (card: "Known data gap").

Card: documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.base import get_company_scope
from app.models.procurement import PickingHeader, PickingLine
from app.models.product import Product
from app.services.company_scope import build_company_predicate


def _approved_grn_lines(db: Session, product_ids: list[str]):
    q = (
        db.query(PickingLine.product_id)
        .join(PickingHeader, PickingHeader.id == PickingLine.picking_header_id)
        .filter(
            PickingHeader.picking_type == "goods_received",
            PickingHeader.picking_status == "approved",
            PickingHeader.is_cancelled.is_(False),
            PickingLine.product_id.in_(product_ids),
        )
    )
    # Explicitly: a column query is not an entity query, so the session listener's
    # company criteria are not relied on (the `spo_last_receipt_service` precedent).
    for model in (PickingLine, PickingHeader):
        predicate = build_company_predicate(model, get_company_scope(db))
        if predicate is not None:
            q = q.filter(predicate)
    return q


def grn_summary_rows(
    db: Session,
    *,
    product_ids: list[str],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[dict[str, Any]]:
    """One row per asked product that exists, in the asked order."""
    if not product_ids:
        return []
    codes = dict(db.query(Product.id, Product.product_code).filter(Product.id.in_(product_ids)).all())
    codes = {str(k): v for k, v in codes.items()}

    windowed = _approved_grn_lines(db, product_ids)
    if date_from is not None:
        windowed = windowed.filter(PickingHeader.picking_date >= date_from)
    if date_to is not None:
        windowed = windowed.filter(PickingHeader.picking_date <= date_to)
    received = {
        str(pid): (int(qty or 0), int(count or 0))
        for pid, qty, count in windowed.with_entities(
            PickingLine.product_id,
            func.sum(PickingLine.quantity_picked),
            func.count(func.distinct(PickingHeader.id)),
        )
        .group_by(PickingLine.product_id)
        .all()
    }

    newest = (
        _approved_grn_lines(db, product_ids)
        .with_entities(PickingLine.product_id, func.max(PickingHeader.picking_date).label("last_date"))
        .group_by(PickingLine.product_id)
        .subquery()
    )
    last = {
        str(pid): (last_date, int(qty or 0))
        for pid, last_date, qty in _approved_grn_lines(db, product_ids)
        .join(newest, (newest.c.product_id == PickingLine.product_id) & (newest.c.last_date == PickingHeader.picking_date))
        .with_entities(PickingLine.product_id, newest.c.last_date, func.sum(PickingLine.quantity_picked))
        .group_by(PickingLine.product_id, newest.c.last_date)
        .all()
    }

    rows: list[dict[str, Any]] = []
    for pid in dict.fromkeys(str(p) for p in product_ids):
        if pid not in codes:
            continue
        qty, count = received.get(pid, (0, 0))
        last_date, last_qty = last.get(pid, (None, None))
        rows.append(
            {
                "product_id": pid,
                "product_code": codes[pid],
                "received_qty": qty,
                "grn_count": count,
                "last_receipt_date": last_date.isoformat() if last_date else None,
                "last_receipt_qty": last_qty,
            }
        )
    return rows
