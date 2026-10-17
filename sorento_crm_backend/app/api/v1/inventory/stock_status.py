"""GET /api/v1/inventory/stock-status - AutoCount's Available Stock Status for one item.

Read-only. Served to every screen that embeds the panel, so any one of their read permissions
opens it: the stand-alone Inventory page, order inquiries and the fulfilment board
(`projects.projects.view`), the loading plan (`scm.reorder.run`) and the SCM dashboard.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_any_permission_with_api_key
from app.services.stock_status_service import stock_status

router = APIRouter()

READ_PERMISSIONS = [
    "inventory.stock.view",
    "projects.projects.view",
    "scm.reorder.run",
    "scm.dashboard.view",
]


@router.get("")
def get_stock_status(
    product_id: str = Query(..., description="The product (uuid)."),
    so_due_by: Optional[date] = Query(
        None,
        description="Count SO due on or before this date only; undated SO always counts.",
    ),
    _user: dict = Depends(require_any_permission_with_api_key(READ_PERMISSIONS)),
    db: Session = Depends(get_db),
):
    """Every location holding the item, its SO / PO / SPO documents and incoming packing lists."""
    return stock_status(db, product_id, so_due_by)
