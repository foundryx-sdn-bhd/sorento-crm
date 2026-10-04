"""Product categories API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List
from app.database import get_db
from app.dependencies import (
    require_any_permission_with_api_key,
    require_permission,
    require_permission_with_api_key,
    require_session_or_api_key_permission,
)
from app.services.product_service import ProductCategoryService
from app.schemas.product import (
    ProductCategoryCreate,
    ProductCategoryResponse,
    ProductCategorySelectItem,
    ProductCategoryUpdate,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.services.error_handler import handle_internal_error
from app.services.uuid_list_param import parse_uuid_list
from app.services.stock_ask_limits import guard_chatbot_limits_edit, NULL_LIMITS

router = APIRouter()


@router.get("/tree")
async def get_categories_tree(
    current_user: dict = Depends(require_permission_with_api_key("master_data.product_categories.view")),
    db: Session = Depends(get_db)
):
    """Get product categories as a tree structure."""
    try:
        service = ProductCategoryService(db)
        tree = service.get_categories_tree()
        return tree
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error in get_categories_tree: {str(e)}")
        logger.error(traceback.format_exc())
        raise handle_internal_error(str(e))


@router.get("/class-labels")
async def get_class_labels(
    current_user: dict = Depends(
        require_any_permission_with_api_key(
            [
                "user_management.settings.view",
                "master_data.spec_registry.view",
                "master_data.products.view",
            ]
        )
    ),
    db: Session = Depends(get_db),
):
    """The distinct class labels categories are grouped by (PLAN-price-tag-combos D2).

    Declared BEFORE `/{category_id}` further down this file, or the path matches
    that route and "class-labels" is read as an id.

    Gated on the SETTINGS view slug, not this router's own category one: its only
    consumer is the System Settings guarded-classes multi-select, and a reader who
    can open that page but holds no category permission would otherwise see an
    empty picker with no way to tell it apart from "nothing is configured"
    (review round 2, S6).

    Either grant since #1286: the Product Specifications list counts Product class's
    choices from this list (its registry row has an open vocabulary), and a spec or
    product reader who holds no settings grant must not see that count fail closed.
    Same either-grant precedent as `GET /spec-registry/applicable-keys`.

    One column, sorted, no paging: the vocabulary is a handful of words and the
    picker shows all of them at once.
    """
    from app.models.product import ProductCategory

    rows = (
        db.query(ProductCategory.class_label)
        .filter(ProductCategory.class_label.isnot(None))
        .filter(ProductCategory.class_label != "")
        .distinct()
        .order_by(ProductCategory.class_label)
        .all()
    )
    return {"data": [label for (label,) in rows]}


@router.get("/", response_model=ListResponse[ProductCategoryResponse])
async def get_categories(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    category_ids: Optional[List[str]] = Query(
        None,
        description="Filter by canonical category UUIDs (repeated / csv / JSON array).",
    ),
    product_ids: Optional[List[str]] = Query(
        None,
        description="Filter to the categories of these product UUIDs (repeated / csv / JSON array).",
    ),
    current_user: dict = Depends(require_permission_with_api_key("master_data.product_categories.view")),
    db: Session = Depends(get_db)
):
    """Get product categories with pagination and search."""
    try:
        service = ProductCategoryService(db)
        result = service.list_categories(
            page=page,
            limit=limit,
            query=query,
            category_ids=parse_uuid_list(category_ids, param_name="category_ids"),
            product_ids=parse_uuid_list(product_ids, param_name="product_ids"),
        )
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/select", response_model=List[ProductCategorySelectItem])
async def get_categories_select(
    query: Optional[str] = Query(None),
    # Any signed-in user (owner ruling 1 Oct 2026, never-stuck L10); an API key still
    # needs the slug. The list, detail and write routes keep their slugs.
    current_user: dict = Depends(require_session_or_api_key_permission("master_data.product_categories.view")),
    db: Session = Depends(get_db)
):
    """Get product categories for select dropdowns."""
    try:
        from sqlalchemy import or_
        from app.models.product import ProductCategory
        q = db.query(ProductCategory).filter(ProductCategory.is_active == True)
        
        if query:
            q = q.filter(
                or_(
                    ProductCategory.category_code.ilike(f"%{query}%"),
                    ProductCategory.category_name.ilike(f"%{query}%")
                )
            )
        q = q.order_by(ProductCategory.display_order.asc(), ProductCategory.category_name.asc())
        categories = q.limit(100).all()
        return categories
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{category_id}", response_model=ProductCategoryResponse)
async def get_category(
    category_id: str,
    current_user: dict = Depends(require_permission_with_api_key("master_data.product_categories.view")),
    db: Session = Depends(get_db)
):
    """Get a single category by ID."""
    try:
        service = ProductCategoryService(db)
        category = service.get_category(category_id)
        return category
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=ProductCategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    category_data: ProductCategoryCreate,
    current_user: dict = Depends(require_permission("master_data.product_categories.add")),
    db: Session = Depends(get_db)
):
    """Create a new category."""
    try:
        guard_chatbot_limits_edit(
            db, current_user["id"], NULL_LIMITS, category_data.model_dump(exclude_unset=True)
        )
        service = ProductCategoryService(db)
        category = service.create_category(category_data)
        return category
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{category_id}", response_model=ProductCategoryResponse)
async def update_category(
    category_id: str,
    category_data: ProductCategoryUpdate,
    current_user: dict = Depends(require_permission("master_data.product_categories.edit")),
    db: Session = Depends(get_db)
):
    """Update a category."""
    try:
        service = ProductCategoryService(db)
        existing = service.get_category(category_id)
        guard_chatbot_limits_edit(
            db, current_user["id"], existing, category_data.model_dump(exclude_unset=True)
        )
        category = service.update_category(category_id, category_data)
        return category
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{category_id}", status_code=status.HTTP_200_OK)
async def delete_category(
    category_id: str,
    current_user: dict = Depends(require_permission("master_data.product_categories.delete")),
    db: Session = Depends(get_db)
):
    """Delete a category. Fails with 409 if any products use this category."""
    try:
        service = ProductCategoryService(db)
        service.delete_category(category_id)
        return {"message": "Category deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
