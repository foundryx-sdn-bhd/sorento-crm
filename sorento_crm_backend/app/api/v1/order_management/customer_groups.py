"""Customer groups API routes: one company's customer, made of several ledgers.

Reads reuse `order_management.customers.view`, writes `.edit`; the delete is the parked
`customer_group.delete` action (`app/services/record_actions.py`), so there is no DELETE
route and no new permission slug. `/select` is declared before `/{group_id}` so the literal
path is not read as an id.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.customer_group import (
    CustomerGroupCustomersAssign,
    CustomerGroupResponse,
    CustomerGroupSelect,
    CustomerGroupWrite,
)
from app.schemas.order import CustomerResponse
from app.services.customer_group_service import CustomerGroupService
from app.services.error_handler import handle_internal_error
from app.services.order_service import CustomerService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()
_RESOURCE = "Customer group"
_VIEW = "order_management.customers.view"
_EDIT = "order_management.customers.edit"


@router.get("/", response_model=ListResponse[CustomerGroupResponse])
async def list_customer_groups(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    dir: str = Query("asc"),
    agent_mixed: bool = Query(False),
    current_user: dict = Depends(require_permission(_VIEW)),
    db: Session = Depends(get_db),
):
    try:
        return CustomerGroupService(db).list_groups(
            page=page, limit=limit, query=query, sort=sort, dir=dir, agent_mixed=agent_mixed
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/select", response_model=dict)
async def select_customer_groups(
    query: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(require_permission(_VIEW)),
    db: Session = Depends(get_db),
):
    """Groups for the customer form's select, searched on the server."""
    try:
        rows = CustomerGroupService(db).select(query=query, limit=limit)
        return {"data": [CustomerGroupSelect(**r).model_dump() for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=CustomerGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_customer_group(
    payload: CustomerGroupWrite,
    current_user: dict = Depends(require_permission(_EDIT)),
    db: Session = Depends(get_db),
):
    try:
        return CustomerGroupService(db).create_group(payload.name)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@router.get("/{group_id}", response_model=CustomerGroupResponse)
async def get_customer_group(
    group_id: str,
    current_user: dict = Depends(require_permission(_VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(group_id, resource=_RESOURCE)
        return CustomerGroupService(db).get_shaped(group_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.patch("/{group_id}", response_model=CustomerGroupResponse)
async def rename_customer_group(
    group_id: str,
    payload: CustomerGroupWrite,
    current_user: dict = Depends(require_permission(_EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(group_id, resource=_RESOURCE)
        return CustomerGroupService(db).rename_group(group_id, payload.name)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))


@router.get("/{group_id}/customers", response_model=ListResponse[CustomerResponse])
async def list_customer_group_customers(
    group_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    sort: str = Query("customer_code"),
    dir: str = Query("asc"),
    current_user: dict = Depends(require_permission(_VIEW)),
    db: Session = Depends(get_db),
):
    """The ledgers in this group, under the caller's company scope."""
    try:
        validate_uuid_path(group_id, resource=_RESOURCE)
        CustomerGroupService(db).get_group(group_id)
        return CustomerService(db).list_customers(
            page=page, limit=limit, query=query, sort_field=sort, sort_dir=dir,
            customer_group_id=group_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{group_id}/customers")
async def assign_customer_group_customers(
    group_id: str,
    payload: CustomerGroupCustomersAssign,
    current_user: dict = Depends(require_permission(_EDIT)),
    db: Session = Depends(get_db),
):
    """Move ledgers into this group, from other groups too. All or nothing: an unknown or
    hidden id is 404, a customer of another company 422, and nothing is written."""
    try:
        validate_uuid_path(group_id, resource=_RESOURCE)
        customers = CustomerGroupService(db).assign_customers(group_id, payload.customer_ids)
        return {"data": [CustomerResponse.model_validate(c).model_dump(mode="json") for c in customers]}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise handle_internal_error(str(e))
