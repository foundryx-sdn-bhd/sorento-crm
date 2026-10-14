"""Quotation API (S3, UAC Group E).

Three route shapes, and the URLs say which is which:

- ``/projects/{id}/quotations`` -- the scopes of one project.
- ``/quotations/{id}/versions`` and ``/versions/{id}/lines`` -- the revision history and
  its contents. Lines hang off a VERSION, never off a quotation, because "which version
  was this line on" is the whole point of the model.
- ``/config/series`` and ``/config/price-floors`` -- the two pieces of policy the alerts
  read. Under /config with the project types, because that is where an admin already is.

Editing a frozen version returns 422 from the service; the route does not second-guess
it, so the rule lives in exactly one place.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.projects._common import acting_company_id, permission_slugs
from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.common import ListResponse
from app.schemas.projects import (
    PriceFloorEffectiveResponse,
    PriceFloorRuleResponse,
    PriceFloorRuleUpsert,
    ProjectQuotationCreate,
    ProjectQuotationLineBulkWrite,
    ProjectQuotationLineCreate,
    ProjectQuotationLineResponse,
    ProjectQuotationLineUpdate,
    ProjectQuotationOutcomeRequest,
    ProjectQuotationResponse,
    ProjectQuotationUpdate,
    ProjectQuotationVersionResponse,
    ProjectSeriesCreate,
    ProjectSeriesProductImport,
    ProjectSeriesProductImportResponse,
    SeriesProductImportJobResponse,
    ProjectSeriesResponse,
    ProjectSeriesUpdate,
    QuotationLineImagesResponse,
    QuotationLineVerdictResponse,
    QuotationRecomputeResponse,
    SeriesProductPricingUpdate,
    SeriesProductRowResponse,
    SeriesSetRowResponse,
)
from app.services import project_pricing_service as pricing
from app.services.error_handler import AppException
from app.services import project_quotation_service as svc
from app.services import project_service as projects
from app.services.error_handler import handle_internal_error
from app.services.uuid_path_param import UUID_PATTERN, validate_uuid_path

logger = logging.getLogger(__name__)

router = APIRouter()

VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"
DELETE = "projects.projects.delete"
CONFIG_VIEW = "projects.types.view"
CONFIG_EDIT = "projects.types.edit"


def _envelope(data: List[dict]):
    return {
        "data": data,
        "pagination": {"total": len(data), "page": 1, "limit": max(len(data), 1)},
        "empty": not data,
    }


def _notify_breaches(db: Session, line, actor_user_id: str) -> None:
    """Tell management a line just went below its floor (AC-E6, AC-E6a).

    Best-effort by design: the price is already saved by the time this runs, and a
    notification backend that is down must not 500 a successful save. Same rule as every
    other post-commit side effect in the codebase.
    """
    events = svc.pop_breach_events(line)
    if not events:
        return
    for event in events:
        logger.warning(
            "project quotation line below floor: line=%s quotation=%s price=%s floor=%s (%s)",
            event["line_id"],
            event["quotation_id"],
            event["unit_price"],
            event["floor_value"],
            event["floor_level"],
        )
    # S5b: the fan-out itself. Management only -- the person who typed the price does not
    # need telling what they just did, and the recipient rule lives in one place
    # (project_notify_service) so it cannot drift from the PO and staleness alerts.
    from app.models.projects import Project, ProjectQuotation
    from app.services import project_notify_service as notify

    project = (
        db.query(Project)
        .join(ProjectQuotation, ProjectQuotation.project_id == Project.id)
        .filter(ProjectQuotation.id == str(events[0]["quotation_id"]))
        .first()
    )
    if project is None:
        return
    for event in events:
        notify.notify_floor_breach(
            db, project=project, event=event, actor_user_id=actor_user_id
        )


# --------------------------------------------------------------- quotations


@router.get(
    "/projects/{project_id}/quotations",
    response_model=ListResponse[ProjectQuotationResponse],
)
async def list_quotations(
    project_id: str,
    # API-key readable (AC-K1): `crm_project_quotations_list` answers "what did we quote".
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        from app.models.projects import ProjectQuotation

        rows = (
            db.query(ProjectQuotation)
            .filter(ProjectQuotation.project_id == project_id)
            .order_by(ProjectQuotation.created_at.asc())
            .all()
        )
        return _envelope(svc.serialize_quotations(db, rows))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotations",
    response_model=ProjectQuotationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_quotation(
    project_id: str,
    payload: ProjectQuotationCreate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Opens version 1 with it: a quotation with no version has nowhere to put a line."""
    try:
        validate_uuid_path(project_id, resource="Project")
        project = projects.get_project_or_404(db, project_id)
        projects.assert_can_edit_project(
            db, project, current_user["id"], permission_slugs(db, current_user["id"])
        )
        quotation = svc.create_quotation(
            db,
            project=project,
            actor_user_id=current_user["id"],
            payload=payload.model_dump(exclude_unset=True),
        )
        db.commit()
        db.refresh(quotation)
        return svc.serialize_quotations(db, [quotation])[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put("/quotations/{quotation_id}", response_model=ProjectQuotationResponse)
async def update_quotation(
    quotation_id: str,
    payload: ProjectQuotationUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(quotation_id, resource="Quotation")
        quotation = _quotation_for_edit(db, quotation_id, current_user)
        svc.update_quotation(
            db, quotation=quotation, payload=payload.model_dump(exclude_unset=True)
        )
        db.commit()
        db.refresh(quotation)
        return svc.serialize_quotations(db, [quotation])[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/quotations/{quotation_id}/outcome", response_model=ProjectQuotationResponse)
async def set_quotation_outcome(
    quotation_id: str,
    payload: ProjectQuotationOutcomeRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Win or lose ONE scope; the project's outcome is re-derived from all of them.

    The project's STATUS is deliberately untouched (AC-E10a) -- a project at "PO
    Received" with an open Common Area scope is still live on the board.
    """
    try:
        validate_uuid_path(quotation_id, resource="Quotation")
        quotation = _quotation_for_edit(db, quotation_id, current_user)
        svc.set_outcome(
            db,
            quotation=quotation,
            outcome=payload.outcome,
            loss_reason=payload.loss_reason,
        )
        db.commit()
        db.refresh(quotation)
        return svc.serialize_quotations(db, [quotation])[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete("/quotations/{quotation_id}")
async def delete_quotation(
    quotation_id: str,
    current_user: dict = Depends(require_permission(DELETE)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(quotation_id, resource="Quotation")
        quotation = _quotation_for_edit(db, quotation_id, current_user)
        svc.delete_quotation(db, quotation)
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


def _quotation_for_edit(db: Session, quotation_id: str, current_user: dict):
    """Load a quotation and check edit rights on its PROJECT.

    Rights live on the project, not the quotation: the owner and collaborators of a
    pursuit are who may price it, and duplicating that rule here would be a second copy
    to keep in step.
    """
    from app.models.projects import ProjectQuotation

    quotation = (
        db.query(ProjectQuotation).filter(ProjectQuotation.id == quotation_id).first()
    )
    if not quotation:
        from app.services.error_handler import AppException

        raise AppException(
            status_code=404, message="Quotation not found.", code="quotation_not_found"
        )
    project = projects.get_project_or_404(db, quotation.project_id)
    projects.assert_can_edit_project(
        db, project, current_user["id"], permission_slugs(db, current_user["id"])
    )
    return quotation


@router.get(
    "/quotations/{quotation_id}/line-verdict",
    response_model=QuotationLineVerdictResponse,
)
async def judge_draft_line(
    quotation_id: str,
    product_id: Optional[str] = Query(None),
    product_set_id: Optional[str] = Query(None, pattern=UUID_PATTERN),
    unit_price: Optional[str] = Query(None),
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Judge one DRAFT line, before anything is saved.

    The client's requirement, verbatim: "the computation of whether it is non standard or
    off catalog needs to be on the spot, cannot wait until I save". The verdicts cannot be
    computed in the browser - series membership counts nominated CATEGORIES the browser
    never fetched, and the floor means walking the category ancestry - so the browser asks,
    per keystroke-settled draft, and this answers with the same `is_in_series` and
    `resolve_floor` the save path runs. One implementation, two moments.

    Nothing is written. `unit_price` arrives as the decimal STRING the drafts hold; a
    half-typed price that does not parse is judged as "no price yet", not an error - the
    person is mid-keystroke, and a 422 would toast at them for typing.

    An off-catalog draft (no product) is non-standard whenever a series is nominated, and
    has no floor - the same rule the save applies (AC-E5).
    """
    from decimal import Decimal, InvalidOperation

    try:
        validate_uuid_path(quotation_id, resource="Quotation")
        from app.models.product import Product
        from app.models.projects import ProjectQuotation

        quotation = (
            db.query(ProjectQuotation).filter(ProjectQuotation.id == quotation_id).first()
        )
        if not quotation:
            raise AppException(
                status_code=404, message="Quotation not found.", code="quotation_not_found"
            )

        price: Optional[Decimal] = None
        if unit_price:
            try:
                price = Decimal(unit_price)
            except InvalidOperation:
                price = None

        if product_set_id:
            # A set draft: the same two rules the save runs, on the set's own series row and
            # the company default. A set the company scope cannot see judges as not in series.
            from app.models.product_set import ProductSet
            from app.services import product_set_service

            product_set = (
                db.query(ProductSet).filter(ProductSet.id == product_set_id).first()
            )
            in_series = product_set is not None and pricing.is_set_in_series(
                db, quotation.series_id, product_set.id
            )
            if product_set is None and not quotation.series_id:
                in_series = True
            floor = (
                pricing.resolve_set_floor(
                    db,
                    company_id=acting_company_id(db),
                    product_set=product_set,
                    list_price=product_set_service.resolve_set_price(product_set).resolved,
                    series_id=quotation.series_id,
                )
                if product_set is not None
                else None
            )
            below = bool(floor and price is not None and price < floor.value)
            return {
                "is_non_standard": not in_series,
                "is_below_floor": below,
                "floor_value": floor.value if floor else None,
                "floor_level": floor.level if floor else None,
            }

        product = (
            db.query(Product).filter(Product.id == product_id).first()
            if product_id
            else None
        )
        # A product id the company scope cannot see judges exactly like no product at all:
        # off-catalog. This is the Mocha-copy case - the row exists, but not for us.
        in_series = pricing.is_in_series(
            db, series_id=quotation.series_id, product=product
        )

        floor = pricing.resolve_floor(
            db,
            company_id=acting_company_id(db),
            product=product,
            series_id=quotation.series_id,
        )
        below = bool(floor and price is not None and price < floor.value)
        return {
            "is_non_standard": not in_series,
            "is_below_floor": below,
            "floor_value": floor.value if floor else None,
            "floor_level": floor.level if floor else None,
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/quotation-line-images", response_model=QuotationLineImagesResponse)
async def quotation_line_images(
    product_id: str = Query(..., pattern=UUID_PATTERN),
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The photo and drawing cells a line with this product would show, before it is saved.

    A new line (or one whose product was just changed) has no stored row to read its pictures
    from, so the editor asks here and can show the thumbnails and offer the picker at once. Same
    answer as a saved editable line with no picks; a product this company cannot see is 404.
    """
    try:
        from app.models.product import Product
        from app.services import project_quotation_image_service as line_images

        if db.query(Product.id).filter(Product.id == product_id).first() is None:
            raise AppException(
                status_code=404, message="Product not found.", code="product_not_found"
            )
        return line_images.product_cells(db, product_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# ----------------------------------------------------------------- versions


@router.get(
    "/quotations/{quotation_id}/versions",
    response_model=ListResponse[ProjectQuotationVersionResponse],
)
async def list_versions(
    quotation_id: str,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Newest first. ``is_current`` is derived from MAX(version_no), not stored."""
    try:
        validate_uuid_path(quotation_id, resource="Quotation")
        rows = svc.list_versions(db, quotation_id)
        return _envelope(svc.serialize_versions(db, rows))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/quotations/{quotation_id}/revise",
    response_model=ProjectQuotationVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def revise_quotation(
    quotation_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Freeze the current version, open the next with its lines carried across (AC-E3)."""
    try:
        validate_uuid_path(quotation_id, resource="Quotation")
        quotation = _quotation_for_edit(db, quotation_id, current_user)
        version = svc.revise(db, quotation=quotation, actor_user_id=current_user["id"])
        db.commit()
        db.refresh(version)
        return svc.serialize_versions(db, [version])[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# -------------------------------------------------------------------- lines


@router.get(
    "/quotation-versions/{version_id}/lines",
    response_model=ListResponse[ProjectQuotationLineResponse],
)
async def list_lines(
    version_id: str,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(version_id, resource="Quotation version")
        return _envelope(svc.serialize_lines(db, svc.list_lines(db, version_id)))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/quotation-versions/{version_id}/lines",
    response_model=ProjectQuotationLineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_line(
    version_id: str,
    payload: ProjectQuotationLineCreate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(version_id, resource="Quotation version")
        version, quotation = _version_for_edit(db, version_id, current_user)
        body = payload.model_dump(exclude_unset=True)
        svc.assert_line_picks(db, quotation=quotation, payload=body)
        line = svc.upsert_line(
            db,
            version=version,
            actor_user_id=current_user["id"],
            payload=body,
        )
        _notify_breaches(db, line, current_user["id"])
        db.commit()
        db.refresh(line)
        return svc.serialize_lines(db, [line])[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put(
    "/quotation-versions/{version_id}/lines",
    response_model=ListResponse[ProjectQuotationLineResponse],
)
async def replace_lines(
    version_id: str,
    payload: ProjectQuotationLineBulkWrite,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Write the WHOLE line set of one version in a single transaction (S10).

    **Why this exists.** The editor writes per row today, so building a ten-line scope is ten
    writes and a half-failed sequence leaves a quotation in a state nobody typed. One request
    makes a Save atomic: either the arrangement the user made is what is stored, or nothing
    moved.

    **The body is the full desired set, in display order.** A line already stored carries its
    ``id``; a new one arrives without one. **Anything stored whose id is absent from the body
    is DELETED** - a client that omits the lines it did not touch will silently wipe them, so
    it must always send everything it is showing. ``sort_order`` comes from array position, so
    reordering is just sending a different order.

    A FROZEN or ISSUED version is refused with the same 422 the per-row routes raise, before
    anything is written.

    Answers the same envelope as ``GET .../lines``, so the client reads one shape.
    """
    try:
        validate_uuid_path(version_id, resource="Quotation version")
        version, _quotation = _version_for_edit(db, version_id, current_user)
        lines = svc.replace_lines(
            db,
            version=version,
            actor_user_id=current_user["id"],
            lines=[item.model_dump(exclude_unset=True) for item in payload.lines],
        )
        for line in lines:
            _notify_breaches(db, line, current_user["id"])
        db.commit()
        return _envelope(svc.serialize_lines(db, svc.list_lines(db, version_id)))
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put(
    "/quotation-versions/{version_id}/lines/{line_id}",
    response_model=ProjectQuotationLineResponse,
)
async def update_line(
    version_id: str,
    line_id: str,
    payload: ProjectQuotationLineUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Saves IN PLACE on the current version (AC-E2). No new version is created."""
    try:
        validate_uuid_path(version_id, resource="Quotation version")
        validate_uuid_path(line_id, resource="Line")
        version, quotation = _version_for_edit(db, version_id, current_user)
        line = _line_or_404(db, version_id, line_id)
        body = payload.model_dump(exclude_unset=True)
        svc.assert_line_picks(db, quotation=quotation, payload=body, line=line)
        line = svc.upsert_line(
            db,
            version=version,
            actor_user_id=current_user["id"],
            line=line,
            payload=body,
        )
        _notify_breaches(db, line, current_user["id"])
        db.commit()
        db.refresh(line)
        return svc.serialize_lines(db, [line])[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete("/quotation-versions/{version_id}/lines/{line_id}")
async def delete_line(
    version_id: str,
    line_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(version_id, resource="Quotation version")
        validate_uuid_path(line_id, resource="Line")
        version, _quotation = _version_for_edit(db, version_id, current_user)
        line = _line_or_404(db, version_id, line_id)
        svc.delete_line(db, version=version, line=line)
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/quotation-versions/{version_id}/recompute",
    response_model=QuotationRecomputeResponse,
)
async def recompute_version(
    version_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Re-ask both guardrails for this version against today's master data (S19).

    The client's own words: "I need to have a recompute button rather than you go and bulk
    write the data, like, a refresh button that recompute this, in case someone change at
    the master data (product or any configuration), then the quotation can refresh to
    repull this". This is that button, and it is also how the stale flags get corrected -
    by somebody pressing it, not by a migration that can only be right once.

    ONE version, named in the URL, and only while it is still editable. A frozen or issued
    version refuses with the same 422 every other write raises: those flags are what was
    true when the customer was sent the paper.

    Synchronous, because a version is tens of lines. Returns what MOVED rather than a bare
    success - the answer to "what did that do" is the whole point of the control.
    """
    try:
        validate_uuid_path(version_id, resource="Quotation version")
        version, _quotation = _version_for_edit(db, version_id, current_user)
        report = svc.recompute_version(db, version=version)
        # A line that has JUST fallen below a floor is news to management, exactly as it is
        # on a save. Transition-only, so re-confirming forty lines notifies nobody.
        _notify_recompute_breaches(db, report, current_user["id"])
        db.commit()
        return {key: value for key, value in report.items() if key != "breach_events"}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


def _notify_recompute_breaches(db: Session, report: dict, actor_user_id: str) -> None:
    """The same fan-out ``_notify_breaches`` performs, off a report instead of a line.

    Best-effort by design, like every other post-commit-shaped side effect here: the flags
    are already recomputed by the time this runs, and a notification backend that is down
    must not 500 an operation that succeeded.
    """
    events = report.get("breach_events") or []
    if not events:
        return
    from app.models.projects import Project, ProjectQuotation
    from app.services import project_notify_service as notify

    project = (
        db.query(Project)
        .join(ProjectQuotation, ProjectQuotation.project_id == Project.id)
        .filter(ProjectQuotation.id == str(report["quotation_id"]))
        .first()
    )
    if project is None:
        return
    for event in events:
        logger.warning(
            "project quotation recompute: line=%s now below floor %s (%s)",
            event["line_id"],
            event["floor_value"],
            event["floor_level"],
        )
        try:
            notify.notify_floor_breach(
                db, project=project, event=event, actor_user_id=actor_user_id
            )
        except Exception:  # noqa: BLE001 -- never fail a recompute over a notification
            logger.exception("project quotation recompute: breach notification failed")


def _version_for_edit(db: Session, version_id: str, current_user: dict):
    from app.models.projects import ProjectQuotationVersion

    from app.services.error_handler import AppException

    version = (
        db.query(ProjectQuotationVersion)
        .filter(ProjectQuotationVersion.id == version_id)
        .first()
    )
    if not version:
        raise AppException(
            status_code=404,
            message="Quotation version not found.",
            code="quotation_version_not_found",
        )
    quotation = _quotation_for_edit(db, version.quotation_id, current_user)
    return version, quotation


def _line_or_404(db: Session, version_id: str, line_id: str):
    from app.models.projects import ProjectQuotationLine

    from app.services.error_handler import AppException

    line = (
        db.query(ProjectQuotationLine)
        .filter(
            ProjectQuotationLine.id == line_id,
            ProjectQuotationLine.version_id == version_id,
        )
        .first()
    )
    if not line:
        raise AppException(
            status_code=404, message="Line not found.", code="quotation_line_not_found"
        )
    return line


# ------------------------------------------------------------------- config


@router.get("/config/loss-reasons", response_model=List[dict])
async def list_loss_reasons(
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The configured loss reasons (AC-E9). Empty means an admin has not set any up, and
    marking a quotation lost will refuse until they do."""
    try:
        return svc.loss_reasons(db)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/config/series", response_model=ListResponse[ProjectSeriesResponse])
async def list_series(
    include_inactive: bool = Query(False),
    _user: dict = Depends(require_permission(CONFIG_VIEW)),
    db: Session = Depends(get_db),
):
    try:
        return _envelope(
            _serialize_series(
                db,
                company_id=acting_company_id(db),
                include_inactive=include_inactive,
            )
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/config/series",
    response_model=ProjectSeriesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_series(
    payload: ProjectSeriesCreate,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    try:
        from app.models.projects import ProjectSeries

        body = payload.model_dump(exclude_unset=True)
        category_ids = body.pop("category_ids", []) or []
        series = ProjectSeries(company_id=acting_company_id(db), **body)
        db.add(series)
        db.flush()
        pricing.set_series_categories(db, series=series, category_ids=category_ids)
        db.commit()
        db.refresh(series)
        return _serialize_series(db, company_id=series.company_id, series_id=series.id)[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put("/config/series/{series_id}", response_model=ProjectSeriesResponse)
async def update_series(
    series_id: str,
    payload: ProjectSeriesUpdate,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(series_id, resource="Series")
        from app.models.projects import ProjectSeries

        from app.services.error_handler import AppException

        series = db.query(ProjectSeries).filter(ProjectSeries.id == series_id).first()
        if not series:
            raise AppException(
                status_code=404, message="Series not found.", code="series_not_found"
            )

        body = payload.model_dump(exclude_unset=True)
        category_ids = body.pop("category_ids", None)
        for field, value in body.items():
            setattr(series, field, value)
        if category_ids is not None:
            pricing.set_series_categories(db, series=series, category_ids=category_ids)
        db.commit()
        db.refresh(series)
        return _serialize_series(db, company_id=series.company_id, series_id=series.id)[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete("/config/series/{series_id}")
async def delete_series(
    series_id: str,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Blocked while a quotation still nominates it: deleting would silently turn every
    line on that quotation standard, erasing an alert somebody was acting on."""
    try:
        validate_uuid_path(series_id, resource="Series")
        from app.models.projects import ProjectQuotation, ProjectSeries

        from app.services.error_handler import AppException

        series = db.query(ProjectSeries).filter(ProjectSeries.id == series_id).first()
        if not series:
            raise AppException(
                status_code=404, message="Series not found.", code="series_not_found"
            )
        in_use = (
            db.query(ProjectQuotation)
            .filter(ProjectQuotation.series_id == series_id)
            .count()
        )
        if in_use:
            raise AppException(
                status_code=409,
                message=(
                    f"{in_use} quotation(s) use this series. Deactivate it instead, so "
                    "their alerts stay meaningful."
                ),
                code="series_in_use",
            )
        db.delete(series)
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


def _series_or_404(db: Session, series_id: str):
    from app.models.projects import ProjectSeries

    from app.services.error_handler import AppException

    series = db.query(ProjectSeries).filter(ProjectSeries.id == series_id).first()
    if not series:
        raise AppException(
            status_code=404, message="Series not found.", code="series_not_found"
        )
    return series


@router.get(
    "/config/series/{series_id}/products/rows",
    response_model=ListResponse[SeriesProductRowResponse],
)
async def list_series_product_rows(
    series_id: str,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The series' products with their price, percentage and derived floor (T2).

    `/rows` rather than replacing the POST on `/products`: that path already means "load a
    list of codes onto this series" and quietly giving the same path a second meaning by
    method is how a route ends up doing two jobs nobody can name.
    """
    try:
        validate_uuid_path(series_id, resource="Series")
        _series_or_404(db, series_id)
        return _envelope(pricing.series_product_rows(db, series_id))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.patch(
    "/config/series/{series_id}/products/{product_id}",
    response_model=SeriesProductRowResponse,
)
async def update_series_product_pricing(
    series_id: str,
    product_id: str,
    payload: SeriesProductPricingUpdate,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Set or clear one product's price and percentage, from the table on the series page."""
    try:
        validate_uuid_path(series_id, resource="Series")
        validate_uuid_path(product_id, resource="Product")
        series = _series_or_404(db, series_id)
        row = pricing.set_series_product_pricing(
            db,
            series=series,
            product_id=product_id,
            selling_price=payload.selling_price,
            max_discount_pct=payload.max_discount_pct,
        )
        db.commit()
        return row
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete(
    "/config/series/{series_id}/products/{product_id}",
    status_code=204,
)
async def remove_series_product(
    series_id: str,
    product_id: str,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Take one product off the series. Hard delete, confirmed on the screen."""
    try:
        validate_uuid_path(series_id, resource="Series")
        validate_uuid_path(product_id, resource="Product")
        series = _series_or_404(db, series_id)
        if not pricing.remove_series_product(db, series=series, product_id=product_id):
            raise AppException(
                status_code=404,
                message="That product is not in this series.",
                code="series_product_not_found",
            )
        db.commit()
        return Response(status_code=204)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/config/series/{series_id}/sets/rows",
    response_model=ListResponse[SeriesSetRowResponse],
)
async def list_series_set_rows(
    series_id: str,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The series' product sets with their price, percentage and derived floor."""
    try:
        validate_uuid_path(series_id, resource="Series")
        _series_or_404(db, series_id)
        return _envelope(pricing.series_set_rows(db, series_id))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.patch(
    "/config/series/{series_id}/sets/{product_set_id}",
    response_model=SeriesSetRowResponse,
)
async def update_series_set_pricing(
    series_id: str,
    product_set_id: str,
    payload: SeriesProductPricingUpdate,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Add a set to the series or set its price and percentage (an upsert)."""
    try:
        validate_uuid_path(series_id, resource="Series")
        validate_uuid_path(product_set_id, resource="Product set")
        series = _series_or_404(db, series_id)
        row = pricing.set_series_set_pricing(
            db,
            series=series,
            product_set_id=product_set_id,
            selling_price=payload.selling_price,
            max_discount_pct=payload.max_discount_pct,
        )
        db.commit()
        return row
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete(
    "/config/series/{series_id}/sets/{product_set_id}",
    status_code=204,
)
async def remove_series_set(
    series_id: str,
    product_set_id: str,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Take one set off the series."""
    try:
        validate_uuid_path(series_id, resource="Series")
        validate_uuid_path(product_set_id, resource="Product set")
        series = _series_or_404(db, series_id)
        if not pricing.remove_series_set(db, series=series, product_set_id=product_set_id):
            raise AppException(
                status_code=404,
                message="That set is not in this series.",
                code="series_set_not_found",
            )
        db.commit()
        return Response(status_code=204)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/config/series/{series_id}/products",
    response_model=ProjectSeriesProductImportResponse,
)
async def import_series_products(
    series_id: str,
    payload: ProjectSeriesProductImport,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Load a list of product CODES onto a series (S18).

    The client's definition of "standard" is a list of codes, not a set of categories:
    "any product that is not in the sheet that I provided you are flagged as non standard".
    Their 151-cell sheet reaches 167 catalogue rows across 31 categories holding 15,048
    products, so nominating categories could not express it.

    Every code that did NOT match comes back in the response. That is deliberate and it is
    the more useful half of the answer: their sheet quotes base codes the catalogue only
    stocks as suffixed variants, so a third of a real list can miss, and a loader that
    dropped them silently would turn a disagreement between two systems into a number
    nobody could interrogate.
    """
    try:
        validate_uuid_path(series_id, resource="Series")
        series = _series_or_404(db, series_id)
        report = pricing.apply_series_product_codes(
            db,
            series=series,
            codes=payload.codes,
            mode=payload.mode,
        )
        db.commit()
        return report
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/config/series/{series_id}/products/upload",
    response_model=SeriesProductImportJobResponse,
    status_code=202,
)
async def upload_series_products(
    series_id: str,
    file: UploadFile = File(..., description="An .xlsx or .csv carrying a PRODUCT CODE column"),
    mode: str = Form("append"),
    user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """The same import, off the file the admin already has. Queued, not run here.

    **202, not 200.** The client's workbook is 9.2 MB and takes seconds to open. This route
    is `async def`, so parsing it inline ran on the event loop and stalled every other
    request in the process for the duration - the upload was not merely slow for the person
    who started it, it was slow for everybody. The read now happens on the imports worker
    and this returns a job id the browser polls at `GET /system/jobs/{job_id}/status`, which
    already reports progress, the finished report, and the error if it died.

    The file is validated for emptiness and size HERE, synchronously, because both are
    instant and a queued job that fails a second later on "that file is empty" is a worse
    way to say the same thing.

    The codes are read by HEADING across every sheet, never by column position: the client's
    own workbook puts a title on row 1, the headings on row 2 and the codes in column F, and
    a positional reader would turn the day a column moves into a hundred wrong products
    called standard.

    Three columns are read: the code, the DEVELOPERS price the series sells at, and the
    DISTRIBUTORS percentage a distributor may take off it. The PRODUCT IMAGE column is still
    ignored - that decision belongs to `product_attachments.is_primary` and reading it here
    would be a second source of truth - and so are the empty CENTRAL / NORTHEN / SOUTHERN
    columns, which would be a per-region price model rather than this one.
    """
    try:
        validate_uuid_path(series_id, resource="Series")
        from app.services.error_handler import AppException
        from app.services.job_service import JobService
        from app.services.queue_service import enqueue_job
        from app.tasks.project_document_tasks import PROJECT_DOCS_QUEUE
        from app.tasks.project_series_tasks import JOB_TYPE, process_series_product_import

        if mode not in pricing.SERIES_IMPORT_MODES:
            raise AppException(
                status_code=422,
                message=f"Unknown mode '{mode}'. Use append or replace.",
                code="series_import_mode_invalid",
            )

        series = _series_or_404(db, series_id)
        content = await file.read()
        if not content:
            raise AppException(
                status_code=422, message="That file is empty.", code="series_import_empty_file"
            )
        if len(content) > _SERIES_UPLOAD_MAX_BYTES:
            raise AppException(
                status_code=422,
                message="That file is larger than 10 MB. Paste the codes instead.",
                code="series_import_file_too_large",
            )

        filename = file.filename or "products.xlsx"
        job_service = JobService(db)
        job = job_service.create_job(
            job_type=JOB_TYPE,
            user_id=user["id"],
            filename=filename,
            metadata={"series_id": str(series.id), "mode": mode},
        )
        db.commit()
        rq_job = enqueue_job(
            process_series_product_import,
            str(job.id),
            content,
            filename,
            str(series.id),
            mode,
            user["id"],
            # `project_docs`, NOT `imports`. Every checkout shares one Redis, and the
            # workers running out of the other worktrees listen on `imports` without
            # having this task module - one of them would claim the job and fail it on
            # import, which reads as a bug in the code that enqueued it. `project_docs`
            # is this checkout's own queue.
            queue_name=PROJECT_DOCS_QUEUE,
            job_timeout=1800,
            # Pre-assign the RQ id to the DB job_id: the worker can finish before this
            # request commits, and rewriting the id afterwards makes its completion land
            # on a row nobody is polling.
            job_id=str(job.job_id),
        )
        job_service.update_job_with_rq_id(job, rq_job.id)
        return {"job_id": rq_job.id, "series_id": str(series.id), "mode": mode}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# A list of codes, not a document. Ten megabytes is already far past any real sheet and
# still small enough that reading it into memory is not a decision worth agonising over.
_SERIES_UPLOAD_MAX_BYTES = 10 * 1024 * 1024


def _serialize_series(
    db: Session,
    *,
    company_id: str,
    series_id: Optional[str] = None,
    include_inactive: bool = True,
) -> List[dict]:
    from app.models.product import Product, ProductCategory
    from app.models.projects import (
        ProjectQuotation,
        ProjectSeries,
        ProjectSeriesCategory,
        ProjectSeriesProduct,
    )

    query = db.query(ProjectSeries).filter(ProjectSeries.company_id == company_id)
    if series_id:
        query = query.filter(ProjectSeries.id == series_id)
    elif not include_inactive:
        query = query.filter(ProjectSeries.is_active.is_(True))
    rows = query.order_by(ProjectSeries.name.asc()).all()
    if not rows:
        return []

    ids = [row.id for row in rows]
    nominated: dict = {}
    for link in (
        db.query(ProjectSeriesCategory)
        .filter(ProjectSeriesCategory.series_id.in_(ids))
        .all()
    ):
        nominated.setdefault(link.series_id, []).append(link.category_id)

    category_ids = {cid for values in nominated.values() for cid in values}
    names = (
        {
            row.id: row.category_name
            for row in db.query(ProductCategory)
            .filter(ProductCategory.id.in_(category_ids))
            .all()
        }
        if category_ids
        else {}
    )

    # Products nominated BY NAME (S18), resolved straight to their codes: the screen names
    # them, and no UUID reaches the UI. One query for every series on the page.
    nominated_products: dict = {}
    product_links = (
        db.query(ProjectSeriesProduct.series_id, Product.product_code)
        .join(Product, Product.id == ProjectSeriesProduct.product_id)
        .filter(ProjectSeriesProduct.series_id.in_(ids))
        .order_by(Product.product_code.asc())
        .all()
    )
    for link_series_id, product_code in product_links:
        nominated_products.setdefault(link_series_id, []).append(product_code)

    brand_ids = {row.brand_id for row in rows if row.brand_id}
    brand_names = {}
    if brand_ids:
        from app.models.product import Brand

        brand_names = {
            row.id: row.brand_name
            for row in db.query(Brand).filter(Brand.id.in_(brand_ids)).all()
        }

    from sqlalchemy import func

    counts = dict(
        db.query(ProjectQuotation.series_id, func.count(ProjectQuotation.id))
        .filter(ProjectQuotation.series_id.in_(ids))
        .group_by(ProjectQuotation.series_id)
        .all()
    )

    out = []
    for row in rows:
        mine = nominated.get(row.id, [])
        codes = nominated_products.get(row.id, [])
        out.append(
            {
                "id": row.id,
                "name": row.name,
                "brand_id": row.brand_id,
                "brand_name": brand_names.get(row.brand_id or ""),
                "description": row.description,
                "is_active": row.is_active,
                "category_ids": mine,
                "category_names": [names.get(cid, "") for cid in mine],
                "covered_category_count": len(
                    pricing.category_with_descendants(db, mine)
                ),
                "product_count": len(codes),
                "product_codes": codes,
                "quotation_count": counts.get(row.id, 0),
            }
        )
    return out


@router.get("/config/price-floors", response_model=ListResponse[PriceFloorRuleResponse])
async def list_price_floors(
    _user: dict = Depends(require_permission(CONFIG_VIEW)),
    db: Session = Depends(get_db),
):
    """Most specific first, which is the order they take effect in."""
    try:
        rules = pricing.list_floor_rules(db, company_id=acting_company_id(db))
        return _envelope(_serialize_floors(db, rules))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/config/price-floors/effective",
    response_model=PriceFloorEffectiveResponse,
)
async def get_effective_price_floor(
    product_id: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    _user: dict = Depends(require_permission(CONFIG_VIEW)),
    db: Session = Depends(get_db),
):
    """What governs ONE product or ONE category.

    Declared before ``/config/price-floors/{rule_id}`` so the literal wins the match: as
    a rule id it would 404 while looking like empty data.

    This exists because the listing endpoint cannot answer the question. A product with
    no rule of its own is still governed by one, and finding out which needs the category
    ancestry walk -- server-side work that the product editor would otherwise have to
    re-implement in the browser and be free to disagree with.
    """
    try:
        if product_id:
            validate_uuid_path(product_id, resource="Product")
        if category_id:
            validate_uuid_path(category_id, resource="Category")
        return pricing.effective_floor_view(
            db,
            company_id=acting_company_id(db),
            product_id=product_id or None,
            category_id=category_id or None,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/config/price-floors",
    response_model=PriceFloorRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upsert_price_floor(
    payload: PriceFloorRuleUpsert,
    current_user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Upsert per level: editing "the Basins floor" means exactly that, not a second
    competing Basins rule."""
    try:
        rule = pricing.upsert_floor_rule(
            db,
            company_id=acting_company_id(db),
            payload=payload.model_dump(exclude_unset=True),
            actor_user_id=current_user["id"],
        )
        db.commit()
        db.refresh(rule)
        return _serialize_floors(db, [rule])[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete("/config/price-floors/{rule_id}")
async def delete_price_floor(
    rule_id: str,
    _user: dict = Depends(require_permission(CONFIG_EDIT)),
    db: Session = Depends(get_db),
):
    """Hard delete. Floors already applied are stored ON their lines (AC-E7), so this
    cannot rewrite quoted history."""
    try:
        validate_uuid_path(rule_id, resource="Price floor rule")
        from app.models.projects import PriceFloorRule

        from app.services.error_handler import AppException

        rule = db.query(PriceFloorRule).filter(PriceFloorRule.id == rule_id).first()
        if not rule:
            raise AppException(
                status_code=404,
                message="Price floor rule not found.",
                code="floor_rule_not_found",
            )
        pricing.delete_floor_rule(db, rule)
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


def _serialize_floors(db: Session, rules) -> List[dict]:
    """Naming a rule's target is the service's job, so the effective-floor read and this
    listing cannot drift apart about what a rule is called."""
    return pricing.serialize_floor_rules(db, rules)
