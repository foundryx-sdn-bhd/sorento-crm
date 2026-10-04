"""Schemas for order-management routes that are not row-CRUD.

CRUD schemas for orders/customers/statuses live in `app/schemas/order.py`; this
file is for a cross-cutting, read-only response shape scoped to
`/api/v1/order-management/*` that is a REPORT, not a listing of one entity -
today, `OutstandingReportResponse`
(`documentation/plans/chatbot/PLAN-chatbot-outstanding-report.md` "Backend
contract"; AC-1110 to AC-1119).
"""
from __future__ import annotations

from datetime import date
from datetime import date as _date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


#: Every quantity below is a WHOLE unit. The columns behind them are `Numeric(15,4)`,
#: so a fractional quantity is storable; the service rounds it HALF UP before it reaches
#: these models (`outstanding_report_service._qty`), because the reply prints
#: thousands-separated units and bankers' rounding would print 2 for 2.5 and 4 for 3.5
#: in the same message.
class OutstandingSOBlock(BaseModel):
    ordered_qty: int
    transferred_qty: int
    outstanding_qty: int
    so_count: int
    order_date_min: Optional[date] = None
    order_date_max: Optional[date] = None


class OutstandingDOBlock(BaseModel):
    """Two populations, one block (owner rulings R1 then R6, 13 Sep 2026).

    `do_qty` sums EVERY DO in scope and `delivered_qty` is the rest of it, so
    `do_qty == delivered_qty + pending_qty` holds. `pending_qty`, `do_count` and the
    date range cover DOs that are still outstanding only - "most of the DO are
    delivered right so what's outstanding?" - so the count answers how many DOs are
    still open while the quantities also state what has gone out.
    """

    do_qty: int
    delivered_qty: int
    pending_qty: int
    do_count: int
    do_date_min: Optional[date] = None
    do_date_max: Optional[date] = None


class OutstandingSOLocationRow(BaseModel):
    code: Optional[str] = None
    ordered_qty: int
    outstanding_qty: int


class OutstandingSOCustomerRow(BaseModel):
    customer_name: Optional[str] = None
    ordered_qty: int
    outstanding_qty: int


class OutstandingDOLocationRow(BaseModel):
    """`do_qty` over every DO at this location, `pending_qty` over the outstanding ones
    (R6). A location that only ever had delivered DOs still gets a row, `pending_qty` 0."""

    code: Optional[str] = None
    do_qty: int
    pending_qty: int


class OutstandingDOCustomerRow(BaseModel):
    """As `OutstandingDOLocationRow`, per customer."""

    customer_name: Optional[str] = None
    do_qty: int
    pending_qty: int


class OutstandingSOProductRow(BaseModel):
    """R13: the By product group, for a CUSTOMER-subject report - "when we ask for
    customer, the by customer section becomes by product section"."""

    product_code: Optional[str] = None
    ordered_qty: int
    outstanding_qty: int


class OutstandingDOProductRow(BaseModel):
    """As `OutstandingSOProductRow`, on the DO side."""

    product_code: Optional[str] = None
    do_qty: int
    pending_qty: int


class OutstandingSORow(BaseModel):
    so_number: str
    customer_name: Optional[str] = None
    product_code: Optional[str] = None
    location: Optional[str] = None
    ordered_qty: int
    transferred_qty: int
    outstanding_qty: int
    order_date: Optional[date] = None


class OutstandingDORow(BaseModel):
    """One PENDING delivery order (see `OutstandingDOBlock`).

    R3 (owner ruling, 13 Sep 2026): a ROW states all three quantities - `do_qty`, then
    `delivered_qty`, which is `0` here by construction and is PRINTED as 0 rather than
    omitted ("if it is 0 then we show 0, don't hide"), then `pending_qty`. The block and
    the breakdowns above the list stay pending-only.
    """

    do_number: str
    customer_name: Optional[str] = None
    product_code: Optional[str] = None
    location: Optional[str] = None
    do_qty: int
    delivered_qty: int
    pending_qty: int
    do_date: Optional[date] = None


class OutstandingReportResponse(BaseModel):
    """`GET /api/v1/order-management/outstanding-report`.

    `so` / `do` is `None` when that scope was not asked (AC-1117) - the ROUTE
    strips the key entirely rather than serializing a null, because the
    chatbot presenter (S1, `sorento_crm_mcp/presenters.py::_outstanding_report`)
    treats "the key is absent" as "not asked" and "present with count 0" as a
    real miss; those are different states and must stay distinguishable on
    the wire, not just in Python.

    Every field below is declared on purpose - a `response_model` silently
    drops any field the schema does not name (see LESSONS-LEARNT.md), so a
    field missing here would vanish from the body with no error anywhere.
    """

    # R13: the SUBJECT may be a customer alone, so the product echo is nullable.
    product_code: Optional[str] = None
    customer_name: Optional[str] = None
    # #1262 slice 9 (F1a): the brand(s) filtered by `brand_ids`, joined by ", " the same
    # way `customer_name` joins several ledgers - `None` when no brand was named, never
    # printed as "all" (the MCP presenter/chatbot header add the "Brand:" line only when
    # this is filled).
    brand_name: Optional[str] = None
    warehouse_codes: List[str] = []
    order_date_from: Optional[date] = None
    order_date_to: Optional[date] = None
    so: Optional[OutstandingSOBlock] = None
    do: Optional[OutstandingDOBlock] = None
    so_by_location: List[OutstandingSOLocationRow] = []
    # R13: which breakdown groups exist depends on the SUBJECT (product -> by_customer,
    # customer -> by_product, both -> neither). A group the subject does not want is
    # `None` here and the route strips the key, the same way it strips an unasked scope:
    # "nobody" and "not asked" are different answers.
    so_by_customer: Optional[List[OutstandingSOCustomerRow]] = None
    so_by_product: Optional[List[OutstandingSOProductRow]] = None
    do_by_location: List[OutstandingDOLocationRow] = []
    do_by_customer: Optional[List[OutstandingDOCustomerRow]] = None
    do_by_product: Optional[List[OutstandingDOProductRow]] = None
    so_rows: List[OutstandingSORow] = []
    do_rows: List[OutstandingDORow] = []


# ---------------------------------------------------------------------------
# sales report - delivered sales by DO date (lane SALES-REPORT, PR #1401;
# `documentation/plans/chatbot/selfref-scope-acceptance-criteria.md` AC-SR-20 to 26)
# ---------------------------------------------------------------------------
#: Money fields are `float`, never `Decimal`: a `Decimal` serialises through
#: `model_dump(mode="json")` as a STRING, and money is a JSON NUMBER, rounded to 2
#: places by the service before it reaches this schema. Quantities are `int`.
class SalesReportFigures(BaseModel):
    qty: int
    amount: float


class SalesReportPeriod(BaseModel):
    """One day, week (Monday to Sunday) or month with a delivery, clipped to the window."""

    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    qty: int
    amount: float


class SalesReportRow(BaseModel):
    """One ranked drill row. `date` only on a delivery order row; `customer_name` only on a
    delivery order row when the scope holds more than one account."""

    rank: int
    name: Optional[str] = None
    qty: int
    amount: float
    date: Optional[_date] = None
    customer_name: Optional[str] = None


class SalesReportOption(BaseModel):
    """A drill-down the reply offers (`key` is the `group_by` it re-runs with)."""

    key: str
    label: str


class SalesReportResponse(BaseModel):
    """`GET /api/v1/order-management/sales-report` (AC-SR-23).

    Every field is declared on purpose - `response_model` silently drops any field the
    schema does not name (LESSONS-LEARNT.md).
    """

    status: str
    message: Optional[str] = None
    basis: str
    customer_name: Optional[str] = None
    customer_count: int
    product_code: Optional[str] = None
    #: Every code the `product_code` prefix covers, sorted. `[]` without a product filter.
    product_codes: List[str] = []
    channel: Optional[str] = None
    channel_shown: bool
    #: Echo only: the location word the caller resolved into `warehouse_codes`.
    location_token: Optional[str] = None
    warehouse_codes: List[str] = []
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    grain: str
    total: SalesReportFigures
    periods: List[SalesReportPeriod] = []
    group_by: Optional[str] = None
    rows: List[SalesReportRow] = []
    more: int = 0
    options: List[SalesReportOption] = []



class TopSellingRow(BaseModel):
    """One ranked item (`group=item`: product code) or category (`group=category`:
    category code). Rows arrive ranked; the presenter prints them in order.
    Owner ruling 26 Sep ~07:40Z: no `name` field, code alone is enough."""

    rank: int
    code: Optional[str] = None
    quantity: int
    amount: float


class TopSellingTotals(BaseModel):
    """Sums over EVERY ranked row, not just the ones `n` kept."""

    quantity: int
    amount: float


class TopSellingFilters(BaseModel):
    """The applied filters, echoed for the reply header. `dealer_scoped` is True
    when the caller is a dealer contact forced to its own customers."""

    customer_name: Optional[str] = None
    category_name: Optional[str] = None
    # Fix lane round 4 (owner retest, 27 Sep 2026): the brand filter's names.
    brand_name: Optional[str] = None
    sales_agent: Optional[str] = None
    channel: Optional[str] = None
    dealer_scoped: bool = False


class TopSellingDetailCustomer(BaseModel):
    customer_name: Optional[str] = None
    quantity: int
    amount: float


class TopSellingDetailMonth(BaseModel):
    month: str
    quantity: int
    amount: float


class TopSellingDetail(BaseModel):
    """The detail offer's answer (AC-1935): one code's customers and months under
    the same filters and basis, each sorted by the ranking metric desc."""

    code: Optional[str] = None
    name: Optional[str] = None
    by_customer: List[TopSellingDetailCustomer] = []
    by_month: List[TopSellingDetailMonth] = []


class TopSellingResponse(BaseModel):
    """`GET /api/v1/order-management/top-selling` (PLAN-chatbot-top-x-hot-selling-24sep,
    S2, owner rulings of 26 Sep 2026). No paging: `n` absent returns every ranked
    row and `total_count` always states the full count. `date_from` / `date_to`
    are the RESOLVED window (the current calendar year when none was given).
    `sales_agent_fill_rate` (0 to 1) is set only when a sales agent filter was
    used. `detail` is set only when `detail_code` named a code with sales; `rows`
    and `totals` then cover that one code. Every field is declared -
    `response_model` drops undeclared ones."""

    rank_by: str
    basis: str
    group: str
    # `top` (most sold first) or `bottom` (least sold first), fix lane round 4.
    direction: str = "top"
    n: Optional[int] = None
    date_from: date
    date_to: date
    filters: TopSellingFilters
    total_count: int
    rows: List[TopSellingRow] = []
    totals: TopSellingTotals
    sales_agent_fill_rate: Optional[float] = None
    detail: Optional[TopSellingDetail] = None
