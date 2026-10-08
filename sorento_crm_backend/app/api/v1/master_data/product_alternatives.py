"""Product alternatives on the product page (PRODUCT-ALTERNATIVES, card Q5 (a)).

    GET  /products/{product_id}/alternatives              (master_data.products.view)
    POST /products/{product_id}/alternatives              (master_data.products.edit)
    POST /products/{product_id}/alternatives/reset-auto   (master_data.products.edit)

Unlink is not a route here: it is the server-deferred record action
`product_alternative.unlink` (`app/services/record_actions.py`), parked through
`/api/v1/pending-actions` like every other detach. Gated the companions way: the
products permission, since the links are configured on the product page itself.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission_with_api_key
from app.schemas.product_alternative import (
    ProductAlternativeCreate,
    ProductAlternativeListOut,
    ProductAlternativeRowOut,
)
from app.services import product_alternative_service as alternatives
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

VIEW = "master_data.products.view"
EDIT = "master_data.products.edit"


@router.get("/{product_id}/alternatives", response_model=ProductAlternativeListOut)
def list_product_alternatives(
    product_id: str,
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    validate_uuid_path(product_id, resource="Product")
    return {"data": alternatives.list_for_product(db, product_id)}


@router.post(
    "/{product_id}/alternatives",
    response_model=ProductAlternativeRowOut,
    status_code=status.HTTP_201_CREATED,
)
def add_product_alternative(
    product_id: str,
    payload: ProductAlternativeCreate,
    current_user: dict = Depends(require_permission_with_api_key(EDIT)),
    db: Session = Depends(get_db),
):
    validate_uuid_path(product_id, resource="Product")
    validate_uuid_path(payload.alternative_product_id, resource="Product")
    row = alternatives.add_manual(
        db, product_id, payload.alternative_product_id, current_user.get("id")
    )
    return {"data": row}


@router.post("/{product_id}/alternatives/reset-auto", response_model=ProductAlternativeListOut)
def reset_product_alternatives(
    product_id: str,
    current_user: dict = Depends(require_permission_with_api_key(EDIT)),
    db: Session = Depends(get_db),
):
    validate_uuid_path(product_id, resource="Product")
    return {"data": alternatives.reset_auto(db, product_id)}
