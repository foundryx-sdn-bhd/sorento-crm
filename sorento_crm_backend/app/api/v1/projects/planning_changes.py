"""Planning changes (`documentation/plans/scm/PLAN-so-book-diff-replanning.md` section 3).

Four routes, mounted on the same `/project-sales` root as fulfilment planning. Reads take
`projects.projects.view`; the two writes (the per-row decision and Apply) take
`projects.projects.edit` - the same dependency `POST /sales-orders/{pso_id}/confirm` uses in
`fulfilment_planning.py`, reused exactly rather than introducing a separate "manage"
permission, because Apply writes through that same supply-confirmation path.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.common import MAX_PAGE_LIMIT
from app.schemas.planning_change import (
    ApplyPlanningChangesResult,
    PlanningChangeBatch,
    PlanningChangeListEnvelope,
    PlanningChangeRow,
    UpdatePlanningChangeRowBody,
)
from app.services import planning_change_service
from app.services.error_handler import handle_internal_error
from app.services.uuid_path_param import validate_uuid_path

logger = logging.getLogger(__name__)

router = APIRouter()

VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"


@router.get("/planning-changes", response_model=PlanningChangeListEnvelope)
def list_planning_changes(
    query: Optional[str] = Query(None, description="Matches the upload's file name."),
    state: Optional[Literal["pending", "applied"]] = Query(None),
    sort: Optional[Literal["created_at"]] = Query(None),
    direction: Optional[Literal["asc", "desc"]] = Query("desc", alias="dir"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=MAX_PAGE_LIMIT),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Batches newest first (AC-R10)."""
    try:
        return planning_change_service.list_batches(
            db, page=page, limit=limit, query=query, state=state, sort=sort,
            direction=direction,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/planning-changes/{batch_id}", response_model=PlanningChangeBatch)
def get_planning_change_batch(
    batch_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """One batch, as it read at step 1 plus what Apply did (AC-R02)."""
    try:
        validate_uuid_path(batch_id, resource="Planning change batch")
        return planning_change_service.get_batch(db, batch_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put("/planning-changes/{batch_id}/rows/{row_id}", response_model=PlanningChangeRow)
def update_planning_change_row(
    batch_id: str,
    row_id: str,
    body: UpdatePlanningChangeRowBody,
    _user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """The one decision per row (AC-R04). `confirm`/`amend` also freeze the composition
    Apply will post for the line - everything else writes nothing but the decision."""
    try:
        validate_uuid_path(batch_id, resource="Planning change batch")
        validate_uuid_path(row_id, resource="Planning change row")
        composition = body.composition.model_dump(mode="json") if body.composition else None
        row = planning_change_service.set_row_decision(
            db, batch_id, row_id, body.decision, composition
        )
        db.commit()
        return row
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/planning-changes/{batch_id}/apply", response_model=ApplyPlanningChangesResult)
def apply_planning_changes(
    batch_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Apply the accepted rows, atomically per order (AC-R05)."""
    try:
        validate_uuid_path(batch_id, resource="Planning change batch")
        result = planning_change_service.apply(db, batch_id, current_user["id"])
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
