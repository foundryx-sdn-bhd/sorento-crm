"""Countries API routes (S1, `PLAN-local-supplier-oi-routing.md`)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    require_permission,
    require_permission_with_api_key,
    require_session_or_api_key_permission,
)
from app.schemas.common import MAX_PAGE_LIMIT, ListResponse
from app.schemas.country import CountryCreate, CountryResponse, CountrySelectItem, CountryUpdate
from app.services.country_service import CountryService
from app.services.error_handler import handle_internal_error
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()


@router.get("/", response_model=ListResponse[CountryResponse])
async def get_countries(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission_with_api_key("master_data.countries.view")),
    db: Session = Depends(get_db),
):
    try:
        return CountryService(db).list_countries(
            page=page, limit=limit, query=query, sort=sort, sort_dir=dir
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/select", response_model=List[CountrySelectItem])
async def get_countries_select(
    query: Optional[str] = Query(None),
    # Any signed-in user (owner ruling 1 Oct 2026, never-stuck L10); an API key still
    # needs the slug. The list, detail and write routes keep their slugs.
    current_user: dict = Depends(require_session_or_api_key_permission("master_data.countries.view")),
    db: Session = Depends(get_db),
):
    try:
        from app.models.country import Country
        from sqlalchemy import or_

        q = db.query(Country).filter(Country.is_active.is_(True))
        if query:
            q = q.filter(
                or_(Country.code.ilike(f"%{query}%"), Country.name.ilike(f"%{query}%"))
            )
        return q.order_by(Country.name.asc()).limit(300).all()
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{country_id}", response_model=CountryResponse)
async def get_country(
    country_id: str,
    current_user: dict = Depends(require_permission_with_api_key("master_data.countries.view")),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(country_id, resource="Country")
        return CountryService(db).get_country(country_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=CountryResponse, status_code=status.HTTP_201_CREATED)
async def create_country(
    data: CountryCreate,
    current_user: dict = Depends(require_permission("master_data.countries.add")),
    db: Session = Depends(get_db),
):
    try:
        return CountryService(db).create_country(data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{country_id}", response_model=CountryResponse)
async def update_country(
    country_id: str,
    data: CountryUpdate,
    current_user: dict = Depends(require_permission("master_data.countries.edit")),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(country_id, resource="Country")
        return CountryService(db).update_country(country_id, data)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{country_id}", status_code=status.HTTP_200_OK)
async def delete_country(
    country_id: str,
    current_user: dict = Depends(require_permission("master_data.countries.delete")),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(country_id, resource="Country")
        CountryService(db).delete_country(country_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
