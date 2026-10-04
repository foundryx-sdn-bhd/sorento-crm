"""Purchase orders placed but not yet received (A5, chatbot-growth-r1)."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user_or_api_key
from app.api.v1.order_management.orders import _parse_flex_date  # shared flexible date parser
from app.services.error_handler import AppException, handle_internal_error
from app.services.po_last_cost_service import last_cost_rows
from app.services.purchase_order_service import (
    PO_GROUP_BY_AXES,
    PO_SORT_DIRS,
    PO_SORT_KEYS,
    group_rows,
    purchase_orders_placed_rows,
    purchase_orders_placed_summary,
)
from app.services.uuid_list_param import parse_uuid_list

router = APIRouter()
logger = logging.getLogger(__name__)


#: (chatbot-last-purchase-cost, review N1) registered ahead of `/placed` below. This
#: router carries no `/{po_id}`-shaped route today, so there is nothing for "last-cost"
#: to collide with yet - the placement is precautionary, matching `/placed`'s own
#: position, so a future bare-uuid route added to this router cannot swallow it by
#: registration order (Starlette matches in the order routes are added).
@router.get("/last-cost")
def get_purchase_orders_last_cost(
    product_ids: Optional[list[str]] = Query(
        None, description="Filter by canonical product UUIDs (csv / JSON / repeated)."
    ),
    warehouse_ids: Optional[list[str]] = Query(
        None, description="Filter by canonical warehouse UUIDs (csv / JSON / repeated)."
    ),
    top_n: int = Query(
        1, ge=1, le=50,
        description="Lines per (product, warehouse) when product_ids is given, else lines overall.",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """The last `top_n` PO lines per `(product, warehouse)`, grouped by product, newest
    PO date first within each product (owner ruling, live verification, 12 Sep 2026,
    verbatim: "we need to sort by latest PO date first otherwise very confusing, so
    first sort by the product, then latest PO date first"). With NO `product_ids` (an
    unscoped ask), `top_n` is instead a plain cap over every line - the same ordering,
    newest first, any product - never one row per product across the whole table.

    Cancelled lines and cancelled POs are excluded; a line with no `unit_cost` never
    answers. `Cost / unit`, `Discount / unit` and `Cost after discount / unit` are all
    PER UNIT, derived from the line's own `discount` / `line_total` amounts - see
    `po_last_cost_service` for the measured shape. `discount_per_unit` is ALWAYS a
    number, `0.0` when the line carries none (owner ruling, live verification, 12 Sep
    2026). `supplier` is the PO's own `suppliers.supplier_name`, None when it has none.
    `warehouse_ids` narrows before the pick.
    """
    try:
        rows = last_cost_rows(
            db,
            product_ids=parse_uuid_list(product_ids, param_name="product_ids"),
            warehouse_ids=parse_uuid_list(warehouse_ids, param_name="warehouse_ids"),
            top_n=top_n,
        )
        return JSONResponse(
            content=jsonable_encoder(
                {
                    "data": rows,
                    "pagination": {"total": len(rows), "page": 1, "limit": len(rows)},
                    "empty": not rows,
                }
            )
        )
    except AppException:
        raise
    except Exception as e:  # noqa: BLE001
        raise handle_internal_error(str(e))


@router.get("/placed")
def get_purchase_orders_placed(
    limit: int = Query(50, ge=1, le=500),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Filter by canonical product UUIDs (csv / JSON / repeated).",
    ),
    warehouse_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Filter by warehouse UUIDs (csv / JSON / repeated): the PO line's warehouse; an SPO "
            "allocation also matches by its book location code."
        ),
    ),
    expected_date_from: Optional[str] = Query(
        None, description="Filter by expected date from (inclusive). Line expected_date, else header's."
    ),
    expected_date_to: Optional[str] = Query(
        None, description="Filter by expected date to (inclusive)."
    ),
    group_by: Optional[str] = Query(
        None, description="Group rows into headed sections: product | supplier | date."
    ),
    include_summary: bool = Query(
        False,
        description="true = also return `summary`: po_placed_qty/po_placed_count over the filtered lines.",
    ),
    sort: Optional[str] = Query("expected_date"),
    dir: Optional[str] = Query("asc"),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "The contact this is asked on behalf of. Its stock-visibility policy withholds "
            "`location` on every row whose location that contact may not see."
        ),
    ),
    space_id: Optional[str] = Query(None, description="Respond.io workspace id for `contact_id`."),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Open purchase-order lines: `qty_ordered - qty_received > 0`, `line_status='open'`.

    Never nets against incoming SPO receipts (`spo_allocations.po_line_id` is
    NULL on every row) - "PO placed, not yet shipped" is the whole answer this
    route gives; A6 answers "last in" separately.

    `warehouse_ids` narrows PO lines by their warehouse and SPO allocations by warehouse or
    location code. `sort` is one of expected_date | product | supplier | outstanding_qty |
    po_date | ordered_qty and `dir` asc | desc; anything else is a 422
    (PLAN-po-spo-warehouse-29sep O1).
    """
    if group_by is not None and group_by not in PO_GROUP_BY_AXES:
        raise AppException(
            422,
            f"Unknown group_by value '{group_by}'",
            detail=f"allowed: {', '.join(sorted(PO_GROUP_BY_AXES))}",
            code="invalid_group_by",
        )
    # An empty `sort=` / `dir=` is the default, as it was before validation existed.
    sort = sort or "expected_date"
    dir = dir or "asc"
    if sort not in PO_SORT_KEYS:
        raise AppException(
            422,
            f"Unknown sort value '{sort}'",
            detail=f"allowed: {', '.join(sorted(PO_SORT_KEYS))}",
            code="invalid_sort",
        )
    if dir not in PO_SORT_DIRS:
        raise AppException(
            422,
            f"Unknown dir value '{dir}'",
            detail=f"allowed: {', '.join(sorted(PO_SORT_DIRS))}",
            code="invalid_dir",
        )
    try:
        resolved_product_ids = parse_uuid_list(product_ids, param_name="product_ids")
        resolved_warehouse_ids = parse_uuid_list(warehouse_ids, param_name="warehouse_ids")
        _from = _parse_flex_date(expected_date_from)
        _to = _parse_flex_date(expected_date_to, end_of_day=True)
        rows = purchase_orders_placed_rows(
            db,
            product_ids=resolved_product_ids,
            warehouse_ids=resolved_warehouse_ids,
            expected_date_from=_from.date() if _from else None,
            expected_date_to=_to.date() if _to else None,
            sort=sort or "expected_date",
            dir=dir or "asc",
            limit=limit,
        )
        if contact_id:
            # STOCK-TOTAL-OS-SCOPE: the chatbot's on-order note must never name a location
            # the contact's stock-visibility policy hides. The row stays (what is on order
            # is still the answer); only its location is withheld.
            from app.services.stock_visibility import visible_location_codes

            allowed = visible_location_codes(db, contact_id, space_id)
            if allowed is not None:
                rows = [
                    {**r, "location": None} if r.get("location") not in allowed else r
                    for r in rows
                ]
        payload: dict = {
            "data": rows,
            "pagination": {"total": len(rows), "page": 1, "limit": len(rows)},
            "empty": not rows,
        }
        if group_by:
            payload["groups"] = group_rows(rows, group_by=group_by)
        if include_summary:
            payload["summary"] = {
                "scope": "filter",
                "row_count": len(rows),
                # The SAME filters the rows took, the date window included (review,
                # should-fix 6): a summary that counts more than the list under it is
                # worse than no summary.
                **purchase_orders_placed_summary(
                    db,
                    product_ids=resolved_product_ids,
                    warehouse_ids=resolved_warehouse_ids,
                    expected_date_from=expected_date_from,
                    expected_date_to=expected_date_to,
                ),
            }
        return JSONResponse(content=jsonable_encoder(payload))
    except (AppException, HTTPException):
        # `parse_uuid_list`'s own 400 (a malformed uuid) is the caller's answer, not a 500.
        raise
    except Exception as e:  # noqa: BLE001
        raise handle_internal_error(str(e))
