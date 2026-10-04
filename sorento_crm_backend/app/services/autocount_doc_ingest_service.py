"""Ingest AutoCount Delivery Orders, Goods Receive Notes and branches (#1354 S2, contract 2.7).

Plan: documentation/plans/autocount/PLAN-autocount-grn-do-ingest-29sep.md. A sibling of
`BillingDocumentIngestService` (same constructor shape, same `ingest()` / `RecordResult`
contract, one SAVEPOINT per record, dry-run rollback), with four differences that come from
where these documents land:

**The record is AutoCount's own.** The shared service forwards the vendor API's header object
with its `Details` rows unmapped; this module reads the PascalCase keys it needs and stores the
whole record as sent in `source_record` (ruling Q1). Unknown keys are kept, never refused.

**Existing tables, so adopt by number.** A DO lands on `orders` / `order_lines` (ruling Q3), a
GRN on `picking_headers` / `picking_lines` (ruling Q4). The tracking upload or the Excel GRN
import may have created the row first; the push adopts it by its document number instead of
duplicating it (Q8 a), keeps its id and every column the upload owns (plan section 3), and
matches its old lines to the incoming ones so their ids (and a GRN line's SPO or PO link)
survive.

**Idempotency on the row.** `(company, book, DocKey)` is a partial unique index on the header
table itself; there is no `integration_references` row, because the row can predate the feed.

**Links are resolved, never waited for.** A line names the SO / PO / SPO line it came from
exactly when the vendor sends a real `FromDocDtlKey` (today it sends 0, which names nothing).
Without one, a DO line that names its SO (`FromDocNo`) links to the one line of its product in
that SO (PLAN-do-so-line-link-1oct.md), and its header links by `RefDocNo` (Q10 a). A GRN line
links to ONE line of the PO or SPO its `FromDocNo` (else `OurPONo`) names, by product and
position (PLAN-autocount-grn-pull-crm-02oct.md 1.3: AutoCount writes one GRN line per SPO line
in the SPO's Seq order). A link once made is never unset by a later push that cannot resolve
it. At the end of every real batch, waiting links that now resolve are filled.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Optional

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.autocount_branch import Branch
from app.models.base import company_scope
from app.models.inventory import Warehouse
from app.models.order import Customer, Order, OrderLine, OrderStatus, SalesOrder, SalesOrderLine
from app.models.procurement import (
    PickingHeader,
    PickingLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
)
from app.models.product import Product
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
from app.services.master_ref_resolver import (
    WARN_CUSTOMER_UNRESOLVED,
    MasterRefResolver,
    dedupe_warnings,
)
from app.services.order_field_ownership import assert_autocount_writes

logger = logging.getLogger(__name__)

DELIVERY_ORDERS_ENTITY = "delivery_orders"
GOODS_RECEIVE_NOTES_ENTITY = "goods_receive_notes"
BRANCHES_ENTITY = "branches"
AUTOCOUNT_DOC_ENTITIES = frozenset({DELIVERY_ORDERS_ENTITY, GOODS_RECEIVE_NOTES_ENTITY})
AUTOCOUNT_BRANCH_ENTITIES = frozenset({BRANCHES_ENTITY})

SOURCE_SYSTEM = "autocount"
# The vendor URL's book segment (`/api/db1/...`, ruling V9: db1 is Sorento).
BOOK_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
# Ruling V1: LastModified is Malaysia time. A naive timestamp is read as +08:00.
MALAYSIA = timezone(timedelta(hours=8))
MAX_LINES = 2000
MAX_RECORD_BYTES = 1_000_000
# A branchbypage row is one address record; 32 KB is far above any real one.
MAX_BRANCH_BYTES = 32_000
# Waiting links re-tried per kind at the end of a batch; the rest wait for the next batch.
MAX_WAITING_LINKS = 500

_DOC_PREFIX = {DELIVERY_ORDERS_ENTITY: "DO", GOODS_RECEIVE_NOTES_ENTITY: "GRN"}

# Contract 2.7 warning vocabulary. `customer_unresolved` and `stale_ignored` are the existing ones.
WARN_STALE_IGNORED = "stale_ignored"
WARN_ADOPTED = "adopted_by_doc_no"
WARN_BRANCH_UNRESOLVED = "branch_unresolved"
WARN_LINE_WITHOUT_ITEM = "line_without_item"
WARN_SO_LINE_UNRESOLVED = "so_line_unresolved"
WARN_PO_LINE_UNRESOLVED = "po_line_unresolved"
WARN_SALES_ORDER_UNRESOLVED = "sales_order_unresolved"
WARN_PURCHASE_ORDER_UNRESOLVED = "purchase_order_unresolved"
WARN_LEGACY_LINKS_RELEASED = "legacy_links_released"
WARN_RESTORED = "restored"
# GRN-PULL-CRM plan 1.3: a GRN line that received more than its PO / SPO line had left, and
# one whose product is not on the PO / SPO it names (D3, owner pending).
WARN_OVER_RECEIPT = "over_receipt"
WARN_ITEM_NOT_ON_ORDER = "item_not_on_order"
CONTRACT_2_7_WARNINGS = (
    WARN_ADOPTED,
    WARN_BRANCH_UNRESOLVED,
    WARN_LINE_WITHOUT_ITEM,
    WARN_SO_LINE_UNRESOLVED,
    WARN_PO_LINE_UNRESOLVED,
    WARN_SALES_ORDER_UNRESOLVED,
    WARN_PURCHASE_ORDER_UNRESOLVED,
    WARN_LEGACY_LINKS_RELEASED,
    WARN_RESTORED,
    WARN_OVER_RECEIPT,
    WARN_ITEM_NOT_ON_ORDER,
)

# Columns a push never UNSETS: a link made earlier (by an exact ref, a document number, a
# waiting-link fill, or carried from an adopted Excel line) stays when this push cannot
# resolve one. A push that resolves a DIFFERENT target replaces it.
_DO_HEADER_LINKS = ("sales_order_id",)
_DO_LINE_LINKS = ("sales_order_line_id",)
_GRN_LINE_LINKS = ("po_line_id", "spo_allocation_id", "purchase_order_id", "spo_number_raw")
# Marks line values whose link columns were RESOLVED and are written as given, None included
# (GRN-PULL-CRM review S4); never a column, stripped before a row is built.
_FORCE_LINKS = "_force_links"


def _columns(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if k != _FORCE_LINKS}

_MONEY = Decimal("0.01")
_FOUR = Decimal("0.0001")
_RATE = Decimal("0.00000001")


# ============================================================================ parsing
class _Invalid(Exception):
    """A record the CRM refuses as sent: `failed`, with the field named."""

    def __init__(self, errors: dict[str, str]):
        super().__init__(json.dumps(errors))
        self.errors = errors


class _Retry(Exception):
    """A product or warehouse the record names is not here yet: `retryable`."""

    def __init__(self, errors: dict[str, str]):
        super().__init__(json.dumps(errors))
        self.errors = errors


def _text(value: Any, limit: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    out = str(value).strip()
    if not out:
        return None
    return out[:limit] if limit else out


_INT8_MAX = 2**63 - 1


def _int(value: Any) -> Optional[int]:
    """An integer that fits the BIGINT key columns, else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        value = int(value) if value.is_integer() else None
    elif not isinstance(value, int):
        text = str(value).strip()
        value = int(text) if re.fullmatch(r"-?\d{1,18}", text) else None
    if value is None or not -_INT8_MAX <= value <= _INT8_MAX:
        return None
    return value


def _link_key(value: Any) -> Optional[int]:
    """A `FromDocDtlKey`, or None when it names no line. AutoCount sends 0 on every DO line
    (3,841 of 3,841 in the 01-03 Sep snapshot) and DtlKeys are positive identities."""
    key = _int(value)
    return key if key is not None and key > 0 else None


def _dec(value: Any, exp: Decimal) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("not a number") from None
    if not out.is_finite():
        raise ValueError("not a number")
    return out.quantize(exp, rounding=ROUND_HALF_UP)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _date(value: Any) -> Optional[date]:
    parsed = _parse_dt(value)
    return parsed.date() if parsed is not None else None


def _naive(value: Any) -> Optional[datetime]:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(MALAYSIA).replace(tzinfo=None)
    return parsed


def _modified(value: Any) -> Optional[datetime]:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MALAYSIA)
    return parsed.astimezone(timezone.utc)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {"T", "Y", "1", "TRUE", "YES"}


def _safe_error(exc: Exception) -> str:
    """What to log for an unexpected failure: the type and, for a database error, its
    SQLSTATE and the innermost frame. Never the message: a driver message carries the bound
    parameters, which are the record as sent."""
    code = getattr(getattr(exc, "orig", None), "pgcode", None)
    frame = exc.__traceback__
    while frame is not None and frame.tb_next is not None:
        frame = frame.tb_next
    where = f"{frame.tb_frame.f_code.co_filename.rsplit('/', 1)[-1]}:{frame.tb_lineno}" if frame else "?"
    return f"{type(exc).__name__} pgcode={code} at={where}"


def _joined(*values: Any) -> Optional[str]:
    parts = [str(v).strip() for v in values if v is not None and str(v).strip()]
    return "\n".join(parts) or None


def _field(errors: dict[str, str], key: str, fn, value):
    """`fn(value)`, recording a parse failure under `key` instead of raising."""
    try:
        return fn(value)
    except (ValueError, TypeError):
        errors[key] = f"invalid value {str(value)[:60]!r}"
        return None


@dataclass
class _Line:
    index: int
    raw: dict
    dtl_key: int
    seq: Optional[int]
    item_code: Optional[str]
    location: Optional[str]
    qty: Optional[Decimal]
    from_doc_type: Optional[str]
    from_doc_no: Optional[str]
    from_dtl_key: Optional[int]
    values: dict[str, Any]


@dataclass
class _Doc:
    raw: dict
    doc_key: int
    doc_no: str
    doc_date: date
    modified: Optional[datetime]
    cancelled: bool
    lines: list[_Line]
    values: dict[str, Any]


@dataclass
class _LinkAsk:
    """One GRN line asking for its PO / SPO line (plan 1.3): the ingest builds these from the
    parsed record, the waiting fill from stored rows, and both get the same answer."""
    order: tuple
    product_id: str
    qty: int
    from_doc_type: Optional[str]
    from_doc_no: Optional[str]
    our_po_no: Optional[str]
    from_dtl_key: Optional[int]


@dataclass
class _Candidate:
    """A PO or SPO line a GRN line may take, with what it has left (drawn down in place)."""
    id: str
    available: int


def _choose_candidate(candidates: list[_Candidate], qty: int,
                      used: set[str]) -> tuple[Optional[_Candidate], bool]:
    """D1 (a), in line order among the lines no other line of this GRN took: remaining equal
    to the qty, else enough remaining, else any remaining (over receipt), else the last line
    (over receipt). Returns (candidate or None when there are none, over_receipt)."""
    if not candidates:
        return None, False
    free = [c for c in candidates if c.id not in used] or candidates
    for test, over in ((lambda c: c.available == qty, False),
                       (lambda c: c.available >= qty, False),
                       (lambda c: c.available > 0, True)):
        pick = next((c for c in free if test(c)), None)
        if pick is not None:
            return pick, over
    return free[-1], True


def _dtl_order(source_ref: Optional[str]) -> int:
    """A PO line's AutoCount DtlKey (the last segment of `source_ref`), its only order (D4)."""
    tail = str(source_ref or "").rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else _INT8_MAX


def _source_ref(book: str, entity: str, doc_key: Any) -> Optional[str]:
    key = _int(doc_key)
    return f"{book}:{_DOC_PREFIX[entity]}:{key}" if key is not None else None


def _parse(entity: str, raw: dict) -> _Doc:
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

    header: dict[str, Any] = {
        "ship_via": _text(raw.get("ShipVia"), 100),
        "ship_info": _text(raw.get("ShipInfo"), 255),
        "ref": _text(raw.get("Ref"), 255),
        "ref_doc_no": _text(raw.get("RefDocNo"), 100),
        "description": _text(raw.get("Description")),
        "doc_status": _text(raw.get("DocStatus"), 20),
        "currency_code": _text(raw.get("CurrencyCode"), 10),
        "currency_rate": _field(errors, "CurrencyRate", lambda v: _dec(v, _RATE),
                                raw.get("CurrencyRate")),
        "local_net_total": _field(errors, "LocalNetTotal", lambda v: _dec(v, _MONEY),
                                  raw.get("LocalNetTotal")),
    }
    total = _field(errors, "Total", lambda v: _dec(v, _MONEY), raw.get("Total"))
    tax = _field(errors, "Tax", lambda v: _dec(v, _MONEY), raw.get("Tax"))
    net = _field(errors, "NetTotal", lambda v: _dec(v, _MONEY), raw.get("NetTotal"))
    if net is None and total is not None:
        net = total + (tax or Decimal("0.00"))
    header.update(subtotal_amount=total, tax_amount=tax, total_amount=net)
    remarks = _joined(raw.get("Remark1"), raw.get("Remark2"), raw.get("Remark3"), raw.get("Remark4"))
    if entity == DELIVERY_ORDERS_ENTITY:
        header.update(
            created_time=_field(errors, "CreatedTimeStamp", _naive, raw.get("CreatedTimeStamp")),
            debtor_code=_text(raw.get("DebtorCode"), 100),
            debtor_name=_text(raw.get("DebtorName"), 255),
            branch_code=_text(raw.get("BranchCode"), 100),
            deliver_address=_joined(raw.get("DeliverAddr1"), raw.get("DeliverAddr2"),
                                    raw.get("DeliverAddr3"), raw.get("DeliverAddr4")),
            deliver_contact=_text(raw.get("DeliverContact"), 255),
            deliver_phone=_text(raw.get("DeliverPhone1"), 100),
            agent=_text(raw.get("SalesAgent"), 100),
            remarks=remarks,
        )
    else:
        header.update(
            creditor_code=_text(raw.get("CreditorCode"), 100),
            creditor_name=_text(raw.get("CreditorName"), 255),
            supplier_do_no=_text(raw.get("SupplierDONo"), 100),
            purchase_agent=_text(raw.get("PurchaseAgent"), 100),
            remarks=remarks,
        )

    details = raw.get("Details")
    if details is None:
        details = []
    lines: list[_Line] = []
    if not isinstance(details, list):
        errors["Details"] = "must be an array"
    elif len(details) > MAX_LINES:
        errors["Details"] = f"more than {MAX_LINES} lines"
    else:
        seen_dtl: set[int] = set()
        seen_seq: set[int] = set()
        for index, row in enumerate(details):
            prefix = f"Details.{index}"
            if not isinstance(row, dict):
                errors[prefix] = "must be an object"
                continue
            dtl_key = _int(row.get("DtlKey"))
            if dtl_key is None:
                errors[f"{prefix}.DtlKey"] = "required, an integer"
                continue
            if dtl_key in seen_dtl:
                errors["Details"] = f"DtlKey {dtl_key} appears twice"
            seen_dtl.add(dtl_key)
            item_code = _text(row.get("ItemCode"), 100)
            seq = _int(row.get("Seq"))
            if item_code is not None and seq is not None:
                if seq in seen_seq:
                    errors["Details"] = f"Seq {seq} appears twice"
                seen_seq.add(seq)
            qty = _field(errors, f"{prefix}.Qty", lambda v: _dec(v, _FOUR), row.get("Qty"))
            values = {
                "item_code": item_code,
                "location_code": _text(row.get("Location"), 50),
                "description": _text(row.get("Description")),
                "foc_qty": _field(errors, f"{prefix}.FOCQty", lambda v: _dec(v, _FOUR),
                                  row.get("FOCQty")),
                "discount_text": _text(row.get("Discount"), 50),
                "delivery_date": _field(errors, f"{prefix}.DeliveryDate", _date,
                                        row.get("DeliveryDate")),
                "proj_no": _text(row.get("ProjNo"), 50),
                "dtl_key": dtl_key,
            }
            unit_price = _field(errors, f"{prefix}.UnitPrice", lambda v: _dec(v, _FOUR),
                                row.get("UnitPrice"))
            discount_amt = _field(errors, f"{prefix}.DiscountAmt", lambda v: _dec(v, _FOUR),
                                  row.get("DiscountAmt"))
            sub_total = _field(errors, f"{prefix}.SubTotal", lambda v: _dec(v, _FOUR),
                               row.get("SubTotal"))
            line_tax = _field(errors, f"{prefix}.Tax", lambda v: _dec(v, _FOUR), row.get("Tax"))
            if entity == DELIVERY_ORDERS_ENTITY:
                values.update(
                    uom=_text(row.get("UOM"), 30),
                    quantity=qty if qty is not None else Decimal("0.0000"),
                    unit_price=unit_price,
                    discount=discount_amt,
                    total=sub_total,
                    tax=line_tax,
                    batch_no=_text(row.get("BatchNo"), 100),
                    your_po_no=_text(row.get("YourPONo"), 100),
                    your_po_date=_field(errors, f"{prefix}.YourPODate", _date,
                                        row.get("YourPODate")),
                )
            else:
                rounded = int((qty or Decimal("0")).to_integral_value(rounding=ROUND_HALF_UP))
                values.update(
                    uom_code=_text(row.get("UOM"), 30),
                    seq=seq,
                    qty=qty,
                    quantity_picked=rounded,
                    quantity_expected=rounded,
                    unit_cost=unit_price.quantize(_MONEY, rounding=ROUND_HALF_UP)
                    if unit_price is not None else None,
                    line_total=sub_total.quantize(_MONEY, rounding=ROUND_HALF_UP)
                    if sub_total is not None else None,
                    discount_amount=discount_amt.quantize(_MONEY, rounding=ROUND_HALF_UP)
                    if discount_amt is not None else None,
                    tax_amount=line_tax.quantize(_MONEY, rounding=ROUND_HALF_UP)
                    if line_tax is not None else None,
                    batch_number_picked=_text(row.get("BatchNo"), 100),
                    our_po_no=_text(row.get("OurPONo"), 100),
                    our_po_date=_field(errors, f"{prefix}.OurPODate", _date,
                                       row.get("OurPODate")),
                )
            lines.append(
                _Line(
                    index=index,
                    raw=row,
                    dtl_key=dtl_key,
                    seq=seq,
                    item_code=item_code,
                    location=values["location_code"],
                    qty=qty,
                    from_doc_type=_text(row.get("FromDocType"), 10),
                    from_doc_no=_text(row.get("FromDocNo"), 100),
                    from_dtl_key=_link_key(row.get("FromDocDtlKey")),
                    values=values,
                )
            )
    if errors:
        raise _Invalid(errors)
    return _Doc(
        raw=raw,
        doc_key=doc_key,
        doc_no=doc_no,
        doc_date=doc_date,
        modified=modified,
        cancelled=_truthy(raw.get("Cancelled")),
        lines=lines,
        values=header,
    )


def run_grn_receipt_hook(db: Session, service: "AutocountDocIngestService") -> None:
    """An AutoCount GRN write moved picking lines that point at SPO allocations (a carried
    Excel link released, a cancel, an exact or line-order link): recompute those
    allocations' receipt the way an approved Excel GRN does. After the batch commit, best
    effort. One function for the push route and the pull's apply (GRN-PULL-CRM)."""
    touched = getattr(service, "touched_allocation_ids", set())
    if not touched:
        return
    try:
        from app.services.procurement_service import InboundShipmentService, PickingHeaderService

        allocations = db.query(SPOAllocation).filter(SPOAllocation.id.in_(list(touched))).all()
        proc = PickingHeaderService(db)
        shipment_ids = proc._sync_received_for_allocations(
            allocations, released=getattr(service, "released_allocation_ids", set())
        )
        db.commit()
        inbound = InboundShipmentService(db)
        for shipment_id in shipment_ids:
            inbound.refresh_shipment_line_statuses(shipment_id)
    except Exception:  # noqa: BLE001 - the ingest itself already committed
        db.rollback()
        logger.warning("ingest.grn_receipt_hook_failed", exc_info=True)


def link_waiting_grn_lines(db: Session, *, company_id: str, numbers: set[str]) -> None:
    """When a PO or SPO arrives (the shipping-order ingest's post-commit hook), link the
    AutoCount GRN lines that were waiting for it, at once, through the same resolver the GRN
    ingest uses (crew e2e gap 1: GR-2026/10-0006 landed before SPO-2026/09-0115). Then the
    receipt of every SPO line it linked is recomputed. Best effort after the caller's own
    commit: a failure is logged and the next GRN batch's fill retries."""
    if not company_id or not numbers:
        return
    service = AutocountDocIngestService(db, None, company_id=str(company_id), book="")
    try:
        with company_scope(db, frozenset({str(company_id)})):
            service._link_waiting_grn_lines(set(numbers))
        db.commit()
    except Exception:  # noqa: BLE001 - the document itself already committed
        db.rollback()
        logger.warning("ingest.grn_waiting_link_failed", exc_info=True)
        return
    run_grn_receipt_hook(db, service)


# ============================================================================ the service
@dataclass
class _Verdict:
    outcome: IngestOutcome
    entity_id: Optional[str]
    warnings: list[str]
    lines: dict[str, int] = field(default_factory=dict)


def _line_counts() -> dict[str, int]:
    return {"created": 0, "updated": 0, "deleted": 0, "adopted": 0, "skipped": 0,
            "linked": 0, "unlinked": 0}


class AutocountDocIngestService(MasterRefResolver):
    """`delivery_orders`, `goods_receive_notes` and `branches` for one company and one book.

    Subclasses `MasterRefResolver` for its anchor-scoped code lookups (`_resolve_by_code`)
    and its memo, never for its back-creating rungs: nothing here creates a master.
    """

    def __init__(
        self, db: Session, integration_id: Optional[str], *, company_id: str, book: str
    ):
        super().__init__(db, integration_id, company_id=company_id)
        self.book = book
        # SPO allocations whose receipt a GRN write moved, for the route's post-commit hook.
        self.touched_allocation_ids: set[str] = set()
        self.released_allocation_ids: set[str] = set()
        self.live_records: list[RecordResult] = []

    #: How often `on_progress` fires mid-batch - the same cadence `MasterIngestService`
    #: uses (B3): a pull snapshot can run to thousands of documents.
    PROGRESS_REPORT_EVERY = 100

    # ------------------------------------------------------------------ the batch
    def ingest(
        self,
        entity_type: str,
        records: list[Any],
        *,
        dry_run: bool = False,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> IngestResult:
        """``on_progress`` (DO-PULL-CRM, the pull preview's "N of M"): called with
        ``(processed, total)`` every `PROGRESS_REPORT_EVERY` records and once more at the
        end with ``(total, total)``. Best-effort: a failing callback is logged, never
        raised, and the push path (no callback) is untouched."""
        if entity_type not in AUTOCOUNT_DOC_ENTITIES | AUTOCOUNT_BRANCH_ENTITIES:
            raise UnsupportedIngestEntity(f"Unsupported AutoCount entity {entity_type!r}")
        result = IngestResult(dry_run=dry_run)
        # The batch's records so far, for an `on_progress` callback that tallies outcomes
        # mid-run (DO-APPLY-PROGRESS); the callback signature stays `(processed, total)`.
        self.live_records = result.records
        total = len(records)
        try:
            for index, raw in enumerate(records, start=1):
                if entity_type == BRANCHES_ENTITY:
                    result.records.append(self._ingest_branch(raw))
                else:
                    result.records.append(self._ingest_one(entity_type, raw))
                if on_progress is not None and index % self.PROGRESS_REPORT_EVERY == 0:
                    self._report_progress(on_progress, index, total)
            if not dry_run and entity_type in AUTOCOUNT_DOC_ENTITIES:
                # Best effort in its own savepoint: a failure here must not roll back the
                # records that already landed; the next batch retries it.
                savepoint = self.db.begin_nested()
                try:
                    with company_scope(self.db, frozenset({self.company_id})):
                        self._fill_waiting_links(entity_type)
                    savepoint.commit()
                except Exception as exc:  # noqa: BLE001
                    savepoint.rollback()
                    logger.warning("ingest.waiting_links_failed entity=%s error=%s",
                                   entity_type, _safe_error(exc))
        finally:
            if dry_run:
                self.db.rollback()
        if on_progress is not None:
            self._report_progress(on_progress, total, total)
        unlinked = sum((r.lines or {}).get("unlinked", 0) for r in result.records)
        if unlinked and not dry_run:
            logger.info(
                "ingest.unlinked_lines entity=%s company=%s count=%d",
                entity_type, self.company_id, unlinked,
            )
        return result

    @staticmethod
    def _report_progress(on_progress: Callable[[int, int], None], processed: int, total: int) -> None:
        try:
            on_progress(processed, total)
        except Exception:  # pragma: no cover - defensive by design
            logger.warning("ingest progress callback failed", exc_info=True)

    def _ingest_one(self, entity: str, raw: Any) -> RecordResult:
        source_ref = _source_ref(self.book, entity, raw.get("DocKey")) if isinstance(raw, dict) else None
        if not isinstance(raw, dict):
            return RecordResult(source_ref=None, outcome=IngestOutcome.FAILED,
                                errors={"record": "must be an object"})
        try:
            size = len(json.dumps(raw, default=str))
        except (TypeError, ValueError):
            size = MAX_RECORD_BYTES + 1
        if size > MAX_RECORD_BYTES:
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors={"record": f"larger than {MAX_RECORD_BYTES} bytes"})
        try:
            doc = _parse(entity, raw)
        except _Invalid as exc:
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors=exc.errors)

        savepoint = self.db.begin_nested()
        try:
            with company_scope(self.db, frozenset({self.company_id})):
                verdict = (
                    self._apply_do(doc) if entity == DELIVERY_ORDERS_ENTITY else self._apply_grn(doc)
                )
            savepoint.commit()
            return RecordResult(
                source_ref=source_ref,
                outcome=verdict.outcome,
                entity_id=verdict.entity_id,
                warnings=dedupe_warnings(verdict.warnings),
                lines=verdict.lines,
            )
        except _Retry as exc:
            savepoint.rollback()
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.RETRYABLE,
                                errors=exc.errors)
        except _Invalid as exc:
            savepoint.rollback()
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors=exc.errors)
        except IntegrityError as exc:
            savepoint.rollback()
            # Never `exc_info`: the driver message carries the bound parameters, i.e. the
            # delivery address and the record as sent (ingest.py `_log_record_outcomes` rule).
            logger.warning("ingest.integrity_conflict entity=%s source_ref=%s errors=%s", entity,
                           source_ref, json.dumps(integrity_conflict_errors(exc)))
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors=integrity_conflict_errors(exc))
        except Exception as exc:  # noqa: BLE001 - one document's failure, not the batch's
            savepoint.rollback()
            logger.warning("ingest.document_failed entity=%s source_ref=%s error=%s", entity,
                           source_ref, _safe_error(exc))
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors={"_": INTERNAL_ERROR_MESSAGE})

    # ------------------------------------------------------------- shared steps
    def _find(self, model, number_column, doc: _Doc, extra_filters=()):
        """(row, adopted). The row keyed by (book, DocKey), else the unowned row with the
        same document number, adopted. A number held by another DocKey fails the record."""
        row = (
            self.db.query(model)
            .filter(model.company_id == self.company_id, model.source_book == self.book,
                    model.doc_key == doc.doc_key, *extra_filters)
            .one_or_none()
        )
        by_number = (
            self.db.query(model)
            .filter(model.company_id == self.company_id, number_column == doc.doc_no,
                    *extra_filters)
            .one_or_none()
        )
        if row is not None:
            if by_number is not None and by_number.id != row.id:
                raise _Invalid({"DocNo": f"{doc.doc_no!r} is already another document's number"})
            return row, False
        if by_number is None:
            return None, False
        if by_number.doc_key is not None:
            raise _Invalid({"DocNo": f"{doc.doc_no!r} is already another AutoCount document's "
                                     f"number (DocKey {by_number.doc_key})"})
        return by_number, True

    def _is_stale(self, existing, doc: _Doc, adopted: bool) -> bool:
        return (
            existing is not None
            and not adopted
            and existing.source_modified_at is not None
            and doc.modified is not None
            and doc.modified < existing.source_modified_at
        )

    def _identity(self, doc: _Doc) -> dict[str, Any]:
        return {
            "source_book": self.book,
            "doc_key": doc.doc_key,
            "source_modified_at": doc.modified,
            "source_vanished_at": None,
            "source_record": doc.raw,
        }

    def _resolve_items(self, doc: _Doc, warnings: list[str], *, warehouse_required: bool):
        """(product_id, warehouse_id) per written line, or `_Retry` naming every missing code.
        A Details row with no ItemCode is not a line (kept in `source_record`)."""
        missing: dict[str, str] = {}
        resolved: dict[int, tuple[str, Optional[str]]] = {}
        for line in doc.lines:
            if line.item_code is None:
                continue
            product_id = self._resolve_by_code(Product, line.item_code)
            if product_id is None:
                missing[f"Details.{line.index}.ItemCode"] = f"product {line.item_code!r} not found"
            warehouse_id = None
            if line.location is not None:
                warehouse_id = self._resolve_by_code(Warehouse, line.location)
                if warehouse_id is None:
                    missing[f"Details.{line.index}.Location"] = (
                        f"warehouse {line.location!r} not found"
                    )
            elif warehouse_required:
                missing[f"Details.{line.index}.Location"] = "required on a delivery order line"
            resolved[line.index] = (product_id, warehouse_id)
        if missing:
            raise _Retry(missing)
        if any(line.item_code is None for line in doc.lines):
            warnings.append(WARN_LINE_WITHOUT_ITEM)
        return resolved

    @staticmethod
    def _changes(row, values: dict[str, Any], keep_links: tuple[str, ...]) -> dict[str, Any]:
        out = {}
        forced = bool(values.get(_FORCE_LINKS))
        for key, value in values.items():
            if key == _FORCE_LINKS:
                continue
            if key in keep_links and value is None and not forced:
                continue
            if getattr(row, key) != value:
                out[key] = value
        return out

    def _customer(self, code: Optional[str], warnings: list[str]) -> Optional[str]:
        if not code:
            return None
        entity_id = self._resolve_by_code(Customer, code)
        if entity_id is None:
            warnings.append(WARN_CUSTOMER_UNRESOLVED)
        return entity_id

    def _branch_name(self, debtor_code: Optional[str], branch_code: Optional[str],
                     warnings: list[str]) -> Optional[str]:
        if not branch_code:
            return None
        rows = (
            self.db.query(Branch.acc_no, Branch.branch_name)
            .filter(Branch.company_id == self.company_id, Branch.source_book == self.book,
                    Branch.branch_code == branch_code)
            .all()
        )
        exact = [r for r in rows if debtor_code and r.acc_no == debtor_code]
        if len(exact) == 1:
            return exact[0].branch_name
        if len(rows) == 1:
            return rows[0].branch_name
        warnings.append(WARN_BRANCH_UNRESOLVED)
        return None

    def _new_status_id(self) -> Optional[str]:
        key = ("order_statuses", "code", "new")
        if key not in self._memo:
            row = (
                self.db.query(OrderStatus.id)
                .filter(OrderStatus.status_code.ilike("new"))
                .first()
            )
            self._memo[key] = str(row[0]) if row else None
        return self._memo[key]

    # --------------------------------------------------------------- link lookups
    @staticmethod
    def _ref_matches(column, dtl_key: int):
        return or_(column == str(dtl_key), column.like(f"%:{dtl_key}"))

    def _so_line(self, dtl_key: int, doc_no: Optional[str]) -> Optional[str]:
        query = (
            self.db.query(SalesOrderLine.id)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.company_id == self.company_id,
                    self._ref_matches(SalesOrderLine.source_ref, dtl_key))
        )
        if doc_no:
            query = query.filter(SalesOrder.so_number == doc_no)
        rows = query.limit(2).all()
        return str(rows[0][0]) if len(rows) == 1 else None

    def _do_so_line(self, dtl_key: Optional[int], doc_no: Optional[str],
                    product_id: Optional[str]) -> Optional[str]:
        """The SO line a DO line came from: by its exact DtlKey when AutoCount sends one,
        else the one line of this product inside the SO numbered `FromDocNo`. Several lines
        of the product in that SO resolve to none: no guess by position, because SO lines
        carry no `line_no` yet (#1400)."""
        if dtl_key is not None:
            return self._so_line(dtl_key, doc_no)
        if not doc_no or not product_id:
            return None
        rows = (
            self.db.query(SalesOrderLine.id)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.company_id == self.company_id,
                    SalesOrder.so_number == doc_no,
                    SalesOrderLine.product_id == product_id)
            .limit(2)
            .all()
        )
        return str(rows[0][0]) if len(rows) == 1 else None

    def _po_or_spo_line(self, dtl_key: int, doc_no: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """(po_line_id, spo_allocation_id): exactly one match across both tables, else both
        None. SPO and PO share one AutoCount table, so one DtlKey resolves against either."""
        po_query = (
            self.db.query(PurchaseOrderLine.id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .filter(PurchaseOrderLine.company_id == self.company_id,
                    self._ref_matches(PurchaseOrderLine.source_ref, dtl_key))
        )
        spo_query = self.db.query(SPOAllocation.id).filter(
            SPOAllocation.company_id == self.company_id,
            self._ref_matches(SPOAllocation.source_ref, dtl_key),
        )
        if doc_no:
            po_query = po_query.filter(PurchaseOrder.po_number == doc_no)
            spo_query = spo_query.filter(SPOAllocation.spo_number == doc_no)
        po_rows = po_query.limit(2).all()
        spo_rows = spo_query.limit(2).all()
        if len(po_rows) + len(spo_rows) != 1:
            return None, None
        if po_rows:
            return str(po_rows[0][0]), None
        return None, str(spo_rows[0][0])

    def _sales_order(self, so_number: Optional[str]) -> Optional[str]:
        if not so_number:
            return None
        rows = (
            self.db.query(SalesOrder.id)
            .filter(SalesOrder.company_id == self.company_id, SalesOrder.so_number == so_number)
            .limit(2)
            .all()
        )
        return str(rows[0][0]) if len(rows) == 1 else None

    # ------------------------------------------------------------ GRN line -> PO / SPO line
    def _grn_links(self, asks: list[_LinkAsk], warnings: list[str], *,
                   exclude_header_id: Optional[str],
                   used: Optional[set[str]] = None) -> list[dict[str, Any]]:
        """The link columns for each ask, in the asks' own order (plan 1.3). Lines are
        resolved in GRN Seq order; each line of a product takes a DIFFERENT line of the
        named PO / SPO while unused ones remain (`used` may arrive pre-filled with what
        sibling lines already hold). `exclude_header_id` is this GRN, so a re-push never
        competes with its own earlier receipt."""
        used = set(used or ())
        pools: dict[tuple[str, str, str], list[_Candidate]] = {}
        out: list[dict[str, Any]] = [{} for _ in asks]
        for position in sorted(range(len(asks)), key=lambda i: asks[i].order):
            ask = asks[position]
            if ask.from_dtl_key is not None:
                po_line, spo_line = self._po_or_spo_line(ask.from_dtl_key, ask.from_doc_no)
                out[position] = {"po_line_id": po_line, "spo_allocation_id": spo_line}
                if po_line is None and spo_line is None:
                    warnings.append(WARN_PO_LINE_UNRESOLVED)
                else:
                    out[position][_FORCE_LINKS] = True
                continue
            # Q3 a: FromDocNo is AutoCount's own; OurPONo is hand typed.
            source = ask.from_doc_no or ask.our_po_no
            if not source:
                continue
            kind, target = self._grn_source(source)
            if kind is None:
                warnings.append(WARN_PURCHASE_ORDER_UNRESOLVED)
                continue
            # A resolved document writes the WHOLE link set (review S4): a re-push that moves a
            # line from a PO to an SPO, or back, must clear the other kind's columns, which
            # `keep_links` would otherwise keep.
            link = {"from_doc_type": ask.from_doc_type or kind,
                    "from_doc_no": ask.from_doc_no or source,
                    "po_line_id": None, "spo_allocation_id": None,
                    "purchase_order_id": None, "spo_number_raw": None,
                    _FORCE_LINKS: True}
            if kind == "PO":
                link["purchase_order_id"] = target
            else:
                link["spo_number_raw"] = source
            key = (kind, target, str(ask.product_id))
            if key not in pools:
                pools[key] = self._grn_candidates(kind, target, ask.product_id,
                                                  exclude_header_id)
            pick, over = _choose_candidate(pools[key], ask.qty, used)
            if pick is None:
                self._unmatched_item(link, warnings)
            else:
                used.add(pick.id)
                pick.available -= ask.qty
                link["po_line_id" if kind == "PO" else "spo_allocation_id"] = pick.id
                if over:
                    warnings.append(WARN_OVER_RECEIPT)
            out[position] = link
        return out

    @staticmethod
    def _unmatched_item(link: dict[str, Any], warnings: list[str]) -> None:
        """D3 (owner pending, built as (a)): the product is not on the named PO / SPO. The
        line keeps the document link already in `link` (`purchase_order_id`, or
        `spo_number_raw` for an SPO, which has no header table) and no line link. One
        function, so the owner's answer changes one place."""
        warnings.append(WARN_ITEM_NOT_ON_ORDER)

    def _grn_source(self, number: str) -> tuple[Optional[str], Optional[str]]:
        """("SPO", the number) or ("PO", purchase_order_id) or (None, None). The number's
        table decides, never `FromDocType`: AutoCount sends 'PO' on SPO lines too (crew,
        2 Oct). SPO is asked first (plan 1.3 rule 3): an SPO the CRM raised itself is a
        `purchase_orders` row AND `spo_allocations` lines under one number
        (`spo_conversion_service`), and its receipt belongs on the SPO line. A PO number held
        twice resolves to nothing (no guess)."""
        from app.services.grn_spo_matching import spo_number_known

        key = ("grn_source", number)
        if key not in self._memo:
            if spo_number_known(self.db, number, company_id=self.company_id):
                self._memo[key] = ("SPO", number)
            else:
                rows = (
                    self.db.query(PurchaseOrder.id)
                    .filter(PurchaseOrder.company_id == self.company_id,
                            PurchaseOrder.po_number == number)
                    .limit(2)
                    .all()
                )
                self._memo[key] = ("PO", str(rows[0][0])) if len(rows) == 1 else (None, None)
        return self._memo[key]

    def _grn_candidates(self, kind: str, target: str, product_id: str,
                        exclude_header_id: Optional[str]) -> list[_Candidate]:
        """The named document's lines of this product, in line order, with what each has left
        after OTHER GRNs (rejected and cancelled ones hold nothing, Q4 a)."""
        from app.services.grn_spo_matching import NON_CONSUMING_GRN_STATUSES, spo_line_candidates

        exclude = {exclude_header_id} if exclude_header_id else set()
        if kind == "SPO":
            return [
                _Candidate(id=entry.allocation_id, available=entry.available)
                for entry in spo_line_candidates(
                    self.db, product_id=str(product_id), spo_number=target,
                    exclude_header_ids=exclude, company_id=self.company_id)
            ]
        rows = (
            self.db.query(PurchaseOrderLine.id, PurchaseOrderLine.qty_ordered,
                          PurchaseOrderLine.source_ref)
            .filter(PurchaseOrderLine.company_id == self.company_id,
                    PurchaseOrderLine.purchase_order_id == target,
                    PurchaseOrderLine.product_id == str(product_id))
            .all()
        )
        if not rows:
            return []
        drawn = func.coalesce(func.sum(PickingLine.quantity_picked), 0)
        consumed_query = (
            self.db.query(PickingLine.po_line_id, drawn)
            .join(PickingHeader, PickingHeader.id == PickingLine.picking_header_id)
            .filter(PickingLine.company_id == self.company_id,
                    PickingLine.po_line_id.in_([str(r[0]) for r in rows]),
                    PickingHeader.picking_type == "goods_received",
                    PickingHeader.picking_status.notin_(NON_CONSUMING_GRN_STATUSES))
            .group_by(PickingLine.po_line_id)
        )
        if exclude:
            consumed_query = consumed_query.filter(PickingLine.picking_header_id.notin_(exclude))
        consumed = {str(r[0]): int(r[1]) for r in consumed_query.all()}
        rows = sorted(rows, key=lambda r: (_dtl_order(r[2]), str(r[0])))
        return [
            _Candidate(id=str(r[0]),
                       available=max(0, int(r[1] or 0) - consumed.get(str(r[0]), 0)))
            for r in rows
        ]

    # ================================================================== delivery orders
    def _apply_do(self, doc: _Doc) -> _Verdict:
        existing, adopted = self._find(Order, Order.order_number, doc)
        if self._is_stale(existing, doc, adopted):
            return _Verdict(IngestOutcome.UNCHANGED, str(existing.id), [WARN_STALE_IGNORED])

        warnings: list[str] = []
        resolved = self._resolve_items(doc, warnings, warehouse_required=True)
        header = dict(doc.values)
        header.update(self._identity(doc))
        header.update(
            order_number=doc.doc_no,
            order_date=doc.doc_date,
            is_cancelled=doc.cancelled,
            customer_id=self._customer(header["debtor_code"], warnings),
            branch_name=self._branch_name(header["debtor_code"], header["branch_code"], warnings),
            sales_order_id=self._sales_order(header["ref_doc_no"]),
        )
        # `orders` money columns are NOT NULL with a 0 default.
        for key in ("subtotal_amount", "tax_amount", "total_amount"):
            if header[key] is None:
                header[key] = Decimal("0.00")
        # Order Tracking owns every other column (DO-OWNERSHIP-GUARD); never write one.
        assert_autocount_writes(header)
        if header["ref_doc_no"] and header["sales_order_id"] is None:
            warnings.append(WARN_SALES_ORDER_UNRESOLVED)

        # A line with no Seq takes a sequence past every explicit one, so it cannot collide
        # with (order_id, line_sequence).
        top = max((l.seq for l in doc.lines if l.seq is not None and l.item_code), default=0)
        default_seq = {}
        for line in doc.lines:
            if line.seq is None and line.item_code is not None:
                top += 1
                default_seq[line.index] = top

        lines: list[dict[str, Any]] = []
        for line in doc.lines:
            if line.item_code is None:
                continue
            product_id, warehouse_id = resolved[line.index]
            values = dict(line.values)
            values.update(
                line_sequence=line.seq if line.seq is not None else default_seq[line.index],
                product_id=product_id,
                warehouse_id=warehouse_id,
                from_doc_type=line.from_doc_type,
                from_doc_no=line.from_doc_no,
                from_dtl_key=line.from_dtl_key,
                sales_order_line_id=None,
            )
            if ((line.from_dtl_key is not None or line.from_doc_no)
                    and line.from_doc_type in (None, "SO")):
                values["sales_order_line_id"] = self._do_so_line(
                    line.from_dtl_key, line.from_doc_no, product_id)
                if values["sales_order_line_id"] is None:
                    warnings.append(WARN_SO_LINE_UNRESOLVED)
            lines.append(values)

        counts = _line_counts()
        counts["skipped"] = sum(1 for line in doc.lines if line.item_code is None)
        if existing is None:
            order = Order(company_id=self.company_id, order_status_id=self._new_status_id(),
                          last_synced_at=datetime.utcnow(), **header)
            self.db.add(order)
            self.db.flush()
            for values in lines:
                self.db.add(OrderLine(company_id=self.company_id, order_id=order.id, **values))
            self.db.flush()
            counts["created"] = len(lines)
            self._count_links(counts, order.lines, "sales_order_line_id")
            return _Verdict(IngestOutcome.CREATED, str(order.id), warnings, counts)

        if adopted:
            warnings.append(WARN_ADOPTED)
        if existing.source_vanished_at is not None:
            warnings.append(WARN_RESTORED)
        changed = self._write_lines(
            existing, list(existing.lines), lines, counts,
            seq_column="line_sequence",
            legacy_key=lambda row: (str(row.product_id), str(row.warehouse_id), row.quantity),
            incoming_key=lambda v: (str(v["product_id"]), str(v["warehouse_id"]), v["quantity"]),
            make=lambda values: OrderLine(company_id=self.company_id, order_id=existing.id,
                                          **values),
            attach=existing.lines,
            keep_links=_DO_LINE_LINKS,
        )
        header_changes = self._changes(existing, header, _DO_HEADER_LINKS)
        if not header_changes and not changed:
            self._count_links(counts, existing.lines, "sales_order_line_id")
            return _Verdict(IngestOutcome.UNCHANGED, str(existing.id), warnings, counts)
        for key, value in header_changes.items():
            setattr(existing, key, value)
        existing.last_synced_at = datetime.utcnow()
        self.db.flush()
        self.db.expire(existing, ["lines"])
        self._count_links(counts, existing.lines, "sales_order_line_id")
        return _Verdict(IngestOutcome.UPDATED, str(existing.id), warnings, counts)

    # ================================================================== goods receive notes
    def _apply_grn(self, doc: _Doc) -> _Verdict:
        goods_received = (PickingHeader.picking_type == "goods_received",)
        existing, adopted = self._find(PickingHeader, PickingHeader.picking_number, doc,
                                       goods_received)
        if self._is_stale(existing, doc, adopted):
            return _Verdict(IngestOutcome.UNCHANGED, str(existing.id), [WARN_STALE_IGNORED])

        warnings: list[str] = []
        resolved = self._resolve_items(doc, warnings, warehouse_required=False)
        header = dict(doc.values)
        header.update(self._identity(doc))
        header.update(
            picking_number=doc.doc_no,
            picking_date=doc.doc_date,
            is_cancelled=doc.cancelled,
            picking_status="cancelled" if doc.cancelled else "approved",
        )

        lines: list[dict[str, Any]] = []
        asks: list[_LinkAsk] = []
        for line in doc.lines:
            if line.item_code is None:
                continue
            product_id, warehouse_id = resolved[line.index]
            values = dict(line.values)
            values.update(
                product_id=product_id,
                destination_warehouse_id=warehouse_id,
                from_doc_type=line.from_doc_type,
                from_doc_no=line.from_doc_no,
                from_dtl_key=line.from_dtl_key,
                po_line_id=None,
                spo_allocation_id=None,
                purchase_order_id=None,
                spo_number_raw=None,
            )
            lines.append(values)
            asks.append(_LinkAsk(
                order=(line.seq is None, line.seq or 0, line.index),
                product_id=product_id, qty=values["quantity_picked"],
                from_doc_type=line.from_doc_type, from_doc_no=line.from_doc_no,
                our_po_no=values["our_po_no"], from_dtl_key=line.from_dtl_key,
            ))
        links = self._grn_links(asks, warnings,
                                exclude_header_id=str(existing.id) if existing is not None else None)
        for values, link in zip(lines, links):
            values.update(link)

        counts = _line_counts()
        counts["skipped"] = sum(1 for line in doc.lines if line.item_code is None)
        if existing is None:
            grn = PickingHeader(company_id=self.company_id, picking_type="goods_received",
                                inspection_status="pending", source_system=SOURCE_SYSTEM,
                                last_synced_at=datetime.utcnow(), **header)
            self.db.add(grn)
            self.db.flush()
            for values in lines:
                self.db.add(PickingLine(company_id=self.company_id, picking_header_id=grn.id,
                                        **_columns(values)))
                if values["spo_allocation_id"]:
                    self.touched_allocation_ids.add(values["spo_allocation_id"])
            self.db.flush()
            counts["created"] = len(lines)
            self.db.expire(grn, ["picking_lines"])
            self._count_links(counts, grn.picking_lines, "po_line_id", "spo_allocation_id")
            return _Verdict(IngestOutcome.CREATED, str(grn.id), warnings, counts)

        if adopted:
            warnings.append(WARN_ADOPTED)
        if existing.source_vanished_at is not None:
            warnings.append(WARN_RESTORED)
        stored = list(existing.picking_lines)
        # A link this write moves leaves its old SPO line's receipt to recompute.
        held_before = {str(row.spo_allocation_id) for row in stored if row.spo_allocation_id}
        self.touched_allocation_ids.update(held_before)
        changed = self._write_lines(
            existing, stored, lines, counts,
            seq_column=None,
            legacy_key=lambda row: (str(row.product_id), row.quantity_picked),
            incoming_key=lambda v: (str(v["product_id"]), v["quantity_picked"]),
            make=lambda values: PickingLine(company_id=self.company_id,
                                            picking_header_id=existing.id, **_columns(values)),
            attach=None,
            keep_links=_GRN_LINE_LINKS,
            on_removed=lambda row: self._release(row, warnings),
            merge=(lambda row: str(row.product_id), lambda row: row.quantity_picked or 0,
                   lambda v: str(v["product_id"]), lambda v: v["quantity_picked"] or 0),
        )
        header_changes = self._changes(existing, header, ())
        if not header_changes and not changed:
            self._count_links(counts, existing.picking_lines, "po_line_id", "spo_allocation_id")
            return _Verdict(IngestOutcome.UNCHANGED, str(existing.id), warnings, counts)
        for key, value in header_changes.items():
            setattr(existing, key, value)
        existing.last_synced_at = datetime.utcnow()
        self.db.flush()
        self.db.expire(existing, ["picking_lines"])
        held_after = set()
        for row in existing.picking_lines:
            if row.spo_allocation_id:
                held_after.add(str(row.spo_allocation_id))
                self.touched_allocation_ids.add(str(row.spo_allocation_id))
        # An SPO line this GRN no longer points at gives its receipt back: the recompute only
        # writes a line it is told was released or that something still picks against.
        self.released_allocation_ids.update(held_before - held_after)
        self._count_links(counts, existing.picking_lines, "po_line_id", "spo_allocation_id")
        return _Verdict(IngestOutcome.UPDATED, str(existing.id), warnings, counts)

    def _release(self, row: PickingLine, warnings: list[str]) -> None:
        if row.spo_allocation_id:
            self.released_allocation_ids.add(str(row.spo_allocation_id))
            self.touched_allocation_ids.add(str(row.spo_allocation_id))
        if (row.spo_allocation_id or row.po_line_id) and row.dtl_key is None:
            warnings.append(WARN_LEGACY_LINKS_RELEASED)

    # ------------------------------------------------------------------ lines
    def _write_lines(self, header, stored, incoming, counts, *, seq_column, legacy_key,
                     incoming_key, make, attach, keep_links, on_removed=None, merge=None) -> bool:
        """Apply the incoming line set to a stored document. Returns whether anything changed.

        Stored lines with a `dtl_key` match by it. Lines without one (an adopted upload's)
        are claimed one to one by `legacy_key == incoming_key`, first fit, and keep their id.
        With `merge` (`(row_group, row_qty, values_group, values_qty)`, GRN only, D2 a): an
        incoming line still unclaimed then claims the remaining stored lines of its group, in
        stored order, while their quantities add up to its own; exactly equal claims them all
        (the first keeps its id, the rest are deleted), anything else claims none. That is the
        Excel import's FIFO split of one AutoCount line (2 + 98 for 100) folding back to one.
        Everything else stored and not claimed is deleted.
        """
        by_dtl = {row.dtl_key: row for row in stored if row.dtl_key is not None}
        legacy = [row for row in stored if row.dtl_key is None]
        plan: list[tuple[Any, dict[str, Any]]] = []  # (row or None, values)
        for values in incoming:
            row = by_dtl.pop(values["dtl_key"], None)
            if row is None:
                key = incoming_key(values)
                match = next((r for r in legacy if legacy_key(r) == key), None)
                if match is not None:
                    legacy.remove(match)
                    counts["adopted"] += 1
                    row = match
            plan.append((row, values))
        merged_away: list[Any] = []
        if merge is not None:
            row_group, row_qty, values_group, values_qty = merge
            for position, (row, values) in enumerate(plan):
                if row is not None:
                    continue
                group = [r for r in legacy if row_group(r) == values_group(values)]
                claimed, total = [], 0
                for candidate in group:
                    if total >= values_qty(values):
                        break
                    claimed.append(candidate)
                    total += row_qty(candidate)
                if len(claimed) > 1 and total == values_qty(values):
                    for candidate in claimed:
                        legacy.remove(candidate)
                    counts["adopted"] += 1
                    plan[position] = (claimed[0], values)
                    merged_away.extend(claimed[1:])
        removed = list(by_dtl.values()) + legacy + merged_away

        changes: list[tuple[Any, dict[str, Any]]] = []
        created: list[dict[str, Any]] = []
        for row, values in plan:
            if row is None:
                created.append(values)
                continue
            diff = self._changes(row, values, keep_links)
            if diff:
                changes.append((row, diff))
                if row.dtl_key is not None:
                    counts["updated"] += 1
        counts["created"] += len(created)
        counts["deleted"] += len(removed)
        if not changes and not created and not removed:
            return False

        for row in removed:
            if on_removed is not None:
                on_removed(row)
            self.db.delete(row)
        self.db.flush()
        if seq_column is not None:
            # (order_id, line_sequence) is unique: park every moving row on a negative
            # sequence first, so a swap never collides mid-flush.
            moving = [(row, diff) for row, diff in changes if seq_column in diff]
            for offset, (row, _) in enumerate(moving, start=1):
                setattr(row, seq_column, -offset)
            if moving:
                self.db.flush()
        for row, diff in changes:
            for key, value in diff.items():
                setattr(row, key, value)
        self.db.flush()
        for values in created:
            self.db.add(make(values))
        self.db.flush()
        if attach is not None:
            self.db.expire(header, ["lines"])
        return True

    @staticmethod
    def _count_links(counts: dict[str, int], rows, *columns: str) -> None:
        linked = sum(1 for row in rows if any(getattr(row, c) for c in columns))
        counts["linked"] = linked
        counts["unlinked"] = len(rows) - linked

    # ------------------------------------------------------------ waiting links
    def _fill_waiting_links(self, entity: str) -> None:
        """Fill null links in the anchor company that now resolve (Q10 a): the SO or PO a
        document named has arrived since it landed. Through the ORM, one row at a time, so
        the audit listener sees the header changes.

        Bounded (security review): the newest `MAX_WAITING_LINKS` per kind, and only lines
        that name their source document (`FromDocNo`), so every lookup is narrowed by an
        indexed document number, never a company-wide `LIKE`. A line naming only a DtlKey
        is resolved when it is pushed and again when it is pushed next."""
        if entity == DELIVERY_ORDERS_ENTITY:
            waiting_lines = (
                self.db.query(OrderLine)
                .filter(OrderLine.company_id == self.company_id,
                        OrderLine.sales_order_line_id.is_(None),
                        OrderLine.from_doc_no.isnot(None),
                        or_(OrderLine.from_doc_type.is_(None), OrderLine.from_doc_type == "SO"))
                .order_by(OrderLine.created_at.desc())
                .limit(MAX_WAITING_LINKS)
                .all()
            )
            for row in waiting_lines:
                # `or None`: a 0 stored before `_link_key` existed names no line either.
                target = self._do_so_line(row.from_dtl_key or None, row.from_doc_no,
                                          row.product_id)
                if target is not None:
                    row.sales_order_line_id = target
            waiting_headers = (
                self.db.query(Order)
                .filter(Order.company_id == self.company_id, Order.doc_key.isnot(None),
                        Order.sales_order_id.is_(None), Order.ref_doc_no.isnot(None))
                .order_by(Order.created_at.desc())
                .limit(MAX_WAITING_LINKS)
                .all()
            )
            for row in waiting_headers:
                target = self._sales_order(row.ref_doc_no)
                if target is not None:
                    row.sales_order_id = target
        else:
            exact = (
                self.db.query(PickingLine)
                .filter(PickingLine.company_id == self.company_id,
                        PickingLine.po_line_id.is_(None),
                        PickingLine.spo_allocation_id.is_(None),
                        PickingLine.from_dtl_key > 0,
                        PickingLine.from_doc_no.isnot(None))
                .order_by(PickingLine.created_at.desc())
                .limit(MAX_WAITING_LINKS)
                .all()
            )
            for row in exact:
                po_line, spo_line = self._po_or_spo_line(row.from_dtl_key, row.from_doc_no)
                if po_line or spo_line:
                    row.po_line_id, row.spo_allocation_id = po_line, spo_line
                    if spo_line:
                        self.touched_allocation_ids.add(spo_line)
            self._link_waiting_grn_lines()
        self.db.flush()

    def _link_waiting_grn_lines(self, numbers: Optional[set[str]] = None) -> None:
        """Link waiting AutoCount GRN lines through `_grn_links` (plan 1.3 rule 11): at the
        end of every GRN batch over the newest waiting lines, and when a PO / SPO arrives
        (`link_waiting_grn_lines`) over the lines that name it."""
        # Lines that name a source document (FromDocNo, else OurPONo, else the SPO an
        # adopted Excel line stated) and hold no line link yet - the PO or SPO had not
        # arrived when they landed (plan 1.3 rule 11). A D3 line (product not on the
        # document, its document link already held) is retried too, so it links the day
        # the document gains the product; the newest `MAX_WAITING_LINKS` bound stays.
        # Review B1: an adopted Excel line still waiting for its SPO keeps the SPO it
        # stated (`spo_number_raw`), and forward matching skips AutoCount GRNs, so this
        # fill is the one path that links it.
        query = (
            self.db.query(PickingLine)
            .filter(PickingLine.company_id == self.company_id,
                    PickingLine.dtl_key.isnot(None),
                    PickingLine.po_line_id.is_(None),
                    PickingLine.spo_allocation_id.is_(None),
                    # <= 0: stored before `_link_key` existed, names no line.
                    or_(PickingLine.from_dtl_key.is_(None), PickingLine.from_dtl_key <= 0),
                    or_(PickingLine.from_doc_no.isnot(None),
                        PickingLine.our_po_no.isnot(None),
                        PickingLine.spo_number_raw.isnot(None)))
        )
        if numbers:
            # Narrowed to the documents that just arrived (`link_waiting_grn_lines`), matched
            # the upload's way on every column a line may name its source in.
            from app.services.grn_spo_matching import _spo_match_key_sql
            from app.services.procurement_service import _spo_match_key

            keys = sorted({k for k in (_spo_match_key(n) for n in numbers) if k})
            if not keys:
                return
            query = query.filter(or_(
                _spo_match_key_sql(PickingLine.from_doc_no).in_(keys),
                _spo_match_key_sql(PickingLine.our_po_no).in_(keys),
                _spo_match_key_sql(PickingLine.spo_number_raw).in_(keys),
            ))
        waiting = (
            query.order_by(PickingLine.created_at.desc())
            .limit(MAX_WAITING_LINKS)
            .all()
        )
        by_header: dict[str, list[PickingLine]] = {}
        for row in waiting:
            by_header.setdefault(str(row.picking_header_id), []).append(row)
        for header_id, rows in by_header.items():
            # Siblings already linked keep their lines: counted as other receipts (no
            # exclusion) and marked used, so a waiting line never takes one of them.
            held = {
                str(target)
                for (po_line, spo_line) in (
                    self.db.query(PickingLine.po_line_id, PickingLine.spo_allocation_id)
                    .filter(PickingLine.company_id == self.company_id,
                            PickingLine.picking_header_id == header_id)
                    .all()
                )
                for target in (po_line, spo_line) if target
            }
            asks = [
                _LinkAsk(order=(row.seq is None, row.seq or 0, str(row.id)),
                         product_id=str(row.product_id), qty=row.quantity_picked or 0,
                         from_doc_type=row.from_doc_type,
                         from_doc_no=row.from_doc_no or row.spo_number_raw,
                         our_po_no=row.our_po_no, from_dtl_key=None)
                for row in rows
            ]
            links = self._grn_links(asks, [], exclude_header_id=None, used=held)
            for row, link in zip(rows, links):
                if not link.get(_FORCE_LINKS):
                    continue  # the document is still not here; the row is left as it was
                for column, value in _columns(link).items():
                    if column in ("from_doc_type", "from_doc_no") and getattr(row, column):
                        continue
                    setattr(row, column, value)
                if link.get("spo_allocation_id"):
                    self.touched_allocation_ids.add(link["spo_allocation_id"])
        self.db.flush()

    # ================================================================== deletions
    def delete(
        self,
        entity: str,
        doc_keys: list[Any],
        date_from: date,
        date_to: date,
        *,
        dry_run: bool = False,
    ) -> DeletionResult:
        """The sweep's vanished DocKeys: cancelled in place, never deleted (plan 1.10)."""
        if entity not in AUTOCOUNT_DOC_ENTITIES:
            raise UnsupportedIngestEntity(f"Unsupported AutoCount entity {entity!r}")
        model = Order if entity == DELIVERY_ORDERS_ENTITY else PickingHeader
        date_column = "order_date" if entity == DELIVERY_ORDERS_ENTITY else "picking_date"
        result = DeletionResult(dry_run=dry_run)
        try:
            with company_scope(self.db, frozenset({self.company_id})):
                for value in doc_keys:
                    result.records.append(
                        self._delete_one(entity, model, date_column, value, date_from, date_to)
                    )
                self.db.flush()
        finally:
            if dry_run:
                self.db.rollback()
        return result

    def _delete_one(self, entity, model, date_column, value, date_from, date_to):
        doc_key = _int(value)
        ref = _source_ref(self.book, entity, doc_key) if doc_key is not None else str(value)
        if doc_key is None:
            return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.FAILED,
                                        errors={"doc_key": "must be an integer"})
        row = (
            self.db.query(model)
            .filter(model.company_id == self.company_id, model.source_book == self.book,
                    model.doc_key == doc_key)
            .one_or_none()
        )
        if row is None:
            return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.NOT_FOUND)
        stored_date = getattr(row, date_column)
        if stored_date is None or not (date_from <= stored_date <= date_to):
            return DeletionRecordResult(
                source_ref=ref, outcome=DeletionOutcome.FAILED, entity_id=str(row.id),
                errors={"doc_date": f"stored DocDate {stored_date} is outside the swept range"},
            )
        if row.source_vanished_at is None:
            row.source_vanished_at = datetime.now(timezone.utc)
            row.is_cancelled = True
            if model is PickingHeader:
                row.picking_status = "cancelled"
                for line in row.picking_lines:
                    if line.spo_allocation_id:
                        self.touched_allocation_ids.add(str(line.spo_allocation_id))
        return DeletionRecordResult(source_ref=ref, outcome=DeletionOutcome.DEACTIVATED,
                                    entity_id=str(row.id))

    # ================================================================== branches
    def _ingest_branch(self, raw: Any) -> RecordResult:
        if not isinstance(raw, dict):
            return RecordResult(source_ref=None, outcome=IngestOutcome.FAILED,
                                errors={"record": "must be an object"})
        code = _text(raw.get("BranchCode"), 100)
        acc_no = _text(raw.get("AccNo"), 100) or ""
        source_ref = f"{self.book}:BR:{acc_no}:{code}" if code else None
        if code is None:
            return RecordResult(source_ref=None, outcome=IngestOutcome.FAILED,
                                errors={"BranchCode": "required"})
        try:
            size = len(json.dumps(raw, default=str))
        except (TypeError, ValueError):
            size = MAX_BRANCH_BYTES + 1
        if size > MAX_BRANCH_BYTES:
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors={"record": f"larger than {MAX_BRANCH_BYTES} bytes"})
        values = {"branch_name": _text(raw.get("BranchName"), 255), "source_record": raw}
        savepoint = self.db.begin_nested()
        try:
            with company_scope(self.db, frozenset({self.company_id})):
                row = (
                    self.db.query(Branch)
                    .filter(Branch.company_id == self.company_id, Branch.source_book == self.book,
                            Branch.acc_no == acc_no, Branch.branch_code == code)
                    .one_or_none()
                )
                if row is None:
                    row = Branch(company_id=self.company_id, source_book=self.book, acc_no=acc_no,
                                 branch_code=code, last_synced_at=datetime.utcnow(), **values)
                    self.db.add(row)
                    outcome = IngestOutcome.CREATED
                else:
                    diff = {k: v for k, v in values.items() if getattr(row, k) != v}
                    if not diff:
                        savepoint.commit()
                        return RecordResult(source_ref=source_ref, outcome=IngestOutcome.UNCHANGED,
                                            entity_id=str(row.id))
                    for key, value in diff.items():
                        setattr(row, key, value)
                    row.last_synced_at = datetime.utcnow()
                    outcome = IngestOutcome.UPDATED
                self.db.flush()
                self._refresh_branch_names(code)
            savepoint.commit()
            return RecordResult(source_ref=source_ref, outcome=outcome, entity_id=str(row.id))
        except Exception as exc:  # noqa: BLE001 - one record's failure, not the batch's
            savepoint.rollback()
            logger.warning("ingest.branch_failed source_ref=%s error=%s", source_ref,
                           _safe_error(exc))
            return RecordResult(source_ref=source_ref, outcome=IngestOutcome.FAILED,
                                errors={"_": INTERNAL_ERROR_MESSAGE})

    def _refresh_branch_names(self, code: str) -> None:
        """Every AutoCount DO of this branch carries the name `_branch_name` now resolves
        (plan Q10), by the SAME rule the DO push uses, so a replayed DO never disagrees
        with a branch push and flips the column back."""
        query = self.db.query(Order).filter(
            Order.company_id == self.company_id, Order.source_book == self.book,
            Order.doc_key.isnot(None), Order.branch_code == code,
        )
        # Every DO of the code, not only this AccNo's: a new row can change how another
        # debtor's DO resolves (a lone code-only match becomes ambiguous).
        by_debtor: dict[Optional[str], Optional[str]] = {}
        for order in query.all():
            if order.debtor_code not in by_debtor:
                by_debtor[order.debtor_code] = self._branch_name(order.debtor_code, code, [])
            resolved = by_debtor[order.debtor_code]
            if order.branch_name != resolved:
                order.branch_name = resolved
        self.db.flush()


# ============================================================================ read-back
class AutocountDocReadService:
    """Current stored state for a batch of `{book}:{DO|GRN}:{DocKey}` refs, in the anchor."""

    def __init__(self, db: Session, *, company_id: str):
        self.db = db
        self.company_id = company_id

    def current_state(self, entity: str, source_refs: list[Any]) -> dict[str, Any]:
        prefix = _DOC_PREFIX[entity]
        found: list[dict[str, Any]] = []
        not_found: list[str] = []
        with company_scope(self.db, frozenset({self.company_id})):
            for value in source_refs:
                ref = value if isinstance(value, str) else str(value)
                parts = ref.split(":")
                doc_key = _int(parts[2]) if len(parts) == 3 and parts[1] == prefix else None
                row = None
                if doc_key is not None:
                    model = Order if entity == DELIVERY_ORDERS_ENTITY else PickingHeader
                    row = (
                        self.db.query(model)
                        .filter(model.company_id == self.company_id,
                                model.source_book == parts[0], model.doc_key == doc_key)
                        .one_or_none()
                    )
                if row is None:
                    not_found.append(ref)
                    continue
                found.append(self._do(ref, row) if entity == DELIVERY_ORDERS_ENTITY
                             else self._grn(ref, row))
        return {"records": found, "not_found": not_found}

    @staticmethod
    def _common(ref: str, row) -> dict[str, Any]:
        return {
            "source_ref": ref,
            "entity_id": str(row.id),
            "book": row.source_book,
            "doc_key": row.doc_key,
            "source_modified_at": _iso(row.source_modified_at),
            "source_vanished_at": _iso(row.source_vanished_at),
            "is_cancelled": row.is_cancelled,
            "doc_status": row.doc_status,
            "ref": row.ref,
            "ref_doc_no": row.ref_doc_no,
            "remarks": row.remarks,
            "description": row.description,
            "currency_code": row.currency_code,
            "currency_rate": _num(row.currency_rate),
            "local_net_total": _num(row.local_net_total),
        }

    def _do(self, ref: str, row: Order) -> dict[str, Any]:
        return {
            **self._common(ref, row),
            "doc_no": row.order_number,
            "doc_date": _iso(row.order_date),
            "debtor_code": row.debtor_code,
            "debtor_name": row.debtor_name,
            "customer_id": row.customer_id,
            "branch_code": row.branch_code,
            "branch_name": row.branch_name,
            "agent": row.agent,
            "sales_order_id": row.sales_order_id,
            "subtotal_amount": _num(row.subtotal_amount),
            "tax_amount": _num(row.tax_amount),
            "total_amount": _num(row.total_amount),
            "lines": [
                {
                    "entity_id": str(line.id),
                    "dtl_key": line.dtl_key,
                    "seq": line.line_sequence,
                    "item_code": line.item_code,
                    "location_code": line.location_code,
                    "quantity": _num(line.quantity),
                    "unit_price": _num(line.unit_price),
                    "total": _num(line.total),
                    "from_doc_type": line.from_doc_type,
                    "from_doc_no": line.from_doc_no,
                    "from_dtl_key": line.from_dtl_key,
                    "sales_order_line_id": line.sales_order_line_id,
                }
                for line in row.lines
            ],
        }

    def _grn(self, ref: str, row: PickingHeader) -> dict[str, Any]:
        lines = sorted(row.picking_lines, key=lambda l: (l.seq is None, l.seq or 0))
        return {
            **self._common(ref, row),
            "doc_no": row.picking_number,
            "doc_date": _iso(row.picking_date),
            "picking_status": row.picking_status,
            "creditor_code": row.creditor_code,
            "creditor_name": row.creditor_name,
            "supplier_do_no": row.supplier_do_no,
            "subtotal_amount": _num(row.subtotal_amount),
            "tax_amount": _num(row.tax_amount),
            "total_amount": _num(row.total_amount),
            "lines": [
                {
                    "entity_id": str(line.id),
                    "dtl_key": line.dtl_key,
                    "seq": line.seq,
                    "item_code": line.item_code,
                    "location_code": line.location_code,
                    "qty": _num(line.qty),
                    "unit_cost": _num(line.unit_cost),
                    "line_total": _num(line.line_total),
                    "our_po_no": line.our_po_no,
                    "from_doc_type": line.from_doc_type,
                    "from_doc_no": line.from_doc_no,
                    "from_dtl_key": line.from_dtl_key,
                    "po_line_id": line.po_line_id,
                    "spo_allocation_id": line.spo_allocation_id,
                    "purchase_order_id": line.purchase_order_id,
                }
                for line in lines
            ],
        }


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _num(value) -> Optional[float]:
    return None if value is None else float(value)


def parse_deletion_body(payload: dict) -> tuple[list[Any], date, date]:
    """(doc_keys, date_from, date_to) or ValueError naming what is wrong (422 INVALID_BODY)."""
    doc_keys = payload.get("doc_keys")
    if not isinstance(doc_keys, list):
        raise ValueError("Body must contain a 'doc_keys' array")
    try:
        date_from = _date(payload.get("doc_date_from"))
        date_to = _date(payload.get("doc_date_to"))
    except (ValueError, TypeError):
        raise ValueError("'doc_date_from' and 'doc_date_to' must be dates") from None
    if date_from is None or date_to is None:
        raise ValueError("Body must contain 'doc_date_from' and 'doc_date_to'")
    if date_from > date_to:
        raise ValueError("'doc_date_from' is after 'doc_date_to'")
    return doc_keys, date_from, date_to


__all__ = [
    "AUTOCOUNT_BRANCH_ENTITIES",
    "AUTOCOUNT_DOC_ENTITIES",
    "BOOK_PATTERN",
    "BRANCHES_ENTITY",
    "CONTRACT_2_7_WARNINGS",
    "DELIVERY_ORDERS_ENTITY",
    "GOODS_RECEIVE_NOTES_ENTITY",
    "AutocountDocIngestService",
    "AutocountDocReadService",
    "parse_deletion_body",
]
