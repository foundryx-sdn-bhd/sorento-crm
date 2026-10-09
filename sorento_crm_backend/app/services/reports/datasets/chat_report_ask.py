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

from app.services.reports import registry as reg
from app.services.reports.datasets import chat_ask_items as items_ds
from app.services.reports.datasets import chat_asks as asks_ds

ASKS = asks_ds.ASKS
_ITEMS = items_ds._ITEMS

PERMISSION = "chatbot.analytics.view"
MODULE_KEY = "chatbot"

_ONE = sa.literal_column("1")


def _day(ctx) -> Any:
    return sa.func.to_char(sa.func.date_trunc("day", ctx.date_basis), "YYYY-MM-DD")


#: The report-only columns: the day bucket, the single-total axis, and the two new measures.
_EXTRA: Tuple[reg.Column, ...] = (
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
ITEMS_DATASET = dataclasses.replace(
    items_ds.DATASET,
    key="chat_report_items",
    columns=_with_extra(items_ds.COLUMNS + (_failure_reason,)),
    base=lambda ctx: items_ds.DATASET.base(ctx).where(_ITEMS.c.role == "named"),
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


def _no_options(db: Any) -> List[Any]:
    return []


def _params() -> tuple:
    return (
        reg.DateBasisParam(key="date_basis", label="Date basis", default="asked_at"),
        reg.PeriodParam(key="period", label="Period", default=asks_ds.default_period),
        reg.SelectParam(
            "contact_type", "Contact type", True, asks_ds.DEFAULT_CONTACT_TYPES,
            asks_ds.contact_type_options, asks_ds.contact_type_condition,
        ),
        reg.SelectParam(
            "outcome", "Outcome", True, asks_ds.DEFAULT_OUTCOMES, asks_ds.outcome_options, asks_ds.outcome_condition
        ),
        reg.SelectParam("subject", "Subject", True, (), asks_ds.subject_options, asks_ds.subject_condition),
        reg.SelectParam("customer", "Customer", True, (), _no_options, customer_condition),
        reg.SelectParam("product", "Product", True, (), _no_options, product_condition),
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
