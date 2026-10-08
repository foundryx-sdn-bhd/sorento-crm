"""Every basis of the chatbot's report ask (lane REPORT-ENGINE; owner direction 4 Oct 2026).

Sales order lines joined to their SO, customer, sales agent and product, filtered by top
selling's own `sales_report_service._common_filters` (a cancelled order or line is out, and a
line with neither a required date nor an SO order date is out, exactly as top selling has it),
with the product's brand and category and the line's warehouse joined on, each line dated by
top selling's own `sales_report_service._bucket_expr` (the line's required date, else its SO's
order date). One definition per basis (`delivered`, `ordered`,
`outstanding`); the qty and amount are imported from `sales_report_service`, never rewritten.

Registered NOWHERE (no `reg.register`): the Reports screen's Yearly comparison catalogue does
not change. `ask.run_ask` runs these definitions directly.
"""
from __future__ import annotations

from typing import Any, List, Optional

import sqlalchemy as sa

from app.models.inventory import Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Brand, Product, ProductCategory
from app.models.sales_agent import SalesAgent
from app.services.reports import registry as reg
from app.services.reports.datasets.delivery_order_lines import NO_AGENT
from app.services.reports.datasets.sales_order_lines import channel_label
from app.services.sales_report_service import _basis_figures, _bucket_expr, _common_filters, _per_line_exprs


def _base(ctx: Any) -> sa.Select:
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
                    bucket_expr=_bucket_expr(),
                )
            )
        )
        .outerjoin(Brand, Brand.id == Product.brand_id)
        .outerjoin(ProductCategory, ProductCategory.id == Product.category_id)
        .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
    )


# ------------------------------------------------------------------------ filters


def _no_options(db: Any) -> List[Any]:
    return []


def customer_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return SalesOrder.customer_id.in_(values)


def product_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return SalesOrderLine.product_id.in_(values)


def brand_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return Product.brand_id.in_(values)


def category_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return Product.category_id.in_(values)


#: The key of the "(no agent)" row (REPORT-DRILLDOWN): an agent filter value meaning "no agent".
NO_AGENT_KEY = "none"


def _agent_in(column: Any, values: List[str], *, owner: Any = None) -> Any:
    """`column` in the agent ids, or NULL when `NO_AGENT_KEY` is one of the values. `owner`:
    the row the column sits on must exist (an outer-joined customer), so "no agent" never
    matches a line with no customer at all."""
    ids = [v for v in values if v != NO_AGENT_KEY]
    parts = [column.in_(ids)] if ids else []
    if NO_AGENT_KEY in values:
        parts.append(column.is_(None) if owner is None else sa.and_(owner.isnot(None), column.is_(None)))
    return sa.or_(*parts)


def sales_agent_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return _agent_in(SalesOrder.sales_agent_id, values)


def customer_agent_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    """The customer CARD link (REPORT-DRILLDOWN, owner 8 Oct 2026): lines of customers whose
    assigned agent (`customers.sales_agent_id`) is one of the values, whoever sold the order."""
    return _agent_in(Customer.sales_agent_id, values, owner=Customer.id)


def location_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return SalesOrderLine.warehouse_id.in_(values)


def channel_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    """Values are demand classes (`retail` / `project`), as `SalesOrder.demand_class` holds them."""
    return SalesOrder.demand_class.in_(values)


# ------------------------------------------------------------------------ catalog

#: Basis -> definition title. The qty and amount expressions come from `_figures`, top selling's own.
_TITLES = {"delivered": "Delivered sales", "ordered": "Ordered sales", "outstanding": "Outstanding orders"}


def _figures(basis: str) -> tuple:
    if basis == "outstanding":
        exprs = _per_line_exprs()
        return exprs["outstanding_qty"], exprs["outstanding_value"]
    return _basis_figures(basis)


def _definition(basis: str) -> reg.ReportDefinition:
    qty_expr, amount_expr = _figures(basis)
    key = f"sales_order_lines_ask_{basis}"
    columns = (
        reg.Column("customer", "Customer", "text", "dimension", lambda c: Customer.customer_name, size=220),
        reg.Column("product", "Product", "text", "dimension", lambda c: Product.product_code, size=160),
        reg.Column("brand", "Brand", "text", "dimension", lambda c: Brand.brand_name, size=160),
        reg.Column("category", "Category", "text", "dimension", lambda c: ProductCategory.category_name, size=160),
        reg.Column(
            "sales_agent",
            "Sales agent",
            "text",
            "dimension",
            lambda c: sa.func.coalesce(SalesAgent.sales_agent, NO_AGENT),
            size=140,
        ),
        reg.Column("location", "Location", "text", "dimension", lambda c: Warehouse.warehouse_code, size=120),
        reg.Column(
            "channel", "Channel", "text", "dimension", lambda c: channel_label(SalesOrder.demand_class), size=120
        ),
        reg.Column("month", "Month", "text", "dimension", lambda c: sa.func.to_char(_bucket_expr(), "YYYY-MM"), size=110),
        reg.Column("all", "All", "text", "dimension", lambda c: sa.cast(sa.literal("all"), sa.Text), size=80),
        reg.Column("amount", "RM", "money", "measure", lambda c: amount_expr, size=130),
        reg.Column("qty", "Qty", "integer", "measure", lambda c: qty_expr, size=100),
    )
    dataset = reg.Dataset(
        key=key,
        scope="company",
        columns=columns,
        date_bases=(reg.DateBasis("order_date", "Line date", _bucket_expr()),),
        base=_base,
        company_column=SalesOrder.company_id,
        product_id_column=SalesOrderLine.product_id,
    )
    definition = reg.ReportDefinition(
        key=key,
        title=_TITLES[basis],
        permission="order_management.orders.view",
        dataset=dataset,
        params=(
            reg.DateBasisParam(key="date_basis", label="Date basis", default="order_date"),
            reg.PeriodParam(key="period", label="Period", default=reg.current_year_period),
            reg.SelectParam("customer", "Customer", True, (), _no_options, customer_condition),
            reg.SelectParam("product", "Product", True, (), _no_options, product_condition),
            reg.SelectParam("brand", "Brand", True, (), _no_options, brand_condition),
            reg.SelectParam("category", "Category", True, (), _no_options, category_condition),
            reg.SelectParam("sales_agent", "Sales agent", True, (), _no_options, sales_agent_condition),
            reg.SelectParam("customer_agent", "Customers assigned to", True, (), _no_options, customer_agent_condition),
            reg.SelectParam("location", "Location", True, (), _no_options, location_condition),
            reg.SelectParam("channel", "Channel", True, (), _no_options, channel_condition),
        ),
        detail=reg.DetailLayout(
            title="Sales order lines",
            order_by=lambda c: [SalesOrder.order_date.desc(), SalesOrder.so_number.asc(), SalesOrderLine.id.asc()],
        ),
        pivot=reg.PivotLayout(title=_TITLES[basis]),
        default_view={
            "params": {"date_basis": "order_date"},
            "detail": {"columns": [], "order": []},
            "pivot": {"rows": "month", "cols": "all", "measures": ["qty", "amount"]},
        },
        workbook=reg.WorkbookSpec(company_name=""),
        module_key="order_management",
    )
    reg.validate(definition)
    return definition


DEFINITIONS = {basis: _definition(basis) for basis in _TITLES}
