"""Procurement service for business logic."""
# ORM models declare Column[T] on the class; at runtime instance attributes are Python values.
# Pyright reports false positives here until models use SQLAlchemy 2.0 Mapped[] typing.
# pyright: reportAttributeAccessIssue=false
# pyright: reportGeneralTypeIssues=false
# pyright: reportArgumentType=false
# pyright: reportCallIssue=false
# pyright: reportReturnType=false
import json
import logging
import re
import secrets
import uuid
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload, aliased
from sqlalchemy import or_, and_, exists, false, func, select, case, cast, literal, Date
from sqlalchemy import inspect
from decimal import Decimal, InvalidOperation
from typing import Optional, List, Dict, Any
from datetime import date, datetime, timedelta, timezone
from app.models.procurement import (
    Supplier, ProductSupplier, InboundShipment, InboundShipmentLine, SPOAllocation,
    PickingHeader, PickingLine, StockInquiry, PurchaseRequestHeader, PurchaseRequestLine,
    ApprovalToken,
    PurchaseOrderLine,
    ViewToken,
)
from app.models.country import Country
from app.models.product import Product
from app.models.resources import Attachment
from app.models.user import User
from app.models.inventory import Warehouse
from app.models.scm import SupplierProductCodeAlias
from app.services.identifier_resolver import resolve_identifier
from app.services.fuzzy_resolver import resolve_via_embedding_then_ilike
from app.services.scm.container_capacity import container_sizes as _container_sizes
from app.services.scm.container_capacity import fit as _fit_capacity
from app.services.scm.container_capacity import line_cbm as _line_cbm
from app.services.rules.master_rules import clean_supplier_name, resolve_master_by_code
from app.services.rules import shipping_order_rules
from app.services.company_scope import build_company_predicate
from app.models.base import get_company_scope
from app.services.document_ingest_service import SOURCE_SYSTEM as ESB_SOURCE_SYSTEM
from app.services.scm.outstanding_import_service import (
    DEFAULT_PO_CURRENCY,
    SPO_UPLOAD_SOURCE,
)
from app.services.storage_router import delete_object_best_effort
from app.schemas.procurement import (
    SupplierCreate, SupplierUpdate, ProductSupplierCreate, ProductSupplierUpdate,
    InboundShipmentCreate, InboundShipmentUpdate,
    SPOAllocationCreate, SPOAllocationUpdate, PickingHeaderCreate, PickingHeaderUpdate,
    StockInquiryCreate, StockInquiryUpdate,
    PurchaseRequestHeaderCreate, PurchaseRequestHeaderUpdate, PurchaseRequestUpdateAndReply,
    ProductSimple, WarehouseSimple, InboundShipmentSimple,
    SPODocument, SPODocumentLine, SPODocumentRow, SPODocumentContainer,
    refuse_missing_sponsorship_unit_prices,
)
from app.services.error_handler import (
    AppException,
    handle_not_found,
    handle_conflict,
    handle_unprocessable,
    handle_validation_error,
)
from app.services.scm.money import BASE_CURRENCY as MONEY_BASE_CURRENCY
from app.services.document_number import display_document_number, strip_revision_suffix
from app.services.response_gate import (
    assert_response_write_allowed,
    is_response_status_allowed,
    response_text_changed,
)
from app.services.banner_person_service import wa_phone_for_user_id
from app.services.validators import validate_project_value
from app.services.requestor_options_service import (
    apply_requestor_contact as _apply_requestor_contact,
)

# Sentinel: "the caller did not mention the requestor field at all", distinct
# from an explicit None (which CLEARS the requestor + its label).
_UNSET_REQUESTOR = object()
from app.config import settings

logger = logging.getLogger(__name__)


def _strip_number_suffix_in_place(payload: dict, key: str) -> None:
    """Keep a STORED document number bare (UAC N2).

    ``request_number`` is user-assignable and the edit forms post it back, so a
    surface rendering ``PR-26-0012-R2`` could round-trip the revision suffix into
    the very column it was derived from - after which every lookup-by-number
    depends on the caller repeating that suffix. The revision lives in
    ``revision_no``; the number never carries it.
    """
    value = payload.get(key)
    if isinstance(value, str):
        payload[key] = strip_revision_suffix(value)


def _pop_status_or_refuse_move(
    payload: dict,
    *,
    current: Optional[str],
    label: str,
    actions: str,
    also_allowed: tuple[str, ...] = (),
) -> None:
    """Take ``status`` out of an edit payload, and refuse a real lifecycle move.

    The lifecycle belongs to the workflow actions, never to an edit: a contact
    revision sets the status back to the restart stage, and an office tab holding
    the superseded value must not be able to stomp it back.

    Dropping the field silently is not enough. Both update schemas still DECLARE
    ``status`` (and removing it would change nothing, since a Pydantic model
    ignores undeclared fields by default), so an n8n / MCP caller that had been
    walking the lifecycle through this endpoint would get ``200`` and no change -
    the worst failure mode available. So a payload whose ``status`` would actually
    MOVE the record is refused with one plain sentence.

    A payload echoing the CURRENT status moves nothing and is dropped quietly, so
    a read-modify-write round trip of the whole entity keeps saving. Same rule the
    response gate uses just below: only a real change counts as a write.

    ``also_allowed`` is for a path that legitimately moves the status ITSELF: the
    purchasing reply always lands the inquiry on ``responded``, so a caller asking
    for exactly the destination the call is about to reach is not refused for it
    (it is still dropped, since the workflow, not the payload, performs the move).
    Anything else is refused as usual.
    """
    if "status" not in payload:
        return
    supplied = payload.pop("status")
    if supplied is None:
        return
    supplied_norm = str(supplied).strip().lower()
    if not supplied_norm:
        return
    accepted = {str(current or "").strip().lower()}
    accepted.update(str(value).strip().lower() for value in also_allowed)
    if supplied_norm in accepted:
        return
    raise handle_unprocessable(
        f"The status of this {label} cannot be changed by editing it, so use the "
        f"{actions} action instead."
    )


def _request_label(header: Any) -> str:
    """"purchase request" / "sponsorship form" for a message the office reads.

    One table, two document types (``request_type``), so every sentence about a
    header has to name the right one. Shared by both write paths that guard the
    status, so the two cannot end up naming it differently.
    """
    return (
        "sponsorship form"
        if getattr(header, "request_type", None) == "sponsorship_form"
        else "purchase request"
    )


# The workflow actions that own a purchase request's / sponsorship form's status.
# Named in the refusal so the caller is told where the move belongs.
_REQUEST_STATUS_ACTIONS = "approval, process, close or void"


class AllocationReceivedGuardError(Exception):
    """Raised by upsert_allocation when the new allocated quantity would drop below
    the quantity already received for an existing allocation. The import loop catches
    this to classify the row as a real (skipped) error rather than a generic conflict."""
    pass


_SHIPMENT_STATUS_ALIASES = {
    "received": "fully_received",
}

_SPO_RECEIPT_STATUS_ALIASES = {
    "received": "fully_received",
    "partially_received": "partial_received",
}


def _normalize_inbound_shipment_status(value: Optional[str]) -> Optional[str]:
    """Normalize legacy/API aliases to DB-valid inbound shipment statuses."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return raw
    return _SHIPMENT_STATUS_ALIASES.get(raw.lower(), raw)


def _normalize_spo_receipt_status(value: Optional[str]) -> Optional[str]:
    """Normalize legacy/API aliases to DB-valid SPO receipt statuses."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return raw
    return _SPO_RECEIPT_STATUS_ALIASES.get(raw.lower(), raw)


def _normalize_spo_number(spo_number: Optional[str]) -> str:
    """Normalize SPO number for matching (e.g. SPO-2026/01-0178 vs SPO-2026.01-0178)."""
    if not spo_number or not str(spo_number).strip():
        return ""
    return str(spo_number).strip().replace("/", ".").replace("\\", ".")


def _resolve_contact_phone_for_webhook(contact: Any, contact_respond_io_id: str) -> Optional[str]:
    """
    Resolve contact phone for incoming-style revise webhooks.
    Prefer local RespondContact.phone_number; fallback to Respond.io API lookup by contact id.
    """
    local_phone = (getattr(contact, "phone_number", None) or "").strip()
    if local_phone:
        return local_phone

    identifier = (contact_respond_io_id or "").strip()
    if not identifier:
        return None

    try:
        from app.services.integration_service import RespondClient

        payload = RespondClient().get_contact_by_identifier(identifier)
        if isinstance(payload, dict):
            contact_block = payload.get("contact") or {}
            phone = (contact_block.get("phone") or "").strip() if isinstance(contact_block, dict) else ""
            return phone or None
    except Exception:
        logger.debug("Could not resolve contact phone from Respond.io for id=%s", identifier, exc_info=True)
    return None


def _forward_match_for_spo(
    db: Session, spo_number: Optional[str], company_id: Optional[str] = None
) -> None:
    """Fire forward matching after an allocation has been WRITTEN and committed.

    The GRN and its SPO arrive in whatever order the supplier and the warehouse
    produce them, so the moment an allocation appears is the moment the lines that
    were waiting on it can be placed. Every call site is post-commit and
    best-effort - a side effect that fails must not turn a successful allocation
    write into a 500, because the caller's retry takes the idempotent path and
    never backfills the missed effect.

    ``company_id`` is the allocation's own company, passed so the match stays
    inside it even on the ``X-API-Key`` path, whose scope is NULL ("all
    companies") and therefore constrains nothing.

    Imported inside the function: ``grn_spo_matching`` imports this module at load
    time, so a module-level import here would be a cycle.
    """
    from app.services.grn_spo_matching import forward_match_grn_lines_for_spo_best_effort

    forward_match_grn_lines_for_spo_best_effort(db, spo_number, company_id=company_id)


def _stated_spo_for_line(
    payload_value: Optional[str], header_spo_number: Optional[str]
) -> Optional[str]:
    """What a picking line SAYS it was received against.

    What the client sent wins - it is the value the GRN screen read back and is
    round-tripping, and re-deriving over it would silently rewrite an imported
    line's evidence on an unrelated edit. Otherwise the header's SPO stands in, but
    only when it names EXACTLY ONE (``_single_spo_or_none``): a joined multi-SPO
    header names no single allocation, and storing it would put a claim on screen
    that the scalar matcher can never honour.

    Without this, ``update_grn`` - which deletes and recreates every picking line -
    would wipe ``spo_number_raw`` off an imported GRN on the first edit and make it
    un-forward-matchable. It also gives the UI and external-API GRN paths a stated
    SPO, so a GRN that arrives that way BEFORE its allocation is forward-matchable
    too.
    """
    if payload_value is not None and str(payload_value).strip():
        return payload_value
    # Local import: app.tasks.import_tasks imports this module at load time, and
    # the sheet-parsing rules live there with the rest of the import vocabulary.
    from app.tasks.import_tasks import _single_spo_or_none

    return _single_spo_or_none(header_spo_number)


def _spo_match_key(spo_number: Optional[str]) -> str:
    """Alphanumeric-only key so SPO-202602-0102 matches SPO-2026/02-0102 and SPO-2026.02-0102."""
    if not spo_number or not str(spo_number).strip():
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(spo_number).strip()).upper()


#: A line nothing may write a receipt onto. There is no `cancelled`
#: `line_status` on `spo_allocations` today - the ESB's own `cancelled`
#: document forces `closed` instead - so the word itself is the guard for the
#: day a writer introduces one: a cancelled line covers no demand.
_CANCELLED_WORDS = frozenset({"cancelled", "canceled"})


def _is_live_group_member(allocation) -> bool:
    """Whether this AutoCount line takes part in its group's recompute (D28c,
    AC-X41).

    Three exclusions, one predicate, because all three are the same statement:
    the line is no longer demand this system may move a receipt onto.

    - RETIRED (`retired_at` set, D28d): the ESB has stopped naming it, by
      absence in a re-push of the same DocKey or because the document was
      re-created under a new one. This is the only one of the three that a
      `closed` + `fully_received` row can carry, and it is what tells a
      retired line from a live fully received one.
    - `cancelled`: covers no demand by definition.
    - closed for a reason OTHER than a receipt (`closed` with `receipt_status`
      anything but `fully_received`): retired by the leftover sweep before
      D28d existed, or by a cancelled document. A recompute must neither give
      it a share nor write to it at all, or a receipt would revive demand
      AutoCount itself retired (and `_write_received`'s reopen would fire on
      it).

    A line closed BY a receipt (`closed` + `fully_received`) and still named
    by the ESB IS live: the receipt is the only reason it is closed, so if
    that receipt goes the line must come back (AC-X35).
    """
    if getattr(allocation, "retired_at", None) is not None:
        return False
    line_status = (allocation.line_status or "").strip().lower()
    receipt_status = (allocation.receipt_status or "").strip().lower()
    if line_status in _CANCELLED_WORDS or receipt_status in _CANCELLED_WORDS:
        return False
    if line_status == "closed" and receipt_status != "fully_received":
        return False
    return True


def _spo_allocation_group_key(allocation) -> tuple:
    """The `(company, spo_number, product, location)` key an AutoCount line
    shares its receipt over (D28a). One definition, used both to visit a group
    once and to see which groups a release touched (D28d)."""
    return (
        str(allocation.company_id),
        _spo_match_key(allocation.spo_number),
        str(allocation.product_id),
        _spo_group_location(allocation.location_code),
    )


def _spo_group_location(location_code: Optional[str]) -> Optional[str]:
    """The location half of the `(product, location)` group key (D28a).

    Normalised exactly the way `shipping_order_rules.supersede_group_key`
    normalises it - upper-cased, blank as `None` - so the group a recompute
    redistributes over is the same group the first-push supersede carried a
    receipt across.
    """
    return (location_code or "").strip().upper() or None


# Separators seen in extracted container numbers (ISO 6346 is 4 letters + 7
# digits, but the PDF/LLM round-trip introduces spaces, dashes and slashes).
# The Python and SQL normalizers below MUST strip exactly the same set or a
# candidate found in SQL would fail the Python-side triple comparison.
_CONTAINER_STRIP_CHARS = (" ", "-", "/", ".", "_")


def _container_match_key(value: Optional[str]) -> str:
    """Normalized container key so 'temu 1234567' matches 'TEMU-1234567'."""
    if not value or not str(value).strip():
        return ""
    key = str(value).strip()
    for ch in _CONTAINER_STRIP_CHARS:
        key = key.replace(ch, "")
    return key.upper()


def _container_key_sql(column):
    """SQL twin of ``_container_match_key``.

    Uses only UPPER/REPLACE - Postgres-only ``regexp_replace`` would break the
    sqlite-backed unit tests (tests/conftest.py).
    """
    expr = column
    for ch in _CONTAINER_STRIP_CHARS:
        expr = func.replace(expr, ch, "")
    return func.upper(expr)


class DuplicatePackingListError(Exception):
    """The same packing list was uploaded twice after its GRN completed.

    Raised instead of creating a second inbound shipment when an already
    received shipment on the same container carries an identical
    (container, ETA, shipment_date) triple. Carries the machine code the
    upload-activity drawer keys its friendly copy on.
    """

    error_code = "DUPLICATE_PACKING_LIST"

    def __init__(self, message: str, existing: "InboundShipment"):
        super().__init__(message)
        self.message = message
        self.existing = existing


def _is_received_status(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("fully_received", "completed")


def _packing_list_triple_matches(existing: "InboundShipment", shipment_data) -> bool:
    """NULL-safe (``IS NOT DISTINCT FROM``) equality on the identity triple.

    Python's ``==`` already gives NULL==NULL -> True and NULL vs value -> False,
    which is exactly the required semantics.
    """
    return (
        _container_match_key(existing.shipping_container_number)
        == _container_match_key(shipment_data.shipping_container_number)
        and existing.estimated_arrival_date == shipment_data.estimated_arrival_date
        and existing.shipment_date == shipment_data.shipment_date
    )


def _format_duplicate_packing_list_message(existing: "InboundShipment", shipment_data) -> str:
    """User-facing rejection copy.

    Identifies the colliding shipment by container + dates (+ shipment number
    when it has one). Never by id - packing lists usually have no shipment
    number, and UUIDs must not surface in user-facing text.
    """
    container = (shipment_data.shipping_container_number or "").strip()
    eta = shipment_data.estimated_arrival_date
    eta_text = eta.isoformat() if eta else "not stated"
    sail_text = (
        shipment_data.shipment_date.isoformat() if shipment_data.shipment_date else "not stated"
    )
    number = (getattr(existing, "shipment_number", None) or "").strip()
    as_number = f" as {number}" if number else ""
    recorded_on = ""
    created_at = getattr(existing, "created_at", None)
    if created_at:
        recorded_on = f" on {created_at.date().isoformat()}"
    return (
        f"Container {container} (shipment date {sail_text}, ETA {eta_text}) was already "
        f"recorded{as_number}{recorded_on} and is fully received. This looks like the same "
        "packing list uploaded twice. If this container is carrying a new shipment, its "
        "shipment date or ETA must be different from the previous one."
    )


def compute_inbound_shipment_line_status(
    quantity_shipped: int,
    allocated_quantity: int,
    quantity_received: int,
) -> str:
    """Compute line-level status for packing list lines (stored in DB for n8n/API).
    Same logic as frontend: in_transit, allocated, partially_allocated, received, partially_received.
    """
    qty = quantity_shipped or 0
    alloc = allocated_quantity or 0
    recv = quantity_received or 0
    if alloc == 0:
        return "in_transit"
    if recv >= alloc:
        return "received"
    if qty > alloc:
        return "partially_allocated"
    if alloc >= qty and recv == 0:
        return "allocated"
    if alloc >= qty and recv > 0:
        return "partially_received"
    return "in_transit"


def _apportion(total: int, lines: list) -> dict[str, int]:
    """Split a PER-PRODUCT total (allocated, or received) across that product's own
    shipment lines (S4, AC-D5) - `lines` already in `(created_at, id)` order.

    Each line takes up to its own `quantity_shipped`; the LAST line takes whatever is
    LEFT, never its own capped share - that is what lets 100 allocated against 50 + 35
    shipped read as 50 / 50 rather than 50 / 35 with 15 unaccounted for. Retires the
    CAVEAT this function's own callers used to carry: `spo_allocated_quantity` /
    `quantity_received` were summed per PRODUCT and stamped onto every line of it, which
    doubled up whenever a container held the same product on two lines.
    """
    remaining = int(total or 0)
    out: dict[str, int] = {}
    for i, line in enumerate(lines):
        if i == len(lines) - 1:
            out[str(line.id)] = max(remaining, 0)
            continue
        take = min(remaining, int(line.quantity_shipped or 0))
        out[str(line.id)] = take
        remaining -= take
    return out


def shipment_supplier_predicate(supplier_id):
    """Containers this supplier is ON: the header names them, or any of their lines does.

    A container filled by two factories has NO header supplier (a mixed container has no
    single one), so a filter that reads the header alone hides that container from both of
    the suppliers actually on it - exactly the containers the per-supplier line was added
    for. One helper rather than the same OR written out at each call site, because a filter
    that drifts from the others is a screen that quietly disagrees with its neighbour.

    Takes one id or an iterable of them (the identifier resolver hands back a list).
    A single id may be a `uuid.UUID` as well as a string - the ORM hands back UUID objects,
    and iterating one of those is a TypeError rather than a filter on that supplier.
    """
    if isinstance(supplier_id, (str, uuid.UUID)):
        ids = [str(supplier_id)]
    else:
        ids = [str(s) for s in supplier_id]
    if not ids:
        return false()
    if len(ids) == 1:
        header = InboundShipment.supplier_id == ids[0]
        line = InboundShipmentLine.supplier_id == ids[0]
    else:
        header = InboundShipment.supplier_id.in_(ids)
        line = InboundShipmentLine.supplier_id.in_(ids)
    return or_(
        header,
        exists(
            select(1)
            .where(
                InboundShipmentLine.shipment_id == InboundShipment.id,
                line,
            )
            # Correlate to the shipment EXPLICITLY. Auto-correlation looks at whatever
            # query this predicate is dropped into, and in the incoming listings - which
            # already select from `inbound_shipment_lines` through a subquery - it
            # correlated the lines table away too and left the EXISTS with no FROM at all
            # ("returned no FROM clauses due to auto-correlation").
            .correlate(InboundShipment)
        ),
    )


#: Line fields where an explicit `null` on the PUT is a stated "clear it", not "not
#: mentioned" - see `_upsert_shipment_lines`. Both are free text with no fallback that
#: `_merge_shipment_lines` computes on the caller's behalf (unlike `supplier_id`), so the
#: key's mere presence in the merged dict already carries the caller's intent correctly.
_CLEARABLE_LINE_FIELDS = {"description", "remarks"}


def _effective_line_supplier(line_dict: dict, header_supplier_id: Optional[str]) -> Optional[str]:
    """Whose line this is: what the line says, else what the header says.

    A packing list is sent per factory, so the upload states the supplier once on the
    header and every line on it belongs to that factory. A caller that knows better says
    so on the line and wins.
    """
    return line_dict.get("supplier_id") or header_supplier_id


def _is_superseded_line(
    line: InboundShipmentLine,
    incoming_suppliers: set[Optional[str]],
    incoming_products: set[str],
) -> bool:
    """Does this upload speak for a line that is already on the container?

    Two ways it does. The obvious one is that the line belongs to a supplier this upload
    names, so the upload is that supplier's corrected list and replaces it. The second is
    an UNATTRIBUTED line for a product this upload carries: the n8n PDF path writes lines
    with no supplier, and a later Excel upload of the same container from the factory that
    actually packed them is the same goods described twice. Keeping both doubles the
    quantity on the container, which is the number the whole screen is read for.

    An unattributed line for a product this upload does NOT carry is a different item that
    nobody has claimed yet, and it stays.
    """
    line_supplier = str(line.supplier_id) if line.supplier_id else None
    if line_supplier is not None:
        return line_supplier in incoming_suppliers
    # Product-scoped, always. A payload that states NO supplier anywhere speaks for the whole
    # container and never reaches here (the caller replaces every line for it); a payload that
    # states one somewhere is a factory's list, so the only unattributed lines it speaks for
    # are the products it actually carries - including when some of its own lines happen to
    # name no supplier of their own.
    return str(line.product_id) in incoming_products


def _merge_shipment_lines(lines_data, header_supplier_id: Optional[str]) -> list[dict]:
    """One dict per (product, supplier) in this payload, quantities summed, price averaged.

    The merge key carries the supplier because a container legitimately carries the same
    product from two factories; merging those two rows into one would lose which factory
    packed what, which is the whole point of the per-supplier line.

    Dumped with `exclude_unset`, so a field the payload never mentioned stays out of the
    dict. `cartons_count` is the one field on the line schema with a non-None default (1),
    and dumping it unconditionally turned "not stated" into a stated 1: the procurement
    edit form sends `{product_id, quantity_shipped}`, and `_upsert_shipment_lines` then
    wrote that 1 over the carton count the packing list had actually given. Inserts are
    unaffected because the column carries the same default.

    A packing list also names the same product twice at two prices, because a price was
    renegotiated part way through the order. Quantities and cartons add up. The PRICE
    cannot: keeping the first line's `unit_cost` silently values the whole merged quantity
    at whichever price happened to be listed first, and the difference is invisible
    afterwards because the two lines no longer exist separately. So the merged cost is the
    QUANTITY-WEIGHTED average, which is what the container actually cost per unit. It is
    only computed when both sides carry a cost in the SAME currency; a currency mismatch is
    not arithmetic, so the first line's cost is kept and the disagreement is logged rather
    than averaged into a meaningless number.
    """
    merged: dict[tuple[str, Optional[str]], dict] = {}
    for line_data in lines_data or []:
        if hasattr(line_data, "model_dump"):
            d = line_data.model_dump(exclude_unset=True)
        else:
            d = dict(line_data)
        d["supplier_id"] = _effective_line_supplier(d, header_supplier_id)
        key = (d["product_id"], d["supplier_id"])
        if key not in merged:
            merged[key] = dict(d)
            continue

        kept = merged[key]
        kept_qty = kept.get("quantity_shipped") or 0
        add_qty = d.get("quantity_shipped") or 0
        kept["quantity_shipped"] = kept_qty + add_qty
        # Only a STATED carton count adds to the total. A duplicate row that says nothing
        # about cartons means "unknown", not "one more carton", and adding a phantom 1 per
        # silent row is how a save with no cartons on it ends up restating them.
        if "cartons_count" in d:
            kept["cartons_count"] = (kept.get("cartons_count") or 0) + d["cartons_count"]

        kept_cost, add_cost = kept.get("unit_cost"), d.get("unit_cost")
        if kept_cost is None and add_cost is None:
            # Neither row priced. Say nothing rather than writing a None over the key
            # `exclude_unset` deliberately left out.
            continue
        if kept_cost is None or add_cost is None:
            # One side priced and the other not: there is nothing to average against, and
            # inventing a zero for the unpriced half would halve the cost of the line.
            kept["unit_cost"] = kept_cost if kept_cost is not None else add_cost
            kept["currency"] = kept.get("currency") or d.get("currency")
            continue
        if (kept.get("currency") or None) != (d.get("currency") or None):
            logger.warning(
                "Shipment line merge: product %s appears twice in different currencies "
                "(%s and %s). Keeping the first line's cost rather than averaging.",
                d["product_id"], kept.get("currency"), d.get("currency"),
            )
            continue

        total_qty = Decimal(str(kept_qty)) + Decimal(str(add_qty))
        if total_qty == 0:
            continue
        kept["unit_cost"] = (
            Decimal(str(kept_cost)) * Decimal(str(kept_qty))
            + Decimal(str(add_cost)) * Decimal(str(add_qty))
        ) / total_qty

    return list(merged.values())


def _line_company_kwargs(shipment: "InboundShipment") -> dict:
    """The company a new line of this container belongs to: its HEADER's.

    The insert auto-stamp (`company_scope._stamp_company_id`) reads the SESSION
    scope, not the row's parent. The n8n re-upload path arrives with no company
    identity at all - scope `None`, the deliberate all-companies principal - so it
    finds a Mocha container and stamps the replacement lines with the incumbent
    company (Sorento). The lines then belong to a different company from the
    container they hang off, and every scoped read of `inbound_shipment_lines`
    stops seeing them: the container reads `in_transit` with nothing received
    although its GRN received the lot (packing list TGHU6295708).

    Returns `{}` when the header itself names no company, so the auto-stamp stays
    the fallback rather than being replaced by a NULL.
    """
    company_id = getattr(shipment, "company_id", None)
    return {"company_id": company_id} if company_id else {}


class SupplierService:
    """Service for supplier operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def _build_list_query(
        self,
        query: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        advanced_filter_clause=None,
    ):
        """Build the filtered + sorted suppliers query shared by ``list_suppliers``
        and ``neighbours`` so the two can never drift.

        The ORDER BY always appends ``Supplier.id`` as a deterministic tie-breaker
        so offset position and prev/next neighbours are unambiguous when the
        primary sort column has equal values.
        """
        # S2 (`PLAN-local-supplier-oi-routing.md`, review S6): eager, so `country_code`/
        # `country_name` never cost an extra query per row on the way to `SupplierResponse`.
        q = self.db.query(Supplier).options(joinedload(Supplier.country))

        if query:
            q = q.filter(
                or_(
                    Supplier.supplier_code.ilike(f"%{query}%"),
                    Supplier.supplier_name.ilike(f"%{query}%"),
                )
            )

        if advanced_filter_clause is not None:
            q = q.filter(advanced_filter_clause)

        sort_map = {
            "created_at": Supplier.created_at,
            "supplier_code": Supplier.supplier_code,
            "supplier_name": Supplier.supplier_name,
            # review S5: sorting by the joined name needs its own join - `joinedload`
            # above is for eager-loading the relationship attribute, not for referencing
            # the join in an ORDER BY.
            "country_name": Country.name,
        }
        sort_column = sort_map.get(sort_field, Supplier.created_at)
        if sort_field == "country_name":
            q = q.outerjoin(Country, Supplier.country_id == Country.id)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc(), Supplier.id.asc())
        else:
            q = q.order_by(sort_column.asc(), Supplier.id.asc())
        return q

    def list_suppliers(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        advanced_filter_clause=None,
    ):
        """List suppliers."""
        q = self._build_list_query(
            query=query,
            sort_field=sort_field,
            sort_dir=sort_dir,
            advanced_filter_clause=advanced_filter_clause,
        )

        total = q.count()
        offset = (page - 1) * limit
        suppliers = q.offset(offset).limit(limit).all()

        return {
            "data": suppliers,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }

    def get_supplier(self, supplier_id: str):
        """Get a supplier by ID."""
        supplier = self.db.query(Supplier).filter(Supplier.id == supplier_id).first()
        if not supplier:
            raise handle_not_found("Supplier", supplier_id)
        return supplier
    
    def _validate_country_id(self, country_id: Optional[str]) -> None:
        """S2: an unresolvable `country_id` is a 422, never a raw FK violation."""
        if not country_id:
            return
        if not self.db.query(Country.id).filter(Country.id == country_id).first():
            raise handle_unprocessable("Unknown country.")

    def create_supplier(self, supplier_data: SupplierCreate):
        """Create a new supplier."""
        # D17: case/whitespace-insensitive, same as every other channel.
        if resolve_master_by_code(self.db, Supplier, supplier_data.supplier_code):
            raise handle_conflict("Supplier code already exists.")
        self._validate_country_id(supplier_data.country_id)

        data = supplier_data.model_dump()
        # D2: AutoCount's trailing currency note (`"ACME (RMB)"`) is not part
        # of the legal name - see `master_rules.clean_supplier_name`. A name
        # with no such suffix passes through unchanged.
        data["supplier_name"] = clean_supplier_name(data.get("supplier_name"))
        supplier = Supplier(**data)
        self.db.add(supplier)
        self.db.commit()
        self.db.refresh(supplier)
        return supplier

    def update_supplier(self, supplier_id: str, supplier_data: SupplierUpdate):
        """Update a supplier."""
        supplier = self.get_supplier(supplier_id)

        update_data = supplier_data.model_dump(exclude_unset=True)
        if "country_id" in update_data:
            self._validate_country_id(update_data["country_id"])
        # D2 (review nit): the trailing currency note (`"ACME (RMB)"`) is not
        # part of the legal name on create - `update_supplier` used to write
        # it through unstripped, so an edit could quietly restore the note
        # `create_supplier`/the ESB push/the outstanding upload all clean.
        if "supplier_name" in update_data:
            update_data["supplier_name"] = clean_supplier_name(update_data["supplier_name"])
        for key, value in update_data.items():
            setattr(supplier, key, value)

        self.db.commit()
        self.db.refresh(supplier)
        return supplier


class InboundShipmentService:
    """Service for inbound shipment (packing list) operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def _build_list_query(
        self,
        query: Optional[str] = None,
        supplier_id: Optional[str] = None,
        shipment_status: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        region: Optional[str] = None,
    ):
        """Build the filtered + sorted inbound-shipment query shared by
        ``list_shipments`` and ``neighbours`` so the two can never drift.

        The ORDER BY always appends ``InboundShipment.id`` as a deterministic
        tie-breaker so offset position and prev/next neighbours stay unambiguous
        when the primary sort column has equal values.

        Returns ``(query, empty)`` - ``empty`` is True when a supplier filter was
        supplied but resolved to no suppliers, in which case the query yields nothing.
        """
        q = self.db.query(InboundShipment)

        filters = []

        supplier_ids = resolve_identifier(
            self.db,
            supplier_id,
            Supplier,
            code_fields=("supplier_code", "supplier_name"),
        )
        if supplier_ids is not None:
            if not supplier_ids:
                # Supplier filter supplied but resolved to nothing -> empty set.
                return q.filter(InboundShipment.id.is_(None)), True
            filters.append(shipment_supplier_predicate(supplier_ids))

        region_norm = (region or "").strip().lower()
        if region_norm:
            filters.append(InboundShipment.regions.contains([region_norm]))

        status_norm = (shipment_status or "").strip().lower()
        if status_norm and status_norm != "all":
            if status_norm == "open":
                filters.append(
                    InboundShipment.shipment_lines.any(InboundShipmentLine.line_status != "received")
                )
            elif status_norm in ("received", "closed"):
                filters.append(
                    ~InboundShipment.shipment_lines.any(InboundShipmentLine.line_status != "received")
                )
            else:
                filters.append(
                    InboundShipment.shipment_status == _normalize_inbound_shipment_status(shipment_status)
                )

        if query:
            term = f"%{query}%"
            # Product-line match: surface shipments whose lines contain a
            # product whose code / name / description matches the term. Uses
            # EXISTS-on-line + joined Product so the predicate stays
            # sargable against pg_trgm indexes on Product columns.
            product_line_clause = InboundShipment.shipment_lines.any(
                InboundShipmentLine.product.has(
                    or_(
                        Product.product_code.ilike(term),
                        Product.product_name.ilike(term),
                        Product.description.ilike(term),
                    )
                )
            )
            filters.append(
                or_(
                    InboundShipment.shipment_number.ilike(term),
                    InboundShipment.bill_of_lading_number.ilike(term),
                    InboundShipment.shipping_container_number.ilike(term),
                    InboundShipment.invoice_number.ilike(term),
                    InboundShipment.supplier.has(Supplier.supplier_name.ilike(term)),
                    InboundShipment.supplier.has(Supplier.supplier_code.ilike(term)),
                    product_line_clause,
                )
            )

        if filters:
            q = q.filter(and_(*filters))

        sort_map = {
            "shipment_number": InboundShipment.shipment_number,
            "shipment_date": InboundShipment.shipment_date,
            "created_at": InboundShipment.created_at,
            "updated_at": InboundShipment.updated_at,
        }
        sort_column = sort_map.get(sort_field, InboundShipment.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc(), InboundShipment.id.asc())
        else:
            q = q.order_by(sort_column.asc(), InboundShipment.id.asc())
        return q, False

    def list_shipments(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        supplier_id: Optional[str] = None,
        shipment_status: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        region: Optional[str] = None,
    ):
        """List inbound shipments."""
        q, empty = self._build_list_query(
            query=query,
            supplier_id=supplier_id,
            shipment_status=shipment_status,
            region=region,
            sort_field=sort_field,
            sort_dir=sort_dir,
        )
        if empty:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }

        total = q.count()
        offset = (page - 1) * limit
        from sqlalchemy.orm import joinedload
        shipments = (
            q.options(joinedload(InboundShipment.supplier))
            .offset(offset)
            .limit(limit)
            .all()
        )

        shipment_ids = [s.id for s in shipments]
        line_counts: dict[str, int] = {}
        non_received_counts: dict[str, int] = {}
        spo_counts: dict[str, int] = {}
        if shipment_ids:
            # Counted per CONTAINER, over the line TABLE rather than the ORM entity:
            # `shipment_ids` came out of the scoped header query above, so the
            # containers are already the ones this caller may see, and the count must
            # then cover every line ON them. Counting through the entity re-applies the
            # company predicate to the LINE, so a line mis-stamped with another company
            # (an n8n re-upload under a company-less scope) dropped out of its own
            # container's count: a fully received container reported 0 lines and stayed
            # `in_transit`. An ORM join does not help - `func.count(Line.id)` puts the
            # line mapper back into the statement's top-level entities.
            lines_table = InboundShipmentLine.__table__

            def _line_counts(*extra_filters) -> dict[str, int]:
                stmt = (
                    select(lines_table.c.shipment_id, func.count(lines_table.c.id))
                    .where(lines_table.c.shipment_id.in_(shipment_ids), *extra_filters)
                    .group_by(lines_table.c.shipment_id)
                )
                return {
                    str(row_id): int(count or 0)
                    for row_id, count in self.db.execute(stmt).all()
                }

            line_counts = _line_counts()
            non_received_counts = _line_counts(lines_table.c.line_status != "received")
            from app.services.scm import spo_supply

            spo_counts = {
                str(shipment_id): int(count or 0)
                for shipment_id, count in (
                    self.db.query(
                        SPOAllocation.inbound_shipment_id,
                        func.count(SPOAllocation.id),
                    )
                    .filter(
                        SPOAllocation.inbound_shipment_id.in_(shipment_ids),
                        # R7/AC-E8: a retired line is not counted, so this figure
                        # agrees with the grouped allocation listing beside it.
                        *spo_supply.visible_line_clauses(),
                    )
                    .group_by(SPOAllocation.inbound_shipment_id)
                    .all()
                )
            }
        for shipment in shipments:
            shipment_id = str(shipment.id)
            total_lines = line_counts.get(shipment_id, 0)
            non_received_lines = non_received_counts.get(shipment_id, 0)
            setattr(shipment, "lines_count", total_lines)
            setattr(shipment, "spo_allocations_count", spo_counts.get(shipment_id, 0))
            if total_lines > 0 and non_received_lines == 0:
                shipment.shipment_status = "fully_received"
            elif (shipment.shipment_status or "").strip().lower() in ("received", "fully_received"):
                shipment.shipment_status = "in_transit"

        return {
            "data": shipments,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_shipment(self, shipment_id: str):
        """Get a shipment by UUID or business reference (shipment_number, container #, BOL, invoice #)."""
        from sqlalchemy.orm import joinedload
        resolved_ids = resolve_identifier(
            self.db,
            shipment_id,
            InboundShipment,
            code_fields=(
                "shipment_number", "shipping_container_number", "bill_of_lading_number",
                # `提单号` lands here (the SO field), not `bill_of_lading_number`, on
                # an upload made through the supplier-documents dialog (S3, review
                # round 1) - a lookup on the B/L alone missed every container
                # uploaded that way.
                "forwarder_order_ref", "invoice_number",
            ),
        )
        if not resolved_ids:
            raise handle_not_found("Inbound Shipment", shipment_id)
        shipment = self.db.query(InboundShipment).options(
            joinedload(InboundShipment.attachment).joinedload(Attachment.attachment_type),
            joinedload(InboundShipment.shipment_lines).joinedload(InboundShipmentLine.product),
        ).filter(InboundShipment.id.in_(resolved_ids)).first()
        if not shipment:
            raise handle_not_found("Inbound Shipment", shipment_id)
        return self._attach_capacity(shipment)

    def get_received_quantities_by_product(self, shipment_id: str) -> dict[str, int]:
        """Return received qty per product for a shipment, ignoring warehouse boundaries.

        The received quantity is ``quantity_picked`` - the same column
        ``build_allocation_pool`` and ``compute_received_for_allocation`` measure,
        see the convention note in ``app.services.grn_spo_matching`` (AC-FM-28).
        This reader PERSISTS ``inbound_shipment_lines.quantity_received`` and
        ``line_status`` (via ``refresh_shipment_line_statuses``), so while it summed
        ``quantity_expected`` any line where picked differed from expected made the
        shipment and its own SPO allocation report different numbers for the same
        goods: a 60-of-100 short receipt read as 100 here and 60 there, and the
        container looked fully received when 40 of it never arrived.

        AC-E14 (PLAN-hide-retired-everywhere): deliberately UNFILTERED by
        `spo_supply.visible_line_clauses()`. This collects PickingLine receipts by
        product across every allocation on the shipment to find what actually landed;
        filtering could drop a REAL receipt rather than hide a phantom line - it looks
        up receipts by number, and a receipt found through a retired line is still a
        receipt.
        """
        received_totals: dict[str, int] = {}

        linked_rows = (
            self.db.query(
                SPOAllocation.product_id,
                func.coalesce(func.sum(PickingLine.quantity_picked), 0).label("total"),
            )
            .join(PickingLine, PickingLine.spo_allocation_id == SPOAllocation.id)
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .filter(
                SPOAllocation.inbound_shipment_id == shipment_id,
                PickingHeader.picking_type == "goods_received",
                PickingHeader.picking_status == "approved",
            )
            .group_by(SPOAllocation.product_id)
            .all()
        )
        for product_id, total in linked_rows:
            received_totals[str(product_id)] = int(total or 0)

        allocation_rows = (
            self.db.query(SPOAllocation.product_id, SPOAllocation.spo_number)
            .filter(
                SPOAllocation.inbound_shipment_id == shipment_id,
                SPOAllocation.spo_number.isnot(None),
            )
            .all()
        )
        spo_numbers_by_product: dict[str, set[str]] = {}
        for product_id, spo_number in allocation_rows:
            normalized = _normalize_spo_number(spo_number)
            if not normalized:
                continue
            spo_numbers_by_product.setdefault(str(product_id), set()).add(normalized)

        if not spo_numbers_by_product:
            return self._floor_by_stated_received(shipment_id, received_totals)

        product_ids = list(spo_numbers_by_product.keys())
        all_spo_numbers = {
            spo_number
            for spo_numbers in spo_numbers_by_product.values()
            for spo_number in spo_numbers
        }
        norm_expr = func.replace(
            func.replace(func.trim(PickingHeader.spo_number), "/", "."),
            "\\",
            ".",
        )
        orphan_rows = (
            self.db.query(
                PickingLine.product_id,
                norm_expr.label("normalized_spo_number"),
                func.coalesce(func.sum(PickingLine.quantity_picked), 0).label("total"),
            )
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .filter(
                PickingLine.spo_allocation_id.is_(None),
                PickingLine.product_id.in_(product_ids),
                PickingHeader.picking_type == "goods_received",
                PickingHeader.picking_status == "approved",
                PickingHeader.spo_number.isnot(None),
                norm_expr.in_(all_spo_numbers),
            )
            .group_by(PickingLine.product_id, norm_expr)
            .all()
        )
        for product_id, normalized_spo_number, total in orphan_rows:
            product_key = str(product_id)
            if normalized_spo_number not in spo_numbers_by_product.get(product_key, set()):
                continue
            received_totals[product_key] = received_totals.get(product_key, 0) + int(total or 0)

        return self._floor_by_stated_received(shipment_id, received_totals)

    def _floor_by_stated_received(
        self, shipment_id: str, received_totals: dict[str, int]
    ) -> dict[str, int]:
        """D35 (SPO-XLSX-SUPERSEDE round 2): per product, never less than what the
        AutoCount lines on this shipment themselves STATE arrived.

        `stated_received` is the declared receipt (AutoCount's TransferedQty, or a
        supersede carry - `app/models/procurement.py`), written only by those
        declarers, so it is the book's own word and not a second count of the same
        picks. The pick total above can fall short of it when a GRN drew against a
        row that is not on this shipment (the owner's GCXU6137164: 4 picked against
        a PL-less Excel row, so the container read 95 of 99 and the chatbot reported
        4 still incoming on an SPO AutoCount called fully received). `max`, never a
        sum: when the picks do hang off these lines the two figures describe the
        same goods. A RETIRED line is left out entirely: its frozen statement
        (D28c/D28d) belongs to a document AutoCount stopped naming, and the live
        line-set that replaced it states the same goods again - counting both would
        call a container received that is still on the water. Its picks, if any,
        are already in the total above.
        """
        stated_rows = (
            self.db.query(
                SPOAllocation.product_id,
                # Each line's statement capped at what it was ordered for
                # (security review S1): a pushed TransferedQty above the line
                # cannot mark more of the container received than the line could
                # ever have brought.
                func.coalesce(
                    func.sum(
                        func.least(
                            SPOAllocation.stated_received,
                            func.coalesce(SPOAllocation.allocated_quantity, 0),
                        )
                    ),
                    0,
                ),
            )
            .filter(
                SPOAllocation.inbound_shipment_id == shipment_id,
                SPOAllocation.stated_received.isnot(None),
                SPOAllocation.retired_at.is_(None),
            )
            .group_by(SPOAllocation.product_id)
            .all()
        )
        for product_id, stated in stated_rows:
            key = str(product_id)
            received_totals[key] = max(received_totals.get(key, 0), int(stated or 0))
        return received_totals

    def open_shipment_ids(self, company_id: Optional[str] = None) -> list[str]:
        """Shipments with at least one line whose stored status is not `received`,
        oldest first: the only ones whose stored figures can still be behind a
        receipt (the nightly refresh and `refresh_container_received.py --all-open`).
        `company_id` narrows explicitly, independent of the session's scope."""
        not_received = exists().where(
            InboundShipmentLine.shipment_id == InboundShipment.id,
            func.coalesce(InboundShipmentLine.line_status, "") != "received",
        )
        query = self.db.query(InboundShipment.id).filter(not_received)
        if company_id:
            query = query.filter(InboundShipment.company_id == company_id)
        return [str(row[0]) for row in query.order_by(InboundShipment.created_at, InboundShipment.id).all()]

    def refresh_shipment_line_statuses(self, shipment_id: str) -> None:
        """Recompute and persist line_status for all lines of this shipment (for n8n/API).

        The lines are read off the RELATIONSHIP, never with a direct query on
        `InboundShipmentLine`. The company-scope filter is applied to a statement's
        top-level entities only and deliberately skips relationship loads, so a line
        mis-stamped with another company answered a direct query with nothing: this
        method returned at `if not lines` and left a fully received container reading
        `in_transit` forever, while the detail page (a joined relationship load) showed
        the very lines it could not find.

        `spo_allocated_quantity` / `quantity_received` are APPORTIONED per line (S4,
        AC-D5, `_apportion`) rather than the product's whole total stamped onto every
        line of it - a container carrying the same product on two lines (Kailu's own
        carton split) used to give BOTH lines the whole figure, doubling what counted
        as incoming. Each product's own lines are walked in `(created_at, id)` order.
        """
        shipment = (
            self.db.query(InboundShipment)
            .filter(InboundShipment.id == shipment_id)
            .first()
        )
        if shipment is None:
            return
        lines = list(shipment.shipment_lines)
        if not lines:
            return
        figures = self.compute_shipment_line_figures(shipment)
        for line in lines:
            # Self-heal: a line belongs to the company of the container it hangs off,
            # and an earlier company-less write may have stamped it with the incumbent
            # company instead. Put it back, or the next scoped read loses it again.
            if shipment.company_id and line.company_id != shipment.company_id:
                line.company_id = shipment.company_id
            alloc, recv, status = figures[str(line.id)]
            line.spo_allocated_quantity = alloc
            line.quantity_received = recv
            line.line_status = status
        all_lines_received = all((line.line_status or "").strip().lower() == "received" for line in lines)
        if all_lines_received:
            shipment.shipment_status = "fully_received"
        elif (shipment.shipment_status or "").strip().lower() in ("received", "fully_received"):
            shipment.shipment_status = "in_transit"
        self.db.commit()

    def compute_shipment_line_figures(self, shipment: InboundShipment) -> dict[str, tuple[int, int, str]]:
        """`line id -> (spo_allocated_quantity, quantity_received, line_status)` as
        `refresh_shipment_line_statuses` would store them, without writing anything
        (the dry run of `scripts/oneoff/refresh_container_received.py` prints these
        beside the stored figures)."""
        shipment_id = shipment.id
        lines = list(shipment.shipment_lines)
        totals_alloc = (
            self.db.query(SPOAllocation.product_id, func.sum(SPOAllocation.allocated_quantity).label("total"))
            .filter(SPOAllocation.inbound_shipment_id == shipment_id)
            .group_by(SPOAllocation.product_id)
            .all()
        )
        spo_by_product = {str(p): int(t) for p, t in totals_alloc}
        received_by_product = self.get_received_quantities_by_product(shipment_id)

        lines_by_product: dict[str, list] = {}
        for line in lines:
            lines_by_product.setdefault(str(line.product_id), []).append(line)
        alloc_by_line: dict[str, int] = {}
        recv_by_line: dict[str, int] = {}
        for product_id, product_lines in lines_by_product.items():
            product_lines.sort(key=lambda l: (l.created_at or datetime.min, str(l.id)))
            alloc_by_line.update(
                _apportion(spo_by_product.get(product_id, 0), product_lines)
            )
            recv_by_line.update(
                _apportion(received_by_product.get(product_id, 0), product_lines)
            )

        figures: dict[str, tuple[int, int, str]] = {}
        for line in lines:
            alloc = alloc_by_line.get(str(line.id), 0)
            recv = recv_by_line.get(str(line.id), 0)
            figures[str(line.id)] = (
                alloc,
                recv,
                compute_inbound_shipment_line_status(line.quantity_shipped or 0, alloc, recv),
            )
        return figures

    def _attach_capacity(self, shipment: InboundShipment) -> InboundShipment:
        """The fill gauge, computed onto the ORM object rather than stored (S5, ruling 1).

        `_fit` moved here from the proforma-invoice serializer: capacity is a property of
        the CONTAINER, and this shipment's own lines - not any one PI that fed it - are what
        the gauge measures. `line_cbm` (S12) is the SAME figure `consolidated_packing_list
        .build()` reads per line - stored `cbm`, else the line's own carton dimensions, else
        the catalogue's - so a line typed with only its own dimensions counts here too, not
        only on the Split card. It is also the same figure a convert wrote in the first
        place (per-unit volume times the placed quantity, or a supplier's own stated total
        on a real packing-list upload), so a percentage cannot drift between the convert's
        over-capacity refusal and this gauge.

        Set as plain attributes, the same pattern the packing-list route already uses for
        `spo_allocated_quantity` / `quantity_received` on a line: `InboundShipmentResponse`
        declares these fields and `from_attributes=True` picks them straight off the object.
        """
        lines = shipment.shipment_lines or []
        cbms = [_line_cbm(line) for line in lines]
        known = [c for c in cbms if c is not None]
        total_cbm = float(sum(known)) if known else None
        unmeasured = len(lines) - len(known)
        sizes_by_id, default_size = _container_sizes(self.db)
        result = _fit_capacity(shipment.container_size_id, total_cbm, sizes_by_id, default_size)
        setattr(shipment, "container_size_code", result["container_size_code"])
        setattr(shipment, "container_cbm", result["container_cbm"])
        setattr(shipment, "total_cbm", result["total_cbm"])
        setattr(shipment, "fill_pct", result["fill_pct"])
        setattr(shipment, "over_by_cbm", result["over_by_cbm"])
        setattr(shipment, "unmeasured_lines", unmeasured)
        return shipment

    def _derive_header_supplier(
        self, shipment: InboundShipment, payload_supplier_id: Optional[str] = None
    ) -> None:
        """The header's supplier is whatever its lines agree on, and nothing when they do not.

        Derived rather than stated, so it never lies, and TOTAL - every case sets the header
        rather than leaving a stale one behind:

        * no lines at all -> whatever the payload said (which may be nothing). There is
          nothing to derive from, so the caller's word is all there is.
        * exactly one distinct non-null supplier across the lines -> that supplier.
        * anything else (two or more, or every line unattributed) -> NULL. A mixed container
          has no single supplier, and an unattributed container is not owned by whoever
          happened to upload it last.

        The last case is why this is total: an n8n resend that names no supplier onto a
        container the header called Kailu used to leave "Kailu" on a header whose lines no
        longer say so, and every supplier filter believed it.
        """
        lines = (
            self.db.query(InboundShipmentLine)
            .filter(InboundShipmentLine.shipment_id == shipment.id)
            .all()
        )
        suppliers = {str(line.supplier_id) for line in lines if line.supplier_id}
        if not lines:
            shipment.supplier_id = payload_supplier_id
        elif len(suppliers) == 1:
            shipment.supplier_id = next(iter(suppliers))
        else:
            shipment.supplier_id = None

    def _upsert_shipment_lines(
        self,
        shipment: InboundShipment,
        incoming: list[dict],
        *,
        existing_lines: Optional[list[InboundShipmentLine]] = None,
    ) -> list[tuple[str, str]]:
        """Apply an explicit line set to a container without throwing its attribution away.

        An edit form states the whole line set, so a line nobody stated is gone. But it does
        NOT restate everything ABOUT a line: the procurement edit form sends
        `{product_id, quantity_shipped}`, and deleting-then-inserting on that payload set
        every line's supplier, cbm and remark to NULL - one save on a mixed container erased
        which factory packed what, which is exactly the loss the per-supplier line exists to
        stop. So an incoming line is matched to the one already there and UPDATED: what the
        payload states wins, what it leaves out is kept.

        `existing_lines` scopes which of the shipment's CURRENT lines are candidates for
        that matching (and so for deletion when nothing claims them) - the caller's whole
        set by default (`update_shipment`'s edit form states the container's whole line
        set), but `create_shipment`'s update-in-place path passes only the lines THIS
        upload speaks for (its own supplier's, per `_is_superseded_line`): a line outside
        that scope belongs to another factory's own packing list and is left alone,
        touched by neither the matching nor the departing-line delete below (browser-test
        round, finding 1 - the same reuse this method already gave the edit form is now
        also what a re-uploaded packing list gets, so a photo on a reused line survives).

        Matching:

        * a line that names a supplier matches on `(product, supplier)` - it says precisely
          which of the two factories' rows it is;
        * failing that, it CLAIMS the container's one unattributed line for that product:
          the n8n PDF path writes lines with no supplier at all, so on such a container
          every pair lookup missed and the save deleted-and-reinserted the lot, wiping the
          cbm and remarks the PDF had given. The row is the same goods; it gets the
          supplier the save states and keeps everything else;
        * a line that names none (nothing on it, nothing on the header) matches the one
          existing line for that product, and keeps that line's supplier;
        * when two factories both shipped that product there is no one line it could mean.
          Guessing would move a quantity from one factory to the other silently, so it is
          refused and the caller is told to say which. Same refusal when a supplier-stated
          line finds two unattributed rows to claim.

        Attributed lines are resolved before unattributed ones, so a payload that mixes the
        two claims the named rows first rather than by payload order.

        Returns the storage `(provider, key)` pairs a departing line's photos left behind
        (see `shipment_line_photos.purge_for_lines`) - the caller purges them, best-effort,
        AFTER its own commit, same ordering `delete_photo` uses.
        """
        existing = list(shipment.shipment_lines) if existing_lines is None else list(existing_lines)
        existing_by_id = {str(line.id): line for line in existing}
        by_pair: dict[tuple[str, Optional[str]], list[InboundShipmentLine]] = {}
        by_product: dict[str, list[InboundShipmentLine]] = {}
        for line in existing:
            product_id = str(line.product_id)
            supplier_id = str(line.supplier_id) if line.supplier_id else None
            by_pair.setdefault((product_id, supplier_id), []).append(line)
            by_product.setdefault(product_id, []).append(line)

        claimed: set[str] = set()
        updates: list[tuple[InboundShipmentLine, dict]] = []
        inserts: list[dict] = []

        def unclaimed(product_id: str, *, unattributed_only: bool) -> list[InboundShipmentLine]:
            return [
                line
                for line in by_product.get(product_id, [])
                if str(line.id) not in claimed
                and (not unattributed_only or line.supplier_id is None)
            ]

        def ambiguous(product_id: str):
            code = (
                self.db.query(Product.product_code)
                .filter(Product.id == product_id)
                .scalar()
            ) or product_id
            return handle_conflict(
                f"Product {code} is on this container from more than one supplier; "
                "state supplier_id per line"
            )

        def line_id_required(product_id: str, supplier_id: Optional[str]):
            code = (
                self.db.query(Product.product_code)
                .filter(Product.id == product_id)
                .scalar()
            ) or product_id
            where = "for this supplier" if supplier_id else "with no supplier stated"
            return AppException(
                409,
                f"Product {code} is on two lines {where}; edit them by line.",
                code="line_id_required",
            )

        # ID-first (S4, AC-D4): a payload line naming an id that IS one of this
        # container's own claims exactly that line, whatever its own (product, supplier)
        # says - the same key can now repeat (AC-D1's own carton split), so an edit that
        # already knows which line it means is never left to the guesswork below.
        id_claims: list[dict] = []
        remaining_incoming: list[dict] = []
        for raw in incoming or []:
            d = dict(raw)
            line_id = d.pop("id", None)
            if line_id and str(line_id) in existing_by_id:
                id_claims.append((str(line_id), d))
            else:
                remaining_incoming.append(d)
        for line_id, d in id_claims:
            target = existing_by_id[line_id]
            claimed.add(str(target.id))
            updates.append((target, d))
        incoming = remaining_incoming

        # Attributed first: see the docstring.
        for d in sorted(incoming, key=lambda d: d.get("supplier_id") is None):
            product_id = str(d["product_id"])
            supplier_id = str(d["supplier_id"]) if d.get("supplier_id") else None
            if supplier_id is not None:
                named = [
                    line for line in by_pair.get((product_id, supplier_id), [])
                    if str(line.id) not in claimed
                ]
                if len(named) > 1:
                    # AC-D1's own scenario: two lines already share (product, supplier)
                    # (Kailu's carton split) - an id-LESS edit naming only the pair
                    # cannot say which one it means, and guessing would move a quantity
                    # from one carton to the other silently.
                    raise line_id_required(product_id, supplier_id)
                target = named[0] if named else None
                if target is None:
                    # Nothing on this container under that supplier yet. Claim the
                    # unattributed row for the product rather than replacing it, so an
                    # n8n-created container survives its first edit-form save with its
                    # cbm and remarks. Rows belonging to ANOTHER supplier are never
                    # candidates - that would move goods between factories.
                    candidates = unclaimed(product_id, unattributed_only=True)
                    # Belt and braces: the unique index is NULLS NOT DISTINCT, so one
                    # product has at most one unattributed row and this cannot fire
                    # today. If that index ever changes, refuse rather than guess.
                    if len(candidates) > 1:
                        raise ambiguous(product_id)
                    if candidates:
                        target = candidates[0]
                        target.supplier_id = supplier_id
            else:
                candidates = unclaimed(product_id, unattributed_only=False)
                if len(candidates) > 1:
                    # Two DIFFERENT suppliers already ship this product and the payload
                    # names neither (pre-existing, unchanged) - `line_id_required` is for
                    # the NEW case below it, where the id-less line names a pair that is
                    # itself ambiguous (two lines of the SAME supplier).
                    if len({str(l.supplier_id) if l.supplier_id else None for l in candidates}) > 1:
                        raise ambiguous(product_id)
                    raise line_id_required(product_id, candidates[0].supplier_id)
                target = candidates[0] if candidates else None
            if target is None:
                inserts.append(d)
            else:
                claimed.add(str(target.id))
                updates.append((target, d))

        # Delete before insert: the unique index on (shipment, product, supplier) treats
        # NULL as a value, so an insert that reuses a departing row's key would collide if
        # the unit of work flushed the save first.
        departing = [line for line in existing if str(line.id) not in claimed]
        photo_objects: list[tuple[str, str]] = []
        if departing:
            departing_ids = [str(line.id) for line in departing]
            # The proforma-invoice links pointing at these lines go with them, HERE, in the
            # same transaction. The FK is ON DELETE SET NULL, which left a phantom behind:
            # a link naming a shipment but no line on it, so the invoice read as sitting on
            # a container that no longer holds its goods, refused every further write, and
            # could never be converted again. One writer for the deletion and the trail it
            # invalidates, rather than a sweeper that runs later and sometimes.
            from app.models.scm import ProformaInvoiceShipmentLink
            from app.services.scm import shipment_line_photos

            self.db.query(ProformaInvoiceShipmentLink).filter(
                ProformaInvoiceShipmentLink.inbound_shipment_line_id.in_(departing_ids)
            ).delete(synchronize_session=False)
            self.db.flush()
            # Same reasoning, for the line's own photos (browser-test round, finding 1):
            # `entity_attachment_links` carries no FK onto `inbound_shipment_lines`, so a
            # departing line's photo links would otherwise sit there naming an id nothing
            # resolves any more.
            photo_objects = shipment_line_photos.purge_for_lines(self.db, departing_ids)
        for line in departing:
            self.db.delete(line)
        self.db.flush()

        for line, d in updates:
            for field, value in d.items():
                if field in _CLEARABLE_LINE_FIELDS:
                    # A key PRESENT here already means "the payload stated this" - real
                    # text, or an explicit None to clear it - because `_merge_shipment_lines`
                    # never fabricates a value for `description` / `remarks` the way it does
                    # for `supplier_id` below; the key comes straight from the caller's
                    # `model_dump(exclude_unset=True)`. Skipping None here kept a cleared
                    # Description/Remarks reading its stale value forever (review, PR #594).
                    setattr(line, field, value)
                    continue
                # None means "the payload did not state it", never "clear it": supplier and
                # cbm are read off the packing list, not typed into the edit form that is
                # saving over them - and `supplier_id` is unconditionally written into the
                # dict above even when nothing named a supplier, so its own None has to stay
                # a no-op.
                if value is not None:
                    setattr(line, field, value)
        for d in inserts:
            self.db.add(
                InboundShipmentLine(
                    **d, shipment_id=shipment.id, **_line_company_kwargs(shipment)
                )
            )
        self.db.flush()
        return photo_objects

    def create_shipment(self, shipment_data: InboundShipmentCreate, created_by: str | None = None):
        """Create or update-in-place an inbound shipment with lines.

        TCK-2026-000020: on duplicate shipment_number we update the header + lines
        when the shipment is still editable (status not in completed/fully_received).
        When the shipment is already completed, reject with an explicit message so
        the caller knows the update path is unavailable.
        """
        # Match an existing shipment by business key (shipment_number) first, then
        # by the secondary identifier (shipping_container_number), then fall back to
        # the linked attachment_id so a re-upload/replace of the same packing-list
        # document updates in place instead of creating a duplicate.
        existing = None
        if shipment_data.shipment_number:
            existing = self.db.query(InboundShipment).filter(
                InboundShipment.shipment_number == shipment_data.shipment_number
            ).first()

        # Container lookup. A container carries one OPEN shipment at a time but is
        # reusable once received, so the status of the match decides the outcome:
        #   not received yet -> update in place (re-uploaded / corrected document)
        #   received + identical (container, ETA, shipment_date) triple
        #                    -> the same packing list uploaded twice; reject
        #   received + any date differs -> container genuinely reused; create new
        # Gated on a non-null container: without one the identity key would
        # collapse to shipment_date alone and would falsely match two different
        # suppliers shipping on the same day.
        # See documentation/plans/PLAN-packing-list-duplicate-detection.md
        if existing is None:
            container_key = _container_match_key(shipment_data.shipping_container_number)
            if container_key:
                candidates = (
                    self.db.query(InboundShipment)
                    .filter(
                        InboundShipment.shipping_container_number.isnot(None),
                        _container_key_sql(InboundShipment.shipping_container_number)
                        == container_key,
                    )
                    .order_by(InboundShipment.created_at.desc())
                    .all()
                )
                for candidate in candidates:
                    if not _is_received_status(candidate.shipment_status):
                        existing = candidate
                        break
                    if _packing_list_triple_matches(candidate, shipment_data):
                        raise DuplicatePackingListError(
                            _format_duplicate_packing_list_message(candidate, shipment_data),
                            candidate,
                        )

        if existing is None and shipment_data.attachment_id:
            existing = self.db.query(InboundShipment).filter(
                InboundShipment.attachment_id == shipment_data.attachment_id
            ).first()
        if existing:
            status_l = (getattr(existing, "shipment_status", None) or "").strip().lower()
            if status_l in ("fully_received", "completed"):
                ref = shipment_data.shipment_number or getattr(existing, "shipment_number", None) or existing.id
                raise handle_conflict(
                    f"Shipment '{ref}' already completed, cannot update."
                )
            # Update-in-place path: rewrite header + replace lines.
            shipment_dict = shipment_data.model_dump(exclude={"shipment_lines"})
            # Regions stated by the upload (payload, else the attachment's) apply like any other
            # field; none stated (None) leaves the container's own, via the `is not None` loop.
            shipment_dict["shipment_status"] = _normalize_inbound_shipment_status(
                shipment_dict.get("shipment_status")
            )
            for k, v in shipment_dict.items():
                if v is not None:
                    setattr(existing, k, v)
            from app.models.scm import ProformaInvoiceShipmentLink

            # AC-D4b (S4): a CONVERTED draft's lines are sourced from proforma invoices,
            # not this forwarder's own PDF/Excel - the manual -> n8n -> back flow updates
            # the HEADER only and leaves convert's own split lines untouched. A shipment
            # with no PI links keeps today's per-product line replacement below.
            has_pi_links = (
                self.db.query(ProformaInvoiceShipmentLink.id)
                .filter(ProformaInvoiceShipmentLink.inbound_shipment_id == existing.id)
                .first()
                is not None
            )
            photo_objects: list[tuple[str, str]]
            if has_pi_links:
                photo_objects = []
                setattr(existing, "lines_skipped_reason", "lines_from_proforma_invoices")
            else:
                # Replace lines, but only the ones this upload speaks for. One container
                # carries several factories and each sends its own packing list, so
                # replacing every line on a supplier-stated upload deleted the other
                # factories' lines - the data-loss this rule exists to end. An upload that
                # names NO supplier (the n8n PDF path, legacy callers) still speaks for
                # the whole container, as it always did.
                merged_lines = _merge_shipment_lines(
                    shipment_data.shipment_lines, shipment_data.supplier_id
                )
                incoming_suppliers = {
                    str(d["supplier_id"]) if d["supplier_id"] else None for d in merged_lines
                }
                incoming_products = {str(d["product_id"]) for d in merged_lines}
                if not merged_lines and shipment_data.supplier_id:
                    # No lines at all: the upload clears what that supplier had on the container.
                    incoming_suppliers = {str(shipment_data.supplier_id)}
                states_supplier = bool(shipment_data.supplier_id) or any(
                    (line.supplier_id or None) for line in (shipment_data.shipment_lines or [])
                )
                if states_supplier:
                    # REUSE: every real, in-app upload names a supplier (R12 asks for one per
                    # file), so this is the path a re-uploaded packing list actually takes.
                    # `_upsert_shipment_lines` matches an incoming line to the row already
                    # there by `(product, supplier)` and updates it IN PLACE rather than
                    # deleting and recreating: a re-upload used to mint a new line id every
                    # time, which orphaned that line's photos (`entity_attachment_links` has
                    # no real FK onto the line) - browser-test round, finding 1. Scoped to
                    # this upload's own lines, same as the delete used to be: a line outside
                    # `_is_superseded_line` belongs to another factory's own list and is left
                    # alone entirely.
                    scoped_existing = [
                        line
                        for line in existing.shipment_lines
                        if _is_superseded_line(line, incoming_suppliers, incoming_products)
                    ]
                    photo_objects = self._upsert_shipment_lines(
                        existing, merged_lines, existing_lines=scoped_existing
                    )
                else:
                    # RE-POINT is not applicable here either: every merged line's
                    # `supplier_id` is explicitly `None` (this upload names no supplier at
                    # all), which never matches an existing line's OWN (possibly attributed)
                    # supplier under the `(product, supplier)` key `_upsert_shipment_lines`
                    # reuses by - reusing here would leave a line's old, real supplier
                    # silently intact although nothing in this upload said so
                    # (`test_an_n8n_resend_clears_the_header_the_container_used_to_name`).
                    # This upload restates the WHOLE container from scratch (the n8n PDF
                    # path, legacy callers) - every existing line is superseded, deleted
                    # outright, and its photos purged with it before the fresh rows land.
                    departing_ids = [str(line.id) for line in existing.shipment_lines]
                    from app.services.scm import shipment_line_photos

                    if departing_ids:
                        self.db.query(ProformaInvoiceShipmentLink).filter(
                            ProformaInvoiceShipmentLink.inbound_shipment_line_id.in_(
                                departing_ids
                            )
                        ).delete(synchronize_session=False)
                        self.db.flush()
                    photo_objects = shipment_line_photos.purge_for_lines(self.db, departing_ids)
                    for line in existing.shipment_lines[:]:
                        self.db.delete(line)
                    self.db.flush()
                    for d in merged_lines:
                        line = InboundShipmentLine(
                            **d, shipment_id=existing.id, **_line_company_kwargs(existing)
                        )
                        self.db.add(line)
                    self.db.flush()
            self._derive_header_supplier(existing, shipment_data.supplier_id)
            self.db.commit()
            for provider, key in photo_objects:
                delete_object_best_effort(provider, key)
            self.db.refresh(existing)
            self.refresh_shipment_line_statuses(existing.id)
            # D6 (S3): a container this shipment now names may already sit on
            # `spo_allocations` rows written before the shipment existed
            # (or before its container was corrected on this same update).
            self._relink_allocations_for_shipment(existing)
            setattr(existing, "_already_existed", True)
            return self._attach_capacity(existing)

        # Create shipment and lines in transaction
        shipment_dict = shipment_data.model_dump(exclude={"shipment_lines"})
        shipment_dict["shipment_status"] = _normalize_inbound_shipment_status(
            shipment_dict.get("shipment_status")
        )
        shipment_dict["created_by"] = created_by
        if shipment_dict.get("regions") is None:
            shipment_dict.pop("regions", None)  # the column default (West only)
        if not (shipment_dict.get("shipment_number") or "").strip():
            # The create form no longer asks for one: a shipment number is ours to issue,
            # and asking somebody to invent a unique string before they have typed anything
            # about the container is a decision the system can make for them. Numbered from
            # the same rule the proforma-to-packing-list convert uses, so a container drafted
            # either way is named the same. AFTER the match attempts above, which key off the
            # number the CALLER stated - numbering first would make every upload a new row.
            from app.services.scm.proforma_invoice_service import _draft_shipment_number

            shipment_dict["shipment_number"] = _draft_shipment_number(self.db)
        shipment = InboundShipment(**shipment_dict)
        self.db.add(shipment)
        # Flushed before the lines are built, so the header's own auto-stamp has
        # already run and `_line_company_kwargs` has a company to copy.
        self.db.flush()  # Get the ID

        # Create lines if provided (one row per product PER SUPPLIER on this shipment;
        # duplicates within the payload are merged on that same pair)
        if shipment_data.shipment_lines:
            for d in _merge_shipment_lines(
                shipment_data.shipment_lines, shipment_data.supplier_id
            ):
                line = InboundShipmentLine(
                    **d, shipment_id=shipment.id, **_line_company_kwargs(shipment)
                )
                self.db.add(line)
            self.db.flush()
            self._derive_header_supplier(shipment, shipment_data.supplier_id)

        self.db.commit()
        self.db.refresh(shipment)
        self.refresh_shipment_line_statuses(shipment.id)
        # D6 (S3): same reason as the update-in-place path above - a brand
        # new shipment can still be the one an already-written allocation
        # was waiting on.
        self._relink_allocations_for_shipment(shipment)
        return self._attach_capacity(shipment)

    def _relink_allocations_for_shipment(self, shipment: "InboundShipment") -> None:
        """D6 (S3): best-effort, post-commit - a failure here must not turn a
        successful shipment write into a 500."""
        container = shipping_order_rules.extract_container_number(
            shipment.shipping_container_number
        )
        if not container:
            return
        try:
            relinked = shipping_order_rules.relink_allocations_for_container(
                self.db, container, company_id=shipment.company_id
            )
            if relinked:
                self.db.commit()
        except Exception:  # noqa: BLE001 - best-effort, the shipment already succeeded
            self.db.rollback()
            logger.warning(
                "shipment.relink_allocations_for_container_failed container=%s",
                container,
                exc_info=True,
            )

    def update_shipment(self, shipment_id: str, shipment_data: InboundShipmentUpdate, updated_by: str):
        """Update an inbound shipment. If shipment_lines provided, replace existing lines."""
        shipment = self.get_shipment(shipment_id)

        update_data = shipment_data.model_dump(exclude_unset=True, exclude={"shipment_lines"})
        if update_data.get("regions") is None:
            update_data.pop("regions", None)  # NOT NULL: an explicit null leaves it alone
        if "shipment_status" in update_data:
            update_data["shipment_status"] = _normalize_inbound_shipment_status(
                update_data.get("shipment_status")
            )
        for key, value in update_data.items():
            setattr(shipment, key, value)

        photo_objects: list[tuple[str, str]] = []
        if "shipment_lines" in shipment_data.model_dump(exclude_unset=True):
            # The whole line set, upserted onto what is already there - id FIRST
            # (`_upsert_shipment_lines`, S4/AC-D4), never merged: two lines of one
            # product from one supplier are legal now (Kailu's own carton split), and
            # `_merge_shipment_lines` would silently sum them back into one, which is
            # exactly the loss the per-line grid exists to stop. The external n8n create
            # route still merges (its payload has no carton grain) - only THIS edit-form
            # path drops it.
            header_supplier = getattr(shipment, "supplier_id", None)
            incoming = [
                {**d, "supplier_id": _effective_line_supplier(d, header_supplier)}
                for d in (
                    ln.model_dump(exclude_unset=True) if hasattr(ln, "model_dump") else dict(ln)
                    for ln in shipment_data.shipment_lines
                )
            ]
            photo_objects = self._upsert_shipment_lines(shipment, incoming)
            # The lines just changed, so the header has to be re-derived from them or it
            # keeps naming a supplier that is no longer on the container.
            self._derive_header_supplier(shipment, header_supplier)


        self.db.commit()
        for provider, key in photo_objects:
            delete_object_best_effort(provider, key)
        self.db.refresh(shipment)
        self.refresh_shipment_line_statuses(shipment_id)
        if "shipping_container_number" in update_data:
            # D6/S3 (review B6): `create_shipment` already relinks a
            # leftover allocation waiting on this container - `update_shipment`
            # never did, so SETTING a container on an existing container-less
            # shipment (the operator filling it in after the fact) left any
            # allocation already naming it stranded forever with
            # `inbound_shipment_id` still NULL.
            self._relink_allocations_for_shipment(shipment)
        return self._attach_capacity(shipment)

    def delete_shipment(self, shipment_id: str) -> None:
        """Delete an inbound shipment. Its own lines cascade; its SPO allocations do NOT.

        SPO-CASCADE (owner ruling 28 Sep 2026): a shipping order is a document in its own
        right, the shipment only the container it was booked on. The unit of work nulls
        `inbound_shipment_id` on every allocation of this shipment (an audited update),
        and a later shipment naming the same container relinks them
        (`_relink_allocations_for_shipment`). Never delete them here: the AutoCount sync
        does not re-push a document it already pushed.
        """
        shipment = self.get_shipment(shipment_id)
        self._unlink_allocations(shipment)
        self.db.delete(shipment)
        self.db.commit()

    def _unlink_allocations(self, shipment: "InboundShipment") -> int:
        """Null `inbound_shipment_id` on every allocation booked on `shipment`, explicitly
        and before the delete flushes.

        Explicit rather than left to the relationship, so each line's unlink can be
        written as an audit row naming who deleted the packing list (`_audit_spo_line`;
        the table opts out of the automatic hooks). It also holds on a database whose FK
        has not been migrated to SET NULL yet: the UPDATE flushes before the DELETE.
        """
        rows = (
            self.db.query(SPOAllocation)
            .filter(SPOAllocation.inbound_shipment_id == shipment.id)
            .all()
        )
        for row in rows:
            row.inbound_shipment_id = None
            _audit_spo_line(
                self.db,
                row,
                "UPDATE",
                old_values={"inbound_shipment_id": str(shipment.id)},
                new_values={"inbound_shipment_id": None},
                description=(
                    f"Unlinked from packing list {shipment.shipment_number or shipment.id}"
                    " on its deletion"
                ),
            )
        return len(rows)

    def bulk_delete_shipments(self, shipment_ids: list[str]) -> dict:
        """Delete multiple inbound shipments by ID. Returns message and deleted_count.

        Same contract as `delete_shipment`: SPO allocations are unlinked, never deleted.
        """
        if not shipment_ids:
            return {"message": "No packing lists to delete", "deleted_count": 0}
        shipments = self.db.query(InboundShipment).filter(InboundShipment.id.in_(shipment_ids)).all()
        for shipment in shipments:
            self._unlink_allocations(shipment)
            self.db.delete(shipment)
        self.db.commit()
        deleted = len(shipments)
        return {"message": f"{deleted} packing list(s) deleted", "deleted_count": deleted}


def _audit_spo_line(
    db: Session,
    row: "SPOAllocation",
    action: str,
    *,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    description: Optional[str] = None,
) -> None:
    """One explicit audit row for a staff-driven change to an SPO line (SPO-CASCADE).

    `spo_allocations` opts out of the default-on hooks (measured for the sync's churn,
    review B3, and the plan's 10x ceiling cannot credit the sync-writer exclusion), so a
    line that disappears leaves nothing behind unless the writer says so itself. This is
    that statement, attributed the way `audit_service.record` attributes: to the stamped
    actor. In the same transaction as the change, on purpose: a delete this table cannot
    account for is worse than a delete that fails.
    """
    from app.audit_context import get_actor
    from app.services.audit_service import _model_to_audit_dict, _uuid_or_none, log_audit

    actor = get_actor(db)
    log_audit(
        db,
        "spo_allocations",
        str(row.id),
        action,
        old_values=old_values if old_values is not None else _model_to_audit_dict(row),
        new_values=new_values,
        user_id=_uuid_or_none(actor.user_id) if actor is not None else None,
        ip_address=actor.ip_address if actor is not None else None,
        description=description,
        company_id=getattr(row, "company_id", None),
        skip_flush=True,
    )


def next_spo_line_number(
    db: Session, spo_number: str, *, taken: Optional[dict] = None
) -> int:
    """The next line number on a shipping order, 1 where it has none yet.

    The LINE is the identity of a row in `spo_allocations` since migration 420
    (`uk_spo_allocations_company_spo_line`), so every writer has to produce one; a row left
    with NULL is a row no re-upload can ever match again, and Postgres treats NULLs as
    distinct so the index would not even complain.

    `taken` is a caller-owned counter for a BATCH that adds several rows to one document
    before flushing any of them: without it every row of the batch reads the same maximum
    off the database and they all claim the same number.
    """
    if taken is not None and spo_number in taken:
        taken[spo_number] += 1
        return taken[spo_number]
    highest = (
        db.query(func.max(SPOAllocation.spo_line_number))
        .filter(SPOAllocation.spo_number == spo_number)
        .scalar()
    )
    nxt = int(highest or 0) + 1
    if taken is not None:
        taken[spo_number] = nxt
    return nxt


class SPOAllocationService:
    """Service for SPO allocation operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_allocations(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        shipment_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        receipt_status: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc"
    ):
        """List SPO allocations. quantity_received is computed on load from approved GRN lines."""
        from sqlalchemy.orm import joinedload
        from app.schemas.procurement import SPOAllocationResponse
        from app.services.scm import spo_supply
        q = self.db.query(SPOAllocation).options(
            joinedload(SPOAllocation.product),
            joinedload(SPOAllocation.warehouse),
            joinedload(SPOAllocation.inbound_shipment),
        )

        # R1/R6: a retired line AutoCount stopped naming is hidden from the grid.
        filters = [*spo_supply.visible_line_clauses()]

        shipment_ids = resolve_identifier(
            self.db,
            shipment_id,
            InboundShipment,
            code_fields=(
                "shipment_number", "shipping_container_number", "bill_of_lading_number",
                # `提单号` lands here (the SO field), not `bill_of_lading_number`, on
                # an upload made through the supplier-documents dialog (S3, review
                # round 1) - a lookup on the B/L alone missed every container
                # uploaded that way.
                "forwarder_order_ref", "invoice_number",
            ),
        )
        if shipment_ids is not None:
            if not shipment_ids:
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
            filters.append(SPOAllocation.inbound_shipment_id.in_(shipment_ids))

        warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if warehouse_ids is not None:
            if not warehouse_ids:
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
            filters.append(SPOAllocation.warehouse_id.in_(warehouse_ids))

        if receipt_status and receipt_status != "all":
            filters.append(
                SPOAllocation.receipt_status == _normalize_spo_receipt_status(receipt_status)
            )
        
        if query:
            filters.append(
                or_(
                    SPOAllocation.spo_number.ilike(f"%{query}%"),
                    SPOAllocation.inbound_shipment.has(InboundShipment.shipment_number.ilike(f"%{query}%")),
                    SPOAllocation.product.has(Product.product_code.ilike(f"%{query}%")),
                    SPOAllocation.product.has(Product.product_name.ilike(f"%{query}%"))
                )
            )
        
        if filters:
            q = q.filter(and_(*filters))
        
        sort_map = {
            "spo_number": SPOAllocation.spo_number,
            "created_at": SPOAllocation.created_at,
            "updated_at": SPOAllocation.updated_at,
        }
        sort_column = sort_map.get(sort_field, SPOAllocation.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc())
        else:
            q = q.order_by(sort_column.asc())
        
        total = q.count()
        offset = (page - 1) * limit
        allocations = q.offset(offset).limit(limit).all()
        data = []
        try:
            computed = [a for a in allocations if self._receipt_is_computed(a)]
            received_map = self.get_computed_received_map([str(a.id) for a in computed])
            for a in allocations:
                resp = SPOAllocationResponse.model_validate(a)
                if not self._receipt_is_computed(a):
                    # An imported document states its own receipt. Left alone.
                    data.append(resp)
                    continue
                rec = received_map.get(str(a.id), 0)
                data.append(resp.model_copy(update={
                    "quantity_received": rec,
                    "receipt_status": "received" if rec >= (a.allocated_quantity or 0) else "pending",
                }))
        except Exception:
            data = [SPOAllocationResponse.model_validate(a) for a in allocations]
        return {
            "data": data,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }

    def list_allocations_grouped_by_shipment(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        product_code: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        receipt_status: Optional[str] = None,
        sort_field: str = "shipment_number",
        sort_dir: str = "asc",
    ):
        """List inbound shipments that have matching SPO allocations as shipment summaries."""
        from app.schemas.procurement import (
            InboundShipmentSimple,
            ShipmentAllocationSummaryGroup,
        )
        from app.services.scm import spo_supply

        # Subquery / join: shipments that have at least one allocation matching filters
        q_shipments = (
            self.db.query(InboundShipment)
            .join(SPOAllocation, SPOAllocation.inbound_shipment_id == InboundShipment.id)
            .distinct()
        )

        resolved_warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if resolved_warehouse_ids is not None and not resolved_warehouse_ids:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }

        # R1/R6: a retired line AutoCount stopped naming is hidden from the grid, and a
        # shipment whose only allocations are hidden must not appear as a group of its own.
        shipment_filters = [*spo_supply.visible_line_clauses()]
        if resolved_warehouse_ids:
            shipment_filters.append(SPOAllocation.warehouse_id.in_(resolved_warehouse_ids))
        if receipt_status and receipt_status != "all":
            shipment_filters.append(
                SPOAllocation.receipt_status == _normalize_spo_receipt_status(receipt_status)
            )
        if product_code and product_code.strip():
            shipment_filters.append(
                SPOAllocation.product.has(Product.product_code.ilike(f"%{product_code.strip()}%"))
            )
        if query:
            q = query.strip()
            shipment_filters.append(
                or_(
                    SPOAllocation.spo_number.ilike(f"%{q}%"),
                    InboundShipment.shipment_number.ilike(f"%{q}%"),
                    InboundShipment.shipping_container_number.ilike(f"%{q}%"),
                    SPOAllocation.product.has(Product.product_code.ilike(f"%{q}%")),
                    SPOAllocation.product.has(Product.product_name.ilike(f"%{q}%")),
                )
            )
        if shipment_filters:
            q_shipments = q_shipments.filter(and_(*shipment_filters))

        allocation_filters = [*spo_supply.visible_line_clauses()]
        if resolved_warehouse_ids:
            allocation_filters.append(SPOAllocation.warehouse_id.in_(resolved_warehouse_ids))
        if receipt_status and receipt_status != "all":
            allocation_filters.append(
                SPOAllocation.receipt_status == _normalize_spo_receipt_status(receipt_status)
            )
        if product_code and product_code.strip():
            allocation_filters.append(
                SPOAllocation.product.has(Product.product_code.ilike(f"%{product_code.strip()}%"))
            )

        sort_map = {
            "shipment_number": InboundShipment.shipment_number,
            "created_at": InboundShipment.created_at,
        }
        sort_column = sort_map.get(sort_field, InboundShipment.shipment_number)
        if sort_dir == "desc":
            q_shipments = q_shipments.order_by(sort_column.desc())
        else:
            q_shipments = q_shipments.order_by(sort_column.asc())

        total = q_shipments.count()
        offset = (page - 1) * limit
        shipments_page = q_shipments.offset(offset).limit(limit).all()
        shipment_ids = [s.id for s in shipments_page]

        if not shipment_ids:
            return {
                "data": [],
                "pagination": {"total": total, "page": page, "limit": limit},
                "empty": True,
            }

        q_alloc = (
            self.db.query(
                SPOAllocation.inbound_shipment_id,
                func.count(SPOAllocation.id).label("matched_spo_allocations_count"),
            )
            .filter(SPOAllocation.inbound_shipment_id.in_(shipment_ids))
            .group_by(SPOAllocation.inbound_shipment_id)
        )
        if allocation_filters:
            q_alloc = q_alloc.filter(and_(*allocation_filters))
        allocation_counts = {
            str(shipment_id): int(count or 0)
            for shipment_id, count in q_alloc.all()
        }

        line_counts = {
            str(shipment_id): int(count or 0)
            for shipment_id, count in (
                self.db.query(
                    InboundShipmentLine.shipment_id,
                    func.count(InboundShipmentLine.id).label("shipment_lines_count"),
                )
                .filter(InboundShipmentLine.shipment_id.in_(shipment_ids))
                .group_by(InboundShipmentLine.shipment_id)
                .all()
            )
        }

        groups = []
        for ship in shipments_page:
            groups.append(
                ShipmentAllocationSummaryGroup(
                    inbound_shipment=InboundShipmentSimple.model_validate(ship),
                    matched_spo_allocations_count=allocation_counts.get(str(ship.id), 0),
                    shipment_lines_count=line_counts.get(str(ship.id), 0),
                )
            )

        return {
            "data": groups,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }

    def list_allocations_grouped_by_spo_number(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        product_code: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        receipt_status: Optional[str] = None,
        sort_field: str = "spo_number",
        sort_dir: str = "asc",
    ):
        """List SPO allocations grouped by spo_number (for list view by SPO). Paginates at DB level."""
        from sqlalchemy.orm import joinedload
        from sqlalchemy import func
        from app.schemas.procurement import (
            SPOAllocationResponse,
            SPOAllocationWithShippedResponse,
            SPOWithAllocationsGroup,
        )
        from app.services.scm import spo_supply

        # Base filter query (no eager load) - reuse for count and for page of spo_numbers
        q_base = self.db.query(SPOAllocation).filter(SPOAllocation.spo_number.isnot(None))
        # R1/R6: a retired line AutoCount stopped naming is hidden from the grid, both from
        # the count/page-of-numbers query and from the allocations loaded per number below.
        filters = [*spo_supply.visible_line_clauses()]
        resolved_warehouse_ids = resolve_identifier(
            self.db,
            warehouse_id,
            Warehouse,
            code_fields=("warehouse_code", "warehouse_name"),
        )
        if resolved_warehouse_ids is not None and not resolved_warehouse_ids:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }
        if resolved_warehouse_ids:
            filters.append(SPOAllocation.warehouse_id.in_(resolved_warehouse_ids))
        if receipt_status and receipt_status != "all":
            filters.append(
                SPOAllocation.receipt_status == _normalize_spo_receipt_status(receipt_status)
            )
        if product_code and product_code.strip():
            filters.append(
                SPOAllocation.product.has(Product.product_code.ilike(f"%{product_code.strip()}%"))
            )
        if query:
            q_str = query.strip()
            q_base = q_base.outerjoin(InboundShipment, SPOAllocation.inbound_shipment_id == InboundShipment.id)
            filters.append(
                or_(
                    SPOAllocation.spo_number.ilike(f"%{q_str}%"),
                    InboundShipment.shipment_number.ilike(f"%{q_str}%"),
                    InboundShipment.shipping_container_number.ilike(f"%{q_str}%"),
                    SPOAllocation.product.has(Product.product_code.ilike(f"%{q_str}%")),
                    SPOAllocation.product.has(Product.product_name.ilike(f"%{q_str}%")),
                )
            )
        if filters:
            q_base = q_base.filter(and_(*filters))

        sort_field_norm = (sort_field or "spo_number").strip().lower()
        sort_dir_norm = (sort_dir or "asc").strip().lower()
        if sort_dir_norm not in {"asc", "desc"}:
            sort_dir_norm = "asc"

        # Total count of distinct SPO numbers
        total = q_base.with_entities(func.count(func.distinct(SPOAllocation.spo_number))).scalar() or 0
        offset = (page - 1) * limit
        if total == 0:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }

        # Page of distinct spo_numbers at DB level.
        # For created_at sorting, order grouped spo_number rows by aggregate timestamp.
        spo_number_col = SPOAllocation.spo_number.label("spo_number")
        latest_created_at_col = func.max(SPOAllocation.created_at).label("latest_created_at")
        q_spo_page = q_base.with_entities(
            spo_number_col,
            latest_created_at_col,
        ).group_by(SPOAllocation.spo_number)

        order_col = latest_created_at_col if sort_field_norm == "created_at" else spo_number_col
        q_spo_page = q_spo_page.order_by(
            order_col.desc() if sort_dir_norm == "desc" else order_col.asc()
        ).offset(offset).limit(limit)

        spo_page = [r[0] for r in q_spo_page.all() if r[0]]

        if not spo_page:
            return {
                "data": [],
                "pagination": {"total": total, "page": page, "limit": limit},
                "empty": True,
            }

        # Load only allocations for this page of spo_numbers, with relations
        q_alloc = (
            self.db.query(SPOAllocation)
            .filter(SPOAllocation.spo_number.in_(spo_page))
            .options(
                joinedload(SPOAllocation.product),
                joinedload(SPOAllocation.warehouse),
                joinedload(SPOAllocation.inbound_shipment),
            )
            .order_by(SPOAllocation.spo_number, SPOAllocation.id)
        )
        if query:
            q_alloc = q_alloc.outerjoin(InboundShipment, SPOAllocation.inbound_shipment_id == InboundShipment.id)
        if filters:
            q_alloc = q_alloc.filter(and_(*filters))
        all_allocations = q_alloc.all()

        by_spo: dict[str, list] = {}
        for a in all_allocations:
            if a.spo_number and a.spo_number in spo_page:
                by_spo.setdefault(a.spo_number, []).append(a)

        shipment_ids = {
            a.inbound_shipment_id for allocs in by_spo.values() for a in allocs
            if a.inbound_shipment_id is not None
        }
        shipped_by_ship_product: dict[tuple[str, str], int] = {}
        if shipment_ids:
            lines_query = (
                self.db.query(InboundShipmentLine)
                .filter(InboundShipmentLine.shipment_id.in_(shipment_ids))
            )
            for line in lines_query.all():
                key = (line.shipment_id, line.product_id)
                shipped_by_ship_product[key] = shipped_by_ship_product.get(key, 0) + (line.quantity_shipped or 0)

        page_alloc_ids = [
            str(a.id)
            for spo_num in spo_page
            for a in by_spo.get(spo_num, [])
            if self._receipt_is_computed(a)
        ]
        try:
            received_map = self.get_computed_received_map(page_alloc_ids)
        except Exception:
            received_map = {}

        groups = []
        for spo_num in spo_page:
            allocs = by_spo.get(spo_num, [])
            alloc_responses = []
            for a in allocs:
                try:
                    data = SPOAllocationResponse.model_validate(a).model_dump()
                    if self._receipt_is_computed(a):
                        rec = received_map.get(str(a.id), 0)
                        data["quantity_received"] = rec
                        data["receipt_status"] = (
                            "received" if rec >= (a.allocated_quantity or 0) else "pending"
                        )
                    qty_shipped = shipped_by_ship_product.get((a.inbound_shipment_id, a.product_id))
                    data["quantity_shipped"] = qty_shipped
                    alloc_responses.append(SPOAllocationWithShippedResponse(**data))
                except Exception:
                    data = SPOAllocationResponse.model_validate(a).model_dump()
                    data["quantity_shipped"] = shipped_by_ship_product.get((a.inbound_shipment_id, a.product_id))
                    alloc_responses.append(SPOAllocationWithShippedResponse(**data))
            groups.append(SPOWithAllocationsGroup(spo_number=spo_num, spo_allocations=alloc_responses))

        return {
            "data": groups,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }

    # ------------------------------------------------------------------------------
    # SPO document list + form view (PLAN-spo-investigation-grid.md S2, AC-11..AC-16b).
    #
    # A DOCUMENT read of `spo_allocations`, grouped by `spo_number` at read time - no new
    # table (plan "Not in scope"). `outstanding` line membership is
    # `spo_supply.open_incoming_clauses()` AND balance > 0 - the fifth reader of ONE rule
    # (AC-12), imported and never restated.
    # ------------------------------------------------------------------------------

    def list_documents(
        self,
        page: int = 1,
        limit: int = 50,
        state: str = "outstanding",
        product_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        overdue_only: bool = False,
        query: Optional[str] = None,
        sort_field: str = "spo_number",
        sort_dir: str = "desc",
    ):
        """`GET /spo-allocations/documents`: paged header rows grouped by `spo_number`.

        The grouping, the header rollups and the paging all happen in SQL against the
        80k-row table (AC-11) - a second, small query reads only the page's own documents
        (at most `limit` of them) to break the majority-supplier tie, the same two-phase
        shape `list_allocations_grouped_by_spo_number` already uses above.
        """
        from app.services.scm import spo_supply

        # The SAME clock the detail route reads (`get_document`'s `date.today()`,
        # review S7) - `func.current_date()` asks the DB server instead of the app
        # server, and the two can disagree by a few minutes around midnight, which
        # would put a line in this list's Overdue count that the document it opens
        # does not agree is late.
        today = date.today()

        # R1/R4/R6: a retired line AutoCount stopped naming is hidden from the header
        # rollups (`total_allocated`, `total_received`, `line_count`) the same as it is
        # from every other grid - one clause, imported from `spo_supply`.
        is_visible = and_(*spo_supply.visible_line_clauses())
        # Round 3 S1 (reviewer): `is_outstanding` gates on visibility too, not only on
        # `open_incoming_clauses()` - a hidden row is never "closed" by any DB
        # constraint (retirement and line_status are independent columns), so without
        # this a hidden-but-line_status='open' row could still count towards Balance,
        # status, worst overdue and earliest ETA here while `get_document` (which
        # already filters the row out entirely) does not agree.
        is_outstanding = and_(
            is_visible,
            *spo_supply.open_incoming_clauses(),
            SPOAllocation.allocated_quantity > func.coalesce(SPOAllocation.quantity_received, 0),
        )
        arrival_expr = func.coalesce(
            InboundShipment.eta_delay_date,
            InboundShipment.estimated_arrival_date,
            SPOAllocation.expected_date,
        )
        overdue_expr = func.greatest(literal(today) - arrival_expr, 0)
        balance_expr = SPOAllocation.allocated_quantity - func.coalesce(SPOAllocation.quantity_received, 0)
        has_outstanding_expr = func.coalesce(func.bool_or(is_outstanding), False)
        balance_sum_expr = func.coalesce(func.sum(case((is_outstanding, balance_expr), else_=0)), 0)
        worst_overdue_expr = func.coalesce(func.max(case((is_outstanding, overdue_expr), else_=0)), 0)
        earliest_eta_expr = func.min(case((is_outstanding, arrival_expr), else_=None))
        # PLAN-spo-list-container-number.md AC-5: the Container No column sorts by the
        # document's first container over its VISIBLE lines - MIN, so a raw or linked
        # container both count and a hidden line never votes; `nullslast()` below is
        # what puts a document with no container last in EITHER direction.
        container_value_expr = func.coalesce(
            SPOAllocation.container_number, InboundShipment.shipping_container_number
        )
        containers_sort_expr = func.min(case((is_visible, container_value_expr), else_=None))
        # COALESCE(min(issue_date), min(created_at)) (review S4): `issue_date` is the
        # document's own date; `created_at` is the import timestamp, a fallback for
        # rows a bare shipment-allocation import never set `issue_date` on. Cast the
        # `created_at` MIN to DATE so the two arms of the coalesce agree on type.
        doc_date_expr = func.coalesce(
            func.min(SPOAllocation.issue_date),
            cast(func.min(SPOAllocation.created_at), Date),
        )
        # Round 3 B2 (reviewer, "the ghost document"): shared by the SELECT's
        # `line_count`, the sort_map's `line_count` entry, and the unconditional HAVING
        # below that drops a document with no visible line from every state - the
        # aggregates were already gated on `is_visible`, but document MEMBERSHIP was
        # not, so a document whose every line retirement hid still grouped as a 0-line
        # row here and 404ed the moment `get_document` (which filters the rows out
        # entirely) was opened for it.
        visible_line_count_expr = func.count(case((is_visible, SPOAllocation.id), else_=None))

        # Which DOCUMENTS have >=1 line matching product/warehouse/query (Q10) - filters
        # match LINES, the list shows the whole document (every other line included).
        # A correlated EXISTS (review S1), not a materialised id list into an unbounded
        # `IN (...)`: the outer query groups by `spo_number`, and this subquery
        # correlates on that SAME column, so every LINE of one document sees the
        # identical boolean - the predicate decides document membership, never which
        # of a document's own lines survive the aggregate.
        #
        # `Product` stays UNALIASED (measured, not assumed - an `aliased(Product)`
        # here 500s: `psycopg2.errors.UndefinedTable`, because the company-scope
        # listener's `with_loader_criteria(Product, ..., include_aliases=True)`
        # cannot find the alias from OUTSIDE this correlated subquery - Product is
        # never referenced anywhere else in this query - and falls back to an
        # unaliased `products.company_id` that has no FROM-clause entry here. A
        # nested, UNCORRELATED `product_id IN (SELECT id FROM products WHERE ...)`
        # needs no alias in the first place, which is the actual fix, not a
        # workaround for it.
        match_filter = None
        if product_id or warehouse_id or (query and query.strip()):
            MatchLine = aliased(SPOAllocation)
            match_conditions = [MatchLine.spo_number == SPOAllocation.spo_number]
            if product_id:
                match_conditions.append(MatchLine.product_id == product_id)
            if warehouse_id:
                match_conditions.append(MatchLine.warehouse_id == warehouse_id)
            if query and query.strip():
                q_str = query.strip()
                matching_product_ids = select(Product.id).where(
                    or_(
                        Product.product_code.ilike(f"%{q_str}%"),
                        Product.product_name.ilike(f"%{q_str}%"),
                    )
                )
                # Same shape as `matching_product_ids` (AC-25): a nested, UNCORRELATED
                # `id IN (SELECT ...)` needs no alias, unlike a join, which is why
                # `Warehouse`/`InboundShipment` stay unaliased here too (see the
                # `Product` comment above this block for the company-scope reason).
                matching_warehouse_ids = select(Warehouse.id).where(
                    Warehouse.warehouse_code.ilike(f"%{q_str}%")
                )
                matching_shipment_ids = select(InboundShipment.id).where(
                    or_(
                        InboundShipment.shipment_number.ilike(f"%{q_str}%"),
                        InboundShipment.shipping_container_number.ilike(f"%{q_str}%"),
                    )
                )
                match_conditions.append(
                    or_(
                        MatchLine.spo_number.ilike(f"%{q_str}%"),
                        MatchLine.product_id.in_(matching_product_ids),
                        MatchLine.warehouse_id.in_(matching_warehouse_ids),
                        MatchLine.inbound_shipment_id.in_(matching_shipment_ids),
                        # AC-4 (PLAN-spo-list-container-number.md): the raw, unlinked
                        # `spo_allocations.container_number` - the shipment's own
                        # `shipping_container_number` is already covered above via
                        # `matching_shipment_ids`.
                        MatchLine.container_number.ilike(f"%{q_str}%"),
                    )
                )
            match_filter = select(MatchLine.id).where(*match_conditions).exists()

        rollup = (
            self.db.query(
                SPOAllocation.spo_number.label("spo_number"),
                doc_date_expr.label("doc_date"),
                func.coalesce(
                    func.sum(case((is_visible, SPOAllocation.allocated_quantity), else_=0)), 0
                ).label("total_allocated"),
                func.coalesce(
                    func.sum(
                        case(
                            (is_visible, func.coalesce(SPOAllocation.quantity_received, 0)),
                            else_=0,
                        )
                    ),
                    0,
                ).label("total_received"),
                visible_line_count_expr.label("line_count"),
                has_outstanding_expr.label("has_outstanding"),
                balance_sum_expr.label("balance"),
                worst_overdue_expr.label("worst_overdue_days"),
                earliest_eta_expr.label("earliest_eta"),
            )
            .outerjoin(InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id)
            .filter(SPOAllocation.spo_number.isnot(None))
        )
        if match_filter is not None:
            rollup = rollup.filter(match_filter)
        rollup = rollup.group_by(SPOAllocation.spo_number)

        # Round 3 B2: unconditional, every state - a document with no visible line
        # does not appear here under "outstanding", "completed" OR "all". `get_document`
        # keeps its 404 for the same number, so the two now agree instead of the list
        # offering a 0-line row that errors the moment it is opened.
        rollup = rollup.having(visible_line_count_expr > 0)
        state_norm = (state or "outstanding").strip().lower()
        if state_norm == "outstanding":
            rollup = rollup.having(has_outstanding_expr.is_(True))
        elif state_norm == "completed":
            rollup = rollup.having(has_outstanding_expr.is_(False))
        if overdue_only:
            rollup = rollup.having(worst_overdue_expr > 0)

        total = rollup.count()
        if total == 0:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }

        sort_map = {
            "spo_number": SPOAllocation.spo_number,
            "doc_date": doc_date_expr,
            "created_at": doc_date_expr,
            "total_allocated": func.sum(case((is_visible, SPOAllocation.allocated_quantity), else_=0)),
            "line_count": visible_line_count_expr,
            "balance": balance_sum_expr,
            "worst_overdue_days": worst_overdue_expr,
            "earliest_eta": earliest_eta_expr,
            "containers": containers_sort_expr,
        }
        sort_dir_norm = (sort_dir or "desc").strip().lower()
        order_col = sort_map.get((sort_field or "spo_number").strip().lower(), SPOAllocation.spo_number)
        if sort_dir_norm == "asc":
            rollup = rollup.order_by(order_col.asc().nullslast(), SPOAllocation.spo_number.asc())
        else:
            rollup = rollup.order_by(order_col.desc().nullslast(), SPOAllocation.spo_number.desc())

        offset = (page - 1) * limit
        page_rows = rollup.offset(offset).limit(limit).all()
        spo_numbers_page = [r.spo_number for r in page_rows if r.spo_number]
        if not spo_numbers_page:
            return {
                "data": [],
                "pagination": {"total": total, "page": page, "limit": limit},
                "empty": total == 0,
            }

        supplier_map = self._document_supplier_rollup(spo_numbers_page)
        container_map = self._document_container_rollup(spo_numbers_page)

        rows = []
        for r in page_rows:
            majority_name, extra_count = supplier_map.get(r.spo_number, (None, 0))
            rows.append(
                SPODocumentRow(
                    id=r.spo_number,
                    spo_number=r.spo_number,
                    doc_date=r.doc_date,
                    supplier_name=majority_name,
                    supplier_extra_count=extra_count,
                    status="outstanding" if r.has_outstanding else "completed",
                    earliest_eta=r.earliest_eta,
                    total_allocated=int(r.total_allocated or 0),
                    total_received=int(r.total_received or 0),
                    balance=int(r.balance or 0),
                    line_count=int(r.line_count or 0),
                    worst_overdue_days=int(r.worst_overdue_days or 0),
                    containers=container_map.get(r.spo_number, []),
                )
            )
        return {
            "data": rows,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": False,
        }

    def _document_supplier_rollup(self, spo_numbers: List[str]) -> Dict[str, tuple]:
        """Majority supplier + how many others disagree, per document (Q8, AC-15).

        Reads only the page's own documents - the small second query the two-phase
        shape above always pays, never the whole table.
        """
        if not spo_numbers:
            return {}
        from app.services.scm import spo_supply

        # ONE join, not two (review: a SECOND `aliased(Supplier)` join is not a
        # SQLAlchemy/Postgres limitation - `project_supply_service._spo_rows` joins
        # `Supplier` unaliased AND `aliased(Supplier)` in the same query and works fine).
        # This stays a single join because `coalesce(shipment.supplier_id,
        # line.supplier_id)` already picks "whichever id is present" as ONE key, so a
        # second join would only return a second, always-empty row to coalesce the
        # NAME from afterwards - one join less for the same answer.
        supplier_key = func.coalesce(InboundShipment.supplier_id, SPOAllocation.supplier_id)
        rows = (
            self.db.query(
                SPOAllocation.spo_number,
                Supplier.supplier_name,
                func.count(SPOAllocation.id).label("cnt"),
            )
            .outerjoin(InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id)
            .outerjoin(Supplier, Supplier.id == supplier_key)
            .filter(
                SPOAllocation.spo_number.in_(spo_numbers),
                # Round 3 S2 (reviewer): the same clause `get_document`'s own
                # `supplier_counts` reads (below), or the majority supplier this list
                # shows can differ from the one the page it opens shows - a hidden
                # line's supplier must not vote.
                *spo_supply.visible_line_clauses(),
            )
            .group_by(SPOAllocation.spo_number, Supplier.supplier_name)
            .all()
        )
        by_doc: Dict[str, list] = {}
        for spo_number, supplier_name, cnt in rows:
            by_doc.setdefault(spo_number, []).append((supplier_name, int(cnt or 0)))
        result: Dict[str, tuple] = {}
        for spo_number, entries in by_doc.items():
            # Majority by line count, ties broken by name for a deterministic result.
            entries.sort(key=lambda e: (-e[1], e[0] or ""))
            majority_name = entries[0][0]
            extra = max(len(entries) - 1, 0)
            result[spo_number] = (majority_name, extra)
        return result

    def _document_container_rollup(self, spo_numbers: List[str]) -> Dict[str, List[SPODocumentContainer]]:
        """One `{container_number, shipment_id}` entry per distinct container over each
        document's VISIBLE lines, sorted by container number (PLAN-spo-list-container-
        number.md AC-1/AC-2/AC-3).

        Same two-phase shape as `_document_supplier_rollup` above: reads only the
        page's own documents. Dedup happens in Python because the winner between a
        linked and a raw duplicate (AC-3) is "carries a shipment id", not a SQL
        aggregate a GROUP BY can express in one pass.
        """
        if not spo_numbers:
            return {}
        from app.services.scm import spo_supply

        container_expr = func.coalesce(
            SPOAllocation.container_number, InboundShipment.shipping_container_number
        )
        rows = (
            self.db.query(
                SPOAllocation.spo_number,
                container_expr.label("container_number"),
                SPOAllocation.inbound_shipment_id,
            )
            .outerjoin(InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id)
            .filter(
                SPOAllocation.spo_number.in_(spo_numbers),
                container_expr.isnot(None),
                *spo_supply.visible_line_clauses(),
            )
            .distinct()
            # Deterministic tiebreak (review S2): one container number can name several
            # DIFFERENT `inbound_shipment_id`s on the same document (39 on the prod
            # copy) - ASC sorts NULLS LAST by default, so a linked row always sorts
            # ahead of a raw duplicate for the same container ("linked wins over raw"
            # falls out of the ordering, no separate branch needed), and among several
            # shipments sharing one container the smallest id wins, every run.
            .order_by(SPOAllocation.spo_number, container_expr, SPOAllocation.inbound_shipment_id)
            .all()
        )
        by_doc: Dict[str, Dict[str, Optional[str]]] = {}
        for spo_number, container_number, shipment_id in rows:
            entries = by_doc.setdefault(spo_number, {})
            # The first row per container already wins the ORDER BY above.
            entries.setdefault(container_number, shipment_id)
        result: Dict[str, List[SPODocumentContainer]] = {}
        for spo_number, entries in by_doc.items():
            result[spo_number] = [
                SPODocumentContainer(
                    container_number=container_number,
                    shipment_id=str(shipment_id) if shipment_id is not None else None,
                )
                for container_number, shipment_id in sorted(entries.items(), key=lambda kv: kv[0])
            ]
        return result

    def _planning_span_map(self, warehouse_ids: List[str]) -> Dict[str, str]:
        """Which of the four planning-visibility states each warehouse earns (AC-14).

        Priority: flagged into fulfilment planning -> `in_plan`; else some OTHER active
        warehouse's `pool_warehouse_id` points at it (the fulfilment-pool netting
        opt-in - a different mechanism from the "site pool" segment classification in
        `scm/pool_predicate.py`, which this is not) -> `pool`; else `off`. A line
        carrying no warehouse at all is `none`, decided by the caller - there is
        nothing to look up.
        """
        from app.services.scm.planning_predicate import in_fulfilment_planning

        if not warehouse_ids:
            return {}
        warehouses = {
            str(w.id): w
            for w in self.db.query(Warehouse).filter(Warehouse.id.in_(warehouse_ids)).all()
        }
        pool_ids = {
            str(pid)
            for (pid,) in self.db.query(Warehouse.pool_warehouse_id)
            .filter(Warehouse.pool_warehouse_id.isnot(None))
            .distinct()
            .all()
        }
        result: Dict[str, str] = {}
        for wid, warehouse in warehouses.items():
            if in_fulfilment_planning(warehouse):
                result[wid] = "in_plan"
            elif wid in pool_ids and warehouse.is_active:
                result[wid] = "pool"
            else:
                result[wid] = "off"
        return result

    def get_document(self, spo_number: str) -> SPODocument:
        """`GET /spo-allocations/documents/{spo_number}`: header rollup + every line
        (AC-16). Computed over ALL of this one document's lines, in Python - a
        document's own line count is small next to the 80k-row table `list_documents`
        pages over, so there is nothing here for SQL to page.

        No server-side prev/next (S1 deviation, accepted 1 Sep): the frontend pager is
        cache-based (`useListPager`).
        """
        from app.services.scm import spo_supply

        is_open_expr = and_(*spo_supply.open_incoming_clauses())
        # ONE join, not two - see `_document_supplier_rollup`'s comment: a second
        # `aliased(Supplier)` join is not a SQLAlchemy/Postgres limitation, just an
        # extra join this coalesced key makes unnecessary (whichever id is present,
        # the shipment's own supplier or the line's).
        supplier_key = func.coalesce(InboundShipment.supplier_id, SPOAllocation.supplier_id)
        rows = (
            self.db.query(
                SPOAllocation,
                InboundShipment,
                Supplier.supplier_name,
                is_open_expr.label("is_open"),
            )
            .outerjoin(InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id)
            .outerjoin(Supplier, Supplier.id == supplier_key)
            .options(
                joinedload(SPOAllocation.product),
                joinedload(SPOAllocation.warehouse),
            )
            .filter(
                SPOAllocation.spo_number == spo_number,
                # R1/R6: a retired line AutoCount stopped naming is hidden from the
                # document's own lines list and its rollups below.
                *spo_supply.visible_line_clauses(),
            )
            .order_by(SPOAllocation.spo_line_number.asc().nullslast(), SPOAllocation.id)
            .all()
        )
        if not rows:
            raise handle_not_found("SPO document", spo_number)

        today = date.today()
        warehouse_ids = {
            str(allocation.warehouse_id) for allocation, *_ in rows if allocation.warehouse_id
        }
        span_map = self._planning_span_map(list(warehouse_ids))

        lines: List[SPODocumentLine] = []
        # Line-grain GRN linkage: the picking lines that name each allocation directly.
        # Sparse on live data (weak-matcher backfill), so the FE falls back to the
        # document's key-matched `linked_grns` for lines this map does not cover.
        line_grns: Dict[str, list] = {}
        alloc_ids = [str(a.id) for a, *_ in rows]
        if alloc_ids:
            for pl_alloc_id, ph_id, ph_number, ph_status, ph_date in (
                self.db.query(
                    PickingLine.spo_allocation_id,
                    PickingHeader.id,
                    PickingHeader.picking_number,
                    PickingHeader.picking_status,
                    PickingHeader.picking_date,
                )
                .join(PickingHeader, PickingHeader.id == PickingLine.picking_header_id)
                .filter(
                    PickingLine.spo_allocation_id.in_(alloc_ids),
                    PickingHeader.picking_type == "goods_received",
                )
                .all()
            ):
                line_grns.setdefault(str(pl_alloc_id), []).append(
                    {
                        "id": str(ph_id),
                        "picking_number": ph_number,
                        "picking_status": ph_status,
                        "picking_date": ph_date,
                    }
                )

        # R23, AC-J2: which purchase-order line each allocation PULLED from, via
        # `SPOAllocation.po_line_id`. No `line_no` on `purchase_order_lines` to name (the
        # model carries no per-line ordinal), so that field is always null - the FE renders
        # the PO number as the link's own label either way.
        po_line_ids = {str(a.po_line_id) for a, *_ in rows if a.po_line_id}
        po_info: Dict[str, dict] = {}
        if po_line_ids:
            from app.models.procurement import PurchaseOrder as _PurchaseOrder

            for pol_id, po_id, po_number in (
                self.db.query(
                    PurchaseOrderLine.id, _PurchaseOrder.id, _PurchaseOrder.po_number
                )
                .join(_PurchaseOrder, _PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
                .filter(PurchaseOrderLine.id.in_(po_line_ids))
                .all()
            ):
                po_info[str(pol_id)] = {
                    "po_number": po_number,
                    "purchase_order_id": str(po_id),
                    "line_no": None,
                }

        # R23, AC-J2: every sales order this allocation covers - `order_inquiry_links`
        # (project, R19's ticks already write these) keyed by `spo_allocation_id`, and the
        # RETAIL half off each SPO line's own `source_ref.so_coverage` (R20 as amended,
        # review round 1): that JSON is the authoritative record of a retail take - it is
        # what `create` and `revise` write, and what `_retail_coverage`, `coverage_for_so_
        # lines` and `planner_state` all read - while `scm.order_link_claim(source=
        # 'planner')` is a best-effort audit echo beside it. Reading the claim here made
        # this document disagree with the planner over the same take twice: a claim refused
        # at write time (that write cannot fail the confirm) showed nothing covered, and the
        # claim's own identity is one row per sales-order line, so two shipment lines of the
        # same product taking one sales order kept only the second.
        #
        # `source_ref` hangs off the SPO LINE, one level above the allocation, so a line
        # split across warehouses shows its coverage on the FIRST of its allocation rows -
        # the same "first if several" `coverage_for_so_lines` already applies to the mirror
        # image of this join, and the only reading that lets the header's `so_qty` foot.
        so_covered_by_alloc: Dict[str, list] = {}
        if alloc_ids:
            from app.models.order import Customer as _Customer
            from app.models.order import SalesOrder as _SalesOrder
            from app.models.order import SalesOrderLine as _SalesOrderLine
            from app.models.project_so import (
                OrderInquiry,
                OrderInquiryLink,
                OrderInquiryRow,
                ProjectSalesOrder,
            )
            from app.models.projects import Project as _Project

            for spo_alloc_id, doc_no, provisional, link_qty, project_title in (
                self.db.query(
                    OrderInquiryLink.spo_allocation_id,
                    ProjectSalesOrder.autocount_doc_no,
                    ProjectSalesOrder.provisional_ref,
                    OrderInquiryLink.qty,
                    _Project.title,
                )
                .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
                .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
                .join(
                    ProjectSalesOrder,
                    ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
                )
                .outerjoin(_Project, _Project.id == ProjectSalesOrder.project_id)
                .filter(OrderInquiryLink.spo_allocation_id.in_(alloc_ids))
                .all()
            ):
                # The SALES side's own document number (`_project_coverage`'s own rule) -
                # `OrderInquiryLink.document` is the PURCHASE side's number instead (already
                # named by THIS document's own `spo_number`), so it is never what "SO
                # covered" means to state here.
                so_covered_by_alloc.setdefault(str(spo_alloc_id), []).append(
                    {
                        "document": doc_no or provisional,
                        "customer": project_title,
                        "demand_class": "project",
                        "qty": float(link_qty or 0),
                    }
                )
            from app.services.scm import spo_conversion_service as _spo_conv

            # The FIRST allocation of each SPO line, in the order this document's own query
            # walked them (`spo_line_number`, then id) - see the note above.
            first_alloc_by_po_line: Dict[str, str] = {}
            for allocation, *_ in rows:
                if allocation.po_line_id:
                    first_alloc_by_po_line.setdefault(
                        str(allocation.po_line_id), str(allocation.id)
                    )
            coverage_rows = _spo_conv._spo_so_coverage_rows(
                self.db, po_line_ids=list(first_alloc_by_po_line.keys())
            )
            so_line_ids = {so_line_id for so_line_id, *_ in coverage_rows}
            so_info: Dict[str, tuple] = {}
            if so_line_ids:
                for sl_id, so_number, customer_name in (
                    self.db.query(
                        _SalesOrderLine.id,
                        _SalesOrder.so_number,
                        _Customer.customer_name,
                    )
                    .join(_SalesOrder, _SalesOrder.id == _SalesOrderLine.sales_order_id)
                    .outerjoin(_Customer, _Customer.id == _SalesOrder.customer_id)
                    .filter(_SalesOrderLine.id.in_(so_line_ids))
                    .all()
                ):
                    so_info[str(sl_id)] = (so_number, customer_name)
            for so_line_id, _spo_number, cover_qty, po_line_id, _po_id in coverage_rows:
                allocation_id = first_alloc_by_po_line.get(po_line_id)
                if not allocation_id:
                    continue
                so_number, customer_name = so_info.get(so_line_id, (None, None))
                so_covered_by_alloc.setdefault(allocation_id, []).append(
                    {
                        "document": so_number,
                        "customer": customer_name,
                        "demand_class": "retail",
                        "qty": float(cover_qty or 0),
                    }
                )

        supplier_counts: Dict[Optional[str], int] = {}
        for allocation, shipment, supplier_name, is_open in rows:
            allocated = allocation.allocated_quantity or 0
            received = allocation.quantity_received or 0
            arrival = None
            if shipment is not None:
                arrival = shipment.eta_delay_date or shipment.estimated_arrival_date
            arrival = arrival or allocation.expected_date
            outstanding = bool(is_open) and allocated > received
            # R5: a line that is not outstanding reads balance 0, so the grid can never
            # again disagree with its own header - `total_allocated`/`balance` above
            # already sum outstanding-only, this was the one reader still computing a
            # closed line's balance as if it were still owed.
            balance = max(allocated - received, 0) if outstanding else 0
            overdue_days = spo_supply.overdue_days(arrival, today) if outstanding else 0
            supplier_counts[supplier_name] = supplier_counts.get(supplier_name, 0) + 1

            warehouse_id = str(allocation.warehouse_id) if allocation.warehouse_id else None
            planning_span = span_map.get(warehouse_id, "off") if warehouse_id else "none"

            lines.append(
                SPODocumentLine(
                    id=str(allocation.id),
                    spo_number=allocation.spo_number,
                    product_id=str(allocation.product_id) if allocation.product_id else None,
                    product=ProductSimple.model_validate(allocation.product)
                    if allocation.product
                    else None,
                    warehouse_id=warehouse_id,
                    warehouse=WarehouseSimple.model_validate(allocation.warehouse)
                    if allocation.warehouse
                    else None,
                    allocated_quantity=allocated,
                    quantity_received=received,
                    quantity_rejected=allocation.quantity_rejected or 0,
                    balance=balance,
                    arrival_date=arrival,
                    overdue_days=overdue_days,
                    supplier_name=supplier_name,
                    supplier_id=str(allocation.supplier_id) if allocation.supplier_id else None,
                    expected_date=allocation.expected_date,
                    planning_span=planning_span,
                    receipt_status=allocation.receipt_status or "pending",
                    outstanding=outstanding,
                    inbound_shipment=InboundShipmentSimple.model_validate(shipment)
                    if shipment
                    else None,
                    grns=line_grns.get(str(allocation.id), []),
                    line_status=allocation.line_status,
                    po=po_info.get(str(allocation.po_line_id)) if allocation.po_line_id else None,
                    # The AutoCount book's own source purchase order, straight off the
                    # ingested column - text, never resolved into `po` above. The two can
                    # disagree (`po_line_id` names a CRM line, `from_po_number` names a
                    # different document) and neither is wrong when they do; see
                    # `PLAN-scm-book-linkage-on-document-lines.md` Slice B.
                    from_po_number=allocation.from_po_number,
                    so_covered=so_covered_by_alloc.get(str(allocation.id), []),
                )
            )

        outstanding_lines = [l for l in lines if l.outstanding]
        total_allocated = sum(l.allocated_quantity for l in lines)
        total_received = sum(l.quantity_received for l in lines)
        balance_sum = sum(l.balance for l in outstanding_lines)
        worst_overdue = max((l.overdue_days for l in outstanding_lines), default=0)
        earliest_eta = min(
            (l.arrival_date for l in outstanding_lines if l.arrival_date is not None),
            default=None,
        )
        # COALESCE(min(issue_date), min(created_at)) (review S4), the same rule
        # `list_documents`' `doc_date_expr` states in SQL: `issue_date` is the
        # document's own date; `created_at` (the import timestamp) only stands in
        # when NONE of the document's lines carry one.
        issue_dates = [
            allocation.issue_date for allocation, *_ in rows if allocation.issue_date is not None
        ]
        if issue_dates:
            doc_date = min(issue_dates)
        else:
            created_ats = [
                allocation.created_at for allocation, *_ in rows if allocation.created_at is not None
            ]
            doc_date = min(created_ats).date() if created_ats else None

        entries = sorted(supplier_counts.items(), key=lambda e: (-e[1], e[0] or ""))
        majority_name = entries[0][0] if entries else None
        extra_count = max(len(entries) - 1, 0)

        linked_grns = self.get_linked_grns_for_spo(spo_number)

        # R23, AC-J3: the header linkage strip - rollups across every line on this
        # document. `packing_list` names the FIRST shipment any line landed off (a document
        # is routinely one container; a split across several picks whichever line the
        # query met first, same "first if several" reasoning `get_document`'s own GRN
        # fallback already uses).
        so_covered_all = [c for line in lines for c in (line.so_covered or [])]
        packing_list = None
        for allocation, shipment, *_ in rows:
            if shipment is not None:
                packing_list = {
                    "id": str(shipment.id),
                    "container": shipment.shipping_container_number,
                }
                break
        linkage = {
            "po_count": len({p["purchase_order_id"] for p in po_info.values()}),
            "line_count": len(lines),
            "so_count": len(so_covered_all),
            "so_qty": sum(c.qty for c in so_covered_all),
            "packing_list": packing_list,
            "grn_count": len(linked_grns),
        }

        return SPODocument(
            spo_number=spo_number,
            doc_date=doc_date,
            supplier_name=majority_name,
            supplier_extra_count=extra_count,
            status="outstanding" if outstanding_lines else "completed",
            total_allocated=total_allocated,
            total_received=total_received,
            balance=balance_sum,
            line_count=len(lines),
            lines=lines,
            # The GRNs received against this document - the retired per-allocation
            # page's Related Documents capability, now at its natural (document)
            # grain. Same key-matched reader the old page used.
            linked_grns=linked_grns,
            linkage=linkage,
        )

    def delete_document(self, spo_number: str) -> dict:
        """`spo_document.delete` (record-actions registry, review B1/AC-16b): hard-
        delete every allocation line of ONE named document, and nothing else.

        `spo_number` is UNIQUE per `(company_id, spo_number, spo_line_number)`, not
        globally - two companies can each own a document numbered identically. A bulk
        ORM `.filter(...).delete()` bypasses `do_orm_execute`'s company-scope filter
        entirely (that listener only rewrites SELECTs), so filtering straight off
        `spo_number` and deleting would let company B delete company A's document.
        The fix is the same shape `_delete_onboarding_request` (record_actions.py)
        uses: resolve the ids under a plain SELECT FIRST - which IS scoped, because
        `do_orm_execute` filters every SELECT to the caller's own company - and delete
        by id. The id list is already narrowed to one company by construction, so the
        second query's own missing scope changes nothing.
        """
        rows = (
            self.db.query(SPOAllocation)
            .filter(SPOAllocation.spo_number == spo_number)
            .all()
        )
        ids = [row.id for row in rows]
        if not ids:
            return {"message": "No SPO document to delete", "deleted_count": 0}
        shipment_ids = {row.inbound_shipment_id for row in rows if row.inbound_shipment_id is not None}
        # One audit row per line before the query-level delete (`_audit_spo_line`): this
        # is the path the UI's deferred "Delete selected" takes, the one an operator uses.
        for row in rows:
            _audit_spo_line(self.db, row, "DELETE", description=f"SPO document {spo_number} deleted")
        deleted = (
            self.db.query(SPOAllocation)
            .filter(SPOAllocation.id.in_(ids))
            .delete(synchronize_session=False)
        )
        self.db.commit()
        inbound_svc = InboundShipmentService(self.db)
        for shipment_id in shipment_ids:
            inbound_svc.refresh_shipment_line_statuses(shipment_id)
        return {"message": f"Deleted {deleted} SPO allocation line(s)", "deleted_count": deleted}

    def get_allocation(self, allocation_id: str):
        """Get an SPO allocation by UUID or spo_number."""
        from sqlalchemy.orm import joinedload
        resolved_ids = resolve_identifier(
            self.db,
            allocation_id,
            SPOAllocation,
            code_fields=("spo_number",),
        )
        if not resolved_ids:
            raise handle_not_found("SPO Allocation", allocation_id)
        allocation = self.db.query(SPOAllocation).options(
            joinedload(SPOAllocation.product),
            joinedload(SPOAllocation.warehouse),
            joinedload(SPOAllocation.inbound_shipment),
        ).filter(SPOAllocation.id.in_(resolved_ids)).first()
        if not allocation:
            raise handle_not_found("SPO Allocation", allocation_id)
        return allocation
    
    def create_allocation(
        self,
        allocation_data: SPOAllocationCreate,
        created_by: str,
        *,
        forward_match: bool = True,
        commit: bool = True,
        source_system: Optional[str] = None,
    ):
        """Create a new SPO allocation.

        ``forward_match=False`` is for a caller that writes SEVERAL allocations for
        one SPO in a batch (the SPO Excel import). Firing the hook per row would
        place a waiting GRN line against whichever allocation happened to be
        written first, before the one that actually covers its warehouse exists -
        so that caller suppresses it here and fires it once, per SPO number, when
        the whole file has landed.

        ``commit=False`` (S3, review round 1) FLUSHES instead, leaving the commit to
        the caller: ``spo_conversion_service.revise`` re-deals a whole SPO's rows in
        one transaction, and a commit per row meant a refusal on the third row left
        the first two standing - a half-applied SPO, which is the one thing that
        action promises never to leave behind. The shipment-line refresh is the
        caller's too then (it commits, and ``revise`` runs it once at the end).
        """
        allocation_dict = allocation_data.model_dump()
        allocation_dict["receipt_status"] = _normalize_spo_receipt_status(
            allocation_dict.get("receipt_status")
        )
        allocation_dict["created_by"] = created_by
        # WHO raised the row (spo-xlsx-supersede D25c, security round 6). A SERVICE
        # argument, never a request field (security round 7): the first-push supersede
        # and the group recompute both branch on this column, so a client that could
        # post `autocount` or clear a stamp would decide which rows get replaced and
        # which receipts get pooled. The two SCM writers that raise one allocation per
        # purchase-order line pass `crm_spo`; the n8n packing-list route and the Excel
        # import pass nothing, and their rows are aggregates. The screen passes nothing
        # too, so a hand-created row stays a first-push supersede candidate: a named
        # residual (PLAN D25c), with no such row among the measured production set.
        allocation_dict["source_system"] = source_system
        # Section 7 currency gap (S3): absent on every SPO xlsx row today -
        # filled the same way the PO side already is, so parity has one less
        # excluded column.
        if not allocation_dict.get("currency"):
            allocation_dict["currency"] = DEFAULT_PO_CURRENCY
        # The LINE is the identity of a row in this table since migration 420
        # (`uk_spo_allocations_company_spo_line`), so a caller that does not state a line
        # number gets the next one on that document. The old guard checked
        # (spo_number, product, warehouse), which is not unique and never was: the captain's
        # book states the same product on one SPO 13,305 times over, two containers at a
        # time, and refusing the second one refuses ordinary data.
        if allocation_dict.get("spo_number") and allocation_dict.get("spo_line_number") is None:
            allocation_dict["spo_line_number"] = self._next_spo_line_number(
                allocation_dict["spo_number"]
            )
        if allocation_dict.get("spo_number") and allocation_dict.get("spo_line_number") is not None:
            existing = self.db.query(SPOAllocation.id).filter(
                SPOAllocation.spo_number == allocation_dict["spo_number"],
                SPOAllocation.spo_line_number == allocation_dict["spo_line_number"],
            ).first()
            if existing:
                raise handle_conflict("This SPO already carries that line number.")

        allocation = SPOAllocation(**allocation_dict)
        # D6 (S3): a container given with no shipment already named (the
        # xlsx import always resolves and states one explicitly - see
        # `import_tasks.process_spo_import` - so this only fires for a
        # caller that knows the container but not the shipment id) is
        # auto-linked; no match is left unresolved rather than failing the
        # create, matching the ESB push's own leniency.
        if allocation.container_number and not allocation.inbound_shipment_id:
            shipping_order_rules.link_allocation_to_shipment(
                self.db, allocation, allocation.container_number
            )
        self.db.add(allocation)
        if commit:
            self.db.commit()
            self.db.refresh(allocation)
        else:
            self.db.flush()
        self._capture_incoming_cost(allocation, commit=commit)
        if commit and allocation.inbound_shipment_id:
            # An SPO document has no shipment until somebody books a container for it, and
            # there is nothing to refresh until then.
            InboundShipmentService(self.db).refresh_shipment_line_statuses(
                allocation.inbound_shipment_id
            )
        # The other half of the journey: any GRN line that stated this SPO and
        # could not be placed when it was imported is now placeable. This is the
        # hook for the paths that write ONE allocation - the UI / API create, and
        # the SCM allocation suggestion. The SPO Excel import writes a file's worth
        # at a time and hooks itself once at the end instead.
        if forward_match:
            _forward_match_for_spo(self.db, allocation.spo_number, allocation.company_id)
        return allocation

    def _next_spo_line_number(self, spo_number: str) -> int:
        return next_spo_line_number(self.db, spo_number)

    def _capture_incoming_cost(
        self, allocation: SPOAllocation, *, commit: bool = True
    ) -> None:
        """Stamp the packing-list cost, in its currency, on the inbound shipment line.

        AC-C3.2. The allocation is the moment the incoming cost becomes a fact about this
        purchase, so it is captured here rather than left to be reconstructed later.

        The packing-list line IS ``inbound_shipment_lines``, so the cost is already on the
        row this writes to; the half that is missing is the CURRENCY, which the packing list
        does not state. It resolves through ``po_line_id`` to the ordered line's currency,
        and where there is no such source it stays NULL. It is never guessed: a currency
        invented for a cost silently changes what the variance means.

        The ordered line is only READ (AC-C3.3). Its cost, its currency and its updated_at
        must come out untouched, because a supplier whose incoming cost drifts above its
        ordered cost has repriced after we committed, and overwriting the ordered figure
        destroys the only evidence of that.

        NOTE (external dependency, recorded in PLAN-scm-purchasing-fulfilment): the
        packing-list ingest cannot supply a cost today -- the extracted product carries
        product_code and quantity only -- so in production every line takes the uncosted
        branch below and is logged. The mechanism is correct the day a cost arrives.

        Best-effort: this runs AFTER the allocation has committed, so a failure here must
        not turn a successful write into a 500 for the caller.

        ``commit=False`` (S3, review round 1) means the CALLER owns the transaction (the
        one batch ``spo_conversion_service.revise`` writes): the stamp is flushed with the
        rest of it, and a failure is re-raised rather than rolled back - a rollback here
        would silently discard the caller's whole batch, and swallowing it would leave the
        caller holding a session it cannot flush anyway.
        """
        try:
            line = (
                self.db.query(InboundShipmentLine)
                .filter(
                    InboundShipmentLine.shipment_id == allocation.inbound_shipment_id,
                    InboundShipmentLine.product_id == allocation.product_id,
                )
                # Two factories can ship the same product code in one container, so order
                # before taking one: an arbitrary pick makes the cost land on a different
                # line from one run to the next.
                .order_by(InboundShipmentLine.created_at, InboundShipmentLine.id)
                .first()
            )
            if line is None:
                # An allocation against a product that is not on the packing list. Real,
                # and not this function's problem to resolve.
                return

            if line.unit_cost is None:
                # Reported, never defaulted. A zero here would read as free goods and would
                # flow into the variance as a 100% saving.
                logger.warning(
                    "SPO allocation %s is written against an uncosted packing-list line "
                    "(shipment %s, product %s): no incoming cost to capture, so the cost "
                    "variance against the ordered line is not computable",
                    allocation.id,
                    allocation.inbound_shipment_id,
                    allocation.product_id,
                )
                return

            if line.currency:
                # The packing list stated the unit itself. Nothing to resolve, and the
                # stated currency is never overwritten by an inferred one.
                return

            currency = None
            if allocation.po_line_id:
                currency = (
                    self.db.query(PurchaseOrderLine.currency)
                    .filter(PurchaseOrderLine.id == allocation.po_line_id)
                    .scalar()
                )

            if not currency:
                logger.warning(
                    "SPO allocation %s captured an incoming cost of %s with no currency "
                    "(shipment %s, product %s): %s, so the unit stays unknown rather than "
                    "assumed",
                    allocation.id,
                    line.unit_cost,
                    allocation.inbound_shipment_id,
                    allocation.product_id,
                    "the allocation links no PO line"
                    if not allocation.po_line_id
                    else "the linked PO line states no currency",
                )
                return

            line.currency = currency
            line.updated_at = datetime.utcnow()
            if commit:
                self.db.commit()
            else:
                self.db.flush()
        except Exception:  # noqa: BLE001 - best-effort side effect, see docstring
            logger.warning(
                "Failed to capture the incoming cost for SPO allocation %s",
                getattr(allocation, "id", None),
                exc_info=True,
            )
            if not commit:
                raise
            self.db.rollback()

    def upsert_allocation(
        self,
        allocation_data: SPOAllocationCreate,
        created_by: str,
        *,
        forward_match: bool = True,
    ) -> tuple[str, SPOAllocation]:
        """Create or update an SPO allocation keyed by (spo_number, product_id, warehouse_id).

        Returns a (action, allocation) tuple where action is one of:
      - "created" - no existing row, a new allocation was inserted.
      - "updated" - existing row's allocated_quantity changed (and is still >= received).
      - "unchanged" - existing row already had the same allocated_quantity; no write.

        Raises AllocationReceivedGuardError when the new allocated quantity is below the
        existing quantity_received (received-below-allocated is a data problem to surface).

        Only allocated_quantity (and updated_at) are ever written on update - receipt_status,
        quantity_received, quantity_rejected, created_by, storage_zone_id, allocation_notes
        are left untouched (the import file doesn't carry them reliably).
        """
        existing = None
        if allocation_data.spo_number and allocation_data.product_id and allocation_data.warehouse_id:
            existing = self.db.query(SPOAllocation).filter(
                SPOAllocation.spo_number == allocation_data.spo_number,
                SPOAllocation.product_id == allocation_data.product_id,
                SPOAllocation.warehouse_id == allocation_data.warehouse_id,
                # D11 (S3): one shipping-order writer identity. NULL is this
                # service's own unstamped rows; `CRM_SPO_SOURCE_SYSTEM` is the
                # same thing stamped, by the two SCM writers that raise one
                # allocation per purchase-order line (security round 7);
                # `SPO_UPLOAD_SOURCE` is the outstanding book's own SPO write
                # path; `ESB_SOURCE_SYSTEM` ("autocount") is
                # `ShippingOrderIngestService`'s. All of them are rows this
                # upload may legitimately correct a quantity on - only
                # `scm_po_history`/`scm_spo_history` (closed history, a
                # different feed entirely) stay excluded.
                or_(
                    shipping_order_rules.crm_raised(SPOAllocation.source_system),
                    SPOAllocation.source_system.in_([SPO_UPLOAD_SOURCE, ESB_SOURCE_SYSTEM]),
                ),
            ).order_by(SPOAllocation.spo_line_number).first()

        if existing is None:
            allocation = self.create_allocation(
                allocation_data, created_by, forward_match=forward_match
            )
            return ("created", allocation)

        new_qty = allocation_data.allocated_quantity
        if new_qty == existing.allocated_quantity:
            return ("unchanged", existing)

        if shipping_order_rules.received_guard(existing, new_qty) == (
            shipping_order_rules.GUARD_RECEIVED_LOCKED
        ):
            received = existing.quantity_received or 0
            raise AllocationReceivedGuardError(
                f"Allocation {existing.spo_number} / product {existing.product_id} / "
                f"warehouse {existing.warehouse_id}: new qty {new_qty} < already received "
                f"{received}, skipped"
            )

        # D11 (S3): "last writer wins" - adopting an ESB-written (or
        # unstamped) row through THIS channel makes it this channel's row
        # again, the same way `ShippingOrderIngestService._write_row`'s own
        # blind setattr already re-stamps an xlsx-era row it adopts.
        #
        # EXCEPT a `crm_spo` row (security round 7). That stamp is not a
        # writer's claim on the row, it is the marker that says this
        # allocation belongs to a purchase-order line, and clearing it would
        # quietly hand the row back to the supersede sweep (D25c reads an
        # unstamped ref-less row as Excel-era). A quantity correction is not a
        # change of what the row IS.
        if existing.source_system != shipping_order_rules.CRM_SPO_SOURCE_SYSTEM:
            existing.source_system = None
        existing.allocated_quantity = new_qty
        existing.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(existing)
        # The allocation was written, so the incoming cost is re-captured against it
        # (AC-C3.2). The "unchanged" path above returns before any write and stamps
        # nothing, because there was no moment of allocation to capture at.
        self._capture_incoming_cost(existing)
        if existing.inbound_shipment_id:
            InboundShipmentService(self.db).refresh_shipment_line_statuses(
                existing.inbound_shipment_id
            )
        # A corrected SPO file raising `allocated_quantity` FREES capacity, and the
        # lines waiting on it should get it. The "unchanged" branch returns above
        # without writing, so there is no moment of allocation to react to there.
        if forward_match:
            _forward_match_for_spo(self.db, existing.spo_number, existing.company_id)
        return ("updated", existing)

    def update_allocation(self, allocation_id: str, allocation_data: SPOAllocationUpdate):
        """Update an SPO allocation."""
        allocation = self.get_allocation(allocation_id)
        previous_shipment_id = allocation.inbound_shipment_id
        update_data = allocation_data.model_dump(exclude_unset=True)
        if "receipt_status" in update_data:
            update_data["receipt_status"] = _normalize_spo_receipt_status(
                update_data.get("receipt_status")
            )
        for key, value in update_data.items():
            setattr(allocation, key, value)
        
        self.db.commit()
        self.db.refresh(allocation)
        inbound_svc = InboundShipmentService(self.db)
        inbound_svc.refresh_shipment_line_statuses(allocation.inbound_shipment_id)
        if previous_shipment_id and allocation.inbound_shipment_id != previous_shipment_id:
            inbound_svc.refresh_shipment_line_statuses(previous_shipment_id)
        return allocation

    def delete_allocation(self, allocation_id: str):
        """Delete an SPO allocation by ID."""
        allocation = self.get_allocation(allocation_id)
        shipment_id = allocation.inbound_shipment_id
        _audit_spo_line(self.db, allocation, "DELETE")
        self.db.delete(allocation)
        self.db.commit()
        InboundShipmentService(self.db).refresh_shipment_line_statuses(shipment_id)

    def bulk_delete_allocations(self, allocation_ids: list[str]):
        """Delete multiple SPO allocations by ID. Returns count of deleted."""
        if not allocation_ids:
            return {"message": "No allocations to delete", "deleted_count": 0}
        # The rows themselves, not only their shipment ids: each one gets its own audit
        # row before the query-level delete removes it (`_audit_spo_line`).
        rows = self.db.query(SPOAllocation).filter(SPOAllocation.id.in_(allocation_ids)).all()
        shipment_ids = {row.inbound_shipment_id for row in rows if row.inbound_shipment_id is not None}
        for row in rows:
            _audit_spo_line(self.db, row, "DELETE")
        deleted = self.db.query(SPOAllocation).filter(SPOAllocation.id.in_(allocation_ids)).delete(synchronize_session=False)
        self.db.commit()
        inbound_svc = InboundShipmentService(self.db)
        for shipment_id in shipment_ids:
            inbound_svc.refresh_shipment_line_statuses(shipment_id)
        return {"message": f"Deleted {deleted} SPO allocation(s)", "deleted_count": deleted}

    def compute_received_for_allocation(self, allocation_id: str) -> int:
        """Computed on read: sum the DRAWN quantity from picking lines where
        spo_allocation_id = allocation_id and the picking line's header (GRN) is
        approved. Not stored in DB.

        The drawn quantity is ``quantity_picked`` - see the convention note in
        ``app.services.grn_spo_matching``. It has to be the same column
        ``build_allocation_pool`` measures, or the capacity a line consumes and the
        receipt it reports disagree: summing ``quantity_expected`` charged a split
        line's whole document quantity to its FIRST allocation and nothing to the
        rest, which reported a partial receipt as fully received."""
        from sqlalchemy import func
        total = (
            self.db.query(func.coalesce(func.sum(PickingLine.quantity_picked), 0))
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .filter(
                PickingLine.spo_allocation_id == allocation_id,
                PickingHeader.picking_type == "goods_received",
                PickingHeader.picking_status == "approved",
            )
            .scalar()
        )
        return int(total)

    #: An allocation whose figures came from a FILE states its own receipt, and the GRN
    #: view of the world must not overwrite it. Every imported SPO document carries a
    #: `source_system` (migration 420) and 74,016 of them are 2020-2023 history that was
    #: written fully received; recomputing their received quantity from the GRNs in this
    #: system returns 0 for all of them, which would show three years of delivered
    #: purchases as outstanding. A row this system raised itself carries no stamp, and for
    #: those the GRN lines ARE the record.
    #:
    #: THREE receipt-ownership rules, and they are deliberately different
    #: (spo-xlsx-supersede D28 / D28a / D28b):
    #:
    #: 1. READ path (this predicate, used by the listings and availability readers
    #:    below): an IMPORTED row's stored figure is trusted as stated, and a row this
    #:    system raised itself is measured from its GRN lines. "Raised itself" is no
    #:    stamp at all OR `crm_spo` (D25c, security round 6): the two SCM writers now
    #:    stamp the rows they create so the supersede leaves them alone, and without
    #:    naming that stamp here every SCM-raised allocation would stop reporting the
    #:    receipt its own approved GRN lines prove.
    #: 2. WRITE path, per allocation (`_sync_received_for_allocations` for a
    #:    `source_system` NULL or `scm_upload` row): recomputed from its OWN picking
    #:    lines, and skipped entirely when nothing picks against it and nobody is
    #:    releasing it - that stored value was stated or carried, not computed here.
    #: 3. WRITE path, per GROUP (`_sync_group_received` for an `autocount` row): the
    #:    group's approved picking total redistributed over its lines (D28a), each line
    #:    floored by its own `stated_received` (D28c) - a pushed line-set that replaced
    #:    one aggregated row shares one receipt, so a partial Sorento pick against one of
    #:    its lines is not evidence that the rest never arrived, while a share only a GRN
    #:    produced leaves again when that GRN does. The floor is per line and comes from
    #:    the DECLARED column, never from `quantity_received`: the stored figure cannot
    #:    say whether it was stated or derived, which is what D28b got wrong.
    @staticmethod
    def _receipt_is_computed(allocation) -> bool:
        return (
            getattr(allocation, "source_system", None) or None
        ) in shipping_order_rules.COMPUTED_RECEIPT_SOURCE_SYSTEMS

    def get_computed_received_map(self, allocation_ids: list[str]) -> dict[str, int]:
        """Bulk: for each allocation id, return computed quantity_received (the sum
        of the drawn quantity over approved GRN lines - the same column
        ``compute_received_for_allocation`` sums)."""
        if not allocation_ids:
            return {}
        from sqlalchemy import func
        rows = (
            self.db.query(PickingLine.spo_allocation_id, func.coalesce(func.sum(PickingLine.quantity_picked), 0))
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .filter(
                PickingLine.spo_allocation_id.in_(allocation_ids),
                PickingHeader.picking_type == "goods_received",
                PickingHeader.picking_status == "approved",
            )
            .group_by(PickingLine.spo_allocation_id)
            .all()
        )
        received_map = {str(r[0]): int(r[1]) for r in rows}
        return {aid: received_map.get(aid, 0) for aid in allocation_ids}

    def get_linked_grns_for_spo(self, spo_number: Optional[str]):
        """Return list of GRN headers (id, picking_number, picking_status, picking_date) for this SPO number.
        Matches by alphanumeric SPO key so variant formats (e.g. SPO-202602-0102 vs SPO-2026/02-0102) align."""
        if not spo_number or not spo_number.strip():
            return []
        target_key = _spo_match_key(spo_number)
        if not target_key:
            return []
        rows = (
            self.db.query(
                PickingHeader.id,
                PickingHeader.picking_number,
                PickingHeader.picking_status,
                PickingHeader.picking_date,
                PickingHeader.spo_number,
            )
            .filter(
                PickingHeader.picking_type == "goods_received",
                PickingHeader.spo_number.isnot(None),
            )
            .order_by(PickingHeader.picking_date.desc().nulls_last(), PickingHeader.picking_number)
            .all()
        )
        return [
            {"id": str(r[0]), "picking_number": r[1], "picking_status": r[2], "picking_date": r[3]}
            for r in rows
            if _spo_match_key(r[4]) == target_key
        ]


class PickingHeaderService:
    """Service for picking header (GRN) operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def _build_grn_list_query(
        self,
        query: Optional[str] = None,
        product_query: Optional[str] = None,
        picking_status: Optional[str] = None,
        inspection_status: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
    ):
        """Build the filtered + sorted GRN query shared by ``list_grns`` and
        ``neighbours`` so the two can never drift.

        Returns ``(query, count_subq)`` where ``count_subq`` is the line/item count
        subquery (only when sorting by ``lines_count``/``items_count``, else ``None``)
        so ``list_grns`` can read the count columns back. The ORDER BY always appends
        ``PickingHeader.id`` as a deterministic tie-breaker so offset position and
        prev/next neighbours are unambiguous when the primary sort column ties.
        """
        from sqlalchemy.orm import noload
        q = self.db.query(PickingHeader).options(
            noload(PickingHeader.picking_lines)
        ).filter(PickingHeader.picking_type == "goods_received")

        filters = []

        if picking_status and picking_status != "all":
            filters.append(PickingHeader.picking_status == picking_status)

        if inspection_status and inspection_status != "all":
            filters.append(PickingHeader.inspection_status == inspection_status)

        if query:
            like = f"%{query}%"
            norm_q = query.replace("/", ".").replace("\\", ".")
            spo_like = f"%{norm_q}%"
            filters.append(or_(
                PickingHeader.picking_number.ilike(like),
                func.replace(func.replace(func.coalesce(PickingHeader.spo_number, ""), "/", "."), "\\", ".").ilike(spo_like),
                PickingHeader.picking_lines.any(
                    PickingLine.product.has(
                        or_(
                            Product.product_code.ilike(like),
                            Product.product_name.ilike(like),
                        )
                    )
                ),
            ))

        if product_query and product_query.strip():
            product_clause, _ = resolve_via_embedding_then_ilike(
                self.db,
                product_query,
                source_type="product",
                ilike_columns=[],
                canonical_model=Product,
                canonical_fields=("product_code", "product_name"),
                extra_filter_builders=[
                    lambda like: PickingHeader.picking_lines.any(
                        PickingLine.product.has(
                            or_(
                                Product.product_code.ilike(like),
                                Product.product_name.ilike(like),
                                Product.description.ilike(like),
                            )
                        )
                    ),
                ],
            )
            if product_clause is not None:
                filters.append(product_clause)

        if filters:
            q = q.filter(and_(*filters))

        sort_map = {
            "picking_number": PickingHeader.picking_number,
            "picking_date": PickingHeader.picking_date,
            "created_at": PickingHeader.created_at,
            "updated_at": PickingHeader.updated_at,
        }
        use_count_sort = sort_field in ("lines_count", "items_count")
        if use_count_sort:
            count_subq = (
                self.db.query(
                    PickingLine.picking_header_id,
                    func.count(PickingLine.id).label("lines_cnt"),
                    func.count(func.distinct(PickingLine.product_id)).label("items_cnt"),
                )
                .group_by(PickingLine.picking_header_id)
            ).subquery()
            q = q.outerjoin(count_subq, PickingHeader.id == count_subq.c.picking_header_id)
            sort_column = count_subq.c.items_cnt if sort_field == "items_count" else count_subq.c.lines_cnt
            if sort_dir == "desc":
                q = q.order_by(sort_column.desc().nulls_last(), PickingHeader.id.asc())
            else:
                q = q.order_by(sort_column.asc().nulls_last(), PickingHeader.id.asc())
            return q, count_subq

        sort_column = sort_map.get(sort_field, PickingHeader.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc().nulls_last(), PickingHeader.id.asc())
        else:
            q = q.order_by(sort_column.asc().nulls_last(), PickingHeader.id.asc())
        return q, None

    def list_grns(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        product_query: Optional[str] = None,
        picking_status: Optional[str] = None,
        inspection_status: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc"
    ):
        """List GRNs (picking headers with type 'goods_received'). Does not load picking_lines; adds lines_count only."""
        q, count_subq = self._build_grn_list_query(
            query=query,
            product_query=product_query,
            picking_status=picking_status,
            inspection_status=inspection_status,
            sort_field=sort_field,
            sort_dir=sort_dir,
        )
        if count_subq is not None:
            total = q.count()
            offset = (page - 1) * limit
            q = q.add_columns(count_subq.c.lines_cnt, count_subq.c.items_cnt)
            rows = q.offset(offset).limit(limit).all()
            grns = []
            for row in rows:
                header, lines_cnt, items_cnt = row[0], row[1], row[2]
                setattr(header, "lines_count", int(lines_cnt) if lines_cnt is not None else 0)
                setattr(header, "items_count", int(items_cnt) if items_cnt is not None else 0)
                setattr(header, "picking_lines", [])
                grns.append(header)
        else:
            total = q.count()
            offset = (page - 1) * limit
            grns = q.offset(offset).limit(limit).all()
            header_ids = [g.id for g in grns]
            if header_ids:
                count_rows = (
                    self.db.query(
                        PickingLine.picking_header_id,
                        func.count(PickingLine.id),
                        func.count(func.distinct(PickingLine.product_id)),
                    )
                    .filter(PickingLine.picking_header_id.in_(header_ids))
                    .group_by(PickingLine.picking_header_id)
                    .all()
                )
                counts_by_header = {str(r[0]): (r[1], r[2]) for r in count_rows}
                for g in grns:
                    lines_cnt, items_cnt = counts_by_header.get(str(g.id), (0, 0))
                    setattr(g, "lines_count", lines_cnt)
                    setattr(g, "items_count", items_cnt)
                    setattr(g, "picking_lines", [])
            else:
                for g in grns:
                    setattr(g, "lines_count", 0)
                    setattr(g, "items_count", 0)
                    setattr(g, "picking_lines", [])
        
        return {
            "data": grns,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def list_picking_lines(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        sort_field: str = "spo_allocation",
        sort_dir: str = "asc",
    ):
        """List picking lines (GRN lines) with sort and search by SPO allocation or product."""
        from sqlalchemy.orm import joinedload
        from app.schemas.procurement import PickingLineResponse
        q = (
            self.db.query(PickingLine)
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .outerjoin(SPOAllocation, PickingLine.spo_allocation_id == SPOAllocation.id)
            .outerjoin(Product, PickingLine.product_id == Product.id)
            .filter(PickingHeader.picking_type == "goods_received")
            .options(
                joinedload(PickingLine.product),
                joinedload(PickingLine.spo_allocation),
                joinedload(PickingLine.source_warehouse),
                joinedload(PickingLine.destination_warehouse),
            )
        )
        if query and query.strip():
            q_str = f"%{query.strip()}%"
            q = q.filter(or_(
                SPOAllocation.spo_number.ilike(q_str),
                # The stated SPO too, or the lines this feature exists for - the
                # ones with no allocation to search by - are unfindable by number.
                PickingLine.spo_number_raw.ilike(q_str),
                Product.product_code.ilike(q_str),
                Product.product_name.ilike(q_str),
            ))
        sort_map = {
            "spo_allocation": SPOAllocation.spo_number,
            "product": Product.product_code,
            "quantity_expected": PickingLine.quantity_expected,
            "quantity_picked": PickingLine.quantity_picked,
        }
        sort_col = sort_map.get(sort_field, SPOAllocation.spo_number)
        if sort_dir == "desc":
            q = q.order_by(sort_col.desc().nulls_last())
        else:
            q = q.order_by(sort_col.asc().nulls_last())
        total = q.count()
        offset = (page - 1) * limit
        lines = q.offset(offset).limit(limit).all()
        data = [PickingLineResponse.model_validate(line) for line in lines]
        return {"data": data, "pagination": {"total": total, "page": page, "limit": limit}, "empty": total == 0}

    def get_grn(self, grn_id: str):
        """Get a GRN by UUID or picking_number."""
        from sqlalchemy.orm import selectinload, joinedload
        resolved_ids = resolve_identifier(
            self.db,
            grn_id,
            PickingHeader,
            code_fields=("picking_number",),
        )
        if not resolved_ids:
            raise handle_not_found("GRN", grn_id)
        grn = self.db.query(PickingHeader).options(
            selectinload(PickingHeader.picking_lines).joinedload(PickingLine.product),
            selectinload(PickingHeader.picking_lines).joinedload(PickingLine.spo_allocation),
            selectinload(PickingHeader.picking_lines).joinedload(PickingLine.source_warehouse),
            selectinload(PickingHeader.picking_lines).joinedload(PickingLine.destination_warehouse),
        ).filter(
            PickingHeader.id.in_(resolved_ids),
            PickingHeader.picking_type == "goods_received"
        ).first()
        if not grn:
            raise handle_not_found("GRN", grn_id)
        self.attach_provenance_labels(grn)
        return grn

    def attach_provenance_labels(self, grn: PickingHeader) -> None:
        """Stamp human-readable provenance onto the instance for the response.

        The UI must never print a UUID, so the stored ``created_by`` /
        ``import_job_id`` are resolved here to a person and a file name. Attributes
        are set on the instance (not columns) and Pydantic reads them off the ORM
        object; they die with the instance, so nothing can accidentally persist.

        Best-effort: a deleted user or a pruned job leaves the label None rather
        than failing the GRN read.
        """
        grn.created_by_label = None  # type: ignore[attr-defined]
        grn.import_filename = None  # type: ignore[attr-defined]
        try:
            if getattr(grn, "created_by", None):
                from app.models.user import User

                row = (
                    self.db.query(User.name, User.email)
                    .filter(User.id == str(grn.created_by))
                    .first()
                )
                if row is not None:
                    grn.created_by_label = row[0] or row[1]  # type: ignore[attr-defined]
            if getattr(grn, "import_job_id", None):
                from app.models.job import ImportJob

                job_row = (
                    self.db.query(ImportJob.filename)
                    .filter(ImportJob.id == str(grn.import_job_id))
                    .first()
                )
                if job_row is not None:
                    grn.import_filename = job_row[0]  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - a label is not worth failing the read
            logger.exception("GRN provenance label lookup failed for %s", grn.id)

    def create_grn(
        self,
        grn_data: PickingHeaderCreate,
        created_by: str | None = None,
        source_system: str = "ui",
    ):
        """Create a new GRN with lines.

        ``source_system`` says which surface wrote it - ``'ui'`` for a staff create,
        ``'external_api'`` for the n8n / AutoCount ingest. Without it the row cannot
        distinguish a person from an integration, which is the case that made a
        wrongly-companied GRN untraceable (the external path leaves no import job
        and no audit row to bracket).
        """
        existing = self.db.query(PickingHeader).filter(
            PickingHeader.picking_number == grn_data.picking_number
        ).first()
        if existing:
            raise handle_conflict("Picking number already exists.")

        # Create GRN header and lines in transaction
        grn_dict = grn_data.model_dump(exclude={"picking_lines"})
        grn_dict["picking_type"] = "goods_received"
        if not grn_dict.get("picked_by_user_id"):
            grn_dict["picked_by_user_id"] = str(created_by) if created_by else None
        grn_dict["created_by"] = str(created_by) if created_by else None
        grn_dict["source_system"] = source_system

        grn = PickingHeader(**grn_dict)
        self.db.add(grn)
        self.db.flush()
        
        # Create lines if provided. Do not link to SPO on create (only link when status becomes approved).
        if grn_data.picking_lines:
            for line_data in grn_data.picking_lines:
                line_dict = line_data.model_dump(exclude={"quantity_discrepancy"}, exclude_none=False)
                line_dict.pop("spo_allocation_id", None)  # Never link on create
                # Not linked is not the same as not stated (AC-FM-9c). A GRN that
                # arrives through the UI or the external API BEFORE its SPO is the
                # case this feature exists for, and a draft line that states
                # nothing is invisible to forward matching until somebody approves
                # it - which is the wrong way round. Same rule as `update_grn`.
                line_dict["spo_number_raw"] = _stated_spo_for_line(
                    line_dict.get("spo_number_raw"), grn_dict.get("spo_number")
                )
                line = PickingLine(**line_dict, picking_header_id=grn.id)
                self.db.add(line)
        
        self.db.commit()
        self.db.refresh(grn)
        return grn
    
    def update_grn(self, grn_id: str, grn_data: PickingHeaderUpdate):
        """Update a GRN. Link to SPO only when status changes to approved; unlink and release quantity when status changes to draft or rejected."""
        grn = self.get_grn(grn_id)
        prev_status = grn.picking_status
        prev_spo_number = grn.spo_number
        # D28: read before anything moves - a re-point or a line rewrite
        # leaves these allocations with no picking line at all, and the
        # re-syncs below are what release the receipt they still carry.
        previously_linked = self._allocation_ids_of_grn(grn_id)

        update_data = grn_data.model_dump(exclude_unset=True)
        picking_lines_payload = update_data.pop("picking_lines", None)

        for key, value in update_data.items():
            setattr(grn, key, value)
        self.db.flush()

        # When status changes from approved to draft/rejected: unlink and release SPO allocation quantity
        if prev_status == "approved" and grn.picking_status in ("draft", "rejected"):
            self._unlink_grn_from_spo(grn_id)
            self.db.flush()

        if picking_lines_payload is not None:
            if prev_status == "approved" and grn.picking_status == "approved":
                # Release BEFORE the delete, for the same reason the approved ->
                # draft transition above does. ``build_allocation_pool`` protects a
                # re-write by subtracting the caller's own linked rows from the
                # stored ``quantity_received`` before treating the excess as an
                # integration's receipt - and that subtraction needs those rows to
                # still exist. Delete them first and this GRN's own approval-written
                # receipt reads as somebody else's, swallowing the allocation it
                # came from: the line is rewritten UNLINKED, and
                # ``sync_grn_received_to_spo`` walks only linked lines, so the
                # allocation is left reporting a receipt no picking line explains
                # and is un-drawable by every later pool build. Nothing self-heals.
                self._unlink_grn_from_spo(grn_id)
                self.db.flush()
            self.db.query(PickingLine).filter(PickingLine.picking_header_id == grn_id).delete()
            # Only link to SPO when status is approved; otherwise create lines without spo_allocation_id
            if grn.picking_status == "approved" and grn.spo_number and str(grn.spo_number).strip():
                self._create_grn_lines_with_spo_fifo(grn_id, grn.spo_number, picking_lines_payload)
            else:
                for line_data in picking_lines_payload:
                    line_dict = {k: v for k, v in line_data.items() if k != "quantity_discrepancy"}
                    line_dict.pop("spo_allocation_id", None)  # Do not link when not approved
                    # Not linked is not the same as not stated: an unapproved GRN is
                    # the normal state of an imported one, and it has to stay
                    # forward-matchable through an edit.
                    line_dict["spo_number_raw"] = _stated_spo_for_line(
                        line_data.get("spo_number_raw"), grn.spo_number
                    )
                    line = PickingLine(**line_dict, picking_header_id=grn_id)
                    self.db.add(line)
        elif grn.picking_status == "approved" and grn.spo_number and str(grn.spo_number).strip():
            status_became_approved = prev_status != "approved"
            spo_changed_while_approved = (
                prev_status == "approved"
                and _spo_match_key(prev_spo_number) != _spo_match_key(grn.spo_number)
            )
            if status_became_approved or spo_changed_while_approved:
                # No line payload: rebuild from existing DB rows and FIFO-link to current SPO
                existing_lines = (
                    self.db.query(PickingLine)
                    .filter(PickingLine.picking_header_id == grn_id)
                    .all()
                )
                if existing_lines:
                    lines_payload = [
                        {
                            "product_id": str(line.product_id),
                            "source_warehouse_id": str(line.source_warehouse_id) if line.source_warehouse_id else None,
                            "quantity_expected": line.quantity_expected or 0,
                            "quantity_picked": line.quantity_picked or 0,
                            # Carried through the delete-and-recreate, or approving an
                            # imported GRN would erase what its sheet said.
                            "spo_number_raw": line.spo_number_raw,
                        }
                        for line in existing_lines
                    ]
                    self.db.query(PickingLine).filter(PickingLine.picking_header_id == grn_id).delete()
                    self._create_grn_lines_with_spo_fifo(grn_id, grn.spo_number, lines_payload)

        self.db.commit()
        self.db.refresh(grn)

        spo_key_changed = _spo_match_key(prev_spo_number) != _spo_match_key(grn.spo_number)
        if grn.picking_status == "approved" and (
            prev_status != "approved"
            or picking_lines_payload is not None
            or spo_key_changed
        ):
            self.sync_grn_received_to_spo(grn_id)
        if (
            grn.picking_status == "approved"
            and prev_status == "approved"
            and spo_key_changed
        ):
            if prev_spo_number and str(prev_spo_number).strip():
                self.sync_received_for_spo_number(
                    prev_spo_number, released_allocation_ids=previously_linked
                )
            if grn.spo_number and str(grn.spo_number).strip():
                self.sync_received_for_spo_number(
                    grn.spo_number, released_allocation_ids=previously_linked
                )

        return grn

    def _unlink_grn_from_spo(self, grn_id: str) -> None:
        """Clear spo_allocation_id from all picking lines of this GRN and re-sync SPO allocations to release quantity."""
        grn = self.db.query(PickingHeader).filter(
            PickingHeader.id == grn_id,
            PickingHeader.picking_type == "goods_received",
        ).first()
        if not grn or not grn.spo_number or not str(grn.spo_number).strip():
            return
        lines = self.db.query(PickingLine).filter(PickingLine.picking_header_id == grn_id).all()
        released = {str(line.spo_allocation_id) for line in lines if line.spo_allocation_id}
        for line in lines:
            line.spo_allocation_id = None
        self.db.flush()
        # D28: the allocations this GRN just let go are named explicitly - the
        # re-sync is what releases their stored receipt, and after the unlink
        # no picking line is left to prove they ever had one.
        self.sync_received_for_spo_number(grn.spo_number, released_allocation_ids=released)
    
    def delete_grn(self, grn_id: str):
        """Delete a GRN and its lines."""
        grn = self.get_grn(grn_id)
        spo_number = grn.spo_number
        was_approved = grn.picking_status == "approved"
        # D28: read BEFORE the lines go - these are the allocations this GRN is
        # releasing, and the re-sync below has no other way to know they ever
        # had a picking line.
        released = self._allocation_ids_of_grn(grn_id)

        # Explicitly delete picking lines first to avoid foreign key constraint issues
        self.db.query(PickingLine).filter(PickingLine.picking_header_id == grn_id).delete()
        
        # Then delete the header
        self.db.delete(grn)
        self.db.commit()
        if was_approved and spo_number and str(spo_number).strip():
            self.sync_received_for_spo_number(spo_number, released_allocation_ids=released)
        return {"message": "GRN deleted successfully"}

    def bulk_delete_grns(self, grn_ids: list[str]) -> dict:
        """Delete multiple GRNs (and their lines) by ID. Only goods_received type."""
        if not grn_ids:
            return {"message": "No GRNs to delete", "deleted_count": 0}
        deleted = 0
        spo_numbers_to_sync = set()
        released_by_spo: dict[str, set] = {}
        for gid in grn_ids:
            grn = (
                self.db.query(PickingHeader)
                .filter(
                    PickingHeader.id == gid,
                    PickingHeader.picking_type == "goods_received",
                )
                .first()
            )
            if grn:
                if grn.picking_status == "approved" and grn.spo_number and str(grn.spo_number).strip():
                    spo_numbers_to_sync.add(str(grn.spo_number))
                    # D28: same release list as `delete_grn`, read before the
                    # lines go, accumulated per SPO number.
                    released_by_spo.setdefault(str(grn.spo_number), set()).update(
                        self._allocation_ids_of_grn(gid)
                    )
                self.db.query(PickingLine).filter(PickingLine.picking_header_id == gid).delete()
                self.db.delete(grn)
                deleted += 1
        self.db.commit()
        for spo_number in spo_numbers_to_sync:
            self.sync_received_for_spo_number(
                spo_number, released_allocation_ids=released_by_spo.get(spo_number, set())
            )
        return {"message": f"{deleted} GRN(s) deleted", "deleted_count": deleted}

    def get_grn_by_picking_number(self, picking_number: str):
        """Get GRN (picking header) by picking_number. Returns None if not found."""
        return self.db.query(PickingHeader).filter(
            PickingHeader.picking_number == picking_number,
            PickingHeader.picking_type == "goods_received",
        ).first()

    def upsert_grn_header_for_import(
        self,
        picking_number: str,
        spo_number: Optional[str],
        picking_date: date,
        *,
        created_by: Optional[str] = None,
        import_job_id: Optional[str] = None,
    ) -> tuple[PickingHeader, bool]:
        """Create or update a GRN header by picking_number (idempotent).

        Returns ``(header, created)``. The caller NEEDS that flag: it used to
        report every success as ``created``, so the last person to re-run a file
        looked like the author of every GRN in it - which is exactly what made
        "who created this GRN" unanswerable.

        Provenance (``created_by`` / ``import_job_id`` / ``source_system``) is
        written ONLY on insert. A re-import overwrites the mutable fields but must
        not rewrite authorship, or the answer is lost the first time someone
        re-uploads the same spreadsheet.
        """
        existing = self.get_grn_by_picking_number(picking_number)
        if existing is not None and existing.doc_key is not None:
            # #1354 S2, plan section 3: the AutoCount GRN ingest owns this GRN; the sheet
            # neither rewrites its header nor adds lines to it (see the line upsert below).
            return existing, False
        if existing:
            existing.spo_number = spo_number
            existing.picking_date = picking_date
            existing.picking_status = "approved"
            self.db.commit()
            self.db.refresh(existing)
            return existing, False
        grn = PickingHeader(
            picking_number=picking_number,
            spo_number=spo_number,
            picking_type="goods_received",
            picking_date=picking_date,
            picking_status="approved",
            inspection_status="pending",
            created_by=created_by,
            source_system="import",
            import_job_id=import_job_id,
        )
        self.db.add(grn)
        self.db.commit()
        self.db.refresh(grn)
        return grn, True

    def upsert_grn_line_for_import(
        self,
        picking_header_id: str,
        product_id: str,
        source_warehouse_id: str,
        quantity: int,
        spo_allocation_id: Optional[str] = None,
        spo_number_raw: Optional[str] = None,
        company_id: Optional[str] = None,
    ):
        """Create or update one picking line by (header, product, source_warehouse, spo_allocation_id).
        Allows multiple lines with same (header, product, warehouse) when spo_allocation_id differs (for splitting).
        Idempotent.

        ``spo_number_raw`` is what the SHEET said this line was received against.
        It is written on BOTH branches, so a corrected export refreshes it in place
        rather than leaving the old claim behind; it is deliberately NOT part of the
        match filter, because the row identity is still
        (header, product, source_warehouse, spo_allocation_id).

        A draw that NOW carries an allocation id (PLAN-grn-link-ignores-mirror-received,
        B1) has no exact match when the row already on the header is the ORPHAN a
        previous import left - unlinked, ``spo_allocation_id IS NULL`` - because the
        pool could not place it at the time. Matching by the exact tuple would insert
        a SIBLING row instead of linking the orphan in place: the GRN reads two lines
        for one sheet row, picked quantity doubles, and the container reports twice
        the receipt. So when the draw carries an allocation id and no exact match
        exists, an unlinked row for the same (header, product, warehouse) is ADOPTED -
        given this draw's allocation id and quantity - the same "link in place, don't
        duplicate" outcome forward matching produces. A later draw of the same sheet
        row (a genuine split) then finds no more unlinked rows to adopt and correctly
        creates a new one.

        The adoption candidate is ALSO confined to what THIS draw's SPO number
        claims (review round 2, B3): a multi-SPO GRN groups its rows by
        ``(doc_no, product, effective_spo)`` (``import_tasks.py``), so the same
        header/product/warehouse can carry an unlinked row stating one SPO and a
        linked draw for a DIFFERENT one. Without the guard the different-SPO draw
        adopted the other SPO's orphan and overwrote its quantity - a 40-unit
        remainder stating SPO-A destroyed by a 60-unit draw against SPO-B, the
        GRN reading one 60-unit line for a 100-unit sheet. An orphan's own
        ``spo_number_raw`` is NULL (nothing has ever stated one for it) or must
        match this draw's ``spo_number_raw`` under the same key
        ``grn_spo_matching._spo_match_key`` uses everywhere else.

        ``company_id`` is the GRN header's own company, stated rather than left to
        the insert hook - the same rule ``_add_picking_line`` follows. An import job
        with no company snapshot runs system-scoped ("all companies"), where the
        hook stamps the INCUMBENT company: the import already confines its POOL to
        the header's company, so without this the row draws company B's capacity
        and lands in Sorento, invisible on its own GRN. Worse, the consumption query
        filters on ``company_id`` too, so the mis-stamped row never counts as
        consumption and a re-import over-draws (AC-FM-27, both halves or neither).
        It is written on BOTH branches, so a row a previous run mis-stamped is
        corrected rather than left behind.
        """
        # #1354 S2: an AutoCount-owned GRN's lines are the ingest's; the sheet adds none.
        owned = (
            self.db.query(PickingHeader.doc_key)
            .filter(PickingHeader.id == picking_header_id)
            .scalar()
        )
        if owned is not None:
            return None
        # Match by (header, product, warehouse, spo_allocation_id) to allow splitting across multiple SPOs
        filters = [
            PickingLine.picking_header_id == picking_header_id,
            PickingLine.product_id == product_id,
            PickingLine.source_warehouse_id == source_warehouse_id,
        ]
        if spo_allocation_id is not None:
            filters.append(PickingLine.spo_allocation_id == spo_allocation_id)
        else:
            filters.append(PickingLine.spo_allocation_id.is_(None))

        line = self.db.query(PickingLine).filter(*filters).first()
        if line is None and spo_allocation_id is not None:
            # No exact match - adopt an orphan of this header/product/warehouse
            # rather than insert a sibling. See the docstring above (B1). The
            # orphan must state NO SPO or the SAME one this draw does (B3) - an
            # orphan stating a different SPO belongs to a different group and
            # must be left for that group's own draw to place.
            from app.services.grn_spo_matching import _spo_match_key_sql

            line = (
                self.db.query(PickingLine)
                .filter(
                    PickingLine.picking_header_id == picking_header_id,
                    PickingLine.product_id == product_id,
                    PickingLine.source_warehouse_id == source_warehouse_id,
                    PickingLine.spo_allocation_id.is_(None),
                    or_(
                        PickingLine.spo_number_raw.is_(None),
                        _spo_match_key_sql(PickingLine.spo_number_raw)
                        == _spo_match_key(spo_number_raw),
                    ),
                )
                .order_by(PickingLine.created_at.asc(), PickingLine.id.asc())
                .first()
            )
            if line is not None:
                line.spo_allocation_id = spo_allocation_id
        if line:
            line.quantity_expected = quantity
            line.quantity_picked = quantity
            line.spo_number_raw = spo_number_raw
            if company_id:
                line.company_id = str(company_id)
            self.db.flush()
            return line
        line = PickingLine(
            picking_header_id=picking_header_id,
            product_id=product_id,
            source_warehouse_id=source_warehouse_id,
            quantity_expected=quantity,
            quantity_picked=quantity,
            spo_allocation_id=spo_allocation_id,
            spo_number_raw=spo_number_raw,
            company_id=str(company_id) if company_id else None,
        )
        self.db.add(line)
        self.db.flush()
        return line

    def _add_picking_line(
        self,
        picking_header_id: str,
        product_id: str,
        source_warehouse_id: Optional[str],
        quantity_expected: int,
        quantity_picked: int,
        spo_allocation_id: Optional[str] = None,
        spo_number_raw: Optional[str] = None,
        company_id: Optional[str] = None,
    ) -> PickingLine:
        """Create one picking line (used by FIFO when splitting).

        ``company_id`` is the GRN header's own company, stated rather than left to
        the insert hook: under the ``X-API-Key`` NULL ("all companies") scope the
        hook stamps the INCUMBENT company, so a row written for a company-B GRN
        would land in Sorento - invisible on the GRN it belongs to while still
        consuming that GRN's allocation (AC-FM-27). The same reason forward
        matching states it on the rows a split creates.
        """
        line = PickingLine(
            picking_header_id=picking_header_id,
            product_id=product_id,
            source_warehouse_id=source_warehouse_id,
            quantity_expected=quantity_expected,
            quantity_picked=quantity_picked,
            spo_allocation_id=spo_allocation_id,
            spo_number_raw=spo_number_raw,
            picked_condition="good",
            company_id=company_id,
        )
        self.db.add(line)
        self.db.flush()
        return line

    def _create_grn_lines_with_spo_fifo(
        self,
        grn_id: str,
        spo_number: str,
        lines_payload: List[Dict[str, Any]],
    ) -> None:
        """Create picking lines for a GRN, assigning spo_allocation_id by drawing on
        the SPO's allocation pool.

        The pool and the draw are ``app.services.grn_spo_matching``'s, not this
        method's. This used to hold a THIRD copy of the two-pass
        warehouse-then-age rule (the import and forward matching hold none now),
        and it sized availability with ``compute_received_for_allocation``, which
        counts APPROVED headers only. The shared pool counts every non-rejected
        header, drafts included - which is what makes forward matching safe, and is
        what put the two furthest apart. The consequence, in the office rather than
        in the code: a draft GRN already linked for 60 against a 100-unit
        allocation was invisible here, so approving a second GRN for 100 through
        the screen drew the full 100 and left 160 units drawing on that allocation.

        Imported inside the method: ``grn_spo_matching`` imports this module at load
        time, so a module-level import here would be a cycle.

        Every line it writes also STATES its SPO (``spo_number_raw``), so a GRN that
        arrives through the UI or the external API before its allocation is
        forward-matchable, and so this delete-and-recreate does not erase what an
        imported line already said.
        """
        import app.services.grn_spo_matching as matching

        spo_key = _spo_match_key(spo_number)
        if not spo_key:
            for line_data in lines_payload:
                line_dict = {k: v for k, v in line_data.items() if k != "quantity_discrepancy"}
                line_dict["spo_number_raw"] = _stated_spo_for_line(
                    line_data.get("spo_number_raw"), spo_number
                )
                line = PickingLine(**line_dict, picking_header_id=grn_id)
                self.db.add(line)
            return

        # The GRN's own company, stated rather than assumed, on BOTH halves: the
        # ``X-API-Key`` principal resolves to a NULL scope ("all companies"), where
        # the scope layer adds no predicate at all. Reading, that offers one
        # company's allocation to another company's GRN line; writing, the insert
        # hook stamps the INCUMBENT company instead. Fixing only the read half is
        # worse than leaving both wrong, because a company-B GRN would then draw
        # correctly and still show none of the rows it drew (AC-FM-27).
        company_id = (
            self.db.query(PickingHeader.company_id)
            .filter(PickingHeader.id == grn_id)
            .scalar()
        )

        # One pool per product, shared by every payload line for that product.
        # Rebuilding it per line would hand the same capacity out twice, because
        # this GRN is excluded from its own consumption (below) and so the rows
        # already written for the previous line would not count.
        pools: Dict[str, List[Any]] = {}

        for line_data in lines_payload:
            stated_spo = _stated_spo_for_line(line_data.get("spo_number_raw"), spo_number)
            product_id = line_data.get("product_id")
            if not product_id:
                continue
            source_warehouse_id = line_data.get("source_warehouse_id")
            quantity_expected = int(line_data.get("quantity_expected") or 0)
            quantity_picked = int(line_data.get("quantity_picked") or 0)
            if quantity_expected <= 0 and quantity_picked <= 0:
                continue

            if str(product_id) not in pools:
                pools[str(product_id)] = matching.build_allocation_pool(
                    self.db,
                    product_id=str(product_id),
                    spo_number=spo_number,
                    # This path DELETES and recreates this GRN's lines, so it must
                    # not see the rows it is about to replace as capacity somebody
                    # else took - the same reason the import excludes itself.
                    exclude_header_ids={str(grn_id)},
                    company_id=str(company_id) if company_id else None,
                )
            pool = pools[str(product_id)]

            # Consume from SPO pool by received qty when present; otherwise expected (draft line with only expected filled).
            remaining = quantity_picked if quantity_picked > 0 else quantity_expected

            # Every chunk of a SPLIT carries the quantity IT drew, in BOTH columns.
            # This used to put the whole `quantity_expected` on the first chunk and
            # 0 on the rest, which made the GRN-line writers disagree: the import
            # writes the per-chunk draw, so the same receipt came out as different
            # rows depending on which writer produced it (AC-FM-19 compares
            # `quantity_expected`), and every reader that sums that column charged
            # one line's whole draw to its first allocation. See the convention note
            # in `app.services.grn_spo_matching`. The cost, stated plainly: a
            # receipt SPLIT across allocations can no longer carry an
            # expected-vs-picked discrepancy, because the split is a fact about
            # what arrived. An unsplit line still can - a single fully-covering
            # draw keeps the caller's `quantity_expected`, so a short receipt's
            # shortfall stays visible (AC-FM-30), the same guard forward matching
            # applies.
            draws = matching.draw_fifo(
                pool,
                warehouse_id=str(source_warehouse_id) if source_warehouse_id else None,
                quantity=remaining,
            )
            if not any(draw.allocation_id for draw in draws):
                # No allocation covered any of it, so this is not a split at all:
                # the line is written exactly as the caller stated it, keeping any
                # expected-vs-picked discrepancy.
                self._add_picking_line(
                    grn_id, product_id, source_warehouse_id,
                    quantity_expected, quantity_picked, None, stated_spo,
                    company_id=str(company_id) if company_id else None,
                )
                continue

            split = len(draws) > 1
            for draw in draws:
                self._add_picking_line(
                    grn_id, product_id, source_warehouse_id,
                    draw.quantity if split else quantity_expected,
                    draw.quantity, draw.allocation_id, stated_spo,
                    company_id=str(company_id) if company_id else None,
                )

    def compute_received_for_allocation(self, allocation_id: str) -> int:
        """Computed on read: sum the DRAWN quantity from picking lines where
        spo_allocation_id = allocation_id and the picking line's header (GRN) is
        approved. Not stored in DB.

        The drawn quantity is ``quantity_picked`` - see the convention note in
        ``app.services.grn_spo_matching``. It has to be the same column
        ``build_allocation_pool`` measures, or the capacity a line consumes and the
        receipt it reports disagree: summing ``quantity_expected`` charged a split
        line's whole document quantity to its FIRST allocation and nothing to the
        rest, which reported a partial receipt as fully received."""
        from sqlalchemy import func
        total = (
            self.db.query(func.coalesce(func.sum(PickingLine.quantity_picked), 0))
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .filter(
                PickingLine.spo_allocation_id == allocation_id,
                PickingHeader.picking_type == "goods_received",
                PickingHeader.picking_status == "approved",
            )
            .scalar()
        )
        return int(total)

    def get_computed_received_map(self, allocation_ids: list[str]) -> dict[str, int]:
        """Bulk: for each allocation id, return computed quantity_received (the sum
        of the drawn quantity over approved GRN lines - the same column
        ``compute_received_for_allocation`` sums)."""
        if not allocation_ids:
            return {}
        from sqlalchemy import func
        rows = (
            self.db.query(PickingLine.spo_allocation_id, func.coalesce(func.sum(PickingLine.quantity_picked), 0))
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .filter(
                PickingLine.spo_allocation_id.in_(allocation_ids),
                PickingHeader.picking_type == "goods_received",
                PickingHeader.picking_status == "approved",
            )
            .group_by(PickingLine.spo_allocation_id)
            .all()
        )
        received_map = {str(r[0]): int(r[1]) for r in rows}
        return {aid: received_map.get(aid, 0) for aid in allocation_ids}

    def _allocation_ids_of_grn(self, grn_id: str) -> set:
        """The allocations this GRN's picking lines currently point at (D28).

        Read before a delete or an unlink, so the re-sync afterwards can tell
        "this allocation is being released" apart from "nothing ever picked
        against this allocation" - the two look identical once the lines are
        gone, and only the first may zero a stored receipt.
        """
        rows = (
            self.db.query(PickingLine.spo_allocation_id)
            .filter(
                PickingLine.picking_header_id == grn_id,
                PickingLine.spo_allocation_id.isnot(None),
            )
            .all()
        )
        return {str(row[0]) for row in rows}

    def _allocation_has_picking_line(self, allocation_id: str) -> bool:
        """Whether ANY picking line points at this allocation (D28,
        spo-xlsx-supersede).

        `compute_received_for_allocation` answers 0 for an allocation nothing
        has ever picked against, and writing that 0 back would erase a receipt
        this system did not compute: an ESB-stated `qty_received`, or the
        receipt a superseded xlsx-era row carried onto its AutoCount lines
        (D26). A GRN deletion lowering an ESB-era line's receipt is named out
        of scope in the plan - it cannot lower one below the ESB-stated value
        today either.

        Deliberately NOT filtered to approved goods-received headers: the
        question here is ownership ("does a GRN draw against this row at all"),
        not how much has been approved, and a line whose GRN is still pending
        is exactly the row whose receipt should compute to 0 rather than stay
        frozen.
        """
        return (
            self.db.query(PickingLine.id)
            .filter(PickingLine.spo_allocation_id == allocation_id)
            .first()
            is not None
        )

    def _write_received(
        self, alloc: SPOAllocation, total: int, *, may_reopen: bool = False
    ) -> None:
        """The one place a recompute writes a receipt onto an allocation.

        `receipt_status` AND `line_status` both come off the same test
        (AC-X33), so no path can leave the pair contradicting itself:

        - receipt reaches the allocation -> `closed` + `fully_received`;
        - receipt below it, row currently open -> stays `open` + `pending`;
        - receipt below it, row closed BY A RECEIPT (`closed` +
          `fully_received`) -> reopened when `may_reopen` (D28c, AC-X35): the
          receipt that closed it is gone, and a line reading closed with
          nothing received is supply this system can no longer see - the exact
          MB2 defect. Only the group path passes `may_reopen`, because only
          there is the receipt the whole reason the line was closed.
        - receipt below it, row closed any OTHER way (the leftover sweep, a
          cancelled document) -> left closed. A recompute does not revive
          demand a document retired; those rows are `scm_upload`-era anyway
          and never reach the group path.
        """
        closed_by_receipt = (alloc.line_status or "") == "closed" and (
            alloc.receipt_status or ""
        ) == "fully_received"
        alloc.quantity_received = total
        fully_received = total >= (alloc.allocated_quantity or 0)
        alloc.receipt_status = "fully_received" if fully_received else "pending"
        if fully_received:
            alloc.line_status = "closed"
        elif may_reopen and closed_by_receipt:
            alloc.line_status = "open"

    def _autocount_group_members(self, alloc: SPOAllocation) -> list[SPOAllocation]:
        """The AutoCount lines sharing this allocation's `(spo_number, product,
        location)` group, in `spo_line_number` order (D28a).

        Only `source_system='autocount'` rows join the group: an AutoCount
        line is one of N lines standing for what the upload held as ONE
        aggregated row, so a GRN draw against any of them measures the
        GROUP's receipt. A `scm_upload` or CRM-written row in the same
        (product, location) keeps its own per-allocation receipt and must not
        be folded in, or its picking lines would be counted twice.

        Scoped to the allocation's OWN company explicitly, on top of the
        ambient company filter (S9), so this can never gather another
        company's lines under a shared SPO number.
        """
        target_key = _spo_match_key(alloc.spo_number)
        location = _spo_group_location(alloc.location_code)
        rows = (
            self.db.query(SPOAllocation)
            .filter(
                SPOAllocation.company_id == alloc.company_id,
                # AC-X34: `spo_number` and `product_id` are BOTH predicates in
                # SQL (`ix_spo_allocations_spo_product_warehouse` leads on
                # exactly that pair), so a product carried by hundreds of
                # shipping orders costs one index probe rather than a load of
                # every SPO it ever appeared on. The equality is safe for this
                # group: an AutoCount row's number is the one AutoCount stated,
                # never a variant spelling - only xlsx-era rows carry those,
                # and they are excluded by `source_system` below. The
                # `_spo_match_key` comparison stays as the authority.
                SPOAllocation.spo_number == alloc.spo_number,
                SPOAllocation.product_id == alloc.product_id,
                SPOAllocation.source_system == shipping_order_rules.AUTOCOUNT_SOURCE_SYSTEM,
                # D28d: a retired line is not a member of anything. It is
                # closed and otherwise identical to a live fully received one,
                # which is how a retired DocKey's rows rejoined the new
                # DocKey's group, took a Seq-order share of its GRN and
                # reopened when that GRN was deleted.
                SPOAllocation.retired_at.is_(None),
            )
            .all()
        )
        members = [
            row
            for row in rows
            if _spo_match_key(row.spo_number) == target_key
            and _spo_group_location(row.location_code) == location
            # Compared in Python too (AC-X47): the SQL predicate above is an
            # index probe, and every OTHER component of the key is re-checked
            # here - a UUID that compares equal in SQL but not as a string
            # (or a driver that hands back a different type) must not slip
            # into a group whose receipt it would then share.
            and str(row.product_id) == str(alloc.product_id)
        ]
        if not members:
            return [alloc]
        members.sort(
            key=lambda r: (
                r.spo_line_number if r.spo_line_number is not None else 10**9,
                str(r.id),
            )
        )
        return members

    def _sync_group_received(
        self, members: list[SPOAllocation], *, released: set, group_released: bool = False
    ) -> None:
        """D28a/D28c: one AutoCount group's approved picking total over its
        lines, floored per line by what a DECLARER stated for it.

        The picking total is spread in `spo_line_number` order through the SAME
        `shipping_order_rules.distribute_received` the first-push supersede
        carries a receipt with (each line up to its allocated quantity,
        remainder onto the last) - never one line's own picking total onto that
        line alone, which is what a repointed GRN draw (D27) would otherwise
        make of a 47-unit receipt against a 29 + 18 line pair.

        D28's ownership rule governs entry, at GROUP level: if nothing
        APPROVED picks against any member and none is being released, nothing
        here computed the stored values and they are left alone. A draft GRN's
        line does not open the group for rewrite - it proves nothing has been
        received yet, and the approval is the event that does.

        D28c, the per-line FLOOR (this replaces D28b's stored-sum floor, which
        used the very figure that could not be trusted): each line is written
        `max(stated_received, its share)`, so

        - AutoCount's own `TransferedQty` (and a supersede / dedupe carry,
          which is the same kind of statement) survives a GRN delete - MB1 was
          a released line's stated 25 being written to 0;
        - a share that only a GRN produced leaves WITH that GRN, because
          nothing states it - MB2 was 18 stranded on a sibling for ever.

        A RELEASED member (its GRN unlinked, re-pointed or deleted) is outside
        the distribution entirely and is written `max(stated, its own
        remaining approved picking lines)`: a sibling's proven receipt must
        never land on the line whose GRN was just taken away.

        A member that is not LIVE (`_is_live_group_member`: cancelled, or
        closed for any reason other than a receipt) is skipped completely - no
        share, no write, no reopen (AC-X41). Its OWN approved draws stay with
        it and are NOT counted for anybody else (AC-X42): the row still
        reports the receipt it was retired holding, so redistributing that
        same figure onto the standing lines would make the group report one
        GRN twice. What the live members share is the picking total of the
        live members, in Seq order, remainder on the last live line.
        """
        member_ids = [str(member.id) for member in members]
        released_ids = released & set(member_ids)
        has_approved_line = (
            self.db.query(PickingLine.id)
            .join(PickingHeader, PickingLine.picking_header_id == PickingHeader.id)
            .filter(
                PickingLine.spo_allocation_id.in_(member_ids),
                PickingHeader.picking_type == "goods_received",
                PickingHeader.picking_status == "approved",
            )
            .first()
            is not None
        )
        if not has_approved_line and not released_ids and not group_released:
            # D28's ownership gate. `group_released` (D28d) is the third way
            # in: the GRN just deleted pointed at a RETIRED line of this
            # group, which is no longer a member but whose receipt these
            # lines were sharing.
            return

        computed = self.get_computed_received_map(member_ids)

        def _stated(member: SPOAllocation) -> int:
            # NULL reads 0: a row written before migration 488 stated nothing.
            return int(member.stated_received or 0)

        non_released = [member for member in members if str(member.id) not in released_ids]
        released_members = [
            member
            for member in members
            if str(member.id) in released_ids and _is_live_group_member(member)
        ]
        live_targets = [member for member in non_released if _is_live_group_member(member)]

        for member in released_members:
            # Its OWN remaining approved picking lines, floored by what a
            # declarer stated: a sibling's proven receipt must never land on
            # the line whose GRN was just taken away (MB1).
            self._write_received(
                member,
                max(_stated(member), computed.get(str(member.id), 0)),
                may_reopen=True,
            )

        if not live_targets:
            return
        # The picking sum of the LIVE non-released members only (AC-X42): a
        # non-live member keeps its own draws, because it also keeps its own
        # receipt - it is never written here. Counting them again for the
        # live lines reported one GRN twice, once on the retired line that
        # still shows it and once redistributed onto the standing ones.
        kept_total = sum(computed.get(str(member.id), 0) for member in live_targets)
        shares = shipping_order_rules.distribute_received(
            kept_total, [int(member.allocated_quantity or 0) for member in live_targets]
        )
        for member, share in zip(live_targets, shares):
            self._write_received(member, max(_stated(member), share), may_reopen=True)

    def _sync_received_for_allocations(
        self, allocations: list[SPOAllocation], *, released: Optional[set] = None
    ) -> set:
        """Recompute `quantity_received` for these allocations; returns the
        shipment ids touched.

        Two rules, decided per row by `source_system` (D28 + D28a): an
        AutoCount line recomputes as part of its whole `(spo_number, product,
        location)` group; anything else (`scm_upload`, or the NULL the CRM UI
        and the n8n packing-list route write) recomputes on its own, which is
        what its own single picking line measures.
        """
        released = {str(value) for value in (released or set())}
        shipment_ids: set = set()
        done_groups: set = set()
        # D28d: which GROUPS a release touched, retired members included. A
        # retired line is no longer a member of its group (it takes no share
        # and is never written), but a GRN deleted off it is still an event
        # for the live lines that SHARED that receipt: without this the group
        # would fail the D28 ownership gate ("no approved line, nobody
        # released") and a sibling would keep a share of a GRN that no longer
        # exists - MB2 again, one row over (AC-X40's sibling).
        released_group_keys = {
            _spo_allocation_group_key(alloc)
            for alloc in allocations
            if str(alloc.id) in released
            and (alloc.source_system or "") == shipping_order_rules.AUTOCOUNT_SOURCE_SYSTEM
        }
        for alloc in allocations:
            if alloc.inbound_shipment_id:
                shipment_ids.add(alloc.inbound_shipment_id)
            if (alloc.source_system or "") == shipping_order_rules.AUTOCOUNT_SOURCE_SYSTEM:
                if alloc.retired_at is not None:
                    # D28d + B2 (round 2 security review, PLAN-hide-retired-spo-lines):
                    # a retired line anchors no group of its own and takes no share
                    # of a sibling's receipt - it is never written by the group path
                    # below, and `may_reopen` is never passed for it, so it can never
                    # reopen. But a receipt approved AFTER retirement (a GRN picked
                    # against it while it was still open, approved once the ingest
                    # retired it) must still reach `quantity_received`, or the row is
                    # hidden (`visible_line_clauses()` R2) with an approved
                    # goods-received note pointing at nothing. Written from its OWN
                    # approved picking lines only, floored by whatever was stated for
                    # it before retirement (D28c) - the same floor a live member gets,
                    # so a later GRN delete cannot drop it below what was already
                    # proven (AC-X40: stated 29 beats a recomputed 0).
                    #
                    # Round 4 (security review, AC-H14): the SAME ownership gate the
                    # non-AutoCount branch applies twenty lines below - skip a row that
                    # is neither released nor picked against, because nothing here
                    # computed its stored value. Without it a backfill-stamped row
                    # carrying a real ESB-stated receipt but no `stated_received` floor
                    # (every row written before migration 488) got written unconditionally
                    # here on the next recompute - `max(0, own picking total 0) = 0` -
                    # zeroing a real receipt and then hiding the row (visible under R2
                    # only via `quantity_received > 0`). A retired row that IS picked
                    # against still takes its own approved total (AC-H10 unchanged).
                    alloc_id = str(alloc.id)
                    if alloc_id not in released and not self._allocation_has_picking_line(alloc_id):
                        continue
                    self._write_received(
                        alloc,
                        max(
                            int(alloc.stated_received or 0),
                            self.compute_received_for_allocation(alloc_id),
                        ),
                        may_reopen=False,
                    )
                    continue
                key = _spo_allocation_group_key(alloc)
                if key in done_groups:
                    continue
                done_groups.add(key)
                members = self._autocount_group_members(alloc)
                self._sync_group_received(
                    members,
                    released=released,
                    group_released=key in released_group_keys,
                )
                for member in members:
                    if member.inbound_shipment_id:
                        shipment_ids.add(member.inbound_shipment_id)
                continue
            alloc_id = str(alloc.id)
            if alloc_id not in released and not self._allocation_has_picking_line(alloc_id):
                # D28: nothing picks against this row and nobody is releasing
                # it, so its stored receipt was stated (ESB) or carried (D26),
                # not computed here.
                continue
            self._write_received(alloc, self.compute_received_for_allocation(alloc_id))
        return shipment_ids

    def sync_grn_received_to_spo(self, picking_header_id: str) -> None:
        """After GRN is approved: set quantity_received on each affected SPO allocation (DB field, for legacy/reports).
        From picking lines (spo_allocation_id = allocation, header approved). Idempotent.
        Also refreshes inbound_shipment_lines.line_status for affected shipments.

        The allocations come from this header's OWN picking lines, so each of
        them has at least one by construction - D28's "no picking line, keep
        the stored value" skip is unreachable here (reviewer cleanup) and is
        not repeated. What DOES apply is D28a: an AutoCount line recomputes
        with its whole group, through the shared
        `_sync_received_for_allocations`.
        """
        lines = self.db.query(PickingLine).filter(
            PickingLine.picking_header_id == picking_header_id,
            PickingLine.spo_allocation_id.isnot(None),
        ).all()
        allocation_ids = {str(line.spo_allocation_id) for line in lines if line.spo_allocation_id}
        allocations = [
            alloc
            for alloc in (
                self.db.query(SPOAllocation).filter(SPOAllocation.id == alloc_id).first()
                for alloc_id in allocation_ids
            )
            if alloc is not None
        ]
        shipment_ids = self._sync_received_for_allocations(allocations)
        self.db.commit()
        inbound_svc = InboundShipmentService(self.db)
        for sid in shipment_ids:
            inbound_svc.refresh_shipment_line_statuses(sid)

    def sync_received_for_spo_number(
        self,
        spo_number: Optional[str],
        *,
        released_allocation_ids: Optional[set] = None,
        company_id: Optional[str] = None,
    ) -> None:
        """Re-sync DB quantity_received for all allocations under this SPO (optional background use).

        `released_allocation_ids` (D28, spo-xlsx-supersede): allocations a
        caller has just DETACHED - a GRN unlinked, re-pointed or deleted - and
        is therefore explicitly releasing. They recompute even though no
        picking line points at them any more, because that recompute IS the
        release: the stored receipt they still carry is the one being given
        back. Every OTHER allocation with no picking line keeps its stored
        value (an ESB-stated receipt, or one carried onto an AutoCount line by
        the first-push supersede), which nothing here computed and nothing
        here may zero.

        `company_id` (S1, review round 2), same shape as `build_allocation_pool`'s:
        when given, it NARROWS the query with an explicit equality filter IN
        ADDITION to the ambient `CompanyScopedMixin` predicate (`do_orm_execute`
        auto-filters every scoped model by the session's company scope) - it does
        not replace that auto-filter, only the extra `build_company_predicate`
        call below, which is redundant with it when the session scope is already
        resolved. A caller that already knows the document's own company (a job
        with no company snapshot runs system-scoped - "all companies" - where the
        ambient auto-filter constrains nothing) states it explicitly rather than
        let this sweep rewrite another company's allocations under the same SPO
        number.
        """
        if not spo_number or not spo_number.strip():
            return
        target_key = _spo_match_key(spo_number)
        if not target_key:
            return
        query = self.db.query(SPOAllocation).filter(SPOAllocation.spo_number.isnot(None))
        if company_id:
            query = query.filter(SPOAllocation.company_id == str(company_id))
        else:
            # S9 (security review): the company filter stated HERE rather than
            # left entirely to the ambient session scope. An SPO number is not
            # unique across companies, and this sweep writes every row it reads -
            # a caller whose session scope was never resolved must not be one
            # accident away from recomputing another company's document.
            predicate = build_company_predicate(SPOAllocation, get_company_scope(self.db))
            if predicate is not None:
                query = query.filter(predicate)
        allocations = [
            alloc
            for alloc in query.all()
            if _spo_match_key(alloc.spo_number) == target_key
        ]
        shipment_ids = self._sync_received_for_allocations(
            allocations, released=released_allocation_ids
        )
        self.db.commit()
        inbound_svc = InboundShipmentService(self.db)
        for sid in shipment_ids:
            inbound_svc.refresh_shipment_line_statuses(sid)

    def get_linked_grns_for_spo(self, spo_number: Optional[str]):
        """Return list of GRN headers (id, picking_number, picking_status, picking_date) for this SPO number.
        Matches by alphanumeric SPO key so variant formats (e.g. SPO-202602-0102 vs SPO-2026/02-0102) align."""
        if not spo_number or not spo_number.strip():
            return []
        target_key = _spo_match_key(spo_number)
        if not target_key:
            return []
        rows = (
            self.db.query(
                PickingHeader.id,
                PickingHeader.picking_number,
                PickingHeader.picking_status,
                PickingHeader.picking_date,
                PickingHeader.spo_number,
            )
            .filter(
                PickingHeader.picking_type == "goods_received",
                PickingHeader.spo_number.isnot(None),
            )
            .order_by(PickingHeader.picking_date.desc().nulls_last(), PickingHeader.picking_number)
            .all()
        )
        return [
            {"id": str(r[0]), "picking_number": r[1], "picking_status": r[2], "picking_date": r[3]}
            for r in rows
            if _spo_match_key(r[4]) == target_key
        ]


class StockInquiryService:
    """Service for stock inquiry operations."""
    
    def __init__(self, db: Session):
        self.db = db
        from app.services.entity_attachment_service import EntityAttachmentService
        self.entity_attachment_service = EntityAttachmentService(db)

    def _company_for_stock_inquiry(self, inquiry_id: str) -> str:
        """AC-E4: a stock inquiry routes to its contact's company, else the default.

        ``stock_inquiries`` has no company column, so the submitter contact is the
        only signal.
        """
        from app.models.procurement import StockInquiry
        from app.services.company_routing_service import company_for_contact

        row = (
            self.db.query(StockInquiry.contact_id)
            .filter(StockInquiry.id == str(inquiry_id))
            .first()
        )
        return company_for_contact(self.db, contact_id=str(row[0]) if row and row[0] else None)

    def _get_team_user_ids_for_agent_code(self, agent_code: str, *, company_id: str) -> List[str]:
        """Return user IDs of all teams assigned to the access agent with the given code."""
        from app.services.user_service import AccessAgentService
        from app.models.access import AgentTeam, TeamMember

        agent_id = AccessAgentService(self.db).get_agent_id_by_code(agent_code)
        if not agent_id:
            logger.debug("No access agent found for code=%s", agent_code)
            return []

        rows = (
            self.db.query(TeamMember.user_id)
            .join(AgentTeam, AgentTeam.team_id == TeamMember.team_id)
            .filter(AgentTeam.agent_id == agent_id)
            .distinct()
            .all()
        )
        return [str(r[0]) for r in rows if r and r[0]]

    def _get_team_user_ids_for_agent_team_assignment(
        self, agent_code: str, team_assignment_code: str, *, company_id: str
    ) -> List[str]:
        """Return user IDs of the team assigned to the agent with the given team assignment code (e.g. project_sales, purchasing)."""
        from app.services.user_service import AccessAgentService
        from app.models.access import TeamMember

        agent_svc = AccessAgentService(self.db)
        agent_id = agent_svc.get_agent_id_by_code(agent_code)
        if not agent_id:
            logger.debug("No access agent found for code=%s", agent_code)
            return []
        team_id = agent_svc.get_team_id_by_code(agent_id, team_assignment_code, company_id=company_id)
        if not team_id:
            logger.debug(
                "No team assignment found for agent %s with code=%s",
                agent_code,
                team_assignment_code,
            )
            return []
        rows = self.db.query(TeamMember.user_id).filter(TeamMember.team_id == team_id).all()
        return [str(r[0]) for r in rows if r and r[0]]

    def _get_team_user_ids_for_agent_tier(
        self, agent_code: str, tier: int, *, company_id: str
    ) -> List[str]:
        """Return user IDs of the team assigned to the agent with the given tier (1=initial, 2/3=escalation)."""
        from app.services.user_service import AccessAgentService
        from app.models.access import TeamMember

        agent_svc = AccessAgentService(self.db)
        agent_id = agent_svc.get_agent_id_by_code(agent_code)
        if not agent_id:
            logger.debug("No access agent found for code=%s", agent_code)
            return []
        team_id = agent_svc.get_team_id_by_tier(agent_id, tier, company_id=company_id)
        if not team_id:
            logger.debug(
                "No team assignment found for agent %s with tier=%s",
                agent_code,
                tier,
            )
            return []
        rows = self.db.query(TeamMember.user_id).filter(TeamMember.team_id == team_id).all()
        return [str(r[0]) for r in rows if r and r[0]]

    def _get_team_user_ids_for_agent_tier_safe(self, agent_code: str, tier: int, *, company_id: str) -> List[str]:
        """Tier lookup; returns [] if multiple teams share the same tier (ambiguous) or lookup fails."""
        try:
            return self._get_team_user_ids_for_agent_tier(agent_code, tier, company_id=company_id)
        except HTTPException:
            logger.warning(
                "Tier %s for agent %s is ambiguous or invalid (e.g. multiple team sets at tier %s). "
                "Assign team code project_sales under this agent.",
                tier,
                agent_code,
                tier,
            )
            return []

    def _build_stock_inquiry_view_url(self, inquiry_id: str, base_url_override: Optional[str] = None) -> str:
        """Build a shareable (no-auth) frontend link for a stock inquiry using view token."""
        from app.models.user import SystemSetting

        view_token = self.get_or_create_view_token(inquiry_id)
        base_url = (base_url_override or "").strip().rstrip("/")
        if not base_url:
            base_url = (settings.frontend_base_url or "").strip().rstrip("/")
        if not base_url:
            sys_settings = self.db.query(SystemSetting).first()
            if sys_settings and getattr(sys_settings, "website_url", None):
                base_url = (sys_settings.website_url or "").strip().rstrip("/")
        return f"{base_url}/view/stock-inquiry?token={view_token}" if base_url else f"/view/stock-inquiry?token={view_token}"

    def _stock_inquiry_portal_or_view_url(self, inquiry, inquiry_id: str) -> str:
        """Bare interactive portal link for the stock inquiry (contact can act /
        resubmit), falling back to the read-only public view URL. Mirrors
        ``ComplaintService._complaint_portal_or_view_url``. Used for the
        ``portal_url`` structured-template variable.
        """
        from app.services.portal_service import PortalService

        portal = PortalService(self.db).submission_link(
            getattr(inquiry, "contact_id", None),
            "stock_inquiry",
            inquiry_id,
        )
        if portal:
            return portal.strip()
        return (self._build_stock_inquiry_view_url(inquiry_id) or "").strip()

    def _build_stock_inquiry_internal_url(self, inquiry_id: str, base_url_override: Optional[str] = None) -> str:
        """Build the IN-SYSTEM (login-required) detail link for a stock inquiry
        used for STAFF team notifications (e.g. "New Stock Inquiry created"), so the
        recipient lands on the authenticated detail page. If not signed in, the
        protected layout sends them to /signin?callbackUrl=… and back here after
        login. Contact-facing messages keep the public /view token link instead."""
        from app.models.user import SystemSetting

        base_url = (base_url_override or "").strip().rstrip("/")
        if not base_url:
            base_url = (settings.frontend_base_url or "").strip().rstrip("/")
        if not base_url:
            sys_settings = self.db.query(SystemSetting).first()
            if sys_settings and getattr(sys_settings, "website_url", None):
                base_url = (sys_settings.website_url or "").strip().rstrip("/")
        path = f"/procurement-management/stock-inquiries/{inquiry_id}"
        return f"{base_url}{path}" if base_url else path

    def _notify_team_stock_inquiry(
        self,
        *,
        inquiry_id: str,
        agent_code: str,
        team_assignment_code: Optional[str] = None,
        title: str,
        intro_plain: str,
        intro_html: str,
        event_type: str,
        base_url_override: Optional[str] = None,
        sync_email: bool = False,
    ) -> None:
        """Notify a team via in-app (each user) + one email to all. If team_assignment_code is set, use that assignment under the agent; else all teams for the agent. When sync_email=True, send email in the same request (e.g. for external API); otherwise enqueue to notifications queue."""
        from app.models.user import User
        from app.models.notification import Notification, NotificationDelivery
        from app.services.notification_service import NotificationService
        from datetime import datetime

        company_id = self._company_for_stock_inquiry(inquiry_id)

        if team_assignment_code:
            if team_assignment_code == "project_sales":
                # Prefer explicit team assignment code over tier 1: multiple tier-1 rows (e.g. purchasing +
                # project_sales + customer_service) make tier lookup raise conflict or pick the wrong team.
                user_ids = (
                    self._get_team_user_ids_for_agent_team_assignment(agent_code, "project_sales", company_id=company_id)
                    or self._get_team_user_ids_for_agent_tier_safe(agent_code, 1, company_id=company_id)
                )
            else:
                user_ids = self._get_team_user_ids_for_agent_team_assignment(agent_code, team_assignment_code, company_id=company_id)
        else:
            user_ids = self._get_team_user_ids_for_agent_code(agent_code, company_id=company_id)
        if not user_ids:
            logger.warning(
                "No team members found for agent code '%s'%s. Assign a team under Team Assignments (Tier 1 or code project_sales).",
                agent_code,
                f" with assignment code '{team_assignment_code}'" if team_assignment_code else "",
            )
            return

        users = self.db.query(User).filter(User.id.in_(user_ids)).all()
        emails = [u.email for u in users if getattr(u, "email", None) and str(u.email).strip()]
        if not emails:
            logger.warning("Team members for %s have no email addresses; skipping email.", agent_code)

        # Staff team notification → in-system detail link (login-required), not the
        # public /view token page. Recipients are internal team members.
        view_url = self._build_stock_inquiry_internal_url(inquiry_id, base_url_override=base_url_override)

        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(self.db).render_code(
            "stock_inquiry_created",
            {"heading": title, "intro": intro_plain, "view_url": view_url},
        )
        title = rendered["subject"]
        body_plain = rendered["body_text"]
        body_html = rendered["body_html"]

        notif_svc = NotificationService(self.db)
        first_uid = user_ids[0]
        now = datetime.utcnow()
        email_data = {"recipient_emails": emails, "single_email_to_all": True, "body_html": body_html}

        def _enqueue_deliveries(notification_id: str) -> None:
            try:
                if sync_email:
                    from app.tasks import notification_tasks

                    notification_tasks.send_notification_deliveries(notification_id)
                else:
                    from app.services.queue_service import enqueue_job
                    from app.tasks import notification_tasks

                    enqueue_job(
                        notification_tasks.send_notification_deliveries,
                        notification_id,
                        queue_name="notifications",
                    )
            except Exception as e:
                logger.warning("Failed to send/enqueue notification deliveries: %s", e)

        if emails:
            existing_main = (
                self.db.query(Notification)
                .filter(
                    Notification.user_id == first_uid,
                    Notification.source_entity_type == "stock_inquiry",
                    Notification.source_entity_id == inquiry_id,
                    Notification.event_type == event_type,
                )
                .first()
            )
            if existing_main:
                existing_main.title = title
                existing_main.body = body_plain
                existing_main.type = "stock_inquiry_notification"
                merged = dict(existing_main.data or {})
                merged.update(email_data)
                existing_main.data = merged
                self.db.add(
                    NotificationDelivery(
                        notification_id=existing_main.id,
                        channel="email",
                        status="pending",
                    )
                )
                self.db.commit()
                self.db.refresh(existing_main)
                _enqueue_deliveries(str(existing_main.id))
            else:
                notification = Notification(
                    user_id=first_uid,
                    type="stock_inquiry_notification",
                    title=title,
                    body=body_plain,
                    data=email_data,
                    source_entity_type="stock_inquiry",
                    source_entity_id=inquiry_id,
                    event_type=event_type,
                )
                self.db.add(notification)
                self.db.flush()
                self.db.add(
                    NotificationDelivery(
                        notification_id=notification.id,
                        channel="in_app",
                        status="sent",
                        sent_at=now,
                    )
                )
                self.db.add(NotificationDelivery(notification_id=notification.id, channel="email", status="pending"))
                self.db.commit()
                self.db.refresh(notification)
                _enqueue_deliveries(str(notification.id))

        for uid in user_ids:
            if uid == first_uid and emails:
                continue
            try:
                existing_u = (
                    self.db.query(Notification)
                    .filter(
                        Notification.user_id == uid,
                        Notification.source_entity_type == "stock_inquiry",
                        Notification.source_entity_id == inquiry_id,
                        Notification.event_type == event_type,
                    )
                    .first()
                )
                if existing_u:
                    existing_u.title = title
                    existing_u.body = body_plain
                    existing_u.type = "stock_inquiry_notification"
                    self.db.commit()
                else:
                    notif_svc.create_in_app_only(
                        user_id=uid,
                        type="stock_inquiry_notification",
                        title=title,
                        body=body_plain,
                        source_entity_type="stock_inquiry",
                        source_entity_id=inquiry_id,
                        event_type=event_type,
                    )
            except Exception as e:
                logger.warning("Failed to create in-app notification for user %s: %s", uid, e)
    
    def _build_list_query(
        self,
        query: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "desc",
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
    ):
        """Build the filtered + sorted stock-inquiry query shared by ``list_inquiries``
        and ``neighbours`` so the two can never drift.

        The ORDER BY always appends ``StockInquiry.id`` as a deterministic tie-breaker
        so offset position and prev/next neighbours are unambiguous when the primary
        sort column has equal (or null) values.
        """
        q = self.db.query(StockInquiry)
        if contact_id is not None:
            q = q.filter(StockInquiry.contact_id == str(contact_id).strip())
        if space_id is not None:
            q = q.filter(StockInquiry.space_id == str(space_id).strip())
        if statuses:
            q = q.filter(StockInquiry.status.in_(statuses))

        if query:
            q = q.filter(
                or_(
                    StockInquiry.inquiry_number.ilike(f"%{query}%"),
                    StockInquiry.product_code.ilike(f"%{query}%"),
                    StockInquiry.item_description.ilike(f"%{query}%"),
                    StockInquiry.project_customer.ilike(f"%{query}%"),
                )
            )

        if sort_field and isinstance(sort_field, str):
            sort_field = sort_field.strip().lower() or "created_at"
        else:
            sort_field = "created_at"

        if sort_dir and isinstance(sort_dir, str):
            sort_dir = sort_dir.strip().lower() or "desc"
        else:
            sort_dir = "desc"

        sort_map = {
            "id": StockInquiry.id,
            "inquiry_number": StockInquiry.inquiry_number,
            "product_code": StockInquiry.product_code,
            "item_description": StockInquiry.item_description,
            "project_customer": StockInquiry.project_customer,
            "project_name": StockInquiry.project_name,
            "quantity": StockInquiry.quantity,
            "delivery_date": StockInquiry.delivery_date,
            "remark": StockInquiry.remark,
            "created_at": StockInquiry.created_at,
            "updated_at": StockInquiry.updated_at,
            "salesperson": StockInquiry.salesperson,
            "status": StockInquiry.status,
            "last_responded_at": StockInquiry.last_responded_at,
            "last_activity_at": func.coalesce(
                StockInquiry.last_revised_at, StockInquiry.created_at
            ),
        }
        sort_column = sort_map.get(sort_field, StockInquiry.created_at)

        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc"

        if sort_dir == "desc":
            q = q.order_by(sort_column.desc().nulls_last(), StockInquiry.id.asc())
        else:
            q = q.order_by(sort_column.asc().nulls_last(), StockInquiry.id.asc())
        return q

    def list_inquiries(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "desc",
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        viewer_user_id: Optional[str] = None,
    ):
        """List stock inquiries.

        ``viewer_user_id`` drives the Print Count column: how many PDF exports the
        CURRENT user has taken of each row (their own downloads only, batched into
        one grouped query - never an N+1 count per row).
        """
        q = self._build_list_query(
            query=query,
            sort_field=sort_field,
            sort_dir=sort_dir,
            contact_id=contact_id,
            space_id=space_id,
            statuses=statuses,
        )

        total = q.count()
        offset = (page - 1) * limit
        inquiries = q.offset(offset).limit(limit).all()
        self._attach_sla_handlers(inquiries)
        self._attach_print_counts(inquiries, viewer_user_id)

        from app.schemas.common import PaginationResponse

        return {
            "data": inquiries,
            "pagination": PaginationResponse(total=total, page=page, limit=limit),
            "empty": total == 0
        }

    def _attach_print_counts(self, items, viewer_user_id: Optional[str]) -> None:
        """Set `print_count` on each inquiry: how many PDF exports the viewing user
        has taken of that record. One grouped query for the page. With no viewer
        (e.g. an API-key principal) every row reads 0 rather than another user's count.
        """
        if not items:
            return
        counts: dict = {}
        if viewer_user_id:
            from app.services.download_service import DownloadService
            counts = DownloadService(self.db).count_map_for_user(
                str(viewer_user_id),
                "stock_inquiry",
                [str(getattr(i, "id", "")) for i in items if getattr(i, "id", None)],
            )
        for it in items:
            setattr(it, "print_count", int(counts.get(str(it.id), 0)))

    def _attach_sla_handlers(self, items) -> None:
        """Set `assigned_to_id` / `assigned_to_name` (the SLA assignee - who the
        tracker is currently escalated/assigned to) and `handled_by_name` (the
        form-handling-lock holder, set only when someone clicks Claim) on each inquiry
        from the latest unresolved form-SLA tracker. Both live on the tracker, not the
        stock_inquiry row, and are distinct: a task can be assigned to CK Lee yet
        handled by nobody. Batched per page.
        """
        ids = [str(getattr(i, "id", "")) for i in items if getattr(i, "id", None)]
        if not ids:
            return
        from app.models.sla import ConversationSLATracking
        from app.models.user import User
        from app.services.sla_scope import open_tracker_scope

        rows = (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == "stock_inquiry",
                ConversationSLATracking.source_entity_id.in_(ids),
                *open_tracker_scope(),
            )
            .order_by(
                ConversationSLATracking.source_entity_id,
                ConversationSLATracking.initiated_at.desc(),
            )
            .all()
        )
        latest: dict = {}
        for r in rows:
            latest.setdefault(r.source_entity_id, r)  # first per id = latest (desc order)
        uids = {
            uid
            for r in latest.values()
            for uid in (
                getattr(r, "handled_by_id", None),
                getattr(r, "assigned_to_id", None),
            )
            if uid
        }
        users = (
            {u.id: u for u in self.db.query(User).filter(User.id.in_(uids)).all()}
            if uids
            else {}
        )

        def _name(uid):
            u = users.get(uid) if uid else None
            return (u.name or u.email) if u else None

        for it in items:
            tracker = latest.get(str(it.id))
            hid = getattr(tracker, "handled_by_id", None) if tracker else None
            aid = getattr(tracker, "assigned_to_id", None) if tracker else None
            setattr(it, "handled_by_name", _name(hid))
            setattr(it, "assigned_to_id", str(aid) if aid else None)
            setattr(it, "assigned_to_name", _name(aid))

    def get_inquiry(
        self,
        inquiry_id: str,
        *,
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
    ):
        """Get a stock inquiry by ID."""
        q = self.db.query(StockInquiry).filter(StockInquiry.id == inquiry_id)
        if contact_id is not None:
            q = q.filter(StockInquiry.contact_id == str(contact_id).strip())
        if space_id is not None:
            q = q.filter(StockInquiry.space_id == str(space_id).strip())
        inquiry = q.first()
        if not inquiry:
            raise handle_not_found("Stock Inquiry", inquiry_id)
        return inquiry

    def _resolve_requestor_display_name(self, contact_id: Optional[str]) -> Optional[str]:
        """Display name of a requestor contact, or None. Read-only lookup - the
        eligibility check + the actual stamping still go through
        requestor_options_service.apply_requestor_contact."""
        raw = str(contact_id).strip() if contact_id else ""
        if not raw:
            return None
        from app.models.access import RespondContact
        from app.services.requestor_options_service import contact_display_name

        row = self.db.query(RespondContact).filter(RespondContact.id == raw).first()
        return contact_display_name(row) if row is not None else None

    def _resolve_user_display_name(self, user_id: Optional[str]) -> Optional[str]:
        """Resolve user id (CRM id or respond_user_id) to display name (name or email)."""
        if not user_id or not str(user_id).strip():
            return None
        from app.models.user import User
        user = (
            self.db.query(User)
            .filter(or_(User.id == user_id, User.respond_user_id == user_id))
            .first()
        )
        if not user:
            return None
        return user.name or user.email or None

    def get_inquiry_for_response(
        self,
        inquiry_id: str,
        *,
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
    ) -> dict:
        """Get stock inquiry as dict with last_responded_by_name resolved for API response."""
        inquiry = self.get_inquiry(inquiry_id, contact_id=contact_id, space_id=space_id)
        data = {attr.key: getattr(inquiry, attr.key) for attr in inspect(inquiry).mapper.column_attrs}
        data["system_id"] = str(inquiry.id)
        data["form_type"] = "stock_inquiry"
        data["view_url"] = self._build_stock_inquiry_view_url(str(inquiry.id))
        # `column_attrs` skips python properties, so the derived requestor name
        # has to be copied in explicitly or the response returns null.
        data["salesperson_contact_name"] = inquiry.salesperson_contact_name
        # Same reason - a python property, skipped by column_attrs. The detail page
        # gates its response affordances on this rather than mirroring the status
        # list (UAC O1).
        data["response_write_allowed"] = inquiry.response_write_allowed
        data["last_responded_by_name"] = (
            self._resolve_user_display_name(inquiry.last_responded_by) if inquiry.last_responded_by else None
        )
        data["rejected_by_name"] = (
            self._resolve_user_display_name(inquiry.rejected_by) if inquiry.rejected_by else None
        )
        # Rejecter's wa.me digits for the rejection banner link (rejected_by = users.id).
        data["rejected_by_wa_phone"] = (
            wa_phone_for_user_id(self.db, inquiry.rejected_by) if inquiry.rejected_by else None
        )
        data["reopened_by_name"] = (
            self._resolve_user_display_name(inquiry.reopened_by) if inquiry.reopened_by else None
        )
        # Void banner (BAN-1): resolve voided_by -> display name; wa phone null
        # (no form-banner-person-links resolver on this branch).
        data["voided_by_name"] = (
            self._resolve_user_display_name(inquiry.voided_by) if getattr(inquiry, "voided_by", None) else None
        )
        data["voided_by_wa_phone"] = None
        links = self.entity_attachment_service.list_links("stock_inquiry", str(inquiry.id))
        data["attachments"] = [
            self.entity_attachment_service.serialize_link(
                link,
                entity_key="inquiry_id",
                link_type="stock_inquiry_attachment",
            )
            for link in links
        ]
        return data

    # ----- Void (terminal, irreversible) -----
    # Stock inquiry has no resolved/closed lifecycle; the terminal states are
    # 'rejected' (which can be reopened) and 'voided'. Everything else
    # (new / pending_project_sales / pending_purchasing / responded) is voidable.
    _VOID_BLOCKED_STATUSES: frozenset[str] = frozenset({"voided", "rejected"})

    def void_inquiry(
        self,
        inquiry_id: str,
        *,
        void_reason: str,
        actor_user_id: Optional[str] = None,
        respond_user_id: Optional[str] = None,
    ):
        """Void a stock inquiry (irreversible). See ``PurchaseRequestService.void_request``."""
        reason = (void_reason or "").strip()
        if len(reason) < 3:
            raise handle_validation_error(
                "A void reason of at least 3 characters is required."
            )
        inquiry = self.get_inquiry(inquiry_id)
        current = (getattr(inquiry, "status", None) or "").strip().lower()
        if current in self._VOID_BLOCKED_STATUSES:
            raise handle_conflict(
                f"This stock inquiry cannot be voided from its current state "
                f"({current or 'unknown'})."
            )

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        inquiry.status = "voided"
        inquiry.voided_by = actor_user_id
        inquiry.voided_at = now_utc
        inquiry.void_reason = reason
        self.db.commit()
        self.db.refresh(inquiry)

        # Notify BEFORE emit (emit may resolve the tracker via 'voided' resolve_event,
        # after which the in-app assignee/handler lookup would find nothing).
        self._notify_inquiry_voided(
            inquiry, actor_user_id=actor_user_id, respond_user_id=respond_user_id
        )

        try:
            from app.services.form_sla_service import emit_form_event

            emit_form_event(
                self.db,
                "stock_inquiry",
                str(inquiry.id),
                "voided",
                contact_id=getattr(inquiry, "contact_id", None),
                actor_user_id=actor_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'voided' failed for inquiry %s: %s", inquiry.id, e)

        return inquiry

    def _notify_inquiry_voided(
        self,
        inquiry: StockInquiry,
        *,
        actor_user_id: Optional[str],
        respond_user_id: Optional[str],
    ) -> None:
        """Best-effort void notifications (NTF): assignee + handler in-app, salesperson WhatsApp."""
        try:
            from app.services.form_void_notify import notify_form_voided_in_app

            notify_form_voided_in_app(
                self.db,
                source_entity_type="stock_inquiry",
                source_entity_id=str(inquiry.id),
                entity_number=display_document_number(inquiry) or None,
                actor_user_id=actor_user_id,
            )
        except Exception:
            logger.warning("Void in-app notify failed for inquiry %s", inquiry.id, exc_info=True)

        try:
            number = (display_document_number(inquiry) or str(inquiry.id)).strip()
            reason = (getattr(inquiry, "void_reason", None) or "").strip()
            message_text = (
                f"Your stock inquiry {number} has been voided. Reason: {reason}"
                if reason
                else f"Your stock inquiry {number} has been voided."
            )
            self._send_stock_inquiry_contact_message(
                inquiry,
                message_text=message_text,
                crm_sender_user_id=actor_user_id,
                respond_user_id_fallback=respond_user_id,
                extra_context_vars={"update": "Voided", "message": message_text},
            )
        except Exception:
            logger.warning(
                "Void salesperson WhatsApp send failed for inquiry %s; void committed.",
                inquiry.id,
                exc_info=True,
            )

    def get_neighbour_ids(self, inquiry_id: str) -> dict:
        """Return prev_id and next_id for the given inquiry (order: id desc, same as default list)."""
        inquiry = self.db.query(StockInquiry).filter(StockInquiry.id == inquiry_id).first()
        if not inquiry:
            return {"prev_id": None, "next_id": None}
        q_desc = (
            self.db.query(StockInquiry.id)
            .order_by(StockInquiry.id.desc())
        )
        ids = [r[0] for r in q_desc.all()]
        try:
            idx = ids.index(inquiry_id)
        except ValueError:
            return {"prev_id": None, "next_id": None}
        prev_id = ids[idx - 1] if idx > 0 else None
        next_id = ids[idx + 1] if idx < len(ids) - 1 else None
        return {"prev_id": prev_id, "next_id": next_id}

    def get_or_create_view_token(self, inquiry_id: str) -> str:
        """Get or create a reusable view token for this stock inquiry.

        New tokens are committed via an isolated session so this can safely be called
        from a serializer/read path without piggybacking the caller's pending writes
        into a premature commit.
        """
        self.get_inquiry(inquiry_id)  # ensure exists
        row = (
            self.db.query(ViewToken)
            .filter(
                ViewToken.entity_type == "stock_inquiry",
                ViewToken.entity_id == inquiry_id,
            )
            .first()
        )
        if row:
            return row.token
        from app.database import SessionLocal

        token_value = secrets.token_urlsafe(32)
        isolated = SessionLocal()
        try:
            existing = (
                isolated.query(ViewToken)
                .filter(
                    ViewToken.entity_type == "stock_inquiry",
                    ViewToken.entity_id == inquiry_id,
                )
                .first()
            )
            if existing:
                return existing.token
            isolated.add(
                ViewToken(
                    entity_type="stock_inquiry",
                    entity_id=inquiry_id,
                    token=token_value,
                )
            )
            isolated.commit()
        except Exception:
            isolated.rollback()
            raise
        finally:
            isolated.close()
        return token_value

    def get_inquiry_summary_by_token(self, token_value: str) -> dict:
        """Return read-only stock inquiry summary for the given view token. No auth required."""
        view_token = (
            self.db.query(ViewToken)
            .filter(ViewToken.token == token_value, ViewToken.entity_type == "stock_inquiry")
            .first()
        )
        if not view_token or not view_token.entity_id:
            raise handle_not_found("View link", "(invalid token)")
        inquiry = self.get_inquiry(str(view_token.entity_id))
        links = self.entity_attachment_service.list_links("stock_inquiry", str(inquiry.id))
        return {
            "entity_type": "stock_inquiry",
            "entity_id": inquiry.id,
            "inquiry_number": getattr(inquiry, "inquiry_number", None),
            "salesperson": getattr(inquiry, "salesperson", None),
            "product_code": getattr(inquiry, "product_code", None),
            "item_description": getattr(inquiry, "item_description", None),
            "project_customer": getattr(inquiry, "project_customer", None),
            "project_name": getattr(inquiry, "project_name", None),
            "quantity": getattr(inquiry, "quantity", None),
            "delivery_date": getattr(inquiry, "delivery_date", None),
            "remark": getattr(inquiry, "remark", None),
            "additional_remark": getattr(inquiry, "additional_remark", None),
            "purchasing_response": getattr(inquiry, "purchasing_response", None),
            "status": getattr(inquiry, "status", None),
            "last_responded_at": getattr(inquiry, "last_responded_at", None),
            "last_responded_by": getattr(inquiry, "last_responded_by", None),
            "last_responded_by_name": (
                self._resolve_user_display_name(inquiry.last_responded_by)
                if getattr(inquiry, "last_responded_by", None)
                else None
            ),
            "rejection_reason": getattr(inquiry, "rejection_reason", None),
            "rejected_at": getattr(inquiry, "rejected_at", None),
            "rejected_by": getattr(inquiry, "rejected_by", None),
            "rejected_by_name": (
                self._resolve_user_display_name(inquiry.rejected_by)
                if getattr(inquiry, "rejected_by", None)
                else None
            ),
            "reopen_reason": getattr(inquiry, "reopen_reason", None),
            "reopened_at": getattr(inquiry, "reopened_at", None),
            "reopened_by": getattr(inquiry, "reopened_by", None),
            "reopened_by_name": (
                self._resolve_user_display_name(inquiry.reopened_by)
                if getattr(inquiry, "reopened_by", None)
                else None
            ),
            "created_at": getattr(inquiry, "created_at", None),
            "updated_at": getattr(inquiry, "updated_at", None),
            "attachments": [
                self.entity_attachment_service.serialize_link(
                    link,
                    entity_key="inquiry_id",
                    link_type="stock_inquiry_attachment",
                )
                for link in links
            ],
        }

    def request_inquiry_revision_by_token(self, token_value: str) -> dict[str, str]:
        """Trigger the external revise webhook for a rejected stock inquiry public view."""
        view_token = (
            self.db.query(ViewToken)
            .filter(ViewToken.token == token_value, ViewToken.entity_type == "stock_inquiry")
            .first()
        )
        if not view_token or not view_token.entity_id:
            raise handle_not_found("View link", "(invalid token)")

        inquiry = self.get_inquiry(str(view_token.entity_id))
        if getattr(inquiry, "status", None) != "rejected":
            raise handle_conflict("Revise is only available for rejected stock inquiries.")

        self._enqueue_public_revise_webhook_for_stock_inquiry(inquiry)
        return {"message": "Revise request sent successfully. You may now open Whatsapp to revise the inquiry."}

    def _build_respond_inbox_url(self, contact_id: Optional[str], space_id: Optional[str]) -> Optional[str]:
        """Build respond.io inbox URL: {base}/space/{space_id}/inbox/{respond_io_id}.

        Accepts internal RespondContact.id (UUID) and resolves it to the numeric
        respond_io_id required by the Respond.io app URL + API.
        """
        if not contact_id or not space_id:
            return None
        from app.services.respond_identifier import (
            format_respond_inbox_url,
            resolve_respond_io_id,
        )

        rid = resolve_respond_io_id(self.db, contact_id)
        return format_respond_inbox_url(settings.respond_app_base_url, space_id, rid)

    # Allowed initial statuses when creating via API (e.g. external flow can start in pending_project_sales).
    _CREATE_ALLOWED_STATUSES = ("new", "pending_project_sales", "pending_purchasing")

    # Integration / MCP / external submit: must match tool spec and agent prompts.
    _STOCK_INQUIRY_SUBMISSION_REQUIRED_FIELDS: tuple[str, ...] = (
        "product_code",
        "salesperson",
        "item_description",
        "project_customer",
        "project_name",
        "quantity",
        "delivery_date",
        "contact_id",
        "space_id",
    )

    def _missing_stock_inquiry_submission_fields(
        self,
        values: Dict[str, Any],
        *,
        require_contact_scope: bool = True,
    ) -> list[str]:
        missing: list[str] = []
        for key in self._STOCK_INQUIRY_SUBMISSION_REQUIRED_FIELDS:
            if not require_contact_scope and key in {"contact_id", "space_id"}:
                continue
            raw = values.get(key)
            if raw is None:
                missing.append(key)
                continue
            if isinstance(raw, str) and not raw.strip():
                missing.append(key)
        return missing

    def _require_complete_stock_inquiry_submission(
        self,
        values: Dict[str, Any],
        *,
        require_contact_scope: bool = True,
    ) -> None:
        missing = self._missing_stock_inquiry_submission_fields(
            values,
            require_contact_scope=require_contact_scope,
        )
        if missing:
            required_fields = [
                f
                for f in self._STOCK_INQUIRY_SUBMISSION_REQUIRED_FIELDS
                if require_contact_scope or f not in {"contact_id", "space_id"}
            ]
            raise handle_validation_error(
                "Stock inquiry submission is incomplete. Required fields: "
                + ", ".join(required_fields)
                + f". Missing or empty: {', '.join(missing)}."
            )

    def _stock_inquiry_row_as_submission_dict(self, inquiry: StockInquiry) -> Dict[str, Any]:
        return {k: getattr(inquiry, k, None) for k in self._STOCK_INQUIRY_SUBMISSION_REQUIRED_FIELDS}

    def _merged_stock_inquiry_submission_for_resubmit(
        self, inquiry: StockInquiry, inquiry_data: StockInquiryCreate
    ) -> Dict[str, Any]:
        """Merge rejected row + incoming payload to validate required submission fields pre-confirmation."""
        merged = self._stock_inquiry_row_as_submission_dict(inquiry)
        incoming = inquiry_data.model_dump(exclude_unset=True)
        for key in self._STOCK_INQUIRY_SUBMISSION_REQUIRED_FIELDS:
            if key in incoming:
                merged[key] = incoming.get(key)
        return merged

    def _resubmit_rejected_inquiry(
        self,
        inquiry: StockInquiry,
        inquiry_data: StockInquiryCreate,
        *,
        require_contact_scope: bool = True,
    ) -> StockInquiry:
        """Apply create payload to an existing rejected inquiry; audit logs via ORM UPDATE tracking."""
        data = inquiry_data.model_dump(exclude_unset=True)
        data.pop("inquiry_number", None)
        data.pop("user_confirmed", None)

        new_status = data.pop("status", None)
        if new_status is not None and new_status not in self._CREATE_ALLOWED_STATUSES:
            raise handle_validation_error(
                f"status must be one of {self._CREATE_ALLOWED_STATUSES!r}, got {new_status!r}"
            )
        if new_status is None:
            new_status = "new"

        column_keys = {a.key for a in inspect(inquiry).mapper.column_attrs}
        immutable = {"id", "created_at", "inquiry_number", "updated_at"}
        requestor_value = data.pop("salesperson_contact_id", _UNSET_REQUESTOR)
        for key, value in list(data.items()):
            if key in immutable or key not in column_keys:
                continue
            setattr(inquiry, key, value)
        if requestor_value is not _UNSET_REQUESTOR:
            _apply_requestor_contact(
                self.db, inquiry, "salesperson_contact_id", "salesperson", requestor_value
            )

        inquiry.status = new_status

        contact_id = inquiry.contact_id
        space_id = inquiry.space_id
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            inquiry.respond_inbox_url = respond_inbox_url
        elif contact_id is None and space_id is None:
            inquiry.respond_inbox_url = None

        if new_status != "rejected":
            inquiry.rejection_reason = None
            inquiry.rejected_at = None
            inquiry.rejected_by = None
            inquiry.rejected_from = None

        inquiry.reopen_reason = None
        inquiry.reopened_at = None
        inquiry.reopened_by = None

        self.db.commit()
        self.db.refresh(inquiry)
        self._require_complete_stock_inquiry_submission(
            self._stock_inquiry_row_as_submission_dict(inquiry),
            require_contact_scope=require_contact_scope,
        )
        try:
            if (inquiry.status or "").strip().lower() != "new":
                from app.services.form_sla_service import emit_form_event
                emit_form_event(
                    self.db,
                    "stock_inquiry",
                    str(inquiry.id),
                    "submit",
                    contact_id=getattr(inquiry, "contact_id", None),
                    actor_user_id=None,
                )
        except Exception as e:
            logger.warning("Form SLA emit 'submit' (resubmit_rejected_inquiry) failed for %s: %s", inquiry.id, e)
        return inquiry

    def create_inquiry(
        self,
        inquiry_data: StockInquiryCreate,
        *,
        require_user_confirmation: bool = False,
    ):
        """
        Create a new stock inquiry, or resubmit a rejected one.

        When ``inquiry_number`` is set and a row exists with ``status == rejected``,
        that row is updated (same id / number) and this returns ``(inquiry, "resubmitted")``.
        Otherwise a new row is inserted and this returns ``(inquiry, "created")``.
        """
        from datetime import date as date_cls

        full = inquiry_data.model_dump()
        lookup_raw = full.get("inquiry_number")
        # Tolerate a revision suffix (UAC N6). The stored number stays bare, but an
        # external caller echoing back a number it read from one of our payloads
        # sends "SI-26-0184-R2". Matching that literally finds nothing and this
        # method then INSERTS a duplicate instead of resubmitting the rejected row -
        # silent duplication on a live integration path, not a visible 404.
        lookup = strip_revision_suffix(lookup_raw) if isinstance(lookup_raw, str) else None
        lookup = lookup.strip() if lookup else None
        if lookup:
            existing = (
                self.db.query(StockInquiry)
                .filter(StockInquiry.inquiry_number == lookup)
                .first()
            )
            if existing is not None:
                if existing.status != "rejected":
                    raise handle_validation_error(
                        f"Inquiry number {lookup!r} already exists with status {existing.status!r}. "
                        "Use inquiry_number only to resubmit a rejected inquiry."
                    )
                # For update, enforce completion against merged existing+incoming values
                # BEFORE asking for explicit confirmation.
                self._require_complete_stock_inquiry_submission(
                    self._merged_stock_inquiry_submission_for_resubmit(existing, inquiry_data),
                    require_contact_scope=require_user_confirmation,
                )
                if require_user_confirmation and inquiry_data.user_confirmed is not True:
                    raise handle_validation_error(
                        "Explicit user confirmation is required before submission. "
                        "Set user_confirmed=true only after the user explicitly confirms the final summary "
                        "(e.g. OK, YES, CONFIRM)."
                    )
                updated = self._resubmit_rejected_inquiry(
                    existing,
                    inquiry_data,
                    require_contact_scope=require_user_confirmation,
                )
                return updated, "resubmitted"

        data = inquiry_data.model_dump()
        data.pop("inquiry_number", None)
        data.pop("user_confirmed", None)
        # `salesperson` is a REQUIRED submission field, but the CRM form now
        # offers a contact picker instead of the free-text input - so derive the
        # label from the FK before the completeness gate, or a perfectly complete
        # submission is rejected for a field the user can no longer type.
        if data.get("salesperson_contact_id") and not (data.get("salesperson") or "").strip():
            derived = self._resolve_requestor_display_name(data["salesperson_contact_id"])
            if derived:
                data["salesperson"] = derived
        self._require_complete_stock_inquiry_submission(
            {k: data.get(k) for k in self._STOCK_INQUIRY_SUBMISSION_REQUIRED_FIELDS},
            require_contact_scope=require_user_confirmation,
        )
        if require_user_confirmation and inquiry_data.user_confirmed is not True:
            raise handle_validation_error(
                "Explicit user confirmation is required before submission. "
                "Set user_confirmed=true only after the user explicitly confirms the final summary "
                "(e.g. OK, YES, CONFIRM)."
            )
        contact_id = data.get("contact_id")
        space_id = data.get("space_id")
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            data["respond_inbox_url"] = respond_inbox_url
        status = data.get("status")
        if status is not None and status not in self._CREATE_ALLOWED_STATUSES:
            raise handle_validation_error(
                f"status must be one of {self._CREATE_ALLOWED_STATUSES!r}, got {status!r}"
            )
        if status is None:
            data["status"] = "new"
        from app.services.numbering_service import NumberingService

        generated = NumberingService(self.db).get_next_number("stock_inquiry", date_cls.today())
        if generated:
            data["inquiry_number"] = generated
        # Filter the splat: a derived/display field that is not a column would
        # raise TypeError and 500 the whole create route (mirrors create_request).
        requestor_value = data.pop("salesperson_contact_id", None)
        inquiry = StockInquiry(**{k: v for k, v in data.items() if hasattr(StockInquiry, k)})
        if requestor_value:
            _apply_requestor_contact(
                self.db, inquiry, "salesperson_contact_id", "salesperson", requestor_value
            )
        self.db.add(inquiry)
        self.db.commit()
        self.db.refresh(inquiry)
        # Portal/external submissions land here directly with status="pending_project_sales"
        # (or "pending_purchasing" for internal flows), bypassing submit_inquiry_for_project_sales.
        # Fire the SLA "submit" event for any non-"new" landing so form_sla_configs starts a tracker.
        try:
            if (inquiry.status or "").strip().lower() != "new":
                from app.services.form_sla_service import emit_form_event
                emit_form_event(
                    self.db,
                    "stock_inquiry",
                    str(inquiry.id),
                    "submit",
                    contact_id=getattr(inquiry, "contact_id", None),
                    actor_user_id=None,
                )
        except Exception as e:
            logger.warning("Form SLA emit 'submit' (create_inquiry) failed for %s: %s", inquiry.id, e)
        return inquiry, "created"

    def delete_inquiry(self, inquiry_id: str) -> dict:
        """Delete a stock inquiry by ID."""
        inquiry = self.get_inquiry(inquiry_id)
        self.entity_attachment_service.delete_links_for_entity("stock_inquiry", str(inquiry.id))
        self.db.delete(inquiry)
        self.db.commit()
        return {"message": "Stock inquiry deleted successfully"}

    def bulk_delete_inquiries(self, inquiry_ids: list[str]) -> dict:
        """Delete multiple stock inquiries by ID."""
        if not inquiry_ids:
            return {"message": "No inquiries to delete", "deleted_count": 0}
        deleted = 0
        for iid in inquiry_ids:
            inquiry = self.db.query(StockInquiry).filter(StockInquiry.id == iid).first()
            if inquiry:
                self.entity_attachment_service.delete_links_for_entity("stock_inquiry", str(inquiry.id))
                self.db.delete(inquiry)
                deleted += 1
        self.db.commit()
        return {"message": f"{deleted} stock inquiry(ies) deleted", "deleted_count": deleted}

    def link_attachment_to_inquiry(self, inquiry_id: str, attachment_id: str, created_by: Optional[str] = None):
        """Link an existing attachment to a stock inquiry (generic entity_attachment_links table)."""
        self.get_inquiry(inquiry_id)  # ensure inquiry exists
        link = self.entity_attachment_service.link_existing_attachment(
            entity_type="stock_inquiry",
            entity_id=str(inquiry_id),
            attachment_id=str(attachment_id),
            created_by=created_by,
        )
        self.db.commit()
        self.db.refresh(link)
        return link

    def delete_inquiry_attachment(self, link_id: str):
        """Delete a stock-inquiry attachment link from generic entity_attachment_links table."""
        link = self.entity_attachment_service.delete_link(link_id, entity_type="stock_inquiry")
        self.db.commit()
        return link

    def _identifier_from_respond_inbox_url(self, respond_inbox_url: Optional[str]) -> Optional[str]:
        """Resolve contact identifier from respond_inbox_url to a numeric respond_io_id.

        The URL's last segment may be either a numeric respond_io_id (correct, new
        rows) or an internal RespondContact.id UUID (legacy rows pre-migration).
        Always resolve via DB so RespondClient.send_message receives the value
        Respond.io expects.
        """
        if not respond_inbox_url or not respond_inbox_url.strip():
            return None
        parts = [p for p in respond_inbox_url.rstrip("/").split("/") if p]
        if not parts:
            return None
        from app.services.respond_identifier import resolve_send_identifier

        return resolve_send_identifier(self.db, parts[-1])

    def _enqueue_stock_inquiry_respond_message(
        self,
        *,
        inquiry_id: str,
        identifier: str,
        message_text: str,
        respond_user_id: str,
        crm_sender_user_id: Optional[str],
        space_id: Optional[str],
        verify_delivery: bool = True,
        extra_context_vars: Optional[dict] = None,
    ) -> None:
        """Push a Respond.io send into the RQ ``respond_io`` queue.

        Decouples the external API call from the request lifecycle so a Respond.io
        4xx/5xx (or failed delivery) does not roll back the surrounding business
        write. Failed jobs land in RQ's FailedJobRegistry and a ``status='failed'``
        integration_logs row.

        ``extra_context_vars`` carries the structured-template vars (bare ``update``
        core + ``portal_url``) that can't be reconstructed from the inquiry row.
        Appended LAST in the positional args (enqueue_job serializes positionally).
        """
        from app.services.queue_service import enqueue_job
        from app.tasks.respond_io_tasks import send_stock_inquiry_respond_message

        job = enqueue_job(
            send_stock_inquiry_respond_message,
            inquiry_id,
            identifier,
            message_text,
            respond_user_id,
            crm_sender_user_id,
            space_id,
            verify_delivery,
            extra_context_vars,
            queue_name="respond_io",
            job_timeout=180,
        )
        logger.info(
            "Enqueued respond.io send job %s for stock_inquiry %s",
            job.id,
            inquiry_id,
        )

    def _send_stock_inquiry_contact_message(
        self,
        inquiry: StockInquiry,
        *,
        message_text: str,
        crm_sender_user_id: Optional[str] = None,
        respond_user_id_fallback: Optional[str] = None,
        extra_context_vars: Optional[dict] = None,
    ) -> None:
        """Enqueue a Respond.io text message for the inquiry's contact."""
        from app.services.error_handler import handle_validation_error

        identifier = self._identifier_from_respond_inbox_url(getattr(inquiry, "respond_inbox_url", None))
        if not identifier:
            raise handle_validation_error(
                "respond_inbox_url is missing or invalid; cannot send message. Set contact_id and space_id."
            )
        message_to_send = str(message_text or "").strip()
        if not message_to_send:
            raise handle_validation_error("message_text is required.")

        self._enqueue_stock_inquiry_respond_message(
            inquiry_id=str(inquiry.id),
            identifier=identifier,
            message_text=message_to_send,
            respond_user_id=(respond_user_id_fallback or "").strip() or identifier,
            crm_sender_user_id=crm_sender_user_id,
            space_id=getattr(inquiry, "space_id", None),
            verify_delivery=False,
            extra_context_vars=extra_context_vars,
        )

    def _enqueue_public_revise_webhook_for_stock_inquiry(self, inquiry: StockInquiry) -> None:
        """Send an incoming-style webhook payload for a rejected stock inquiry revise request."""
        import threading
        import time

        from app.models.access import RespondContact
        from app.schemas.integration import IntegrationLogCreate
        from app.services.error_handler import handle_validation_error
        from app.services.integration_service import IntegrationLogService
        from app.services.n8n_webhook_settings import get_n8n_stock_inquiry_revise_webhook_url

        webhook_url = get_n8n_stock_inquiry_revise_webhook_url(self.db)
        if not webhook_url:
            raise handle_validation_error("Stock inquiry revise webhook is not configured.")

        contact_key = (
            (getattr(inquiry, "contact_id", None) or "").strip()
            or (self._identifier_from_respond_inbox_url(getattr(inquiry, "respond_inbox_url", None)) or "").strip()
        )
        if not contact_key:
            raise handle_validation_error("This stock inquiry is not linked to a contact.")

        contact = (
            self.db.query(RespondContact)
            .filter(
                or_(
                    RespondContact.respond_io_id == contact_key,
                    RespondContact.id == contact_key,
                )
            )
            .first()
        )
        contact_respond_io_id = (
            (getattr(contact, "respond_io_id", None) or "").strip() or contact_key
        )
        contact_phone = _resolve_contact_phone_for_webhook(contact, contact_respond_io_id)
        contact_id_value: Any
        try:
            contact_id_value = int(str(contact_respond_io_id).strip())
        except (TypeError, ValueError):
            contact_id_value = contact_respond_io_id

        first_name = ((getattr(contact, "first_name", None) or "").strip() or None)
        last_name = ((getattr(contact, "last_name", None) or "").strip() or None)
        if not (first_name or last_name):
            raw_name = (getattr(contact, "name", None) or "").strip()
            if raw_name:
                parts = raw_name.split(None, 1)
                first_name = parts[0] if parts else None
                last_name = parts[1] if len(parts) > 1 else None

        inquiry_number = (display_document_number(inquiry) or str(inquiry.id)).strip()
        now_ms = int(time.time() * 1000)
        now_s = int(time.time())
        payload = [
            {
                "contact": {
                    "id": contact_id_value,
                    "phone": contact_phone,
                    "firstName": first_name or "",
                    "lastName": last_name or "",
                    "role": "user",
                    "created_at": now_s,
                },
                "message": {
                    "messageId": now_ms * 1000,
                    "channelMessageId": None,
                    "contactId": contact_id_value,
                    "channelId": None,
                    "traffic": "incoming",
                    "timestamp": now_ms,
                    "message": {
                        "type": "text",
                        "text": f"I want to edit stock inquiry for {inquiry_number}",
                    },
                },
                "channel": {
                    "id": None,
                    "name": "Whatsapp Business",
                    "source": "whatsapp_business",
                    "meta": None,
                    "created_at": now_s,
                },
                "sender": {
                    "source": "contact",
                    "userId": None,
                    "teamId": None,
                    "workflowId": None,
                    "broadcastHistoryId": None,
                },
                "source": "Contact",
                "crm": {
                    "business_table": "stock_inquiries",
                    "business_id": str(inquiry.id),
                    "space_id": getattr(inquiry, "space_id", None),
                },
            }
        ]

        log_service = IntegrationLogService(self.db)
        integration_log = log_service.create_integration_log(
            IntegrationLogCreate(
                integration_channel="n8n_stock_inquiry_revise",
                business_table="stock_inquiries",
                business_id=str(inquiry.id),
                external_reference=str(contact_respond_io_id),
                direction="outbound",
                endpoint=webhook_url,
                http_method="POST",
                status="pending",
            ),
            request_payload_dict=payload,
        )

        log_id = str(integration_log.id)

        def send_async() -> None:
            try:
                from app.database import SessionLocal

                bg_db = SessionLocal()
                try:
                    bg_service = IntegrationLogService(bg_db)
                    bg_service.send_webhook_for_log(log_id)
                finally:
                    bg_db.close()
            except Exception as e:
                logger.error(
                    "Stock inquiry revise webhook failed for log %s: %s",
                    log_id,
                    e,
                    exc_info=True,
                )

        threading.Thread(target=send_async, daemon=True).start()

    def update_inquiry(self, inquiry_id: str, inquiry_data: StockInquiryUpdate):
        """Update a stock inquiry. Status is only changed via workflow actions (submit/approve/reject/reopen)."""
        inquiry = self.get_inquiry(inquiry_id)

        update_data = inquiry_data.model_dump(exclude_unset=True)
        # Status only via the workflow endpoints; a payload that would MOVE it is
        # refused rather than silently dropped.
        _pop_status_or_refuse_move(
            update_data,
            current=inquiry.status,
            label="stock inquiry",
            actions="submit, approve, reject or reopen",
        )
        update_data.pop("inquiry_number", None)  # System-assigned; not editable via update API

        # Response gate (UAC O1): the purchasing response is stage output, so it
        # may only be REWRITTEN while the inquiry is still with purchasing. Every
        # other field stays editable at any status, and a save that posts the
        # response back unchanged (the edit form posts the whole entity) is not a
        # response write.
        if "purchasing_response" in update_data and response_text_changed(
            getattr(inquiry, "purchasing_response", None),
            update_data.get("purchasing_response"),
        ):
            assert_response_write_allowed("stock_inquiry", inquiry.status)

        contact_id = update_data.get("contact_id") if "contact_id" in update_data else inquiry.contact_id
        space_id = update_data.get("space_id") if "space_id" in update_data else inquiry.space_id
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            update_data["respond_inbox_url"] = respond_inbox_url
        elif contact_id is None and space_id is None:
            update_data["respond_inbox_url"] = None

        # Requestor FK is validated + label-derived, never a bare setattr: the
        # document / PDF / approval page print the TEXT label, so stamping the FK
        # alone leaves them reading "-" (PLAN-requested-by-contact-routing D6/D9).
        requestor_value = update_data.pop("salesperson_contact_id", _UNSET_REQUESTOR)

        # Status is only changed via workflow actions (submit/approve/reject/reopen) and update_and_reply
        for key, value in update_data.items():
            setattr(inquiry, key, value)
        if requestor_value is not _UNSET_REQUESTOR:
            _apply_requestor_contact(
                self.db, inquiry, "salesperson_contact_id", "salesperson", requestor_value
            )

        self.db.commit()
        self.db.refresh(inquiry)
        return inquiry

    STOCK_INQUIRY_REPLY_PREAMBLE = "There is a response to your stock inquiry"

    @classmethod
    def compose_stock_inquiry_reply_message(cls, bare_body: str, portal_url: str) -> str:
        """The contact-facing WhatsApp text for a purchasing reply.

        Composed HERE, not in the frontend. The FE used to build
        ``f"{preamble}{' ' + view_url}: {body}"``, which produced two defects:

        1. The ``:`` landed immediately after the URL, so WhatsApp's autolinker
           pulled it into the href and the contact got an invalid link.
        2. It used the read-only ``/view/stock-inquiry?token=`` page, built on
           ``window.location.origin`` - whatever host the staff browser was on - 
           instead of the interactive portal link the backend already resolves via
           ``_stock_inquiry_portal_or_view_url`` for the template's ``portal_url``.

        The URL therefore goes LAST, alone on its own line after a blank line, so
        no punctuation or word can ever be absorbed into it. The colon stays where
        it reads correctly: after the preamble.
        """
        body = (bare_body or "").strip()
        url = (portal_url or "").strip()
        text = f"{cls.STOCK_INQUIRY_REPLY_PREAMBLE}:"
        if body:
            text = f"{text}\n{body}"
        return f"{text}\n\n{url}" if url else text

    @staticmethod
    def _bare_stock_inquiry_reply(raw: Optional[str]) -> str:
        """Strip a LEGACY "There is a response to your stock inquiry {link}: "
        preamble, keeping ONLY the purchasing wording for the lean ``update``
        template var.

        The frontend no longer composes that string - it posts the bare wording and
        ``compose_stock_inquiry_reply_message`` builds the outgoing text - so for
        current clients this is a no-op passthrough. It stays for two inputs that
        still carry the old shape: a stored ``purchasing_response`` written before
        the change and re-sent, and any client not yet on the new build. Without it
        those would stack a second preamble and re-send the stale read-only view
        link.

        The old link had no ": " (colon-space), so the last ": " is the body
        separator. Mirrors complaint reply normalization.
        """
        s = (raw or "").strip()
        # Both spellings: the module was renamed Stock Inquiry -> Stock Inquiry, and
        # rows written before that carry the old preamble.
        if not s.startswith(
            ("There is a response to your stock inquiry", "There is a response to your stock inquiry")
        ):
            return s
        idx = s.rfind(": ")
        return s[idx + 2 :].strip() if idx != -1 else s

    def update_inquiry_and_reply(
        self,
        inquiry_id: str,
        inquiry_data: StockInquiryUpdate,
        respond_user_id: str,
        request_url: str = "",
        crm_sender_user_id: Optional[str] = None,
    ):
        """
        Deliver the purchasing response: mark SLA as responded, set status=responded,
        record ``last_responded_*`` and queue the Respond.io message via RQ.

        This is the RESPONSE path, not the chat path, so it is gated on status
        (UAC O1): outside the response stage it raises 422. Plain messaging is a
        different endpoint (``/conversation/send-message``) and stays open at any
        status, including closed, rejected and voided inquiries (UAC O2).

        DB writes commit synchronously; the external Respond.io call is decoupled
        through the ``respond_io`` queue so a downstream 4xx/5xx no longer rolls
        back the business state. All integration calls are logged via IntegrationLogService.
        """
        import logging
        from datetime import datetime, timezone
        from app.services.integration_service import IntegrationLogService
        from app.schemas.integration import IntegrationLogCreate
        from app.services.sla_service import ConversationSLATrackingService
        from app.schemas.sla import ConversationSLATrackingUpdate

        logger = logging.getLogger(__name__)
        log_service = IntegrationLogService(self.db)

        inquiry = self.get_inquiry(inquiry_id)
        # The whole call is a response write (it stamps last_responded_* and fires
        # the purchasing_respond SLA event), so refuse it outside the response
        # stage rather than gating a single field.
        assert_response_write_allowed("stock_inquiry", inquiry.status)
        # Now guaranteed true by the gate above; kept as the one name the workflow
        # steps below read, and sourced from the same tuple as the gate - through
        # the same normalizer, or the two could disagree. They used to: the gate
        # matches on a stripped, lower-cased status while this read the raw column,
        # so a row holding "Responded" passed the gate and then skipped the flip,
        # which is the one path on which a payload ``status`` reached the entity.
        transition_to_responded_workflow = is_response_status_allowed(
            "stock_inquiry", inquiry.status
        )

        update_data = inquiry_data.model_dump(exclude_unset=True)
        # Belt and braces on top of that. The flip below overwrites whatever a
        # payload asked for, so a supplied status was inert - but "inert because a
        # later line happens to overwrite it" is not a guard, and this endpoint
        # accepts an API key (n8n). Refused the same way as on the plain update
        # path, with ``responded`` accepted as the echo of where this call lands,
        # so a caller asking for exactly what the reply does is not turned away.
        _pop_status_or_refuse_move(
            update_data,
            current=inquiry.status,
            label="stock inquiry",
            actions="submit, approve, reject or reopen",
            also_allowed=("responded",) if transition_to_responded_workflow else (),
        )
        update_data.pop("inquiry_number", None)
        contact_id = update_data.get("contact_id") if "contact_id" in update_data else inquiry.contact_id
        space_id = update_data.get("space_id") if "space_id" in update_data else inquiry.space_id
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            update_data["respond_inbox_url"] = respond_inbox_url
        elif contact_id is None and space_id is None:
            update_data["respond_inbox_url"] = None

        # Message to send (may include view link); do not write back to purchasing_response
        message_text = update_data.get("purchasing_response") or inquiry.purchasing_response
        if not (message_text and str(message_text).strip()):
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error("purchasing_response is required to reply.")
        # Do not persist the reply payload (often includes view link) into purchasing_response
        update_data.pop("purchasing_response", None)

        for key, value in update_data.items():
            setattr(inquiry, key, value)
        self.db.flush()

        identifier = self._identifier_from_respond_inbox_url(inquiry.respond_inbox_url)
        if not identifier:
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error("respond_inbox_url is missing or invalid; cannot send message.")

        # Compose the full message (may include view link) - sent async via RQ after commit
        message_to_send = str(message_text).strip()

        # Attachment sentence (UAC D1-D4, D2 hard blocker): the sentence is
        # composed here for the OUTGOING message only, from the count of
        # staff-uploaded (uploader_kind='user') attachments already linked to
        # this inquiry (staged in the "Edit purchasing response" modal). It is
        # bare-stripped BEFORE appending, since _bare_stock_inquiry_reply's
        # rfind(": ") would otherwise match the sentence's own "response: N"
        # colon instead of the FE-composed preamble's, dropping the real reply
        # body. purchasing_response itself is never touched: it was already
        # popped from update_data above.
        from app.services.entity_attachment_service import (
            compose_response_attachment_sentence,
            count_staff_attachments,
        )

        attachment_sentence = compose_response_attachment_sentence(
            count_staff_attachments(self.db, "stock_inquiry", str(inquiry.id))
        )
        # The FE now posts only the purchasing wording, so the strip is a no-op;
        # it still runs so a legacy client (or a re-sent stored row) carrying the old
        # "{preamble} {view_url}: {body}" shape is normalised instead of stacking a
        # second preamble and re-sending the stale read-only view link.
        bare_reply = self._bare_stock_inquiry_reply(message_to_send)
        outgoing_bare = f"{bare_reply}\n{attachment_sentence}" if attachment_sentence else bare_reply

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        if transition_to_responded_workflow:
            sla_service = ConversationSLATrackingService(self.db)
            tracking = sla_service.get_tracking_by_source_entity("stock_inquiry", inquiry_id)
            if tracking:
                try:
                    sla_service.update_tracking(
                        str(tracking.id),
                        ConversationSLATrackingUpdate(
                            is_responded=True,
                            responded_at=now_utc,
                            responded_by=respond_user_id,
                        ),
                    )
                    log_service.create_integration_log(
                        IntegrationLogCreate(
                            integration_channel="sla_management",
                            business_table="conversation_sla_tracking",
                            business_id=str(tracking.id),
                            external_reference=inquiry_id,
                            direction="inbound",
                            endpoint=request_url or "/api/v1/procurement/stock-inquiries/update-and-reply",
                            http_method="POST",
                            status="success",
                        ),
                        request_payload_dict={"is_responded": True, "responded_by": respond_user_id},
                    )
                except Exception as sla_err:
                    logger.warning("SLA tracking update failed for stock_inquiry %s: %s", inquiry_id, sla_err)
                    log_service.create_integration_log(
                        IntegrationLogCreate(
                            integration_channel="sla_management",
                            business_table="conversation_sla_tracking",
                            business_id=str(tracking.id),
                            external_reference=inquiry_id,
                            direction="inbound",
                            endpoint=request_url or "/api/v1/procurement/stock-inquiries/update-and-reply",
                            http_method="POST",
                            status="failed",
                            error_message=str(sla_err),
                        ),
                        request_payload_dict={"is_responded": True, "responded_by": respond_user_id},
                    )

            inquiry.status = "responded"

        inquiry.last_responded_by = respond_user_id
        inquiry.last_responded_at = now_utc
        self.db.commit()
        self.db.refresh(inquiry)

        # Structured-template vars: bare purchasing_response text (+ attachment
        # sentence, D10) as `update` core + links. Mirrors complaint's bare
        # `stored_body`.
        # One resolution, shared by the in-window text and the out-of-window
        # template, so both carry the same interactive portal link.
        portal_url = self._stock_inquiry_portal_or_view_url(inquiry, str(inquiry.id))
        reply_extra_vars = {
            "update": outgoing_bare,
            "portal_url": portal_url,
            "view_url": (self._build_stock_inquiry_view_url(str(inquiry.id)) or "").strip(),
        }
        outgoing_text = self.compose_stock_inquiry_reply_message(outgoing_bare, portal_url)
        self._enqueue_stock_inquiry_respond_message(
            inquiry_id=inquiry_id,
            identifier=identifier,
            message_text=outgoing_text,
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
            space_id=getattr(inquiry, "space_id", None),
            verify_delivery=True,
            extra_context_vars=reply_extra_vars,
        )

        if transition_to_responded_workflow:
            try:
                from app.services.form_sla_service import emit_form_event
                emit_form_event(
                    self.db,
                    "stock_inquiry",
                    str(inquiry.id),
                    "purchasing_respond",
                    contact_id=getattr(inquiry, "contact_id", None),
                    actor_user_id=crm_sender_user_id or respond_user_id,
                )
            except Exception as e:
                logger.warning("Form SLA emit 'purchasing_respond' failed for stock_inquiry %s: %s", inquiry_id, e)
        return inquiry

    def submit_inquiry_for_project_sales(self, inquiry_id: str, actor_user_id: Optional[str] = None) -> StockInquiry:
        """Move inquiry from new to pending_project_sales."""
        inquiry = self.get_inquiry(inquiry_id)
        if inquiry.status != "new":
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error(f"Cannot submit for project sales when status is {inquiry.status}. Expected: new.")
        inquiry.status = "pending_project_sales"
        self.db.commit()
        self.db.refresh(inquiry)
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "stock_inquiry",
                str(inquiry.id),
                "submit",
                contact_id=getattr(inquiry, "contact_id", None),
                actor_user_id=actor_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'submit' failed for stock_inquiry %s: %s", inquiry_id, e)
        return inquiry

    def project_sales_approve_inquiry(
        self,
        inquiry_id: str,
        actor_user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
        respond_user_id_fallback: Optional[str] = None,
    ) -> StockInquiry:
        """Move inquiry from pending_project_sales to pending_purchasing."""
        inquiry = self.get_inquiry(inquiry_id)
        if inquiry.status != "pending_project_sales":
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error(f"Cannot approve when status is {inquiry.status}. Expected: pending_project_sales.")
        inquiry.status = "pending_purchasing"
        inquiry.rejection_reason = None
        inquiry.rejected_at = None
        inquiry.rejected_by = None
        inquiry.rejected_from = None
        self.db.commit()
        self.db.refresh(inquiry)
        # Notify the CONTACT that the inquiry advanced to purchasing review
        # (template when the 24h window is closed). Best-effort post-commit - a
        # send hiccup must not roll back the approved transition.
        try:
            inquiry_number = (display_document_number(inquiry) or str(inquiry.id)).strip()
            portal_url = self._stock_inquiry_portal_or_view_url(inquiry, str(inquiry.id))
            approve_extra_vars = {
                "update": "Approved by project sales manager",
                "portal_url": portal_url,
                "view_url": (self._build_stock_inquiry_view_url(str(inquiry.id)) or "").strip(),
            }
            self._send_stock_inquiry_contact_message(
                inquiry,
                message_text=(
                    f"Your stock inquiry {inquiry_number} has been approved by the project "
                    f"sales manager. Please view your submission here {portal_url}"
                ),
                crm_sender_user_id=crm_sender_user_id or actor_user_id,
                respond_user_id_fallback=respond_user_id_fallback or actor_user_id,
                extra_context_vars=approve_extra_vars,
            )
        except Exception as e:
            logger.warning(
                "Failed to notify contact on project_sales_approve for stock_inquiry %s: %s",
                inquiry_id,
                e,
            )
        try:
            self._notify_team_stock_inquiry(
                inquiry_id=str(inquiry.id),
                agent_code="lead_time_enquiries",
                team_assignment_code="purchasing",
                title="Stock Inquiry pending purchasing review",
                intro_plain="Dear Purchasing Team,\n\nA stock inquiry has been approved and is now pending purchasing review.",
                intro_html="Dear Purchasing Team,<br /><br />A stock inquiry has been approved and is now pending purchasing review.",
                event_type="pending_purchasing",
            )
        except Exception as e:
            logger.warning("Failed to notify purchasing team for stock inquiry %s: %s", inquiry_id, e)
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "stock_inquiry",
                str(inquiry.id),
                "project_sales_approve",
                contact_id=getattr(inquiry, "contact_id", None),
                actor_user_id=actor_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'project_sales_approve' failed for stock_inquiry %s: %s", inquiry_id, e)
        return inquiry

    def project_sales_reject_inquiry(
        self,
        inquiry_id: str,
        reason: Optional[str] = None,
        user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
        respond_user_id_fallback: Optional[str] = None,
    ) -> StockInquiry:
        """Move inquiry from pending_project_sales to rejected."""
        from datetime import datetime, timezone
        inquiry = self.get_inquiry(inquiry_id)
        if inquiry.status != "pending_project_sales":
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error(f"Cannot reject when status is {inquiry.status}. Expected: pending_project_sales.")
        inquiry_number = (display_document_number(inquiry) or str(inquiry.id)).strip()
        reason_text = (reason or "").strip()
        if not reason_text:
            from app.services.error_handler import handle_validation_error

            raise handle_validation_error("Rejection reason is required.")
        view_url = self._build_stock_inquiry_view_url(str(inquiry.id))
        # Prefer the interactive submission portal link (minted on the fly); fall
        # back to the read-only view link.
        from app.services.portal_service import PortalService
        view_url = PortalService(self.db).submission_link(
            getattr(inquiry, "contact_id", None),
            "stock_inquiry",
            str(inquiry.id),
        ) or view_url
        # Structured-template vars: LEAN `update` core (status only) + links
        # "Rejected, reason: X" (no preamble, no inline URL). Mirrors complaint.
        ps_reject_extra_vars = {
            "update": f"Rejected, reason: {reason_text}" if reason_text else "Rejected",
            "portal_url": self._stock_inquiry_portal_or_view_url(inquiry, str(inquiry.id)),
            "view_url": (self._build_stock_inquiry_view_url(str(inquiry.id)) or "").strip(),
        }
        inquiry.status = "rejected"
        inquiry.rejected_from = "pending_project_sales"
        inquiry.rejection_reason = reason_text
        inquiry.rejected_at = datetime.now(timezone.utc).replace(tzinfo=None)
        inquiry.rejected_by = user_id
        self.db.commit()
        self.db.refresh(inquiry)
        # Notify AFTER the commit and best-effort, mirroring approve/submit. When
        # this ran first, a contact with no reachable Respond.io inbox (no
        # `respond_inbox_url`, e.g. a contact missing `respond_io_id`) raised here
        # and aborted the whole rejection - the inquiry stayed pending and could
        # never be rejected at all.
        try:
            self._send_stock_inquiry_contact_message(
                inquiry,
                message_text=(
                    f"Your stock inquiry {inquiry_number} has been rejected due to "
                    f"{reason_text} by project sales. Please view your submission here {view_url}"
                ),
                crm_sender_user_id=crm_sender_user_id or user_id,
                respond_user_id_fallback=respond_user_id_fallback or user_id,
                extra_context_vars=ps_reject_extra_vars,
            )
        except Exception as e:
            logger.warning(
                "Failed to notify contact on project_sales_reject for stock_inquiry %s: %s",
                inquiry_id,
                e,
            )
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "stock_inquiry",
                str(inquiry.id),
                "project_sales_reject",
                contact_id=getattr(inquiry, "contact_id", None),
                actor_user_id=user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'project_sales_reject' failed for stock_inquiry %s: %s", inquiry_id, e)
        return inquiry

    def purchasing_reject_inquiry(
        self,
        inquiry_id: str,
        reason: Optional[str] = None,
        user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
        respond_user_id_fallback: Optional[str] = None,
    ) -> StockInquiry:
        """Move inquiry from pending_purchasing to rejected."""
        from datetime import datetime, timezone
        inquiry = self.get_inquiry(inquiry_id)
        if inquiry.status not in ("pending_purchasing", "responded"):
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error(f"Cannot reject when status is {inquiry.status}. Expected: pending_purchasing or responded.")
        inquiry_number = (display_document_number(inquiry) or str(inquiry.id)).strip()
        reason_text = (reason or "").strip()
        if not reason_text:
            from app.services.error_handler import handle_validation_error

            raise handle_validation_error("Rejection reason is required.")
        view_url = self._build_stock_inquiry_view_url(str(inquiry.id))
        # Prefer the interactive submission portal link (minted on the fly); fall
        # back to the read-only view link.
        from app.services.portal_service import PortalService
        view_url = PortalService(self.db).submission_link(
            getattr(inquiry, "contact_id", None),
            "stock_inquiry",
            str(inquiry.id),
        ) or view_url
        # Structured-template vars: LEAN `update` core (status only) + links
        # "Rejected, reason: X" (no preamble, no inline URL). Mirrors complaint.
        pur_reject_extra_vars = {
            "update": f"Rejected, reason: {reason_text}" if reason_text else "Rejected",
            "portal_url": self._stock_inquiry_portal_or_view_url(inquiry, str(inquiry.id)),
            "view_url": (self._build_stock_inquiry_view_url(str(inquiry.id)) or "").strip(),
        }
        prior_status = inquiry.status
        inquiry.status = "rejected"
        inquiry.rejected_from = prior_status
        inquiry.rejection_reason = reason_text
        inquiry.rejected_at = datetime.now(timezone.utc).replace(tzinfo=None)
        inquiry.rejected_by = user_id
        self.db.commit()
        self.db.refresh(inquiry)
        # See project_sales_reject_inquiry: notify after the commit, best-effort,
        # so an unreachable contact cannot block the rejection itself.
        try:
            self._send_stock_inquiry_contact_message(
                inquiry,
                message_text=(
                    f"Your stock inquiry {inquiry_number} has been rejected due to "
                    f"{reason_text} by purchasing. Please view your submission here {view_url}"
                ),
                crm_sender_user_id=crm_sender_user_id or user_id,
                respond_user_id_fallback=respond_user_id_fallback or user_id,
                extra_context_vars=pur_reject_extra_vars,
            )
        except Exception as e:
            logger.warning(
                "Failed to notify contact on purchasing_reject for stock_inquiry %s: %s",
                inquiry_id,
                e,
            )
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                "stock_inquiry",
                str(inquiry.id),
                "purchasing_decide",
                contact_id=getattr(inquiry, "contact_id", None),
                actor_user_id=user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'purchasing_decide' failed for stock_inquiry %s: %s", inquiry_id, e)
        return inquiry

    def reopen_inquiry(
        self, inquiry_id: str, reason: Optional[str] = None, user_id: Optional[str] = None
    ) -> StockInquiry:
        """Move inquiry from rejected back to the state it was rejected from (rejected_from)."""
        from datetime import datetime, timezone
        inquiry = self.get_inquiry(inquiry_id)
        if inquiry.status != "rejected":
            from app.services.error_handler import handle_validation_error
            raise handle_validation_error(f"Cannot reopen when status is {inquiry.status}. Expected: rejected.")
        # Restore to the state before rejection (pending_project_sales or pending_purchasing)
        inquiry.status = inquiry.rejected_from or "pending_project_sales"
        inquiry.reopen_reason = reason
        inquiry.reopened_at = datetime.now(timezone.utc).replace(tzinfo=None)
        inquiry.reopened_by = user_id
        inquiry.rejection_reason = None
        inquiry.rejected_at = None
        inquiry.rejected_by = None
        inquiry.rejected_from = None
        self.db.commit()
        self.db.refresh(inquiry)
        return inquiry


class ProductSupplierService:
    """Service for product supplier operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_product_suppliers(
        self,
        page: int = 1,
        limit: int = 50,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        product_id: Optional[str] = None,
        supplier_id: Optional[str] = None
    ):
        """List product suppliers with filtering and pagination."""
        from sqlalchemy.orm import joinedload
        
        q = self.db.query(ProductSupplier).options(
            joinedload(ProductSupplier.product),
            joinedload(ProductSupplier.supplier)
        )
        
        if product_id:
            q = q.filter(ProductSupplier.product_id == product_id)
        
        if supplier_id:
            q = q.filter(ProductSupplier.supplier_id == supplier_id)
        
        sort_map = {
            "created_at": ProductSupplier.created_at,
        }
        sort_column = sort_map.get(sort_field, ProductSupplier.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc())
        else:
            q = q.order_by(sort_column.asc())
        
        total = q.count()
        offset = (page - 1) * limit
        product_suppliers = q.offset(offset).limit(limit).all()
        
        return {
            "data": product_suppliers,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_product_supplier(self, product_supplier_id: str):
        """Get a product supplier by ID."""
        from sqlalchemy.orm import joinedload
        product_supplier = self.db.query(ProductSupplier).options(
            joinedload(ProductSupplier.product),
            joinedload(ProductSupplier.supplier)
        ).filter(ProductSupplier.id == product_supplier_id).first()
        if not product_supplier:
            raise handle_not_found("Product Supplier", product_supplier_id)
        return product_supplier

    def list_suppliers_for_product(self, product_id: str) -> list:
        """Every current sourcing link for a product, with "their code" alongside each one
        (S4, AC-D2) - the supplier's own spelling, read off `scm.supplier_product_code_alias`
        for that (product, supplier) pair, never a column on `product_suppliers` itself: the
        alias table is the single writer, so a manual match and this field cannot drift.

        A supplier who has been matched on more than one code for this product (a correction,
        or two spellings that both landed here) shows the NEWEST alias; a dismissal names no
        product, so it never joins here at all.
        """
        rows = self.list_product_suppliers(page=1, limit=1000, product_id=product_id).get(
            "data", []
        )
        supplier_ids = {str(r.supplier_id) for r in rows}
        code_by_supplier: Dict[str, str] = {}
        if supplier_ids:
            alias_rows = (
                self.db.query(SupplierProductCodeAlias)
                .filter(
                    SupplierProductCodeAlias.product_id == product_id,
                    SupplierProductCodeAlias.supplier_id.in_(supplier_ids),
                )
                .order_by(SupplierProductCodeAlias.created_at.desc())
                .all()
            )
            for alias in alias_rows:
                code_by_supplier.setdefault(str(alias.supplier_id), alias.supplier_code)
        for row in rows:
            row.supplier_item_code = code_by_supplier.get(str(row.supplier_id))
        return rows

    @staticmethod
    def _assert_priced_in_a_currency(unit_cost, currency) -> None:
        """A price with no currency is read as ringgit everywhere downstream.

        `scm.money.normalize_currency` treats a blank code as the base currency, which is
        right for rows that predate the book having more than one currency. It is wrong
        for a price somebody types today: a yuan figure saved with no code is silently
        ranked, summed and budgeted as if it were ringgit, understating the buy. Nothing
        later can detect that, because the number looks perfectly valid. So the pairing is
        required at the point of entry, where the person still knows what they meant.
        """
        if unit_cost is None:
            return
        if not (currency or "").strip():
            raise handle_validation_error(
                "Enter the currency this price is in. A price with no currency is read as "
                f"{MONEY_BASE_CURRENCY}, which would understate the cost if it is not."
            )

    def create_product_supplier(self, product_supplier_data: ProductSupplierCreate):
        """Create a new product supplier relationship."""
        # Check if relationship already exists
        existing = self.db.query(ProductSupplier).filter(
            ProductSupplier.product_id == product_supplier_data.product_id,
            ProductSupplier.supplier_id == product_supplier_data.supplier_id
        ).first()
        if existing:
            raise handle_conflict("Product supplier relationship already exists.")

        self._assert_priced_in_a_currency(
            product_supplier_data.unit_cost, product_supplier_data.currency
        )
        # exclude_none, not a plain dump: `is_primary_supplier` is NOT NULL with a column
        # default, so passing the unset field through as an explicit None would insert NULL
        # and fail. Every other field is nullable, where omitted and null mean the same.
        product_supplier = ProductSupplier(
            **product_supplier_data.model_dump(exclude_none=True)
        )
        self.db.add(product_supplier)
        self.db.commit()
        self.db.refresh(product_supplier)
        
        # Reload with relationships
        from sqlalchemy.orm import joinedload
        return self.db.query(ProductSupplier).options(
            joinedload(ProductSupplier.product),
            joinedload(ProductSupplier.supplier)
        ).filter(ProductSupplier.id == product_supplier.id).first()
    
    def update_product_supplier(self, product_supplier_id: str, product_supplier_data: ProductSupplierUpdate):
        """Update a product supplier relationship."""
        product_supplier = self.get_product_supplier(product_supplier_id)

        update_data = product_supplier_data.model_dump(exclude_unset=True)
        # Check the MERGED row, not the patch: setting only a price on a row whose currency
        # is blank is exactly the case the rule exists for, and the patch alone cannot see it.
        self._assert_priced_in_a_currency(
            update_data.get("unit_cost", product_supplier.unit_cost),
            update_data.get("currency", product_supplier.currency),
        )
        for key, value in update_data.items():
            # Same NOT NULL column as on create: an explicit null for the primary flag is
            # not a value, so leave the row's own.
            if key == "is_primary_supplier" and value is None:
                continue
            setattr(product_supplier, key, value)

        self.db.commit()
        self.db.refresh(product_supplier)
        
        # Reload with relationships
        from sqlalchemy.orm import joinedload
        return self.db.query(ProductSupplier).options(
            joinedload(ProductSupplier.product),
            joinedload(ProductSupplier.supplier)
        ).filter(ProductSupplier.id == product_supplier.id).first()
    
    def delete_product_supplier(self, product_supplier_id: str):
        """Delete a product supplier relationship."""
        product_supplier = self.get_product_supplier(product_supplier_id)
        self.db.delete(product_supplier)
        self.db.commit()


class PurchaseRequestService:
    """Service for purchase request / sponsorship form operations."""

    def __init__(self, db: Session):
        self.db = db
        from app.services.entity_attachment_service import EntityAttachmentService
        self.entity_attachment_service = EntityAttachmentService(db)

    def _build_respond_inbox_url(self, contact_id: Optional[str], space_id: Optional[str]) -> Optional[str]:
        """Build respond.io inbox URL: {base}/space/{space_id}/inbox/{respond_io_id}.

        Accepts internal RespondContact.id (UUID) and resolves it via DB to the
        numeric respond_io_id required by the Respond.io app URL + API.
        """
        if not contact_id or not space_id:
            return None
        from app.services.respond_identifier import (
            format_respond_inbox_url,
            resolve_respond_io_id,
        )

        rid = resolve_respond_io_id(self.db, contact_id)
        return format_respond_inbox_url(settings.respond_app_base_url, space_id, rid)

    def _identifier_from_respond_inbox_url(self, respond_inbox_url: Optional[str]) -> Optional[str]:
        """Resolve contact identifier from respond_inbox_url to a numeric respond_io_id.

        The URL's last segment may be either a numeric respond_io_id (correct, new
        rows) or an internal RespondContact.id UUID (legacy rows pre-migration).
        Always resolve via DB so RespondClient.send_message receives the value
        Respond.io expects.
        """
        if not respond_inbox_url or not respond_inbox_url.strip():
            return None
        parts = [p for p in respond_inbox_url.rstrip("/").split("/") if p]
        if not parts:
            return None
        from app.services.respond_identifier import resolve_send_identifier

        return resolve_send_identifier(self.db, parts[-1])

    def _build_request_view_url(self, header_id: str, base_url_override: Optional[str] = None) -> str:
        """Build a shareable (no-auth) frontend link for a purchase request / sponsorship form."""
        from app.models.user import SystemSetting

        view_token = self.get_or_create_view_token(header_id)
        base_url = (base_url_override or "").strip().rstrip("/")
        if not base_url:
            base_url = (settings.frontend_base_url or "").strip().rstrip("/")
        if not base_url:
            sys_settings = self.db.query(SystemSetting).first()
            if sys_settings and getattr(sys_settings, "website_url", None):
                base_url = (sys_settings.website_url or "").strip().rstrip("/")
        return f"{base_url}/view/request?token={view_token}" if base_url else f"/view/request?token={view_token}"

    def _purchase_request_portal_or_view_url(self, header, request_id: str) -> str:
        """Bare interactive portal link for the request (contact can act /
        resubmit), falling back to the read-only public view URL. Mirrors
        ``ComplaintService._complaint_portal_or_view_url``. Used for the
        ``portal_url`` structured-template variable. ``submission_link`` resolves
        the entity type from the row's ``request_type`` (purchase_request /
        sponsorship_form).
        """
        from app.services.portal_service import PortalService

        portal = PortalService(self.db).submission_link(
            getattr(header, "contact_id", None),
            getattr(header, "request_type", None) or "purchase_request",
            request_id,
        )
        if portal:
            return portal.strip()
        return (self._build_request_view_url(request_id) or "").strip()

    def _send_purchase_request_contact_message(
        self,
        header: PurchaseRequestHeader,
        *,
        message_text: str,
        crm_sender_user_id: Optional[str] = None,
        respond_user_id_fallback: Optional[str] = None,
        extra_context_vars: Optional[dict] = None,
    ) -> None:
        """Send a text message to the request's Respond.io contact and mirror to the outbound webhook."""
        from app.schemas.integration import IntegrationLogCreate
        from app.services.crm_chat_outbound_webhook import (
            enqueue_crm_chat_outbound_webhook,
            resolve_sla_assignee_respond_user_id,
        )
        from app.services.error_handler import handle_validation_error
        from app.services.integration_service import IntegrationLogService

        log_service = IntegrationLogService(self.db)
        identifier = self._identifier_from_respond_inbox_url(getattr(header, "respond_inbox_url", None))
        if not identifier:
            raise handle_validation_error(
                "respond_inbox_url is missing or invalid; cannot send message. Set contact_id and space_id."
            )

        message_to_send = str(message_text or "").strip()
        if not message_to_send:
            raise handle_validation_error("message_text is required.")

        assignee_rid = resolve_sla_assignee_respond_user_id(
            self.db, "purchase_request", str(header.id)
        )

        # Window-aware send: plain text inside the 24h window, default WhatsApp
        # template outside it (plan: PLAN-whatsapp-template-fallback.md).
        from app.services.respond_messaging_service import (
            build_context_vars,
            send_text_or_template,
            use_case_for_purchase_request,
        )

        use_case = use_case_for_purchase_request(header)
        request_payload = {"message": {"type": "text", "text": message_to_send}}
        try:
            context_vars = build_context_vars(
                self.db,
                use_case=use_case,
                business_id=str(header.id),
                identifier=identifier,
            )
            # Structured-template vars (bare ``update`` core + portal_url) that can't
            # be reconstructed from the row alone - merge over the auto-resolved vars.
            if extra_context_vars:
                context_vars.update(extra_context_vars)
            result = send_text_or_template(
                self.db,
                identifier=identifier,
                text=message_to_send,
                use_case=use_case,
                context_vars=context_vars,
            )
            request_payload = result["request_payload"]
            response = result["response"]

            enqueue_crm_chat_outbound_webhook(
                self.db,
                business_table="purchase_requests",
                business_id=str(header.id),
                contact_respond_io_id=identifier,
                message_text=message_to_send,
                respond_api_response=response if isinstance(response, dict) else None,
                space_id=getattr(header, "space_id", None),
                crm_sender_user_id=crm_sender_user_id,
                respond_user_id_fallback=(respond_user_id_fallback or "").strip() or identifier,
                assignee_respond_user_id=assignee_rid,
            )

            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="purchase_requests",
                    business_id=str(header.id),
                    external_reference=identifier,
                    direction="outbound",
                    endpoint=f"https://api.respond.io/v2/contact/id:{identifier}/message",
                    http_method="POST",
                    status="success",
                    response_payload=str(response)[:50000] if response else None,
                    created_by=str(crm_sender_user_id).strip() if crm_sender_user_id else None,
                ),
                request_payload_dict=request_payload,
            )
        except Exception as e:
            logger.exception(
                "Respond.io send failed for purchase_request %s", getattr(header, "id", None)
            )
            # Log what was ACTUALLY attempted (text vs template): send_text_or_template
            # stamps the real payload + Respond HTTP response on the exception. A
            # closed-window send fails as a TEMPLATE, so without this the outbox
            # mislabels it as the default text payload (and drops the 4xx code).
            request_payload = getattr(e, "request_payload", request_payload)
            resp = getattr(e, "response", None)
            resp_code = None
            resp_body = None
            if resp is not None:
                try:
                    resp_code = resp.status_code
                except Exception:
                    resp_code = None
                try:
                    resp_body = (resp.text or "")[:50000]
                except Exception:
                    resp_body = None
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="purchase_requests",
                    business_id=str(header.id),
                    external_reference=identifier or "",
                    direction="outbound",
                    endpoint=f"https://api.respond.io/v2/contact/id:{identifier or ''}/message",
                    http_method="POST",
                    status="failed",
                    status_code=resp_code,
                    response_payload=resp_body,
                    error_message=str(e),
                    created_by=str(crm_sender_user_id).strip() if crm_sender_user_id else None,
                ),
                request_payload_dict=request_payload,
            )
            raise

    _UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )

    def _resolve_actor_display_name(self, actor_user_id: Optional[str]) -> str:
        """Resolve a CRM user id to a display name for storage in ``approved_by``.

        Returns the user's name (or email) so the "Approved by"/"Rejected by"
        field never renders a raw UUID. Falls back to the id string only if it
        is already a non-UUID value; empty string when nothing usable.
        """
        from app.models.user import User

        uid = (actor_user_id or "").strip()
        if not uid:
            return ""
        user = self.db.query(User).filter(User.id == uid).first()
        if user:
            return ((user.name or "").strip() or user.email or uid).strip() or ""
        # Not a matching user row - echo back only if it isn't a bare UUID.
        return "" if self._UUID_RE.match(uid) else uid

    def attach_rejection_person(self, header: PurchaseRequestHeader) -> PurchaseRequestHeader:
        """Set ``rejected_by_name`` + ``rejected_by_wa_phone`` on a header for the detail
        DTO (PLAN-form-banner-person-links REJ-3/REJ-4/HIST-3).

        Resolution:
        1. ``rejected_by_id`` (new column) -> User -> name + wa.me digits.
        2. Legacy rejected PRs (no ``rejected_by_id``): fall back to the ``approved_by``
           display-name string, plain text (no phone). Only when the PR is rejected.
        Never leaks a raw UUID; never raises.
        """
        rid = (getattr(header, "rejected_by_id", None) or "").strip() or None
        name = (self._resolve_actor_display_name(rid) or None) if rid else None
        phone = wa_phone_for_user_id(self.db, rid) if rid else None
        if not name:
            status_val = (getattr(header, "approval_status", None) or getattr(header, "status", None) or "").strip().lower()
            if status_val == "rejected":
                legacy = (getattr(header, "approved_by", None) or "").strip()
                # approved_by holds a display-name string for rejected PRs; echo it only
                # when it is not a bare UUID.
                if legacy and not self._UUID_RE.match(legacy):
                    name = legacy
        setattr(header, "rejected_by_name", name)
        setattr(header, "rejected_by_wa_phone", phone)
        return header

    def _resolve_approver_display_name(self, header: PurchaseRequestHeader) -> str:
        """Resolve ``approved_by`` to a human-readable display name.

        ``approved_by`` may hold either a CRM user id (public approval link flow)
        OR the approver's display name (in-system approve route stores the name
        directly). Resolution order - chosen so the outbound message matches the
        "Approved by" shown on the detail page (which renders ``approved_by`` raw):
        1. ``approved_by`` matches a ``User.id`` → that user's name/email.
        2. ``approved_by`` is a non-empty, non-UUID-shaped string → return it
           verbatim (the stored display name, e.g. "CK Lee"). This is the
           in-system case where no user row matches the name string.
        3. ``approver_email`` (public link, no name) is set → the email.
        4. Nothing usable → ``"unknown"`` (never leaks a raw UUID).
        """
        from app.models.user import User

        approver_id = (getattr(header, "approved_by", None) or "").strip()
        if approver_id:
            user = (
                self.db.query(User)
                .filter(User.id == approver_id)
                .first()
            )
            if user:
                return (user.name or user.email or approver_id).strip() or approver_id
        # In-system (and named public-link) approvals store the display name
        # straight into approved_by. Prefer it over the email so the message
        # reads the same as the detail page; never echo a bare UUID.
        if approver_id and not self._UUID_RE.match(approver_id):
            return approver_id
        email = (getattr(header, "approver_email", None) or "").strip()
        if email:
            return email
        return "unknown"

    def _notify_contact_on_approval_rejected(self, header: PurchaseRequestHeader) -> None:
        """Notify the linked Respond.io contact when a public approval flow rejects the request."""
        request_number = (display_document_number(header) or str(header.id)).strip()
        reason = (getattr(header, "approval_comments", None) or "").strip() or "no reason provided"
        approver = self._resolve_approver_display_name(header)
        view_url = self._build_request_view_url(str(header.id))
        # Prefer the interactive submission portal link (minted on the fly) so the
        # contact can act/resubmit; fall back to the read-only view link.
        from app.services.portal_service import PortalService
        view_url = PortalService(self.db).submission_link(
            getattr(header, "contact_id", None),
            getattr(header, "request_type", None) or "purchase_request",
            str(header.id),
        ) or view_url
        rt = getattr(header, "request_type", None) or ""
        if rt == "sponsorship_form":
            message_text = (
                f"Your sponsorship form {request_number} has been rejected due to {reason} by {approver}. "
                f"Please view your submission here {view_url}"
            )
        else:
            message_text = (
                f"Your purchase request {request_number} has been rejected due to {reason} by {approver}. "
                f"Please view your submission here {view_url}"
            )
        # Structured-template vars: LEAN `update` core (status only) + links
        # "Rejected, reason: X" (no preamble, no inline URL). Mirrors complaint.
        reason_core = (getattr(header, "approval_comments", None) or "").strip()
        reject_extra_vars = {
            "update": f"Rejected, reason: {reason_core}" if reason_core else "Rejected",
            "portal_url": self._purchase_request_portal_or_view_url(header, str(header.id)),
            "view_url": (self._build_request_view_url(str(header.id)) or "").strip(),
        }
        self._send_purchase_request_contact_message(
            header, message_text=message_text, extra_context_vars=reject_extra_vars
        )

    def _notify_contact_on_approval_approved(self, header: PurchaseRequestHeader) -> None:
        """Notify the linked Respond.io contact when a public approval flow approves the request."""
        request_number = (display_document_number(header) or str(header.id)).strip()
        approver = self._resolve_approver_display_name(header)
        note = (getattr(header, "approval_comments", None) or "").strip()
        note_part = f" Note: {note}." if note else ""
        view_url = self._build_request_view_url(str(header.id))
        # Prefer the interactive submission portal link (minted on the fly) so the
        # contact can act/resubmit; fall back to the read-only view link.
        from app.services.portal_service import PortalService
        view_url = PortalService(self.db).submission_link(
            getattr(header, "contact_id", None),
            getattr(header, "request_type", None) or "purchase_request",
            str(header.id),
        ) or view_url
        rt = getattr(header, "request_type", None) or ""
        if rt == "sponsorship_form":
            message_text = (
                f"Your sponsorship form {request_number} has been approved by {approver}.{note_part} "
                f"Please view your submission here {view_url}"
            )
        else:
            message_text = (
                f"Your purchase request {request_number} has been approved by {approver}.{note_part} "
                f"Please view your submission here {view_url}"
            )
        # Structured-template vars: LEAN `update` core (status only) + links.
        approve_extra_vars = {
            "update": "Approved",
            "portal_url": self._purchase_request_portal_or_view_url(header, str(header.id)),
            "view_url": (self._build_request_view_url(str(header.id)) or "").strip(),
        }
        self._send_purchase_request_contact_message(
            header, message_text=message_text, extra_context_vars=approve_extra_vars
        )

    # Customer-service finalize: only an approved request (post-approval, CS stage)
    # can be finalized. 'processed_by_cs' (CS handled it) and 'closed' (can't
    # fulfil) both close the customer-service form-SLA stage (same 'resolved' event).
    _CS_FINALIZE_FROM_STATUSES: tuple[str, ...] = ("approved",)
    _CS_FINALIZE_STATUSES: tuple[str, ...] = ("processed_by_cs", "closed")
    _CS_FINALIZE_STATUS_LABELS: dict[str, str] = {
        "processed_by_cs": "processed by our customer service team",
        "closed": "closed",
    }

    def mark_processed_by_cs(
        self,
        request_id: str,
        *,
        note: Optional[str] = None,
        respond_user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
    ):
        """Mark an approved request as processed by customer service (closes the CS stage)."""
        return self._finalize_request(
            request_id,
            "processed_by_cs",
            note=note,
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
        )

    def close_request(
        self,
        request_id: str,
        *,
        note: Optional[str] = None,
        respond_user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
    ):
        """Close an approved request that can't be fulfilled (status='closed'; closes the CS stage)."""
        return self._finalize_request(
            request_id,
            "closed",
            note=note,
            respond_user_id=respond_user_id,
            crm_sender_user_id=crm_sender_user_id,
        )

    def _finalize_request(
        self,
        request_id: str,
        new_status: str,
        *,
        note: Optional[str] = None,
        respond_user_id: Optional[str] = None,
        crm_sender_user_id: Optional[str] = None,
    ):
        """Finalize an approved purchase request / sponsorship form to ``new_status``.

        Mirrors complaint CS handoff (``ComplaintService._finalize_complaint``):
          (a) set lifecycle status            -> committed (primary)
          (b) send a Respond.io status-update message to the contact (best-effort,
              window-aware via ``send_text_or_template``)
          (c) emit the 'resolved' form-SLA event (closes the customer-service stage)
        (b) and (c) never fail the operation; the committed status is source of truth.
        """
        from app.services.error_handler import handle_validation_error

        if new_status not in self._CS_FINALIZE_STATUSES:
            raise handle_validation_error(f"Unsupported finalize status: {new_status!r}.")

        header = self.get_request(request_id)
        current_status = (getattr(header, "status", None) or "").strip().lower()
        if current_status == new_status:
            return header  # idempotent
        if current_status not in self._CS_FINALIZE_FROM_STATUSES:
            raise handle_validation_error(
                f"Request must be approved before it can be marked {new_status} "
                f"(current status: {current_status or 'unknown'})."
            )

        request_number = (display_document_number(header) or str(header.id)).strip()
        rt = getattr(header, "request_type", None) or "purchase_request"
        type_word = "sponsorship form" if rt == "sponsorship_form" else "purchase request"
        status_label = self._CS_FINALIZE_STATUS_LABELS.get(new_status, new_status)
        view_url = self._build_request_view_url(str(header.id))
        # Prefer the interactive submission portal link (minted on the fly) so the
        # contact can act/resubmit; fall back to the read-only view link.
        from app.services.portal_service import PortalService
        view_url = PortalService(self.db).submission_link(
            getattr(header, "contact_id", None),
            getattr(header, "request_type", None) or "purchase_request",
            str(header.id),
        ) or view_url
        note_clean = (note or "").strip()
        note_part = f" Note: {note_clean}." if note_clean else ""
        message_text = (
            f"There has been an update regarding your {type_word} {request_number}: "
            f"status changed to {status_label}.{note_part} "
            f"Please view your submission here {view_url}"
        )

        # (a) Commit the status (primary, synchronous).
        header.status = new_status
        self.db.commit()
        self.db.refresh(header)

        # Structured-template vars: LEAN `update` core (status only) + links
        # "Processed by CS" / "Closed" (no preamble, no inline URL).
        finalize_update_core = "Processed by CS" if new_status == "processed_by_cs" else "Closed"
        finalize_extra_vars = {
            "update": finalize_update_core,
            "portal_url": self._purchase_request_portal_or_view_url(header, str(header.id)),
            "view_url": (self._build_request_view_url(str(header.id)) or "").strip(),
        }

        # (b) Send the status-update message to the contact (best-effort, decoupled).
        try:
            self._send_purchase_request_contact_message(
                header,
                message_text=message_text,
                crm_sender_user_id=crm_sender_user_id,
                respond_user_id_fallback=respond_user_id,
                extra_context_vars=finalize_extra_vars,
            )
        except Exception:
            logger.exception(
                "Request %s finalize=%s: Respond.io message failed; status committed.",
                request_id,
                new_status,
            )

        # (c) Close the customer-service form-SLA stage (best-effort).
        try:
            from app.services.form_sla_service import emit_form_event

            emit_form_event(
                self.db,
                rt,
                str(header.id),
                "resolved",
                contact_id=getattr(header, "contact_id", None),
                actor_user_id=crm_sender_user_id or respond_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'resolved' failed for request %s: %s", header.id, e)

        return header

    # ----- Void (terminal, irreversible) -----
    # Blocked once terminal. PR uses status + approval_status: a rejected request
    # sets BOTH status='rejected' and approval_status='rejected' (reject_submitted /
    # decide_approval), so either signal blocks a second void.
    _VOID_BLOCKED_STATUSES: frozenset[str] = frozenset(
        {"voided", "rejected", "closed", "processed_by_cs"}
    )

    def void_request(
        self,
        request_id: str,
        *,
        void_reason: str,
        actor_user_id: Optional[str] = None,
        respond_user_id: Optional[str] = None,
    ):
        """Void a purchase request / sponsorship form (irreversible).

        Requires a free-text reason (>= 3 chars), allowed only from a non-terminal
        state. Sets status='voided' + the reason quad, emits the 'voided' form-SLA
        event (SLA stop is pure config), then best-effort notifies assignee +
        handling-lock holder (in-app) and the salesperson (WhatsApp).
        """
        reason = (void_reason or "").strip()
        if len(reason) < 3:
            raise handle_validation_error(
                "A void reason of at least 3 characters is required."
            )
        header = self.get_request(request_id)
        current = (getattr(header, "status", None) or "").strip().lower()
        approval = (getattr(header, "approval_status", None) or "").strip().lower()
        if current in self._VOID_BLOCKED_STATUSES or approval == "rejected":
            raise handle_conflict(
                f"This request cannot be voided from its current state "
                f"({current or 'unknown'})."
            )

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        header.status = "voided"
        header.voided_by = actor_user_id
        header.voided_at = now_utc
        header.void_reason = reason
        self.db.commit()
        self.db.refresh(header)

        rt = getattr(header, "request_type", None) or "purchase_request"

        # Best-effort notify (never rolls back the committed void). MUST run BEFORE
        # emit_form_event: when the config lists 'voided' in resolve_event, emit
        # resolves the active tracker, and the in-app notify (which reads the
        # NOT-yet-resolved tracker for the assignee/handler) would then find nothing.
        self._notify_request_voided(
            header, actor_user_id=actor_user_id, respond_user_id=respond_user_id
        )

        # Emit the 'voided' form-SLA event. NO direct tracker-stop code: whether the
        # tracker closes is pure config (form_sla_config.resolve_event lists 'voided').
        try:
            from app.services.form_sla_service import emit_form_event

            emit_form_event(
                self.db,
                rt,
                str(header.id),
                "voided",
                contact_id=getattr(header, "contact_id", None),
                actor_user_id=actor_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'voided' failed for request %s: %s", header.id, e)

        return header

    def _notify_request_voided(
        self,
        header: PurchaseRequestHeader,
        *,
        actor_user_id: Optional[str],
        respond_user_id: Optional[str],
    ) -> None:
        """Best-effort void notifications (NTF): assignee + handler in-app, salesperson WhatsApp."""
        rt = getattr(header, "request_type", None) or "purchase_request"
        try:
            from app.services.form_void_notify import notify_form_voided_in_app

            notify_form_voided_in_app(
                self.db,
                source_entity_type=rt,
                source_entity_id=str(header.id),
                entity_number=display_document_number(header) or None,
                actor_user_id=actor_user_id,
            )
        except Exception:
            logger.warning("Void in-app notify failed for request %s", header.id, exc_info=True)

        # Salesperson WhatsApp via the existing status-update choke point
        # (send_text_or_template + integration_log on success AND failure).
        try:
            type_word = "sponsorship form" if rt == "sponsorship_form" else "purchase request"
            number = (display_document_number(header) or str(header.id)).strip()
            reason = (getattr(header, "void_reason", None) or "").strip()
            message_text = (
                f"Your {type_word} {number} has been voided. "
                f"Reason: {reason}" if reason else f"Your {type_word} {number} has been voided."
            )
            self._send_purchase_request_contact_message(
                header,
                message_text=message_text,
                crm_sender_user_id=actor_user_id,
                respond_user_id_fallback=respond_user_id,
                extra_context_vars={"update": "Voided", "message": message_text},
            )
        except Exception:
            logger.warning(
                "Void salesperson WhatsApp send failed for request %s; void committed.",
                header.id,
                exc_info=True,
            )

    def request_revision_by_token(self, token_value: str) -> dict[str, str]:
        """Trigger the external revise webhook for a rejected purchase request / sponsorship form public view."""
        view_token = (
            self.db.query(ViewToken)
            .filter(ViewToken.token == token_value, ViewToken.entity_type == "purchase_request")
            .first()
        )
        if not view_token or not view_token.entity_id:
            raise handle_not_found("View link", "(invalid token)")

        header = self.get_request(str(view_token.entity_id))
        if getattr(header, "approval_status", None) != "rejected":
            raise handle_conflict("Revise is only available for rejected requests.")

        self._enqueue_public_revise_webhook_for_purchase_request(header)
        rt = getattr(header, "request_type", None) or ""
        if rt == "sponsorship_form":
            return {
                "message": (
                    "Revise request sent successfully. You can open WhatsApp to continue editing the sponsorship form."
                )
            }
        return {
            "message": (
                "Revise request sent successfully. You can open WhatsApp to continue editing the purchase request."
            )
        }

    def _enqueue_public_revise_webhook_for_purchase_request(self, header: PurchaseRequestHeader) -> None:
        """Send an incoming-style webhook payload for a rejected request revise (same n8n URL as stock inquiry)."""
        import threading
        import time

        from app.models.access import RespondContact
        from app.schemas.integration import IntegrationLogCreate
        from app.services.error_handler import handle_validation_error
        from app.services.integration_service import IntegrationLogService
        from app.services.n8n_webhook_settings import get_n8n_stock_inquiry_revise_webhook_url

        webhook_url = get_n8n_stock_inquiry_revise_webhook_url(self.db)
        if not webhook_url:
            raise handle_validation_error("Revise webhook is not configured.")

        contact_key = (
            (getattr(header, "contact_id", None) or "").strip()
            or (
                self._identifier_from_respond_inbox_url(getattr(header, "respond_inbox_url", None)) or ""
            ).strip()
        )
        if not contact_key:
            raise handle_validation_error("This request is not linked to a contact.")

        contact = (
            self.db.query(RespondContact)
            .filter(
                or_(
                    RespondContact.respond_io_id == contact_key,
                    RespondContact.id == contact_key,
                )
            )
            .first()
        )
        contact_respond_io_id = (getattr(contact, "respond_io_id", None) or "").strip() or contact_key
        contact_phone = _resolve_contact_phone_for_webhook(contact, contact_respond_io_id)
        contact_id_value: Any
        try:
            contact_id_value = int(str(contact_respond_io_id).strip())
        except (TypeError, ValueError):
            contact_id_value = contact_respond_io_id

        first_name = ((getattr(contact, "first_name", None) or "").strip() or None)
        last_name = ((getattr(contact, "last_name", None) or "").strip() or None)
        if not (first_name or last_name):
            raw_name = (getattr(contact, "name", None) or "").strip()
            if raw_name:
                parts = raw_name.split(None, 1)
                first_name = parts[0] if parts else None
                last_name = parts[1] if len(parts) > 1 else None

        request_number = (display_document_number(header) or str(header.id)).strip()
        rt = getattr(header, "request_type", None) or ""
        if rt == "sponsorship_form":
            revise_text = f"I want to edit sponsorship form for {request_number}"
        else:
            revise_text = f"I want to edit purchase request for {request_number}"

        now_ms = int(time.time() * 1000)
        now_s = int(time.time())
        payload = [
            {
                "contact": {
                    "id": contact_id_value,
                    "phone": contact_phone,
                    "firstName": first_name or "",
                    "lastName": last_name or "",
                    "role": "user",
                    "created_at": now_s,
                },
                "message": {
                    "messageId": now_ms * 1000,
                    "channelMessageId": None,
                    "contactId": contact_id_value,
                    "channelId": None,
                    "traffic": "incoming",
                    "timestamp": now_ms,
                    "message": {
                        "type": "text",
                        "text": revise_text,
                    },
                },
                "channel": {
                    "id": None,
                    "name": "Whatsapp Business",
                    "source": "whatsapp_business",
                    "meta": None,
                    "created_at": now_s,
                },
                "sender": {
                    "source": "contact",
                    "userId": None,
                    "teamId": None,
                    "workflowId": None,
                    "broadcastHistoryId": None,
                },
                "source": "Contact",
                "crm": {
                    "business_table": "purchase_requests",
                    "business_id": str(header.id),
                    "space_id": getattr(header, "space_id", None),
                },
            }
        ]

        log_service = IntegrationLogService(self.db)
        integration_log = log_service.create_integration_log(
            IntegrationLogCreate(
                integration_channel="n8n_purchase_request_revise",
                business_table="purchase_requests",
                business_id=str(header.id),
                external_reference=str(contact_respond_io_id),
                direction="outbound",
                endpoint=webhook_url,
                http_method="POST",
                status="pending",
            ),
            request_payload_dict=payload,
        )

        log_id = str(integration_log.id)

        def send_async() -> None:
            try:
                from app.database import SessionLocal

                bg_db = SessionLocal()
                try:
                    bg_service = IntegrationLogService(bg_db)
                    bg_service.send_webhook_for_log(log_id)
                finally:
                    bg_db.close()
            except Exception as e:
                logger.error(
                    "Purchase request revise webhook failed for log %s: %s",
                    log_id,
                    e,
                    exc_info=True,
                )

        threading.Thread(target=send_async, daemon=True).start()

    def _parse_date(self, value: Optional[str | date | datetime]) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
        return None

    def _snapshot_pr_lines_json(self, lines: List[Any]) -> str:
        """Stable JSON for comparing line sets (ORM lines)."""
        rows: List[dict] = []
        ordered = sorted(
            lines,
            key=lambda l: (
                l.sort_order is None,
                l.sort_order if l.sort_order is not None else 0,
                str(getattr(l, "id", "")),
            ),
        )
        for l in ordered:
            rows.append(
                {
                    "item_code": l.item_code,
                    "quantity": str(l.quantity) if l.quantity is not None else None,
                    "remark": l.remark,
                    "unit_price": str(l.unit_price) if l.unit_price is not None else None,
                    "total": str(l.total) if l.total is not None else None,
                }
            )
        return json.dumps(rows, ensure_ascii=True)

    def _snapshot_pr_lines_json_from_products(self, products: List[Any]) -> str:
        """Stable JSON from external payload line objects."""
        rows: List[dict] = []
        for i, line_data in enumerate(products or []):
            q = getattr(line_data, "quantity", None)
            up = getattr(line_data, "unit_price", None)
            tot = getattr(line_data, "total", None)
            rows.append(
                {
                    "sort_order": i,
                    "item_code": getattr(line_data, "item_code", None),
                    "quantity": str(q) if q is not None else None,
                    "remark": getattr(line_data, "remark", None),
                    "unit_price": str(up) if up is not None else None,
                    "total": str(tot) if tot is not None else None,
                }
            )
        return json.dumps(rows, ensure_ascii=True)

    _PR_REQUIRED_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
        "purchase_request": (
            "customer_name",
            "project_title",
            "purpose",
            "expected_delivery_date",
            "requested_by",
            "contact_id",
            "space_id",
            "products",
            "sales_type",  # project / cash_sales - required for PR (SF omits it); routes CS
        ),
        "sponsorship_form": (
            "sponsor_subject",
            "customer_name",
            "date_of_delivery",
            "requested_by",
            "contact_id",
            "space_id",
            "products",
        ),
    }

    def _missing_external_request_fields(self, payload, existing: Optional[PurchaseRequestHeader] = None) -> list[str]:
        request_type = str(getattr(payload, "request_type", "") or "").strip()
        required = self._PR_REQUIRED_FIELDS_BY_TYPE.get(request_type, ())
        missing: list[str] = []

        for key in required:
            if key == "expected_delivery_date":
                # Accept either expected_delivery_date or date_of_delivery.
                val = self._parse_date(getattr(payload, "expected_delivery_date", None))
                if val is None:
                    val = self._parse_date(getattr(payload, "date_of_delivery", None))
                if val is None and existing is not None:
                    val = getattr(existing, "expected_delivery_date", None)
                if val is None:
                    missing.append("expected_delivery_date")
                continue

            if key == "date_of_delivery":
                val = self._parse_date(getattr(payload, "date_of_delivery", None))
                if val is None:
                    val = self._parse_date(getattr(payload, "expected_delivery_date", None))
                if val is None and existing is not None:
                    val = getattr(existing, "expected_delivery_date", None)
                if val is None:
                    missing.append("date_of_delivery")
                continue

            if key == "products":
                rows = list(getattr(payload, "products", None) or [])
                if not rows and existing is not None:
                    rows = list(getattr(existing, "lines", None) or [])
                if not rows:
                    missing.append("products")
                    continue
                bad_rows: list[str] = []
                for idx, row in enumerate(rows, start=1):
                    item_code = getattr(row, "item_code", None)
                    quantity = getattr(row, "quantity", None)
                    if item_code is None or (isinstance(item_code, str) and not item_code.strip()) or quantity is None:
                        bad_rows.append(str(idx))
                if bad_rows:
                    missing.append(f"products(item_code,quantity) rows: {', '.join(bad_rows)}")
                continue

            val = getattr(payload, key, None)
            if (val is None or (isinstance(val, str) and not val.strip())) and existing is not None:
                val = getattr(existing, key, None)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(key)
        return missing

    def _require_complete_external_request_submission(
        self, payload, existing: Optional[PurchaseRequestHeader] = None
    ) -> None:
        request_type = str(getattr(payload, "request_type", "") or "").strip()
        required = self._PR_REQUIRED_FIELDS_BY_TYPE.get(request_type, ())
        if not required:
            return
        missing = self._missing_external_request_fields(payload, existing=existing)
        if missing:
            raise handle_validation_error(
                f"{request_type} submission is incomplete. Required fields: "
                + ", ".join(required)
                + f". Missing or empty: {', '.join(missing)}."
            )

    def upsert_external_request(self, payload):
        """
        Create or update from external payload.

        If ``request_number`` is non-empty after strip, look up that number; on match, update header
        and replace lines (audited). If no row exists, create a new record (uses payload number or generated).

        Returns ``(header, "created" | "updated")``.
        """
        rn_raw = getattr(payload, "request_number", None)
        if rn_raw is None:
            lookup = ""
        else:
            # Tolerate a revision suffix (UAC N6) - see create_inquiry for why a
            # miss here duplicates the record instead of failing loudly.
            lookup = (strip_revision_suffix(str(rn_raw)) or "").strip()
        if lookup:
            existing = (
                self.db.query(PurchaseRequestHeader)
                .filter(PurchaseRequestHeader.request_number == lookup)
                .first()
            )
            if existing is not None:
                if (str(getattr(existing, "approval_status", "") or "").strip().lower()) != "rejected":
                    raise handle_validation_error(
                        f"Request number {lookup!r} already exists with approval_status "
                        f"{getattr(existing, 'approval_status', None)!r}. "
                        "Use request_number only to resubmit a rejected request."
                    )
                self._require_complete_external_request_submission(payload, existing=existing)
                if getattr(payload, "user_confirmed", None) is not True:
                    raise handle_validation_error(
                        "Explicit user confirmation is required before submission. "
                        "Set user_confirmed=true only after the user explicitly confirms the final summary "
                        "(e.g. OK, YES, CONFIRM)."
                    )
                return self._update_external_request(existing, payload), "updated"
        self._require_complete_external_request_submission(payload, existing=None)
        if getattr(payload, "user_confirmed", None) is not True:
            raise handle_validation_error(
                "Explicit user confirmation is required before submission. "
                "Set user_confirmed=true only after the user explicitly confirms the final summary "
                "(e.g. OK, YES, CONFIRM)."
            )
        return self.create_external_request(payload), "created"

    # Lookup set bound to purchase_requests.sponsor_subject (see migration 243).
    _SPONSOR_SUBJECT_SET_KEY = "procurement_sponsor_subject"

    def _normalize_sponsor_subject(
        self, request_type: Optional[str], raw_subject: Optional[str]
    ) -> tuple[Optional[str], Optional[str]]:
        """Resolve an incoming ``sponsor_subject`` against the lookup set.

        Returns ``(sponsor_subject, sponsor_subject_other)``:
      - Non-sponsorship requests pass the raw value through untouched (PR never
          binds sponsor_subject), other stays None.
      - Empty/blank → ``(None, None)``.
      - Resolves through the lookup resolver; a match yields the canonical
          option value with no other text.
      - Unmatched free text (e.g. n8n submissions) → ``("others", <raw text>)``
          so the strict lookup write-validator never 422s the intake.
        """
        if (request_type or "").strip() != "sponsorship_form":
            return raw_subject, None
        raw = (raw_subject or "").strip()
        if not raw:
            return None, None
        from app.services.lookup_resolver import LookupResolverService
        from app.services.error_handler import AppException

        try:
            resolved = LookupResolverService(self.db).resolve(
                self._SPONSOR_SUBJECT_SET_KEY, raw
            )
            value = (resolved.value or "").strip()
            if value == "others":
                return "others", raw
            return value or "others", None
        except AppException:
            # Unresolved (or set missing) → park raw text under 'others'.
            return "others", raw

    def _apply_sponsor_subject_to_payload(
        self, payload: dict, header: PurchaseRequestHeader
    ) -> None:
        """In-place normalize ``sponsor_subject`` on an exclude_unset update payload.

        Only acts when ``sponsor_subject`` was actually supplied. Resolves it for
        sponsorship rows and derives ``sponsor_subject_other`` (preserving an
        explicitly supplied other detail). ``request_type`` falls back to the
        existing header when not part of the update.
        """
        if "sponsor_subject" not in payload:
            return
        request_type = payload.get("request_type") or getattr(header, "request_type", None)
        norm_subject, norm_other = self._normalize_sponsor_subject(
            request_type, payload.get("sponsor_subject")
        )
        payload["sponsor_subject"] = norm_subject
        if "sponsor_subject_other" in payload:
            explicit_other = (payload.get("sponsor_subject_other") or "").strip() or None
            payload["sponsor_subject_other"] = explicit_other or norm_other
        else:
            payload["sponsor_subject_other"] = norm_other

    def create_external_request(self, payload):
        """Create purchase request header + lines from external payload."""
        expected_po_date_text = None
        if isinstance(payload.expected_po_date, str):
            expected_po_date_text = payload.expected_po_date.strip() or None

        # Resolve expected_delivery_date: use date_of_delivery when expected_delivery_date is empty
        expected_delivery_date = self._parse_date(getattr(payload, "expected_delivery_date", None))
        if expected_delivery_date is None:
            expected_delivery_date = self._parse_date(getattr(payload, "date_of_delivery", None))

        # Strict numeric (shared guard rail with portal): non-numeric / too-large
        # are rejected. Descriptive text is deprecated; new rows store numeric only.
        total_project_value = validate_project_value(
            getattr(payload, "total_project_value", None)
        )
        total_project_value_text = None

        contact_id = getattr(payload, "contact_id", None) or None
        space_id = getattr(payload, "space_id", None) or None
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)

        # Generate request_number if not provided (strip to match upsert lookup).
        # A revision suffix comes off here too (UAC N2): the STORED number is always
        # bare, so a caller that echoed back "PR-26-0012-R2" for a number we do not
        # have does not mint a new row whose number carries a revision it never had.
        request_number = getattr(payload, "request_number", None) or None
        if isinstance(request_number, str):
            request_number = (strip_revision_suffix(request_number) or "").strip() or None
        if not request_number:
            from app.services.numbering_service import NumberingService
            ref_date = self._parse_date(getattr(payload, "date", None)) or date.today()
            request_number = NumberingService(self.db).get_next_number(payload.request_type, ref_date)

        initial_approval = None
        if "approval_status" in payload.model_fields_set:
            initial_approval = getattr(payload, "approval_status", None)

        sponsor_subject, sponsor_subject_other = self._normalize_sponsor_subject(
            getattr(payload, "request_type", None),
            getattr(payload, "sponsor_subject", None),
        )
        # An explicitly supplied 'others' detail wins over the parked raw text.
        explicit_other = getattr(payload, "sponsor_subject_other", None)
        if explicit_other is not None and (explicit_other or "").strip():
            sponsor_subject_other = explicit_other.strip()

        header = PurchaseRequestHeader(
            request_type=payload.request_type,
            request_number=request_number,
            request_date=self._parse_date(payload.date),
            customer_name=payload.customer_name,
            pic=getattr(payload, "pic", None),
            project_title=payload.project_title,
            purpose=payload.purpose,
            delivery_address=getattr(payload, "delivery_address", None),
            total_project_value=total_project_value,
            total_project_value_text=total_project_value_text,
            sponsor_subject=sponsor_subject,
            sponsor_subject_other=sponsor_subject_other,
            expected_delivery_date=expected_delivery_date,
            expected_po_date=self._parse_date(payload.expected_po_date),
            expected_po_date_text=expected_po_date_text,
            requested_by=payload.requested_by,
            requested_at=self._parse_date(payload.requested_at),
            external_reference=payload.external_reference,
            contact_id=contact_id,
            space_id=space_id,
            respond_inbox_url=respond_inbox_url,
            approval_status=initial_approval,
            status="draft",
            source="external",
        )
        self.db.add(header)
        self.db.flush()

        if payload.products:
            for index, line_data in enumerate(payload.products):
                line = PurchaseRequestLine(
                    purchase_request_id=header.id,
                    item_code=line_data.item_code,
                    quantity=line_data.quantity,
                    remark=line_data.remark,
                    unit_price=getattr(line_data, "unit_price", None),
                    total=getattr(line_data, "total", None),
                    sort_order=index,
                )
                self.db.add(line)

        self.db.commit()
        self.db.refresh(header)
        # Capture header fields before any further commits (session may expire objects)
        header_id = str(header.id)
        header_request_type = getattr(header, "request_type", None)
        header_request_number = display_document_number(header) or "N/A"
        header_project_title = getattr(header, "project_title", None) or "N/A"
        try:
            self.get_or_create_view_token(header_id)
            self.db.commit()
        except Exception:
            pass
        try:
            base_url_override = getattr(payload, "base_url", None) if payload else None
            self._notify_team_on_external_pr_created(
                header_id=header_id,
                request_type=header_request_type,
                request_number=header_request_number,
                project_title=header_project_title,
                base_url_override=base_url_override,
                integration_action="created",
            )
        except Exception as e:
            logger.warning(
                "Failed to notify team for external purchase request %s: %s",
                header_id,
                e,
                exc_info=True,
            )
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                str(header_request_type) if header_request_type else "purchase_request",
                header_id,
                "submit",
                contact_id=contact_id,
                actor_user_id=None,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'submit' failed for %s %s: %s", header_request_type, header_id, e)
        return header

    def _update_external_request(self, header: PurchaseRequestHeader, payload) -> PurchaseRequestHeader:
        """Apply external payload to an existing header; replace lines; audit line changes explicitly."""
        from app.services.audit_service import log_audit
        from app.audit_context import get_audit_context

        row = (
            self.db.query(PurchaseRequestHeader)
            .options(joinedload(PurchaseRequestHeader.lines))
            .filter(PurchaseRequestHeader.id == header.id)
            .first()
        )
        if row is None:
            raise handle_not_found("Request", str(header.id))

        old_line_blob = self._snapshot_pr_lines_json(list(row.lines or []))
        new_line_blob = self._snapshot_pr_lines_json_from_products(getattr(payload, "products", None) or [])

        expected_po_date_text = None
        if isinstance(payload.expected_po_date, str):
            expected_po_date_text = payload.expected_po_date.strip() or None

        expected_delivery_date = self._parse_date(getattr(payload, "expected_delivery_date", None))
        if expected_delivery_date is None:
            expected_delivery_date = self._parse_date(getattr(payload, "date_of_delivery", None))

        # Strict numeric (shared guard rail with portal). Legacy total_project_value_text
        # on existing rows is preserved (not written from this strict-numeric path).
        total_project_value = validate_project_value(
            getattr(payload, "total_project_value", None)
        )

        contact_id = getattr(payload, "contact_id", None) or None
        space_id = getattr(payload, "space_id", None) or None
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)

        row.request_type = payload.request_type
        row.request_date = self._parse_date(payload.date)
        row.customer_name = payload.customer_name
        row.pic = getattr(payload, "pic", None)
        row.project_title = payload.project_title
        row.purpose = payload.purpose
        row.delivery_address = getattr(payload, "delivery_address", None)
        row.total_project_value = total_project_value
        ext_sponsor_subject, ext_sponsor_subject_other = self._normalize_sponsor_subject(
            getattr(payload, "request_type", None),
            getattr(payload, "sponsor_subject", None),
        )
        ext_explicit_other = getattr(payload, "sponsor_subject_other", None)
        if ext_explicit_other is not None and (ext_explicit_other or "").strip():
            ext_sponsor_subject_other = ext_explicit_other.strip()
        row.sponsor_subject = ext_sponsor_subject
        row.sponsor_subject_other = ext_sponsor_subject_other
        row.expected_delivery_date = expected_delivery_date
        row.expected_po_date = self._parse_date(payload.expected_po_date)
        row.expected_po_date_text = expected_po_date_text
        row.requested_by = payload.requested_by
        row.requested_at = self._parse_date(payload.requested_at)
        row.external_reference = payload.external_reference
        row.contact_id = contact_id
        row.space_id = space_id
        if respond_inbox_url is not None:
            row.respond_inbox_url = respond_inbox_url
        if "approval_status" in payload.model_fields_set:
            row.approval_status = getattr(payload, "approval_status", None)

        for line in list(row.lines or []):
            self.db.delete(line)
        self.db.flush()

        if payload.products:
            for index, line_data in enumerate(payload.products):
                line = PurchaseRequestLine(
                    purchase_request_id=row.id,
                    item_code=line_data.item_code,
                    quantity=line_data.quantity,
                    remark=line_data.remark,
                    unit_price=getattr(line_data, "unit_price", None),
                    total=getattr(line_data, "total", None),
                    sort_order=index,
                )
                self.db.add(line)

        if old_line_blob != new_line_blob:
            user_id, ip_address = get_audit_context()
            log_audit(
                self.db,
                "purchase_request",
                str(row.id),
                "UPDATE",
                old_values={"line_items": old_line_blob},
                new_values={"line_items": new_line_blob},
                user_id=user_id,
                ip_address=ip_address,
                skip_flush=True,
            )

        self.db.commit()
        self.db.refresh(row)

        header_id = str(row.id)
        header_request_type = getattr(row, "request_type", None)
        header_request_number = display_document_number(row) or "N/A"
        header_project_title = getattr(row, "project_title", None) or "N/A"
        try:
            self.get_or_create_view_token(header_id)
            self.db.commit()
        except Exception:
            pass
        try:
            base_url_override = getattr(payload, "base_url", None) if payload else None
            self._notify_team_on_external_pr_created(
                header_id=header_id,
                request_type=header_request_type,
                request_number=header_request_number,
                project_title=header_project_title,
                base_url_override=base_url_override,
                integration_action="updated",
            )
        except Exception as e:
            logger.warning(
                "Failed to notify team for external purchase request update %s: %s",
                header_id,
                e,
                exc_info=True,
            )
        return row

    def _get_purchase_request_project_sales_tier1_user_ids(
        self, *, company_id: Optional[str] = None
    ) -> List[str]:
        """Members of Tier 1 under team set code project_sales for access agent purchase_request.

        ``company_id`` defaults to Sorento: some callers are generic team lookups with
        no purchase request in hand. Where the PR is known, pass its contact's company.
        """
        from app.services.user_service import AccessAgentService
        from app.models.access import TeamMember

        from app.services.company_routing_service import DEFAULT_COMPANY_ID

        company_id = str(company_id or DEFAULT_COMPANY_ID)
        agent_svc = AccessAgentService(self.db)
        agent_id = agent_svc.get_agent_id_by_code("purchase_request")
        if not agent_id:
            logger.debug("No access agent found for code=purchase_request")
            return []
        team_id = agent_svc.get_team_id_by_tier(agent_id, 1, team_set_code="project_sales", company_id=company_id)
        if not team_id:
            team_id = agent_svc.get_team_id_by_code(agent_id, "project_sales", company_id=company_id)
        if not team_id:
            try:
                team_id = agent_svc.get_team_id_by_tier(agent_id, 1, company_id=company_id)
            except HTTPException:
                logger.warning(
                    "Tier 1 for agent 'purchase_request' is ambiguous (multiple team sets). "
                    "Assign team set code 'project_sales' on Tier 1 (Project Sales Executive) in Team Assignments."
                )
                return []
        if not team_id:
            return []
        rows = self.db.query(TeamMember.user_id).filter(TeamMember.team_id == team_id).all()
        return [str(r[0]) for r in rows if r and r[0]]

    def _notify_team_on_external_pr_created(
        self,
        header_id: str,
        request_type: Optional[str] = None,
        request_number: str = "N/A",
        project_title: str = "N/A",
        base_url_override: Optional[str] = None,
        *,
        integration_action: str = "created",
        sync_email: bool = False,
    ) -> None:
        """Notify Tier 1 project_sales under agent purchase_request: one email to all, plus in-app for each."""
        from app.models.user import User, SystemSetting
        from app.models.notification import Notification, NotificationDelivery
        from app.services.notification_service import NotificationService
        from datetime import datetime

        user_ids = self._get_purchase_request_project_sales_tier1_user_ids()
        if not user_ids:
            logger.warning(
                "No team members found for agent 'purchase_request', Tier 1, team set code 'project_sales'. "
                "Create an Access Agent with code 'purchase_request' and assign Project Sales Executive at Tier 1 under code 'project_sales'."
            )
            return
        users = self.db.query(User).filter(User.id.in_(user_ids)).all()
        emails = [u.email for u in users if getattr(u, "email", None) and str(u.email).strip()]
        if not emails:
            logger.warning(
                "Project Sales (Tier 1) team members have no email addresses; skipping email delivery row."
            )
        rt = (request_type or "").strip()
        updated = integration_action == "updated"
        # Wording aligned with external stock inquiry: "New … created" vs "… updated (integration …)".
        if rt == "purchase_request":
            title = (
                "Purchase request updated"
                if updated
                else "New purchase request created"
            )
            kind_sentence = (
                "A purchase request has been updated and may need your review."
                if updated
                else "A new purchase request has been created and requires your review."
            )
        elif rt == "sponsorship_form":
            title = (
                "Sponsorship form updated"
                if updated
                else "New sponsorship form created"
            )
            kind_sentence = (
                "A sponsorship form has been updated and may need your review."
                if updated
                else "A new sponsorship form has been created and requires your review."
            )
        else:
            title = "Request updated (integration)" if updated else "New request created"
            kind_sentence = (
                "A request has been updated and may need your review."
                if updated
                else "A new request has been created and requires your review."
            )
        base_url = (base_url_override or "").strip().rstrip("/")
        if not base_url:
            base_url = (settings.frontend_base_url or "").strip().rstrip("/")
        if not base_url:
            sys_settings = self.db.query(SystemSetting).first()
            if sys_settings and getattr(sys_settings, "website_url", None):
                base_url = (sys_settings.website_url or "").strip().rstrip("/")
        if not base_url:
            logger.warning(
                "No app domain for notification email link. Set Website URL in User Management > Settings (General), "
                "or FRONTEND_BASE_URL in backend .env, or pass base_url in the external create payload."
            )
        # Staff team notification → in-system detail link (login-required), not the
        # public /view token page. The recipient is the internal Project Sales team.
        detail_path = (
            f"/procurement-management/sponsorship-forms/{header_id}"
            if rt == "sponsorship_form"
            else f"/procurement-management/purchase-requests/{header_id}"
        )
        view_url = f"{base_url}{detail_path}" if base_url else detail_path

        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(self.db).render_code(
            "purchase_request_submitted",
            {
                "heading": title,
                "intro": kind_sentence,
                "purchase_request": {"request_number": request_number, "project_title": project_title},
                "view_url": view_url,
            },
        )
        title = rendered["subject"]
        body_plain = rendered["body_text"]
        body_html = rendered["body_html"]
        event_type = "external_updated" if updated else "external_created"
        notif_type = "purchase_request_updated" if updated else "purchase_request_created"
        notif_svc = NotificationService(self.db)
        first_uid = user_ids[0]
        now = datetime.utcnow()
        email_data = {"recipient_emails": emails, "single_email_to_all": True, "body_html": body_html}

        def _enqueue_pr_notification_deliveries(notification_id: str) -> None:
            try:
                if sync_email:
                    from app.tasks import notification_tasks

                    notification_tasks.send_notification_deliveries(notification_id)
                else:
                    from app.services.queue_service import enqueue_job
                    from app.tasks import notification_tasks

                    enqueue_job(
                        notification_tasks.send_notification_deliveries,
                        notification_id,
                        queue_name="notifications",
                    )
            except Exception as e:
                logger.warning("Failed to send/enqueue notification deliveries: %s", e)

        if emails:
            existing_main = (
                self.db.query(Notification)
                .filter(
                    Notification.user_id == first_uid,
                    Notification.source_entity_type == "purchase_request",
                    Notification.source_entity_id == header_id,
                    Notification.event_type == event_type,
                )
                .first()
            )
            if existing_main:
                existing_main.title = title
                existing_main.body = body_plain
                existing_main.type = notif_type
                merged = dict(existing_main.data or {})
                merged.update(email_data)
                existing_main.data = merged
                self.db.add(
                    NotificationDelivery(
                        notification_id=existing_main.id,
                        channel="email",
                        status="pending",
                    )
                )
                self.db.commit()
                self.db.refresh(existing_main)
                _enqueue_pr_notification_deliveries(str(existing_main.id))
            else:
                notification = Notification(
                    user_id=first_uid,
                    type=notif_type,
                    title=title,
                    body=body_plain,
                    data=email_data,
                    source_entity_type="purchase_request",
                    source_entity_id=header_id,
                    event_type=event_type,
                )
                self.db.add(notification)
                self.db.flush()
                self.db.add(
                    NotificationDelivery(
                        notification_id=notification.id,
                        channel="in_app",
                        status="sent",
                        sent_at=now,
                    )
                )
                self.db.add(NotificationDelivery(notification_id=notification.id, channel="email", status="pending"))
                self.db.commit()
                self.db.refresh(notification)
                _enqueue_pr_notification_deliveries(str(notification.id))
        for uid in user_ids:
            if uid == first_uid and emails:
                continue
            try:
                existing_u = (
                    self.db.query(Notification)
                    .filter(
                        Notification.user_id == uid,
                        Notification.source_entity_type == "purchase_request",
                        Notification.source_entity_id == header_id,
                        Notification.event_type == event_type,
                    )
                    .first()
                )
                if existing_u:
                    existing_u.title = title
                    existing_u.body = body_plain
                    existing_u.type = notif_type
                    self.db.commit()
                else:
                    notif_svc.create_in_app_only(
                        user_id=uid,
                        type=notif_type,
                        title=title,
                        body=body_plain,
                        source_entity_type="purchase_request",
                        source_entity_id=header_id,
                        event_type=event_type,
                    )
            except Exception as e:
                logger.warning("Failed to create in-app notification for user %s: %s", uid, e)
        logger.info(
            "Notifying %s team member(s) for external PR/sponsorship %s: %s (1 email to all)",
            len(user_ids),
            integration_action,
            header_id,
        )

    def _notify_requester_on_approved(self, header: PurchaseRequestHeader) -> None:
        """Notify the user who requested approval when the purchase request / sponsorship form is approved."""
        requested_by_uid = getattr(header, "requested_approval_by_user_id", None)
        if not requested_by_uid:
            return
        type_label = "Purchase Request" if getattr(header, "request_type", None) == "purchase_request" else "Sponsorship Form"
        form_number = display_document_number(header) or "N/A"
        project = getattr(header, "project_title", None) or "N/A"

        view_token = self.get_or_create_view_token(str(header.id))
        base_url = (settings.frontend_base_url or "").strip().rstrip("/")
        if not base_url:
            from app.models.user import SystemSetting
            sys_settings = self.db.query(SystemSetting).first()
            if sys_settings and getattr(sys_settings, "website_url", None):
                base_url = (sys_settings.website_url or "").strip().rstrip("/")
        view_url = f"{base_url}/view/request?token={view_token}" if base_url else f"/view/request?token={view_token}"

        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(self.db).render_code(
            "purchase_request_requester_approved",
            {
                "purchase_request": {"type_label": type_label, "request_number": form_number, "project_title": project},
                "view_url": view_url,
            },
        )

        from app.services.notification_service import NotificationService
        NotificationService(self.db).create(
            user_id=str(requested_by_uid),
            type="purchase_request_approved",
            title=rendered["subject"],
            # The bell and web push keep their one-line body; the email's text part is
            # the rendered template (notification_tasks prefers data["body_text"]).
            body=f"{type_label} {form_number} (Project: {project}) has been approved.\n\nView form: {view_url}",
            data={"body_html": rendered["body_html"], "body_text": rendered["body_text"]},
            source_entity_type="purchase_request",
            source_entity_id=str(header.id),
            event_type="approved",
        )

    def _notify_requester_on_rejected(self, header: PurchaseRequestHeader) -> None:
        """Notify the user who requested approval when the purchase request / sponsorship form is rejected."""
        requested_by_uid = getattr(header, "requested_approval_by_user_id", None)
        if not requested_by_uid:
            return
        type_label = "Purchase Request" if getattr(header, "request_type", None) == "purchase_request" else "Sponsorship Form"
        form_number = display_document_number(header) or "N/A"
        project = getattr(header, "project_title", None) or "N/A"

        view_token = self.get_or_create_view_token(str(header.id))
        base_url = (settings.frontend_base_url or "").strip().rstrip("/")
        if not base_url:
            from app.models.user import SystemSetting
            sys_settings = self.db.query(SystemSetting).first()
            if sys_settings and getattr(sys_settings, "website_url", None):
                base_url = (sys_settings.website_url or "").strip().rstrip("/")
        view_url = f"{base_url}/view/request?token={view_token}" if base_url else f"/view/request?token={view_token}"

        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(self.db).render_code(
            "purchase_request_requester_rejected",
            {
                "purchase_request": {"type_label": type_label, "request_number": form_number, "project_title": project},
                "view_url": view_url,
            },
        )

        from app.services.notification_service import NotificationService
        NotificationService(self.db).create(
            user_id=str(requested_by_uid),
            type="purchase_request_rejected",
            title=rendered["subject"],
            # The bell and web push keep their one-line body; the email's text part is
            # the rendered template (notification_tasks prefers data["body_text"]).
            body=f"{type_label} {form_number} (Project: {project}) has been rejected.\n\nView form: {view_url}",
            data={"body_html": rendered["body_html"], "body_text": rendered["body_text"]},
            source_entity_type="purchase_request",
            source_entity_id=str(header.id),
            event_type="rejected",
        )

    def _dispatch_approval_automation(self, header: PurchaseRequestHeader) -> None:
        """Fire the configurable automation for an approved PR / sponsorship form.

        Mirrors the complaint-approved pattern in ``ComplaintsService``: build a
        Jinja-ready context dict and call ``AutomationService.dispatch_event``
        with the trigger_type that matches ``header.request_type``. Admins
        configure recipients + email template in System Management → Automation.
        """
        from datetime import date as _date

        from app.services.automation_service import AutomationService
        from app.services.automation_triggers import _build_purchase_request_link

        request_type = (getattr(header, "request_type", None) or "purchase_request").strip()
        if request_type == "sponsorship_form":
            trigger_type = "sponsorship_form_approved"
            type_label = "Sponsorship Form"
        else:
            trigger_type = "purchase_request_approved"
            type_label = "Purchase Request"

        header_id = str(header.id)
        total_value = getattr(header, "total_project_value", None)
        approved_at = getattr(header, "approved_at", None)
        request_date = getattr(header, "request_date", None)
        expected_delivery_date = getattr(header, "expected_delivery_date", None)
        expected_po_date = getattr(header, "expected_po_date", None)

        ctx = {
            "purchase_request": {
                "id": header_id,
                "type": request_type,
                "type_label": type_label,
                "request_number": getattr(header, "request_number", None),
                "request_date": request_date.isoformat() if request_date else None,
                "customer_name": getattr(header, "customer_name", None),
                "pic": getattr(header, "pic", None),
                "project_title": getattr(header, "project_title", None),
                "purpose": getattr(header, "purpose", None),
                "requested_by": getattr(header, "requested_by", None),
                "approved_by": getattr(header, "approved_by", None),
                "approved_at": approved_at.isoformat() if approved_at else None,
                "approval_comments": getattr(header, "approval_comments", None),
                "total_project_value": str(total_value) if total_value is not None else None,
                "total_project_value_text": getattr(header, "total_project_value_text", None),
                "expected_delivery_date": (
                    expected_delivery_date.isoformat() if expected_delivery_date else None
                ),
                "expected_po_date": (
                    expected_po_date.isoformat() if expected_po_date else None
                ),
                "expected_po_date_text": getattr(header, "expected_po_date_text", None),
                "status": "approved",
                "link": _build_purchase_request_link(header_id, request_type),
            },
            "today": _date.today().isoformat(),
        }
        AutomationService(self.db).dispatch_event(
            trigger_type,
            context=ctx,
            source_kind="purchase_request",
            source_id=header_id,
        )

    def _build_request_list_query(
        self,
        query: Optional[str] = None,
        request_type: Optional[str] = None,
        approval_status: Optional[str] = None,
        sort_field: str = "request_date",
        sort_dir: str = "desc",
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ):
        """Build the filtered + sorted PR/SF query shared by ``list_requests`` and
        ``neighbours`` so the two can never drift.

        The ORDER BY always appends ``PurchaseRequestHeader.id`` as a deterministic
        tie-breaker so offset position and prev/next neighbours are unambiguous when
        the primary sort column has equal (or NULL) values. ``request_type`` is part
        of the filter set, so PR navigation stays within PRs and SF within SFs.
        """
        q = self.db.query(PurchaseRequestHeader)
        if contact_id is not None:
            q = q.filter(PurchaseRequestHeader.contact_id == str(contact_id).strip())
        if assigned_to is not None and str(assigned_to).strip():
            # Filter by the latest unresolved form-SLA assignee (project-sales
            # before approval, customer-service after) - mirrors complaint.
            from app.models.sla import ConversationSLATracking
            from app.services.sla_scope import open_tracker_scope

            base = self.db.query(ConversationSLATracking.source_entity_id).filter(
                ConversationSLATracking.source_entity_type.in_(
                    ("purchase_request", "sponsorship_form")
                ),
                *open_tracker_scope(),
            )
            val = str(assigned_to).strip()
            if val.lower() == "__unassigned__":
                assigned_subq = base.filter(
                    ConversationSLATracking.assigned_to_id.isnot(None)
                )
                q = q.filter(~PurchaseRequestHeader.id.in_(assigned_subq))
            else:
                q = q.filter(
                    PurchaseRequestHeader.id.in_(
                        base.filter(ConversationSLATracking.assigned_to_id == val)
                    )
                )
        if space_id is not None:
            q = q.filter(PurchaseRequestHeader.space_id == str(space_id).strip())
        if query:
            like = f"%{query}%"
            line_subq = (
                self.db.query(PurchaseRequestLine.purchase_request_id)
                .filter(
                    or_(
                        PurchaseRequestLine.item_code.ilike(like),
                        PurchaseRequestLine.remark.ilike(like),
                    )
                )
            )
            q = q.filter(
                or_(
                    PurchaseRequestHeader.request_number.ilike(like),
                    PurchaseRequestHeader.customer_name.ilike(like),
                    PurchaseRequestHeader.project_title.ilike(like),
                    PurchaseRequestHeader.purpose.ilike(like),
                    PurchaseRequestHeader.delivery_address.ilike(like),
                    PurchaseRequestHeader.sponsor_subject.ilike(like),
                    PurchaseRequestHeader.requested_by.ilike(like),
                    PurchaseRequestHeader.external_reference.ilike(like),
                    PurchaseRequestHeader.total_project_value_text.ilike(like),
                    PurchaseRequestHeader.status.ilike(like),
                    PurchaseRequestHeader.approval_status.ilike(like),
                    PurchaseRequestHeader.id.in_(line_subq),
                )
            )
        if request_type and request_type.strip() in ("purchase_request", "sponsorship_form"):
            q = q.filter(PurchaseRequestHeader.request_type == request_type.strip())
        if approval_status and approval_status.strip():
            status_val = approval_status.strip().lower()
            if status_val == "draft":
                q = q.filter(
                    or_(
                        PurchaseRequestHeader.approval_status.is_(None),
                        PurchaseRequestHeader.approval_status == "",
                    )
                )
            elif status_val in ("pending", "approved", "rejected"):
                q = q.filter(PurchaseRequestHeader.approval_status == status_val)

        sort_map = {
            "submitted_at": PurchaseRequestHeader.submitted_at,
            "request_date": PurchaseRequestHeader.request_date,
            "created_at": PurchaseRequestHeader.created_at,
            "customer_name": PurchaseRequestHeader.customer_name,
            "project_title": PurchaseRequestHeader.project_title,
        }
        sort_col = sort_map.get(sort_field, PurchaseRequestHeader.submitted_at)
        if sort_dir == "desc":
            q = q.order_by(sort_col.desc().nullslast(), PurchaseRequestHeader.id.asc())
        else:
            q = q.order_by(sort_col.asc().nullsfirst(), PurchaseRequestHeader.id.asc())
        return q

    def list_requests(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        request_type: Optional[str] = None,
        approval_status: Optional[str] = None,
        sort_field: str = "request_date",
        sort_dir: str = "desc",
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ):
        """List purchase requests / sponsorship forms with pagination."""
        q = self._build_request_list_query(
            query=query,
            request_type=request_type,
            approval_status=approval_status,
            sort_field=sort_field,
            sort_dir=sort_dir,
            contact_id=contact_id,
            space_id=space_id,
            assigned_to=assigned_to,
        )

        total = q.count()
        offset = (page - 1) * limit
        items = q.offset(offset).limit(limit).all()
        self._attach_sla_assignees(items)
        return {
            "data": items,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }

    def _attach_sla_assignees(self, items) -> None:
        """Set `assigned_to_id` / `assigned_to_name` / `handled_by_name` on each
        header from the latest unresolved form-SLA tracker (project-sales
        pre-approval, CS post-approval). `handled_by_name` is the form-handling-lock
        holder (separate from the assignee). Mirrors complaint's
        `_latest_unresolved_sla_assignee_name`, batched per page.
        """
        ids = [str(getattr(i, "id", "")) for i in items if getattr(i, "id", None)]
        if not ids:
            return
        from app.models.sla import ConversationSLATracking
        from app.models.user import User
        from app.services.sla_scope import open_tracker_scope

        rows = (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type.in_(
                    ("purchase_request", "sponsorship_form")
                ),
                ConversationSLATracking.source_entity_id.in_(ids),
                *open_tracker_scope(),
            )
            .order_by(
                ConversationSLATracking.source_entity_id,
                ConversationSLATracking.initiated_at.desc(),
            )
            .all()
        )
        latest: dict = {}
        for r in rows:
            latest.setdefault(r.source_entity_id, r)  # first per id = latest (desc order)
        uids = {r.assigned_to_id for r in latest.values() if r.assigned_to_id}
        uids |= {
            getattr(r, "handled_by_id", None)
            for r in latest.values()
            if getattr(r, "handled_by_id", None)
        }
        users = (
            {u.id: u for u in self.db.query(User).filter(User.id.in_(uids)).all()}
            if uids
            else {}
        )

        def _name(uid):
            user = users.get(uid) if uid else None
            return (user.name or user.email) if user else None

        for it in items:
            tracker = latest.get(str(it.id))
            aid = tracker.assigned_to_id if tracker else None
            hid = getattr(tracker, "handled_by_id", None) if tracker else None
            setattr(it, "assigned_to_id", aid)
            setattr(it, "assigned_to_name", _name(aid))
            setattr(it, "handled_by_name", _name(hid))

    def get_request(
        self,
        request_id: str,
        *,
        contact_id: Optional[str] = None,
        space_id: Optional[str] = None,
    ):
        """Get a purchase request by ID with lines."""
        from sqlalchemy.orm import joinedload

        q = (
            self.db.query(PurchaseRequestHeader)
            .options(joinedload(PurchaseRequestHeader.lines))
            .filter(PurchaseRequestHeader.id == request_id)
        )
        if contact_id is not None:
            q = q.filter(PurchaseRequestHeader.contact_id == str(contact_id).strip())
        if space_id is not None:
            q = q.filter(PurchaseRequestHeader.space_id == str(space_id).strip())
        header = q.first()
        if not header:
            raise handle_not_found("Request", request_id)
        self._attach_project_display(header)
        return header

    def _attach_project_display(self, header) -> None:
        """Stamp the linked project's CODE onto the row for serialization (AC-L3).

        The response model reads attributes off the ORM object, so setting one here is what
        gets `project_code` to the office-side detail page -- the portal already resolves it
        for contacts, and the two surfaces showing different things (one a code, one nothing)
        is how a link starts looking broken. `project_title` remains the fallback for the
        thousands of rows that have no link.
        """
        if not getattr(header, "project_id", None):
            header.project_code = None
            return
        try:
            from app.models.projects import Project

            row = (
                self.db.query(Project.project_code)
                .filter(Project.id == str(header.project_id))
                .first()
            )
            header.project_code = row[0] if row else None
        except Exception:  # noqa: BLE001 -- display sugar must never break a read
            header.project_code = None

    def get_neighbour_ids(
        self, request_id: str, request_type: Optional[str] = None
    ) -> dict:
        """Return prev_id and next_id for the given request (order: submitted_at desc, same as list)."""
        header = self.get_request(request_id)
        q = self.db.query(PurchaseRequestHeader.id).order_by(
            PurchaseRequestHeader.submitted_at.desc().nullslast(),
            PurchaseRequestHeader.id.desc(),
        )
        if request_type and request_type.strip() in ("purchase_request", "sponsorship_form"):
            q = q.filter(PurchaseRequestHeader.request_type == request_type.strip())
        ids = [r[0] for r in q.all()]
        try:
            idx = ids.index(header.id)
        except ValueError:
            return {"prev_id": None, "next_id": None, "total_count": 0, "current_index": 0}
        prev_id = ids[idx - 1] if idx > 0 else None
        next_id = ids[idx + 1] if idx < len(ids) - 1 else None
        return {
            "prev_id": prev_id,
            "next_id": next_id,
            "total_count": len(ids),
            "current_index": idx + 1,
        }

    def create_request(self, data: PurchaseRequestHeaderCreate):
        """Create purchase request header + lines (internal API)."""
        dump = data.model_dump(exclude={"products"})
        dump["status"] = "draft"
        dump["source"] = "manual"
        _strip_number_suffix_in_place(dump, "request_number")
        norm_subject, norm_other = self._normalize_sponsor_subject(
            dump.get("request_type"), dump.get("sponsor_subject")
        )
        dump["sponsor_subject"] = norm_subject
        # Keep an explicitly supplied 'others' detail; else use the parked raw text.
        explicit_other = (dump.get("sponsor_subject_other") or "").strip() or None
        dump["sponsor_subject_other"] = explicit_other or norm_other
        if not dump.get("request_number"):
            from app.services.numbering_service import NumberingService
            ref_date = dump.get("request_date") or date.today()
            if isinstance(ref_date, datetime):
                ref_date = ref_date.date() if hasattr(ref_date, "date") else ref_date
            number = NumberingService(self.db).get_next_number(dump["request_type"], ref_date)
            if number:
                dump["request_number"] = number
        contact_id = dump.get("contact_id")
        space_id = dump.get("space_id")
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            dump["respond_inbox_url"] = respond_inbox_url
        requestor_value = dump.pop("requested_by_contact_id", None)
        header = PurchaseRequestHeader(**{k: v for k, v in dump.items() if hasattr(PurchaseRequestHeader, k)})
        if requestor_value:
            # Validated + label-derived, never a bare setattr: the document, the
            # PDF and the public approval page print the TEXT label (D6/D9).
            _apply_requestor_contact(
                self.db, header, "requested_by_contact_id", "requested_by", requestor_value
            )
        self.db.add(header)
        self.db.flush()

        for index, line_data in enumerate(data.products or []):
            line = PurchaseRequestLine(
                purchase_request_id=header.id,
                item_code=line_data.item_code,
                quantity=line_data.quantity,
                remark=line_data.remark,
                unit_price=getattr(line_data, "unit_price", None),
                total=getattr(line_data, "total", None),
                sort_order=index,
            )
            self.db.add(line)

        self.db.commit()
        self.db.refresh(header)
        try:
            self.get_or_create_view_token(str(header.id))
            self.db.commit()
        except Exception:
            pass
        return header

    def update_request(self, request_id: str, data: PurchaseRequestHeaderUpdate):
        """Update purchase request header and optionally replace lines.

        Status is NOT editable here - it moves only through the workflow actions
        (set-pending-approval / approval-decision / reject-submitted / process /
        close / void), exactly as ``StockInquiryService.update_inquiry`` has
        always done. ``PurchaseRequestHeaderUpdate`` still exposes ``status``, so
        without this guard a plain PUT could walk the lifecycle sideways - and a
        contact revision, which sets the status back to the restart stage, could
        be stomped straight back to `approved` by an office tab that was mid-edit.
        A payload that would actually MOVE the status is refused (422), never
        silently dropped: see ``_pop_status_or_refuse_move``.
        """
        header = self.get_request(request_id)
        if data.products is not None:
            # #1232 blocking 2: `request_type` is optional on this schema and usually
            # absent on a plain header edit, so `refuse_missing_sponsorship_unit_prices`
            # (which only fires on request_type == "sponsorship_form") must be checked
            # against the EFFECTIVE type - the payload's own if it sent one, else the
            # stored header's - or a caller that simply omits request_type bypasses the
            # rule on a stored sponsorship form.
            refuse_missing_sponsorship_unit_prices(
                data.request_type or header.request_type, data.products
            )
        payload = data.model_dump(exclude_unset=True, exclude={"products"})
        _pop_status_or_refuse_move(
            payload,
            current=header.status,
            label=_request_label(header),
            actions=_REQUEST_STATUS_ACTIONS,
        )
        _strip_number_suffix_in_place(payload, "request_number")
        contact_id = payload.get("contact_id") if "contact_id" in payload else header.contact_id
        space_id = payload.get("space_id") if "space_id" in payload else header.space_id
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            payload["respond_inbox_url"] = respond_inbox_url
        elif contact_id is None and space_id is None:
            payload["respond_inbox_url"] = None
        self._apply_sponsor_subject_to_payload(payload, header)
        requestor_value = payload.pop("requested_by_contact_id", _UNSET_REQUESTOR)
        for key, value in payload.items():
            if hasattr(header, key):
                setattr(header, key, value)
        if requestor_value is not _UNSET_REQUESTOR:
            _apply_requestor_contact(
                self.db, header, "requested_by_contact_id", "requested_by", requestor_value
            )

        if data.products is not None:
            for line in list(header.lines or []):
                self.db.delete(line)
            self.db.flush()
            for index, line_data in enumerate(data.products):
                line = PurchaseRequestLine(
                    purchase_request_id=header.id,
                    item_code=line_data.item_code,
                    quantity=line_data.quantity,
                    remark=line_data.remark,
                    unit_price=getattr(line_data, "unit_price", None),
                    total=getattr(line_data, "total", None),
                    sort_order=index,
                )
                self.db.add(line)

        self.db.commit()
        self.db.refresh(header)
        return header

    def update_request_and_reply(
        self,
        request_id: str,
        data: PurchaseRequestUpdateAndReply,
        respond_user_id: str,
        request_url: str = "",
        crm_sender_user_id: Optional[str] = None,
    ):
        """
        Update purchase request (e.g. request_number), then send a reply to the conversation via Respond.io.
        Message is reply_message if provided, otherwise built from request_number.

        The status guard is the SAME one ``update_request`` applies, deliberately:
        this endpoint is an office edit plus a chat send, and it moves the lifecycle
        no more than a plain PUT does. ``PurchaseRequestUpdateAndReply`` inherits
        ``status`` from the header update schema, so without the guard here the
        hardening on the PUT path was a fence with a gate left open beside it.

        It matters for revisions specifically: a revision sets the status back to
        the restart stage, and a stale office tab (or an n8n write) that can set
        ``status`` freely stomps it straight back to the value the revision
        superseded - the exact defect the guard exists to prevent. A payload
        echoing the current status still saves, so read-modify-write keeps working.
        """
        import logging
        from app.services.integration_service import IntegrationLogService
        from app.schemas.integration import IntegrationLogCreate
        from app.services.error_handler import handle_validation_error

        logger = logging.getLogger(__name__)
        log_service = IntegrationLogService(self.db)

        header = self.get_request(request_id)
        if data.products is not None:
            # #1232 blocking 2: same effective-type check as `update_request` - this
            # schema inherits `request_type` (optional) from `PurchaseRequestHeaderUpdate`.
            refuse_missing_sponsorship_unit_prices(
                data.request_type or header.request_type, data.products
            )
        payload = data.model_dump(exclude_unset=True, exclude={"products", "reply_message"})
        _pop_status_or_refuse_move(
            payload,
            current=header.status,
            label=_request_label(header),
            actions=_REQUEST_STATUS_ACTIONS,
        )
        _strip_number_suffix_in_place(payload, "request_number")
        contact_id = payload.get("contact_id") if "contact_id" in payload else header.contact_id
        space_id = payload.get("space_id") if "space_id" in payload else header.space_id
        respond_inbox_url = self._build_respond_inbox_url(contact_id, space_id)
        if respond_inbox_url is not None:
            payload["respond_inbox_url"] = respond_inbox_url
        elif contact_id is None and space_id is None:
            payload["respond_inbox_url"] = None

        self._apply_sponsor_subject_to_payload(payload, header)
        for key, value in payload.items():
            if hasattr(header, key):
                setattr(header, key, value)

        if data.products is not None:
            for line in list(header.lines or []):
                self.db.delete(line)
            self.db.flush()
            for index, line_data in enumerate(data.products):
                line = PurchaseRequestLine(
                    purchase_request_id=header.id,
                    item_code=line_data.item_code,
                    quantity=line_data.quantity,
                    remark=line_data.remark,
                    unit_price=getattr(line_data, "unit_price", None),
                    total=getattr(line_data, "total", None),
                    sort_order=index,
                )
                self.db.add(line)

        self.db.flush()
        self.db.refresh(header)

        reply_message = (getattr(data, "reply_message", None) or "").strip()
        # The number the CONTACT is told, so it carries the revision (UAC N1/N5).
        request_number = display_document_number(header) or payload.get("request_number")
        if request_number is not None and isinstance(request_number, str):
            request_number = request_number.strip() or None
        if not reply_message and request_number:
            reply_message = f"Your request has been assigned form number: {request_number}."
        if not reply_message:
            raise handle_validation_error(
                "Provide reply_message or request_number so we can reply to the conversation."
            )

        identifier = self._identifier_from_respond_inbox_url(getattr(header, "respond_inbox_url", None))
        if not identifier:
            raise handle_validation_error(
                "respond_inbox_url is missing or invalid; cannot send message. Set contact_id and space_id."
            )

        display_message = reply_message
        # Window-aware send: plain text inside the 24h window, default WhatsApp
        # template outside it (plan: PLAN-whatsapp-template-fallback.md).
        from app.services.respond_messaging_service import (
            build_context_vars,
            send_text_or_template,
            use_case_for_purchase_request,
        )

        use_case = use_case_for_purchase_request(header)
        request_payload = {"message": {"type": "text", "text": display_message}}
        try:
            context_vars = build_context_vars(
                self.db,
                use_case=use_case,
                business_id=request_id,
                identifier=identifier,
            )
            # Structured-template vars: bare reply text as `update` core + links.
            # Mirrors complaint's bare `stored_body` (no preamble, no inline URL).
            context_vars.update({
                "update": display_message,
                "portal_url": self._purchase_request_portal_or_view_url(header, str(header.id)),
                "view_url": (self._build_request_view_url(str(header.id)) or "").strip(),
            })
            result = send_text_or_template(
                self.db,
                identifier=identifier,
                text=display_message,
                use_case=use_case,
                context_vars=context_vars,
            )
            request_payload = result["request_payload"]
            response = result["response"]
            from app.services.crm_chat_outbound_webhook import (
                enqueue_crm_chat_outbound_webhook,
                resolve_sla_assignee_respond_user_id,
            )

            enqueue_crm_chat_outbound_webhook(
                self.db,
                business_table="purchase_requests",
                business_id=request_id,
                contact_respond_io_id=identifier,
                message_text=display_message,
                respond_api_response=response if isinstance(response, dict) else None,
                space_id=getattr(header, "space_id", None),
                crm_sender_user_id=crm_sender_user_id,
                respond_user_id_fallback=respond_user_id,
                assignee_respond_user_id=resolve_sla_assignee_respond_user_id(
                    self.db, "purchase_request", request_id
                ),
            )
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="purchase_requests",
                    business_id=request_id,
                    external_reference=identifier,
                    direction="outbound",
                    endpoint="https://api.respond.io/v2/contact/id:{}/message".format(identifier),
                    http_method="POST",
                    status="success",
                    response_payload=str(response)[:50000] if response else None,
                ),
                request_payload_dict=request_payload,
            )
        except Exception as e:
            logger.exception("Respond.io send failed for purchase_request %s", request_id)
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="purchase_requests",
                    business_id=request_id,
                    external_reference=identifier or "",
                    direction="outbound",
                    endpoint="https://api.respond.io/v2/contact/id:{}/message".format(identifier or ""),
                    http_method="POST",
                    status="failed",
                    error_message=str(e),
                ),
                request_payload_dict=request_payload,
            )
            raise

        self.db.commit()
        self.db.refresh(header)
        return header

    def delete_request(self, request_id: str) -> None:
        """Delete a purchase request and its lines."""
        header = self.get_request(request_id)
        self.entity_attachment_service.delete_links_for_entity("purchase_request", str(header.id))
        self.db.delete(header)
        self.db.commit()

    def link_attachment_to_request(
        self, request_id: str, attachment_id: str, created_by: Optional[str] = None
    ):
        """Link an existing attachment to a purchase request (generic entity_attachment_links table)."""
        self.get_request(request_id)  # ensure request exists
        link = self.entity_attachment_service.link_existing_attachment(
            entity_type="purchase_request",
            entity_id=str(request_id),
            attachment_id=str(attachment_id),
            created_by=created_by,
        )
        self.db.commit()
        self.db.refresh(link)
        return link

    def delete_request_attachment(self, link_id: str) -> None:
        """Delete a purchase-request attachment link from generic entity_attachment_links table."""
        self.entity_attachment_service.delete_link(link_id, entity_type="purchase_request")
        self.db.commit()

    def bulk_delete_requests(self, request_ids: List[str]) -> dict:
        """Delete multiple purchase requests / sponsorship forms by ID. Returns deleted_count."""
        if not request_ids:
            return {"message": "No records to delete", "deleted_count": 0}
        headers = (
            self.db.query(PurchaseRequestHeader)
            .filter(PurchaseRequestHeader.id.in_(request_ids))
            .all()
        )
        for header in headers:
            self.entity_attachment_service.delete_links_for_entity("purchase_request", str(header.id))
            self.db.delete(header)
        self.db.commit()
        return {"message": f"Deleted {len(headers)} record(s)", "deleted_count": len(headers)}

    def set_pending_approval(self, request_id: str, requested_by_user_id: Optional[str] = None):
        """Set request to pending approval. Refuses when approval_status='rejected':
        a rejected request must go back through the salesperson's portal re-submit
        loop, not be force-moved to pending by the reviewer.
        """
        from app.services.error_handler import handle_conflict

        header = self.get_request(request_id)
        current = (getattr(header, "approval_status", None) or "").strip().lower()
        if current == "rejected":
            raise handle_conflict(
                "Cannot change to pending approval: request was rejected. "
                "Salesperson must edit and re-submit from the portal."
            )
        # No-op guard: only treat this as a real transition into pending when it
        # wasn't already pending. A redundant call (double-click before the FE
        # refetch hides the button, client/proxy retry, etc.) must NOT re-emit the
        # 'send_for_approval' SLA event - that re-runs the approval-stage start and,
        # combined with stages sharing a policy, spawns a duplicate assignment +
        # duplicate WhatsApp. See _active_tracker stage-scoping fix in form_sla_service.
        already_pending = current == "pending"
        header.approval_status = "pending"
        header.approved_at = None
        header.approved_by = None
        header.approval_signature_ref = None
        header.approval_comments = None
        if requested_by_user_id is not None:
            header.requested_approval_by_user_id = requested_by_user_id
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise
        if not already_pending:
            try:
                from app.services.form_sla_service import emit_form_event
                emit_form_event(
                    self.db,
                    getattr(header, "request_type", None) or "purchase_request",
                    str(header.id),
                    "send_for_approval",
                    contact_id=getattr(header, "contact_id", None),
                    actor_user_id=requested_by_user_id,
                )
            except Exception as e:
                logger.warning("Form SLA emit 'send_for_approval' failed for %s: %s", request_id, e)
            # Tell the contact their form has entered approval. Every other transition
            # on this form talks to them (approve, reject, processed, closed); this step
            # was the one silent one, so from their side the form went quiet between
            # submitting and hearing a decision. Guarded by `already_pending` so a
            # redundant call cannot re-send it, and best-effort so a Respond.io failure
            # never rolls back the committed status.
            try:
                self._notify_contact_on_pending_approval(header)
            except Exception as e:
                logger.warning(
                    "Failed to send Respond.io pending-approval message for %s: %s",
                    request_id,
                    e,
                )
        # Re-query with relationships loaded to avoid expired instance issues
        return self.get_request(request_id)

    def _notify_contact_on_pending_approval(self, header) -> None:
        """Notify the linked Respond.io contact that the form is now awaiting approval."""
        request_number = (getattr(header, "request_number", None) or str(header.id)).strip()
        rt = getattr(header, "request_type", None) or ""
        type_word = "sponsorship form" if rt == "sponsorship_form" else "purchase request"
        portal_url = self._purchase_request_portal_or_view_url(header, str(header.id))
        message_text = (
            f"Your {type_word} {request_number} has been sent for approval. "
            f"We will let you know as soon as it is decided. "
            f"You can view your submission here {portal_url}"
        )
        self._send_purchase_request_contact_message(
            header,
            message_text=message_text,
            extra_context_vars={
                "update": "Pending approval",
                "portal_url": portal_url,
                "view_url": (self._build_request_view_url(str(header.id)) or "").strip(),
            },
        )

    def reject_submitted(
        self,
        request_id: str,
        rejection_reason: str,
        actor_user_id: Optional[str] = None,
    ):
        """Reject a submitted purchase request / sponsorship form before sending for approval.

        Allowed only when the request has not yet been sent for approval (approval_status
        is null/empty/draft). Mirrors public-approval rejection: writes approval_status,
        approval_comments, and notifies the contact via Respond.io. Same permission as
        Send for Approval.
        """
        from app.services.error_handler import handle_validation_error, handle_conflict

        reason = (rejection_reason or "").strip()
        if not reason:
            raise handle_validation_error("Rejection reason is required.")

        header = self.get_request(request_id)
        current = (getattr(header, "approval_status", None) or "").strip().lower()
        if current and current not in ("", "draft"):
            raise handle_conflict(
                f"Cannot reject submitted: approval_status is already '{current}'. "
                "Reject is only available before sending for approval."
            )

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        header.approval_status = "rejected"
        header.status = "rejected"
        header.approval_comments = reason
        header.approved_at = now_utc
        # Store a resolved display name (never a raw UUID) so the "Rejected by"
        # field renders a person, consistent with the approval-decision path.
        # The raw actor id is preserved separately in requested_approval_by_user_id.
        header.approved_by = self._resolve_actor_display_name(actor_user_id)
        # Dedicated rejecter id so the banner can resolve a name + wa.me phone.
        # Guard the FK the same way the approval-decision path does: only set it when
        # the actor resolves to a real users row, so a non-user actor id can never
        # abort the whole reject on the FK constraint (NULL -> banner plain text).
        _rid = (actor_user_id or "").strip() if actor_user_id else None
        header.rejected_by_id = (
            _rid if (_rid and self.db.query(User.id).filter(User.id == _rid).first()) else None
        )
        header.approval_signature_ref = None
        if actor_user_id is not None:
            header.requested_approval_by_user_id = actor_user_id
        self.db.commit()
        self.db.refresh(header)

        try:
            self._notify_contact_on_approval_rejected(header)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(
                "Failed to send Respond.io rejection notice for %s %s: %s",
                getattr(header, "request_type", None),
                request_id,
                e,
            )

        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                getattr(header, "request_type", None) or "purchase_request",
                str(header.id),
                "reject_submitted",
                contact_id=getattr(header, "contact_id", None),
                actor_user_id=actor_user_id,
            )
        except Exception as e:
            logger.warning("Form SLA emit 'reject_submitted' failed for %s: %s", request_id, e)

        return self.get_request(request_id)

    def create_approval_token(
        self,
        request_id: str,
        approver_email: Optional[str] = None,
        approver_user_id: Optional[str] = None,
        requested_by_user_id: Optional[str] = None,
        expires_hours: int = 24,
        base_url: str = "",
    ) -> tuple[ApprovalToken, str]:
        """Create one-time approval token for a purchase request. Returns (token row, approval_url)."""
        from app.models.user import User

        header = self.get_request(request_id)
        if approver_user_id:
            header.approver_user_id = approver_user_id
            user = self.db.query(User).filter(User.id == approver_user_id).first()
            if user:
                header.approver_email = approver_email or user.email or header.approver_email
            elif approver_email:
                header.approver_email = approver_email
        else:
            header.approver_user_id = None
            if approver_email:
                header.approver_email = approver_email
        if requested_by_user_id is not None:
            header.requested_approval_by_user_id = requested_by_user_id
        # When resending after approved/rejected, clear previous approval so request is back in "pending approval"
        if header.approval_status in ("approved", "rejected"):
            header.approved_at = None
            header.approved_by = None
            header.approval_signature_ref = None
            header.approval_comments = None
        header.approval_status = "pending"
        self.db.flush()

        token_value = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(hours=expires_hours)
        approval_token = ApprovalToken(
            entity_type="purchase_request",
            entity_id=request_id,
            token=token_value,
            expires=expires,
        )
        self.db.add(approval_token)
        self.db.commit()
        self.db.refresh(approval_token)

        approval_url = f"{base_url.rstrip('/')}/approval?token={token_value}" if base_url else f"/approval?token={token_value}"
        return approval_token, approval_url

    def get_approval_summary_by_token(self, token_value: str):
        """Validate token and return request summary for public approval page. Raises if invalid/expired/used."""
        approval_token = (
            self.db.query(ApprovalToken)
            .filter(ApprovalToken.token == token_value)
            .first()
        )
        if not approval_token:
            raise handle_not_found("Approval link", "(invalid token)")
        if approval_token.used_at is not None:
            raise handle_conflict("This approval link has already been used.")
        now = datetime.utcnow()
        if approval_token.expires <= now:
            raise handle_conflict("This approval link has expired.")
        header = self.get_request(approval_token.entity_id)
        lines = []
        grand_total = None
        if getattr(header, "lines", None):
            for line in sorted(header.lines, key=lambda l: (l.sort_order if l.sort_order is not None else 999, getattr(l, "id", 0))):
                qty = line.quantity
                if qty is not None and hasattr(qty, "__float__"):
                    try:
                        qty = float(qty)
                    except (TypeError, ValueError):
                        pass
                up = getattr(line, "unit_price", None)
                tot = getattr(line, "total", None)
                if up is not None and hasattr(up, "__float__"):
                    try:
                        up = float(up)
                    except (TypeError, ValueError):
                        up = None
                if tot is not None and hasattr(tot, "__float__"):
                    try:
                        tot = float(tot)
                    except (TypeError, ValueError):
                        tot = None
                lines.append({
                    "item_code": line.item_code,
                    "quantity": qty,
                    "remark": line.remark,
                    "unit_price": up,
                    "total": tot,
                    "sort_order": line.sort_order,
                })
            if getattr(header, "request_type", None) == "sponsorship_form" and lines:
                try:
                    total_sum = Decimal("0")
                    for l in lines:
                        t = l.get("total")
                        if t is not None:
                            total_sum += Decimal(str(t))
                        else:
                            q, u = l.get("quantity"), l.get("unit_price")
                            if q is not None and u is not None:
                                total_sum += Decimal(str(q)) * Decimal(str(u))
                    grand_total = total_sum
                except (InvalidOperation, ValueError, TypeError):
                    grand_total = None
        approver_display_name = None
        approver_email = getattr(header, "approver_email", None) or None
        approver_user_id = getattr(header, "approver_user_id", None)
        if approver_user_id:
            user = self.db.query(User).filter(User.id == approver_user_id).first()
            if user:
                approver_display_name = (user.name and user.name.strip()) or user.email or None
                if not approver_email and user.email:
                    approver_email = user.email

        return {
            "entity_type": approval_token.entity_type,
            "entity_id": approval_token.entity_id,
            "request_number": header.request_number,
            "request_type": header.request_type,
            "customer_name": header.customer_name,
            "pic": header.pic,
            "project_title": header.project_title,
            "purpose": header.purpose,
            "delivery_address": getattr(header, "delivery_address", None),
            "total_project_value": getattr(header, "total_project_value", None),
            "total_project_value_text": getattr(header, "total_project_value_text", None),
            "sponsor_subject": getattr(header, "sponsor_subject", None),
            "sponsor_subject_other": getattr(header, "sponsor_subject_other", None),
            "requested_by": header.requested_by,
            "requested_by_contact_name": header.requested_by_contact_name,
            "request_date": getattr(header, "request_date", None),
            "submitted_at": getattr(header, "submitted_at", None),
            "created_at": getattr(header, "created_at", None),
            "expected_delivery_date": getattr(header, "expected_delivery_date", None),
            "expected_po_date": getattr(header, "expected_po_date", None),
            "expected_po_date_text": getattr(header, "expected_po_date_text", None),
            "expires_at": approval_token.expires,
            "lines": lines,
            "grand_total": grand_total,
            "approval_status": getattr(header, "approval_status", None),
            "approver_display_name": approver_display_name,
            "approver_email": approver_email,
            "requested_at": getattr(header, "requested_at", None),
            "approved_at": getattr(header, "approved_at", None),
            "approved_by": getattr(header, "approved_by", None),
            "approval_comments": getattr(header, "approval_comments", None),
        }

    def get_or_create_view_token(self, entity_id: str) -> str:
        """Get or create a reusable view token for this purchase request.

        New tokens are committed via an isolated session so this can safely be called
        from a serializer/read path without piggybacking the caller's pending writes
        into a premature commit.
        """
        row = (
            self.db.query(ViewToken)
            .filter(
                ViewToken.entity_type == "purchase_request",
                ViewToken.entity_id == entity_id,
            )
            .first()
        )
        if row:
            return row.token
        from app.database import SessionLocal

        token_value = secrets.token_urlsafe(32)
        isolated = SessionLocal()
        try:
            existing = (
                isolated.query(ViewToken)
                .filter(
                    ViewToken.entity_type == "purchase_request",
                    ViewToken.entity_id == entity_id,
                )
                .first()
            )
            if existing:
                return existing.token
            isolated.add(
                ViewToken(
                    entity_type="purchase_request",
                    entity_id=entity_id,
                    token=token_value,
                )
            )
            isolated.commit()
        except Exception:
            isolated.rollback()
            raise
        finally:
            isolated.close()
        return token_value

    def get_view_summary_by_token(self, token_value: str) -> dict:
        """Return read-only request summary for the given view token. No auth required.
        If the token is not a view token, also try approval token so approval links can be used to view."""
        view_token = (
            self.db.query(ViewToken)
            .filter(ViewToken.token == token_value)
            .first()
        )
        entity_id = None
        if view_token:
            entity_id = str(view_token.entity_id) if view_token.entity_id else None
        if not entity_id:
            approval_token = (
                self.db.query(ApprovalToken)
                .filter(ApprovalToken.token == token_value)
                .first()
            )
            if approval_token:
                entity_id = str(approval_token.entity_id) if approval_token.entity_id else None
        if not entity_id:
            raise handle_not_found("View link", "(invalid token)")
        header = self.get_request(entity_id)
        lines = []
        grand_total = None
        if getattr(header, "lines", None):
            for line in sorted(header.lines, key=lambda l: (l.sort_order if l.sort_order is not None else 999, getattr(l, "id", 0))):
                qty = line.quantity
                if qty is not None and hasattr(qty, "__float__"):
                    try:
                        qty = float(qty)
                    except (TypeError, ValueError):
                        pass
                up = getattr(line, "unit_price", None)
                tot = getattr(line, "total", None)
                if up is not None and hasattr(up, "__float__"):
                    try:
                        up = float(up)
                    except (TypeError, ValueError):
                        up = None
                if tot is not None and hasattr(tot, "__float__"):
                    try:
                        tot = float(tot)
                    except (TypeError, ValueError):
                        tot = None
                lines.append({
                    "item_code": line.item_code,
                    "quantity": qty,
                    "remark": line.remark,
                    "unit_price": up,
                    "total": tot,
                    "sort_order": line.sort_order,
                })
            if getattr(header, "request_type", None) == "sponsorship_form" and lines:
                try:
                    total_sum = Decimal("0")
                    for l in lines:
                        t = l.get("total")
                        if t is not None:
                            total_sum += Decimal(str(t))
                        else:
                            q, u = l.get("quantity"), l.get("unit_price")
                            if q is not None and u is not None:
                                total_sum += Decimal(str(q)) * Decimal(str(u))
                    grand_total = total_sum
                except (InvalidOperation, ValueError, TypeError):
                    grand_total = None
        approver_display_name = None
        approver_email = getattr(header, "approver_email", None) or None
        approver_user_id = getattr(header, "approver_user_id", None)
        if approver_user_id:
            user = self.db.query(User).filter(User.id == approver_user_id).first()
            if user:
                approver_display_name = (user.name and user.name.strip()) or user.email or None
                if not approver_email and user.email:
                    approver_email = user.email
        entity_type = view_token.entity_type if view_token else "purchase_request"
        return {
            "entity_type": entity_type,
            "entity_id": header.id,
            "request_number": header.request_number,
            "request_type": header.request_type,
            "customer_name": header.customer_name,
            "pic": header.pic,
            "project_title": header.project_title,
            "purpose": header.purpose,
            "delivery_address": getattr(header, "delivery_address", None),
            "total_project_value": getattr(header, "total_project_value", None),
            "total_project_value_text": getattr(header, "total_project_value_text", None),
            "sponsor_subject": getattr(header, "sponsor_subject", None),
            "sponsor_subject_other": getattr(header, "sponsor_subject_other", None),
            "requested_by": header.requested_by,
            "requested_by_contact_name": header.requested_by_contact_name,
            "request_date": getattr(header, "request_date", None),
            "requested_at": getattr(header, "requested_at", None),
            "submitted_at": getattr(header, "submitted_at", None),
            "created_at": getattr(header, "created_at", None),
            "expected_delivery_date": getattr(header, "expected_delivery_date", None),
            "expected_po_date": getattr(header, "expected_po_date", None),
            "expected_po_date_text": getattr(header, "expected_po_date_text", None),
            "expires_at": None,
            "lines": lines,
            "grand_total": grand_total,
            "approval_status": getattr(header, "approval_status", None),
            "approver_display_name": approver_display_name,
            "approver_email": approver_email,
            "approved_at": getattr(header, "approved_at", None),
            "approved_by": getattr(header, "approved_by", None),
            "approval_comments": getattr(header, "approval_comments", None),
        }

    def decide_approval(
        self,
        request_id: str,
        action: str,
        approved_by: Optional[str] = None,
        approval_comments: Optional[str] = None,
        actor_user_id: Optional[str] = None,
    ):
        """Authenticated in-system approve/reject (no token) - the form's Approve/
        Reject buttons. Same effect as the public link's ``submit_approval``: it
        runs the shared ``_apply_approval_decision``. Guards that the request is
        still awaiting a decision (approval_status == 'pending')."""
        header = self.get_request(request_id)
        appr = (getattr(header, "approval_status", None) or "").strip().lower()
        if appr in ("approved", "rejected"):
            raise handle_conflict(f"This request has already been {appr}.")
        if appr != "pending":
            raise handle_conflict(
                "This request is not pending approval. Send it for approval first."
            )
        return self._apply_approval_decision(
            header,
            action,
            approved_by=approved_by,
            approval_comments=approval_comments,
            actor_user_id=actor_user_id,
        )

    def submit_approval(
        self,
        token_value: str,
        action: str,
        approved_by: Optional[str] = None,
        approval_signature_ref: Optional[str] = None,
        approval_comments: Optional[str] = None,
    ):
        """Consume token and update purchase request with approval/rejection. Returns updated header."""
        approval_token = (
            self.db.query(ApprovalToken)
            .filter(ApprovalToken.token == token_value)
            .first()
        )
        if not approval_token:
            raise handle_not_found("Approval link", "(invalid token)")
        if approval_token.used_at is not None:
            raise handle_conflict("This approval link has already been used.")
        now = datetime.now(timezone.utc)
        if approval_token.expires.tzinfo is None:
            now = datetime.now()
        if approval_token.expires <= now:
            raise handle_conflict("This approval link has expired.")
        header = self.get_request(approval_token.entity_id)
        approval_token.used_at = datetime.utcnow()
        return self._apply_approval_decision(
            header,
            action,
            approved_by=approved_by,
            approval_signature_ref=approval_signature_ref,
            approval_comments=approval_comments,
        )

    def _apply_approval_decision(
        self,
        header,
        action: str,
        *,
        approved_by: Optional[str] = None,
        approval_signature_ref: Optional[str] = None,
        approval_comments: Optional[str] = None,
        actor_user_id: Optional[str] = None,
    ):
        """Apply an approve/reject decision and run ALL side effects (status,
        notifications, form-SLA event, approval automation). Shared by the public
        token flow (``submit_approval``) and the authenticated in-system Approve/
        Reject buttons so both behave identically. Does NOT touch approval tokens.
        """
        from app.services.error_handler import handle_validation_error

        if action not in ("approved", "rejected"):
            raise handle_conflict("action must be 'approved' or 'rejected'.")

        if action == "rejected":
            rejection_notes = (approval_comments or "").strip()
            if not rejection_notes:
                raise handle_validation_error("Rejection reason is required.")
            approval_comments = rejection_notes

        now = datetime.now(timezone.utc)
        header.approval_status = action
        # Advance the lifecycle status off "submitted" so every view reflects the
        # decision, not just approval_status.
        header.status = action
        header.approved_at = now
        header.approved_by = approved_by or header.approver_email or ""
        if action == "rejected":
            # Populate the dedicated rejecter id when it resolves to a CRM user
            # (in-system decision). External-email approvers (public link) have no
            # user row -> NULL, and the banner falls back to plain text (REJ-4).
            resolved_rejecter_id = None
            for candidate in (actor_user_id, getattr(header, "approver_user_id", None)):
                cid = (candidate or "").strip() if candidate else None
                if cid and self.db.query(User.id).filter(User.id == cid).first():
                    resolved_rejecter_id = cid
                    break
            header.rejected_by_id = resolved_rejecter_id
        header.approval_signature_ref = approval_signature_ref
        header.approval_comments = approval_comments
        self.db.commit()
        self.db.refresh(header)
        if action == "approved":
            requested_by_uid = getattr(header, "requested_approval_by_user_id", None)
            if requested_by_uid:
                try:
                    self._notify_requester_on_approved(header)
                except Exception as e:
                    logger.warning("Failed to notify requester for approved purchase request %s: %s", header.id, e)
            try:
                self._notify_contact_on_approval_approved(header)
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.warning(
                    "Failed to send Respond.io approval message for purchase request %s: %s",
                    header.id,
                    e,
                )
        elif action == "rejected":
            requested_by_uid = getattr(header, "requested_approval_by_user_id", None)
            if requested_by_uid:
                try:
                    self._notify_requester_on_rejected(header)
                except Exception as e:
                    logger.warning("Failed to notify requester for rejected purchase request %s: %s", header.id, e)
            try:
                self._notify_contact_on_approval_rejected(header)
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.warning(
                    "Failed to send Respond.io rejection message for purchase request %s: %s",
                    header.id,
                    e,
                )
        try:
            from app.services.form_sla_service import emit_form_event
            emit_form_event(
                self.db,
                getattr(header, "request_type", None) or "purchase_request",
                str(header.id),
                "approved" if action == "approved" else "approval_rejected",
                contact_id=getattr(header, "contact_id", None),
                actor_user_id=None,
            )
        except Exception as e:
            logger.warning("Form SLA emit '%s' failed for %s: %s", action, header.id, e)
        # Dispatch the approval automation AFTER the form-SLA event so the
        # customer-service stage tracker (and its assigned PIC) already exists
        # the "Assigned CS PIC" recipient option resolves from that tracker.
        if action == "approved":
            try:
                self._dispatch_approval_automation(header)
            except Exception:
                logger.exception(
                    "Automation dispatch on approved purchase request %s failed",
                    header.id,
                )
        return header
