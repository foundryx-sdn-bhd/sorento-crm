"""OI-LINE-SPLIT: split SO lines into deliveries, the whole sales order at once.

    GET /project-sales/sales-orders/{pso_id}/line-splits   (view, API key allowed)
    PUT /project-sales/sales-orders/{pso_id}/line-splits   (edit + the order's own check)

Plan: `documentation/plans/scm/PLAN-oi-line-split-16oct.md` ("Build design (final)", API);
UAC `oi-line-split-16oct-acceptance-criteria.md` B and C. PUT replaces the splits of every
line it names in one transaction and answers the GET payload; a refusal (422, naming the
line) saves nothing. Company scope comes from the mixin: another company's order is 404.

Mounted with the fulfilment planning routers, ahead of the plain `/sales-orders/{pso_id}`
one, for the same reason `fulfilment_planning` is.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.projects.fulfilment_planning import _assert_can_act_on
from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.project_line_split import LineSplitsBody, LineSplitsOut
from app.services.error_handler import handle_internal_error
from app.services.project_line_split_service import LineSplitService
from app.services.project_supply_service import ProjectSupplyService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"


@router.get("/sales-orders/{pso_id}/line-splits", response_model=LineSplitsOut)
def get_line_splits(
    pso_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Every open line of the order with its base, its AutoCount now and its splits."""
    try:
        validate_uuid_path(pso_id, resource="Sales order")
        order = ProjectSupplyService(db).get_order(pso_id)
        return LineSplitService(db).read(order)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error()


@router.put("/sales-orders/{pso_id}/line-splits", response_model=LineSplitsOut)
def put_line_splits(
    pso_id: str,
    payload: LineSplitsBody,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Save the splits of the named lines, all or nothing. The server owns each line's base."""
    try:
        validate_uuid_path(pso_id, resource="Sales order")
        order = ProjectSupplyService(db).get_order(pso_id)
        _assert_can_act_on(db, order, current_user)
        service = LineSplitService(db)
        service.save(order, payload.lines, actor_user_id=current_user["id"])
        db.commit()
        return service.read(order)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error()
