"""Units of measure API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List
from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import (
    require_permission,
    require_permission_with_api_key,
    require_session_or_api_key_permission,
)
from app.services.product_service import UnitOfMeasureService
from app.schemas.product import (
    UnitOfMeasureCreate,
    UnitOfMeasureResponse,
    UnitOfMeasureSelectItem,
    UnitOfMeasureUpdate,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error
from app.services.uuid_list_param import parse_uuid_list

router = APIRouter()


@router.get("/", response_model=ListResponse[UnitOfMeasureResponse])
async def get_units_of_measure(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    uom_ids: Optional[List[str]] = Query(
        None,
        description="Filter by canonical unit-of-measure UUIDs (repeated / csv / JSON array).",
    ),
    product_ids: Optional[List[str]] = Query(
        None,
        description="Filter to the base UOMs of these product UUIDs (repeated / csv / JSON array).",
    ),
    current_user: dict = Depends(require_permission_with_api_key("master_data.units_of_measure.view")),
    db: Session = Depends(get_db)
):
    """Get units of measure with pagination and search."""
    try:
        service = UnitOfMeasureService(db)
        result = service.list_uoms(
            page=page,
            limit=limit,
            query=query,
            uom_ids=parse_uuid_list(uom_ids, param_name="uom_ids"),
            product_ids=parse_uuid_list(product_ids, param_name="product_ids"),
        )
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/select", response_model=List[UnitOfMeasureSelectItem])
async def get_uoms_select(
    query: Optional[str] = Query(None),
    # Any signed-in user (owner ruling 1 Oct 2026, never-stuck L10); an API key still
    # needs the slug. The list, detail and write routes keep their slugs.
    current_user: dict = Depends(require_session_or_api_key_permission("master_data.units_of_measure.view")),
    db: Session = Depends(get_db)
):
    """Get units of measure for select dropdowns."""
    try:
        from sqlalchemy import or_
        from app.models.product import UnitOfMeasure
        q = db.query(UnitOfMeasure)
        
        if query:
            q = q.filter(
                or_(
                    UnitOfMeasure.uom_code.ilike(f"%{query}%"),
                    UnitOfMeasure.uom_name.ilike(f"%{query}%")
                )
            )
        
        uoms = q.limit(100).all()
        return uoms
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{uom_id}", response_model=UnitOfMeasureResponse)
async def get_uom(
    uom_id: str,
    current_user: dict = Depends(require_permission_with_api_key("master_data.units_of_measure.view")),
    db: Session = Depends(get_db)
):
    """Get a single UOM by ID."""
    try:
        validate_uuid_path(uom_id, resource="Unit of Measure")
        service = UnitOfMeasureService(db)
        uom = service.get_uom(uom_id)
        return uom
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=UnitOfMeasureResponse, status_code=status.HTTP_201_CREATED)
async def create_uom(
    uom_data: UnitOfMeasureCreate,
    current_user: dict = Depends(require_permission("master_data.units_of_measure.add")),
    db: Session = Depends(get_db)
):
    """Create a new UOM."""
    try:
        service = UnitOfMeasureService(db)
        uom = service.create_uom(uom_data)
        return uom
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{uom_id}", response_model=UnitOfMeasureResponse)
async def update_uom(
    uom_id: str,
    uom_data: UnitOfMeasureUpdate,
    current_user: dict = Depends(require_permission("master_data.units_of_measure.edit")),
    db: Session = Depends(get_db)
):
    """Update a UOM."""
    try:
        validate_uuid_path(uom_id, resource="Unit of Measure")
        service = UnitOfMeasureService(db)
        uom = service.update_uom(uom_id, uom_data)
        return uom
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{uom_id}", status_code=status.HTTP_200_OK)
async def delete_uom(
    uom_id: str,
    current_user: dict = Depends(require_permission("master_data.units_of_measure.delete")),
    db: Session = Depends(get_db)
):
    """Delete a UOM."""
    try:
        validate_uuid_path(uom_id, resource="Unit of Measure")
        service = UnitOfMeasureService(db)
        return service.delete_uom(uom_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
