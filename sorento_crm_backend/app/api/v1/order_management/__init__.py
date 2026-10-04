"""Order management API routes."""
from fastapi import APIRouter
from app.api.v1.order_management import (
    orders,
    branches,
    customers,
    customer_groups,
    customers_select,
    order_statuses,
)

router = APIRouter()

router.include_router(orders.router, prefix="/orders", tags=["orders"])
# No prefix: the plan/UAC pin this path at `/order-management/outstanding-report`,
# a report over orders, not a row under `/orders/*` (`orders.py` docstring above
# the router definition).
router.include_router(orders.outstanding_report_router, tags=["orders"])
# No prefix, same reason: the plan/UAC pin this path at
# `/order-management/sales-report`, a report over orders, not a row under
# `/orders/*`.
router.include_router(orders.sales_report_router, tags=["orders"])
# `customers_select` FIRST. Both mount at `/customers`, and `customers.router` carries
# `GET /{customer_id}` - so mounted first it matches `/customers/select`, tries to read
# "select" as a customer id and answers 404 "Customer not found. Someone might have deleted
# it already." Every screen with a customer dropdown then shows that toast on load, which
# reads as a data problem rather than a routing one. FastAPI matches in registration order,
# so the literal path has to be registered before the parameterised one. Same defect family
# as the SLA `/integration/escalate` shadowing.
router.include_router(customers_select.router, prefix="/customers", tags=["customers"])
router.include_router(customers.router, prefix="/customers", tags=["customers"])
router.include_router(customer_groups.router, prefix="/customer-groups", tags=["customer-groups"])
# Customer Branches (#1356): the AutoCount branch table, read only.
router.include_router(branches.router, prefix="/branches", tags=["branches"])
router.include_router(order_statuses.router, prefix="/order-statuses", tags=["order-statuses"])