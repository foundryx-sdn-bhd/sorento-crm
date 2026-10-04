"""The rows behind the chatbot's sales report: every delivered DO line (lane SALES-REPORT,
PR #1401; `documentation/plans/chatbot/PLAN-chatbot-selfref-scope-30sep.md`, "Design note:
one dimension/measure model"; UAC AC-SR-20 to AC-SR-22).

One row per `order_lines` row of a delivery order (`orders`) that is not cancelled, not
deleted, dated, and not a replacement DO (number `REP...`). Filed by the DO's own date
(`orders.order_date`).

**The amount is computed once, per line, in a base subquery**, so every grouping (customer,
product, period, DO, sales agent) sums to the same Total to the sen:

* an AutoCount DO (`source_book` set, `db1`) carries its own line totals: a line's amount is
  its own `total`;
* a legacy import (`source_book` NULL) repeats the DOC total on every line: the DO counts it
  ONCE, split across its lines by weight qty x unit price x (1 - discount), in equal shares
  when every weight is 0. The split is rounded by cumulative rounding (each line takes the
  rounded running total minus the rounded running total before it), so the shares sum to
  the DOC total exactly, the last sen included;
* a legacy DO whose lines carry MORE THAN ONE distinct total is not a repeated DOC total
  (owner ruling, PR #1401 fix round 1; rule a2, fix round 2): each line is the LESSER of its
  own qty x unit price x (1 - discount), rounded to the sen, and its stored total (none
  stored = the line math). The discount is normalised first (`discount_fraction`).

The DO-level filters a run names (its customers, its window, its company grant) are pushed
into that subquery as well: a share is a fraction of its whole DO, so only a filter that
keeps or drops a WHOLE DO may go there. Product and location filters (per line) stay outside.

Not registered on the Reports screen (`reg.register`): the chatbot's sales report runs this
definition directly (`sales_report_service.delivered_sales_report`). Trigger for the screen:
the owner wants it there.
"""
from __future__ import annotations

from typing import Any, List, Optional

import sqlalchemy as sa

from app.models.inventory import Warehouse
from app.models.order import Customer, Order, OrderLine, SalesOrder
from app.models.product import Product
from app.models.sales_agent import SalesAgent
from app.services.reports import registry as reg
from app.services.scm.demand_class import PROJECT_SEGMENTS

#: A DO with no sales order, or a sales order with no agent, groups here.
NO_AGENT = "(no agent)"

#: The two channel values the `channel` dimension and filter speak (`demand_class.class_of`).
CHANNELS = ("project", "retail")

_SEGMENT = sa.func.lower(sa.func.trim(Customer.market_segment_code))

#: `demand_class.class_of`'s rule, in SQL, over its own `PROJECT_SEGMENTS` words: a segment
#: naming project work is Project, any other stated segment is Retail, none is NULL.
CHANNEL_EXPR = sa.case(
    (sa.or_(*(_SEGMENT.like(f"%{word}%") for word in sorted(PROJECT_SEGMENTS))), sa.literal("project")),
    (sa.func.coalesce(_SEGMENT, "") != "", sa.literal("retail")),
    else_=sa.null(),
)


def _fixed_filters() -> List[Any]:
    """The predicates that define which DO lines exist at all (AC-SR-20)."""
    return [
        Order.is_cancelled.is_(False),
        Order.deleted_at.is_(None),
        Order.order_date.isnot(None),
        ~Order.order_number.like("REP%"),
    ]


def _do_level_filters(ctx: Any) -> List[Any]:
    """The run's filters that keep or drop a WHOLE DO, safe to apply before the split."""
    preds: List[Any] = [
        Order.order_date >= ctx.period.start,
        Order.order_date < ctx.period.end_exclusive,
    ]
    customers = ctx.values.get("customer") or []
    if customers:
        preds.append(Order.customer_id.in_(customers))
    grants = ctx.company_grants
    if grants is None:
        pass
    elif isinstance(grants, frozenset) and grants:
        preds.append(Order.company_id.in_(sorted(grants)))
    else:
        preds.append(sa.false())
    return preds


def discount_fraction() -> Any:
    """A line's discount as a fraction 0..1. The column is mostly a fraction, but some rows
    store a percent (real data: three lines carry 100.0000), so a value above 1 is read as a
    percent (100 -> 1.0, 37 -> 0.37), then clamped to 0..1."""
    raw = sa.func.coalesce(OrderLine.discount, 0)
    fraction = sa.case((raw > 1, raw / 100), else_=raw)
    return sa.func.least(sa.func.greatest(fraction, 0), 1)


def _line_amounts(ctx: Any) -> Any:
    """Each DO line's amount, as a subquery keyed by the line id. Built once per run (the
    base and the `amount` measure must read the SAME subquery object)."""
    cached = ctx.__dict__.get("_delivery_order_line_amounts")
    if cached is not None:
        return cached

    partition = (OrderLine.order_id,)
    weight = (
        sa.func.coalesce(OrderLine.quantity, 0)
        * sa.func.coalesce(OrderLine.unit_price, 0)
        * (1 - discount_fraction())
    )
    lines = (
        sa.select(
            OrderLine.id.label("line_id"),
            OrderLine.order_id.label("order_id"),
            OrderLine.line_sequence.label("line_sequence"),
            Order.source_book.label("source_book"),
            sa.func.coalesce(OrderLine.total, 0).label("line_total"),
            OrderLine.total.label("stored_total"),
            weight.label("weight"),
            sa.func.coalesce(sa.func.max(OrderLine.total).over(partition_by=partition), 0).label("doc_total"),
            # Postgres has no COUNT(DISTINCT) window: min <> max is "more than one total".
            (
                sa.func.min(OrderLine.total).over(partition_by=partition)
                != sa.func.max(OrderLine.total).over(partition_by=partition)
            ).label("mixed_totals"),
            sa.func.sum(weight).over(partition_by=partition).label("weight_sum"),
            sa.func.count().over(partition_by=partition).label("line_count"),
        )
        .select_from(OrderLine)
        .join(Order, Order.id == OrderLine.order_id)
        .where(sa.and_(*_fixed_filters(), *_do_level_filters(ctx)))
        .subquery("do_lines")
    )
    share = sa.case(
        (lines.c.weight_sum > 0, lines.c.doc_total * lines.c.weight / lines.c.weight_sum),
        else_=lines.c.doc_total / lines.c.line_count,
    )
    running = sa.func.sum(share).over(
        partition_by=lines.c.order_id,
        order_by=(lines.c.line_sequence, lines.c.line_id),
        rows=(None, 0),
    )
    amount = sa.case(
        (lines.c.source_book.isnot(None), sa.func.round(lines.c.line_total, 2)),
        # A legacy DO whose lines carry more than one distinct total is not one repeated DOC
        # total (owner ruling, fix round 1 S1; rule a2, fix round 2): each line is the lesser
        # of its own qty x price x (1 - discount), rounded to the sen, and its stored total
        # (none stored = the line math). Nothing is split.
        (
            lines.c.mixed_totals.is_(True),
            sa.func.least(
                sa.func.round(lines.c.weight, 2),
                sa.func.coalesce(sa.func.round(lines.c.stored_total, 2), sa.func.round(lines.c.weight, 2)),
            ),
        ),
        else_=sa.func.round(running, 2) - sa.func.round(running - share, 2),
    )
    subquery = sa.select(lines.c.line_id, amount.label("amount")).subquery("do_line_amounts")
    ctx.__dict__["_delivery_order_line_amounts"] = subquery
    return subquery


def _base(ctx: Any) -> sa.Select:
    amounts = _line_amounts(ctx)
    return (
        sa.select()
        .select_from(OrderLine)
        .join(Order, Order.id == OrderLine.order_id)
        .join(amounts, amounts.c.line_id == OrderLine.id)
        .outerjoin(Customer, Customer.id == Order.customer_id)
        .outerjoin(Product, Product.id == OrderLine.product_id)
        .outerjoin(Warehouse, Warehouse.id == OrderLine.warehouse_id)
        .outerjoin(SalesOrder, SalesOrder.id == Order.sales_order_id)
        .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
        .where(sa.and_(*_fixed_filters()))
    )


# ------------------------------------------------------------------------ filters


def customer_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return Order.customer_id.in_(values)


def product_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return OrderLine.product_id.in_(values)


def location_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    return OrderLine.warehouse_id.in_(values)


def channel_condition(ctx: Any, values: List[str]) -> Optional[Any]:
    """An account with no segment is in every channel, so a channel filter keeps it."""
    return sa.or_(CHANNEL_EXPR.in_(values), CHANNEL_EXPR.is_(None))


# ------------------------------------------------------------------------ catalog


def _text_date(expr: Any) -> Any:
    return sa.func.to_char(expr, "YYYY-MM-DD")


COLUMNS = (
    reg.Column("customer", "Customer", "text", "dimension", lambda c: Customer.customer_name, size=220),
    reg.Column("product", "Product", "text", "dimension", lambda c: Product.product_code, size=160),
    reg.Column(
        "sales_agent",
        "Sales agent",
        "text",
        "dimension",
        lambda c: sa.func.coalesce(SalesAgent.sales_agent, NO_AGENT),
        size=140,
    ),
    reg.Column("location", "Location", "text", "dimension", lambda c: Warehouse.warehouse_code, size=120),
    reg.Column("channel", "Channel", "text", "dimension", lambda c: CHANNEL_EXPR, size=120),
    reg.Column("day", "Day", "text", "dimension", lambda c: _text_date(Order.order_date), size=110),
    reg.Column(
        "week",
        "Week",
        "text",
        "dimension",
        lambda c: _text_date(sa.func.date_trunc("week", Order.order_date)),
        size=110,
    ),
    reg.Column("month", "Month", "text", "dimension", lambda c: sa.func.to_char(Order.order_date, "YYYY-MM"), size=110),
    reg.Column("delivery_order", "Delivery order", "text", "dimension", lambda c: Order.order_number, size=140),
    # The one-column side of a one-dimension view. An expression, not a bare constant:
    # Postgres refuses a constant in GROUP BY.
    reg.Column("all", "All", "text", "dimension", lambda c: sa.cast(sa.literal("all"), sa.Text), size=80),
    reg.Column("amount", "RM", "money", "measure", lambda c: _line_amounts(c).c.amount, size=130),
    reg.Column("qty", "Qty", "integer", "measure", lambda c: OrderLine.quantity, size=100),
)

DATASET = reg.Dataset(
    key="delivery_order_lines",
    scope="company",
    columns=COLUMNS,
    date_bases=(reg.DateBasis("order_date", "DO date", Order.order_date),),
    base=_base,
    company_column=Order.company_id,
    product_id_column=OrderLine.product_id,
)


def _no_options(db: Any) -> List[Any]:
    return []


#: The definition the chatbot's sales report runs (never registered on the Reports screen).
DEFINITION = reg.ReportDefinition(
    key="delivery_order_lines",
    title="Delivered sales",
    permission="order_management.orders.view",
    dataset=DATASET,
    params=(
        reg.DateBasisParam(key="date_basis", label="Date basis", default="order_date"),
        reg.PeriodParam(key="period", label="Period", default=reg.current_year_period),
        reg.SelectParam("customer", "Customer", True, (), _no_options, customer_condition),
        reg.SelectParam("product", "Product", True, (), _no_options, product_condition),
        reg.SelectParam("location", "Location", True, (), _no_options, location_condition),
        reg.SelectParam("channel", "Channel", True, (), _no_options, channel_condition),
    ),
    detail=reg.DetailLayout(
        title="Delivery order lines",
        order_by=lambda c: [Order.order_date.desc(), Order.order_number.desc(), OrderLine.line_sequence.asc()],
    ),
    pivot=reg.PivotLayout(title="Delivered sales"),
    default_view={
        "params": {"date_basis": "order_date"},
        "detail": {"columns": [], "order": []},
        "pivot": {"rows": "month", "cols": "all", "measures": ["qty", "amount"]},
    },
    workbook=reg.WorkbookSpec(company_name=""),
    module_key="order_management",
)

reg.validate(DEFINITION)
