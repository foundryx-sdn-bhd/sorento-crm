"""SLA policies API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import (
    require_permission_with_api_key,
    require_session_or_api_key_permission,
)
from app.services.sla_service import SLAPolicyService, SLAPolicyTierService
from app.schemas.sla import (
    SLAPolicyCreate, SLAPolicyUpdate, SLAPolicyResponse,
    SLAPolicyTierCreate, SLAPolicyTierUpdate, SLAPolicyTierResponse
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error

router = APIRouter()

# Writes carry the registry's policy slugs (`_crud("sla_management", "sla_policies", ...)`).
# A tier is part of its policy, so the tier writes take `.edit`. An X-API-Key caller is
# held to the same slug through its act-as user, as the reads already are.
ADD = "sla_management.sla_policies.add"
EDIT = "sla_management.sla_policies.edit"
DELETE = "sla_management.sla_policies.delete"


@router.get("/", response_model=ListResponse[SLAPolicyResponse])
async def get_sla_policies(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    current_user: dict = Depends(require_session_or_api_key_permission("sla_management.sla_policies.view")),
    db: Session = Depends(get_db)
):
    """Get SLA policies with pagination, filtering, and sorting."""
    try:
        service = SLAPolicyService(db)
        result = service.list_policies(
            page=page,
            limit=limit,
            query=query,
            status=status,
            sort_field=sort or "created_at",
            sort_dir=dir or "asc"
        )
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{policy_id}", response_model=SLAPolicyResponse)
async def get_sla_policy(
    policy_id: str,
    current_user: dict = Depends(require_permission_with_api_key("sla_management.sla_policies.view")),
    db: Session = Depends(get_db)
):
    """Get a single SLA policy by ID."""
    try:
        validate_uuid_path(policy_id, resource="SLA Policy")
        service = SLAPolicyService(db)
        policy = service.get_policy(policy_id)
        return policy
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=SLAPolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_sla_policy(
    policy_data: SLAPolicyCreate,
    current_user: dict = Depends(require_permission_with_api_key(ADD)),
    db: Session = Depends(get_db)
):
    """Create a new SLA policy with tiers."""
    try:
        service = SLAPolicyService(db)
        policy = service.create_policy(policy_data)
        return policy
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{policy_id}", response_model=SLAPolicyResponse)
async def update_sla_policy(
    policy_id: str,
    policy_data: SLAPolicyUpdate,
    current_user: dict = Depends(require_permission_with_api_key(EDIT)),
    db: Session = Depends(get_db)
):
    """Update an SLA policy."""
    try:
        validate_uuid_path(policy_id, resource="SLA Policy")
        service = SLAPolicyService(db)
        policy = service.update_policy(policy_id, policy_data)
        return policy
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{policy_id}", status_code=status.HTTP_200_OK)
async def delete_sla_policy(
    policy_id: str,
    current_user: dict = Depends(require_permission_with_api_key(DELETE)),
    db: Session = Depends(get_db)
):
    """Delete an SLA policy."""
    try:
        validate_uuid_path(policy_id, resource="SLA Policy")
        service = SLAPolicyService(db)
        return service.delete_policy(policy_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{policy_id}/tiers", response_model=list[SLAPolicyTierResponse])
async def get_sla_policy_tiers(
    policy_id: str,
    current_user: dict = Depends(require_permission_with_api_key("sla_management.sla_policies.view")),
    db: Session = Depends(get_db)
):
    """Get tiers for an SLA policy."""
    try:
        validate_uuid_path(policy_id, resource="SLA Policy")
        service = SLAPolicyTierService(db)
        tiers = service.list_tiers(policy_id)
        return tiers
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{policy_id}/tiers", response_model=SLAPolicyTierResponse, status_code=status.HTTP_201_CREATED)
async def create_sla_policy_tier(
    policy_id: str,
    tier_data: SLAPolicyTierCreate,
    current_user: dict = Depends(require_permission_with_api_key(EDIT)),
    db: Session = Depends(get_db)
):
    """Create a tier for an SLA policy."""
    try:
        validate_uuid_path(policy_id, resource="SLA Policy")
        service = SLAPolicyTierService(db)
        # Ensure policy_id matches
        tier_data.policy_id = policy_id
        tier = service.create_tier(tier_data)
        return tier
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{policy_id}/tiers/{tier_id}", response_model=SLAPolicyTierResponse)
async def update_sla_policy_tier(
    policy_id: str,
    tier_id: str,
    tier_data: SLAPolicyTierUpdate,
    current_user: dict = Depends(require_permission_with_api_key(EDIT)),
    db: Session = Depends(get_db)
):
    """Update an SLA policy tier."""
    try:
        validate_uuid_path(policy_id, resource="SLA Policy")
        service = SLAPolicyTierService(db)
        tier = service.update_tier(tier_id, tier_data)
        return tier
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{policy_id}/tiers/{tier_id}", status_code=status.HTTP_200_OK)
async def delete_sla_policy_tier(
    policy_id: str,
    tier_id: str,
    current_user: dict = Depends(require_permission_with_api_key(EDIT)),
    db: Session = Depends(get_db)
):
    """Delete an SLA policy tier."""
    try:
        validate_uuid_path(policy_id, resource="SLA Policy")
        service = SLAPolicyTierService(db)
        result = service.delete_tier(tier_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
