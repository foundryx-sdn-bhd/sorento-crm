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

**Exact, or say why not (R2, R7; owner rule 13 Oct).** A line with a `FromDocDtlKey` matches
that exact line; a key naming a line that is not there while its document is stays unmatched
(`dangling_line_key`), never an item match. Without a key, the line is decided with its whole
group (one document + item, plus the location for a PO / SPO; owner rule 14 Oct: a Cancel SO
groups on SO + item whatever the location) by `autocount_cancel_matching.resolve_group`, by
arithmetic only: the group's one line, or, when nothing moved and the cancels are every
transfer, each line's own transfer (`group_whole`: the cancel names the group, and each line's
cancelled qty is its transfer). Anything else is `undetermined`: kept at document + item level,
named on no line, counted in the sync log. No tie-break, no fallback, no guess, no person
reviewing. No document yet is `waiting`, retried every batch and when that order is pushed. The
answer depends only on what is stored, never on arrival order.

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
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import String, and_, func, or_
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.autocount_cancel import (
    KIND_CANCEL_PO,
    KIND_CANCEL_SO,
    KIND_CONSIGNMENT,
    KIND_CONSIGNMENT_RETURN,
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
    REASON_GROUP_WHOLE,
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
# Crew, 13 Oct: AutoCount Consignment / Consignment Return are accepted and stored only on the
# same surface and shape; no consignment behaviour in this lane.
CONSIGNMENTS_ENTITY = "consignments"
CONSIGNMENT_RETURNS_ENTITY = "consignment_returns"
AUTOCOUNT_CANCEL_ENTITIES = frozenset({CANCEL_SALES_ORDERS_ENTITY, CANCEL_PURCHASE_ORDERS_ENTITY,
                                       CONSIGNMENTS_ENTITY, CONSIGNMENT_RETURNS_ENTITY})

_KIND = {CANCEL_SALES_ORDERS_ENTITY: KIND_CANCEL_SO, CANCEL_PURCHASE_ORDERS_ENTITY: KIND_CANCEL_PO,
         CONSIGNMENTS_ENTITY: KIND_CONSIGNMENT,
         CONSIGNMENT_RETURNS_ENTITY: KIND_CONSIGNMENT_RETURN}
_PREFIX = {CANCEL_SALES_ORDERS_ENTITY: "XS", CANCEL_PURCHASE_ORDERS_ENTITY: "XP",
           CONSIGNMENTS_ENTITY: "CSGN", CONSIGNMENT_RETURNS_ENTITY: "CSGR"}
_STORED_ONLY = frozenset({KIND_CONSIGNMENT, KIND_CONSIGNMENT_RETURN})

# Why a cancel line matched nothing (R2, R7). `waiting` lines are retried every batch.
REASON_ORDER_NOT_FOUND = "order_not_found"
REASON_DANGLING_LINE_KEY = "dangling_line_key"
REASON_AMBIGUOUS = "ambiguous"
REASON_NO_SOURCE_DOC = "no_source_doc"
REASON_LINE_WITHOUT_ITEM = "line_without_item"
REASON_STORED_ONLY = "stored_only"

# Contract 2.8 warnings on a record's verdict.
WARN_CANCEL_LINE_WAITING = "cancel_line_waiting"
WARN_CANCEL_LINE_UNMATCHED = "cancel_line_unmatched"
CONTRACT_2_8_WARNINGS = (WARN_CANCEL_LINE_WAITING, WARN_CANCEL_LINE_UNMATCHED)

# `qty` is Numeric(15, 4) and `seq` an Integer: anything beyond is a field error on the record.
_MAX_QTY = Decimal("100000000000")
_INT4_MAX = 2**31 - 1
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
            elif qty is not None and qty >= _MAX_QTY:
                errors[f"{prefix}.Qty"] = "out of range"
            seq = _int(row.get("Seq"))
            if seq is not None and not -_INT4_MAX <= seq <= _INT4_MAX:
                errors[f"{prefix}.Seq"] = "out of range"
                seq = None
            lines.append(
                _CancelLine(
                    dtl_key=dtl_key,
                    seq=seq,
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
_MATCH_FIELDS = ("item_code", "location_code", "from_doc_no", "from_dtl_key", "qty")


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


def _live_doc():
    return and_(AutocountCancelDoc.is_void.is_(False), AutocountCancelDoc.source_vanished_at.is_(None))


def _loc(value: Optional[str]) -> str:
    """The location a group is keyed on. Compared in Python only, never against a SQL upper /
    trim, which normalise some characters differently (security review should-fix 1)."""
    return (value or "").strip().upper()


def _group_location(target: str, value: Optional[str]) -> str:
    """The location part of a group key: none for an SO, the normalised location for a PO /
    SPO. Owner rule 14 Oct: a Cancel SO links to its SO by document + item, because a cancel or
    DO line's location can differ from its SO line's (dev: 570 of 9,828 single-match DO lines)."""
    return "" if target == "so" else _loc(value)


def _names_any(ids: list[str]):
    """A `group_whole` cancel line whose group holds any of these order lines."""
    return and_(AutocountCancelLine.match_reason == REASON_GROUP_WHOLE,
                AutocountCancelLine.candidate_line_ids.op("?|")(array(ids, type_=String)))


@dataclass(frozen=True)
class _Group:
    """One document + item (+ location for a PO / SPO): the unit the exact arithmetic is
    decided over."""
    kind: str  # cancel_so / cancel_po
    target: str  # so / po / spo
    doc: str  # SO / PO header id, or the SPO match key
    number: str  # the number the cancel lines name
    item_code: str
    location: str  # `_group_location`: "" for an SO


class CancelLineMatcher:
    """R2 from what the CRM holds now. A key-less line is decided with its whole group
    (`autocount_cancel_matching.resolve_group`), so nothing depends on arrival order."""

    def __init__(self, db: Session, company_id: str):
        self.db = db
        self.company_id = company_id

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
    def _candidates(self, group: _Group) -> tuple[list[CandidateIn], Decimal]:
        """The group's lines with T_i (TransferedQty less the live cancels matched to the line by
        line key) and the group's moved qty D: DO qty for an SO (linked to a group line, or
        naming the SO and item at any location, since a repeated item's DO line links to the
        SO + item group and no line), GRN qty on the group's lines for a PO / SPO."""
        if group.target == "so":
            rows = (self.db.query(SalesOrderLine.id, SalesOrderLine.qty_delivered)
                    .join(Product, Product.id == SalesOrderLine.product_id)
                    .filter(SalesOrderLine.sales_order_id == group.doc,
                            Product.product_code == group.item_code).all())
            ids = [str(r[0]) for r in rows]
            named = and_(OrderLine.from_doc_no == group.number,
                         OrderLine.item_code == group.item_code)
            dos = (self.db.query(OrderLine.quantity)
                   .join(Order, Order.id == OrderLine.order_id)
                   # company_id: defence in depth over the ORM company scope, not instead of it
                   .filter(Order.company_id == self.company_id, Order.is_cancelled.is_(False),
                           or_(OrderLine.sales_order_line_id.in_(ids), named) if ids else named)
                   .all())
            moved = sum((as_decimal(q) for (q,) in dos), _ZERO)
            keyed = self._keyed(AutocountCancelLine.sales_order_line_id, ids)
            return [CandidateIn(str(r[0]), as_decimal(r[1]) - keyed.get(str(r[0]), _ZERO))
                    for r in rows], moved
        if group.target == "po":
            rows = (self.db.query(PurchaseOrderLine.id, Warehouse.warehouse_code,
                                  PurchaseOrderLine.qty_received)
                    .join(Product, Product.id == PurchaseOrderLine.product_id)
                    .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
                    .filter(PurchaseOrderLine.purchase_order_id == group.doc,
                            Product.product_code == group.item_code).all())
            rows = [r for r in rows if _loc(r[1]) == group.location]
            ids = [str(r[0]) for r in rows]
            moved = sum(self._grn(PickingLine.po_line_id, ids).values(), _ZERO)
            keyed = self._keyed(AutocountCancelLine.purchase_order_line_id, ids)
            return [CandidateIn(str(r[0]), as_decimal(r[2]) - keyed.get(str(r[0]), _ZERO))
                    for r in rows], moved
        from app.services.grn_spo_matching import _spo_match_key_sql

        rows = (self.db.query(SPOAllocation.id,
                              func.coalesce(Warehouse.warehouse_code, SPOAllocation.location_code),
                              SPOAllocation.stated_received)
                .join(Product, Product.id == SPOAllocation.product_id)
                .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
                .filter(SPOAllocation.company_id == self.company_id,
                        _spo_match_key_sql(SPOAllocation.spo_number) == group.doc,
                        Product.product_code == group.item_code).all())
        rows = [r for r in rows if _loc(r[1]) == group.location]
        ids = [str(r[0]) for r in rows]
        moved = sum(self._grn(PickingLine.spo_allocation_id, ids).values(), _ZERO)
        keyed = self._keyed(AutocountCancelLine.spo_allocation_id, ids)
        return [CandidateIn(str(r[0]), as_decimal(r[2]) - keyed.get(str(r[0]), _ZERO))
                for r in rows], moved

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
        """Every key-less cancel line of a LIVE doc naming this document and item (and, for a
        PO / SPO, location)."""
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
        return [(l, d) for l, d in query.all()
                if _group_location(group.target, l.location_code) == group.location]

    def _resolve(self, group: _Group) -> dict[str, tuple[AutocountCancelLine, _Match, str]]:
        members = self._group_lines(group)
        candidates, moved = self._candidates(group)
        outcomes = resolve_group([CancelIn(str(l.id), as_decimal(l.qty)) for l, _ in members],
                                 candidates, moved)
        out = {}
        for line, doc in members:
            o = outcomes[str(line.id)]
            target = {_TARGET_FIELD[group.target]: o.target_id} if o.target_id else {}
            out[str(line.id)] = (line, _Match(o.status, o.reason, candidates=list(o.candidates)
                                              or None, **target), doc.doc_no)
        return out

    def _target_group(self, kind: str, target: str, target_id: str) -> Optional[_Group]:
        """The group a line-key match lands in: its document and item, plus the location for a
        PO / SPO (keyed matches change the T_i the group is decided on)."""
        if target == "so":
            row = (self.db.query(SalesOrder.id, SalesOrder.so_number, Product.product_code)
                   .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
                   .join(Product, Product.id == SalesOrderLine.product_id)
                   .filter(SalesOrderLine.id == target_id).first())
            return (_Group(kind, "so", str(row[0]), row[1], row[2], _group_location("so", None))
                    if row else None)
        if target == "po":
            row = (self.db.query(PurchaseOrder.id, PurchaseOrder.po_number, Product.product_code,
                                 Warehouse.warehouse_code)
                   .join(PurchaseOrderLine, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
                   .join(Product, Product.id == PurchaseOrderLine.product_id)
                   .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
                   .filter(PurchaseOrderLine.id == target_id).first())
            return _Group(kind, "po", str(row[0]), row[1], row[2], _loc(row[3])) if row else None
        from app.services.grn_spo_matching import _spo_match_key

        row = (self.db.query(SPOAllocation.spo_number, Product.product_code,
                             func.coalesce(Warehouse.warehouse_code, SPOAllocation.location_code))
               .join(Product, Product.id == SPOAllocation.product_id)
               .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
               .filter(SPOAllocation.id == target_id).first())
        if not row or not row[0]:
            return None
        return _Group(kind, "spo", _spo_match_key(row[0]), row[0], row[1], _loc(row[2]))

    def _old_groups(self, kind: str, line: AutocountCancelLine) -> set[_Group]:
        """The group the line was last decided in: through its target, or through the group's
        lines (`candidate_line_ids`) when it named no single line."""
        out: set[_Group] = set()
        for target, column in (("so", "sales_order_line_id"), ("po", "purchase_order_line_id"),
                               ("spo", "spo_allocation_id")):
            if getattr(line, column):
                group = self._target_group(kind, target, str(getattr(line, column)))
                if group:
                    out.add(group)
        # Every stored line is in the one group; the first that still exists names it (a line
        # the order push removed resolves to nothing).
        targets = ("so",) if kind == KIND_CANCEL_SO else ("po", "spo")
        for candidate in line.candidate_line_ids or []:
            group = next((g for g in (self._target_group(kind, t, str(candidate)) for t in targets)
                          if g), None)
            if group:
                out.add(group)
                break
        return out

    def groups_of(self, kind: str, line: AutocountCancelLine) -> set[_Group]:
        """Every group a line counts in, before it is removed: the one it was decided in and
        the one it names now."""
        out = self._old_groups(kind, line) if kind not in _STORED_ONLY else set()
        current = self._key_less_group(kind, line) if kind not in _STORED_ONLY else None
        return out | ({current} if current else set())

    def _key_less_group(self, kind: str, line: AutocountCancelLine) -> Optional[_Group]:
        """The group a key-less line names now, live or not, or None."""
        if line.from_dtl_key is not None or not line.from_doc_no or not line.item_code:
            return None
        target, doc = self._document(kind, line.from_doc_no)
        if target is None:
            return None
        return _Group(kind, target, doc, line.from_doc_no, line.item_code,
                      _group_location(target, line.location_code))

    # ------------------------------------------------------------- the pass
    def match_all(self, lines: Iterable[AutocountCancelLine], kinds: dict[str, str],
                  live: dict[str, bool], assign: Callable[[AutocountCancelLine, _Match, str], None],
                  extra_groups: Iterable[_Group] = ()) -> None:
        """Decide every line asked about, plus every other key-less line sharing a group with
        them (a group is decided whole), handing each answer to `assign`.

        Two passes (review B1): line-key matches are assigned and flushed FIRST, because a
        group's unexplained transfer subtracts them; deciding a group before they land would
        make the answer depend on which document arrived first."""
        answers: dict[str, tuple[AutocountCancelLine, _Match, str]] = {}
        groups: set[_Group] = set(extra_groups)
        for line in lines:
            kind = kinds[str(line.cancel_doc_id)]
            if kind in _STORED_ONLY:
                answers[str(line.id)] = (line, _Match(MATCH_UNMATCHED, REASON_STORED_ONLY), "")
                continue
            # The group the line USED to count in is decided again too (review B1): leaving it,
            # by a void, a sweep or an edit, changes what the rest of that group adds up to.
            groups.update(self._old_groups(kind, line))
            key_less = self._key_less_group(kind, line)
            if key_less:
                groups.add(key_less)
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
        for line, match, doc_no in answers.values():
            assign(line, match, doc_no)
        self.db.flush()
        for group in sorted(groups, key=lambda g: (g.kind, g.target, g.doc, g.item_code,
                                                   g.location)):
            for line, match, doc_no in self._resolve(group).values():
                assign(line, match, doc_no)
        self.db.flush()


# ============================================================================ derivation
@dataclass
class Touched:
    """Target lines whose cancelled qty may have moved, recomputed at the end of a batch."""
    so_line_ids: set[str] = field(default_factory=set)
    po_line_ids: set[str] = field(default_factory=set)
    spo_ids: set[str] = field(default_factory=set)

    def add_from(self, line: AutocountCancelLine) -> None:
        """The line's target and every line of its group (a `group_whole` cancel moves each of
        them). Group ids go in every set: the recompute reads each set by id, so an id from
        another table finds nothing there."""
        if line.candidate_line_ids:
            ids = {str(i) for i in line.candidate_line_ids}
            self.so_line_ids |= ids
            self.po_line_ids |= ids
            self.spo_ids |= ids
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


def _whole_group_lines(db: Session, ids: set[str]) -> set[str]:
    """Of these order lines, the ones in a group a live `group_whole` cancel decided: each one's
    cancelled qty is its own transfer (owner rule 13 Oct, D = 0 and C = sum(T))."""
    if not ids:
        return set()
    wanted = sorted(ids)
    rows = (db.query(AutocountCancelLine.candidate_line_ids)
            .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
            .filter(AutocountCancelLine.match_status == MATCH_MATCHED, _names_any(wanted),
                    _live_doc())
            .all())
    return {str(i) for (members,) in rows for i in (members or [])} & set(wanted)


def _settled_status(ordered: Any, transferred: Any) -> str:
    """The status a line's own quantities give (`document_ingest_service._line_status`)."""
    ordered = ordered or 0
    return "closed" if ordered > 0 and (transferred or 0) >= ordered else "open"


@dataclass
class Derived:
    """What a recompute moved, for the post-commit hooks: SO lines that BECAME fully cancelled
    (`(line id, cancel doc no)`, R5) and SO lines whose planned qty moved while they stayed
    live (`(line id, planned before, planned after)`, Q4 a)."""
    newly_cancelled: list[tuple[str, str]] = field(default_factory=list)
    plan_moves: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)

    def extend(self, other: "Derived") -> None:
        self.newly_cancelled.extend(other.newly_cancelled)
        self.plan_moves.extend(other.plan_moves)


def recompute_cancelled(db: Session, touched: Touched) -> Derived:
    """R3/R5/R6: write `qty_cancelled` (capped at ordered) on every touched line and move the
    status of SO / PO lines that became, or stopped being, fully cancelled. In the caller's
    transaction."""
    derived = Derived()
    newly_cancelled = derived.newly_cancelled
    whole = _whole_group_lines(db, touched.so_line_ids | touched.po_line_ids | touched.spo_ids)
    if touched.so_line_ids:
        sums = _live_sums(db, AutocountCancelLine.sales_order_line_id, touched.so_line_ids)
        rows = (
            db.query(SalesOrderLine, SalesOrder.status)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.id.in_(list(touched.so_line_ids)))
            .all()
        )
        for row, header_status in rows:
            before_c, before_status = Decimal(row.qty_cancelled or 0), row.line_status
            total = (as_decimal(row.qty_delivered) if str(row.id) in whole
                     else sums.get(str(row.id), _ZERO))
            if _apply(row, total, row.qty_delivered, header_status):
                newly_cancelled.append((str(row.id), _latest_doc_no(db, str(row.id))))
            elif (row.qty_cancelled != before_c and before_status != CANCELLED
                  and row.line_status != CANCELLED):
                # The planned basis `plan_qty_of` reads (review should-fix 5).
                base = Decimal(row.qty_ordered if row.qty_required is None else row.qty_required)
                derived.plan_moves.append((str(row.id), max(base - before_c, _ZERO),
                                           max(base - Decimal(row.qty_cancelled), _ZERO)))
    if touched.po_line_ids:
        sums = _live_sums(db, AutocountCancelLine.purchase_order_line_id, touched.po_line_ids)
        rows = (
            db.query(PurchaseOrderLine, PurchaseOrder.status)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .filter(PurchaseOrderLine.id.in_(list(touched.po_line_ids)))
            .all()
        )
        for row, header_status in rows:
            total = (as_decimal(row.qty_received) if str(row.id) in whole
                     else sums.get(str(row.id), _ZERO))
            _apply(row, total, row.qty_received, header_status)
    if touched.spo_ids:
        sums = _live_sums(db, AutocountCancelLine.spo_allocation_id, touched.spo_ids)
        for row in db.query(SPOAllocation).filter(SPOAllocation.id.in_(list(touched.spo_ids))):
            cap = Decimal(row.allocated_quantity or 0)
            total = (as_decimal(row.stated_received) if str(row.id) in whole
                     else sums.get(str(row.id), _ZERO))
            value = max(min(total, cap), _ZERO)
            if row.qty_cancelled != value:
                row.qty_cancelled = value
    db.flush()
    return derived


def _apply(row, total: Decimal, transferred: Any, header_status: Optional[str]) -> bool:
    """One SO / PO line. True when cancel documents have just cancelled ALL of it.

    `cancelled_by_cancel_doc` records that it was this rule that cancelled the line (review
    should-fix 2): only such a line reopens when the cancel lowers. A line already cancelled
    for another reason (board Cancel SO, removed from the AutoCount order, order cancelled)
    is never claimed, so it never reopens here."""
    ordered = Decimal(row.qty_ordered or 0)
    value = max(min(total, ordered), _ZERO)
    now_full = ordered > 0 and value >= ordered
    if row.qty_cancelled != value:
        row.qty_cancelled = value
    if now_full:
        if row.line_status != CANCELLED:
            # A flag left set on a line something reopened (an SO push raising ordered, a
            # relink that failed) is re-cancelled here, but it is not a NEW cancellation: its
            # OI rows were retired the first time (review N1).
            newly = not row.cancelled_by_cancel_doc
            row.line_status = CANCELLED
            row.cancelled_by_cancel_doc = True
            return newly
        return False
    if row.cancelled_by_cancel_doc:
        row.cancelled_by_cancel_doc = False
        if (row.line_status == CANCELLED
                and getattr(row, "cancelled_at", None) is None
                and (header_status or "").lower() != CANCELLED):
            row.line_status = _settled_status(row.qty_ordered, transferred)
    return False


def _latest_doc_no(db: Session, so_line_id: str) -> str:
    row = (
        db.query(AutocountCancelDoc.doc_no)
        .join(AutocountCancelLine, AutocountCancelLine.cancel_doc_id == AutocountCancelDoc.id)
        .filter(or_(AutocountCancelLine.sales_order_line_id == so_line_id,
                    _names_any([so_line_id])),
                AutocountCancelLine.match_status == MATCH_MATCHED,
                _live_doc())
        .order_by(AutocountCancelDoc.doc_date.desc(), AutocountCancelDoc.doc_no.desc())
        .first()
    )
    return row[0] if row else ""


def rematch_lines(db: Session, company_id: str, lines: Iterable[AutocountCancelLine],
                  kinds: dict[str, str], touched: Touched,
                  counts: Optional[dict[str, int]] = None, groups: Iterable[Any] = ()) -> None:
    """Match the lines (and everything sharing a group with them, plus `groups`: those of lines
    already removed) again from the current state; old and new targets (and groups) are both
    touched. `counts` tallies status:reason."""
    lines, groups = list(lines), list(groups)
    if not lines and not groups:
        return
    doc_ids = {str(l.cancel_doc_id) for l in lines}
    live = {
        str(r[0]): (not r[1] and r[2] is None)
        for r in db.query(AutocountCancelDoc.id, AutocountCancelDoc.is_void,
                          AutocountCancelDoc.source_vanished_at)
        .filter(AutocountCancelDoc.id.in_(list(doc_ids))).all()
    }

    def assign(line: AutocountCancelLine, answer: _Match, doc_no: str) -> None:
        touched.add_from(line)
        line.match_status = answer.status
        line.match_reason = answer.reason
        line.candidate_line_ids = answer.candidates
        line.sales_order_line_id = answer.so_line_id
        line.purchase_order_line_id = answer.po_line_id
        line.spo_allocation_id = answer.spo_id
        touched.add_from(line)
        if counts is not None:
            key = f"{answer.status}:{answer.reason}"
            counts[key] = counts.get(key, 0) + 1

    CancelLineMatcher(db, company_id).match_all(lines, kinds, live, assign, groups)


# ============================================================================ the service
class AutocountCancelIngestService:
    """`cancel_sales_orders` and `cancel_purchase_orders` for one company and one book."""

    def __init__(self, db: Session, integration_id: Optional[str], *, company_id: str, book: str):
        self.db = db
        self.integration_id = integration_id
        self.company_id = str(company_id)
        self.book = book
        # What this batch's derivation moved, for the post-commit hooks (R5, Q4 a).
        self.derived = Derived()
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
        self._removed_groups: set = set()
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
                    .order_by(AutocountCancelLine.created_at.desc(), AutocountCancelLine.id)
                    .limit(MAX_WAITING_LINES)
                    .all()
                )
                lines = {id(l): l for l in [*to_match, *waiting]}.values()
                kinds = self._kinds(lines)
                counts: dict[str, int] = {}
                rematch_lines(self.db, self.company_id, lines, kinds, touched, counts,
                              self._removed_groups)
                derived = recompute_cancelled(self.db, touched)
                self._warn_unmatched(entity, result)
            savepoint.commit()
            # Only a derivation that landed reaches the post-commit hooks (review nit).
            self.derived = derived
            self.match_counts = counts
            if counts:
                # R7: the sync log carries how every line was decided this batch (undetermined
                # and unmatched included), never the record content.
                logger.info("ingest.cancel_match entity=%s company=%s %s", entity,
                            self.company_id,
                            " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
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
                    AutocountCancelLine.match_status != MATCH_MATCHED,
                    # a voided doc's or a stored-only doc's lines are not a matching problem
                    func.coalesce(AutocountCancelLine.match_reason, "").notin_(
                        (REASON_DOC_NOT_LIVE, REASON_STORED_ONLY)))
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
                # Review B1: what the rest of its group adds up to changes, so the group is
                # decided again at the end of the batch (the removed line itself is gone).
                self._removed_groups |= CancelLineMatcher(self.db, self.company_id).groups_of(
                    kind, line)
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
                self.derived = recompute_cancelled(self.db, touched)
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


def relink_for_orders(db: Session, *, company_id: str, numbers: set[str]) -> Derived:
    """An SO, PO or shipping order push landed: re-match the cancel lines that name those
    numbers (waiting ones link now; an ordered qty change re-caps) and derive again. In the
    caller's transaction."""
    if not numbers:
        return Derived()
    touched = Touched()
    with company_scope(db, frozenset({str(company_id)})):
        cap = MAX_WAITING_LINES * 4
        lines = (
            db.query(AutocountCancelLine)
            .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
            .filter(AutocountCancelLine.company_id == str(company_id),
                    AutocountCancelLine.from_doc_no.in_(sorted(numbers)),
                    _live_doc())
            .order_by(AutocountCancelLine.created_at.desc(), AutocountCancelLine.id)
            .limit(cap + 1)
            .all()
        )
        if not lines:
            return Derived()
        if len(lines) > cap:
            # Security review should-fix 2: never a silent subset. The rest are re-matched by
            # their own next cancel sync.
            logger.warning("ingest.cancel_relink_capped company=%s cap=%d", company_id, cap)
            lines = lines[:cap]
        kinds = {
            str(r[0]): r[1] for r in db.query(AutocountCancelDoc.id, AutocountCancelDoc.kind)
            .filter(AutocountCancelDoc.id.in_({str(l.cancel_doc_id) for l in lines})).all()
        }
        rematch_lines(db, str(company_id), lines, kinds, touched)
        return recompute_cancelled(db, touched)


def cancel_sources(db: Session, column, line_ids: Iterable[str]) -> dict[str, list[dict]]:
    """Owner hand test (13 Oct): per SO / PO line, the cancel documents that make up its
    `qty_cancelled` (live docs only, the same set `recompute_cancelled` reads), oldest first,
    each with its qty and how it was matched. A `group_whole` document is listed once on every
    line of its group with no qty: only each line's total is known, not a document's share
    (review S1). One read for all the lines asked about."""
    ids = [str(i) for i in line_ids if i]
    if not ids:
        return {}
    rows = (
        db.query(column, AutocountCancelLine.candidate_line_ids, AutocountCancelDoc.doc_no,
                 AutocountCancelLine.qty, AutocountCancelLine.match_reason)
        .join(AutocountCancelDoc, AutocountCancelDoc.id == AutocountCancelLine.cancel_doc_id)
        .filter(or_(column.in_(ids), _names_any(ids)),
                AutocountCancelLine.match_status == MATCH_MATCHED, _live_doc())
        .order_by(AutocountCancelDoc.doc_date, AutocountCancelDoc.doc_no,
                  AutocountCancelLine.dtl_key)
        .all()
    )
    wanted = set(ids)
    out: dict[str, list[dict]] = {}
    for line_id, members, doc_no, qty, reason in rows:
        if line_id:
            out.setdefault(str(line_id), []).append(
                {"doc_no": doc_no, "qty": float(qty or 0), "match_reason": reason})
            continue
        for target in (str(i) for i in members or [] if str(i) in wanted):
            entry = {"doc_no": doc_no, "qty": None, "match_reason": reason}
            if entry not in out.setdefault(target, []):
                out[target].append(entry)
    return out


def run_cancel_hooks(db: Session, service: Any, *, actor: Optional[str]) -> None:
    """After the batch's own commit, each reaction best effort with its own commit (a failure
    costs that reaction only, logged, never the ingest):

    - R5: the order inquiry rows of every SO line that just became fully cancelled are retired
      the way a board Cancel SO retires them (#1530: rows cancelled, links removed, handover
      queued, "Transferred to Cancel SO: <XS no>") and the acknowledged ones flagged. Rows
      already retired stay retired (owner, 13 Oct).
    - Q4 a: a partial cancel moved a live line's planned qty, so the AutoCount planning change
      is raised for CS on the board exactly as an SO push's qty edit raises it (#1599 flow;
      `planning_change_service.build_batch` keeps its own gate: held or inquired lines only).
    """
    derived = getattr(service, "derived", None) or Derived()
    if derived.newly_cancelled:
        try:
            from app.services.project_order_inquiry_service import (
                ProjectOrderInquiryService,
                flag_rows_for_cancelled_lines,
            )

            ProjectOrderInquiryService(db).retire_rows_for_cancelled_lines(
                [(line_id, doc_no or "AutoCount") for line_id, doc_no in derived.newly_cancelled],
                actor_user_id=actor,
            )
            flag_rows_for_cancelled_lines(db, [line_id for line_id, _ in derived.newly_cancelled])
            db.commit()
        except Exception as exc:  # noqa: BLE001 - the ingest itself already committed
            db.rollback()
            logger.warning("ingest.cancel_oi_hook_failed error=%s", _safe_error(exc))
    if derived.plan_moves:
        try:
            _raise_planning_change(db, derived.plan_moves, actor=actor)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - the ingest itself already committed
            db.rollback()
            logger.warning("ingest.cancel_planning_change_failed error=%s", _safe_error(exc))


def _raise_planning_change(db: Session, moves: list[tuple[str, Decimal, Decimal]], *,
                           actor: Optional[str]) -> None:
    from app.services import planning_change_service
    from app.services.scm.outstanding_diff import Line, diff_lines

    rows = (
        db.query(SalesOrderLine, SalesOrder.id, SalesOrder.so_number, Product.product_code,
                 Warehouse.warehouse_code)
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
        .filter(SalesOrderLine.id.in_([m[0] for m in moves]))
        .all()
    )
    by_id = {str(r[0].id): r for r in rows}
    before, after, order_ids = [], [], {}
    for line_id, was, now in moves:
        found = by_id.get(line_id)
        if found is None:
            continue
        line, header_id, so_number, item_code, location = found
        order_ids[so_number] = str(header_id)
        for side, qty in ((before, was), (after, now)):
            side.append(Line(doc_number=so_number, item_code=item_code or "",
                             location=location or "", qty=float(qty),
                             required_date=line.required_date, row_ref=line_id,
                             line_id=line_id))
    diff = diff_lines(before, after)
    applied = {}
    for change in diff.changes:
        ref = (change.after.row_ref if change.after else None) or (
            change.before.row_ref if change.before else None)
        if ref:
            applied[id(change)] = ref
    planning_change_service.build_batch(db, diff, applied_line_ids=applied, order_ids=order_ids,
                                        actor=actor, import_job_id=None, file_name=None)


__all__ = [
    "AUTOCOUNT_CANCEL_ENTITIES",
    "CANCEL_PURCHASE_ORDERS_ENTITY",
    "CANCEL_SALES_ORDERS_ENTITY",
    "CONSIGNMENTS_ENTITY",
    "CONSIGNMENT_RETURNS_ENTITY",
    "CONTRACT_2_8_WARNINGS",
    "AutocountCancelIngestService",
    "CancelLineMatcher",
    "Derived",
    "cancel_sources",
    "recompute_cancelled",
    "relink_for_orders",
    "run_cancel_hooks",
]
