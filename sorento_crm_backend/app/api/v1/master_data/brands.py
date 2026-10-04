"""Brands API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import Literal, Optional, List
from app.database import get_db
from app.dependencies import (
    require_permission,
    require_permission_with_api_key,
    require_session_or_api_key_permission,
)
from app.services.company_scope_resolver import (
    company_name_map,
    grants_requested,
    tag_company,
    widen_if_requested,
)
from app.services.product_service import BrandService
from app.schemas.product import BrandCreate, BrandResponse, BrandSelectItem, BrandUpdate
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error
from app.services.uuid_list_param import parse_uuid_list

router = APIRouter()


def _assert_company_readable(db: Session, current_user: dict, company_id: str) -> None:
    """Refuse a company the caller cannot reach.

    Mirrors ``system/companies.get_company``: superadmin/admin reach every company,
    everyone else only the ones granted to them. The API-key principal has no
    grants and is deliberately unscoped, so it passes. A company the caller cannot
    reach reads as absent rather than forbidden, which is what that endpoint does
    and is one less thing to learn from a probe.
    """
    from app.models.company import Company, UserCompany
    from app.services.error_handler import handle_not_found
    from app.services.identifier_resolver import is_uuid
    from app.services.user_service import UserPermissionService

    # Compared against a uuid column, so a non-UUID would abort the transaction
    # with a cast error rather than answer the question asked.
    if not is_uuid(company_id):
        raise handle_not_found("Company", company_id)

    # ``integration_api_key`` is what an X-API-Key call actually carries
    # (integration_auth.resolve_integration_principal); the bare ``api_key`` spelling
    # is accepted too because several older routes assume it and a hand-built
    # principal in a test may still use it.
    if (current_user or {}).get("auth_method") in {"api_key", "integration_api_key"}:
        return

    user_id = str((current_user or {}).get("id") or "")
    slugs = UserPermissionService(db).get_user_role_slugs(user_id)
    if slugs & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}:
        exists = db.query(Company.id).filter(Company.id == company_id).first()
        if exists is None:
            raise handle_not_found("Company", company_id)
        return

    granted = (
        db.query(UserCompany.id)
        .filter(UserCompany.user_id == user_id, UserCompany.company_id == company_id)
        .first()
    )
    if granted is None:
        raise handle_not_found("Company", company_id)


def _active_brands(db: Session, query: Optional[str]):
    """Active brands for a dropdown, under whatever company scope is in force."""
    from sqlalchemy import or_
    from app.models.product import Brand

    q = db.query(Brand).filter(Brand.is_active == True)

    if query:
        q = q.filter(
            or_(
                Brand.brand_code.ilike(f"%{query}%"),
                Brand.brand_name.ilike(f"%{query}%"),
            )
        )

    # Alphabetical, so every consumer of the dropdown gets a stable, scannable
    # list instead of physical row order.
    return q.order_by(Brand.brand_name).limit(100).all()


@router.get("/", response_model=ListResponse[BrandResponse])
async def get_brands(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    brand_ids: Optional[List[str]] = Query(
        None,
        description="Filter by canonical brand UUIDs (repeated / csv / JSON array).",
    ),
    product_ids: Optional[List[str]] = Query(
        None,
        description="Filter to the brands of these product UUIDs (repeated / csv / JSON array).",
    ),
    company_scope: Optional[Literal["grants"]] = Query(
        None,
        description="`grants` reads every company the caller is granted (staff sessions only) and tags each row with its company.",
    ),
    request: Request = None,
    current_user: dict = Depends(require_permission_with_api_key("master_data.brands.view")),
    db: Session = Depends(get_db)
):
    """Get brands with pagination and search."""
    try:
        service = BrandService(db)
        with widen_if_requested(db, request, current_user, company_scope):
            result = service.list_brands(
                page=page,
                limit=limit,
                query=query,
                brand_ids=parse_uuid_list(brand_ids, param_name="brand_ids"),
                product_ids=parse_uuid_list(product_ids, param_name="product_ids"),
            )
            if grants_requested(request, current_user, company_scope):
                tag_company(result["data"], company_name_map(db))
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/select", response_model=List[BrandSelectItem])
async def get_brands_select(
    query: Optional[str] = Query(None),
    company_id: Optional[str] = Query(
        None,
        description=(
            "Brands of THIS company, whichever company the caller is switched "
            "into. Omit for the caller's active-company scope (the default)."
        ),
    ),
    # Any signed-in user (owner ruling 1 Oct 2026, never-stuck L10); an API key still
    # needs the slug. The list, detail and write routes keep their slugs.
    current_user: dict = Depends(require_session_or_api_key_permission("master_data.brands.view")),
    db: Session = Depends(get_db)
):
    """Get brands for select dropdowns.

    ``company_id`` exists for the product-discontinued scope editor: an admin sets
    a user's brand scopes for companies they are not currently switched into, and
    the session scope would return that company's brands as an empty list. The
    named company must be one the caller can reach (their grant, or any company
    for a superadmin/admin or the API-key principal), so this widens WHICH company
    is being read, never WHO may read it.
    """
    try:
        from app.models.base import company_scope

        if not company_id:
            return _active_brands(db, query)

        _assert_company_readable(db, current_user, company_id)
        with company_scope(db, frozenset({company_id})):
            return _active_brands(db, query)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{brand_id}", response_model=BrandResponse)
async def get_brand(
    brand_id: str,
    current_user: dict = Depends(require_permission_with_api_key("master_data.brands.view")),
    db: Session = Depends(get_db)
):
    """Get a single brand by ID."""
    try:
        service = BrandService(db)
        brand = service.get_brand(brand_id)
        return brand
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=BrandResponse, status_code=status.HTTP_201_CREATED)
async def create_brand(
    brand_data: BrandCreate,
    current_user: dict = Depends(require_permission("master_data.brands.add")),
    db: Session = Depends(get_db)
):
    """Create a new brand."""
    try:
        service = BrandService(db)
        brand = service.create_brand(brand_data)
        return brand
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{brand_id}", response_model=BrandResponse)
async def update_brand(
    brand_id: str,
    brand_data: BrandUpdate,
    current_user: dict = Depends(require_permission("master_data.brands.edit")),
    db: Session = Depends(get_db)
):
    """Update a brand."""
    try:
        service = BrandService(db)
        brand = service.update_brand(brand_id, brand_data)
        return brand
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{brand_id}", status_code=status.HTTP_200_OK)
async def delete_brand(
    brand_id: str,
    current_user: dict = Depends(require_permission("master_data.brands.delete")),
    db: Session = Depends(get_db)
):
    """Delete a brand."""
    try:
        service = BrandService(db)
        return service.delete_brand(brand_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
