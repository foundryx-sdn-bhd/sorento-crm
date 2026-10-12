"""Seat cover <-> WC compatibility (WC-SEAT-COMPAT).

    GET  /products/{product_id}/compatibility                  (master_data.products.view)
    POST /products/{product_id}/compatibility                  (master_data.products.edit)
    GET  /product-compatibility/flyer-review                   (master_data.products.view)
    POST /product-compatibility/flyer-review/apply             (master_data.products.edit)

Mounted with no prefix: the routes live under two path roots (the combos precedent). Writes
are session-only (`require_permission`), as for alternatives: an integration key never
rewrites what fits what.

Unlink and retiring the old `Seat Cover` spec are server-deferred record actions
(`product_compatibility.unlink`, `seat_cover_spec.retire`, `app/services/record_actions.py`)
parked through `/api/v1/pending-actions`.
Plan: documentation/plans/products/PLAN-wc-seat-compat-12oct.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.product_compatibility import (
    FlyerApply,
    FlyerApplyOut,
    FlyerReviewOut,
    ProductCompatibilityCreate,
    ProductCompatibilityListOut,
    ProductCompatibilityRowOut,
)
from app.services import product_compatibility_service as compat
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

VIEW = "master_data.products.view"
EDIT = "master_data.products.edit"


@router.get("/products/{product_id}/compatibility", response_model=ProductCompatibilityListOut)
def list_product_compatibility(
    product_id: str,
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    product_id = validate_uuid_path(product_id, resource="Product")
    return {"data": compat.list_for_product(db, product_id)}


@router.post(
    "/products/{product_id}/compatibility",
    response_model=ProductCompatibilityRowOut,
    status_code=status.HTTP_201_CREATED,
)
def add_product_compatibility(
    product_id: str,
    payload: ProductCompatibilityCreate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    product_id = validate_uuid_path(product_id, resource="Product")
    other_id = validate_uuid_path(payload.other_product_id, resource="Product")
    row = compat.add_manual(db, product_id, other_id, payload.other_role, current_user.get("id"))
    return {"data": row}


@router.get("/product-compatibility/flyer-review", response_model=FlyerReviewOut)
def get_flyer_review(
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    return {"data": compat.flyer_review(db)}


@router.post("/product-compatibility/flyer-review/apply", response_model=FlyerApplyOut)
def apply_flyer_review(
    payload: FlyerApply,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    ids = [pid.lower() for pid in payload.wc_product_ids]
    return {"data": compat.apply_flyer(db, ids, current_user.get("id"))}
