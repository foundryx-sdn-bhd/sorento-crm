"""Promotion types API routes.

The vocabulary that decides what happens to a promotion after its end date, and
how an uploaded file is classified. Admin-maintained, so a sixth kind of
promotion is a row here rather than a deploy.

Reads stay open to any authenticated principal (the promotions form and the MCP
surface both need the vocabulary), but the writes are permission-gated: this
config decides what the WhatsApp bot tells a customer about an ended promotion,
so flipping `special.show_expired` back on is an administrative act, not
something every logged-in account may do. Slugs are registered and granted in
migration 362.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission, require_session_or_api_key_permission
from app.schemas.common import ListResponse
from app.schemas.marketing import (
    PromotionTypeCreate,
    PromotionTypeResponse,
    PromotionTypeUpdate,
)
from app.services.error_handler import handle_internal_error
from app.services.marketing_service import PromotionTypeService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()


@router.get("/", response_model=ListResponse[PromotionTypeResponse])
async def get_promotion_types(
    current_user: dict = Depends(require_session_or_api_key_permission("marketing.promotion_types.view")),
    db: Session = Depends(get_db),
):
    """Every promotion type, in display order."""
    try:
        types = PromotionTypeService(db).list_promotion_types()
        return {
            "data": types,
            "pagination": {"total": len(types), "page": 1, "limit": max(len(types), 1)},
            "empty": len(types) == 0,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{type_id}", response_model=PromotionTypeResponse)
async def get_promotion_type(
    type_id: str,
    current_user: dict = Depends(require_session_or_api_key_permission("marketing.promotion_types.view")),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(type_id, resource="Promotion Type")
        return PromotionTypeService(db).get_promotion_type(type_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=PromotionTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_promotion_type(
    type_data: PromotionTypeCreate,
    current_user: dict = Depends(require_permission("marketing.promotion_types.add")),
    db: Session = Depends(get_db),
):
    try:
        return PromotionTypeService(db).create_promotion_type(type_data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{type_id}", response_model=PromotionTypeResponse)
async def update_promotion_type(
    type_id: str,
    type_data: PromotionTypeUpdate,
    current_user: dict = Depends(require_permission("marketing.promotion_types.edit")),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(type_id, resource="Promotion Type")
        return PromotionTypeService(db).update_promotion_type(type_id, type_data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{type_id}", status_code=status.HTTP_200_OK)
async def delete_promotion_type(
    type_id: str,
    current_user: dict = Depends(require_permission("marketing.promotion_types.delete")),
    db: Session = Depends(get_db),
):
    """Hard delete. Promotions of this type become unclassified and are served
    under the default type's policy."""
    try:
        validate_uuid_path(type_id, resource="Promotion Type")
        return PromotionTypeService(db).delete_promotion_type(type_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
