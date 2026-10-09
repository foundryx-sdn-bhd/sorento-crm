"""The rows behind the Chat overview: one row per customer ask the chatbot answered
(`chatbot.chat_ask_facts`, chat analytics CA-1).

**Company scope is deliberately `none`, and this is where that is justified.** One bot
serves every company, and an ask carries no company of its own: `company_id` on a fact is a
snapshot of the asking contact's primary customer link, so an unlinked contact (a stranger
who wrote in) has none at all. Scoping through it would blank exactly the asks the owner most
wants to see. The report is staff-only behind `chatbot.analytics.view`, granted to whoever
may already read the chat history. CA-3 adds the company-scoped dealer and salesman views,
which is where a per-company arm earns its place. Declared, not defaulted (registry.Dataset
refuses a dataset that says nothing about scope).

**Nothing here can show what a customer wrote.** The fact table has no message, summary,
phone or contact name to select, and the catalog adds none: `customer` is the linked
ACCOUNT's name from `customers`, a business, not a person. No UUID is a column either.

**Weeks and months are Malaysia's.** `ctx.date_basis` is already the Malaysia wall clock
(engine `resolve`), so `date_trunc('week', ...)` buckets a Sunday 17:00 UTC ask into the
Monday it is in Kuala Lumpur, and the period predicate, the bucket and the printed date
cannot disagree. Weeks run Monday to Sunday (Postgres' own).

**`failed` is `failure_reason IS NOT NULL`.** The writer sets a failure reason for exactly the
failed outcomes (technical, cannot_do, missed), so the report needs no copy of that list that
could drift from `chatbot/analytics/outcome.py` (and the chatbot package may not be imported
from here).

`customers` is reached as a CORE table, not a mapped entity: it is company-scoped, and the
ORM scope listener would splice that scope into any statement mentioning the mapped class,
blanking the name for a session with no company set.
"""
from __future__ import annotations

from datetime import date
from typing import Any, List, Sequence, Tuple

import sqlalchemy as sa

from app.models.chat_facts import ChatAskFact
from app.models.order import Customer
from app.services.reports import registry as reg
from app.services.reports.engine import month_label

# Core tables, not mapped entities (see the module docstring).
ASKS = ChatAskFact.__table__
_CUSTOMERS = Customer.__table__

#: The first day the bot's traces exist (the one-time load starts here). The period opens on
#: it, and the coverage note says so; before it there is nothing to count (CA-4 may add it).
TRACES_BEGIN = date(2026, 9, 5)

COVERAGE_NOTE = "From bot traces, 5 Sep 2026 on. Topics arrive with CA-2."

#: (value, label) in the order a reader meets them. Values are `outcome.turn_outcome`'s.
OUTCOME_LABELS: Tuple[Tuple[str, str], ...] = (
    ("answered", "Answered"),
    ("answered_empty", "Answered, nothing found"),
    ("clarified", "Asked back"),
    ("refused_policy", "Refused by policy"),
    ("handover", "Handed over"),
    ("cannot_do", "Cannot do"),
    ("missed", "Missed"),
    ("technical", "Technical failure"),
    ("noise", "Noise"),
)

#: What a run counts unless told otherwise: a greeting or a stray emoji is not an ask.
DEFAULT_OUTCOMES: Tuple[str, ...] = tuple(v for v, _ in OUTCOME_LABELS if v != "noise")

AUDIENCE_LABELS: Tuple[Tuple[str, str], ...] = (
    ("dealer", "Dealers"),
    ("staff", "Staff"),
    ("unknown", "Unknown contacts"),
)

#: Staff testing the bot are not customers, so the report opens without them.
DEFAULT_AUDIENCES: Tuple[str, ...] = ("dealer", "unknown")


def default_period() -> dict:
    """The traces' first day to today in Malaysia, resolved when ASKED FOR."""
    today = reg.today_malaysia()
    return {"kind": "custom", "from": TRACES_BEGIN.isoformat(), "to": max(today, TRACES_BEGIN).isoformat()}


# ------------------------------------------------------------------------ expressions


def week(ctx) -> Any:
    """The Monday of the ask's week, Malaysia clock, as `YYYY-MM-DD`."""
    return sa.func.to_char(sa.func.date_trunc("week", ctx.date_basis), "YYYY-MM-DD")


def month(ctx) -> Any:
    return sa.func.to_char(sa.func.date_trunc("month", ctx.date_basis), "YYYY-MM")


def week_label(key: str) -> str:
    """"2026-09-07" -> "7 Sep'26": the Monday the week opens on."""
    year, month_number, day = key.split("-")
    return f"{int(day)} {month_label(f'{year}-{month_number}')}"


_CUSTOMER = (
    sa.select(_CUSTOMERS.c.customer_name)
    .where(_CUSTOMERS.c.id == ASKS.c.customer_id)
    .correlate(ASKS)
    .scalar_subquery()
)

_ONE = sa.literal_column("1")
_FAILED = sa.case((ASKS.c.failure_reason.isnot(None), 1), else_=0)


# --------------------------------------------------------------------------- params


def outcome_options(db) -> Sequence[Tuple[str, str]]:
    return list(OUTCOME_LABELS)


def outcome_condition(ctx, values: List[str]) -> Any:
    return ASKS.c.outcome.in_(values)


def audience_options(db) -> Sequence[Tuple[str, str]]:
    return list(AUDIENCE_LABELS)


def audience_condition(ctx, values: List[str]) -> Any:
    return ASKS.c.audience.in_(values)


# ---------------------------------------------------------------------- the catalog


def ask_dimensions() -> Tuple[reg.Column, ...]:
    """The dimensions that are facts about the ASK, shared by the item grain (which joins
    the ask, so each of these reads the same on both)."""
    return (
        reg.Column("outcome", "Outcome", "text", "dimension", lambda c: ASKS.c.outcome, size=150),
        reg.Column("week", "Week", "text", "dimension", week, size=100, value_label=week_label),
        reg.Column("month", "Month", "text", "dimension", month, size=110, period_months=True, value_label=month_label),
        reg.Column("domain", "Domain", "text", "dimension", lambda c: ASKS.c.domain, size=120),
        reg.Column("audience", "Audience", "text", "dimension", lambda c: ASKS.c.audience, size=110),
        reg.Column("customer", "Customer", "text", "dimension", lambda c: _CUSTOMER, size=200),
    )


COLUMNS: Tuple[reg.Column, ...] = (
    reg.Column("asked_at", "Asked on", "date", "date", lambda c: ASKS.c.asked_at, size=120),
    *ask_dimensions(),
    reg.Column("failure_reason", "Failure reason", "text", "dimension", lambda c: ASKS.c.failure_reason, size=140),
    reg.Column("branch_kind", "Branch", "text", "dimension", lambda c: ASKS.c.branch_kind, size=140),
    reg.Column("asks", "Asks", "integer", "measure", lambda c: _ONE, size=80),
    reg.Column("failed", "Failed", "integer", "measure", lambda c: _FAILED, size=80),
)


def _base(ctx) -> sa.Select:
    return sa.select().select_from(ASKS)


DATASET = reg.Dataset(
    key="chat_asks",
    scope="none",  # justified in the module docstring
    columns=COLUMNS,
    date_bases=(reg.DateBasis("asked_at", "Asked on", ASKS.c.asked_at),),
    base=_base,
)


def order_by(ctx) -> Sequence[Any]:
    """Newest first, on the date the user is reading by. The item grain joins the same ask
    table, so one ordering serves both datasets."""
    return [sa.nullslast(ctx.date_basis.desc()), ASKS.c.id.asc()]
