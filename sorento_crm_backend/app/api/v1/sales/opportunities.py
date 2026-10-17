"""Sales Opportunities API, CRM side (plan 3.4, 3.7; section 16, slice S2).

Plain `def` handlers: nothing here awaits, and an `async def` that never awaits blocks
the worker's event loop (LESSONS-LEARNT).
"""
from __future__ import annotations

import logging
from datetime import date as DateType
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.sales import (
    SalesOpportunityCreate,
    SalesOpportunityCustomerOptionsResponse,
    SalesOpportunityMeta,
    SalesOpportunityResponse,
    SalesOpportunitySalesOrderOption,
    SalesOpportunityUpdate,
    SalesTeamAgentOption,
)
from app.services.error_handler import handle_internal_error
from app.services.sales import opportunity_service as svc
from app.services.sales import team_service
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()
logger = logging.getLogger(__name__)

VIEW = "sales.opportunities.view"
ADD = "sales.opportunities.add"
EDIT = "sales.opportunities.edit"
DELETE = "sales.opportunities.delete"


def _reraise(db: Session, exc: Exception):
    """An AppException/HTTPException (a deliberate, already-shaped response) is
    re-raised as-is. Anything else is unexpected - log it with the real text server
    side, but never put that text in the response body (Phase 3 fix S1): a bare
    `str(exc)` on a DB error is a SQL/psycopg string, which is exactly the kind of
    detail an internal-error response must not leak to the caller.
    """
    db.rollback()
    if hasattr(exc, "status_code"):
        raise exc
    logger.error("Unexpected error in sales opportunities route: %s", exc, exc_info=True)
    raise handle_internal_error()


def _stamp_actor(db: Session, user: dict) -> None:
    """Attribute an audited write to the acting user (S2-9).

    `require_permission` reads `current_user` off `Depends(get_current_user)`, whose real
    body stamps the full session actor (identity S0); stamped here only when that has not
    happened for this user, so the audit row is still correct when a caller (a test)
    overrides that dependency directly with a bare dict, and the richer actor is kept.
    """
    from app.audit_context import AuditActor, get_actor, stamp_actor

    user_id = user.get("id")
    if not user_id:
        return
    actor = get_actor(db)
    if actor is not None and str(user_id) in (actor.user_id, actor.real_user_id):
        return
    stamp_actor(AuditActor(actor_type="user", user_id=str(user_id), real_user_id=str(user_id)), db=db)


def _list(data: list) -> dict:
    return {
        "data": data,
        "pagination": {"total": len(data), "page": 1, "limit": max(len(data), 1)},
        "empty": not data,
    }


@router.get("", response_model=ListResponse[SalesOpportunityResponse])
def list_opportunities(
    query: Optional[str] = Query(None),
    status_id: Optional[str] = Query(None),
    sales_agent_id: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None),
    close_from: Optional[DateType] = Query(None),
    close_to: Optional[DateType] = Query(None),
    outcome: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    sort: str = Query("created_at"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    project_id: Optional[UUID] = Query(None),
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        rows, total = svc.list_opportunities(
            db,
            query=query,
            status_id=status_id,
            sales_agent_id=sales_agent_id,
            customer_id=customer_id,
            close_from=close_from,
            close_to=close_to,
            outcome=outcome,
            page=page,
            limit=limit,
            sort=sort,
            dir=dir,
            project_id=str(project_id) if project_id else None,
        )
        return {
            "data": svc.serialize_list(db, rows),
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
    except Exception as exc:
        _reraise(db, exc)


# Declared before `/{opportunity_id}`, which would otherwise capture them.
@router.get("/meta", response_model=SalesOpportunityMeta)
def get_meta(_user: dict = Depends(require_permission(VIEW)), db: Session = Depends(get_db)):
    try:
        return svc.meta(db)
    except Exception as exc:
        _reraise(db, exc)


@router.get("/customer-options", response_model=SalesOpportunityCustomerOptionsResponse)
def get_customer_options(
    q: Optional[str] = Query(None),
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        return svc.customer_options(db, q=q)
    except Exception as exc:
        _reraise(db, exc)


@router.get("/agent-options", response_model=ListResponse[SalesTeamAgentOption])
def get_agent_options(
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        company_id = team_service.acting_company_id(db)
        return _list(svc.agent_options(db, company_id=company_id))
    except Exception as exc:
        _reraise(db, exc)


@router.post("", response_model=SalesOpportunityResponse, status_code=status.HTTP_201_CREATED)
def create_opportunity(
    payload: SalesOpportunityCreate,
    current_user: dict = Depends(require_permission(ADD)),
    db: Session = Depends(get_db),
):
    try:
        _stamp_actor(db, current_user)
        company_id = team_service.acting_company_id(db)
        body = payload.model_dump(exclude_unset=True)
        opportunity = svc.create_opportunity(
            db,
            company_id=company_id,
            payload=body,
            sales_agent_id=body.get("sales_agent_id"),
            source="crm",
            created_by_user_id=current_user["id"],
            stamp_agent_from_customer=True,
        )
        db.commit()
        db.refresh(opportunity)
        return svc.serialize(db, opportunity)
    except Exception as exc:
        _reraise(db, exc)


@router.get("/{opportunity_id}", response_model=SalesOpportunityResponse)
def get_opportunity(
    opportunity_id: str,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(opportunity_id, resource="Sales Opportunity")
        opportunity = svc.get_opportunity_or_404(db, opportunity_id)
        return svc.serialize(db, opportunity)
    except Exception as exc:
        _reraise(db, exc)


@router.patch("/{opportunity_id}", response_model=SalesOpportunityResponse)
def update_opportunity(
    opportunity_id: str,
    payload: SalesOpportunityUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        _stamp_actor(db, current_user)
        validate_uuid_path(opportunity_id, resource="Sales Opportunity")
        opportunity = svc.get_opportunity_or_404(db, opportunity_id)
        svc.update_opportunity(db, opportunity, payload.model_dump(exclude_unset=True))
        db.commit()
        db.refresh(opportunity)
        return svc.serialize(db, opportunity)
    except Exception as exc:
        _reraise(db, exc)


@router.delete("/{opportunity_id}")
def delete_opportunity(
    opportunity_id: str,
    current_user: dict = Depends(require_permission(DELETE)),
    db: Session = Depends(get_db),
):
    """Immediate hard delete. The screen parks it as a deferred action instead (D7)."""
    try:
        _stamp_actor(db, current_user)
        validate_uuid_path(opportunity_id, resource="Sales Opportunity")
        opportunity = svc.get_opportunity_or_404(db, opportunity_id)
        name = opportunity.title
        svc.delete_opportunity(db, opportunity)
        db.commit()
        return {"message": f"{name} deleted"}
    except Exception as exc:
        _reraise(db, exc)


@router.get(
    "/{opportunity_id}/sales-order-options",
    response_model=ListResponse[SalesOpportunitySalesOrderOption],
)
def get_sales_order_options(
    opportunity_id: str,
    q: Optional[str] = Query(None),
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(opportunity_id, resource="Sales Opportunity")
        opportunity = svc.get_opportunity_or_404(db, opportunity_id)
        return _list(svc.sales_order_options(db, opportunity, q=q))
    except Exception as exc:
        _reraise(db, exc)
