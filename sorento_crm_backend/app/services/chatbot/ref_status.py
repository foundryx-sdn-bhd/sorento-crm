"""REF-PROBES: the chatbot's card for a purchase order (our PO number) or a purchase request
the resolver placed. Staff only; the lane arm that calls this checks the audience and the
`purchase_orders.placed` grant. Read in process on the engine's own session, like `so_status`.

One card per row, words only:

    *Purchase order PO-2026/04-0011* - Supplier Bhd
    Issued 5 Sep 2026 - Active
    SKU x 5
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.procurement import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequestHeader,
    PurchaseRequestLine,
    Supplier,
)
from app.models.product import Product

#: Lines printed per card; the rest are counted, not listed.
MAX_LINES = 10
_FOLD = re.compile(r"[\s-]+")


@dataclass
class RefLookup:
    cards: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # One {id, number, debtor} per card, in card order; debtor is always None (a PO / PR has none).
    rows: list[dict[str, Any]] = field(default_factory=list)


def _fold(word: str) -> str:
    return _FOLD.sub("", word).lower()


def _db_fold(col):
    return func.lower(func.regexp_replace(col, r"[-\s]+", "", "g"))


def _qty(value: Any) -> str:
    d = Decimal(str(value if value is not None else 0)).normalize()
    return format(d, "f")


def _day(d: Any) -> str:
    return f"{d.day} {d:%b %Y}"


def _words(value: str | None) -> str:
    return (value or "").replace("_", " ").strip()


def _lines_text(lines: list[tuple[str, Any]]) -> list[str]:
    out = [f"{name} x {_qty(qty)}" for name, qty in lines[:MAX_LINES]]
    if len(lines) > MAX_LINES:
        out.append(f"and {len(lines) - MAX_LINES} more lines")
    return out


def lookup_purchase_orders(db: Session, words: Iterable[str]) -> RefLookup:
    typed = [w.strip() for w in words if isinstance(w, str) and _fold(w)]
    out = RefLookup()
    if not typed:
        return out
    keys = {_fold(w) for w in typed}
    rows = (
        db.query(PurchaseOrder, Supplier.supplier_name)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .filter(_db_fold(PurchaseOrder.po_number).in_(keys))
        .order_by(PurchaseOrder.po_number)
        .all()
    )
    by_key: dict[str, list[tuple[PurchaseOrder, str | None]]] = {}
    for po, supplier in rows:
        by_key.setdefault(_fold(po.po_number), []).append((po, supplier))
    seen: set[str] = set()
    for word in typed:
        key = _fold(word)
        if key in seen:
            continue
        seen.add(key)
        found = by_key.get(key) or []
        if not found:
            out.missing.append(word)
        for po, supplier in found:
            lines = [
                (code or "", qty)
                for code, qty in db.query(Product.product_code, PurchaseOrderLine.qty_ordered)
                .join(Product, Product.id == PurchaseOrderLine.product_id)
                .filter(PurchaseOrderLine.purchase_order_id == po.id)
                .order_by(Product.product_code)
                .all()
            ]
            head = f"*Purchase order {po.po_number}*" + (f" - {supplier}" if supplier else "")
            status = _words(po.status).capitalize() or "Unknown"
            second = f"Issued {_day(po.issue_date)} - {status}" if po.issue_date else status
            out.cards.append("\n".join([head, second, *_lines_text(lines)]))
            out.rows.append({"id": str(po.id), "number": po.po_number, "debtor": None})
    return out


def lookup_purchase_requests(db: Session, words: Iterable[str]) -> RefLookup:
    typed = [w.strip() for w in words if isinstance(w, str) and _fold(w)]
    out = RefLookup()
    if not typed:
        return out
    keys = {_fold(w) for w in typed}
    rows = (
        db.query(PurchaseRequestHeader)
        .filter(_db_fold(PurchaseRequestHeader.request_number).in_(keys))
        .order_by(PurchaseRequestHeader.request_date.desc().nullslast(), PurchaseRequestHeader.created_at.desc())
        .all()
    )
    by_key: dict[str, list[PurchaseRequestHeader]] = {}
    for pr in rows:
        by_key.setdefault(_fold(pr.request_number), []).append(pr)
    seen: set[str] = set()
    for word in typed:
        key = _fold(word)
        if key in seen:
            continue
        seen.add(key)
        found = by_key.get(key) or []
        if not found:
            out.missing.append(word)
        for pr in found:
            lines = [
                (item or "", qty)
                for item, qty in db.query(PurchaseRequestLine.item_code, PurchaseRequestLine.quantity)
                .filter(PurchaseRequestLine.purchase_request_id == pr.id)
                .order_by(PurchaseRequestLine.sort_order)
                .all()
            ]
            head = f"*Purchase request {pr.request_number}*" + (f" - {pr.customer_name}" if pr.customer_name else "")
            status = _words(pr.status).capitalize() or "Unknown"
            if pr.approval_status:
                status += f" - {_words(pr.approval_status)}"
            second = f"Requested {_day(pr.request_date)} - {status}" if pr.request_date else status
            out.cards.append("\n".join([head, second, *_lines_text(lines)]))
            out.rows.append({"id": str(pr.id), "number": pr.request_number, "debtor": None})
    return out
