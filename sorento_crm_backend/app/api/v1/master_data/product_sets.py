"""Product sets: the code that names an assembly sold as one thing.

`SRTWC8608-RL` is printed on a flyer and asked for on WhatsApp, but the catalogue
holds only its parts. A set is that missing code, and this is where a person
authors one.

A set is NOT orderable. There is deliberately no stock write, no costing and no
order route here - what would be ordered is always its members.

Company scope is applied by the session listener, not by these handlers, so a
set belonging to another company is a 404 rather than a 403: a scoped reader must
not learn that it exists.

UAC: `documentation/plans/master-data/product-sets-acceptance-criteria.md`.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user_or_api_key, require_permission_with_api_key
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.product_set import (
    ApplyProductSetProposalsRequest,
    ApplyProductSetProposalsResponse,
    ProductSetCreate,
    ProductSetDetailResponse,
    ProductSetProposalBatchResponse,
    ProductSetProposalsResponse,
    ProductSetResponse,
    ProductSetUpdate,
)
from app.modules.runtime.guards import module_allowed_with_api_key
from app.services.error_handler import AppException, handle_internal_error
from app.services.product_set_proposal_service import ProductSetProposalService
from app.services.product_set_service import ProductSetService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()


def _company_name(db: Session, company_id: Optional[str]) -> Optional[str]:
    """Resolved for the header, never a bare `company_id` on screen."""
    if not company_id:
        return None
    from app.models.company import Company

    row = db.query(Company).filter(Company.id == company_id).first()
    return row.name if row else None


def _user_name(db: Session, user_id: Optional[str]) -> Optional[str]:
    """Who set the override. Never the raw id - no UUID reaches the screen."""
    if not user_id:
        return None
    from app.models.user import User

    row = db.query(User).filter(User.id == str(user_id)).first()
    return (row.name or row.email) if row else None


def _serialize(product_set, db: Session) -> dict:
    """Flatten the members' product fields, so no UUID reaches the screen."""
    price = product_set.price.as_dict()
    # Merged in here rather than in `SetPrice.as_dict()`: that method is a pure,
    # session-free dataclass method with its own field-set test, and "who set
    # it" needs a query. Additive only - nothing already declared moves.
    price["override_set_by_name"] = _user_name(db, product_set.override_set_by)
    price["override_set_at"] = product_set.override_set_at
    payload = {
        "id": product_set.id,
        "set_code": product_set.set_code,
        "name": product_set.name,
        "is_active": product_set.is_active,
        "company_id": product_set.company_id,
        "company_name": _company_name(db, product_set.company_id),
        "price": price,
        "member_count": product_set.member_count,
        "complete_sets": product_set.complete_sets,
        "limiting_member_code": product_set.limiting_member_code,
        "override_set_by": product_set.override_set_by,
        "override_set_at": product_set.override_set_at,
        "created_at": product_set.created_at,
        "updated_at": product_set.updated_at,
        "members": [
            {
                "id": m.id,
                "product_id": m.product_id,
                "product_code": getattr(m.product, "product_code", None),
                "product_name": getattr(m.product, "product_name", None),
                "description": getattr(m.product, "description", None),
                "list_price": getattr(m.product, "list_price", None),
                "is_discontinued": bool(getattr(m.product, "is_discontinued", False)),
                "quantity": m.quantity,
                "contributes_to_price": m.contributes_to_price,
                "sort_order": m.sort_order,
                "available": getattr(m, "available", None),
            }
            for m in product_set.members
        ],
    }
    return payload


@router.get("/", response_model=ListResponse[ProductSetResponse])
def list_product_sets(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.view")
    ),
    db: Session = Depends(get_db),
):
    try:
        result = ProductSetService(db).list(page=page, limit=limit, query=query)
        return {
            "data": [_serialize(row, db) for row in result.data],
            "pagination": {"total": result.total, "page": result.page, "limit": result.limit},
            "empty": result.total == 0,
        }
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/select")
def select_product_sets(
    query: Optional[str] = Query(None),
    ids: Optional[str] = Query(None, description="Comma list; inactive sets included."),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Sets for the quotation line picker, session-gated like `products/select`.

    Deliberately NOT behind `master_data.product_sets.view`: the sales roles that quote a set
    do not hold it, exactly as they read products through `products/select`. Own company only
    (the scoped session), items carry no cost figure.

    DECLARED BEFORE `/{product_set_id}`, or that route swallows `select` whole.
    """
    try:
        wanted = [part.strip() for part in (ids or "").split(",") if part.strip()]
        if len(wanted) > 100:
            raise AppException(status_code=422, message="Too many ids (100 at most).")
        for part in wanted:
            try:
                validate_uuid_path(part, resource="Product set")
            except Exception:
                raise AppException(status_code=422, message="An id is not valid.")
        rows = ProductSetService(db).select(query=query, ids=wanted or None)
        # Stock is the inventory module's data and the quoting roles' to read: anyone else gets
        # the set without a count.
        from app.services.user_service import UserPermissionService

        with_stock = bool(
            UserPermissionService(db).check_user_has_permission(
                current_user["id"], "projects.projects.view"
            )
        ) and module_allowed_with_api_key(db, current_user, "inventory")
        brands = ProductSetService(db).driver_brand_names([row.id for row in rows])
        return {
            "data": [
                {
                    "id": row.id,
                    "set_code": row.set_code,
                    "name": row.name,
                    "is_active": row.is_active,
                    "member_count": row.member_count,
                    "list_price": row.price.resolved,
                    "complete_sets": row.complete_sets if with_stock else None,
                    "brand_name": brands.get(row.id),
                    "parts": [
                        {
                            "product_code": getattr(m.product, "product_code", None),
                            "quantity": m.quantity,
                        }
                        for m in row.members
                    ],
                }
                for row in rows
            ]
        }
    except Exception as e:
        raise e if hasattr(e, "status_code") else handle_internal_error(str(e))


# --------------------------------------------------------------- proposals
#
# DECLARED BEFORE `/{product_set_id}`, and it has to stay that way: FastAPI
# matches in declaration order, so a UUID path param declared first swallows
# `/proposals` whole and the review screen gets "Product set not found" instead
# of its batch. This repo has already shipped exactly that bug on the SLA
# escalate routes.
#
# Same router, so the module guard and the permission scheme already apply, and
# the permissions are the set's own: proposing and applying are authoring.


@router.get("/proposals", response_model=ProductSetProposalsResponse)
def get_product_set_proposals(
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.view")
    ),
    db: Session = Depends(get_db),
):
    """The open batch, or null when no pass has run.

    Null is not the same as a pass that found nothing, and the review screen says
    two different things about the two.
    """
    return {"batch": ProductSetProposalService(db).current()}


@router.post("/proposals", response_model=ProductSetProposalBatchResponse)
def run_product_set_proposals(
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.edit")
    ),
    db: Session = Depends(get_db),
):
    """Derive candidates from the catalogue and REPLACE the company's open batch.

    Writes nothing to `product_sets`. Synchronous, deliberately: this is a pure
    derivation over code shape with no model call and no file to read, so a job
    queue would only add a status to poll.
    """
    return ProductSetProposalService(db).run(created_by=current_user.get("id"))


@router.post("/proposals/apply", response_model=ApplyProductSetProposalsResponse)
def apply_product_set_proposals(
    payload: ApplyProductSetProposalsRequest,
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.edit")
    ),
    db: Session = Depends(get_db),
):
    """Create a set per ticked proposal. Ids only; a refusal is named, not raised."""
    return ProductSetProposalService(db).apply(
        payload.proposal_ids, applied_by=current_user.get("id")
    )


@router.get("/{product_set_id}", response_model=ProductSetDetailResponse)
def get_product_set(
    product_set_id: str,
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.view")
    ),
    db: Session = Depends(get_db),
):
    validate_uuid_path(product_set_id, resource="Product Set")
    return _serialize(ProductSetService(db).get(product_set_id), db)


@router.post(
    "/", response_model=ProductSetDetailResponse, status_code=status.HTTP_201_CREATED
)
def create_product_set(
    payload: ProductSetCreate,
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.edit")
    ),
    db: Session = Depends(get_db),
):
    created = ProductSetService(db).create(
        payload.model_dump(), created_by=current_user.get("id")
    )
    return _serialize(created, db)


@router.put("/{product_set_id}", response_model=ProductSetDetailResponse)
def update_product_set(
    product_set_id: str,
    payload: ProductSetUpdate,
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.edit")
    ),
    db: Session = Depends(get_db),
):
    validate_uuid_path(product_set_id, resource="Product Set")
    # `exclude_unset` is what preserves "omitted leaves membership alone" against
    # "`members: []` empties it" - model_dump() alone would flatten both to [].
    updated = ProductSetService(db).update(
        product_set_id,
        payload.model_dump(exclude_unset=True),
        updated_by=current_user.get("id"),
    )
    return _serialize(updated, db)


@router.delete("/{product_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product_set(
    product_set_id: str,
    current_user: dict = Depends(
        require_permission_with_api_key("master_data.product_sets.delete")
    ),
    db: Session = Depends(get_db),
):
    """Hard delete, per the CRUD standard. Members go; their products do not."""
    validate_uuid_path(product_set_id, resource="Product Set")
    ProductSetService(db).delete(product_set_id)
    return None
