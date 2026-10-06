"""Campaign types API routes."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import get_current_user, require_session_or_api_key_permission
from app.services.marketing_service import CampaignTypeService
from app.schemas.marketing import CampaignTypeCreate, CampaignTypeUpdate, CampaignTypeResponse
from app.schemas.common import ListResponse
from app.services.error_handler import handle_internal_error

router = APIRouter()


@router.get("/", response_model=ListResponse[CampaignTypeResponse])
async def get_campaign_types(
    current_user: dict = Depends(require_session_or_api_key_permission("marketing.campaigns.view")),
    db: Session = Depends(get_db)
):
    """Get all campaign types."""
    try:
        service = CampaignTypeService(db)
        types = service.list_campaign_types()
        return {
            "data": types,
            "pagination": {"total": len(types), "page": 1, "limit": len(types)},
            "empty": len(types) == 0
        }
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{type_id}", response_model=CampaignTypeResponse)
async def get_campaign_type(
    type_id: str,
    current_user: dict = Depends(require_session_or_api_key_permission("marketing.campaigns.view")),
    db: Session = Depends(get_db)
):
    """Get a single campaign type by ID."""
    try:
        validate_uuid_path(type_id, resource="Campaign Type")
        service = CampaignTypeService(db)
        campaign_type = service.get_campaign_type(type_id)
        return campaign_type
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=CampaignTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign_type(
    type_data: CampaignTypeCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new campaign type."""
    try:
        service = CampaignTypeService(db)
        campaign_type = service.create_campaign_type(type_data)
        return campaign_type
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{type_id}", response_model=CampaignTypeResponse)
async def update_campaign_type(
    type_id: str,
    type_data: CampaignTypeUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a campaign type."""
    try:
        validate_uuid_path(type_id, resource="Campaign Type")
        service = CampaignTypeService(db)
        campaign_type = service.update_campaign_type(type_id, type_data)
        return campaign_type
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{type_id}", status_code=status.HTTP_200_OK)
async def delete_campaign_type(
    type_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a campaign type."""
    try:
        validate_uuid_path(type_id, resource="Campaign Type")
        service = CampaignTypeService(db)
        return service.delete_campaign_type(type_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
