"""Ingest AutoCount Cancel SO (XS) and Cancel PO (XP) documents (AC-CANCEL-CRM, contract 2.8).

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md (rules R1-R7). A sibling
of `AutocountDocIngestService` (same record shape, envelope `book`, identity (company, book,
DocKey), LastModified stale guard, one SAVEPOINT per record, dry-run rollback); its parse
helpers are reused, not copied.

**Cancelled qty is derived, never an event (owner, 13 Oct).** AutoCount adds a Cancel SO to the
SO line's TransferedQty, which the SO push stores as `qty_delivered`, so a cancelled unit reads as
delivered. This module keeps every cancel line with the SO / PO / SPO line it is matched to, and
after every batch sets each touched line's `qty_cancelled` to the sum of the matched lines of
LIVE cancel docs (not voided, not vanished), capped at ordered. A doc deleted or voided in
AutoCount simply drops out of the sum on the next sync.

**Match, or say why not (R2, R7; owner Q2 a / Q3, 13 Oct).** A line with a `FromDocDtlKey`
matches that exact line; a key naming a line that is not there while its document is stays
unmatched (`dangling_line_key`), never an item fallback. Without a key, the line is decided with
its whole group (one document + one item) by `autocount_cancel_matching.resolve_group`: one
candidate, the unexplained-transfer tie-break, then a deterministic lowest-Seq fallback that is
written to the matched line's history. No person reviews anything; what fits nowhere stays
unmatched and is counted in the sync log. No document yet is `waiting`, retried every batch and
when that order is pushed. The answer depends only on what is stored, never on arrival order.

**Status (R5, R6).** A fully cancelled SO / PO line reads `cancelled`. When the cancel lowers
again, a line this module had cancelled goes back to the status its own quantities give. A line
CS cancelled on the board (`cancelled_at`, #1524) or whose order is cancelled is never reopened.
SPO lines keep their own status rule (TransferedQty already counts the cancel as received).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.autocount_cancel import (
    KIND_CANCEL_PO,
    KIND_CANCEL_SO,
    MATCH_MATCHED,
    MATCH_UNMATCHED,
    MATCH_WAITING,
    AutocountCancelDoc,
    AutocountCancelLine,
)
from app.models.base import company_scope
from app.models.inventory import Warehouse
from app.models.order import Order, OrderLine, SalesOrder, SalesOrderLine
from app.models.procurement import (
    PickingHeader,
    PickingLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
)
from app.models.product import Product
from app.services.autocount_doc_ingest_service import (
    MAX_LINES,
    MAX_RECORD_BYTES,
    _date,
    _dec,
    _FOUR,
    _field,
    _int,
    _Invalid,
    _link_key,
    _modified,
    _safe_error,
    _text,
    _truthy,
)
from app.services.autocount_cancel_matching import (
    REASON_FALLBACK,
    CancelIn,
    CandidateIn,
    as_decimal,
    resolve_group,
)
from app.services.deletion_service import (
    DeletionOutcome,
    DeletionRecordResult,
    DeletionResult,
)
from app.services.master_ingest_service import (
    INTERNAL_ERROR_MESSAGE,
    IngestOutcome,
    IngestResult,
    RecordResult,
    UnsupportedIngestEntity,
    integrity_conflict_errors,
)

logger = logging.getLogger(__name__)

CANCEL_SALES_ORDERS_ENTITY = "cancel_sales_orders"
CANCEL_PURCHASE_ORDERS_ENTITY = "cancel_purchase_orders"
AUTOCOUNT_CANCEL_ENTITIES = frozenset({CANCEL_SALES_ORDERS_ENTITY, CANCEL_PURCHASE_ORDERS_ENTITY})

_KIND = {CANCEL_SALES_ORDERS_ENTITY: KIND_CANCEL_SO, CANCEL_PURCHASE_ORDERS_ENTITY: KIND_CANCEL_PO}
_PREFIX = {CANCEL_SALES_ORDERS_ENTITY: "XS", CANCEL_PURCHASE_ORDERS_ENTITY: "XP"}

# Why a cancel line matched nothing (R2, R7). `waiting` lines are retried every batch.
REASON_ORDER_NOT_FOUND = "order_not_found"
REASON_DANGLING_LINE_KEY = "dangling_line_key"
REASON_AMBIGUOUS = "ambiguous"
REASON_NO_SOURCE_DOC = "no_source_doc"
REASON_LINE_WITHOUT_ITEM = "line_without_item"

# Contract 2.8 warnings on a record's verdict.
WARN_CANCEL_LINE_WAITING = "cancel_line_waiting"
WARN_CANCEL_LINE_UNMATCHED = "cancel_line_unmatched"
CONTRACT_2_8_WARNINGS = (WARN_CANCEL_LINE_WAITING, WARN_CANCEL_LINE_UNMATCHED)

# Waiting lines re-tried per batch; the rest wait for the next batch.
MAX_WAITING_LINES = 500
CANCELLED = "cancelled"
_ZERO = Decimal("0")


def _source_ref(book: str, entity: str, doc_key: Any) -> Optional[str]:
    key = _int(doc_key)
    return f"{book}:{_PREFIX[entity]}:{key}" if key is not None else None


# ============================================================================ parsing
@dataclass
class _CancelLine:
    dtl_key: int
    seq: Optional[int]
    item_code: Optional[str]
    location_code: Optional[str]
    qty: Decimal
    from_doc_type: Optional[str]
    from_doc_no: Optional[str]
    from_dtl_key: Optional[int]


@dataclass
class _CancelDoc:
    raw: dict
    doc_key: int
    doc_no: str
    doc_date: date
    modified: Optional[datetime]
    void: bool
    lines: list[_CancelLine]


def _parse(raw: dict) -> _CancelDoc:
    errors: dict[str, str] = {}
    doc_key = _int(raw.get("DocKey"))
    if doc_key is None:
        errors["DocKey"] = "required, an integer"
    doc_no = _text(raw.get("DocNo"))
    if doc_no is None:
        errors["DocNo"] = "required"
    elif len(doc_no) > 50:
        errors["DocNo"] = "longer than 50 characters"
    doc_date = _field(errors, "DocDate", _date, raw.get("DocDate"))
    if doc_date is None and "DocDate" not in errors:
        errors["DocDate"] = "required"
    modified = _field(errors, "LastModified", _modified, raw.get("LastModified"))

    details = raw.get("Details")
    if details is None:
        details = []
    lines: list[_CancelLine] = []
    if not isinstance(details, list):
        errors["Details"] = "must be an array"
    elif len(details) > MAX_LINES:
        errors["Details"] = f"more than {MAX_LINES} lines"
    else:
        seen: set[int] = set()
        for index, row in enumerate(details):
            prefix = f"Details.{index}"
            if not isinstance(row, dict):
                errors[prefix] = "must be an object"
                continue
            dtl_key = _int(row.get("DtlKey"))
            if dtl_key is None:
                errors[f"{prefix}.DtlKey"] = "required, an integer"
                continue
            if dtl_key in seen:
                errors["Details"] = f"DtlKey {dtl_key} appears twice"
            seen.add(dtl_key)
            qty = _field(errors, f"{prefix}.Qty", lambda v: _dec(v, _FOUR), row.get("Qty"))
            if qty is not None and qty < 0:
                errors[f"{prefix}.Qty"] = "must not be negative"
            lines.append(
                _CancelLine(
                    dtl_key=dtl_key,
                    seq=_int(row.get("Seq")),
                    item_code=_text(row.get("ItemCode"), 100),
                    location_code=_text(row.get("Location"), 50),
                    qty=qty if qty is not None else Decimal("0.0000"),
                    from_doc_type=_text(row.get("FromDocType"), 10),
                    from_doc_no=_text(row.get("FromDocNo"), 100),
                    from_dtl_key=_link_key(row.get("FromDocDtlKey")),
                )
            )
    if errors:
        raise _Invalid(errors)
    return _CancelDoc(raw=raw, doc_key=doc_key, doc_no=doc_no, doc_date=doc_date,
                      modified=modified, void=_truthy(raw.get("Cancelled")), lines=lines)


_LINE_FIELDS = ("seq", "item_code", "location_code", "qty", "from_doc_type", "from_doc_no",
                "from_dtl_key")
# Changing any of these can change what the line matches.
_MATCH_FIELDS = ("item_code", "location_code", "from_doc_no", "from_dtl_key")


# ============================================================================ matching
@dataclass
class _Match:
    status: str
    reason: Optional[str] = None
    so_line_id: Optional[str] = None
    po_line_id: Optional[str] = None
    spo_id: Optional[str] = None
    candidates: Optional[list[str]] = None


_TARGET_FIELD = {"so": "so_line_id", "po": "po_line_id", "spo": "spo_id"}
_REASON_LINE_KEY = "line_key"
REASON_DOC_NOT_LIVE = "doc_not_live"


def _ref_matches(column, dtl_key: int):
    return or_(column == str(dtl_key), column.like(f"%:{dtl_key}"))


def _dtl_of(source_ref: Optional[str]) -> int:
    tail = str(source_ref or "").rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 2**63 - 1


def _live_doc():
    return and_(AutocountCancelDoc.is_void.is_(False), AutocountCancelDoc.source_vanished_at.is_(None))


@dataclass(frozen=True)
class _Group:
    """One document + one item: the unit Q2 a and the fallback are decided over."""
    kind: str  # cancel_so / cancel_po
    target: str  # so / po / spo
    doc: str  # SO / PO header id, or the SPO match key
    number: str  # the number the cancel lines name
    item_code: str


class CancelLineMatcher:
    """R2 from what the CRM holds now. A key-less line is decided with its whole group
    (`autocount_cancel_matching.resolve_group`), so nothing depends on arrival order."""

    def __init__(self, db: Session, company_id: str):
        self.db = db
        self.company_id = company_id
        self.counts: dict[str, int] = {}
        self.fallbacks: list[tuple[AutocountCancelLine, str]] = []

    # ------------------------------------------------------------- documents
    def _so_header(self, number: Optional[str]) -> Optional[str]:
        if not number:
            return None
        rows = (self.db.query(SalesOrder.id)
                .filter(SalesOrder.company_id == self.company_id, SalesOrder.so_number == number)
                .limit(2).all())
        return str(rows[0][0]) if len(rows) == 1 else None

    def _po_header(self, number: Optional[str]) -> Optional[str]:
        if not number:
            return None
        rows = (self.db.query(PurchaseOrder.id)
                .filter(PurchaseOrder.company_id == self.company_id,
                        PurchaseOrder.po_number == number)
                .limit(2).all())
        return str(rows[0][0]) if len(rows) == 1 else None

    def _document(self, kind: str, number: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """(target kind, document identity) for the number a cancel line names, or (None, None).
        For a Cancel PO the number's table decides, SPO first (as the GRN ingest does): PO and
        SPO share one AutoCount table and FromDocType reads 'PO' on both."""
        if not number:
            return None, None
        if kind == KIND_CANCEL_SO:
            header = self._so_header(number)
            return ("so", header) if header else (None, None)
        from app.services.grn_spo_matching import _spo_match_key, spo_number_known

        if spo_number_known(self.db, number, company_id=self.company_id):
            return "spo", _spo_match_key(number)
        header = self._po_header(number)
        return ("po", header) if header else (None, None)

    # ------------------------------------------------------------- keyed lines
    def _by_key(self, kind: str, line: AutocountCancelLine) -> _Match:
        if kind == KIND_CANCEL_SO:
            query = (self.db.query(SalesOrderLine.id)
                     .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
                     .filter(SalesOrderLine.company_id == self.company_id,
                             _ref_matches(SalesOrderLine.source_ref, line.from_dtl_key)))
            if line.from_doc_no:
                query = query.filter(SalesOrder.so_number == line.from_doc_no)
            hits = [("so", str(r[0])) for r in query.limit(2).all()]
        else:
            po_query = (self.db.query(PurchaseOrderLine.id)
                        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
                        .filter(PurchaseOrderLine.company_id == self.company_id,
                                _ref_matches(PurchaseOrderLine.source_ref, line.from_dtl_key)))
            spo_query = self.db.query(SPOAllocation.id).filter(
                SPOAllocation.company_id == self.company_id,
                _ref_matches(SPOAllocation.source_ref, line.from_dtl_key))
            if line.from_doc_no:
                po_query = po_query.filter(PurchaseOrder.po_number == line.from_doc_no)
                spo_query = spo_query.filter(SPOAllocation.spo_number == line.from_doc_no)
            hits = ([("po", str(r[0])) for r in po_query.limit(2).all()]
                    + [("spo", str(r[0])) for r in spo_query.limit(2).all()])
        if len(hits) == 1:
            target, target_id = hits[0]
            return _Match(MATCH_MATCHED, _REASON_LINE_KEY, **{_TARGET_FIELD[target]: target_id})
        if len(hits) > 1:
            return _Match(MATCH_UNMATCHED, REASON_AMBIGUOUS, candidates=sorted(h[1] for h in hits))
        if self._document(kind, line.from_doc_no)[0] is not None:
            return _Match(MATCH_UNMATCHED, REASON_DANGLING_LINE_KEY)
        return _Match(MATCH_WAITING, REASON_ORDER_NOT_FOUND)

    # ------------------------------------------------------------- groups
    def _candidates(self, group: _Group) -> list[CandidateIn]:
        """The group's lines with their unexplained transfer: TransferedQty - DO / GRN qty linked
        to the line - cancel qty matched to it BY LINE KEY (key-less matches are what is being
        decided, so they never feed it)."""
        if group.target == "so":
            rows = (self.db.query(SalesOrderLine.id, SalesOrderLine.line_no,
                                  SalesOrderLine.source_ref, Warehouse.warehouse_code,
                                  SalesOrderLine.qty_delivered)
                    .join(Product, Product.id == SalesOrderLine.product_id)
                    .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
                    .filter(SalesOrderLine.sales_order_id == group.doc,
                            Product.product_code == group.item_code).all())
            ids = [str(r[0]) for r in rows]
            moved = self._sums(
                self.db.query(OrderLine.sales_order_line_id, func.sum(OrderLine.quantity))
                .join(Order, Order.id == OrderLine.order_id)
                .filter(OrderLine.sales_order_line_id.in_(ids), Order.is_cancelled.is_(False))
                .group_by(OrderLine.sales_order_line_id))
            keyed = self._keyed(AutocountCancelLine.sales_order_line_id, ids)
            return [CandidateIn(str(r[0]), (r[1] if r[1] is not None else 2**31, _dtl_of(r[2])),
                                r[3], as_decimal(r[4]) - moved.get(str(r[0]), _ZERO)
                                - keyed.get(str(r[0]), _ZERO)) for r in rows]
        if group.target == "po":
            rows = (self.db.query(PurchaseOrderLine.id, PurchaseOrderLine.source_ref,
                                  Warehouse.warehouse_code, PurchaseOrderLine.qty_received)
                    .join(Product, Product.id == PurchaseOrderLine.product_id)
                    .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
                    .filter(PurchaseOrderLine.purchase_order_id == group.doc,
                            Product.product_code == group.item_code).all())
            ids = [str(r[0]) for r in rows]
            moved = self._grn(PickingLine.po_line_id, ids)
            keyed = self._keyed(AutocountCancelLine.purchase_order_line_id, ids)
            return [CandidateIn(str(r[0]), (_dtl_of(r[1]),), r[2],
                                as_decimal(r[3]) - moved.get(str(r[0]), _ZERO)
                                - keyed.get(str(r[0]), _ZERO)) for r in rows]
        from app.services.grn_spo_matching import _spo_match_key_sql

        rows = (self.db.query(SPOAllocation.id, SPOAllocation.spo_line_number,
                              SPOAllocation.source_ref,
                              func.coalesce(Warehouse.warehouse_code, SPOAllocation.location_code),
                              SPOAllocation.stated_received)
                .join(Product, Product.id == SPOAllocation.product_id)
                .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
                .filter(SPOAllocation.company_id == self.company_id,
                        _spo_match_key_sql(SPOAllocation.spo_number) == group.doc,
                        Product.product_code == group.item_code).all())
        ids = [str(r[0]) for r in rows]
        moved = self._grn(PickingLine.spo_allocation_id, ids)
        keyed = self._keyed(AutocountCancelLine.spo_allocation_id, ids)
        return [CandidateIn(str(r[0]), (r[1] if r[1] is not None else 2**31, _dtl_of(r[2])),
                            r[3], as_decimal(r[4]) - moved.get(str(r[0]), _ZERO)
                            - keyed.get(str(r[0]), _ZERO)) for r in rows]

    @staticmethod
    def _sums(query) -> dict[str, Decimal]:
        return {str(k): as_decimal(v) for k, v in query.all() if k is not None}

    def _grn(self, column, ids: list[str]) -> dict[str, Decimal]:
        if not ids:
            return {}
        return self._sums(
            self.db.query(column, func.sum(PickingLine.quantity_picked))
            .join(PickingHeader, PickingHeader.id == PickingLine.picking_header_id)
            .filter(column.in_(ids), PickingHeader.is_cancelled.is_(False))
            .group_by(column))

    def _keyed(self, column, ids: list[str]) -> dict[str, Decimal]:
        if not ids:
            return {}
        return self._sums(
            self.db.query(column, func.sum(AutocountCancelLine.qty))
            .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
            .filter(column.in_(ids), AutocountCancelLine.match_status == MATCH_MATCHED,
                    AutocountCancelLine.from_dtl_key.isnot(None), _live_doc())
            .group_by(column))

    def _group_lines(self, group: _Group) -> list[tuple[AutocountCancelLine, AutocountCancelDoc]]:
        """Every key-less cancel line of a LIVE doc naming this document and item."""
        query = (self.db.query(AutocountCancelLine, AutocountCancelDoc)
                 .join(AutocountCancelDoc,
                       AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
                 .filter(AutocountCancelLine.company_id == self.company_id,
                         AutocountCancelDoc.kind == group.kind,
                         AutocountCancelLine.from_dtl_key.is_(None),
                         AutocountCancelLine.item_code == group.item_code,
                         _live_doc()))
        if group.target == "spo":
            from app.services.grn_spo_matching import _spo_match_key_sql

            query = query.filter(_spo_match_key_sql(AutocountCancelLine.from_doc_no) == group.doc)
        else:
            query = query.filter(AutocountCancelLine.from_doc_no == group.number)
        return query.all()

    def _resolve(self, group: _Group) -> dict[str, tuple[AutocountCancelLine, _Match, str]]:
        members = self._group_lines(group)
        outcomes = resolve_group(
            [CancelIn(str(l.id), as_decimal(l.qty), l.location_code,
                      (d.doc_date, d.doc_no, l.dtl_key)) for l, d in members],
            self._candidates(group),
        )
        out = {}
        for line, doc in members:
            o = outcomes[str(line.id)]
            if o.matched:
                match = _Match(MATCH_MATCHED, o.reason, candidates=list(o.candidates) or None,
                               **{_TARGET_FIELD[group.target]: o.target_id})
            else:
                match = _Match(MATCH_UNMATCHED, o.reason, candidates=list(o.candidates) or None)
            out[str(line.id)] = (line, match, doc.doc_no)
        return out

    def _target_group(self, kind: str, target: str, target_id: str) -> Optional[_Group]:
        """The group a line-key match lands in: its document and item (keyed matches change
        the unexplained transfer the group is decided on)."""
        if target == "so":
            row = (self.db.query(SalesOrder.id, SalesOrder.so_number, Product.product_code)
                   .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
                   .join(Product, Product.id == SalesOrderLine.product_id)
                   .filter(SalesOrderLine.id == target_id).first())
            return _Group(kind, "so", str(row[0]), row[1], row[2]) if row else None
        if target == "po":
            row = (self.db.query(PurchaseOrder.id, PurchaseOrder.po_number, Product.product_code)
                   .join(PurchaseOrderLine, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
                   .join(Product, Product.id == PurchaseOrderLine.product_id)
                   .filter(PurchaseOrderLine.id == target_id).first())
            return _Group(kind, "po", str(row[0]), row[1], row[2]) if row else None
        from app.services.grn_spo_matching import _spo_match_key

        row = (self.db.query(SPOAllocation.spo_number, Product.product_code)
               .join(Product, Product.id == SPOAllocation.product_id)
               .filter(SPOAllocation.id == target_id).first())
        if not row or not row[0]:
            return None
        return _Group(kind, "spo", _spo_match_key(row[0]), row[0], row[1])

    # ------------------------------------------------------------- the pass
    def match_all(self, lines: Iterable[AutocountCancelLine], kinds: dict[str, str],
                  live: dict[str, bool]) -> dict[str, tuple[AutocountCancelLine, _Match, str]]:
        """The answer for every line asked about, plus every other key-less line sharing a group
        with them (a group is decided whole). Keyed by line id."""
        answers: dict[str, tuple[AutocountCancelLine, _Match, str]] = {}
        groups: set[_Group] = set()
        for line in lines:
            kind = kinds[str(line.cancel_doc_id)]
            # The group the line USED to count in is decided again too: leaving it frees what
            # the line had taken there.
            for target, column in (("so", "sales_order_line_id"), ("po", "purchase_order_line_id"),
                                   ("spo", "spo_allocation_id")):
                if getattr(line, column):
                    group = self._target_group(kind, target, str(getattr(line, column)))
                    if group:
                        groups.add(group)
            if not live.get(str(line.cancel_doc_id), True):
                answers[str(line.id)] = (line, _Match(MATCH_UNMATCHED, REASON_DOC_NOT_LIVE), "")
                continue
            if line.from_dtl_key is not None:
                match = self._by_key(kind, line)
                answers[str(line.id)] = (line, match, "")
                for target, field_name in _TARGET_FIELD.items():
                    target_id = getattr(match, field_name)
                    if target_id:
                        group = self._target_group(kind, target, target_id)
                        if group:
                            groups.add(group)
                continue
            if not line.from_doc_no:
                answers[str(line.id)] = (line, _Match(MATCH_UNMATCHED, REASON_NO_SOURCE_DOC), "")
                continue
            target, doc = self._document(kind, line.from_doc_no)
            if target is None:
                answers[str(line.id)] = (line, _Match(MATCH_WAITING, REASON_ORDER_NOT_FOUND), "")
                continue
            if not line.item_code:
                answers[str(line.id)] = (
                    line, _Match(MATCH_UNMATCHED, REASON_LINE_WITHOUT_ITEM), "")
                continue
            groups.add(_Group(kind, target, doc, line.from_doc_no, line.item_code))
        for group in sorted(groups, key=lambda g: (g.kind, g.target, g.doc, g.item_code)):
            answers.update(self._resolve(group))
        return answers


# ============================================================================ derivation
@dataclass
class Touched:
    """Target lines whose cancelled qty may have moved, recomputed at the end of a batch."""
    so_line_ids: set[str] = field(default_factory=set)
    po_line_ids: set[str] = field(default_factory=set)
    spo_ids: set[str] = field(default_factory=set)

    def add_from(self, line: AutocountCancelLine) -> None:
        if line.sales_order_line_id:
            self.so_line_ids.add(str(line.sales_order_line_id))
        if line.purchase_order_line_id:
            self.po_line_ids.add(str(line.purchase_order_line_id))
        if line.spo_allocation_id:
            self.spo_ids.add(str(line.spo_allocation_id))

    def __bool__(self) -> bool:
        return bool(self.so_line_ids or self.po_line_ids or self.spo_ids)


def _live_sums(db: Session, column, ids: Iterable[str]) -> dict[str, Decimal]:
    rows = (
        db.query(column, func.coalesce(func.sum(AutocountCancelLine.qty), 0))
        .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
        .filter(column.in_(list(ids)),
                AutocountCancelLine.match_status == MATCH_MATCHED,
                AutocountCancelDoc.is_void.is_(False),
                AutocountCancelDoc.source_vanished_at.is_(None))
        .group_by(column)
        .all()
    )
    return {str(r[0]): Decimal(r[1]) for r in rows}


def _settled_status(ordered: Any, transferred: Any) -> str:
    """The status a line's own quantities give (`document_ingest_service._line_status`)."""
    ordered = ordered or 0
    return "closed" if ordered > 0 and (transferred or 0) >= ordered else "open"


def recompute_cancelled(db: Session, touched: Touched) -> list[tuple[str, str]]:
    """R3/R5/R6: write `qty_cancelled` (capped at ordered) on every touched line and move the
    status of SO / PO lines that became, or stopped being, fully cancelled. Returns the
    `(sales order line id, cancel doc no)` pairs that BECAME fully cancelled, for the OI
    retire hook. In the caller's transaction."""
    newly_cancelled: list[tuple[str, str]] = []
    if touched.so_line_ids:
        sums = _live_sums(db, AutocountCancelLine.sales_order_line_id, touched.so_line_ids)
        rows = (
            db.query(SalesOrderLine, SalesOrder.status)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.id.in_(list(touched.so_line_ids)))
            .all()
        )
        for row, header_status in rows:
            if _apply(row, sums.get(str(row.id), _ZERO), row.qty_delivered, header_status):
                newly_cancelled.append((str(row.id), _latest_doc_no(db, str(row.id))))
    if touched.po_line_ids:
        sums = _live_sums(db, AutocountCancelLine.purchase_order_line_id, touched.po_line_ids)
        rows = (
            db.query(PurchaseOrderLine, PurchaseOrder.status)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .filter(PurchaseOrderLine.id.in_(list(touched.po_line_ids)))
            .all()
        )
        for row, header_status in rows:
            _apply(row, sums.get(str(row.id), _ZERO), row.qty_received, header_status)
    if touched.spo_ids:
        sums = _live_sums(db, AutocountCancelLine.spo_allocation_id, touched.spo_ids)
        for row in db.query(SPOAllocation).filter(SPOAllocation.id.in_(list(touched.spo_ids))):
            cap = Decimal(row.allocated_quantity or 0)
            value = max(min(sums.get(str(row.id), _ZERO), cap), _ZERO)
            if row.qty_cancelled != value:
                row.qty_cancelled = value
    db.flush()
    return newly_cancelled


def _apply(row, total: Decimal, transferred: Any, header_status: Optional[str]) -> bool:
    """One SO / PO line. True when the line has just become fully cancelled."""
    ordered = Decimal(row.qty_ordered or 0)
    value = max(min(total, ordered), _ZERO)
    was_full = ordered > 0 and Decimal(row.qty_cancelled or 0) >= ordered
    now_full = ordered > 0 and value >= ordered
    if row.qty_cancelled != value:
        row.qty_cancelled = value
    if now_full:
        if row.line_status != CANCELLED:
            row.line_status = CANCELLED
            return True
        return False
    # R6: only a line THIS module cancelled reopens. A board cancel (#1524) or a cancelled
    # order keeps it cancelled.
    if (was_full and row.line_status == CANCELLED
            and getattr(row, "cancelled_at", None) is None
            and (header_status or "").lower() != CANCELLED):
        row.line_status = _settled_status(row.qty_ordered, transferred)
    return False


def _latest_doc_no(db: Session, so_line_id: str) -> str:
    row = (
        db.query(AutocountCancelDoc.doc_no)
        .join(AutocountCancelLine, AutocountCancelLine.cancel_doc_id == AutocountCancelDoc.id)
        .filter(AutocountCancelLine.sales_order_line_id == so_line_id,
                AutocountCancelLine.match_status == MATCH_MATCHED)
        .order_by(AutocountCancelDoc.doc_date.desc(), AutocountCancelDoc.doc_no.desc())
        .first()
    )
    return row[0] if row else ""


def rematch_lines(db: Session, company_id: str, lines: Iterable[AutocountCancelLine],
                  kinds: dict[str, str], touched: Touched,
                  counts: Optional[dict[str, int]] = None) -> None:
    """Match the lines (and everything sharing a group with them) again from the current
    state; old and new targets are both touched. A fallback that lands on a line, or moves to
    another, writes one history row on that line (Q3). `counts` tallies status:reason."""
    lines = list(lines)
    if not lines:
        return
    doc_ids = {str(l.cancel_doc_id) for l in lines}
    live = {
        str(r[0]): (not r[1] and r[2] is None)
        for r in db.query(AutocountCancelDoc.id, AutocountCancelDoc.is_void,
                          AutocountCancelDoc.source_vanished_at)
        .filter(AutocountCancelDoc.id.in_(list(doc_ids))).all()
    }
    answers = CancelLineMatcher(db, company_id).match_all(lines, kinds, live)
    for line, answer, doc_no in answers.values():
        before = (line.sales_order_line_id, line.purchase_order_line_id, line.spo_allocation_id)
        touched.add_from(line)
        line.match_status = answer.status
        line.match_reason = answer.reason
        line.candidate_line_ids = answer.candidates
        line.sales_order_line_id = answer.so_line_id
        line.purchase_order_line_id = answer.po_line_id
        line.spo_allocation_id = answer.spo_id
        touched.add_from(line)
        after = (line.sales_order_line_id, line.purchase_order_line_id, line.spo_allocation_id)
        if counts is not None:
            key = f"{answer.status}:{answer.reason}"
            counts[key] = counts.get(key, 0) + 1
        if answer.reason == REASON_FALLBACK and after != before:
            _record_fallback(db, line, doc_no)
    db.flush()


def _record_fallback(db: Session, line: AutocountCancelLine, doc_no: str) -> None:
    """Q3: the auto-match is visible in the matched line's history (it rolls up to its order)."""
    from app.services import audit_service

    if line.sales_order_line_id:
        entity_type, entity_id = "sales_order_lines", line.sales_order_line_id
    elif line.purchase_order_line_id:
        entity_type, entity_id = "purchase_order_lines", line.purchase_order_line_id
    else:
        entity_type, entity_id = "spo_allocations", line.spo_allocation_id
    audit_service.record(
        db,
        event="CANCEL_AUTO_MATCHED",
        entity_type=entity_type,
        entity_id=str(entity_id),
        description=f"auto-matched: ambiguous cancel {doc_no}",
        new_values={"cancel_doc_no": doc_no, "cancel_dtl_key": line.dtl_key,
                    "qty": str(line.qty), "candidates": line.candidate_line_ids or []},
        company_id=str(line.company_id) if line.company_id else None,
    )


# ============================================================================ the service
class AutocountCancelIngestService:
    """`cancel_sales_orders` and `cancel_purchase_orders` for one company and one book."""

    def __init__(self, db: Session, integration_id: Optional[str], *, company_id: str, book: str):
        self.db = db
        self.integration_id = integration_id
        self.company_id = str(company_id)
        self.book = book
        # SO lines that became fully cancelled in this batch, for the post-commit OI hook.
        self.newly_cancelled_so_lines: list[tuple[str, str]] = []
        # status:reason -> lines decided in the last batch (the sync log line).
        self.match_counts: dict[str, int] = {}

    # ------------------------------------------------------------------ the batch
    def ingest(self, entity_type: str, records: list[Any], *, dry_run: bool = False,
               on_progress=None) -> IngestResult:
        if entity_type not in AUTOCOUNT_CANCEL_ENTITIES:
            raise UnsupportedIngestEntity(f"Unsupported AutoCount entity {entity_type!r}")
        result = IngestResult(dry_run=dry_run)
        touched = Touched()
        to_match: list[AutocountCancelLine] = []
        try:
            for raw in records:
                result.records.append(self._ingest_one(entity_type, raw, touched, to_match))
            self._finish_batch(entity_type, touched, to_match, result)
        finally:
            if dry_run:
                self.db.rollback()
        if on_progress is not None:
            try:
                on_progress(len(records), len(records))
            except Exception:  # pragma: no cover - defensive by design
                logger.warning("ingest progress callback failed", exc_info=True)
        return result

    def _finish_batch(self, entity: str, touched: Touched, to_match: list[AutocountCancelLine],
                      result: IngestResult) -> None:
        """Match the batch's new and changed lines, retry waiting ones, then derive. In its own
        savepoint: a failure here costs the derivation (the next batch redoes it), never the
        documents that already landed."""
        savepoint = self.db.begin_nested()
        try:
            with company_scope(self.db, frozenset({self.company_id})):
                kind = _KIND[entity]
                waiting = (
                    self.db.query(AutocountCancelLine)
                    .join(AutocountCancelDoc,
                          AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
                    .filter(AutocountCancelLine.company_id == self.company_id,
                            AutocountCancelDoc.kind == kind,
                            AutocountCancelLine.match_status == MATCH_WAITING)
                    .order_by(AutocountCancelLine.created_at)
                    .limit(MAX_WAITING_LINES)
                    .all()
                )
                lines = {id(l): l for l in [*to_match, *waiting]}.values()
                kinds = self._kinds(lines)
                counts: dict[str, int] = {}
                rematch_lines(self.db, self.company_id, lines, kinds, touched, counts)
                self.newly_cancelled_so_lines = recompute_cancelled(self.db, touched)
                self._warn_unmatched(entity, result)
                self.match_counts = counts
            if counts:
                # R7 / Q3: the sync log carries how every line was decided this batch
                # (fallbacks and unmatched included), never the record content.
                logger.info("ingest.cancel_match entity=%s company=%s %s", entity,
                            self.company_id,
                            " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
            savepoint.commit()
        except Exception as exc:  # noqa: BLE001
            savepoint.rollback()
            logger.warning("ingest.cancel_derivation_failed entity=%s error=%s",
                           entity, _safe_error(exc))

    def _kinds(self, lines: Iterable[AutocountCancelLine]) -> dict[str, str]:
        doc_ids = {str(l.cancel_doc_id) for l in lines}
        if not doc_ids:
            return {}
        rows = (self.db.query(AutocountCancelDoc.id, AutocountCancelDoc.kind)
                .filter(AutocountCancelDoc.id.in_(list(doc_ids))).all())
        return {str(r[0]): r[1] for r in rows}

    def _warn_unmatched(self, entity: str, result: IngestResult) -> None:
        """A record's verdict names its lines that wait or need review (R7)."""
        ids = [r.entity_id for r in result.records if r.entity_id]
        if not ids:
            return
        rows = (
            self.db.query(AutocountCancelLine.cancel_doc_id, AutocountCancelLine.match_status)
            .filter(AutocountCancelLine.cancel_doc_id.in_(ids),
                    AutocountCancelLine.match_status != MATCH_MATCHED)
            .distinct().all()
        )
        by_doc: dict[str, set[str]] = {}
        for doc_id, status in rows:
            by_doc.setdefault(str(doc_id), set()).add(status)
        for record in result.records:
            statuses = by_doc.get(str(record.entity_id), set())
            if MATCH_WAITING in statuses and WARN_CANCEL_LINE_WAITING not in record.warnings:
                record.warnings.append(WARN_CANCEL_LINE_WAITING)
            if (MATCH_UNMATCHED in statuses
                    and WARN_CANCEL_LINE_UNMATCHED not in record.warnings):
                record.warnings.append(WARN_CANCEL_LINE_UNMATCHED)

    def _ingest_one(self, entity: str, raw: Any, touched: Touched,
                    to_match: list[AutocountCancelLine]) -> RecordResult:
        if not isinstance(raw, dict):
            return RecordResult(source_ref=None, outcome=IngestOutcome.FAILED,
                                errors={"record": "must be an object"})
        source_ref = _source_ref(self.book, entity, raw.get("DocKey"))
        try:
            size = len(json.dumps(raw, default=str))
        except (TypeError, ValueError):
            size = MAX_RECORD_BYTES + 1
        if size > MAX_RECORD_BYTES:
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors={"record": f"larger than {MAX_RECORD_BYTES} bytes"})
        try:
            doc = _parse(raw)
        except _Invalid as exc:
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors=exc.errors)
        savepoint = self.db.begin_nested()
        try:
            with company_scope(self.db, frozenset({self.company_id})):
                outcome, entity_id, matched = self._apply(_KIND[entity], doc, touched)
            savepoint.commit()
            to_match.extend(matched)
            return RecordResult(source_ref=source_ref, outcome=outcome, entity_id=entity_id,
                                warnings=[])
        except IntegrityError as exc:
            savepoint.rollback()
            logger.warning("ingest.integrity_conflict entity=%s source_ref=%s errors=%s",
                           entity, source_ref, json.dumps(integrity_conflict_errors(exc)))
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors=integrity_conflict_errors(exc))
        except Exception as exc:  # noqa: BLE001 - one document's failure, not the batch's
            savepoint.rollback()
            logger.warning("ingest.document_failed entity=%s source_ref=%s error=%s",
                           entity, source_ref, _safe_error(exc))
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors={"_": INTERNAL_ERROR_MESSAGE})

    def _apply(self, kind: str, doc: _CancelDoc, touched: Touched):
        """(outcome, doc id, lines to match). Lines are kept by DtlKey; a removed line's target
        is touched so its cancelled qty drops."""
        row = (
            self.db.query(AutocountCancelDoc)
            .filter(AutocountCancelDoc.company_id == self.company_id,
                    AutocountCancelDoc.kind == kind,
                    AutocountCancelDoc.source_book == self.book,
                    AutocountCancelDoc.doc_key == doc.doc_key)
            .one_or_none()
        )
        if (row is not None and row.source_modified_at is not None and doc.modified is not None
                and doc.modified < row.source_modified_at):
            return IngestOutcome.UNCHANGED, str(row.id), []
        header = {"doc_no": doc.doc_no, "doc_date": doc.doc_date, "is_void": doc.void,
                  "source_modified_at": doc.modified, "source_vanished_at": None}
        created = row is None
        if created:
            row = AutocountCancelDoc(company_id=self.company_id, kind=kind,
                                     source_book=self.book, doc_key=doc.doc_key)
            self.db.add(row)
        changed = created
        for key, value in header.items():
            if getattr(row, key) != value:
                setattr(row, key, value)
                changed = True
        self.db.flush()
        header_moved = changed and not created

        existing = {
            l.dtl_key: l for l in self.db.query(AutocountCancelLine)
            .filter(AutocountCancelLine.cancel_doc_id == row.id)
        }
        to_match: list[AutocountCancelLine] = []
        seen: set[int] = set()
        for parsed in doc.lines:
            seen.add(parsed.dtl_key)
            line = existing.get(parsed.dtl_key)
            values = {f: getattr(parsed, f) for f in _LINE_FIELDS}
            if line is None:
                line = AutocountCancelLine(company_id=self.company_id, cancel_doc_id=row.id,
                                           dtl_key=parsed.dtl_key, match_status=MATCH_WAITING,
                                           **values)
                self.db.add(line)
                to_match.append(line)
                changed = True
                continue
            diff = {f: v for f, v in values.items() if getattr(line, f) != v}
            if not diff:
                continue
            changed = True
            touched.add_from(line)  # its qty or target moves
            for f, v in diff.items():
                setattr(line, f, v)
            if any(f in diff for f in _MATCH_FIELDS):
                to_match.append(line)
        if header_moved:
            # Voiding, un-voiding or restoring a doc moves every line's contribution, and what
            # its key-less lines took in their groups: all of them are matched again.
            for dtl_key, line in existing.items():
                if dtl_key in seen and line not in to_match:
                    to_match.append(line)
        for dtl_key, line in existing.items():
            if dtl_key not in seen:
                touched.add_from(line)
                self.db.delete(line)
                changed = True
        if changed:
            row.source_record = doc.raw
            row.last_synced_at = datetime.utcnow()
        self.db.flush()
        if created:
            return IngestOutcome.CREATED, str(row.id), to_match
        return (IngestOutcome.UPDATED if changed else IngestOutcome.UNCHANGED), str(row.id), to_match

    # ================================================================== deletions
    def delete(self, entity: str, doc_keys: list[Any], date_from: date, date_to: date, *,
               dry_run: bool = False) -> DeletionResult:
        """The sweep's vanished DocKeys: marked vanished (they cancel nothing any more), never
        removed, and the lines they cancelled are derived again."""
        if entity not in AUTOCOUNT_CANCEL_ENTITIES:
            raise UnsupportedIngestEntity(f"Unsupported AutoCount entity {entity!r}")
        result = DeletionResult(dry_run=dry_run)
        touched = Touched()
        try:
            with company_scope(self.db, frozenset({self.company_id})):
                for value in doc_keys:
                    result.records.append(
                        self._delete_one(entity, value, date_from, date_to, touched))
                self.db.flush()
                self.newly_cancelled_so_lines = recompute_cancelled(self.db, touched)
        finally:
            if dry_run:
                self.db.rollback()
        return result

    def _delete_one(self, entity, value, date_from, date_to, touched: Touched):
        doc_key = _int(value)
        ref = _source_ref(self.book, entity, doc_key) if doc_key is not None else str(value)
        if doc_key is None:
            return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.FAILED,
                                        errors={"doc_key": "must be an integer"})
        row = (
            self.db.query(AutocountCancelDoc)
            .filter(AutocountCancelDoc.company_id == self.company_id,
                    AutocountCancelDoc.kind == _KIND[entity],
                    AutocountCancelDoc.source_book == self.book,
                    AutocountCancelDoc.doc_key == doc_key)
            .one_or_none()
        )
        if row is None:
            return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.NOT_FOUND)
        if not (date_from <= row.doc_date <= date_to):
            return DeletionRecordResult(
                source_ref=ref, outcome=DeletionOutcome.FAILED, entity_id=str(row.id),
                errors={"doc_date": f"stored DocDate {row.doc_date} is outside the swept range"},
            )
        if row.source_vanished_at is None:
            row.source_vanished_at = datetime.now(timezone.utc)
            self.db.flush()
            lines = self.db.query(AutocountCancelLine).filter(
                AutocountCancelLine.cancel_doc_id == row.id).all()
            rematch_lines(self.db, self.company_id, lines, {str(row.id): row.kind}, touched)
        return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.DEACTIVATED,
                                    entity_id=str(row.id))


def relink_for_orders(db: Session, *, company_id: str, numbers: set[str]) -> list[tuple[str, str]]:
    """An SO, PO or shipping order push landed: re-match the cancel lines that name those
    numbers (waiting ones link now; an ordered qty change re-caps) and derive again. In the
    caller's transaction; returns the SO lines that became fully cancelled."""
    if not numbers:
        return []
    touched = Touched()
    with company_scope(db, frozenset({str(company_id)})):
        lines = (
            db.query(AutocountCancelLine)
            .filter(AutocountCancelLine.company_id == str(company_id),
                    AutocountCancelLine.from_doc_no.in_(sorted(numbers)))
            .limit(MAX_WAITING_LINES * 4)
            .all()
        )
        if not lines:
            return []
        kinds = {
            str(r[0]): r[1] for r in db.query(AutocountCancelDoc.id, AutocountCancelDoc.kind)
            .filter(AutocountCancelDoc.id.in_({str(l.cancel_doc_id) for l in lines})).all()
        }
        rematch_lines(db, str(company_id), lines, kinds, touched)
        return recompute_cancelled(db, touched)


def run_cancel_hooks(db: Session, service: Any, *, actor: Optional[str]) -> None:
    """R5, after the batch's own commit: the order inquiry rows of every SO line that just
    became fully cancelled are retired the way a board Cancel SO retires them (#1530: rows
    cancelled, links removed, handover queued, "Transferred to Cancel SO: <XS no>") and the
    acknowledged ones flagged. Best effort, its own commit: a failure costs the reaction only
    (logged), never the ingest. Rows already retired stay retired (owner, 13 Oct)."""
    pairs = list(getattr(service, "newly_cancelled_so_lines", []) or [])
    if not pairs:
        return
    try:
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
            flag_rows_for_cancelled_lines,
        )

        ProjectOrderInquiryService(db).retire_rows_for_cancelled_lines(
            [(line_id, doc_no or "AutoCount") for line_id, doc_no in pairs],
            actor_user_id=actor,
        )
        flag_rows_for_cancelled_lines(db, [line_id for line_id, _ in pairs])
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the ingest itself already committed
        db.rollback()
        logger.warning("ingest.cancel_oi_hook_failed error=%s", _safe_error(exc))


__all__ = [
    "AUTOCOUNT_CANCEL_ENTITIES",
    "CANCEL_PURCHASE_ORDERS_ENTITY",
    "CANCEL_SALES_ORDERS_ENTITY",
    "CONTRACT_2_8_WARNINGS",
    "AutocountCancelIngestService",
    "CancelLineMatcher",
    "recompute_cancelled",
    "relink_for_orders",
    "run_cancel_hooks",
]
