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
    #: One entry per card whose lines were cut at `MAX_LINES`, in card order (CHAT-COMPOSER Lists L3).
    cut: list[dict[str, Any]] = field(default_factory=list)


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


def _po_lines(db: Session, po_id: Any) -> list[tuple[str, Any]]:
    """A PO's lines, by product code then line id: one query for the card and every later page, so
    the pages never overlap or skip."""
    return [
        (code or "", qty)
        for code, qty in db.query(Product.product_code, PurchaseOrderLine.qty_ordered)
        .join(Product, Product.id == PurchaseOrderLine.product_id)
        .filter(PurchaseOrderLine.purchase_order_id == po_id)
        .order_by(Product.product_code, PurchaseOrderLine.id)
        .all()
    ]


def _pr_lines(db: Session, pr_id: Any) -> list[tuple[str, Any]]:
    return [
        (item or "", qty)
        for item, qty in db.query(PurchaseRequestLine.item_code, PurchaseRequestLine.quantity)
        .filter(PurchaseRequestLine.purchase_request_id == pr_id)
        .order_by(PurchaseRequestLine.sort_order, PurchaseRequestLine.id)
        .all()
    ]


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
            lines = _po_lines(db, po.id)
            head = f"*Purchase order {po.po_number}*" + (f" - {supplier}" if supplier else "")
            status = _words(po.status).capitalize() or "Unknown"
            second = f"Issued {_day(po.issue_date)} - {status}" if po.issue_date else status
            out.cards.append("\n".join([head, second, *_lines_text(lines)]))
            if len(lines) > MAX_LINES:
                out.cut.append({"kind": "po", "id": po.id, "lines": len(lines)})
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
            lines = _pr_lines(db, pr.id)
            head = f"*Purchase request {pr.request_number}*" + (f" - {pr.customer_name}" if pr.customer_name else "")
            status = _words(pr.status).capitalize() or "Unknown"
            if pr.approval_status:
                status += f" - {_words(pr.approval_status)}"
            second = f"Requested {_day(pr.request_date)} - {status}" if pr.request_date else status
            out.cards.append("\n".join([head, second, *_lines_text(lines)]))
            if len(lines) > MAX_LINES:
                out.cut.append({"kind": "pr", "id": pr.id, "lines": len(lines)})
    return out


def card_page(db: Session, ref: dict[str, Any], offset: int) -> str | None:
    """CHAT-COMPOSER Lists L3: the next `MAX_LINES` lines of a cut card, re-read by id (None when the
    row is gone). Head and range, then the lines; past the last line, the end sentence."""
    if ref.get("kind") == "po":
        row = db.query(PurchaseOrder).filter(PurchaseOrder.id == ref.get("id")).first()
        if row is None:
            return None
        supplier = db.query(Supplier.supplier_name).filter(Supplier.id == row.supplier_id).scalar()
        head = f"*Purchase order {row.po_number}*" + (f" - {supplier}" if supplier else "")
        lines = _po_lines(db, row.id)
    else:
        row = db.query(PurchaseRequestHeader).filter(PurchaseRequestHeader.id == ref.get("id")).first()
        if row is None:
            return None
        head = f"*Purchase request {row.request_number}*" + (f" - {row.customer_name}" if row.customer_name else "")
        lines = _pr_lines(db, row.id)
    total = len(lines)
    if offset >= total:
        return f"{head} ({total:,}). That is all {total:,}."
    page = lines[offset : offset + MAX_LINES]
    out = [f"{head} ({total:,}, showing {offset + 1} to {offset + len(page)})"]
    out += [f"{name} x {_qty(qty)}" for name, qty in page]
    if offset + len(page) < total:
        out.append(f"and {total - offset - len(page)} more lines")
    return "\n".join(out)


def next_ref(carry: dict[str, Any]) -> dict[str, Any]:
    """The cursor after the page the carry names: the same card ten lines on, else the next cut card
    from its line `MAX_LINES` (its first page already printed), else unchanged at the end."""
    cards = carry.get("ref_cards") or []
    card, shown, total = int(carry.get("card") or 0), int(carry.get("shown") or 0), int(carry.get("total") or 0)
    if shown < total:
        return {**carry, "shown": min(shown + MAX_LINES, total)}
    if card + 1 < len(cards):
        lines = int(cards[card + 1].get("lines") or 0)
        return {**carry, "card": card + 1, "shown": min(MAX_LINES, lines), "total": lines}
    return carry
