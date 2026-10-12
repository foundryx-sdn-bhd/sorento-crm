"""Generic product find (CHAT-FIND): `GET /products/find`, the route behind MCP `crm_find`.

    GET /api/v1/master-data/products/find?filters=<json>&contact_id=&space_id=
        (master_data.products.view, session or X-API-Key)

`filters` = JSON list of `{key, product_ids?}`, keys from the registry
(`app/services/product_find.py`). Company and brand scope come from the request
(`contact_id` + `space_id`, `company_scope_resolver`); the stock lines follow the contact's
stock-visibility policy. Read only. Registered BEFORE `products.router` so `/find` is never
read as a product id. Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission_with_api_key
from app.services import product_find

router = APIRouter()

VIEW = "master_data.products.view"


@router.get("/find")
def find_products(
    filters: Optional[str] = Query(
        None,
        description=(
            "JSON list of {key, product_ids?}. Keys: the find registry "
            "(app/services/product_find.py), e.g. fits = the products linked as fitting "
            "the named products (seat cover <-> WC)."
        ),
    ),
    contact_id: Optional[str] = Query(None, description="The contact asked on behalf of."),
    space_id: Optional[str] = Query(None, description="Respond.io workspace of contact_id."),
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    parsed = product_find.parse_filters(filters)
    return product_find.run_find(db, parsed, contact_id=contact_id, space_id=space_id)
