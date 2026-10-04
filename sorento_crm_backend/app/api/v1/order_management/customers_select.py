"""Customer select endpoint for dropdowns."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Literal, Optional
from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.models.base import get_company_scope
from app.models.order import Customer
from app.services.company_scope_resolver import (
    company_name_map,
    grants_requested,
    tag_company,
    widen_if_requested,
)
from app.services.error_handler import handle_internal_error
from app.services.scm.sales_order_service import SalesOrderService

router = APIRouter()

#: The largest page a caller may ask for, matching `master_data/products_select.py`.
MAX_LIMIT = 200


@router.get("/select")
async def get_customers_select(
    query: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    company_scope: Optional[Literal["grants"]] = Query(
        None,
        description="`grants` reads every company the caller is granted (staff sessions only) and tags each row with its company.",
    ),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Customers for a dropdown, SEARCHABLE AND PAGEABLE on the server.

    This used to return every active customer as a whole ORM row - 6,397 of them on the
    client's database - on every open of every customer select in the product. The
    sales-order detail page is where it finally showed: seconds to open a dropdown, because
    the browser was being handed the entire debtor master to filter locally.

    **`limit` has no default, deliberately.** `products_select.py` could default to 100
    because that is exactly what it already returned unconditionally; this endpoint already
    returned EVERYTHING, so a default of 50 here would silently make the 51st customer
    unreachable in the two callers that hold the whole array and filter it in the browser
    (`use-customer-select-query`, `scmOptionsService.getCustomerOptions`). Omitted means
    what it has always meant; a caller that wants paging asks for it.

    Ordered by code so paging is stable: without an ORDER BY, two pages of the same result
    set can repeat or skip rows.

    Fields are listed explicitly rather than dumping the ORM row - the raw row carries
    credit limits and payment terms, and a dropdown is not the place to decide who may see a
    customer's credit.
    """
    try:
        with widen_if_requested(db, request, current_user, company_scope):
            return _customers_select(db, query, limit, offset, tag=grants_requested(request, current_user, company_scope))
    except Exception as e:
        raise handle_internal_error(str(e))


def _customers_select(db: Session, query: Optional[str], limit: Optional[int], offset: int, tag: bool):
    q = db.query(Customer).filter(Customer.is_active == True)  # noqa: E712

    if query:
        needle = f"%{query.strip()}%"
        q = q.filter(
            or_(
                Customer.customer_code.ilike(needle),
                Customer.customer_name.ilike(needle),
            )
        )

    q = q.order_by(Customer.customer_code).offset(offset)
    if limit is not None:
        q = q.limit(limit)
    customers = q.all()
    names = company_name_map(db) if tag else None

    return {
        "data": [
            {
                "id": c.id,
                "customer_code": c.customer_code,
                "customer_name": c.customer_name,
                "market_segment_code": c.market_segment_code,
                # The agent handling this customer, so a picker can say who a pick
                # would move it away from. The relationship is `selectin`: no per-row query.
                "sales_agent_id": c.sales_agent_id,
                "sales_agent_code": c.sales_agent_code,
                "sales_agent_name": c.sales_agent_name,
                # The group the customer sits in (`selectin`, no per-row query), so the
                # group page's Add ledgers picker can say where a pick would move it from.
                "customer_group_id": c.customer_group_id,
                "customer_group_name": c.customer_group_name,
                # Set only on a `company_scope=grants` read.
                **(
                    {"company_id": c.company_id, "company_name": names.get(str(c.company_id))}
                    if names is not None
                    else {}
                ),
            }
            for c in customers
        ],
        # `total` is the size of THIS page, as it always has been - a true count would
        # be a second full scan on every keystroke, and no caller reads it.
        "pagination": {
            "total": len(customers),
            "page": (offset // limit) + 1 if limit else 1,
            "limit": limit,
            "offset": offset,
        },
        "empty": len(customers) == 0
    }


@router.get("/sales-agents-select")
async def get_customer_sales_agents_select(
    query: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("order_management.customers.view")),
    db: Session = Depends(get_db),
):
    """Sales agents for the customer form's "Sales agent" select, code + name, unscoped by
    page (~38 active rows today, comfortably below one), restricted to the caller's company
    scope (shared agents - `company_id IS NULL`, the ~38 real rows today - always included).

    Gated on `order_management.customers.view` (review PR #1177, security item 1) rather
    than left on the bare module guard every other route in this file carries: this one
    hands back the whole agent roster (codes + person names), which is more than "pick a
    customer" needs, so it earns its own permission check the siblings do not.

    Delegates the query to `SalesOrderService.list_agents` (review PR #1177, blocking item
    1) - the SAME function `/scm/sales-orders/agents` calls - so this select and the SCM one
    can never drift about which agents exist. `query` matches the code OR the annotated
    person, since a customer edit picks the agent by whichever one is remembered.
    """
    try:
        agents = SalesOrderService(db).list_agents(query, scope=get_company_scope(db))
        return {
            "data": [
                {"id": a["id"], "sales_agent": a["sales_agent"], "person_label": a["person_label"]}
                for a in agents
            ],
        }
    except Exception as e:
        raise handle_internal_error(str(e))
