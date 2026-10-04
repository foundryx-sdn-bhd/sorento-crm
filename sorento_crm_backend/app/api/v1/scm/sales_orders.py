"""SCM M1 sales-order endpoints - CRUD + create-DO-from-SO.

Read gated on ``scm.dashboard.view``; write gated on ``scm.reorder.run`` (the
SCM operator capability). No UUIDs surfaced - SO by so_number.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas import scm_orders as scm_orders_schemas
from app.schemas.scm_orders import (
    CreateDoResponse,
    SalesAgentOption,
    SalesOrder,
    SalesOrderFormData,
    SalesOrderListResponse,
    SalesOrderUpdate,
)
from app.services.scm.sales_order_service import SalesOrderService

router = APIRouter()

_READ = require_permission_with_api_key("scm.dashboard.view")
_WRITE = require_permission("scm.reorder.run")


class UomOption(BaseModel):
    """One active `units_of_measure` row, for the line UoM select on the detail page."""

    id: str
    uom_code: str
    uom_name: str


class PlanningChangeBatchEnvelope(BaseModel):
    """`PLAN-so-book-diff-replanning.md` section 2's own envelope - the same shape the
    SO-book upload's own preview response carries, so the FE reads one contract whichever
    of the two raised it."""

    id: str
    order_count: int
    line_count: int


class SalesOrderUpdateResponse(SalesOrder):
    """`SalesOrder` plus the planning-change batch THIS save raised, when the edit changed
    a project-linked line's qty/date (`SalesOrderService._propagate_planning_change`).
    `None` on every save that raised nothing - most of them."""

    planning_change_batch: Optional[PlanningChangeBatchEnvelope] = None


# `SalesOrder` (the parent) is declared in `app.schemas.scm_orders`, a different module -
# also under `from __future__ import annotations`, so its INHERITED fields (`lines:
# List[...]` etc) are still STRING annotations that need THAT module's namespace (`List`
# is not imported here) to resolve, while `planning_change_batch` needs THIS module's
# (`PlanningChangeBatchEnvelope`). Left unbuilt, this class is "not fully defined" (a
# `PydanticUserError` on the very first request); a plain `force=True` rebuild resolves
# against only this module and breaks on the inherited fields instead. Both namespaces,
# explicitly.
SalesOrderUpdateResponse.model_rebuild(
    force=True,
    _types_namespace={**vars(scm_orders_schemas), **globals()},
)


@router.get("/sales-orders", response_model=SalesOrderListResponse)
def list_sales_orders(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    sort: Optional[str] = Query(None),
    dir: str = Query("desc"),
    query: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    source: Optional[str] = Query(
        None,
        description=(
            "Where the order came from: autocount | inquiry | upload | history | manual. "
            "Omit for all."
        ),
    ),
    date_from: Optional[date] = Query(None, description="Earliest order date, inclusive."),
    date_to: Optional[date] = Query(None, description="Latest order date, inclusive."),
    customer_code: Optional[str] = Query(
        None,
        description="Keep only this customer's orders. By code, which several legal entities "
                    "can share, so it means all of them.",
    ),
    outstanding: bool = Query(
        False, description="Keep only orders with quantity still owed. Off narrows nothing."
    ),
    sales_agent_id: Optional[str] = Query(
        None, description="Keep only this sales agent's orders."
    ),
    demand_class: Optional[str] = Query(
        None,
        description="Keep only this planning class: project | retail | unclassified "
                    "(demand_class IS NULL). Omit for all.",
    ),
    transferable: Optional[str] = Query(
        None,
        description="AutoCount's Transferable flag: yes | no | unknown (never stated). "
                    "Omit for all.",
    ),
    db: Session = Depends(get_db),
    _user: dict = Depends(_READ),
):
    """The sales-order list.

    `source=inquiry` answers "show me the orders the Order Inquiry sheet created" - a filter
    on this list rather than a screen of its own, because a second list of the same entity is
    how two screens start disagreeing about the same order.

    The date range is inclusive of both ends, and `outstanding` reads the same rule the
    netting engine does (`app.services.scm.demand`) so this screen and the plan cannot
    disagree about which orders are still owed.
    """
    svc = SalesOrderService(db)
    out = svc.list(
        page, limit, sort, dir, query, status, priority, source,
        date_from=date_from, date_to=date_to, customer_code=customer_code,
        outstanding=outstanding, sales_agent_id=sales_agent_id,
        demand_class=demand_class, transferable=transferable,
    )
    out["data"] = svc.with_planning_state(
        svc.with_planning_changes(
            svc.with_order_inquiries(svc.with_links(out["data"]))
        )
    )
    return out


@router.get("/sales-orders/agents", response_model=list[SalesAgentOption])
def list_sales_order_agents(
    q: Optional[str] = Query(
        None, description="Substring match on the agent code or person label."
    ),
    db: Session = Depends(get_db),
    _user: dict = Depends(_READ),
):
    """Every active sales agent, for the list's Agent filter and the detail page's Agent
    select. Declared before `/{so_id}` so `agents` never parses as a SO id.

    Gated on `scm.dashboard.view` (this router's read permission) rather than
    `master_data.sales_agents.view` - the sales-agents master's own CRUD permission - since
    a role that can read SCM sales orders (e.g. Purchasing) does not necessarily hold the
    master-data one, and would otherwise 403 on this select alone.
    """
    return SalesOrderService(db).list_agents(q)


@router.get("/sales-orders/uoms", response_model=list[UomOption])
def list_sales_order_uoms(
    db: Session = Depends(get_db),
    _user: dict = Depends(_READ),
):
    """Every active unit of measure, for the line UoM select on the detail page.

    Gated on `scm.dashboard.view` - this router's own read permission - rather than the
    master data `/units-of-measure/select` route's `master_data.units_of_measure.view`,
    for the same reason `/sales-orders/agents` above is: a purchasing/SCM-only role does
    not necessarily hold the master-data permission, and would 403 on that select alone.
    Declared before `/{so_id}` so `uoms` never parses as a SO id.
    """
    return SalesOrderService(db).list_uom_options()


@router.get("/sales-orders/{so_id}", response_model=SalesOrder)
def get_sales_order(so_id: str, db: Session = Depends(get_db), _user: dict = Depends(_READ)):
    return SalesOrderService(db).get(so_id)


@router.post("/sales-orders", response_model=SalesOrder, status_code=status.HTTP_201_CREATED)
def create_sales_order(
    data: SalesOrderFormData,
    db: Session = Depends(get_db),
    user: dict = Depends(_WRITE),
):
    return SalesOrderService(db).create(data, user.get("id"))


@router.put("/sales-orders/{so_id}", response_model=SalesOrderUpdateResponse)
def update_sales_order(
    so_id: str,
    data: SalesOrderUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(_WRITE),
):
    return SalesOrderService(db).update(so_id, data, user.get("id"))


@router.delete("/sales-orders/{so_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sales_order(so_id: str, db: Session = Depends(get_db), _user: dict = Depends(_WRITE)):
    SalesOrderService(db).delete(so_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sales-orders/{so_id}/reset-planning", response_model=scm_orders_schemas.ResetPlanningResponse)
def reset_sales_order_planning(
    so_id: str,
    body: scm_orders_schemas.ResetPlanningRequest,
    db: Session = Depends(get_db),
    _user: dict = Depends(_WRITE),
):
    """Back to never-planned: inquiries, links, allocations, transfers, decisions go; the order stays."""
    from app.services.scm.planning_reset_service import reset_planning

    return reset_planning(db, so_id, rewind_book=body.rewind_book)


@router.post("/sales-orders/{so_id}/create-do", response_model=CreateDoResponse)
def create_do_from_so(
    so_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(_WRITE),
):
    return SalesOrderService(db).create_do_from_so(so_id, user.get("id"))
