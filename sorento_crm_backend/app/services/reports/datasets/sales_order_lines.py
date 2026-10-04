"""The rows behind the sales reports: every live sales order line (PLAN-retail-sales-reports-26sep
5.1).

**The money is the sales report's own, imported, never copied.** `_per_line_exprs` is the
per-line ordered and confirmed (transferred to DO) value, rounded to the sen per line, and
`_common_filters` drops a cancelled order and a cancelled line. So the Yearly comparison,
the chatbot and `GET /order-management/sales-report` cannot disagree about a line's value.

**Filed by the order's own date** (`sales_orders.order_date`, G1), not by the line's
required date the sales report files a delivery under: a March line on a February order is
February's sale.

**Company scope is "company", and the company is a filter.** A sales order belongs to one
company; the report reads ONE company at a time (the Company filter), inside the caller's
grant, and the engine's company arm is fail-closed (no grant, no rows). Every expression
here uses the mapped classes, so the engine runs them with the ORM listener's own scope
off and applies the company itself (`engine._unscoped`), which is what lets a user granted
Sorento and Mocha read Mocha from a Sorento session.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

import sqlalchemy as sa

from app.models.company import Company
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product
from app.models.sales_agent import SalesAgent
from app.services.error_handler import AppException
from app.services.reports import registry as reg
from app.services.reports.engine import month_label
from app.services.sales_report_service import _common_filters, _per_line_exprs

MONTH_OF_YEAR = reg.MONTHS_OF_YEAR

#: The Channel filter's values and the words each reads as (G4: `demand_class`).
CHANNELS: Tuple[Tuple[str, str], ...] = (("dealer", "Dealer"), ("project", "Project team"))
_DEMAND_CLASS = {"dealer": "retail", "project": "project"}

#: The Basis filter (G1): Delivered is the default, and the basis prints on every header.
#: Invoiced (finance S1) reads billing documents, `datasets/billing_documents.py`.
BASES: Tuple[Tuple[str, str], ...] = (
    ("delivered", "Delivered"),
    ("ordered", "Ordered"),
    ("invoiced", "Invoiced"),
)
BASIS_WORDS = {
    "delivered": "Delivered (transferred to DO)",
    "ordered": "Ordered",
    "invoiced": "Invoiced (invoices, cash sales and debit notes less credit notes, excluding tax)",
}


def channel_label(demand_class: Any) -> Any:
    """The Channel dimension over a demand class column: `retail` reads Dealer, `project`
    reads Project team, anything else is the pivot's `(blank)`. One copy, for the order
    bases and the invoiced one (ruling Q14: both group by the sales order type)."""
    return sa.case(
        (demand_class == "retail", sa.literal("Dealer")),
        (demand_class == "project", sa.literal("Project team")),
        else_=sa.null(),
    )


def channel_filter(demand_class: Any, values: List[str]) -> Any:
    """The Channel filter over a demand class column. A NULL class is in no channel, so
    with both ticked an unclassified row is in neither block (the cleared filter has it)."""
    unknown = [v for v in values if v not in _DEMAND_CLASS]
    if unknown:
        raise AppException(status_code=422, message=f"Unknown channel '{unknown[0]}'",
                           code="REPORT_INVALID_PARAMS")
    return demand_class.in_([_DEMAND_CLASS[v] for v in values])


_CHANNEL_LABEL = channel_label(SalesOrder.demand_class)


def _exprs() -> dict:
    return _per_line_exprs()


def _basis(ctx) -> str:
    chosen = (ctx.values.get("basis") or ["delivered"])[0]
    return chosen


def _sales_value(ctx) -> Any:
    exprs = _exprs()
    return exprs["ordered_value"] if _basis(ctx) == "ordered" else exprs["confirmed_value"]


def _base(ctx) -> sa.Select:
    return (
        sa.select()
        .select_from(SalesOrderLine)
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
        .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
        .outerjoin(Product, Product.id == SalesOrderLine.product_id)
        .where(
            sa.and_(
                *_common_filters(
                    product_ids=None,
                    customer_query=None,
                    customer_ids=None,
                    channel=None,
                    warehouse_ids=None,
                    date_from=None,
                    date_to=None,
                    bucket_expr=SalesOrder.order_date,
                )
            )
        )
    )


def years(db) -> List[int]:
    """The years the period picker offers: any company's order years plus the last three.
    Metadata only (which years exist), never a figure, so it is read across companies."""
    stmt = sa.select(sa.distinct(sa.extract("year", SalesOrder.order_date))).where(
        SalesOrder.order_date.isnot(None)
    )
    from app.models.base import company_scope

    with company_scope(db, None):
        found = {int(row[0]) for row in db.execute(stmt) if row[0] is not None}
    this_year = reg.today_malaysia().year
    found.update(range(this_year - 2, this_year + 1))
    return sorted(found, reverse=True)


def company_options(db) -> Sequence[Tuple[str, str]]:
    """Every active company, by name. The ROUTE narrows this to the caller's grant."""
    rows = db.query(Company.id, Company.name).filter(Company.is_active.is_(True)).all()
    return sorted(((str(i), str(n)) for i, n in rows), key=lambda r: (r[0] != _SORENTO, r[1]))


_SORENTO = "00000000-0000-0000-0000-000000000001"


def company_condition(ctx, values: List[str]) -> Optional[Any]:
    """The run's dataset's own company column: the invoiced basis reads another table."""
    return ctx.dataset.company_column == values[0]


def channel_condition(ctx, values: List[str]) -> Optional[Any]:
    return channel_filter(SalesOrder.demand_class, values)


def basis_condition(ctx, values: List[str]) -> Optional[Any]:
    """The basis picks the MEASURE, not the rows: it filters nothing. It is still checked,
    because an unknown basis would otherwise read silently as Delivered."""
    if len(values) != 1 or values[0] not in BASIS_WORDS:
        raise AppException(status_code=422, message="Basis is Ordered, Delivered or Invoiced",
                           code="REPORT_INVALID_PARAMS")
    return None


def note(ctx) -> str:
    """The basis line on every header (G1; finance S1 UAC S1-5 for Invoiced)."""
    basis = _basis(ctx)
    if basis == "invoiced":
        return f"Basis: {BASIS_WORDS[basis]}, by document date, grouped by the sales order type."
    return f"Basis: {BASIS_WORDS[basis]}, by sales order date. Sales orders, not invoices."


COLUMNS: Tuple[reg.Column, ...] = (
    reg.Column("so_number", "SO no", "text", "text", lambda c: SalesOrder.so_number, size=130),
    reg.Column("order_date", "Order date", "date", "date", lambda c: SalesOrder.order_date, size=120),
    reg.Column("customer", "Customer", "text", "text", lambda c: Customer.customer_name, size=220),
    reg.Column("agent_code", "Sales agent code", "text", "text", lambda c: SalesAgent.sales_agent, size=140),
    reg.Column("product_code", "Product", "text", "text", lambda c: Product.product_code, size=160),
    reg.Column("channel", "Channel", "text", "dimension", lambda c: _CHANNEL_LABEL, size=120),
    reg.Column(
        "year",
        "Year",
        "text",
        "dimension",
        lambda c: sa.cast(sa.cast(sa.extract("year", SalesOrder.order_date), sa.Integer), sa.Text),
        size=80,
        period_years=True,
    ),
    reg.Column(
        "month_of_year",
        "Month",
        "text",
        "dimension",
        lambda c: sa.func.to_char(SalesOrder.order_date, "MM"),
        size=80,
        fixed_values=MONTH_OF_YEAR,
    ),
    reg.Column(
        "year_month",
        "Month of the period",
        "text",
        "dimension",
        lambda c: sa.func.to_char(sa.func.date_trunc("month", c.date_basis), "YYYY-MM"),
        size=110,
        period_months=True,
        value_label=month_label,
    ),
    reg.Column("sales_value", "RM", "money", "measure", _sales_value, size=130),
    reg.Column("ordered_value", "Ordered RM", "money", "measure",
               lambda c: _exprs()["ordered_value"], size=130),
    reg.Column("delivered_value", "Delivered RM", "money", "measure",
               lambda c: _exprs()["confirmed_value"], size=130),
)

DATASET = reg.Dataset(
    key="sales_order_lines",
    scope="company",
    columns=COLUMNS,
    date_bases=(reg.DateBasis("order_date", "Order date", SalesOrder.order_date),),
    base=_base,
    company_column=SalesOrder.company_id,
    company_param="company",
    years=years,
    product_id_column=SalesOrderLine.product_id,
)


def order_by(ctx) -> Sequence[Any]:
    """Newest order first: a truncated detail keeps the most recent lines."""
    return [SalesOrder.order_date.desc(), SalesOrder.so_number.asc(), SalesOrderLine.id.asc()]
