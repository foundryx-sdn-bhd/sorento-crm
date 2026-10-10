"""The chat bases of the chatbot's report ask (chat analytics CA-3).

Two bases over `chatbot.chat_ask_facts`, both run directly by `ask.run_ask` and registered
NOWHERE (the Reports screen's catalogue does not change):

- `asks` counts every ask; `turns` counts each message once (`chat_asks.turn_only`, ask 1).
- Ranking by product reads the item grain instead (`datasets_by` on the `grain` param): one
  row per product an ask NAMED, so a ranking counts asks that named it, never products shown.

The measures are the chat screens' own (`asks`, `failed`) plus the two CA-3 kinds: `contacts`
(count-distinct `contact_respond_id`) and `failure_rate` (failed / asks, a ratio recomputed from
the summed parts in every row and total). Company scope is `none` for the reason
`chat_asks` gives; the route scopes a dealer or salesman by customer instead.
"""
from __future__ import annotations

import dataclasses
from typing import Any, List, Tuple

import sqlalchemy as sa

from app.models.access import RespondContact
from app.models.product import Brand, ProductCategory
from app.models.sales_agent import SalesAgent
from app.services import contact_asker
from app.services.reports import registry as reg
from app.services.reports.datasets import chat_ask_items as items_ds
from app.services.reports.datasets import chat_asks as asks_ds

ASKS = asks_ds.ASKS
_ITEMS = items_ds._ITEMS

PERMISSION = "chatbot.analytics.view"
MODULE_KEY = "chatbot"

_ONE = sa.literal_column("1")

_PRODUCTS = items_ds._PRODUCTS
_CATEGORIES = ProductCategory.__table__
_BRANDS = Brand.__table__
_CONTACTS = RespondContact.__table__
_AGENTS = SalesAgent.__table__

#: Who asks (`contact_asker`, the one rule), as the words a reader meets. Every asker class is in a
#: run by default (owner override 10 Oct); removing a word here is the one switch that leaves a class
#: out, and the reply then says how many unclassified contacts were left out (UAC-29). Read
#: at run time (`ask._run_chat`), so a change takes effect without a restart of the definitions.
DEFAULT_ASKERS: Tuple[str, ...] = ("dealer", "salesperson", "staff", "unclassified")
ASKER_LABELS: Tuple[Tuple[str, str], ...] = (
    ("dealer", "Dealer"), ("salesperson", "Salesperson"), ("staff", "Staff"), ("unclassified", "Unclassified"),
)
_ASKER_WORD = contact_asker.sql_expr(ASKS.c.contact_respond_id)
_ASKER = asks_ds._words(_ASKER_WORD, ASKER_LABELS)
#: A customer as `name US id`: two customers sharing a name stay two rows (and each its own agent).
CUSTOMER_SEP = "\x1f"
_CUSTOMER_REF = sa.case(
    (
        ASKS.c.customer_id.isnot(None),
        sa.func.coalesce(asks_ds._CUSTOMER, sa.literal("")) + sa.literal(CUSTOMER_SEP) + sa.cast(ASKS.c.customer_id, sa.Text),
    ),
    else_=sa.null(),
)
#: The asking salesperson's name, only for a contact the rule reads as one: the role user's own name,
#: else the linked sales agent's (`sales_agents`) for a contact with no active user.
_SALESPERSON = sa.case(
    (
        _ASKER_WORD == "salesperson",
        sa.func.coalesce(
            contact_asker.salesperson_user_name(ASKS.c.contact_respond_id),
            sa.select(_AGENTS.c.sales_agent)
            .select_from(_AGENTS.join(_CONTACTS, _CONTACTS.c.id == _AGENTS.c.contact_id))
            .where(_CONTACTS.c.respond_io_id == ASKS.c.contact_respond_id)
            .order_by(_AGENTS.c.sales_agent)
            .limit(1)
            .correlate(ASKS)
            .scalar_subquery(),
        ),
    ),
    else_=sa.null(),
)


def _day(ctx) -> Any:
    return sa.func.to_char(sa.func.date_trunc("day", ctx.date_basis), "YYYY-MM-DD")


#: The report-only columns: the day bucket, the single-total axis, the asker axes and the two
#: new measures.
_EXTRA: Tuple[reg.Column, ...] = (
    reg.Column("customer_ref", "Customer", "text", "dimension", lambda c: _CUSTOMER_REF, size=200),
    reg.Column("asker", "Asked by", "text", "dimension", lambda c: _ASKER, size=110),
    reg.Column("salesperson", "Salesperson", "text", "dimension", lambda c: _SALESPERSON, size=140),
    reg.Column("day", "Day", "text", "dimension", _day, size=100),
    reg.Column("all", "All", "text", "dimension", lambda c: sa.cast(sa.literal("all"), sa.Text), size=80),
    reg.Column(
        "contacts", "Contacts", "integer", "measure", lambda c: ASKS.c.contact_respond_id, size=90,
        agg="count_distinct",
    ),
    reg.Column(
        "failure_rate", "Failure rate", "integer", "measure", lambda c: _ONE, size=90, ratio_of=("failed", "asks")
    ),
)


def _with_extra(columns: Tuple[reg.Column, ...]) -> Tuple[reg.Column, ...]:
    have = {c.key for c in columns}
    return columns + tuple(c for c in _EXTRA if c.key not in have)


ASKS_DATASET = dataclasses.replace(asks_ds.DATASET, key="chat_report_asks", columns=_with_extra(asks_ds.COLUMNS))

_failure_reason = next(c for c in asks_ds.COLUMNS if c.key == "failure_reason")
_ITEMS_FROM = (
    items_ds._FROM.outerjoin(_CATEGORIES, _CATEGORIES.c.id == _PRODUCTS.c.category_id)
    .outerjoin(_BRANDS, _BRANDS.c.id == _PRODUCTS.c.brand_id)
)
_ITEM_AXES: Tuple[reg.Column, ...] = (
    reg.Column("category", "Category", "text", "dimension", lambda c: _CATEGORIES.c.category_name, size=160),
    reg.Column("brand", "Brand", "text", "dimension", lambda c: _BRANDS.c.brand_name, size=140),
)
# On the item grain an ask naming two products of one category would count twice: the report's
# `asks` and `failed` count DISTINCT asks (a failed ask once), so a bucket is a number of asks.
_DISTINCT_ASKS = (
    reg.Column("asks", "Asks", "integer", "measure", lambda c: ASKS.c.id, size=80, agg="count_distinct"),
    reg.Column(
        "failed", "Failed", "integer", "measure",
        lambda c: sa.case((ASKS.c.failure_reason.isnot(None), ASKS.c.id), else_=sa.null()), size=80,
        agg="count_distinct",
    ),
)
_ITEM_COLUMNS = tuple(c for c in items_ds.COLUMNS if c.key not in ("asks", "failed")) + _DISTINCT_ASKS
ITEMS_DATASET = dataclasses.replace(
    items_ds.DATASET,
    key="chat_report_items",
    columns=_with_extra(_ITEM_COLUMNS + (_failure_reason,)) + _ITEM_AXES,
    base=lambda ctx: sa.select().select_from(_ITEMS_FROM).where(_ITEMS.c.kind == "product", _ITEMS.c.role == "named"),
)

#: The `grain` values: the ask grain is the default, products reads the item grain.
GRAIN_OPTIONS: Tuple[Tuple[str, str], ...] = (("asks", "Asks"), ("products", "Products"))


def _is_items(ctx) -> bool:
    return ctx.dataset.key.startswith(ITEMS_DATASET.key)


def customer_condition(ctx, values: List[str]) -> Any:
    return ASKS.c.customer_id.in_(values)


def product_condition(ctx, values: List[str]) -> Any:
    """Asks that NAMED one of the products (the item grain is already named-only)."""
    if _is_items(ctx):
        return _ITEMS.c.ref_id.in_(values)
    return sa.exists().where(
        _ITEMS.c.ask_id == ASKS.c.id,
        _ITEMS.c.kind == "product",
        _ITEMS.c.role == "named",
        _ITEMS.c.ref_id.in_(values),
    )


def asker_condition(ctx, values: List[str]) -> Any:
    return _ASKER_WORD.in_(values)


def _named_product_of(column: Any, values: List[str]) -> Any:
    """Asks that NAMED a product whose `column` (category / brand id) is one of `values`."""
    return sa.exists().where(
        _ITEMS.c.ask_id == ASKS.c.id,
        _ITEMS.c.kind == "product",
        _ITEMS.c.role == "named",
        sa.cast(_PRODUCTS.c.id, sa.Text) == _ITEMS.c.ref_id,
        column.in_(values),
    )


def category_condition(ctx, values: List[str]) -> Any:
    if _is_items(ctx):
        return _PRODUCTS.c.category_id.in_(values)
    return _named_product_of(_PRODUCTS.c.category_id, values)


def brand_condition(ctx, values: List[str]) -> Any:
    if _is_items(ctx):
        return _PRODUCTS.c.brand_id.in_(values)
    return _named_product_of(_PRODUCTS.c.brand_id, values)


def unclassified_contacts(db: Any, definition: Any, params: dict) -> int:
    """Distinct unclassified contacts with a counted ask in the period and every filter but the
    asker one (so "dealers only" still says how many unclassified contacts are not in it)."""
    from app.services.reports import engine

    ctx = engine.resolve(db, definition, {k: v for k, v in params.items() if k not in ("asker", "grain")})
    preds = [ctx.date_basis >= ctx.period.start, ctx.date_basis < ctx.period.end_exclusive]
    for param in ctx.definition.params:
        chosen = ctx.values.get(param.key) or []
        if isinstance(param, reg.SelectParam) and chosen:
            condition = param.condition(ctx, chosen)
            if condition is not None:
                preds.append(condition)
    return int(
        db.execute(
            sa.select(sa.func.count(sa.distinct(ASKS.c.contact_respond_id)))
            .select_from(ASKS)
            .where(*preds, _ASKER_WORD == "unclassified")
        ).scalar()
        or 0
    )


def period_has_asks(db: Any, definition: Any, period: dict, scope_customers: Any = None) -> bool:
    """Is there a counted ask in the period at all, whatever the user's filters? (The defaults of the
    report, such as noise out, still apply.) `scope_customers` is the caller's own scope (a dealer's
    customers, a salesperson's book): None = unscoped, a list (even empty) limits the probe to those
    customers, so a scoped caller never learns that others asked."""
    from app.services.reports import engine

    if scope_customers is not None and not scope_customers:
        return False
    params: dict[str, Any] = {"date_basis": "asked_at", "period": period}
    if scope_customers is not None:
        params["customer"] = list(scope_customers)
    ctx = engine.resolve(db, definition, params)
    preds = [ctx.date_basis >= ctx.period.start, ctx.date_basis < ctx.period.end_exclusive]
    for param in ctx.definition.params:
        chosen = ctx.values.get(param.key) or []
        if isinstance(param, reg.SelectParam) and chosen:
            condition = param.condition(ctx, chosen)
            if condition is not None:
                preds.append(condition)
    return bool(db.execute(sa.select(sa.literal(1)).select_from(ASKS).where(*preds).limit(1)).first())


def card_agents(db: Any, customer_ids: List[str]) -> dict:
    """customer id -> the customer card's agent name (None when it has none)."""
    from app.models.order import Customer

    customers = Customer.__table__
    rows = db.execute(
        sa.select(sa.cast(customers.c.id, sa.Text), _AGENTS.c.sales_agent)
        .select_from(customers.outerjoin(_AGENTS, _AGENTS.c.id == customers.c.sales_agent_id))
        .where(sa.cast(customers.c.id, sa.Text).in_(customer_ids))
    ).all()
    return {cid: agent for cid, agent in rows}


def _no_options(db: Any) -> List[Any]:
    return []


def _params() -> tuple:
    return (
        reg.DateBasisParam(key="date_basis", label="Date basis", default="asked_at"),
        reg.PeriodParam(key="period", label="Period", default=asks_ds.default_period),
        # No default: `DEFAULT_ASKERS` is the one switch of a chat run (crew ruling 10 Oct). The
        # writer stamps every unlinked contact office, so the screens' office-out default would
        # hide exactly the unclassified askers this report names.
        reg.SelectParam(
            "contact_type", "Contact type", True, (), asks_ds.contact_type_options, asks_ds.contact_type_condition,
        ),
        reg.SelectParam(
            "outcome", "Outcome", True, asks_ds.DEFAULT_OUTCOMES, asks_ds.outcome_options, asks_ds.outcome_condition
        ),
        reg.SelectParam("subject", "Subject", True, (), asks_ds.subject_options, asks_ds.subject_condition),
        reg.SelectParam("customer", "Customer", True, (), _no_options, customer_condition),
        reg.SelectParam("product", "Product", True, (), _no_options, product_condition),
        reg.SelectParam(
            "asker", "Asked by", True, (), lambda db: list(ASKER_LABELS), asker_condition
        ),
        reg.SelectParam("category", "Category", True, (), _no_options, category_condition),
        reg.SelectParam("brand", "Brand", True, (), _no_options, brand_condition),
        reg.SelectParam(
            "grain", "Grain", False, (GRAIN_OPTIONS[0][0],), lambda db: list(GRAIN_OPTIONS),
            lambda ctx, values: None, clearable=False,  # picks the dataset, filters nothing
        ),
    )


def _definition(basis: str) -> reg.ReportDefinition:
    asks, items = ASKS_DATASET, ITEMS_DATASET
    if basis == "turns":
        asks, items = asks_ds.turn_only(asks), asks_ds.turn_only(items)
    definition = reg.ReportDefinition(
        key=f"chat_report_{basis}",
        title="Chat asks",
        permission=PERMISSION,
        module_key=MODULE_KEY,
        dataset=asks,
        params=_params(),
        detail=reg.DetailLayout(title="Asks", order_by=asks_ds.order_by),
        pivot=reg.PivotLayout(title="Chat asks"),
        default_view={
            "params": {"date_basis": "asked_at"},
            "detail": {"columns": [], "order": []},
            "pivot": {"rows": "month", "cols": "all", "measures": ["asks"]},
        },
        workbook=reg.WorkbookSpec(company_name=""),
        datasets_by=("grain", {"products": items}),
    )
    reg.validate(definition)
    return definition


DEFINITIONS = {basis: _definition(basis) for basis in ("asks", "turns")}
