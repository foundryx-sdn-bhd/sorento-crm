"""AutoCount Consignment (CG) and Consignment Return on the CRM side (AC-CONSIGN-CRM).

Plan: documentation/plans/autocount/PLAN-autocount-consign-crm-14oct.md. The documents arrive
on #1623's `consignments` / `consignment_returns` doors and are kept in `autocount_cancel_docs`
/ `_lines` exactly as sent; this module is what they then mean.

**A consignment is demand (owner ruling 1, 13 Oct).** `consignment_payload` turns the STORED
record into a canonical sales order, which the consignments ingest writes through the SO ingest
itself (`DocumentIngestService`, `sales_doc_type = consignment`): customer ladder, demand class
and adoption (Q3 a), line sync, status and the planning hooks are the sales order's own.

**A consignment's goods are already at the customer (CONSIGN-DELIVERED-FIX, owner 14 Oct).**
A CG is a stock transfer to the CON location, so each line is delivered in full at issue
(delivered = Qty) and the order is closed: never open demand. TransferedQty on a CG line is
the qty already RETURNED (Consignment Return, incl. 'CVT TO DO'), so it is `qty_returned`,
never delivered. A voided or vanished CG is a cancelled order.

**A return is reporting only (ruling 2, Q4 a).** Each return line is matched to ONE consignment
line or to none, with a reason: its FromDocDtlKey names the line exactly; without a key, the
(CG, item, location) group's one line; anything else is undetermined and names no line (ruling
3: never a guess). `recompute_returned` sets `qty_returned` to the sum of matched lines of live
return docs or the CG's own TransferedQty, whichever is larger, capped at ordered. Nothing else
on the line moves.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from sqlalchemy import String, func, or_
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from app.services.autocount_doc_ingest_service import _int
from app.models.autocount_cancel import (
    KIND_CONSIGNMENT,
    KIND_CONSIGNMENT_RETURN,
    MATCH_MATCHED,
    MATCH_UNMATCHED,
    MATCH_WAITING,
    AutocountCancelDoc,
    AutocountCancelLine,
)
from app.models.inventory import Warehouse
from app.models.order import DOC_TYPE_CONSIGNMENT, SalesOrder, SalesOrderLine
from app.models.product import Product

CG_PREFIX = "CSGN"

# Why a return line matched no consignment line. Shared words with the cancel matcher where
# the meaning is the same (`line_key`, `item`, `dangling_line_key`, `order_not_found`, ...).
REASON_LINE_KEY = "line_key"
REASON_ITEM = "item"
REASON_UNDETERMINED = "undetermined"
REASON_ITEM_NOT_ON_ORDER = "item_not_on_order"
REASON_DANGLING_LINE_KEY = "dangling_line_key"
REASON_ORDER_NOT_FOUND = "order_not_found"
REASON_NO_SOURCE_DOC = "no_source_doc"
REASON_LINE_WITHOUT_ITEM = "line_without_item"
REASON_AMBIGUOUS = "ambiguous"
# A return line raised from something other than a consignment (FromDocType not 'CG').
REASON_NOT_FROM_CONSIGNMENT = "not_from_consignment"
REASON_GROUP_WHOLE = "group_whole"

_ZERO = Decimal("0")


# ============================================================================ CG -> sales order
def consignment_source_ref(book: str, doc_key: Any) -> str:
    return f"{book}:{CG_PREFIX}:{doc_key}"


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> Any:
    """A quantity or amount as sent, or None when absent or blank. A value that is not a number
    is passed through so the SO ingest's own validation names the field."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value


def carries_transfers(doc: AutocountCancelDoc) -> bool:
    """Crew gate, 14 Oct: a CG becomes an order only once its record states TransferedQty on
    every item line (contract 2.9), the returned qty the order carries; a 2.8 record is kept
    stored only until a push that carries it."""
    details = [d for d in ((doc.source_record or {}).get("Details") or [])
               if isinstance(d, dict) and _text(d.get("ItemCode"))]
    return bool(details) and all(d.get("TransferedQty") is not None for d in details)


def consignment_payload(book: str, doc: AutocountCancelDoc) -> dict[str, Any]:
    """The canonical sales order a stored CG document stands for.

    Built from the STORED record (the newest the CRM accepted, the LastModified guard already
    applied), never from a stale push. A line without an ItemCode is a text line, not demand,
    and is left out. Contract 2.9 fields (DebtorCode, DebtorName, SalesAgent; per line
    UnitPrice, SubTotal, UOM) are taken when sent; absent, the order lands with no customer,
    and the next push that carries them fills them in. Every line is delivered in full: the
    goods left with the CG (CONSIGN-DELIVERED-FIX). TransferedQty is the returned qty and is
    folded into `qty_returned` by `recompute_returned`, not sent to the SO ingest."""
    raw = doc.source_record or {}
    lines: list[dict[str, Any]] = []
    for detail in raw.get("Details") or []:
        if not isinstance(detail, dict):
            continue
        item = _text(detail.get("ItemCode"))
        # The integer key, so "012", 12.0 and 12 are one line (AC-CONSIGN-CRM).
        dtl_key = _int(detail.get("DtlKey"))
        if not item or dtl_key is None:
            continue
        qty = _number(detail.get("Qty")) or _ZERO
        line: dict[str, Any] = {
            "source_ref": f"{consignment_source_ref(book, doc.doc_key)}:{dtl_key}",
            "product_code": item,
            "qty_ordered": qty,
            "qty_delivered": qty,
        }
        seq = detail.get("Seq")
        if isinstance(seq, int) and seq >= 0:
            line["line_number"] = seq
        location = _text(detail.get("Location"))
        if location:
            line["warehouse_code"] = location
        for source, target in (("UnitPrice", "unit_price"), ("SubTotal", "line_total")):
            value = _number(detail.get(source))
            if value is not None:
                line[target] = value
        uom = _text(detail.get("UOM"))
        if uom:
            line["uom"] = uom
        lines.append(line)

    payload: dict[str, Any] = {
        "source_ref": consignment_source_ref(book, doc.doc_key),
        "so_number": doc.doc_no,
        "doc_date": doc.doc_date.isoformat() if doc.doc_date else None,
        "status": _status(doc, lines),
        "lines": lines,
    }
    for source, target in (("DebtorCode", "customer_code"), ("DebtorName", "customer_name"),
                           ("SalesAgent", "agent_code")):
        value = _text(raw.get(source))
        if value:
            payload[target] = value
    return payload


def _status(doc: AutocountCancelDoc, lines: list[dict[str, Any]]) -> str:
    """Stated, never left to the SO ingest's derivation, which keeps a cancelled order
    cancelled: an un-voided or restored CG has to come back. A live CG is `closed`: its goods
    are at the customer, so it is never open demand (CONSIGN-DELIVERED-FIX)."""
    if doc.is_void or doc.source_vanished_at is not None:
        return "cancelled"
    return "closed"


# ============================================================================ returns
def _loc(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _ref_matches(column, dtl_key: int):
    return or_(column == str(dtl_key), column.like(f"%:{dtl_key}"))


def _consignment(db: Session, company_id: str, number: Optional[str]) -> Optional[str]:
    if not number:
        return None
    rows = (db.query(SalesOrder.id)
            .filter(SalesOrder.company_id == company_id, SalesOrder.so_number == number,
                    SalesOrder.doc_type == DOC_TYPE_CONSIGNMENT)
            .limit(2).all())
    return str(rows[0][0]) if len(rows) == 1 else None


@dataclass(frozen=True)
class ReturnGroup:
    """One consignment + item + location: what a key-less return line is decided with."""
    header_id: str
    number: str
    item_code: str
    location: str  # normalised (`_loc`)


def return_line_answer(db: Session, company_id: str, line: AutocountCancelLine
                       ) -> tuple[Optional[tuple[str, str, Optional[str]]], Optional[ReturnGroup]]:
    """For one return line of a live doc: either a final (status, reason, line id), or the
    group it is decided with (a key-less line naming a consignment the CRM holds)."""
    if line.from_doc_type and line.from_doc_type.strip().upper() != "CG":
        return (MATCH_UNMATCHED, REASON_NOT_FROM_CONSIGNMENT, None), None
    if line.from_dtl_key is not None:
        query = (db.query(SalesOrderLine.id)
                 .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
                 .filter(SalesOrderLine.company_id == company_id,
                         SalesOrder.doc_type == DOC_TYPE_CONSIGNMENT,
                         _ref_matches(SalesOrderLine.source_ref, line.from_dtl_key)))
        # A keyed line names a line of its OWN book only (the key is per book).
        book = (db.query(AutocountCancelDoc.source_book)
                .filter(AutocountCancelDoc.id == line.cancel_doc_id).scalar())
        if book:
            query = query.filter(SalesOrderLine.source_ref.startswith(f"{book}:{CG_PREFIX}:", autoescape=True))
        if line.from_doc_no:
            query = query.filter(SalesOrder.so_number == line.from_doc_no)
        hits = [str(r[0]) for r in query.limit(2).all()]
        if len(hits) == 1:
            return (MATCH_MATCHED, REASON_LINE_KEY, hits[0]), None
        if hits:
            return (MATCH_UNMATCHED, REASON_AMBIGUOUS, None), None
        if _consignment(db, company_id, line.from_doc_no):
            return (MATCH_UNMATCHED, REASON_DANGLING_LINE_KEY, None), None
        return (MATCH_WAITING, REASON_ORDER_NOT_FOUND, None), None
    if not line.from_doc_no:
        return (MATCH_UNMATCHED, REASON_NO_SOURCE_DOC, None), None
    header = _consignment(db, company_id, line.from_doc_no)
    if header is None:
        return (MATCH_WAITING, REASON_ORDER_NOT_FOUND, None), None
    if not line.item_code:
        return (MATCH_UNMATCHED, REASON_LINE_WITHOUT_ITEM, None), None
    return None, ReturnGroup(header, line.from_doc_no, line.item_code, _loc(line.location_code))


def group_of_line(db: Session, so_line_id: str, company_id: Optional[str] = None
                  ) -> Optional[ReturnGroup]:
    """The group a consignment line sits in (to re-decide the group a return line LEFT)."""
    query = (db.query(SalesOrder.id, SalesOrder.so_number, Product.product_code,
                      Warehouse.warehouse_code)
             .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
             .join(Product, Product.id == SalesOrderLine.product_id)
             .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
             .filter(SalesOrderLine.id == so_line_id,
                     SalesOrder.doc_type == DOC_TYPE_CONSIGNMENT))
    if company_id:
        query = query.filter(SalesOrderLine.company_id == company_id)
    row = query.first()
    if row is None:
        return None
    return ReturnGroup(str(row[0]), row[1], row[2], _loc(row[3]))


def _live_return():
    return (AutocountCancelDoc.kind == KIND_CONSIGNMENT_RETURN,
            AutocountCancelDoc.is_void.is_(False),
            AutocountCancelDoc.source_vanished_at.is_(None))


def resolve_return_group(db: Session, company_id: str, group: ReturnGroup
                         ) -> list[tuple[AutocountCancelLine, str, str, Optional[str],
                                         Optional[list[str]]]]:
    """Every key-less live return line of the group, decided together by exact arithmetic
    (the #1623 rule, owner 13 Oct): the group's one line; or, when the returns are every unit
    still out on the group (C = sum of ordered less keyed returns), each line's own remainder
    (`group_whole`, the lines named as candidates, no single line); else `undetermined`."""
    rows = (db.query(SalesOrderLine.id, Warehouse.warehouse_code, SalesOrderLine.qty_ordered)
            .join(Product, Product.id == SalesOrderLine.product_id)
            .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
            .filter(SalesOrderLine.sales_order_id == group.header_id,
                    SalesOrderLine.company_id == company_id,
                    Product.product_code == group.item_code).all())
    rows = [r for r in rows if _loc(r[1]) == group.location]
    ids = sorted(str(r[0]) for r in rows)
    members = [
        l for l in (db.query(AutocountCancelLine)
                    .join(AutocountCancelDoc,
                          AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
                    .filter(AutocountCancelLine.company_id == company_id, *_live_return(),
                            AutocountCancelLine.from_dtl_key.is_(None),
                            AutocountCancelLine.from_doc_no == group.number,
                            AutocountCancelLine.item_code == group.item_code)
                    .all())
        if _loc(l.location_code) == group.location
        and (not l.from_doc_type or l.from_doc_type.strip().upper() == "CG")
    ]
    if not ids:
        return [(l, MATCH_UNMATCHED, REASON_ITEM_NOT_ON_ORDER, None, None) for l in members]
    if len(ids) == 1:
        return [(l, MATCH_MATCHED, REASON_ITEM, ids[0], None) for l in members]
    returned = sum((Decimal(l.qty or 0) for l in members), _ZERO)
    keyed = _matched_returns(db, ids, keyed_only=True, company_id=company_id)
    remaining = sum((max(Decimal(r[2] or 0) - keyed.get(str(r[0]), _ZERO), _ZERO) for r in rows),
                    _ZERO)
    if returned > 0 and returned == remaining:
        return [(l, MATCH_MATCHED, REASON_GROUP_WHOLE, None, ids) for l in members]
    return [(l, MATCH_UNMATCHED, REASON_UNDETERMINED, None, None) for l in members]


def _matched_returns(db: Session, ids: list[str], *, keyed_only: bool,
                     company_id: Optional[str] = None) -> dict[str, Decimal]:
    """Per consignment line, the qty of live return lines matched to it (by key only, or all)."""
    if not ids:
        return {}
    extra = (AutocountCancelLine.from_dtl_key.isnot(None),) if keyed_only else ()
    if company_id:
        extra += (AutocountCancelLine.company_id == company_id,)
    return {
        str(k): Decimal(v) for k, v in (
            db.query(AutocountCancelLine.sales_order_line_id,
                     func.coalesce(func.sum(AutocountCancelLine.qty), 0))
            .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
            .filter(AutocountCancelLine.sales_order_line_id.in_(ids), *_live_return(),
                    AutocountCancelLine.match_status == MATCH_MATCHED, *extra)
            .group_by(AutocountCancelLine.sales_order_line_id).all())
    }


def _transferred(db: Session, rows: list[SalesOrderLine]) -> dict[str, Decimal]:
    """Per consignment line, the TransferedQty its stored CG record states (the qty AutoCount
    counts as returned), read off the line's `source_ref` `<book>:CSGN:<DocKey>:<DtlKey>`."""
    wanted: dict[tuple[str, int], dict[int, str]] = {}
    for row in rows:
        parts = (row.source_ref or "").rsplit(":", 3)
        if len(parts) != 4 or parts[1] != CG_PREFIX:
            continue
        doc_key, dtl_key = _int(parts[2]), _int(parts[3])
        if doc_key is None or dtl_key is None:
            continue
        wanted.setdefault((parts[0], doc_key), {})[dtl_key] = str(row.id)
    if not wanted:
        return {}
    out: dict[str, Decimal] = {}
    docs = (db.query(AutocountCancelDoc)
            .filter(AutocountCancelDoc.kind == KIND_CONSIGNMENT,
                    AutocountCancelDoc.company_id == rows[0].company_id,
                    AutocountCancelDoc.doc_key.in_(sorted({k for _, k in wanted})))
            .all())
    for doc in docs:
        lines = wanted.get((doc.source_book, doc.doc_key))
        if not lines:
            continue
        for detail in (doc.source_record or {}).get("Details") or []:
            if not isinstance(detail, dict):
                continue
            line_id = lines.get(_int(detail.get("DtlKey")))
            value = _number(detail.get("TransferedQty"))
            if line_id and isinstance(value, Decimal):
                out[line_id] = value
    return out


def recompute_returned(db: Session, so_line_ids: Iterable[str],
                       company_id: Optional[str] = None) -> None:
    """`qty_returned` on these lines: the matched lines of live return docs, or the CG's own
    TransferedQty (AutoCount's returned qty, CONSIGN-DELIVERED-FIX), whichever is larger,
    capped at ordered. The two are one fact pushed twice; the larger is the one that has
    landed. Only this column is written. In the caller's transaction."""
    ids = sorted({str(i) for i in so_line_ids if i})
    if not ids:
        return
    sums = _matched_returns(db, ids, keyed_only=False, company_id=company_id)
    whole = {
        str(i) for (members,) in (
            db.query(AutocountCancelLine.candidate_line_ids)
            .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
            .filter(AutocountCancelLine.match_status == MATCH_MATCHED,
                    AutocountCancelLine.match_reason == REASON_GROUP_WHOLE, *_live_return(),
                    *((AutocountCancelLine.company_id == company_id,) if company_id else ()),
                    AutocountCancelLine.candidate_line_ids.op("?|")(array(ids, type_=String)))
            .all())
        for i in (members or [])
    }
    rows_q = db.query(SalesOrderLine).filter(SalesOrderLine.id.in_(ids))
    if company_id:
        rows_q = rows_q.filter(SalesOrderLine.company_id == company_id)
    rows = rows_q.all()
    transferred = _transferred(db, rows)
    for row in rows:
        ordered = Decimal(row.qty_ordered or 0)
        # A whole-group return: every unit still out on the group came back, so the line's
        # returned qty is all of it (its keyed returns plus its own remainder).
        total = ordered if str(row.id) in whole else sums.get(str(row.id), _ZERO)
        total = max(total, transferred.get(str(row.id), _ZERO))
        value = max(min(total, ordered), _ZERO)
        if Decimal(row.qty_returned or 0) != value:
            row.qty_returned = value
    db.flush()


__all__ = [
    "CG_PREFIX",
    "carries_transfers",
    "consignment_payload",
    "consignment_source_ref",
    "ReturnGroup",
    "group_of_line",
    "recompute_returned",
    "resolve_return_group",
    "return_line_answer",
]
