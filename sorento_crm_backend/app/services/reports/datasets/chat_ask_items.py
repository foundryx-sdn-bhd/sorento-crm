"""The rows behind "count by products": one row per product an ask NAMED or was SHOWN
(`chatbot.chat_ask_items` joined to its ask and to the catalogue, chat analytics CA-1).

**Company scope is `none`, for the reason `chat_asks` gives** (one bot for every company,
unlinked contacts have no company, staff-only permission; CA-3 adds the company-scoped
views). The two datasets must agree on it: the registry refuses a report whose datasets
disagree on scope.

**The row label is the product CODE.** `ref_id` is a CRM id and no id reaches the screen; the
code is joined in from `products` (a Core table: `products` is company-scoped, and the ORM
listener would blank it for a session with no company). Brand items are left out here: a
brand is not a product row. A code that exists in two companies is already resolved to one
product by the writer, so a row is one product.

**The ask's own columns come through the join**, so the outcome, audience and noise filters
of the report apply to this grain exactly as to the ask grain: they are conditions on the
ask table, which this base joins. The registry also validates the report's default view
(outcome by week, asks and failed) against EVERY dataset, so those four are carried here.

**`asks` and `failed` here count ITEM ROWS, not asks.** An ask naming three products is three
rows, so the measure is labelled "Item rows" while keeping the key `asks`: the registry
validates the report's one default view (outcome by week, asks and failed) against every
dataset, so the key must exist here. On this grain the measures to read are `named` (asks
that named the product) and `shown` (replies that showed it); a true ask count is the Asks
view's.
"""
from __future__ import annotations

from typing import Tuple

import sqlalchemy as sa

from app.models.chat_facts import ChatAskItem
from app.models.product import Product
from app.services.reports import registry as reg
from app.services.reports.datasets import chat_asks as asks_ds

# Core tables, not mapped entities (see the module docstring).
ASKS = asks_ds.ASKS
_ITEMS = ChatAskItem.__table__
_PRODUCTS = Product.__table__

_FAILED = sa.case((ASKS.c.failure_reason.isnot(None), 1), else_=0)
_NAMED = sa.case((_ITEMS.c.role == "named", 1), else_=0)
_SHOWN = sa.case((_ITEMS.c.role == "shown", 1), else_=0)

_FROM = _ITEMS.join(ASKS, ASKS.c.id == _ITEMS.c.ask_id).join(
    _PRODUCTS, sa.cast(_PRODUCTS.c.id, sa.Text) == _ITEMS.c.ref_id
)

COLUMNS: Tuple[reg.Column, ...] = (
    reg.Column("asked_at", "Asked on", "date", "date", lambda c: ASKS.c.asked_at, size=120),
    *asks_ds.ask_dimensions(),
    reg.Column("product", "Product", "text", "dimension", lambda c: _PRODUCTS.c.product_code, size=160),
    reg.Column("asks", "Item rows", "integer", "measure", lambda c: sa.literal_column("1"), size=80),
    reg.Column("failed", "Failed", "integer", "measure", lambda c: _FAILED, size=80),
    reg.Column("named", "Named", "integer", "measure", lambda c: _NAMED, size=80),
    reg.Column("shown", "Shown", "integer", "measure", lambda c: _SHOWN, size=80),
)


def _base(ctx) -> sa.Select:
    return sa.select().select_from(_FROM).where(_ITEMS.c.kind == "product")


DATASET = reg.Dataset(
    key="chat_ask_items",
    scope="none",  # justified in the module docstring
    columns=COLUMNS,
    date_bases=(reg.DateBasis("asked_at", "Asked on", ASKS.c.asked_at),),
    base=_base,
)
