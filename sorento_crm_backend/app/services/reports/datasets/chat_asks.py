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

**Codes are shown as words, in the dataset.** Outcome, contact type, audience, failure reason and branch
are CASE expressions that map the stored code to its plain words (the same device as
`sponsorship_forms._STATUS_LABEL`), so the pivot's row labels and the detail table read the
same and no code reaches the screen. The filters (`outcome`, `contact_type`) compare the raw
columns, never the words. A pivot row dimension prints only the values present, which is why
this is not `fixed_values`: that would print an empty row for every outcome, noise included,
and would still leave the detail table in codes. The rows therefore sort alphabetically by
their words. The branch words are a local copy of the trace screen's lane words: the report
may not import the chatbot package.

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

import dataclasses
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, List, Sequence, Tuple

import sqlalchemy as sa

from app.models.chat_facts import ChatAskFact, ChatTopic
from app.models.order import Customer
from app.services.reports import registry as reg
from app.services.reports.engine import month_label

# Core tables, not mapped entities (see the module docstring).
ASKS = ChatAskFact.__table__
_CUSTOMERS = Customer.__table__
_TOPICS = ChatTopic.__table__

#: The first day the bot's traces exist (the one-time load starts here). The period opens on
#: it, and the coverage note says so; before it there is nothing to count (CA-4 may add it).
TRACES_BEGIN = date(2026, 9, 5)

TRACES_NOTE = "From bot traces, 5 Sep 2026 on."
COVERAGE_NOTE = (
    f"{TRACES_NOTE} Most asked products: Count by Products, measure Asked "
    "(products the bot looked up)."
)

#: (value, label) in the order a reader meets them. Values are `outcome.turn_outcome`'s.
OUTCOME_LABELS: Tuple[Tuple[str, str], ...] = (
    ("answered", "Answered"),
    ("answered_empty", "Answered, nothing found"),
    ("clarified", "Asked back"),
    ("refused_policy", "Refused by policy"),
    ("handover", "Handed over"),
    ("off_topic", "Off topic"),
    ("cannot_do", "Cannot do"),
    ("missed", "Missed"),
    ("technical", "Technical failure"),
    ("noise", "Noise"),
)

#: What a run counts unless told otherwise: a greeting or a stray emoji is not an ask.
DEFAULT_OUTCOMES: Tuple[str, ...] = tuple(v for v, _ in OUTCOME_LABELS if v != "noise")

#: A branch (lane) in plain words. A branch not listed prints as stored.
BRANCH_WORDS: Tuple[Tuple[str, str], ...] = (
    ("access_denied", "Access refused"),
    ("escalate_offer", "Escalation offer"),
    ("out_of_scope", "Escalation"),
    ("ideate", "Idea capture"),
    ("offer_hold", "Holding an offer open"),
    ("escalation_declined", "Escalation declined"),
    ("check_promotion", "Business query: promotion"),
    ("low_signal", "Small talk"),
    ("clarify_menu", "Asked to clarify"),
    ("not_supported", "Not supported"),
    ("stock_denied", "Stock access refused"),
    ("demand_qty", "Asked for a quantity"),
    ("business_query", "Business query"),
    ("ai_not_allowed", "AI not allowed"),
)

AUDIENCE_LABELS: Tuple[Tuple[str, str], ...] = (
    ("dealer", "Dealers"),
    ("staff", "Staff"),
    ("unknown", "Unknown contacts"),
)

#: Who the contact is to the business (`facts._who`). Office is people inside the company, and
#: anyone the bot answers who is linked to no customer.
CONTACT_TYPE_LABELS: Tuple[Tuple[str, str], ...] = (
    ("dealer", "Dealer"),
    ("office", "Office"),
    ("not_allowed", "Not allowed"),
    ("unknown", "Unknown"),
)

#: Office is out by default: staff testing the bot are not customers.
DEFAULT_CONTACT_TYPES: Tuple[str, ...] = ("dealer", "not_allowed", "unknown")


def topics_note(ctx, *, skip: Tuple[str, ...] = ()) -> str:
    """`Topics labelled: 71% of asks (taxonomy v2).` plus the coverage line.

    The share is over the asks the run counts: the period and the ask-level filters (a
    param named in `skip` filters another table and is left out). No asks reads 0%.
    """
    preds = [ctx.date_basis >= ctx.period.start, ctx.date_basis < ctx.period.end_exclusive]
    for param in ctx.definition.params:
        chosen = ctx.values.get(param.key) or []
        if isinstance(param, reg.SelectParam) and chosen and param.key not in skip:
            condition = param.condition(ctx, chosen)
            if condition is not None:
                preds.append(condition)
    counted, labelled = ctx.db.execute(
        sa.select(sa.func.count(), sa.func.count(ASKS.c.topic_code)).select_from(ASKS).where(*preds)
    ).one()
    percent = int((Decimal(labelled) * 100 / Decimal(counted)).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if counted else 0
    version = ctx.db.execute(
        sa.select(sa.func.max(_TOPICS.c.version)).where(_TOPICS.c.status == "active")
    ).scalar()
    tail = f" (taxonomy v{version})" if version is not None else ""
    return f"Topics labelled: {percent}% of asks{tail}. {TRACES_NOTE}"


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

def _words(column: Any, pairs: Tuple[Tuple[str, str], ...]) -> Any:
    """`column` as plain words; a value with no entry prints as stored."""
    return sa.case({value: label for value, label in pairs}, value=column, else_=column)


#: A subject the taxonomy has no row for prints as its code; no domain is the `general` subject.
SUBJECT_CODE = sa.func.coalesce(ASKS.c.domain, sa.literal("general"))
NOT_LABELLED = "(not labelled yet)"


def _topic_label(code: Any, fallback: Any) -> Any:
    """The taxonomy's words for `code`, else `fallback`. A scalar subquery on the Core
    table (code is the primary key, so at most one row)."""
    label = sa.select(_TOPICS.c.label).where(_TOPICS.c.code == code).correlate(ASKS).scalar_subquery()
    return sa.func.coalesce(label, fallback)


_SUBJECT = _topic_label(SUBJECT_CODE, SUBJECT_CODE)
_TOPIC = sa.case(
    (ASKS.c.topic_code.is_(None), sa.literal(NOT_LABELLED)),
    else_=_topic_label(ASKS.c.topic_code, ASKS.c.topic_code),
)
_OUTCOME = _words(ASKS.c.outcome, OUTCOME_LABELS)
_AUDIENCE = _words(ASKS.c.audience, AUDIENCE_LABELS)
_CONTACT_TYPE = _words(ASKS.c.contact_type, CONTACT_TYPE_LABELS)
_FAILURE_REASON = _words(ASKS.c.failure_reason, OUTCOME_LABELS)
_BRANCH = _words(ASKS.c.branch_kind, BRANCH_WORDS)
_REPLY_SECONDS = sa.func.round(ASKS.c.reply_ms / sa.literal(1000.0))
_TOOLS = sa.func.array_to_string(ASKS.c.tools, ", ")

_ONE = sa.literal_column("1")
_FAILED = sa.case((ASKS.c.failure_reason.isnot(None), 1), else_=0)


# --------------------------------------------------------------------------- params


def outcome_options(db) -> Sequence[Tuple[str, str]]:
    return list(OUTCOME_LABELS)


def outcome_condition(ctx, values: List[str]) -> Any:
    return ASKS.c.outcome.in_(values)


def contact_type_options(db) -> Sequence[Tuple[str, str]]:
    return list(CONTACT_TYPE_LABELS)


def contact_type_condition(ctx, values: List[str]) -> Any:
    return ASKS.c.contact_type.in_(values)


def subject_options(db) -> Sequence[Tuple[str, str]]:
    """The subject rows of the taxonomy, in its own order."""
    rows = db.execute(
        sa.select(_TOPICS.c.code, _TOPICS.c.label)
        .where(_TOPICS.c.parent_code.is_(None), _TOPICS.c.status == "active")
        .order_by(_TOPICS.c.sort_order, _TOPICS.c.code)
    ).all()
    return [(code, label) for code, label in rows]


def subject_condition(ctx, values: List[str]) -> Any:
    return SUBJECT_CODE.in_(values)


# ---------------------------------------------------------------------- the catalog


def ask_dimensions() -> Tuple[reg.Column, ...]:
    """The dimensions that are facts about the ASK, shared by the item grain (which joins
    the ask, so each of these reads the same on both)."""
    return (
        reg.Column("outcome", "Outcome", "text", "dimension", lambda c: _OUTCOME, size=150),
        reg.Column("week", "Week", "text", "dimension", week, size=100, value_label=week_label),
        reg.Column("month", "Month", "text", "dimension", month, size=110, period_months=True, value_label=month_label),
        reg.Column("subject", "Subject", "text", "dimension", lambda c: _SUBJECT, size=140),
        reg.Column("topic", "Topic", "text", "dimension", lambda c: _TOPIC, size=200),
        reg.Column("language", "Language", "text", "dimension", lambda c: ASKS.c.language, size=100),
        reg.Column("domain", "Domain", "text", "dimension", lambda c: ASKS.c.domain, size=120),
        reg.Column("contact_type", "Contact type", "text", "dimension", lambda c: _CONTACT_TYPE, size=120),
        reg.Column("audience", "Audience", "text", "dimension", lambda c: _AUDIENCE, size=110),
        reg.Column("customer", "Customer", "text", "dimension", lambda c: _CUSTOMER, size=200),
    )


COLUMNS: Tuple[reg.Column, ...] = (
    reg.Column("asked_at", "Asked on", "date", "date", lambda c: ASKS.c.asked_at, size=120),
    *ask_dimensions(),
    reg.Column("failure_reason", "Failure reason", "text", "dimension", lambda c: _FAILURE_REASON, size=140),
    reg.Column("branch_kind", "Branch", "text", "dimension", lambda c: _BRANCH, size=160),
    reg.Column("rows_offered", "Rows offered", "integer", "text", lambda c: ASKS.c.rows_offered, size=110),
    reg.Column("reply_seconds", "Reply seconds", "integer", "text", lambda c: _REPLY_SECONDS, size=110),
    reg.Column("tools", "Tools", "text", "text", lambda c: _TOOLS, size=200),
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


def turn_only(dataset: reg.Dataset) -> reg.Dataset:
    """`dataset` counting each TURN once: only ask 1 of a split message, so a turn measure
    (asks, failed) does not double on the labeller's extra asks. Top asks keeps every ask."""
    return dataclasses.replace(
        dataset,
        key=f"{dataset.key}_turns",
        base=lambda ctx: dataset.base(ctx).where(ASKS.c.ask_seq == 1),
    )


def order_by(ctx) -> Sequence[Any]:
    """Newest first, on the date the user is reading by. The item grain joins the same ask
    table, so one ordering serves both datasets."""
    return [sa.nullslast(ctx.date_basis.desc()), ASKS.c.id.asc()]
