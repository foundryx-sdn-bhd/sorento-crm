"""Portal submission revisions: policy, history and the void-and-restart transaction.

PLAN/UAC ``portal-submission-revisions``. The engine is generic on
``source_entity_type`` and knows nothing about stock inquiries (UAC J1): every
per-type fact lives in a :class:`RevisionAdapter`, and wiring a new form type is
one adapter plus one ``portal_revision_configs`` row (UAC J3).

Three adapters are registered and live from day one: ``stock_inquiry``,
``purchase_request``, ``sponsorship_form``. ``complaint`` ships with a config row
that is disabled, so turning it on later needs an adapter and a checkbox, never a
migration.

Two counters, never collapsed (UAC C0):

* ``version_no`` - every submitted version, for history ordering. Unique per entity.
* ``revision_no`` - contact-initiated revisions only, the cap counter. A reject then
  resubmit advances ``version_no`` and leaves ``revision_no`` alone.

Naming note: ``n8n_stock_inquiry_revise_webhook_url`` and
``ProcurementService._enqueue_public_revise_webhook_for_stock_inquiry`` are
UNRELATED to this module - they notify a contact that the office wants a revision.
Nothing here touches them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.portal import (
    PortalFormRevision,
    PortalRevisionConfig,
    PortalRevisionDraft,
    PortalToken,
)
from app.models.price_tag import PriceTagRequest
from app.models.procurement import PurchaseRequestHeader, PurchaseRequestLine, StockInquiry
from app.services.document_number import suffix_revision
from app.services.error_handler import handle_conflict, handle_not_found, handle_unprocessable

logger = logging.getLogger(__name__)

KIND_ORIGINAL = "original"
KIND_REVISION = "revision"
KIND_RESUBMISSION = "resubmission"

VOID_REASON_REVISED = "revised_by_contact"

REASON_MAX_LEN = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Adapters - the ONLY place a per-type fact is allowed to live (UAC J2)
# --------------------------------------------------------------------------- #


def _si_status_for_stage(stage_code: str) -> str:
    return {
        "project_sales": "pending_project_sales",
        "purchasing": "pending_purchasing",
    }.get(stage_code, "pending_project_sales")


def _request_status_for_stage(stage_code: str) -> str:
    # PR and SF share one lifecycle: draft -> submitted -> approved -> processed_by_cs.
    # `submitted` spans both the project-sales stage and the pending-approval stage
    # (pending approval is an approval_status sub-state, not a lifecycle status).
    return {
        "main": "submitted",
        "project_sales": "submitted",
        "project_sales_manager": "submitted",
        "customer_service": "approved",
    }.get(stage_code, "submitted")


def _serialize_request_lines(db: Session, row: Any) -> list[dict]:
    """Post-edit line items for a purchase request / sponsorship form.

    Read back from the table rather than off ``row.lines``: the revise path
    replaces lines with a bulk delete + inserts, so the relationship is stale
    until the flush this runs after.
    """
    lines = (
        db.query(PurchaseRequestLine)
        .filter(PurchaseRequestLine.purchase_request_id == str(row.id))
        .order_by(
            PurchaseRequestLine.sort_order.asc().nulls_last(),
            PurchaseRequestLine.id.asc(),
        )
        .all()
    )
    return [
        {
            "item_code": ln.item_code,
            "quantity": _jsonable(ln.quantity),
            "unit_price": _jsonable(ln.unit_price),
            "total": _jsonable(ln.total),
            "remark": ln.remark,
        }
        for ln in lines
    ]


@dataclass(frozen=True)
class RevisionAdapter:
    """Everything type-specific about revising one portal submission type."""

    source_entity_type: str
    model: type
    # Human label used in every blocked sentence the contact reads.
    label: str
    # Column holding the document number - the notification renders it suffixed.
    number_attr: str
    # Fields snapshotted BEYOND the type's portal edit whitelist. The whitelist
    # itself is read from PortalService._editable_fields so there is exactly one
    # field list per type (UAC AB1); these are the read-only bits of context that
    # make a snapshot readable on its own (document number, status, line items).
    snapshot_extra_fields: tuple[str, ...] = ()
    # The fields the PORTAL FORM of this type actually collects, in form order -
    # what the read-only full-form viewer renders. Separate from the edit whitelist
    # because purchase_request and sponsorship_form SHARE one whitelist tuple: read
    # off that, a sponsorship form would show `sales_type` / `expected_po_date` and a
    # purchase request would show `sponsor_subject` / `delivery_address`, neither of
    # which their form ever asked for. The requestor's human-name sibling
    # (salesperson / requested_by) stands in for the contact FK, which never renders.
    snapshot_form_fields: tuple[str, ...] = ()
    # Snapshot field -> lookup set_key. The stored value is the option CODE; the
    # viewer must read the option LABEL, resolved server side so both surfaces
    # cannot render it differently.
    lookup_fields: dict = field(default_factory=dict)
    # Snapshot fields holding a date, presented as DD/MM/YYYY. Named per type rather
    # than sniffed from the value: a remark that happens to look like an ISO date is
    # a remark, not a date.
    date_display_fields: tuple[str, ...] = ()
    # Child lines -> list[dict]. None for a type with no lines (stock inquiry).
    serialize_lines: Optional[Callable[[Session, Any], list[dict]]] = None
    # R3-1: how `revise()` applies the payload's lines to the row - the ONE
    # code path every line-carrying type shares (`(db, row, payload) -> None`).
    # PR/SF's own `PortalService._replace_request_lines_if_needed` moved onto
    # this field rather than staying a name `revise()` special-cased; price
    # tags run their own submit-shaped validation (set guard, duplicate
    # product, completeness) here too, since a revision is a save the office
    # already treats as complete. None for a type with no lines.
    apply_lines: Optional[Callable[[Session, Any, dict], None]] = None
    # UAC AB2: fields a revision may NEVER change. Subtracted from the payload,
    # never from the whitelist.
    frozen_on_revise: tuple[str, ...] = ()
    # UAC FB2/FB4: stage output the restart invalidates - snapshotted into the
    # revision row, then cleared on the entity.
    invalidated_on_revise: tuple[str, ...] = ()
    # UAC J4: the restart STAGE is declared once, on the config. The adapter only
    # translates that stage into the status the entity should carry.
    status_for_stage: Callable[[str], str] = _si_status_for_stage
    # Extra ownership filter (PR and SF share one table).
    request_type: Optional[str] = None
    revision_no_attr: str = "revision_no"
    last_revised_at_attr: str = "last_revised_at"
    # Statuses that are terminal for this type - only used to word the blocked
    # sentence, never to decide eligibility (that is allowed_statuses' job).
    terminal_statuses: tuple[str, ...] = ()
    field_labels: dict = field(default_factory=dict)


_SI_ADAPTER = RevisionAdapter(
    source_entity_type="stock_inquiry",
    model=StockInquiry,
    label="stock inquiry",
    number_attr="inquiry_number",
    snapshot_extra_fields=("inquiry_number", "status"),
    snapshot_form_fields=(
        "product_code",
        "item_description",
        "quantity",
        "delivery_date",
        "project_customer",
        "project_name",
        "salesperson",
        "remark",
        "additional_remark",
    ),
    lookup_fields={},
    date_display_fields=("delivery_date",),
    serialize_lines=None,  # a stock inquiry carries a single product, no lines
    # The requestor is the CS pin routing key: letting a revision change it silently
    # re-routes the inquiry to someone else's queue mid-life, which is a reassignment
    # disguised as an edit. `salesperson` is the label derived from the FK, so both go.
    frozen_on_revise=("salesperson", "salesperson_contact_id"),
    # Purchasing may already have answered (stock inquiry is revisable at `responded`
    # by explicit decision). That answer priced the superseded version.
    invalidated_on_revise=("purchasing_response", "last_responded_by", "last_responded_at"),
    status_for_stage=_si_status_for_stage,
    terminal_statuses=("rejected", "voided", "closed"),
    field_labels={
        "inquiry_number": "Inquiry number",
        "product_code": "Product code",
        "item_description": "Item description",
        "project_customer": "Project customer",
        "project_name": "Project name",
        "quantity": "Quantity",
        "delivery_date": "Delivery date",
        "remark": "Remark",
        "additional_remark": "Additional remark",
    },
)

_REQUEST_FROZEN = ("requested_by", "requested_by_contact_id")
# Approval is stage output exactly as the purchasing response is: a form revisable
# at `approved` carries an approval granted against the superseded version.
_REQUEST_INVALIDATED = (
    "approval_status",
    "approval_comments",
    "approved_at",
    "approved_by",
    "approval_signature_ref",
)
_REQUEST_LABELS = {
    "request_number": "Request number",
    "customer_name": "Customer name",
    "pic": "PIC",
    "project_title": "Project title",
    "purpose": "Purpose",
    "delivery_address": "Delivery address",
    "total_project_value": "Total project value",
    "total_project_value_text": "Total project value (text)",
    "sponsor_subject": "Sponsorship subject",
    "sponsor_subject_other": "Sponsorship subject (other)",
    "sales_type": "Sales type",
    "expected_delivery_date": "Expected delivery date",
    "expected_po_date": "Expected PO date",
    "external_reference": "Reference",
    "products": "Products",
}

def _apply_request_lines(kind: str) -> Callable[[Session, Any, dict], None]:
    """PR/SF's own line replace, moved onto `apply_lines` (R3-1) so `revise()`
    has ONE code path for every line-carrying type instead of special-casing
    these two by name."""

    def _apply(db: Session, row: Any, payload: dict) -> None:
        from app.services.portal_service import PortalService

        PortalService(db)._replace_request_lines_if_needed(  # noqa: SLF001
            kind, row, payload
        )

    return _apply


_PR_ADAPTER = RevisionAdapter(
    source_entity_type="purchase_request",
    model=PurchaseRequestHeader,
    label="purchase request",
    number_attr="request_number",
    snapshot_extra_fields=("request_number", "status"),
    snapshot_form_fields=(
        "customer_name",
        "pic",
        "project_title",
        "purpose",
        "sales_type",
        "expected_delivery_date",
        "expected_po_date",
        "requested_by",
        "external_reference",
    ),
    lookup_fields={"sales_type": "procurement_sales_type"},
    date_display_fields=("expected_delivery_date", "expected_po_date"),
    serialize_lines=_serialize_request_lines,
    apply_lines=_apply_request_lines("purchase_request"),
    frozen_on_revise=_REQUEST_FROZEN,
    invalidated_on_revise=_REQUEST_INVALIDATED,
    status_for_stage=_request_status_for_stage,
    request_type="purchase_request",
    terminal_statuses=("rejected", "voided", "closed", "processed_by_cs"),
    field_labels=_REQUEST_LABELS,
)

_SF_ADAPTER = RevisionAdapter(
    source_entity_type="sponsorship_form",
    model=PurchaseRequestHeader,
    label="sponsorship form",
    number_attr="request_number",
    snapshot_extra_fields=("request_number", "status"),
    snapshot_form_fields=(
        "customer_name",
        "pic",
        "delivery_address",
        "project_title",
        "total_project_value",
        "sponsor_subject",
        "sponsor_subject_other",
        "expected_delivery_date",
        "requested_by",
    ),
    lookup_fields={"sponsor_subject": "procurement_sponsor_subject"},
    date_display_fields=("expected_delivery_date",),
    serialize_lines=_serialize_request_lines,
    apply_lines=_apply_request_lines("sponsorship_form"),
    frozen_on_revise=_REQUEST_FROZEN,
    invalidated_on_revise=_REQUEST_INVALIDATED,
    status_for_stage=_request_status_for_stage,
    request_type="sponsorship_form",
    terminal_statuses=("rejected", "voided", "closed", "processed_by_cs"),
    field_labels=_REQUEST_LABELS,
)


def _serialize_price_tag_lines(db: Session, row: Any) -> list[dict]:
    """Post-edit line items for a price tag request, read back from the table
    (same reasoning as `_serialize_request_lines`: `apply_lines` replaces rows
    with a clear + re-add, so `row.lines` is stale until refreshed)."""
    from app.models.price_tag import PriceTagRequestLine

    lines = (
        db.query(PriceTagRequestLine)
        .filter(PriceTagRequestLine.request_id == row.id)
        .order_by(PriceTagRequestLine.sort_order.asc().nulls_last(), PriceTagRequestLine.id.asc())
        .all()
    )
    return [
        {
            "product_id": ln.product_id,
            "product_set_id": ln.product_set_id,
            "quantity": _jsonable(ln.quantity),
            "remarks": ln.remarks,
            # D1/D5 (this round): without these two, a revision's history
            # diff could not show a promotion pick or a hand-typed price
            # changing - the two facts this whole slice moved onto the line.
            "promotion_id": ln.promotion_id,
            "manual_sell_price": _jsonable(ln.manual_sell_price),
        }
        for ln in lines
    ]


def _convert_ptag_revise_line(raw: dict, idx: int) -> dict:
    """A revise payload line names a product/set by id and a quantity, the
    same shape the portal PUT/create routes already read - never a
    `line_type`, which those routes require. Derived here instead.

    D1/D5: `promotion_id` / `manual_sell_price`, when the raw line sends
    them, carry straight through - the salesperson's own line-level control
    over the price basis is not a create/update-only feature.
    `replace_lines` -> `_add_lines` validates promotion coverage exactly
    like create/update do (AC-S6-5, 422 naming the line), one gate, not a
    second copy of it here. Left OFF the dict (not defaulted to `None`)
    when the raw line does not carry the key at all, so
    `_apply_price_tag_lines`'s carry-over step below can tell "cleared"
    from "not sent" and fall back to the old line's own value for the
    latter, the same way it already does for `included_accessories` /
    `combo_id` / `parts`.

    R6 (security review): `raw` is validated through `PriceTagReviseLineIn`
    FIRST - the revise composer's payload never ran through pydantic at
    all, so a bad `manual_sell_price` ("abc", -5, 0, an absurd `1E+400`)
    reached `Decimal()` completely unvalidated. `.get(...)` below reads off
    the VALIDATED model, not the raw dict, so what flows into `converted`
    is already bound-checked.
    """
    from pydantic import ValidationError

    from app.schemas.price_tag import PriceTagReviseLineIn
    from app.services.error_handler import AppException

    try:
        validated = PriceTagReviseLineIn(**raw)
    except ValidationError as exc:
        # Review round 2: every refusal here read "this line's price could
        # not be saved", including a quantity of 0 - the composer put the
        # message on a line whose price was fine and the salesperson had
        # nothing to act on. Named from the field that actually failed.
        labels = {
            "quantity": "quantity",
            "manual_sell_price": "price",
            "promotion_id": "promotion",
        }
        failed = [
            labels[str(error["loc"][0])]
            for error in exc.errors()
            if error.get("loc") and str(error["loc"][0]) in labels
        ]
        field = failed[0] if failed else "details"
        raise AppException(
            status_code=422,
            message=f"This line's {field} could not be saved.",
            detail=f"line:{idx}",
            code="VALIDATION_ERROR",
        ) from exc
    clean = validated.model_dump()

    converted = (
        {
            "line_type": "product_set",
            "product_id": None,
            "product_set_id": clean.get("product_set_id"),
            "quantity": clean.get("quantity", 1),
            "remarks": clean.get("remarks"),
        }
        if clean.get("product_set_id")
        else {
            "line_type": "product",
            "product_id": clean.get("product_id"),
            "product_set_id": None,
            "quantity": clean.get("quantity", 1),
            "remarks": clean.get("remarks"),
        }
    )
    if "promotion_id" in raw:
        converted["promotion_id"] = clean.get("promotion_id")
    if "manual_sell_price" in raw:
        converted["manual_sell_price"] = clean.get("manual_sell_price")
    return converted


def _apply_price_tag_lines(db: Session, row: Any, payload: dict) -> None:
    """R3-1/AC-R3: the same completeness, set-guard and duplicate-product bar
    Submit and the post-submit PUT already enforce - a revision is a save the
    office already treats as complete. Validated BEFORE any mutation: once
    `PriceTagRequestService.replace_lines` clears the old rows it flushes
    immediately, so a guard raised only AFTER calling it would still have
    wiped the request's existing lines on a refused revision.
    """
    from app.services.dealer_kit import tag_data_service
    from app.services.error_handler import AppException
    from app.services.price_tag_request_service import PriceTagRequestService

    # Review round 3: `price_mode` reaches the row via bare-setattr
    # (`portal._apply_payload`) - create/update validate it through
    # pydantic's `Literal["list", "selling"]`, but a revise payload is a raw
    # dict with no schema, so nothing stopped a bad or null value
    # (`setattr(row, "price_mode", None)`) landing on a NOT NULL column.
    if "price_mode" in payload and payload.get("price_mode") not in ("list", "selling"):
        raise AppException(
            status_code=422,
            message="Price must be List price or Selling price.",
            detail="price_mode",
            code="VALIDATION_ERROR",
        )

    # `no_autoflush` wraps every guard below, not just the promotion one:
    # `portal._apply_payload` (which runs before this callable) already left
    # `debtor_name` / `promotion_id` / `price_mode` dirty in-memory via bare
    # setattr, and the FIRST query any guard below issues (the promotion
    # lookup, `_ala_carte_offender`'s product lookup, even the lazy-load on
    # `row.lines`) would autoflush that dirty state to Postgres before a
    # later guard gets a chance to refuse it - "nothing written" on a refusal
    # has to mean nothing FLUSHED, not just nothing committed.
    with db.no_autoflush:
        # D1 (S6): the promotion moved to the LINE - there is no header
        # `promotion_id` left on the row for a revise payload to set, so the
        # old per-revision audience check (Gap A, security review of S10)
        # has nothing left to guard here. A line's own `promotion_id` /
        # `manual_sell_price` go through `_convert_ptag_revise_line` and
        # `_add_lines`' AC-S6-4/S6-5 checks inside `replace_lines` below -
        # the same gate create/update already run, so a revise cannot accept
        # something either of those would refuse.
        if "products" not in payload:
            # No lines in this revision - still has to clear the same bar
            # with whatever the row already carries. `require_debtor=True`:
            # review round 3 - a revision CAN change the dealer
            # (`debtor_name` is on the portal edit whitelist, applied above
            # via `portal._apply_payload`), so the post-submit PUT's own
            # "debtor already locked in" argument for `require_debtor=False`
            # does not hold here.
            PriceTagRequestService.validate_submittable(row, require_debtor=True)
            return
        converted = [
            _convert_ptag_revise_line(d, idx)
            for idx, d in enumerate(payload.get("products") or [])
        ]
        if not converted:
            raise AppException(
                status_code=422,
                message="This request needs at least one line before it can be submitted.",
                detail="lines",
                code="SUBMIT_INCOMPLETE",
            )
        # The set guard used to refuse here with a 422. It is retired (D2,
        # AC-S2-7): a revision is no more refusable for a package reason than a
        # submit is, or a salesperson could send a bare cabinet and then be
        # blocked from correcting the request holding it. The warning is stamped
        # after `replace_lines` below, where the new rows exist to read.
        PriceTagRequestService._raise_on_duplicate_line(db, converted)  # noqa: SLF001
        # Review round 3: the same completeness bar the no-products branch
        # above runs - checked here, BEFORE `replace_lines` touches anything,
        # since `require_debtor=True` (a revision can change the dealer) has
        # to refuse before that call's own internal `db.flush()` calls, or a
        # refused revision would still have wiped the request's existing
        # lines and blanked its debtor.
        PriceTagRequestService.validate_submittable(row, require_debtor=True)
        # Gap D (security review of S10): the revise composer's payload line
        # has no field for `alternatives` / `included_accessories` - same
        # reason it has none for `marketing_price_override` (review round
        # 2's own carry-over) - so a revise silently wiped both back to `[]`
        # / `None`. Carried from the OLD rows, keyed by product/set, same
        # mechanism. D1/D5: a line's `promotion_id` / `manual_sell_price`
        # carry the same way - a revision that only touches a remark must
        # not silently drop a promotion or a hand-typed price the
        # salesperson had already set on this line.
        old_by_key = {
            (old.product_id, old.product_set_id): (
                old.included_accessories,
                old.combo_id,
                [
                    {
                        "product_id": part.product_id,
                        "role": part.role,
                        "candidates": list(part.candidates or []),
                    }
                    for part in sorted(
                        old.parts or [], key=lambda p: (p.sort_order or 0, p.id)
                    )
                ],
                old.promotion_id,
                old.manual_sell_price,
            )
            for old in row.lines
        }
        for line in converted:
            key = (line.get("product_id"), line.get("product_set_id"))
            if key in old_by_key:
                (
                    included_accessories,
                    combo_id,
                    parts,
                    promotion_id,
                    manual_sell_price,
                ) = old_by_key[key]
                line["included_accessories"] = included_accessories
                # Same carry-over reason as `included_accessories` above, now
                # for the package: the revise composer has no field for it, so
                # a revision that only changes a remark must not throw away the
                # parts the salesperson asked for.
                #
                # These ids are re-validated by `_add_lines` on the way back in
                # (`_resolve_combo_id` / `_add_line_parts`, security review B1),
                # the same gate the create path takes - `_convert_ptag_revise_line`
                # never lets a revise payload supply them directly, so the only
                # source is this carry-over, and it is checked anyway.
                line.setdefault("combo_id", combo_id)
                line.setdefault("parts", parts)
                # `_convert_ptag_revise_line` only sets these keys when the raw
                # payload line sent them explicitly (including an explicit
                # `null`, which clears one) - `setdefault` therefore only
                # falls back to the old value when the key is absent, never
                # overriding a deliberate clear.
                line.setdefault("promotion_id", promotion_id)
                line.setdefault("manual_sell_price", manual_sell_price)
        # Security review (this round): a revision is a PORTAL CONTACT
        # writing, exactly like create/update - `replace_lines` with no
        # viewer falls back to `staff_viewer()` (`is_internal_copy=True`),
        # which `_may_see_offer` never gates, so a promotion outside this
        # contact's own audience passed AC-S6-5's check here even though the
        # identical create/update payload would have been refused. The SAME
        # viewer create/update already pass (`tag_data_service.contact_viewer`).
        viewer = tag_data_service.contact_viewer(db, row.contact_id)
    # Security review (this round, M2): `_covering_promotions`' query for a
    # line's own promotion is scoped through the ORM's company filter, so
    # without a scope set here it ran UNSCOPED - the same class of gap R5's
    # own regression test already covers under the request's SINGLE company
    # (green because the request row itself is company-scoped elsewhere),
    # but a genuinely multi-company caller reaching this code path with no
    # scope set at all would see every company's promotions. Scoped exactly
    # like create (`portal_create_price_tag_request`) and update
    # (`portal_update_price_tag_request`) already are.
    from app.models.base import company_scope

    with company_scope(db, frozenset({row.company_id})):
        PriceTagRequestService.replace_lines(db, row, converted, viewer=viewer)
    # Where the retired set guard ran, the D2 warning is stamped instead - on the
    # rows that now exist, so submit and revise cannot answer differently.
    PriceTagRequestService.apply_package_warnings(db, row)
    db.flush()


_PTAG_ADAPTER = RevisionAdapter(
    source_entity_type="price_tag_request",
    model=PriceTagRequest,
    label="price tag request",
    number_attr="doc_number",
    snapshot_extra_fields=("doc_number", "status"),
    # D1 (S6): `promotion_id` is a LINE fact now, not a header snapshot field.
    snapshot_form_fields=(
        "debtor_code",
        "debtor_name",
        "needed_by_date",
        "notes",
        "price_mode",
    ),
    lookup_fields={},
    date_display_fields=("needed_by_date",),
    serialize_lines=_serialize_price_tag_lines,
    apply_lines=_apply_price_tag_lines,
    frozen_on_revise=(),
    invalidated_on_revise=(),
    status_for_stage=lambda _stage_code: "new",
    terminal_statuses=("approved", "ready", "void"),
    field_labels={
        "doc_number": "Request number",
        "debtor_code": "Dealer code",
        "debtor_name": "Dealer",
        "promotion_id": "Promotion",
        "needed_by_date": "Need by",
        "notes": "Notes",
        "price_mode": "Price",
        "products": "Products",
    },
)

ADAPTERS: dict[str, RevisionAdapter] = {
    _SI_ADAPTER.source_entity_type: _SI_ADAPTER,
    _PR_ADAPTER.source_entity_type: _PR_ADAPTER,
    _SF_ADAPTER.source_entity_type: _SF_ADAPTER,
    _PTAG_ADAPTER.source_entity_type: _PTAG_ADAPTER,
}


def get_adapter(source_entity_type: str) -> Optional[RevisionAdapter]:
    return ADAPTERS.get((source_entity_type or "").strip().lower())


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RevisionPolicy:
    enabled: bool
    allowed: bool
    used: int
    max: int
    remaining: int
    blocked_reason: Optional[str]
    # Where the flow restarts, in words, for the confirm dialog (UAC E1/E1a). None
    # when there is nothing to name and the generic sentence has to stand.
    restart_stage_label: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "allowed": self.allowed,
            "used": self.used,
            "max": self.max,
            "remaining": self.remaining,
            "blocked_reason": self.blocked_reason,
            "restart_stage_label": self.restart_stage_label,
        }


_DISABLED_SENTENCE = "This form cannot be revised."


def _jsonable(value: Any) -> Any:
    """JSONB-safe rendering of a column value. Decimals and dates become strings so
    a snapshot compares equal across a round trip through the database."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _humanize(field_name: str) -> str:
    return field_name.replace("_", " ").strip().capitalize()


def _format_display_date(value: Any) -> Optional[str]:
    """A snapshotted date (``YYYY-MM-DD``) as the form shows it: ``DD/MM/YYYY``.

    ``None`` for anything that is not one: several of these columns are free text
    (a stock inquiry's ``delivery_date`` accepts "ASAP"), and a value that is not a
    date must render exactly as the contact typed it.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return None


class PortalRevisionService:
    """Policy resolution, revision history, and the revise transaction."""

    def __init__(self, db: Session):
        self.db = db

    # ---------------- config / policy ----------------

    def get_config(self, source_entity_type: str) -> Optional[PortalRevisionConfig]:
        return (
            self.db.query(PortalRevisionConfig)
            .filter(PortalRevisionConfig.source_entity_type == source_entity_type)
            .first()
        )

    def _global_settings(self) -> tuple[bool, int]:
        """(kill switch, fallback cap) from the ``system_settings`` singleton."""
        from app.models.user import SystemSetting

        try:
            row = self.db.query(SystemSetting).first()
        except Exception:  # noqa: BLE001 - partial schema in a scratch DB
            self.db.rollback()
            row = None
        if row is None:
            return True, 2
        enabled = getattr(row, "portal_revisions_enabled", True)
        cap = getattr(row, "portal_max_revisions", 2)
        return (True if enabled is None else bool(enabled), 2 if cap is None else int(cap))

    def type_policy(self, source_entity_type: str) -> tuple[bool, int]:
        """(is this type revisable at all, cap) - the part of the policy that does
        not depend on any one submission (UAC A3/A4).

        Fails closed: no adapter, no config row, the config disabled, the global
        kill switch off, or a cap of zero all mean off. Split out so the office
        "which tabs exist" map and the per-submission policy are ONE derivation and
        cannot drift apart.
        """
        adapter = get_adapter(source_entity_type)
        config = self.get_config(source_entity_type)
        global_enabled, global_cap = self._global_settings()

        if adapter is None or config is None or not bool(config.is_enabled) or not global_enabled:
            return False, 0

        cap = config.max_revisions if config.max_revisions is not None else global_cap
        cap = max(0, int(cap))
        # A cap of zero turns revisions off for this type whatever is_enabled says
        # (UAC A4), and reads to the contact as "not available" rather than "used up".
        return cap > 0, cap

    def enabled_type_map(self) -> dict[str, bool]:
        """`{type: effective enabled}` for every portal submission type.

        What the office detail pages read to decide whether the Revisions tab
        exists. Names every supported type, so the caller never has to tell an
        absent key from a false one.
        """
        from app.services.portal_service import SUPPORTED_TYPES

        return {kind: self.type_policy(kind)[0] for kind in SUPPORTED_TYPES}

    def resolve_policy(self, source_entity_type: str, row: Any) -> RevisionPolicy:
        """Effective revision policy for one submission (UAC A3/A4, B1, B2).

        Fails closed: no adapter or no config row means disabled.
        """
        adapter = get_adapter(source_entity_type)
        config = self.get_config(source_entity_type)
        type_enabled, cap = self.type_policy(source_entity_type)

        used = int(getattr(row, "revision_no", 0) or 0) if row is not None else 0

        if not type_enabled or adapter is None or config is None:
            return RevisionPolicy(
                enabled=False,
                allowed=False,
                used=used,
                max=0,
                remaining=0,
                blocked_reason=_DISABLED_SENTENCE,
            )

        remaining = max(0, cap - used)

        label = adapter.label
        status = (getattr(row, "status", None) or "").strip().lower()
        allowed_statuses = [
            str(s).strip().lower() for s in (config.allowed_statuses or []) if str(s).strip()
        ]

        blocked: Optional[str] = None
        if getattr(row, "portal_draft_at", None) is not None:
            # A draft is EDITABLE, which is the existing path - it is not revisable.
            blocked = f"This {label} is still a draft. Edit and submit it instead."
        elif getattr(row, "voided_at", None) is not None:
            blocked = f"This {label} has been voided and can no longer be revised."
        elif status not in allowed_statuses:
            if status in adapter.terminal_statuses:
                blocked = f"This {label} is {status.replace('_', ' ')} and can no longer be revised."
            else:
                blocked = (
                    f"This {label} cannot be revised while it is "
                    f"{status.replace('_', ' ') or 'in its current state'}."
                )
        elif remaining <= 0:
            blocked = f"You have used all {cap} revisions."

        return RevisionPolicy(
            enabled=True,
            allowed=blocked is None,
            used=used,
            max=cap,
            remaining=remaining,
            blocked_reason=blocked,
            # Named from config, never hardcoded (UAC E1a): the confirm dialog has to
            # say where THIS type goes back to, and three of the four do not route to
            # purchasing.
            restart_stage_label=self.restart_stage_label(source_entity_type),
        )

    def policy_for(self, source_entity_type: str, submission_id: str) -> RevisionPolicy:
        """Policy for an id, without the caller holding the ORM row."""
        adapter = get_adapter(source_entity_type)
        if adapter is None:
            return RevisionPolicy(False, False, 0, 0, 0, _DISABLED_SENTENCE)
        row = self.db.query(adapter.model).filter(adapter.model.id == str(submission_id)).first()
        if row is None:
            return RevisionPolicy(False, False, 0, 0, 0, _DISABLED_SENTENCE)
        return self.resolve_policy(source_entity_type, row)

    # ---------------- history ----------------

    def _next_version_no(self, source_entity_type: str, source_entity_id: str) -> int:
        current = (
            self.db.query(func.max(PortalFormRevision.version_no))
            .filter(
                PortalFormRevision.source_entity_type == source_entity_type,
                PortalFormRevision.source_entity_id == str(source_entity_id),
            )
            .scalar()
        )
        return 0 if current is None else int(current) + 1

    def has_history(self, source_entity_type: str, source_entity_id: str) -> bool:
        return (
            self.db.query(PortalFormRevision.id)
            .filter(
                PortalFormRevision.source_entity_type == source_entity_type,
                PortalFormRevision.source_entity_id == str(source_entity_id),
            )
            .first()
            is not None
        )

    def record_initial(
        self,
        source_entity_type: str,
        row: Any,
        contact_id: Optional[str] = None,
        *,
        is_reconstructed: bool = False,
    ) -> Optional[PortalFormRevision]:
        """Write the ``revision_no = 0`` history row (UAC G1). Idempotent.

        Called from ``PortalService.submit_draft`` on the transition out of draft,
        and lazily from :meth:`revise` for submissions that predate the feature -
        those are flagged ``is_reconstructed`` and labelled "Original
        (reconstructed)" so nobody reads them as a verbatim record (UAC G2).
        """
        adapter = get_adapter(source_entity_type)
        if adapter is None:
            return None
        if self.has_history(source_entity_type, str(row.id)):
            return None
        return self._write_revision_row(
            adapter,
            row,
            kind=KIND_ORIGINAL,
            version_no=0,
            revision_no=0,
            reason=None,
            contact_id=contact_id,
            is_reconstructed=is_reconstructed,
        )

    def record_resubmission(
        self,
        source_entity_type: str,
        row: Any,
        contact_id: Optional[str] = None,
        *,
        reason: Optional[str] = None,
    ) -> Optional[PortalFormRevision]:
        """History row for a resubmit after an office rejection (UAC C4).

        ``version_no`` advances, ``revision_no`` does NOT - an office reject never
        burns one of the contact's revisions (UAC C1). Without this row the
        contact's history silently skips a version they actually sent.
        """
        adapter = get_adapter(source_entity_type)
        if adapter is None:
            return None
        # Keep the lineage complete: a legacy row resubmitted before it ever had a
        # history gets its (reconstructed) original first, so version 1 is not an orphan.
        self.record_initial(source_entity_type, row, contact_id, is_reconstructed=True)
        return self._write_revision_row(
            adapter,
            row,
            kind=KIND_RESUBMISSION,
            version_no=self._next_version_no(source_entity_type, str(row.id)),
            revision_no=int(getattr(row, adapter.revision_no_attr, 0) or 0),
            reason=(reason or "").strip() or None,
            contact_id=contact_id,
        )

    def record_submission(
        self,
        source_entity_type: str,
        row: Any,
        contact_id: Optional[str] = None,
        *,
        is_resubmission: bool = False,
        reason: Optional[str] = None,
    ) -> Optional[PortalFormRevision]:
        """One entry point for ``submit_draft``: original on first submit, a
        ``resubmission`` row on the reject-then-resubmit path."""
        if is_resubmission:
            return self.record_resubmission(source_entity_type, row, contact_id, reason=reason)
        return self.record_initial(source_entity_type, row, contact_id)

    def _write_revision_row(
        self,
        adapter: RevisionAdapter,
        row: Any,
        *,
        kind: str,
        version_no: int,
        revision_no: int,
        reason: Optional[str],
        contact_id: Optional[str],
        is_reconstructed: bool = False,
        invalidated: Optional[dict] = None,
        voided_stages: Optional[list[dict]] = None,
    ) -> PortalFormRevision:
        # Every voided stage is stored, and the FIRST (newest) also lands in the two
        # scalar columns so the single-stage readers stay exactly as they were.
        stages = list(voided_stages or [])
        primary = stages[0] if stages else {}
        revision = PortalFormRevision(
            source_entity_type=adapter.source_entity_type,
            source_entity_id=str(row.id),
            version_no=version_no,
            revision_no=revision_no,
            kind=kind,
            reason=reason,
            invalidated_json=invalidated or None,
            snapshot_json=self.build_snapshot(adapter, row),
            attachments_json=self.snapshot_attachments(adapter, row),
            voided_stage_code=primary.get("stage_code"),
            voided_assignee_user_id=primary.get("assignee_user_id"),
            voided_stages_json=stages or None,
            submitted_at=_utcnow(),
            submitted_by_contact_id=str(contact_id) if contact_id else None,
            is_reconstructed=is_reconstructed,
        )
        self.db.add(revision)
        self.db.flush()
        return revision

    # ---------------- snapshot ----------------

    def editable_fields(self, source_entity_type: str) -> tuple[str, ...]:
        """The type's portal edit whitelist. ONE list, owned by PortalService."""
        from app.services.portal_service import PortalService

        return PortalService(self.db)._editable_fields(source_entity_type)  # noqa: SLF001

    def build_snapshot(self, adapter: RevisionAdapter, row: Any) -> dict:
        """Post-edit field values, including line items (UAC G1)."""
        snapshot: dict[str, Any] = {}
        for name in self.editable_fields(adapter.source_entity_type):
            snapshot[name] = _jsonable(getattr(row, name, None))
        for name in adapter.snapshot_extra_fields:
            snapshot[name] = _jsonable(getattr(row, name, None))
        if adapter.serialize_lines is not None:
            snapshot["products"] = adapter.serialize_lines(self.db, row)
        return snapshot

    def snapshot_attachments(self, adapter: RevisionAdapter, row: Any) -> list[dict]:
        """The attachment list as it stands at this version (UAC G4).

        Keyed on ``attachment_id`` first: a file removed by a later revision loses
        its ``EntityAttachmentLink``, and a link-keyed history would 404 on exactly
        the files this snapshot exists to show (UAC G6/I2a).
        """
        from app.models.entity_attachment import EntityAttachmentLink
        from app.models.resources import Attachment

        entity_type = _attachment_entity_type(adapter.source_entity_type)
        rows = (
            self.db.query(EntityAttachmentLink, Attachment)
            .join(Attachment, Attachment.id == EntityAttachmentLink.attachment_id)
            .filter(
                EntityAttachmentLink.entity_type == entity_type,
                EntityAttachmentLink.entity_id == str(row.id),
            )
            .order_by(
                EntityAttachmentLink.sort_order.asc().nulls_last(),
                EntityAttachmentLink.created_at.asc(),
            )
            .all()
        )
        return [
            {
                "attachment_id": str(att.id),
                "link_id": str(link.id),
                "filename": att.original_filename,
                "size": att.file_size_bytes,
                "mime": getattr(att, "mime_type", None),
            }
            for link, att in rows
        ]

    def attachment_ids_in_history(
        self, source_entity_type: str, source_entity_id: str
    ) -> set[str]:
        """Every attachment id referenced by any snapshot of this submission.

        Backs the portal preview/download authorisation for a file that an earlier
        revision carried and the current version no longer links (UAC I2a).
        """
        out: set[str] = set()
        rows = (
            self.db.query(PortalFormRevision.attachments_json)
            .filter(
                PortalFormRevision.source_entity_type == source_entity_type,
                PortalFormRevision.source_entity_id == str(source_entity_id),
            )
            .all()
        )
        for (payload,) in rows:
            for item in payload or []:
                aid = (item or {}).get("attachment_id")
                if aid:
                    out.add(str(aid))
        return out

    # ---------------- read ----------------

    def list_revisions(self, source_entity_type: str, source_entity_id: str) -> list[dict]:
        """Full lineage oldest-first, each entry carrying what changed since the
        version before it (UAC G4/H3). Read-only on both sides (UAC G5/H5)."""
        adapter = get_adapter(source_entity_type)
        rows = (
            self.db.query(PortalFormRevision)
            .filter(
                PortalFormRevision.source_entity_type == source_entity_type,
                PortalFormRevision.source_entity_id == str(source_entity_id),
            )
            .order_by(PortalFormRevision.version_no.asc())
            .all()
        )
        if not rows:
            return []

        names = self._contact_names({r.submitted_by_contact_id for r in rows})
        # One lookup for every handler named anywhere in the lineage: the scalar
        # column AND every entry of the per-revision stage list.
        assignees = self._user_names(
            {r.voided_assignee_user_id for r in rows}
            | {
                (entry or {}).get("assignee_user_id")
                for r in rows
                for entry in (r.voided_stages_json or [])
            }
        )
        urls = self._attachment_urls(rows)
        # Option labels for the type's lookup-bound fields: one query for the whole
        # lineage, never one per version.
        lookup_labels = self._lookup_labels(adapter)

        out: list[dict] = []
        previous: Optional[dict] = None
        for row in rows:
            snapshot = row.snapshot_json or {}
            out.append(
                {
                    "id": str(row.id),
                    "version_no": row.version_no,
                    "revision_no": row.revision_no,
                    "kind": row.kind,
                    "label": _revision_label(row),
                    "reason": row.reason,
                    "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                    "submitted_by": names.get(str(row.submitted_by_contact_id or "")),
                    "is_reconstructed": bool(row.is_reconstructed),
                    "snapshot": snapshot,
                    "attachments": [
                        dict(item, url=urls.get(str((item or {}).get("attachment_id") or "")))
                        for item in (row.attachments_json or [])
                    ],
                    "invalidated": row.invalidated_json or None,
                    # The primary (newest) voided stage, unchanged - both timelines
                    # render these two fields today.
                    "voided_stage_code": row.voided_stage_code,
                    "voided_assignee_name": assignees.get(
                        str(row.voided_assignee_user_id or "")
                    ),
                    # And every stage this revision stopped, newest first, so a
                    # multi-stage cancellation is not under-reported (UAC H3).
                    "voided_stages": [
                        {
                            "stage_code": (entry or {}).get("stage_code"),
                            "assignee_name": assignees.get(
                                str((entry or {}).get("assignee_user_id") or "")
                            ),
                        }
                        for entry in (row.voided_stages_json or [])
                    ],
                    "changes": self._diff(adapter, previous, snapshot),
                    # The whole form as it stood at this version, labeled and in
                    # form order, so either side can render it without a second
                    # field list that would drift from the adapter's.
                    "snapshot_fields": self._snapshot_fields(
                        adapter, snapshot, lookup_labels
                    ),
                }
            )
            previous = snapshot
        return out

    def _lookup_labels(self, adapter: Optional[RevisionAdapter]) -> dict:
        """``{field: {lower(option value): option label}}`` for this type's
        lookup-bound fields.

        Read ONCE per history read, not per entry: a lineage is several versions
        long and every one of them would otherwise re-query the same handful of
        options. Inactive options are deliberately included - a superseded version
        may name an option that has since been retired, and history has to render
        the label it was chosen under rather than falling back to the raw code.
        """
        if adapter is None:
            return {}
        set_keys = {key for key in (adapter.lookup_fields or {}).values() if key}
        if not set_keys:
            return {}

        from app.models.lookup import LookupOption, LookupSet

        try:
            rows = (
                self.db.query(LookupSet.set_key, LookupOption.value, LookupOption.label)
                .join(LookupOption, LookupOption.set_id == LookupSet.id)
                .filter(LookupSet.set_key.in_(set_keys))
                .all()
            )
        except Exception:  # noqa: BLE001 - a missing lookup table is not a 500
            self.db.rollback()
            return {}

        by_set: dict[str, dict[str, str]] = {}
        for set_key, value, label in rows:
            by_set.setdefault(str(set_key), {})[str(value or "").strip().lower()] = label
        return {
            name: by_set.get(set_key, {})
            for name, set_key in (adapter.lookup_fields or {}).items()
        }

    def _snapshot_fields(
        self,
        adapter: Optional[RevisionAdapter],
        snapshot: dict,
        lookup_labels: Optional[dict] = None,
    ) -> list[dict]:
        """One snapshot as an ordered ``[{field, label, value, display}]`` list -
        the full form at that version, for the read-only revision viewer (both
        sides).

        Built at read time from the stored snapshot, never from the live row: the
        viewer must show the form AS IT WAS. The field list is the type's OWN
        portal form order (``snapshot_form_fields``), not the shared edit
        whitelist, so a sponsorship form never shows purchase-request fields.
        Contact FK columns (``*_contact_id``) never appear - the human name sits
        in its sibling field and a raw id never reaches a screen.

        ``status`` is stored but NOT rendered: the snapshot is written before the
        post-revision status restart is applied, so it holds the superseded
        version's status and would read as wrong information under an "as it was"
        heading.

        ``display`` is the human presentation of ``value`` (a lookup label, a
        DD/MM/YYYY date), or ``None`` when the raw value already reads correctly.
        Resolved here so both surfaces render the same string. ``products``
        renders last, as the line items live at the bottom of the form itself.
        """
        if adapter is None:
            return []
        labels = lookup_labels or {}
        fields: list[dict] = []

        def _display(name: str, value: Any) -> Optional[str]:
            if name in (adapter.lookup_fields or {}):
                key = str(value or "").strip().lower()
                return labels.get(name, {}).get(key) if key else None
            if name in adapter.date_display_fields:
                return _format_display_date(value)
            return None

        def _append(name: str) -> None:
            value = snapshot.get(name)
            fields.append(
                {
                    "field": name,
                    "label": adapter.field_labels.get(name) or _humanize(name),
                    "value": value,
                    "display": _display(name, value),
                }
            )

        _append(adapter.number_attr)
        for name in adapter.snapshot_form_fields:
            if name.endswith("_contact_id"):
                continue
            _append(name)
        if adapter.serialize_lines is not None:
            _append("products")
        return fields

    def _attachment_urls(self, rows: list) -> dict:
        """``{attachment_id: signed url}`` for every file any snapshot references.

        The snapshot deliberately stores NO url (UAC I2a): a signed url expires, and
        a stored one would be dead the moment history is read. It is resolved here,
        at read time, against the attachment row - including for a file whose
        ``EntityAttachmentLink`` a later revision removed, which is exactly the case
        history exists to serve.

        Signed per row through ``storage_router``: the provider (``s3`` / ``r2``) is a
        per-attachment column, so one global signer would break every migrated row.
        One query for the whole lineage, never one per entry.
        """
        from app.models.resources import Attachment
        from app.services.storage_router import resolve_signed_url

        ids: set[str] = set()
        for row in rows:
            for item in getattr(row, "attachments_json", None) or []:
                aid = (item or {}).get("attachment_id")
                if aid:
                    ids.add(str(aid))
        if not ids:
            return {}

        out: dict[str, Optional[str]] = {}
        attachments = (
            self.db.query(Attachment).filter(Attachment.id.in_(ids)).all()
        )
        for att in attachments:
            file_path = getattr(att, "file_path", None)
            if not file_path:
                out[str(att.id)] = None
                continue
            try:
                out[str(att.id)] = resolve_signed_url(
                    file_path, provider=getattr(att, "storage_provider", None)
                ) or file_path
            except Exception as exc:  # noqa: BLE001 - a signer outage is not a 500
                logger.warning(
                    "Revision history: signing attachment %s failed: %s", att.id, exc
                )
                out[str(att.id)] = file_path
        # An attachment row that no longer exists resolves to None rather than being
        # dropped: the entry still renders with its filename and the fallback card.
        for aid in ids:
            out.setdefault(aid, None)
        return out

    def _contact_names(self, contact_ids: set) -> dict:
        from app.models.access import RespondContact
        from app.services.portal_service import PortalService

        ids = {str(c) for c in contact_ids if c}
        if not ids:
            return {}
        rows = self.db.query(RespondContact).filter(RespondContact.id.in_(ids)).all()
        return {c.id: PortalService._contact_display_name(c) for c in rows}  # noqa: SLF001

    def _user_names(self, user_ids: set) -> dict:
        from app.models.user import User

        ids = {str(u) for u in user_ids if u}
        if not ids:
            return {}
        rows = self.db.query(User).filter(User.id.in_(ids)).all()
        return {u.id: (u.name or u.email) for u in rows}

    def _diff(
        self,
        adapter: Optional[RevisionAdapter],
        previous: Optional[dict],
        current: dict,
    ) -> list[dict]:
        """Field-level changes vs the previous version.

        Scoped to the contact-editable fields plus line items: the extras carried
        for context (document number, status) move on their own and would read as
        noise. Line reordering is deliberately not diffed (UAC K).
        """
        if previous is None or adapter is None:
            return []
        changes: list[dict] = []
        for name in self.editable_fields(adapter.source_entity_type):
            if name == "customer_id":
                # An id is never shown (no UUID in the UI); a re-pick reads as the
                # Customer name change. The snapshot still carries it.
                continue
            before, after = previous.get(name), current.get(name)
            if before == after:
                continue
            changes.append(
                {
                    "field": name,
                    "label": adapter.field_labels.get(name) or _humanize(name),
                    "from": before,
                    "to": after,
                }
            )
        changes.extend(_diff_lines(previous.get("products"), current.get("products")))
        return changes

    # ---------------- revise ----------------

    def fetch_owned(self, token: PortalToken, source_entity_type: str, submission_id: str) -> Any:
        """The ORM row, scoped to the token's contact. 403 when it belongs to another
        contact, 404 when it does not exist - same distinction the rest of the portal
        makes, via the same helper."""
        from app.services.portal_service import PortalService
        from app.services.uuid_path_param import validate_uuid_path

        adapter = get_adapter(source_entity_type)
        if adapter is None:
            raise handle_unprocessable(_DISABLED_SENTENCE)
        # Gap E (security review of S10): `adapter.model.id` is a UUID column -
        # a malformed value reaching the query below 500s from Postgres
        # refusing the comparison instead of answering the usual 404.
        submission_id = validate_uuid_path(submission_id, resource=adapter.label.title())
        if source_entity_type == "price_tag_request":
            # Gap B (security review of S10): price_tag_request is a GATED
            # kind (GATED_LANDING_KINDS) - a contact's grant can be revoked -
            # unlike the other three adapters here, which are always visible.
            # `revise` / `save_draft` / `discard_draft` (via this one choke
            # point) never re-checked it, so a revoked contact could still
            # act on their own old request. Mirrors `_assert_visible`, which
            # the rest of the price tag surface already runs.
            from app.api.v1.public.portal_price_tag import _assert_visible

            _assert_visible(self.db, token.contact_id)
        query = self.db.query(adapter.model).filter(
            adapter.model.id == str(submission_id),
            adapter.model.contact_id == token.contact_id,
        )
        # price_tag_request has no space_id column - ownership there has
        # always been contact_id alone (portal_price_tag.py's own
        # _require_own_request never checks it either).
        if hasattr(adapter.model, "space_id"):
            query = query.filter(adapter.model.space_id == token.space_id)
        if adapter.request_type is not None:
            query = query.filter(adapter.model.request_type == adapter.request_type)
        row = query.first()
        if row is None:
            if source_entity_type == "price_tag_request":
                # price_tag_request's own convention (portal_price_tag.
                # _require_own_request): always 404, never the generic
                # kinds' 403 "log in as the owner" - no owner hint leak.
                raise handle_not_found(adapter.label.title(), str(submission_id))
            PortalService(self.db)._raise_submission_missing(  # noqa: SLF001
                adapter.label.title(),
                adapter.model,
                str(submission_id),
                request_type=adapter.request_type,
            )
        return row

    # ---------------- revision drafts ----------------
    #
    # A draft is a work-in-progress revision the contact can leave and resume.
    # It never touches the entity - it lives entirely in `portal_revision_drafts`
    # until Send revision, which applies the SAME payload through `revise` and
    # deletes the draft row.

    def _get_draft_row(
        self, source_entity_type: str, submission_id: str
    ) -> Optional[PortalRevisionDraft]:
        return (
            self.db.query(PortalRevisionDraft)
            .filter(
                PortalRevisionDraft.source_entity_type == source_entity_type,
                PortalRevisionDraft.source_entity_id == str(submission_id),
            )
            .first()
        )

    def _draft_as_dict(self, adapter: RevisionAdapter, row: Any, draft: PortalRevisionDraft) -> dict:
        current_revision_no = int(getattr(row, adapter.revision_no_attr, 0) or 0)
        return {
            "fields": draft.payload_json or {},
            "reason": draft.reason,
            "base_revision_no": draft.base_revision_no,
            "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
            "stale": draft.base_revision_no != current_revision_no,
        }

    def get_draft(self, source_entity_type: str, submission_id: str) -> Optional[dict]:
        """The stored draft for one submission, or ``None`` when there is none.

        No ownership check here - the caller (``portal_get_submission``) already
        ran ``PortalService.get_submission`` under the token, which is the same
        403/404 the rest of the portal makes.
        """
        adapter = get_adapter(source_entity_type)
        if adapter is None:
            return None
        draft = self._get_draft_row(source_entity_type, submission_id)
        if draft is None:
            return None
        row = (
            self.db.query(adapter.model)
            .filter(adapter.model.id == str(submission_id))
            .first()
        )
        if row is None:
            return None
        return self._draft_as_dict(adapter, row, draft)

    def save_draft(
        self,
        token: PortalToken,
        source_entity_type: str,
        submission_id: str,
        payload: dict,
        reason: Optional[str],
        base_revision_no: int,
    ) -> dict:
        """Upsert the draft for one submission.

        Re-runs the SAME policy gate ``revise`` uses: a draft that could never be
        sent must not be storable. The reason is optional here (a draft may be
        mid-thought) but capped at the same length.
        """
        adapter = get_adapter(source_entity_type)
        if adapter is None:
            raise handle_unprocessable(_DISABLED_SENTENCE)

        row = self.fetch_owned(token, source_entity_type, submission_id)

        policy = self.resolve_policy(source_entity_type, row)
        if not policy.allowed:
            raise handle_unprocessable(policy.blocked_reason or _DISABLED_SENTENCE)

        reason_text = (reason or "").strip() or None
        if reason_text and len(reason_text) > REASON_MAX_LEN:
            raise handle_unprocessable(f"Keep the reason under {REASON_MAX_LEN} characters.")

        # Same stripping `revise` does - a draft cannot smuggle a frozen field in
        # either (UAC AB1/AB2).
        clean_payload = {
            key: value
            for key, value in (payload or {}).items()
            if key not in adapter.frozen_on_revise
        }

        draft = self._get_draft_row(source_entity_type, str(row.id))
        if draft is None:
            draft = PortalRevisionDraft(
                source_entity_type=source_entity_type,
                source_entity_id=str(row.id),
                contact_id=token.contact_id,
            )
            self.db.add(draft)
        draft.contact_id = token.contact_id
        draft.base_revision_no = int(base_revision_no)
        draft.payload_json = clean_payload
        draft.reason = reason_text
        self.db.commit()
        self.db.refresh(draft)

        return self._draft_as_dict(adapter, row, draft)

    def discard_draft(self, source_entity_type: str, submission_id: str) -> None:
        """Delete the draft row for one submission. Idempotent - no error when
        there is none."""
        self.db.query(PortalRevisionDraft).filter(
            PortalRevisionDraft.source_entity_type == source_entity_type,
            PortalRevisionDraft.source_entity_id == str(submission_id),
        ).delete(synchronize_session=False)
        self.db.commit()

    def revise(
        self,
        token: PortalToken,
        source_entity_type: str,
        submission_id: str,
        payload: dict,
        reason: str,
        expected_revision_no: Optional[int],
    ) -> dict:
        """Apply a contact revision: snapshot, restart the flow, notify (UAC F).

        Everything up to the commit is one transaction. The stage-1 respawn and the
        voided-handler notification run AFTER it and are best-effort: a post-commit
        failure must never turn a revision that actually committed into a 500 (UAC F5).
        """
        adapter = get_adapter(source_entity_type)
        if adapter is None:
            raise handle_unprocessable(_DISABLED_SENTENCE)

        row = self.fetch_owned(token, source_entity_type, submission_id)

        # 0. Stale-write guard (UAC C5). A double tap or a retried request carries the
        #    revision_no the contact was looking at; a mismatch is refused before any
        #    side effect, so a replay yields one revision, not two voids and two restarts.
        current_revision_no = int(getattr(row, adapter.revision_no_attr, 0) or 0)
        if expected_revision_no is None or int(expected_revision_no) != current_revision_no:
            raise handle_conflict(
                f"This {adapter.label} was revised while you were working on it. "
                f"Reload to see revision {current_revision_no}."
            )

        # 1. Policy, re-checked server side. The FE gate is a convenience (UAC B4).
        policy = self.resolve_policy(source_entity_type, row)
        if not policy.allowed:
            raise handle_unprocessable(policy.blocked_reason or _DISABLED_SENTENCE)

        # 2. The reason is the only thing this journey asks for, so it is required -
        #    any non-empty length, by explicit decision (no character floor).
        reason_text = (reason or "").strip()
        if not reason_text:
            raise handle_unprocessable("Tell us what changed and why.")
        if len(reason_text) > REASON_MAX_LEN:
            raise handle_unprocessable(
                f"Keep the reason under {REASON_MAX_LEN} characters."
            )

        # 3. Backfill version 0 from current state for a pre-feature submission, BEFORE
        #    the payload is applied so it records the version being superseded (UAC G2).
        self.record_initial(
            source_entity_type, row, getattr(row, "contact_id", None), is_reconstructed=True
        )

        # 4. Capture the stages being voided BEFORE voiding them - the revision row and
        #    the notification both need them.
        #
        #    EVERY open stage, not just the newest: a form can sit with two stages open
        #    (a purchase request with project sales and approval), the void cancels all
        #    of them and the notifier tells all of their handlers, so history has to
        #    account for the same set. Newest first, which is the order
        #    ``active_trackers_for_source`` returns and the order the void applies -
        #    the same query on both sides, so the two can never name different rows.
        from app.services.form_sla_service import FormSLAOrchestrator

        orchestrator = FormSLAOrchestrator(self.db)
        active = orchestrator.active_trackers_for_source(source_entity_type, str(row.id))
        voided_stages = [
            {
                "stage_code": self._stage_code_for_tracker(tracker),
                "assignee_user_id": (
                    str(tracker.assigned_to_id) if tracker.assigned_to_id else None
                ),
            }
            for tracker in active
        ]

        # 5. Apply the payload through the EXISTING portal writer, with the frozen
        #    fields stripped from the payload rather than from the whitelist - one
        #    field list, no fork (UAC AB1/AB2).
        from app.services.portal_service import PortalService

        portal = PortalService(self.db)
        clean_payload = {
            key: value
            for key, value in (payload or {}).items()
            if key not in adapter.frozen_on_revise
        }
        portal._apply_payload(source_entity_type, row, clean_payload)  # noqa: SLF001
        if adapter.apply_lines is not None:
            adapter.apply_lines(self.db, row, clean_payload)
        self.db.flush()

        # #1232 blocking 4: a revise is a second submission path back into the
        # approval flow, so it must refuse the same way `submit_draft` does - a
        # sponsorship line without a usable unit price is checked against the
        # lines actually persisted by `apply_lines` above (autoflushed by this
        # query), same reasoning and same detail shape as
        # `PortalService._require_sponsorship_unit_prices`.
        if source_entity_type == "sponsorship_form":
            portal._require_sponsorship_unit_prices(row)  # noqa: SLF001

        # 6. Snapshot the stage output this revision invalidates, then clear it on the
        #    entity: an answer to the superseded version must never read as current
        #    (UAC FB2/FB3).
        invalidated: dict[str, Any] = {}
        for name in adapter.invalidated_on_revise:
            value = getattr(row, name, None)
            if value is not None:
                invalidated[name] = _jsonable(value)
            setattr(row, name, None)

        # 7. Counters (UAC C0/C2).
        new_revision_no = current_revision_no + 1
        setattr(row, adapter.revision_no_attr, new_revision_no)
        setattr(row, adapter.last_revised_at_attr, _utcnow())

        # 8. The history row carries the POST-edit snapshot.
        revision_row = self._write_revision_row(
            adapter,
            row,
            kind=KIND_REVISION,
            version_no=self._next_version_no(source_entity_type, str(row.id)),
            revision_no=new_revision_no,
            reason=reason_text,
            contact_id=token.contact_id,
            invalidated=invalidated or None,
            voided_stages=voided_stages,
        )

        # 9. Restart status. The stage is declared once on the config; the adapter only
        #    maps it to the status the entity carries (UAC J4).
        restart_stage, start_event = self._restart_stage(source_entity_type)
        setattr(row, "status", adapter.status_for_stage(restart_stage or ""))
        self.db.flush()

        # 10. Void every active form-SLA stage. Done LAST inside the transaction: the
        #     shared event-log writer commits, so anything left unwritten at this point
        #     would land in a later commit and could be lost on a failure between them.
        voided = orchestrator.void_active_for_source(
            source_entity_type,
            str(row.id),
            reason=(
                f"Revision {new_revision_no} submitted by the contact. Reason: {reason_text}"
            ),
        )

        # 10a. A sent revision has no leftover draft.
        self.db.query(PortalRevisionDraft).filter(
            PortalRevisionDraft.source_entity_type == source_entity_type,
            PortalRevisionDraft.source_entity_id == str(row.id),
        ).delete(synchronize_session=False)

        # 11. One transaction ends here.
        self.db.commit()

        entity_number = getattr(row, adapter.number_attr, None)
        voided_ids = [str(t.id) for t in voided]

        # 12. Post-commit side effects. Best-effort, catch and warn, never raise: the
        #     revision has committed and a retry would take the stale-guard path and
        #     never backfill any of this (UAC F5).
        self._void_pending_takeovers(voided_ids)
        self._restart_flow(source_entity_type, row, start_event)
        self._notify_voided_handlers(
            adapter=adapter,
            row=row,
            voided=voided,
            revision_no=new_revision_no,
            reason=reason_text,
            entity_number=entity_number,
            submitted_by=self._contact_names({token.contact_id}).get(str(token.contact_id)),
        )

        self.db.refresh(row)
        return {
            "revision_id": str(revision_row.id),
            "revision_no": new_revision_no,
            "policy": self.resolve_policy(source_entity_type, row).as_dict(),
            "voided_tracking_ids": voided_ids,
        }

    # ---------------- restart plumbing ----------------

    def _restart_stage_row(self, source_entity_type: str) -> tuple[Optional[str], Any]:
        """``(configured stage_code, the FormSLAConfig it resolves to)``.

        ``portal_revision_configs.restart_stage_code`` names the stage; NULL means the
        first stage of the type's chain, which is the stage started by the form's own
        submit event. Falls back to the config nothing else points at, then to the
        oldest. The matched row is returned so the start event and the human label are
        read off ONE selection - two selections would drift the moment a chain changes.
        """
        from app.models.sla import FormSLAConfig

        config = self.get_config(source_entity_type)
        stage_code = (getattr(config, "restart_stage_code", None) or "").strip() or None

        try:
            rows = (
                self.db.query(FormSLAConfig)
                .filter(
                    FormSLAConfig.source_entity_type == source_entity_type,
                    FormSLAConfig.is_active.is_(True),
                )
                .order_by(FormSLAConfig.created_at.asc())
                .all()
            )
        except Exception:  # noqa: BLE001 - never block a revision on SLA config
            self.db.rollback()
            return stage_code, None
        if not rows:
            return stage_code, None

        if stage_code:
            match = next((c for c in rows if c.stage_code == stage_code), None)
        else:
            match = next(
                (c for c in rows if "submit" in _event_tokens(c.start_event)),
                None,
            )
            if match is None:
                referenced = {str(c.next_config_id) for c in rows if c.next_config_id}
                match = next((c for c in rows if str(c.id) not in referenced), rows[0])
        return stage_code, match

    def _restart_stage(self, source_entity_type: str) -> tuple[Optional[str], Optional[str]]:
        """``(stage_code, start_event)`` the flow restarts at."""
        stage_code, match = self._restart_stage_row(source_entity_type)
        if match is None:
            return stage_code, "submit"
        events = _event_tokens(match.start_event)
        return match.stage_code, (events[0] if events else "submit")

    def restart_stage_label(self, source_entity_type: str) -> Optional[str]:
        """Where a revision sends the form back to, in words (UAC E1/E1a).

        The confirm dialog has to name the destination - "goes back to the start of
        its approval flow" is the fallback for when we genuinely cannot, not the
        target copy. A DISPLAY label, never a stage code: the contact never sees
        ``project_sales``.

        ``None`` when the type has no SLA chain and no configured stage, which is the
        only case the generic sentence is still correct for.
        """
        stage_code, match = self._restart_stage_row(source_entity_type)
        if match is not None:
            return _stage_display_label(
                match.stage_code, getattr(match, "team_set_code", None)
            )
        return _stage_display_label(stage_code, None) if stage_code else None

    def _stage_code_for_tracker(self, tracker: Any) -> Optional[str]:
        """A tracker stores ``team_set_code``; the stage code lives on its config.
        The pair ``(source_entity_type, team_set_code)`` identifies a stage."""
        from app.models.sla import FormSLAConfig

        try:
            config = (
                self.db.query(FormSLAConfig)
                .filter(
                    FormSLAConfig.source_entity_type == tracker.source_entity_type,
                    FormSLAConfig.team_set_code == tracker.team_set_code,
                )
                .first()
            )
        except Exception:  # noqa: BLE001
            self.db.rollback()
            return getattr(tracker, "team_set_code", None)
        return config.stage_code if config is not None else getattr(tracker, "team_set_code", None)

    def _void_pending_takeovers(self, tracking_ids: list[str]) -> None:
        """Mirror the escalation path: a pending takeover on a stage that no longer
        exists is void (UAC F2). ``void_for_tracking`` commits, so it runs here rather
        than inside the revise transaction."""
        if not tracking_ids:
            return
        try:
            from app.services.sla_takeover_service import SlaTakeoverService

            service = SlaTakeoverService(self.db)
            for tracking_id in tracking_ids:
                service.void_for_tracking(tracking_id, VOID_REASON_REVISED)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Revision: voiding pending takeovers failed for %s", tracking_ids, exc_info=True
            )

    def _restart_flow(self, source_entity_type: str, row: Any, start_event: Optional[str]) -> None:
        """Spawn stage 1 through the normal assignment path, with its normal
        assignment notification (UAC F3)."""
        try:
            from app.services.form_sla_service import emit_form_event

            emit_form_event(
                self.db,
                source_entity_type,
                str(row.id),
                start_event or "submit",
                contact_id=getattr(row, "contact_id", None),
                actor_user_id=None,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Revision: restarting the SLA chain failed for %s %s",
                source_entity_type,
                row.id,
                exc_info=True,
            )

    # ---------------- notification ----------------

    def _notify_voided_handlers(
        self,
        *,
        adapter: RevisionAdapter,
        row: Any,
        voided: list,
        revision_no: int,
        reason: str,
        entity_number: Optional[str],
        submitted_by: Optional[str],
    ) -> None:
        """Tell whoever was mid-work that their stage is void (UAC F6/F6a/F7/FT1).

        Revision-specific copy, never recycled assignment copy: the message names the
        document at its new revision, who revised it, why, and which stage stopped, so
        the recipient can act without opening the record. In-app always; email and
        WhatsApp follow that user's existing assignment preferences (Q2). When a voided
        stage had nobody assigned the message goes to that stage's team at the tracker's
        tier, so a revision never lands in nobody's inbox.
        """
        if not voided:
            return
        try:
            from app.services.form_sla_service import _form_detail_link, _full_form_link
            from app.services.notification_service import NotificationService

            source_entity_type = adapter.source_entity_type
            entity_id = str(row.id)
            # UAC F7: internal detail page, never the public ?token= view - every
            # recipient here is internal staff.
            link = _form_detail_link(source_entity_type, entity_id)
            full_link = _full_form_link(source_entity_type, entity_id)
            display_number = suffix_revision(entity_number, revision_no) or adapter.label
            who = submitted_by or "the contact"
            service = NotificationService(self.db)

            for tracker in voided:
                stage = self._stage_code_for_tracker(tracker) or "this stage"
                stage_label = str(stage).replace("_", " ")
                title = f"Revision {revision_no}: {display_number} - stop work"
                body = (
                    f"{adapter.label.capitalize()} {display_number} was revised by {who}. "
                    f"Your {stage_label} stage was voided - stop work on the previous "
                    f"version.\n\nReason: {reason}\n\n"
                    f"The {adapter.label} has restarted from the first stage.\n\n"
                    f"Open: {full_link}"
                )
                data = {
                    "tracking_id": str(tracker.id),
                    "source_entity_type": source_entity_type,
                    "source_entity_id": entity_id,
                    "revision_no": revision_no,
                    "voided_stage_code": stage,
                    "link": link,
                }
                for user_id in self._revision_notify_targets(tracker):
                    try:
                        service.create_with_channel_preferences(
                            user_id=user_id,
                            type="form_revised",
                            title=title,
                            body=body,
                            data=dict(data),
                            source_entity_type="form_sla_tracking",
                            source_entity_id=str(tracker.id),
                            event_type="form_revised",
                            send_in_app=True,
                            send_email=True,
                            send_web_push=False,
                            send_whatsapp=True,
                            whatsapp_pref_attr="notify_whatsapp_on_assignment",
                            email_pref_attr="notify_email_on_assignment",
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "Revision notify failed for user %s on tracker %s",
                            user_id,
                            tracker.id,
                            exc_info=True,
                        )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Revision notify fan-out failed for %s %s",
                adapter.source_entity_type,
                row.id,
                exc_info=True,
            )

    def _revision_notify_targets(self, tracker: Any) -> list[str]:
        """Assignee (plus the handling-lock holder), else the stage team (UAC FT1)."""
        targets: list[str] = []
        assignee_id = getattr(tracker, "assigned_to_id", None)
        # The void releases the handling lock, so read the holder off the marker the
        # voider left behind rather than the (now empty) column.
        handler_id = getattr(tracker, "_voided_handled_by_id", None) or getattr(
            tracker, "handled_by_id", None
        )
        if assignee_id:
            targets.append(str(assignee_id))
        if handler_id and str(handler_id) not in targets:
            targets.append(str(handler_id))
        if targets:
            return targets
        return self._stage_team_members(tracker)

    def _stage_team_members(self, tracker: Any) -> list[str]:
        """Members of the voided stage's team at the tracker's tier, resolved through
        the shared tier fallback so a missing tier is skipped rather than fatal."""
        try:
            from app.models.access import TeamMember
            from app.services.sla_service import _tracking_company_id
            from app.services.user_service import AccessAgentService

            agent_id = getattr(tracker, "agent_id", None)
            if not agent_id:
                return []
            resolved = AccessAgentService(self.db).resolve_team_with_tier_fallback(
                str(agent_id),
                int(getattr(tracker, "current_tier", 1) or 1),
                team_set_code=getattr(tracker, "team_set_code", None),
                company_id=_tracking_company_id(tracker),
            )
            if not resolved:
                return []
            team_id, _tier = resolved
            rows = (
                self.db.query(TeamMember.user_id)
                .filter(TeamMember.team_id == team_id)
                .all()
            )
            return [str(uid) for (uid,) in rows if uid]
        except Exception:  # noqa: BLE001
            logger.warning(
                "Revision notify: stage-team fallback failed for tracker %s",
                getattr(tracker, "id", None),
                exc_info=True,
            )
            return []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _attachment_entity_type(source_entity_type: str) -> str:
    """Sponsorship form shares the purchase_request entity_type for attachments -
    the same mapping the portal attachment routes use."""
    return "purchase_request" if source_entity_type == "sponsorship_form" else source_entity_type


# Stage codes that name a POSITION in the chain rather than the team working it.
# ``main`` is the first stage of purchase_request / sponsorship_form / complaint, and
# "Main" tells the contact nothing - the team set behind it ("project_sales") does.
_POSITIONAL_STAGE_CODES = frozenset({"main", "default", "stage_1", "start"})

# Codes whose title-cased form is wrong. Everything else title-cases correctly
# (``project_sales`` -> "Project Sales"), so this stays a short exception list.
_STAGE_LABEL_OVERRIDES: dict[str, str] = {
    "customer_service": "Customer Service",
    "cs": "CS",
    "it_admin": "IT Admin",
    "qc": "QC",
}


def _stage_display_label(
    stage_code: Optional[str], team_set_code: Optional[str] = None
) -> Optional[str]:
    """A stage rendered for a human. Never a raw code."""
    code = (stage_code or "").strip().lower()
    if code in _POSITIONAL_STAGE_CODES:
        code = (team_set_code or "").strip().lower() or code
    if not code:
        return None
    override = _STAGE_LABEL_OVERRIDES.get(code)
    if override:
        return override
    return " ".join(part.capitalize() for part in code.replace("-", "_").split("_") if part)


def _event_tokens(value: Optional[str]) -> list[str]:
    return [token.strip() for token in str(value or "").split(",") if token.strip()]


def _revision_label(row: PortalFormRevision) -> str:
    if row.kind == KIND_ORIGINAL:
        return "Original (reconstructed)" if row.is_reconstructed else "Original"
    if row.kind == KIND_RESUBMISSION:
        return "Resubmitted"
    return f"Revision {row.revision_no}"


def _line_summary(line: dict) -> str:
    parts = [str(line.get("item_code") or "").strip() or "(no item code)"]
    qty = line.get("quantity")
    if qty not in (None, ""):
        parts.append(f"x{qty}")
    return " ".join(parts)


def _diff_lines(previous: Any, current: Any) -> list[dict]:
    """Line-item changes: added, removed, and per-field value changes at the same
    position. Reordering is deliberately not reported (UAC K)."""
    before = list(previous or [])
    after = list(current or [])
    if not before and not after:
        return []
    changes: list[dict] = []
    for index in range(max(len(before), len(after))):
        old = before[index] if index < len(before) else None
        new = after[index] if index < len(after) else None
        if old == new:
            continue
        if old is None:
            changes.append(
                {
                    "field": f"products[{index}]",
                    "label": f"Product line {index + 1} added",
                    "from": None,
                    "to": _line_summary(new or {}),
                }
            )
            continue
        if new is None:
            changes.append(
                {
                    "field": f"products[{index}]",
                    "label": f"Product line {index + 1} removed",
                    "from": _line_summary(old),
                    "to": None,
                }
            )
            continue
        for key in ("item_code", "quantity", "unit_price", "total", "remark"):
            if old.get(key) != new.get(key):
                changes.append(
                    {
                        "field": f"products[{index}].{key}",
                        "label": f"Product line {index + 1} {_humanize(key).lower()}",
                        "from": old.get(key),
                        "to": new.get(key),
                    }
                )
    return changes
