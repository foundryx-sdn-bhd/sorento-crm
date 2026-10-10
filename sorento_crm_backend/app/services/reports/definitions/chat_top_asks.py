"""Report: Top asks, what customers ask the chatbot about (chat analytics CA-2).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Top asks". The
whole report is this file plus the two chat datasets: no route, no schema and no page of its
own beyond the shared report screen.

- Opens on topic by month, counting asks, as a bar chart. An ask the labeller has not reached
  yet is counted under "(not labelled yet)", never dropped; the note says how much is labelled.
- Subject narrows the asks to one or more subjects of the taxonomy.
- "Count by" swaps the row set: topics (one row per ask) or products (one row per product an
  ask named or was shown). "Products" then picks which: asked (the customer named it, or the
  bot looked it up) or shown (it was in a reply). An ask that both named and looked up one
  product counts that product once.
- The period opens on 5 Sep 2026, the first day the bot's traces exist.
"""
from __future__ import annotations

import dataclasses

import sqlalchemy as sa

from app.models.chat_facts import ChatAskItem
from app.services.reports import registry as reg
from app.services.reports.datasets import chat_ask_items as items_ds
from app.services.reports.datasets import chat_asks as ds

KEY = "chat_top_asks"
PERMISSION = "chatbot.analytics.view"
MODULE_KEY = "chatbot"

COUNT_BY: tuple = (("topics", "Topics"), ("products", "Products"))
PRODUCT_ROLES: tuple = (("asked", "Asked"), ("shown", "Shown"))

_ITEMS = ChatAskItem.__table__

#: The product rows, with the count measure called Asks: here a row is one (ask, product).
ITEMS_DATASET = dataclasses.replace(
    items_ds.DATASET,
    key="chat_top_ask_items",
    columns=tuple(
        dataclasses.replace(c, label="Asks") if c.key == "asks" else c for c in items_ds.DATASET.columns
    ),
)

DEFAULT_DETAIL_COLUMNS = ["asked_at", "subject", "topic", "language", "outcome", "customer", "product"]


def _roles_condition(ctx, values):
    """Filters the products grain only; the ask grain has no item table to filter."""
    if ctx.dataset is not ITEMS_DATASET:
        return None
    if values[0] == "shown":
        return _ITEMS.c.role == "shown"
    # Named or asked; an `asked` row of a product the same ask also NAMED is the same
    # product, so it is left out and the product counts once.
    twin = sa.alias(_ITEMS)
    named_too = sa.exists().where(
        twin.c.ask_id == _ITEMS.c.ask_id,
        twin.c.kind == _ITEMS.c.kind,
        twin.c.ref_id == _ITEMS.c.ref_id,
        twin.c.role == "named",
    )
    return sa.and_(_ITEMS.c.role.in_(("named", "asked")), sa.not_(sa.and_(_ITEMS.c.role == "asked", named_too)))


def params() -> tuple:
    return (
        reg.DateBasisParam(key="date_basis", label="Date basis", default="asked_at"),
        reg.PeriodParam(key="period", label="Period", default=ds.default_period, default_reads_db=True),
        reg.SelectParam(
            key="contact_type",
            label="Contact type",
            multi=True,
            default=ds.DEFAULT_CONTACT_TYPES,
            options=ds.contact_type_options,
            condition=ds.contact_type_condition,
        ),
        reg.SelectParam(
            key="outcome",
            label="Outcome",
            multi=True,
            default=ds.DEFAULT_OUTCOMES,
            options=ds.outcome_options,
            condition=ds.outcome_condition,
        ),
        reg.SelectParam(
            key="subject",
            label="Subject",
            multi=True,
            default=(),
            options=ds.subject_options,
            condition=ds.subject_condition,
        ),
        reg.SelectParam(
            key="count_by",
            label="Count by",
            multi=False,
            default=(COUNT_BY[0][0],),
            options=lambda db: list(COUNT_BY),
            condition=lambda ctx, values: None,  # picks the dataset, filters nothing
            clearable=False,
        ),
        reg.SelectParam(
            key="products",
            label="Products",
            multi=False,
            default=(PRODUCT_ROLES[0][0],),
            options=lambda db: list(PRODUCT_ROLES),
            condition=_roles_condition,
            clearable=False,
        ),
    )


REPORT = reg.register(
    reg.ReportDefinition(
        key=KEY,
        title="Top asks",
        permission=PERMISSION,
        module_key=MODULE_KEY,
        dataset=ds.DATASET,
        params=params(),
        detail=reg.DetailLayout(title="Asks", order_by=ds.order_by, cap="truncate"),
        pivot=reg.PivotLayout(title="Asks by month", chart="bar"),
        default_view={
            "params": {
                "date_basis": "asked_at",
                "contact_type": list(ds.DEFAULT_CONTACT_TYPES),
                "outcome": list(ds.DEFAULT_OUTCOMES),
                "count_by": [COUNT_BY[0][0]],
                "products": [PRODUCT_ROLES[0][0]],
            },
            "detail": {"columns": list(DEFAULT_DETAIL_COLUMNS), "order": list(DEFAULT_DETAIL_COLUMNS)},
            "pivot": {"rows": "topic", "cols": "month", "measures": ["asks"]},
        },
        note=lambda ctx: ds.topics_note(ctx, skip=("products", "count_by")),
        opens_on="summary",
        datasets_by=("count_by", {"products": ITEMS_DATASET}),
        pivot_rows_by={"topics": "topic", "products": "product"},
        workbook=reg.WorkbookSpec(
            company_name="SORENTO SDN BHD",
            report_title="TOP ASKS",
            headers={"topic": "TOPIC", "month": "MONTH"},
            month_sheets=False,
        ),
    )
)
