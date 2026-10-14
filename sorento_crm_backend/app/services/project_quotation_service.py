"""Quotations, versions and the two guardrail alerts (UAC Group E).

The version model is defined by what it does NOT have. There is no
``current_version_id`` and no ``is_frozen`` column: **current is MAX(version_no)** and
everything below it is frozen (AC-E3a). Two facts that must agree is one too many -- a
pointer and a flag drift the first time a write half-fails, and after that nobody can
say which version the customer is holding.

Editing the current version saves IN PLACE (AC-E2). Revise freezes it and opens the
next (AC-E3). That split is what makes "which document did we send" answerable.

Two alerts, computed on save and STORED on the line:

- **non-standard SKU** -- off-catalog, or a product outside the nominated series.
- **below minimum price** -- fires to management on the TRANSITION into breach only
  (AC-E6a). In-place editing is how the job is done, and an alert per save is an alert
  that gets filtered into a folder nobody opens.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import Integer, func, or_
from sqlalchemy.orm import Session, aliased

from app.models.lookup import LookupOption, LookupSet
from app.models.product import Product
from app.models.projects import (
    OUTCOME_LOST,
    OUTCOME_OPEN,
    OUTCOME_WON,
    QUOTATION_LOSS_REASON_SET_KEY,
    QUOTATION_OUTCOME_LOST,
    QUOTATION_OUTCOME_OPEN,
    QUOTATION_OUTCOME_WON,
    UNIT_TYPES,
    Project,
    ProjectQuotation,
    ProjectQuotationIssueScope,
    ProjectQuotationLine,
    ProjectQuotationVersion,
)
from app.services import project_pricing_service as pricing
from app.services.error_handler import AppException

OUTCOME_OPEN_Q = QUOTATION_OUTCOME_OPEN
OUTCOME_WON_Q = QUOTATION_OUTCOME_WON
OUTCOME_LOST_Q = QUOTATION_OUTCOME_LOST
QUOTATION_OUTCOMES = (OUTCOME_OPEN_Q, OUTCOME_WON_Q, OUTCOME_LOST_Q)

# Re-exported so callers read one name for "open" rather than importing from two places.
OUTCOME_OPEN = QUOTATION_OUTCOME_OPEN

_CENTS = Decimal("0.01")

# Where a just-computed breach is parked for the caller to notify on. An instance attr
# rather than a return value because ``upsert_line`` returns the line, and the marker
# dies on re-query -- the same smuggling pattern the SLA services use, and for the same
# reason: the route needs it, the row must not carry it.
_BREACH_ATTR = "_breach_events"

LINE_FIELDS = (
    "product_id",
    "product_code_snapshot",
    "description_snapshot",
    "image_attachment_id",
    "drawing_attachment_id",
    "unit_price",
    "quantity",
    "uom",
    "unit_type",
    "sort_order",
    "notes",
    # The columns the printed quotation carries but the screen did not: the A / B / C item letter
    # that groups a fitting with its valve and hose, the band heading off the customer's own bill
    # of quantities, and the alternate that is quoted at a rate WITHOUT being added up.
    "item_label",
    "brand_snapshot",
    "technical_spec",
    "complete_set",
    "band_label",
    "is_rate_only",
)

_NUMERIC_LINE_FIELDS = ("unit_price", "quantity")


# ------------------------------------------------------------------- versions


def get_quotation_or_404(db: Session, project_id: str, quotation_id: str) -> ProjectQuotation:
    quotation = (
        db.query(ProjectQuotation)
        .filter(
            ProjectQuotation.id == quotation_id,
            ProjectQuotation.project_id == project_id,
        )
        .first()
    )
    if not quotation:
        raise AppException(
            status_code=404, message="Quotation not found.", code="quotation_not_found"
        )
    return quotation


def list_versions(db: Session, quotation_id: str) -> List[ProjectQuotationVersion]:
    """Newest first: the current version is what people open."""
    return (
        db.query(ProjectQuotationVersion)
        .filter(ProjectQuotationVersion.quotation_id == quotation_id)
        .order_by(ProjectQuotationVersion.version_no.desc())
        .all()
    )


def current_version(db: Session, quotation_id: str) -> ProjectQuotationVersion:
    """MAX(version_no). The single fact the whole model rests on (AC-E3a)."""
    version = (
        db.query(ProjectQuotationVersion)
        .filter(ProjectQuotationVersion.quotation_id == quotation_id)
        .order_by(ProjectQuotationVersion.version_no.desc())
        .first()
    )
    if not version:
        raise AppException(
            status_code=404,
            message="This quotation has no versions.",
            code="quotation_version_missing",
        )
    return version


def is_frozen(db: Session, version: ProjectQuotationVersion) -> bool:
    """Derived, never read from a column. Two ways a version stops being editable:

    1. A later version exists, so this one was superseded.
    2. An ISSUE points at it, so the customer already holds these rows.

    The second clause is what stops an issue rewriting itself. ``issue_scopes`` records the exact
    version each scope contributed to R1, but the highest-numbered version stays editable under
    the first clause alone - so editing a line after issuing would have changed the very rows R1
    claims to have contained, and the PDF on file would quietly stop matching the record behind it.

    Extended rather than replaced by a ``frozen`` column ON PURPOSE: this function is the single
    definition, and a stamped flag would be a second fact that has to agree with it. The model
    comment on ``ProjectQuotationVersion`` says why - two facts that must agree drift the first
    time a write half-fails, and then nobody can say which version the customer holds.
    """
    highest = (
        db.query(func.max(ProjectQuotationVersion.version_no))
        .filter(ProjectQuotationVersion.quotation_id == version.quotation_id)
        .scalar()
    )
    if highest is not None and version.version_no < highest:
        return True
    return is_issued(db, version)


def frozen_version_ids(db: Session, version_ids: Sequence[Any]) -> Set[str]:
    """``is_frozen`` for several versions in ONE query: superseded by a later version, or issued.

    For a line table, which asks it once per render rather than once per line.
    """
    wanted = list(dict.fromkeys(str(v) for v in version_ids if v))
    if not wanted:
        return set()
    later = aliased(ProjectQuotationVersion)
    superseded = (
        db.query(later.id)
        .filter(
            later.quotation_id == ProjectQuotationVersion.quotation_id,
            later.version_no > ProjectQuotationVersion.version_no,
        )
        .exists()
    )
    issued = (
        db.query(ProjectQuotationIssueScope.id)
        .filter(ProjectQuotationIssueScope.version_id == ProjectQuotationVersion.id)
        .exists()
    )
    rows = (
        db.query(ProjectQuotationVersion.id)
        .filter(ProjectQuotationVersion.id.in_(wanted), or_(superseded, issued))
        .all()
    )
    return {str(row[0]) for row in rows}


def is_issued(db: Session, version: ProjectQuotationVersion) -> bool:
    """Whether any issue of the parent document went out carrying this version."""
    return (
        db.query(ProjectQuotationIssueScope.id)
        .filter(ProjectQuotationIssueScope.version_id == version.id)
        .first()
        is not None
    )


def assert_editable(db: Session, version: ProjectQuotationVersion) -> None:
    if not is_frozen(db, version):
        return
    # The two reasons read differently to a person: one says somebody moved on, the other says the
    # customer is holding this paper. "Superseded" on a version you just issued would be baffling.
    if is_issued(db, version):
        raise AppException(
            status_code=422,
            message=(
                f"Version {version.version_no} has been issued to the customer and cannot be "
                "changed. Open a revision instead."
            ),
            code="quotation_version_issued",
        )
    raise AppException(
        status_code=422,
        message=(
            f"Version {version.version_no} was superseded and cannot be changed. "
            "Edit the current version instead."
        ),
        code="quotation_version_frozen",
    )


def get_version_or_404(
    db: Session, quotation_id: str, version_id: str
) -> ProjectQuotationVersion:
    version = (
        db.query(ProjectQuotationVersion)
        .filter(
            ProjectQuotationVersion.id == version_id,
            ProjectQuotationVersion.quotation_id == quotation_id,
        )
        .first()
    )
    if not version:
        raise AppException(
            status_code=404,
            message="Quotation version not found.",
            code="quotation_version_not_found",
        )
    return version


# -------------------------------------------------------------------- create


def create_quotation(
    db: Session,
    *,
    project: Project,
    actor_user_id: str,
    payload: Dict[str, Any],
    document_id: Optional[str] = None,
) -> ProjectQuotation:
    """A scope of the project, with version 1 opened immediately.

    Opened rather than deferred: a quotation with no version has nowhere to put a line,
    and every caller would have to remember to create one.

    Every scope belongs to a DOCUMENT, and `document_id` is stamped on the INSERT rather than
    assigned afterwards: the column is NOT NULL in Postgres, so a two-step "insert then attach"
    dies on the flush. It read as safe only because the model left the column nullable while the
    migration made it NOT NULL - the same model-versus-column drift that has bitten this codebase
    before, and the reason the model now declares it NOT NULL too.

    A caller with no document in hand (the original per-scope endpoint) gets one created for it,
    one document per scope, exactly as the backfill did for existing rows.
    """
    scope_label = " ".join((payload.get("scope_label") or "").split())
    if not scope_label:
        raise AppException(
            status_code=422,
            message="A quotation needs a scope, e.g. House Units or Common Area.",
            code="quotation_scope_required",
        )

    if document_id is None:
        from app.services import project_quotation_document_service as documents

        document_id = documents.create_document(
            db, project=project, actor_user_id=actor_user_id
        ).id

    quotation = ProjectQuotation(
        company_id=project.company_id,
        project_id=project.id,
        document_id=document_id,
        scope_label=scope_label,
        series_id=payload.get("series_id"),
        notes=payload.get("notes"),
        outcome=OUTCOME_OPEN_Q,
        created_by=actor_user_id,
    )
    db.add(quotation)
    db.flush()

    db.add(
        ProjectQuotationVersion(
            company_id=project.company_id,
            quotation_id=quotation.id,
            version_no=1,
            issued_by=actor_user_id,
            total_amount=Decimal("0.00"),
        )
    )
    db.flush()

    from app.services import project_activity_service as activity

    activity.record_project_event(
        db,
        project=project,
        template="quotation_created",
        payload={"quotation_id": str(quotation.id), "scope_label": quotation.scope_label},
        actor_id=actor_user_id,
    )
    db.flush()
    return quotation


def update_quotation(
    db: Session, *, quotation: ProjectQuotation, payload: Dict[str, Any]
) -> ProjectQuotation:
    """Scope label, series and notes. Outcome has its own action, so it is not here:
    losing a quotation needs a reason and a decided-at stamp."""
    if "scope_label" in payload:
        scope_label = " ".join((payload.get("scope_label") or "").split())
        if not scope_label:
            raise AppException(
                status_code=422,
                message="A quotation needs a scope.",
                code="quotation_scope_required",
            )
        quotation.scope_label = scope_label
    if "series_id" in payload:
        quotation.series_id = payload["series_id"] or None
    if "notes" in payload:
        quotation.notes = payload["notes"]
    db.flush()
    return quotation


def revise(
    db: Session, *, quotation: ProjectQuotation, actor_user_id: str
) -> ProjectQuotationVersion:
    """Freeze the current version and open the next one, carrying the lines (AC-E3).

    Carrying the lines rather than starting blank: a revision is almost always "the same
    quotation with three prices moved", and re-keying forty lines is how people end up
    editing the frozen one in a spreadsheet instead.
    """
    live = current_version(db, quotation.id)
    live.frozen_at = datetime.utcnow()

    nxt = ProjectQuotationVersion(
        company_id=quotation.company_id,
        quotation_id=quotation.id,
        version_no=live.version_no + 1,
        issued_by=actor_user_id,
        total_amount=live.total_amount,
        notes=live.notes,
    )
    db.add(nxt)
    db.flush()

    for line in list_lines(db, live.id):
        db.add(
            ProjectQuotationLine(
                company_id=quotation.company_id,
                version_id=nxt.id,
                product_id=line.product_id,
                product_code_snapshot=line.product_code_snapshot,
                description_snapshot=line.description_snapshot,
                list_price_snapshot=line.list_price_snapshot,
                image_attachment_id=line.image_attachment_id,
                drawing_attachment_id=line.drawing_attachment_id,
                # The printed columns: a revision is the same quotation with some prices moved,
                # and dropping these silently re-typed the bill-of-quantities layout to nothing.
                item_label=line.item_label,
                brand_snapshot=line.brand_snapshot,
                technical_spec=line.technical_spec,
                complete_set=line.complete_set,
                band_label=line.band_label,
                is_rate_only=line.is_rate_only,
                unit_price=line.unit_price,
                quantity=line.quantity,
                uom=line.uom,
                unit_type=line.unit_type,
                line_total=line.line_total,
                is_non_standard=line.is_non_standard,
                floor_value_applied=line.floor_value_applied,
                floor_level_applied=line.floor_level_applied,
                is_below_floor=line.is_below_floor,
                sort_order=line.sort_order,
                notes=line.notes,
            )
        )
    db.flush()
    _recalculate_total(db, nxt)

    project = db.query(Project).filter(Project.id == quotation.project_id).first()
    if project is not None:
        from app.services import project_activity_service as activity

        activity.record_project_event(
            db,
            project=project,
            template="quotation_revised",
            payload={
                "quotation_id": str(quotation.id),
                "version_no": nxt.version_no,
            },
            actor_id=actor_user_id,
        )
        db.flush()
    return nxt


def delete_quotation(db: Session, quotation: ProjectQuotation) -> None:
    """Hard delete, cascading to versions and lines. The project's derived outcome is
    recomputed, because removing the only lost scope reopens the project."""
    project = db.query(Project).filter(Project.id == quotation.project_id).first()
    db.delete(quotation)
    db.flush()
    if project:
        sync_project_outcome(db, project)


# ---------------------------------------------------------------------- lines


def _next_sort_order(db: Session, version_id: str) -> int:
    """One past the last line on this version, counting rows not yet flushed.

    The pending rows matter: a caller adding several lines before a flush would
    otherwise read the same max every time and hand them all the same position,
    which is the tie this exists to remove.
    """
    stored = (
        db.query(func.max(ProjectQuotationLine.sort_order))
        .filter(ProjectQuotationLine.version_id == version_id)
        .scalar()
    )
    pending = [
        int(obj.sort_order or 0)
        for obj in db.new
        if isinstance(obj, ProjectQuotationLine)
        and str(getattr(obj, "version_id", "")) == str(version_id)
    ]
    return max([int(stored or 0), *pending]) + 1


def list_lines(db: Session, version_id: str) -> List[ProjectQuotationLine]:
    return (
        db.query(ProjectQuotationLine)
        .filter(ProjectQuotationLine.version_id == version_id)
        .order_by(
            ProjectQuotationLine.sort_order.asc(),
            ProjectQuotationLine.created_at.asc(),
            # `id` is the last resort, and it is not decorative: every line written
            # through the per-row path before this was fixed sits at sort_order 0 with
            # a created_at shared to the microsecond by its siblings, so without a
            # total key those rows reorder between renders of the same document.
            ProjectQuotationLine.id.asc(),
        )
        .all()
    )


def _recalculate_total(db: Session, version: ProjectQuotationVersion) -> None:
    """The version's money, with rate-only lines EXCLUDED.

    A rate-only line keeps its `line_total` (the rate is what the customer is being shown), so
    summing the column blindly would make `total_amount` disagree with what the document footer
    and the PDF print. The sample quotation's five alternates would have added RM 235,075 that
    nobody quoted.
    """
    total = (
        db.query(func.coalesce(func.sum(ProjectQuotationLine.line_total), 0))
        .filter(
            ProjectQuotationLine.version_id == version.id,
            ProjectQuotationLine.is_rate_only.is_(False),
        )
        .scalar()
    )
    version.total_amount = Decimal(total or 0).quantize(_CENTS)
    db.flush()


def _snapshot_product(db: Session, line: ProjectQuotationLine, product: Product) -> None:
    """Freeze what the product looked like when it was quoted (AC-E4).

    A catalogue rename or re-price next month must not rewrite what the customer was
    sent, and a discontinued SKU must still print on last year's quotation.
    """
    line.product_code_snapshot = product.product_code
    line.description_snapshot = (
        line.description_snapshot or product.description or product.product_name
    )
    line.list_price_snapshot = product.list_price
    if line.uom is None:
        line.uom = _uom_code(db, product.base_uom_id)
    # The PICTURE is deliberately not snapshotted here (S21). Only 30 of the 535 products with
    # candidate photos carry a chosen one, so almost every line is priced before anybody has
    # answered for its product, and a stamp taken now would freeze that "nobody has chosen"
    # forever - nobody is going to re-save 52 lines to collect a decision made ten minutes
    # later. A draft resolves live; ISSUING is what writes the picture down.
    # See project_quotation_image_service.freeze_version_images.


def _uom_code(db: Session, uom_id: Optional[str]) -> Optional[str]:
    if not uom_id:
        return None
    from app.models.product import UnitOfMeasure

    row = db.query(UnitOfMeasure.uom_code).filter(UnitOfMeasure.id == uom_id).first()
    return row[0] if row else None


def resolve_product_image(db: Session, product_id: str) -> Optional[str]:
    """The photograph somebody CHOSE for this product, or nothing (S21, superseding AC-E8).

    It used to be "the image-class attachment with the lowest sort order", which is the
    whichever-row-came-first fallback that ``product_attachments.is_primary`` exists to
    remove: for ``SRTWC286-SH`` the first-linked row is one of 31 files including a blank
    page and two other products' photographs.

    There is exactly ONE image decision in this system and the quotation is its third
    consumer, after the brochure and 3D-model generation. This function is now a thin alias
    so existing callers keep working; the decision itself lives in
    ``app.services.product_image_service``.
    """
    from app.services import product_image_service

    return product_image_service.chosen_attachment_id(db, product_id)


def _apply_guardrails(
    db: Session,
    *,
    line: ProjectQuotationLine,
    quotation: ProjectQuotation,
    product: Optional[Product],
    membership: Optional["pricing.SeriesMembership"] = None,
    series_pricing: Optional[Dict[str, tuple]] = None,
) -> Optional[Dict[str, Any]]:
    """Recompute both alerts for one line. Returns a breach event, or None.

    An event is returned ONLY on the transition into breach (AC-E6a): the previous state
    is read before the new one is written, and a line that was already breaching stays
    silent no matter how many times it is saved. ``recompute_version`` leans on exactly
    that: re-confirming forty lines raises nothing.

    ``membership`` is the series expanded once (S18). Passed in by a caller writing a whole
    version so the category walk and the product read happen once rather than per line.
    ``series_pricing`` is its twin for the money (T5) and is passed for the same reason.
    """
    was_below = bool(line.is_below_floor)

    # Off-catalog is ALWAYS non-standard (AC-E5), series or no series: "we quoted
    # something that is not in our catalogue" is the case the alert exists for, and a
    # project that never nominated a series must not silently excuse it.
    line.is_non_standard = product is None or not pricing.is_in_series(
        db,
        series_id=quotation.series_id,
        product=product,
        membership=membership,
    )

    floor = pricing.resolve_floor(
        db,
        company_id=quotation.company_id,
        product=product,
        list_price=line.list_price_snapshot,
        series_id=quotation.series_id,
        series_pricing=series_pricing,
    )
    if floor is None:
        line.floor_value_applied = None
        line.floor_level_applied = None
        line.is_below_floor = False
        return None

    line.floor_value_applied = floor.value
    line.floor_level_applied = floor.level
    line.is_below_floor = Decimal(line.unit_price or 0) < floor.value

    if line.is_below_floor and not was_below:
        return {
            "line_id": line.id,
            "quotation_id": quotation.id,
            "unit_price": Decimal(line.unit_price or 0),
            "floor_value": floor.value,
            "floor_level": floor.level,
            "product_code": line.product_code_snapshot,
        }
    return None


def pop_breach_events(line: ProjectQuotationLine) -> List[Dict[str, Any]]:
    """Read and clear the breach events smuggled onto the line by the last save.

    Popped rather than read so a caller cannot notify twice, and so the marker cannot
    survive into a later request.
    """
    events = list(getattr(line, _BREACH_ATTR, []) or [])
    try:
        setattr(line, _BREACH_ATTR, [])
    except Exception:  # noqa: BLE001 -- detached instance; nothing to clear
        pass
    return events


def _quotation_of(db: Session, version: ProjectQuotationVersion) -> ProjectQuotation:
    quotation = (
        db.query(ProjectQuotation)
        .filter(ProjectQuotation.id == version.quotation_id)
        .first()
    )
    if not quotation:
        raise AppException(
            status_code=404, message="Quotation not found.", code="quotation_not_found"
        )
    return quotation


#: The picked-file columns a line write accepts, with the refusal each raises (AC-QLG09).
_PICKS = (
    ("image_attachment_id", False, "quotation_line_image_invalid", "photo"),
    ("drawing_attachment_id", True, "quotation_line_drawing_invalid", "drawing"),
)


def assert_line_picks(
    db: Session,
    *,
    quotation: ProjectQuotation,
    payload: Dict[str, Any],
    line: Optional[ProjectQuotationLine] = None,
) -> None:
    """Refuse a picked photo or drawing that is not one of the line's product's own files (422).

    The ids arrive from the browser, so they are checked against `product_attachments` for the
    product the line WILL have (the payload's, else the stored one): an image, not deleted, this
    company's (or shared), and for a drawing also typed "Technical Specifications"; a photo is
    never a drawing. Only a value that is unchanged together with an unchanged product is skipped,
    so a stamp on a line whose file was deleted later does not block the next save of that line,
    while the old product's file can never ride along onto a new product. A malformed id is the
    same refusal, checked before any query. Clearing (null) is always allowed. The product's own
    attachments are never written from here.
    """
    from app.services import product_image_service as images

    product_id = payload["product_id"] if "product_id" in payload else (
        line.product_id if line is not None else None
    )
    product_unchanged = line is not None and str(product_id or "") == str(line.product_id or "")
    for field, is_drawing, code, word in _PICKS:
        if field not in payload or not payload[field]:
            continue
        value = str(payload[field])
        if product_unchanged and str(getattr(line, field) or "") == value:
            continue
        refusal = AppException(
            status_code=422,
            message=f"That {word} is not one of this product's files.",
            code=code,
        )
        # Canonical form only: `uuid.UUID` also accepts `urn:uuid:...`, braces and bare hex,
        # none of which the database will take as an id.
        if not _is_canonical_uuid(value) or (
            product_id and not _is_canonical_uuid(str(product_id))
        ):
            raise refusal
        if not product_id or not images.is_product_file(
            db,
            product_id=str(product_id),
            attachment_id=value,
            drawing=is_drawing,
            company_id=quotation.company_id,
        ):
            raise refusal


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _write_line(
    db: Session,
    *,
    version: ProjectQuotationVersion,
    quotation: ProjectQuotation,
    payload: Dict[str, Any],
    line: Optional[ProjectQuotationLine] = None,
) -> ProjectQuotationLine:
    """One line written, with NO editability check and NO total recomputed.

    Split out of ``upsert_line`` so the bulk write (S10) can apply a whole set through the
    same coercion, snapshotting and guardrails and then recompute the total ONCE. Both
    callers share this body, so the two ways of saving a line cannot drift apart.
    """
    # A different product's photo or drawing cannot be this one's: a product change that does
    # not send picks of its own clears the stored ones, in every path that writes a line.
    if (
        line is not None
        and "product_id" in payload
        and str(payload["product_id"] or "") != str(line.product_id or "")
    ):
        payload = {
            **{field: None for field, *_rest in _PICKS},
            **payload,
        }

    unit_type = payload.get("unit_type")
    if unit_type and unit_type not in UNIT_TYPES:
        raise AppException(
            status_code=422,
            message=f"Unknown unit type '{unit_type}'.",
            code="quotation_unit_type_invalid",
        )

    if line is None:
        line = ProjectQuotationLine(
            company_id=quotation.company_id,
            version_id=version.id,
            # A new line goes to the END, explicitly. `sort_order` defaults to 0, and
            # the per-row editor path never assigned one, so every line added this way
            # tied at 0 - and the `created_at` tiebreaker ties too, because Postgres
            # freezes now() for the whole transaction, giving rows written in one
            # request the SAME timestamp to the microsecond. With both keys tied the
            # order is whatever the plan yields, so an issued quotation could renumber
            # its lines between two renders of the same document. `replace_lines`
            # already writes the array position; this is the per-row path catching up.
            sort_order=_next_sort_order(db, version.id),
        )
        db.add(line)

    for field in LINE_FIELDS:
        if field in payload:
            value = payload[field]
            # Coerced on the way in, so the in-session value matches what a re-read would give.
            # Left as the caller's string, `line.unit_price` stays a str until the session is
            # expired, and any Python arithmetic on it downstream breaks on a type nobody expected.
            if field in _NUMERIC_LINE_FIELDS and value is not None and not isinstance(value, Decimal):
                value = Decimal(str(value))
            setattr(line, field, value)

    if line.unit_price is None:
        line.unit_price = Decimal("0.00")
    if line.quantity is None:
        line.quantity = Decimal("1")

    product = (
        db.query(Product).filter(Product.id == line.product_id).first()
        if line.product_id
        else None
    )
    if product is not None:
        _snapshot_product(db, line, product)
    elif not line.description_snapshot:
        raise AppException(
            status_code=422,
            message="A custom item needs a description.",
            code="quotation_line_description_required",
        )

    line.line_total = (
        Decimal(line.unit_price or 0) * Decimal(line.quantity or 0)
    ).quantize(_CENTS)

    db.flush()
    event = _apply_guardrails(db, line=line, quotation=quotation, product=product)
    setattr(line, _BREACH_ATTR, [event] if event else [])

    db.flush()
    return line


def upsert_line(
    db: Session,
    *,
    version: ProjectQuotationVersion,
    actor_user_id: str,
    payload: Dict[str, Any],
    line: Optional[ProjectQuotationLine] = None,
) -> ProjectQuotationLine:
    """Add or edit one line on the CURRENT version, in place (AC-E2).

    Both alerts are recomputed here rather than on read, so the values stored on the
    line are the ones that were true when it was priced (AC-E7).
    """
    assert_editable(db, version)
    quotation = _quotation_of(db, version)
    line = _write_line(
        db, version=version, quotation=quotation, payload=payload, line=line
    )
    _recalculate_total(db, version)
    return line


def replace_lines(
    db: Session,
    *,
    version: ProjectQuotationVersion,
    actor_user_id: str,
    lines: Sequence[Dict[str, Any]],
) -> List[ProjectQuotationLine]:
    """The whole desired line set for one version, written in ONE transaction (S10).

    **Why this exists.** The editor used to write per row, so building a ten-line scope was
    ten requests, and a sequence that half-failed left a quotation in a state nobody typed:
    three saved prices, a deletion that did not land, and a total that matched neither. One
    request makes a Save atomic - either the set the user arranged is what is stored, or
    nothing moved at all.

    **This is a REPLACE, not a merge.** ``lines`` is the full desired set. A stored line
    whose id is absent from it is DELETED. A client that sends only the rows it happened to
    touch will therefore wipe every row it left out, silently and completely, so the caller
    must always send the whole set it is showing the user.

    **Identity.** A line already stored carries its ``id``; a new one arrives without one.
    An ``id`` that is not on this version is refused rather than treated as new, because
    treating it as new would duplicate the row the client meant to move.

    **Order.** ``sort_order`` is the array position, so reordering is just sending a
    different order, and any ``sort_order`` in the payload is ignored. The client never has
    to compute one.

    The total is recomputed exactly once at the end, through the same helper the per-row
    path uses, so rate-only lines keep contributing nothing without that rule being restated
    here.
    """
    # Before anything is touched: a frozen or issued version refuses the whole request, and
    # the refusal must not be reached halfway through a set of writes.
    assert_editable(db, version)
    quotation = _quotation_of(db, version)

    stored = {row.id: row for row in list_lines(db, version.id)}
    kept: Set[str] = set()
    written: List[ProjectQuotationLine] = []

    for index, incoming in enumerate(lines):
        payload = dict(incoming)
        line_id = payload.pop("id", None)
        target: Optional[ProjectQuotationLine] = None
        if line_id:
            line_id = str(line_id)
            if line_id in kept:
                raise AppException(
                    status_code=422,
                    message="The same line appears twice in this save.",
                    code="quotation_line_duplicate",
                )
            target = stored.get(line_id)
            if target is None:
                raise AppException(
                    status_code=404,
                    message="One of these lines is not on this version.",
                    code="quotation_line_not_found",
                )
            kept.add(line_id)
        payload["sort_order"] = index
        assert_line_picks(db, quotation=quotation, payload=payload, line=target)
        written.append(
            _write_line(
                db,
                version=version,
                quotation=quotation,
                payload=payload,
                line=target,
            )
        )

    for line_id, row in stored.items():
        if line_id not in kept:
            db.delete(row)

    db.flush()
    _recalculate_total(db, version)
    return written


def delete_line(
    db: Session, *, version: ProjectQuotationVersion, line: ProjectQuotationLine
) -> None:
    assert_editable(db, version)
    db.delete(line)
    db.flush()
    _recalculate_total(db, version)


# ----------------------------------------------------------------- recompute


def recompute_version(
    db: Session, *, version: ProjectQuotationVersion
) -> Dict[str, Any]:
    """Re-ask both guardrails for every line on one version, against TODAY's policy (S19).

    **Why a button and not a migration.** Both alerts are computed on line WRITE and stored
    ON the line (AC-E7). That is right for a quotation the customer holds - a floor moved
    next year must not retro-flag what was already sent - and wrong for one still being
    priced, where the flags go stale the moment a series or a floor changes and nothing
    re-asks. The client said so themselves and refused a bulk write: "I need to have a
    recompute button rather than you go and bulk write the data ... in case someone change
    at the master data (product or any configuration)". Master data keeps moving, so the
    correction has to be an action somebody can repeat, not a one-off script.

    **Scope: this version, and only if it is still editable.** ``assert_editable`` refuses a
    frozen or issued one with the same 422 every other write raises, because those flags are
    what was true when the paper went out and rewriting them would rewrite quoted history.
    That also answers "every editable version on the document?" - no. Recomputing a scope
    somebody else has open is a surprise, and the caller already knows which version the
    reader is looking at.

    **Cost.** One pass, synchronous, inside the caller's transaction. The series is expanded
    ONCE, and the products of the whole version are read in one query, so the per-line work
    is the floor resolution alone. A version is tens of lines (the client's own sample is
    52), which is why this is not queued. If a document ever carries hundreds, the thing to
    move is the floor lookup - ``resolve_floor`` re-reads the company's rules per line - and
    then the whole pass belongs on the ``project_docs`` queue with the same report delivered
    through My Downloads rather than a response body.

    Returns what MOVED, never a bare success. "6 lines are no longer non-standard, 1 is now
    below floor" is the answer to the question the button asks; a silent toast is not.
    """
    assert_editable(db, version)
    quotation = _quotation_of(db, version)
    lines = list_lines(db, version.id)

    products: Dict[str, Product] = {}
    product_ids = {line.product_id for line in lines if line.product_id}
    if product_ids:
        products = {
            row.id: row
            for row in db.query(Product).filter(Product.id.in_(product_ids)).all()
        }

    # Expanded once for the whole version rather than per line (S18, and T5 for the money).
    membership = pricing.series_membership(db, quotation.series_id)
    series_pricing = pricing.series_pricing_map(db, quotation.series_id)

    report = {
        "version_id": version.id,
        "version_no": version.version_no,
        "quotation_id": quotation.id,
        "scope_label": quotation.scope_label,
        "line_count": len(lines),
        "now_non_standard": 0,
        "no_longer_non_standard": 0,
        "now_below_floor": 0,
        "no_longer_below_floor": 0,
        "floor_changed": 0,
        # Lines that NAME a product the catalogue will not give us. Counted and reported
        # because otherwise this pass is indistinguishable from "everything is fine": the
        # line reads as off-catalog, stays non-standard, and the recompute says nothing
        # changed - which is true and completely unhelpful. On the live database 46 lines
        # of one quotation are in exactly this state (they point at another company's
        # identically-coded product row), and the report is how anybody finds out.
        "unresolved_products": 0,
        "changed_lines": [],
        "breach_events": [],
    }

    for line in lines:
        if line.product_id and line.product_id not in products:
            report["unresolved_products"] += 1
        before = (
            bool(line.is_non_standard),
            bool(line.is_below_floor),
            line.floor_value_applied,
            line.floor_level_applied,
        )
        event = _apply_guardrails(
            db,
            line=line,
            quotation=quotation,
            product=products.get(line.product_id or ""),
            membership=membership,
            series_pricing=series_pricing,
        )
        if event:
            report["breach_events"].append(event)

        after = (
            bool(line.is_non_standard),
            bool(line.is_below_floor),
            line.floor_value_applied,
            line.floor_level_applied,
        )
        if before == after:
            continue

        if after[0] and not before[0]:
            report["now_non_standard"] += 1
        elif before[0] and not after[0]:
            report["no_longer_non_standard"] += 1
        if after[1] and not before[1]:
            report["now_below_floor"] += 1
        elif before[1] and not after[1]:
            report["no_longer_below_floor"] += 1
        # The floor moved under a line without either side of it crossing: still worth
        # saying, because the number printed beside a breach is now a different number.
        if before[0] == after[0] and before[1] == after[1]:
            report["floor_changed"] += 1

        # Named by what a person calls the line. No id reaches the reader.
        report["changed_lines"].append(
            line.product_code_snapshot or line.description_snapshot or "Custom item"
        )

    db.flush()
    # The version TOTAL is deliberately not recomputed. This re-asks the guardrails and
    # nothing else; a recompute that moved money would be a re-price, which is not what the
    # button says it does.
    report["changed_count"] = len(report["changed_lines"])
    return report


# ------------------------------------------------------------------- outcomes


def loss_reasons(db: Session) -> List[Dict[str, str]]:
    """Active options of the configurable loss-reason set (AC-E9)."""
    lookup_set = (
        db.query(LookupSet)
        .filter(LookupSet.set_key == QUOTATION_LOSS_REASON_SET_KEY)
        .first()
    )
    if not lookup_set:
        return []
    rows = (
        db.query(LookupOption)
        .filter(LookupOption.set_id == lookup_set.id, LookupOption.is_active.is_(True))
        .order_by(LookupOption.sort_order.asc(), LookupOption.label.asc())
        .all()
    )
    return [{"value": row.value, "label": row.label} for row in rows]


def _assert_loss_reason(db: Session, reason: Optional[str]) -> str:
    options = {row["value"] for row in loss_reasons(db)}
    if not options:
        raise AppException(
            status_code=422,
            message=(
                "No loss reasons are configured. Add options to the "
                f"'{QUOTATION_LOSS_REASON_SET_KEY}' lookup set first."
            ),
            code="quotation_loss_reasons_unconfigured",
        )
    if not reason:
        raise AppException(
            status_code=422,
            message="A lost quotation needs a reason -- it is the report management reads.",
            code="quotation_loss_reason_required",
        )
    if reason not in options:
        raise AppException(
            status_code=422,
            message=f"'{reason}' is not a configured loss reason.",
            code="quotation_loss_reason_invalid",
        )
    return reason


def set_outcome(
    db: Session,
    *,
    quotation: ProjectQuotation,
    outcome: str,
    loss_reason: Optional[str] = None,
) -> ProjectQuotation:
    """Win, lose or reopen ONE scope, then re-derive the project's outcome (AC-E10).

    The project's STATUS is deliberately untouched (AC-E10a): status is a funnel
    position and outcome is the commercial result, and a service that moved both would
    make the board disagree with the pipeline.
    """
    if outcome not in QUOTATION_OUTCOMES:
        raise AppException(
            status_code=422,
            message=f"Unknown outcome '{outcome}'.",
            code="quotation_outcome_invalid",
        )

    if outcome == OUTCOME_LOST_Q:
        quotation.loss_reason = _assert_loss_reason(db, loss_reason)
    else:
        quotation.loss_reason = None

    quotation.outcome = outcome
    quotation.decided_at = (
        datetime.utcnow() if outcome != OUTCOME_OPEN_Q else None
    )
    db.flush()

    project = db.query(Project).filter(Project.id == quotation.project_id).first()
    if project:
        sync_project_outcome(db, project)
    return quotation


def derive_project_outcome(db: Session, project: Project) -> str:
    """Won if ANY quotation is won; lost only when ALL are lost; otherwise open.

    No quotations at all is OPEN, not lost: nothing has been decided yet, and a project
    registered this morning is not a loss.
    """
    rows = (
        db.query(ProjectQuotation.outcome, func.count(ProjectQuotation.id))
        .filter(ProjectQuotation.project_id == project.id)
        .group_by(ProjectQuotation.outcome)
        .all()
    )
    counts = {row[0]: row[1] for row in rows}
    if counts.get(OUTCOME_WON_Q):
        return OUTCOME_WON
    total = sum(counts.values())
    if total and counts.get(OUTCOME_LOST_Q, 0) == total:
        return OUTCOME_LOST
    return OUTCOME_OPEN


def sync_project_outcome(db: Session, project: Project) -> str:
    """Write the derived outcome onto the project.

    Stored rather than computed on read because the pipeline list, the forecast and the
    board all filter on it, and a correlated subquery per row would be the N+1 that
    makes the board slow.
    """
    outcome = derive_project_outcome(db, project)
    if project.outcome != outcome:
        project.outcome = outcome
        db.flush()
    return outcome


# ----------------------------------------------------------------- serialize


def serialize_quotations(
    db: Session, quotations: Sequence[ProjectQuotation]
) -> List[Dict[str, Any]]:
    """Bulk, with the current version and alert counts folded in.

    Alert counts come from the CURRENT version only: a breach on a superseded version is
    history, and counting it would make a fixed quotation look permanently broken.
    """
    if not quotations:
        return []

    ids = [q.id for q in quotations]
    version_rows = (
        db.query(ProjectQuotationVersion)
        .filter(ProjectQuotationVersion.quotation_id.in_(ids))
        .all()
    )
    highest: Dict[str, ProjectQuotationVersion] = {}
    counts: Dict[str, int] = {}
    for row in version_rows:
        counts[row.quotation_id] = counts.get(row.quotation_id, 0) + 1
        current = highest.get(row.quotation_id)
        if current is None or row.version_no > current.version_no:
            highest[row.quotation_id] = row

    current_ids = [v.id for v in highest.values()]
    alert_rows = (
        db.query(
            ProjectQuotationLine.version_id,
            func.sum(func.cast(ProjectQuotationLine.is_below_floor, Integer)),
            func.sum(func.cast(ProjectQuotationLine.is_non_standard, Integer)),
            func.count(ProjectQuotationLine.id),
        )
        .filter(ProjectQuotationLine.version_id.in_(current_ids))
        .group_by(ProjectQuotationLine.version_id)
        .all()
        if current_ids
        else []
    )
    alerts = {
        row[0]: {
            "below_floor": int(row[1] or 0),
            "non_standard": int(row[2] or 0),
            "line_count": int(row[3] or 0),
        }
        for row in alert_rows
    }

    series_ids = {q.series_id for q in quotations if q.series_id}
    series_names: Dict[str, str] = {}
    if series_ids:
        from app.models.projects import ProjectSeries

        series_names = {
            row.id: row.name
            for row in db.query(ProjectSeries).filter(ProjectSeries.id.in_(series_ids)).all()
        }

    labels = {row["value"]: row["label"] for row in loss_reasons(db)}

    out: List[Dict[str, Any]] = []
    for quotation in quotations:
        version = highest.get(quotation.id)
        stats = alerts.get(version.id if version else "", {})
        out.append(
            {
                "id": quotation.id,
                "project_id": quotation.project_id,
                "scope_label": quotation.scope_label,
                "series_id": quotation.series_id,
                "series_name": series_names.get(quotation.series_id or ""),
                "notes": quotation.notes,
                "outcome": quotation.outcome,
                "loss_reason": quotation.loss_reason,
                "loss_reason_label": labels.get(quotation.loss_reason or ""),
                "decided_at": quotation.decided_at,
                "version_count": counts.get(quotation.id, 0),
                "current_version_id": version.id if version else None,
                "current_version_no": version.version_no if version else None,
                "current_total": version.total_amount if version else None,
                "below_floor_count": stats.get("below_floor", 0),
                "non_standard_count": stats.get("non_standard", 0),
                "line_count": stats.get("line_count", 0),
                "created_at": quotation.created_at,
                "updated_at": quotation.updated_at,
            }
        )
    return out


def serialize_versions(
    db: Session, versions: Sequence[ProjectQuotationVersion]
) -> List[Dict[str, Any]]:
    if not versions:
        return []
    highest_by_quotation: Dict[str, int] = {}
    for version in versions:
        highest_by_quotation[version.quotation_id] = max(
            highest_by_quotation.get(version.quotation_id, 0), version.version_no
        )
    # The set on screen may be one page of a longer history, so the real maximum has to
    # come from the DB rather than from what happens to be in this list.
    quotation_ids = list(highest_by_quotation)
    rows = (
        db.query(
            ProjectQuotationVersion.quotation_id,
            func.max(ProjectQuotationVersion.version_no),
        )
        .filter(ProjectQuotationVersion.quotation_id.in_(quotation_ids))
        .group_by(ProjectQuotationVersion.quotation_id)
        .all()
    )
    real_max = {row[0]: row[1] for row in rows}

    # Which of these versions has already gone out to a customer. Batched rather than asked per
    # row: the editor needs it for every version it renders, and `is_current` alone cannot answer
    # it - an issued version is still the HIGHEST version until somebody revises, so a screen
    # gated on `is_current` offers edits the server then refuses with 422.
    issued_ids = {
        row[0]
        for row in db.query(ProjectQuotationIssueScope.version_id)
        .filter(ProjectQuotationIssueScope.version_id.in_([v.id for v in versions]))
        .all()
    }

    issuer_ids = {v.issued_by for v in versions if v.issued_by}
    names: Dict[str, str] = {}
    if issuer_ids:
        from app.models.user import User

        names = {
            row.id: (row.name or row.email)
            for row in db.query(User).filter(User.id.in_(issuer_ids)).all()
        }

    return [
        {
            "id": version.id,
            "quotation_id": version.quotation_id,
            "version_no": version.version_no,
            "is_current": version.version_no == real_max.get(version.quotation_id),
            # Sent to the customer, so its rows are what they hold and cannot be rewritten.
            "is_issued": version.id in issued_ids,
            # The one flag the editor should gate on: superseded OR issued.
            "is_editable": (
                version.version_no == real_max.get(version.quotation_id)
                and version.id not in issued_ids
            ),
            "frozen_at": version.frozen_at,
            "issued_by": version.issued_by,
            "issued_by_name": names.get(version.issued_by or ""),
            "issued_on": version.issued_on,
            "total_amount": version.total_amount,
            "notes": version.notes,
            "created_at": version.created_at,
        }
        for version in versions
    ]


def serialize_lines(
    db: Session, lines: Sequence[ProjectQuotationLine]
) -> List[Dict[str, Any]]:
    # S21. The product's chosen photograph, resolved for the WHOLE table in one pass: a scope
    # runs to 52 lines and a query per row would be 52 round trips to draw one column.
    from app.services import project_quotation_image_service

    # A frozen version is what the customer holds: nothing on it can be picked any more, so its
    # lines carry no candidates (and nothing is signed for a picker that cannot open).
    pictures = project_quotation_image_service.line_images(
        db, lines, frozen_versions=frozen_version_ids(db, [line.version_id for line in lines])
    )
    no_cells: Dict[str, Any] = {}
    return [
        {
            "id": line.id,
            "version_id": line.version_id,
            "product_id": line.product_id,
            "product_code": line.product_code_snapshot,
            "description": line.description_snapshot,
            "list_price": line.list_price_snapshot,
            "image_attachment_id": line.image_attachment_id,
            "product_image": pictures.get(str(line.id), no_cells).get("product_image"),
            "drawing_attachment_id": line.drawing_attachment_id,
            "technical_drawing": pictures.get(str(line.id), no_cells).get("technical_drawing"),
            "unit_price": line.unit_price,
            "quantity": line.quantity,
            "uom": line.uom,
            "unit_type": line.unit_type,
            "line_total": line.line_total,
            # Printed as a rate, counted as nothing. The screen needs it to render "rate only"
            # in the money column and to keep it out of the footer sum.
            "is_rate_only": bool(line.is_rate_only),
            "item_label": line.item_label,
            "brand": line.brand_snapshot,
            "technical_spec": line.technical_spec,
            "complete_set": line.complete_set,
            "band_label": line.band_label,
            "is_non_standard": line.is_non_standard,
            "floor_value_applied": line.floor_value_applied,
            "floor_level_applied": line.floor_level_applied,
            "is_below_floor": line.is_below_floor,
            "sort_order": line.sort_order,
            "notes": line.notes,
        }
        for line in lines
    ]
