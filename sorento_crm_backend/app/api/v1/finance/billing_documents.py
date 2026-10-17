"""Billing documents API, read only (UAC S2-4, S2-8, S3-1, S5-8).

Plain `def` handlers: nothing here awaits, and an `async def` that never awaits blocks the
worker's event loop (LESSONS-LEARNT).
"""
from __future__ import annotations

import logging
from datetime import date as DateType
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.schemas.common import MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error
from app.services.finance import billing_document_query_service as svc
from app.services.uuid_path_param import UUID_PATTERN

router = APIRouter()
customers_router = APIRouter()
logger = logging.getLogger(__name__)

VIEW = svc.BILLING_VIEW_SLUG


def _reraise(db: Session, exc: Exception):
    """A deliberate AppException is re-raised as is; anything else is logged with its real
    text and answered as a generic internal error, never echoed."""
    db.rollback()
    if hasattr(exc, "status_code"):
        raise exc
    logger.error("Unexpected error in finance billing documents route: %s", exc, exc_info=True)
    raise handle_internal_error()


@router.get("")
def list_billing_documents(
    query: Optional[str] = Query(None),
    # Repeated (`document_type=a&document_type=b`) or comma separated (`a,b`).
    document_type: Optional[list[str]] = Query(None),
    status: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None, pattern=UUID_PATTERN),
    agent_code: Optional[str] = Query(None),
    date_from: Optional[DateType] = Query(None),
    date_to: Optional[DateType] = Query(None),
    open_only: bool = Query(False),
    sales_order_id: Optional[str] = Query(None, pattern=UUID_PATTERN),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    sort: str = Query(svc.DEFAULT_SORT),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        rows, total = svc.list_documents(
            db,
            query=query,
            document_types=document_type,
            status=status,
            customer_id=customer_id,
            agent_code=agent_code,
            date_from=date_from,
            date_to=date_to,
            open_only=open_only,
            sales_order_id=sales_order_id,
            page=page,
            limit=limit,
            sort=sort,
            dir=dir,
        )
        return {
            "data": svc.serialize_list(db, rows),
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
    except Exception as exc:
        _reraise(db, exc)


# Before `/{document_id}`, which would otherwise capture it.
@router.get("/agent-options")
def agent_options(
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        data = svc.agent_options(db)
        return {
            "data": data,
            "pagination": {"total": len(data), "page": 1, "limit": max(len(data), 1)},
            "empty": not data,
        }
    except Exception as exc:
        _reraise(db, exc)


@router.get("/{document_id}")
def get_billing_document(
    document_id: str,
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        return svc.document_detail(db, svc.get_document_or_404(db, document_id))
    except Exception as exc:
        _reraise(db, exc)


@customers_router.get("/{customer_id}/outstanding")
def customer_outstanding(
    customer_id: str,
    _user: dict = Depends(require_permission(svc.OUTSTANDING_VIEW_SLUG)),
    db: Session = Depends(get_db),
):
    try:
        return svc.customer_outstanding(db, customer_id)
    except Exception as exc:
        _reraise(db, exc)
