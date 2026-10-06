"""Attachment types API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import (
    get_current_user,
    require_permission_with_api_key,
    require_session_or_api_key_permission,
)
from app.services.resources_service import AttachmentTypeService
from app.schemas.resources import AttachmentTypeCreate, AttachmentTypeUpdate, AttachmentTypeResponse
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error

router = APIRouter()


@router.get("/", response_model=ListResponse[AttachmentTypeResponse])
async def get_attachment_types(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query("desc", regex="^(asc|desc)$"),
    current_user: dict = Depends(require_session_or_api_key_permission("resource.attachment_types.view")),
    db: Session = Depends(get_db)
):
    """Get attachment types with pagination and filtering."""
    try:
        service = AttachmentTypeService(db)
        sort_dir = dir or "desc"
        result = service.list_types(
            page=page,
            limit=limit,
            query=query,
            sort=sort,
            dir=sort_dir
        )
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{type_id}", response_model=AttachmentTypeResponse)
async def get_attachment_type(
    type_id: str,
    current_user: dict = Depends(require_permission_with_api_key("resource.attachment_types.view")),
    db: Session = Depends(get_db)
):
    """Get a single attachment type by ID."""
    try:
        validate_uuid_path(type_id, resource="Attachment Type")
        service = AttachmentTypeService(db)
        attachment_type = service.get_type(type_id)
        return attachment_type
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=AttachmentTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_attachment_type(
    type_data: AttachmentTypeCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new attachment type."""
    try:
        service = AttachmentTypeService(db)
        attachment_type = service.create_type(type_data)
        return attachment_type
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{type_id}", response_model=AttachmentTypeResponse)
async def update_attachment_type(
    type_id: str,
    type_data: AttachmentTypeUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an attachment type."""
    try:
        validate_uuid_path(type_id, resource="Attachment Type")
        service = AttachmentTypeService(db)
        attachment_type = service.update_type(type_id, type_data)
        return attachment_type
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{type_id}", status_code=status.HTTP_200_OK)
async def delete_attachment_type(
    type_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an attachment type."""
    try:
        validate_uuid_path(type_id, resource="Attachment Type")
        service = AttachmentTypeService(db)
        service.delete_type(type_id)
        return {"message": "Attachment type deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
