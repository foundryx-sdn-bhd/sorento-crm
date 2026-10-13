"""Dataset catalog and the whitelist query builder for the chat tools (card sections 4.3, 5).

No raw SQL ever comes from the caller. A query names a dataset, columns, filters, group-bys and
measures from the catalog below; the builder turns them into SQLAlchemy Core over the
`analytics_connector` views, with every value bound and the `contains` pattern escaped.
Anything outside the catalog raises `QueryError`, which the route answers with 422.
"""
from __future__ import annotations

import json
import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Integer, column, create_engine, distinct, func, select, table, text
from sqlalchemy import cast as sa_cast
from sqlalchemy.exc import DBAPIError

from app.services.analytics_connector.views import SCHEMA

MAX_ROWS = 1000
DEFAULT_ROWS = 200
MAX_OUTPUT_CHARS = 120_000  # under claude.ai's ~150,000-character tool result limit
STATEMENT_TIMEOUT = "10s"
BUCKETS = ("hour", "day", "week", "month", "dow", "hour_of_day")


class QueryError(ValueError):
    """The request is outside the catalog (answered 422)."""


@dataclass(frozen=True)
class Col:
    key: str
    type: str  # text | int | number | datetime | bool | json
    description: str
    filterable: bool = True
    groupable: bool = False
    measurable: bool = False


@dataclass(frozen=True)
class Dataset:
    key: str
    description: str
    date_column: str
    columns: tuple[Col, ...]
    id_column: Optional[str] = "id"

    def col(self, key: str) -> Col:
        for c in self.columns:
            if c.key == key:
                return c
        raise QueryError(f"unknown column {key!r} in dataset {self.key!r}")


_CONTACT_COLS = (
    Col("contact_id", "text", "CRM contact id", groupable=True),
    Col("contact_name", "text", "Contact name", groupable=True),
)

CATALOG: dict[str, Dataset] = {
    "messages": Dataset(
        "messages",
        "Every WhatsApp message in or out, with contact and customer names. Sign-in codes are masked.",
        "sent_at",
        (
            Col("id", "int", "Message row id"),
            Col("sent_at", "datetime", "When it was sent (Malaysia time)"),
            Col("direction", "text", "incoming (customer) or outgoing (bot or staff)", groupable=True),
            Col("channel", "text", "Chat channel", groupable=True),
            *_CONTACT_COLS,
            Col("contact_respond_id", "text", "Respond.io contact id", groupable=True),
            Col("customer_code", "text", "Primary customer code of the contact", groupable=True),
            Col("customer_name", "text", "Primary customer name of the contact", groupable=True),
            Col("companies", "text", "Companies the contact belongs to", groupable=True),
            Col("text", "text", "Message text"),
            Col("reply_to_text", "text", "Text of the message this one quotes"),
            Col("chatbot_turn_id", "text", "Bot turn that produced an outgoing message"),
            Col("delivery_status", "text", "sent, delivered, read, failed", groupable=True),
        ),
    ),
    "contacts": Dataset(
        "contacts",
        "WhatsApp contacts with their customer, companies, chatbot switches and message counts.",
        "created_at",
        (
            Col("id", "text", "CRM contact id"),
            Col("respond_io_id", "text", "Respond.io contact id"),
            Col("name", "text", "Contact name", groupable=True),
            Col("first_name", "text", "First name"),
            Col("last_name", "text", "Last name"),
            Col("customer_code", "text", "Primary customer code", groupable=True),
            Col("customer_name", "text", "Primary customer name", groupable=True),
            Col("companies", "text", "Companies", groupable=True),
            Col("created_at", "datetime", "When the contact was created (Malaysia time)"),
            Col("chatbot_allowed", "bool", "Chatbot enabled", groupable=True),
            Col("chatbot_stock_allowed", "bool", "May ask the bot for stock", groupable=True),
            Col("outbound_enabled", "bool", "Outbound messages enabled", groupable=True),
            Col("escalation_allowed", "bool", "May be escalated to staff", groupable=True),
            Col("chatbot_memory_level", "text", "Bot memory level", groupable=True),
            Col("profile_tier", "text", "Chatbot profile tier", groupable=True),
            Col("profile_language", "text", "Chatbot profile language", groupable=True),
            Col("first_message_at", "datetime", "First message (Malaysia time)"),
            Col("last_message_at", "datetime", "Last message (Malaysia time)"),
            Col("message_count", "int", "Messages exchanged", measurable=True),
            Col("profile_facts", "json", "Facts the chatbot remembers about the contact", filterable=False),
        ),
    ),
    "bot_turns": Dataset(
        "bot_turns",
        "One row per chatbot turn: what the customer asked, what the bot answered, how it went.",
        "created_at",
        (
            Col("id", "text", "Turn id"),
            Col("contact_id", "text", "CRM contact id", groupable=True),
            Col("contact_respond_id", "text", "Respond.io contact id", groupable=True),
            Col("contact_name", "text", "Contact name", groupable=True),
            Col("created_at", "datetime", "Turn start (Malaysia time)"),
            Col("finished_at", "datetime", "Turn end (Malaysia time)"),
            Col("latency_ms", "int", "Milliseconds from start to finish", measurable=True),
            Col("status", "text", "queued, processing, delegated, done, failed", groupable=True),
            Col("stage", "text", "Where the turn stopped", groupable=True),
            Col("branch_kind", "text", "Branch the engine took", groupable=True),
            Col("error", "text", "Error text of a failed turn"),
            Col("attempt", "int", "Attempt number", measurable=True),
            Col("inbound_text", "text", "What the customer wrote"),
            Col("reply_text", "text", "What the bot answered"),
        ),
    ),
    "episodes": Dataset(
        "episodes",
        "Conversation episodes the bot tracked: topic, outcome, tools used and a summary.",
        "started_at",
        (
            Col("id", "text", "Episode id"),
            Col("contact_id", "text", "CRM contact id", groupable=True),
            Col("contact_name", "text", "Contact name", groupable=True),
            Col("domain", "text", "Domain (stock, order, ...)", groupable=True),
            Col("intent", "text", "Intent", groupable=True),
            Col("status", "text", "open or closed", groupable=True),
            Col("close_reason", "text", "Why it closed", groupable=True),
            Col("started_at", "datetime", "Start (Malaysia time)"),
            Col("last_activity_at", "datetime", "Last activity (Malaysia time)"),
            Col("closed_at", "datetime", "Close (Malaysia time)"),
            Col("turn_count", "int", "Turns in the episode", measurable=True),
            Col("tools_used", "text", "Tools the bot called"),
            Col("summary", "text", "Episode summary"),
            Col("last_user_message", "text", "Last thing the customer wrote"),
            Col("last_assistant_summary", "text", "Summary of the bot's last answer"),
        ),
    ),
    # CHAT-TURN-COST (plan documentation/plans/analytics/PLAN-chat-turn-cost-13oct.md).
    "turn_costs": Dataset(
        "turn_costs",
        "One row per chatbot turn with what it cost: every LLM call of the turn summed, priced in USD"
        " from the model price table, plus the conversation category (domain, intent, topic), outcome and"
        " latency. Group by contact_name, episode_id, domain or created_at buckets for cost per contact,"
        " conversation, category or day. Category columns are empty for a turn still open. cost_usd is NULL when a call used a model with no price (priced = false).",
        "created_at",
        (
            Col("id", "text", "Turn id (the same id as bot_turns.id and llm_calls.chatbot_turn_id)"),
            Col("created_at", "datetime", "Turn start (Malaysia time)"),
            *_CONTACT_COLS,
            Col("contact_respond_id", "text", "Respond.io contact id", groupable=True),
            Col("customer_name", "text", "Primary customer name of the contact", groupable=True),
            Col("episode_id", "text", "Conversation episode the turn belongs to (episodes.id); empty while the"
                " episode is still open", groupable=True),
            Col("inbound_text", "text", "What the customer wrote (sign-in codes masked)"),
            Col("reply_text", "text", "What the bot answered (sign-in codes masked)"),
            Col("status", "text", "queued, processing, delegated, done, failed", groupable=True),
            Col("stage", "text", "Where the turn stopped", groupable=True),
            Col("branch_kind", "text", "Reply branch (lane) the engine took, e.g. business, low_signal", groupable=True),
            Col("domain", "text", "Conversation category: the domain the turn acted on (stock, order, ...),"
                " the planner's first domain, else the parser's domain_hint", groupable=True),
            Col("intent", "text", "Parser's intent_hint within the domain", groupable=True),
            Col("topic_code", "text", "Topic the nightly labeller gave the ask (empty until labelled)", groupable=True),
            Col("topic_label", "text", "Readable name of topic_code", groupable=True),
            Col("outcome", "text", "How the ask ended: answered, answered_empty, clarified, handover, missed, cannot_do, refused_policy, off_topic, noise, technical", groupable=True),
            Col("prompt_version", "int", "Parser prompt version the turn ran on", groupable=True),
            Col("model", "text", "Model(s) the turn's LLM calls used, comma-separated", groupable=True),
            Col("llm_calls", "int", "LLM calls the turn made (parser, plus the clarifier on small-talk turns)",
                measurable=True),
            Col("prompt_tokens", "int", "Input tokens over the turn's calls. Includes cached tokens for OpenAI and"
                " Gemini, excludes them for Anthropic", measurable=True),
            Col("cached_tokens", "int", "Input tokens served from the provider's prompt cache (billed at the"
                " cached rate)", measurable=True),
            Col("completion_tokens", "int", "Output tokens over the turn's calls", measurable=True),
            Col("total_tokens", "int", "prompt_tokens + completion_tokens over the turn's calls", measurable=True),
            Col("llm_ms", "int", "Milliseconds spent waiting on LLM calls", measurable=True),
            Col("latency_ms", "int", "Milliseconds from turn start to finish", measurable=True),
            Col("cost_usd", "number", "USD cost of the turn's LLM calls; 0 for a turn with no call, NULL when a"
                " call's model has no price", measurable=True),
            Col("priced", "bool", "false when some call's model has no price row (cost_usd is then NULL)",
                groupable=True),
        ),
    ),
    "llm_calls": Dataset(
        "llm_calls",
        "One row per LLM call the chatbot made (the parser on every turn, the clarifier on small talk), with"
        " tokens, latency and USD cost. chatbot_turn_id links a call to turn_costs.",
        "created_at",
        (
            Col("id", "text", "Usage log row id"),
            Col("created_at", "datetime", "When the call was logged (Malaysia time)"),
            Col("feature", "text", "What made the call: chatbot_parser or chatbot_clarifier", groupable=True),
            Col("chatbot_turn_id", "text", "Chatbot turn the call belongs to (turn_costs.id)", groupable=True),
            *_CONTACT_COLS,
            Col("provider", "text", "openai, anthropic or gemini", groupable=True),
            Col("model", "text", "Model name", groupable=True),
            Col("prompt_tokens", "int", "Input tokens. Includes cached tokens for OpenAI and Gemini, excludes them"
                " for Anthropic", measurable=True),
            Col("cached_tokens", "int", "Input tokens served from the prompt cache", measurable=True),
            Col("completion_tokens", "int", "Output tokens", measurable=True),
            Col("total_tokens", "int", "Total tokens as the provider reported them", measurable=True),
            Col("response_time_ms", "int", "Milliseconds the call took", measurable=True),
            Col("was_answered", "bool", "The model returned a usable answer", groupable=True),
            Col("cost_usd", "number", "USD cost from the model price table; NULL when the model has no price",
                measurable=True),
            Col("priced", "bool", "false when the model has no price row", groupable=True),
        ),
    ),
}


# ------------------------------------------------------------------ request models


class Filter(BaseModel):
    column: str
    op: Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null"]
    value: Any = None


class Measure(BaseModel):
    fn: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    column: Optional[str] = None


class OrderBy(BaseModel):
    column: str
    dir: Literal["asc", "desc"] = "asc"


class Period(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: Optional[date] = Field(default=None, alias="from")
    to: Optional[date] = None


class QuerySpec(BaseModel):
    dataset: str
    columns: list[str] = []
    filters: list[Filter] = []
    group_by: list[str] = []
    measures: list[Measure] = []
    period: Optional[Period] = None
    order_by: list[OrderBy] = []
    limit: int = Field(default=DEFAULT_ROWS, ge=1, le=MAX_ROWS)
    offset: int = Field(default=0, ge=0)


# ------------------------------------------------------------------ engine


# The one live read-only engine and its DSN. A changed DSN replaces it and the old pool is
# disposed (security review L2), so connections under an old password or host do not linger.
_ro_current: dict[str, Any] = {}
_ro_lock = threading.Lock()


def _ro_engine():
    """The read-only role's engine for the DSN saved on Settings > Integrations (env
    ANALYTICS_RO_DATABASE_URL as the fallback), read per call, so a changed DSN takes effect on
    the next call without a restart."""
    from app.services import runtime_settings

    url = runtime_settings.get("analytics_ro_database_url")
    with _ro_lock:
        current = _ro_current.get("engine")
        if current is not None and _ro_current.get("url") == url:
            return current
        _ro_current.clear()
        if current is not None:
            current.dispose()
        if not url:
            return None
        engine = create_engine(
            url,
            pool_size=2,
            max_overflow=3,
            pool_pre_ping=True,
            connect_args={
                "options": f"-c statement_timeout={int(STATEMENT_TIMEOUT[:-1]) * 1000} "
                "-c default_transaction_read_only=on -c timezone=utc"
            },
        )
        _ro_current.update(url=url, engine=engine)
        return engine


@contextmanager
def ro_connection(db: Any) -> Iterator[Any]:
    """A read-only handle on the views. With ``ANALYTICS_RO_DATABASE_URL`` it is the
    low-privilege role's own pooled connection. Without it (dev, CI) it is the request's session
    inside a savepoint that is rolled back, so the read-only flag and timeout never outlive the call."""
    engine = _ro_engine()
    if engine is not None:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
            try:
                yield conn
            finally:
                conn.rollback()
        return
    nested = db.begin_nested()
    try:
        db.execute(text("SET LOCAL transaction_read_only = on"))
        db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        yield db
    finally:
        nested.rollback()


# ------------------------------------------------------------------ builder


def _view(ds: Dataset):
    return table(ds.key, *[column(c.key) for c in ds.columns], schema=SCHEMA)


def _coerce(col: Col, value: Any) -> Any:
    try:
        if col.type == "int":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if col.type == "number":
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if not math.isfinite(number):
                raise ValueError
            return number
        if col.type == "bool":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "false"):
                return str(value).lower() == "true"
            raise ValueError
        if col.type == "datetime":
            return datetime.fromisoformat(str(value))
        return str(value)
    except (TypeError, ValueError):
        raise QueryError(f"bad value for {col.key!r}") from None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filter_clause(ds: Dataset, v, f: Filter):
    col = ds.col(f.column)
    if not col.filterable:
        raise QueryError(f"column {f.column!r} cannot be filtered")
    expr = v.c[col.key]
    op = f.op
    if op == "is_null":
        return expr.is_(None)
    if op == "not_null":
        return expr.is_not(None)
    if op in ("in", "not_in"):
        if not isinstance(f.value, list) or not f.value or len(f.value) > 500:
            raise QueryError(f"{op} needs a list of 1 to 500 values")
        values = [_coerce(col, x) for x in f.value]
        return expr.in_(values) if op == "in" else expr.not_in(values)
    if f.value is None:
        raise QueryError(f"{op} needs a value")
    if op == "contains":
        if col.type != "text":
            raise QueryError("contains works on text columns only")
        return expr.ilike(f"%{_escape_like(str(f.value))}%", escape="\\")
    value = _coerce(col, f.value)
    if op in ("gt", "gte", "lt", "lte") and col.type not in ("int", "number", "datetime"):
        raise QueryError(f"{op} works on numbers and dates only")
    return {
        "eq": expr == value, "ne": expr != value,
        "gt": expr > value, "gte": expr >= value, "lt": expr < value, "lte": expr <= value,
    }[op]


def _group_expr(ds: Dataset, v, key: str):
    base, _, bucket = key.partition(":")
    col = ds.col(base)
    if not col.groupable and not (bucket and col.type == "datetime"):
        raise QueryError(f"column {base!r} cannot be grouped")
    expr = v.c[col.key]
    if not bucket:
        return expr
    if col.type != "datetime" or bucket not in BUCKETS:
        raise QueryError(f"bad date bucket {key!r}")
    if bucket == "hour":
        return func.to_char(expr, "YYYY-MM-DD HH24:00")
    if bucket == "day":
        return func.to_char(expr, "YYYY-MM-DD")
    if bucket == "week":
        return func.to_char(func.date_trunc("week", expr), "YYYY-MM-DD")
    if bucket == "month":
        return func.to_char(expr, "YYYY-MM")
    if bucket == "dow":
        return sa_cast(func.extract("isodow", expr), Integer)
    return sa_cast(func.extract("hour", expr), Integer)


def _measure_expr(ds: Dataset, v, m: Measure) -> tuple[str, Any]:
    if m.fn == "count" and m.column is None:
        return "count", func.count()
    if m.column is None:
        raise QueryError(f"{m.fn} needs a column")
    col = ds.col(m.column)
    expr = v.c[col.key]
    label = f"{m.fn}:{col.key}"
    if m.fn == "count":
        return label, func.count(expr)
    if m.fn == "count_distinct":
        return label, func.count(distinct(expr))
    if m.fn in ("sum", "avg"):
        if not col.measurable:
            raise QueryError(f"{m.fn} needs a numeric column")
        return label, (func.sum(expr) if m.fn == "sum" else func.avg(expr))
    if not (col.measurable or col.type == "datetime"):
        raise QueryError(f"{m.fn} needs a numeric or date column")
    return label, (func.min(expr) if m.fn == "min" else func.max(expr))


def build(spec: QuerySpec, *, default_order: bool = True):
    """The Core statement for ``spec`` plus its output labels (limit + 1 rows, to detect more)."""
    ds = CATALOG.get(spec.dataset)
    if ds is None:
        raise QueryError(f"unknown dataset {spec.dataset!r}")
    v = _view(ds)
    where = [_filter_clause(ds, v, f) for f in spec.filters]
    if spec.period is not None:
        date_col = v.c[ds.date_column]
        if spec.period.from_ is not None:
            where.append(date_col >= datetime.combine(spec.period.from_, datetime.min.time()))
        if spec.period.to is not None:
            where.append(date_col < datetime.combine(spec.period.to, datetime.min.time()) + timedelta(days=1))

    named: dict[str, Any] = {}
    if spec.group_by or spec.measures:
        measures = list(spec.measures) or [Measure(fn="count")]
        groups = [(k, _group_expr(ds, v, k)) for k in spec.group_by]
        aggs = [_measure_expr(ds, v, m) for m in measures]
        named = dict(groups) | dict(aggs)
        stmt = select(*[e.label(f"c{i}") for i, e in enumerate(named.values())]).select_from(v)
        if groups:
            stmt = stmt.group_by(*[e for _, e in groups])
        order = [(named[o.column], o.dir) for o in spec.order_by if o.column in named]
        if len(order) != len(spec.order_by):
            raise QueryError("order_by must name an output column")
        if not order:
            order = [(e, "asc") for _, e in groups]
    else:
        keys = spec.columns or [c.key for c in ds.columns]
        named = {k: v.c[ds.col(k).key] for k in keys}
        stmt = select(*[e.label(f"c{i}") for i, e in enumerate(named.values())]).select_from(v)
        order = [(v.c[ds.col(o.column).key], o.dir) for o in spec.order_by]
        if not order and default_order:
            order = [(v.c[ds.date_column], "desc")]
        if order and ds.id_column:
            order.append((v.c[ds.id_column], "desc" if order[0][1] == "desc" else "asc"))
    for e, d in order:
        stmt = stmt.order_by(e.desc() if d == "desc" else e.asc())
    if where:
        stmt = stmt.where(*where)
    stmt = stmt.limit(spec.limit + 1).offset(spec.offset)
    return stmt, list(named.keys())


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _clip_row(row: list, limit: int) -> list:
    return [c[:limit] if isinstance(c, str) else c for c in row]


def fit_rows(columns: list[str], rows: list[list], extra: Optional[dict] = None) -> tuple[list[list], bool]:
    """The longest prefix of ``rows`` whose JSON stays inside ``MAX_OUTPUT_CHARS``.

    Sized with ``json.dumps`` defaults (the wider separators), so the real compact
    response is always smaller than the cap. Returns (rows, cut)."""
    head = {"columns": columns, "rows": [], "row_count": 0, "truncated": True, "next_offset": 99_999_999, **(extra or {})}
    used = len(json.dumps(head, default=str))
    kept: list[list] = []
    for row in rows:
        size = len(json.dumps(row, default=str)) + 2
        if used + size > MAX_OUTPUT_CHARS:
            if not kept:  # one row bigger than the cap: clip it so a page always makes progress
                kept.append(_clip_row(row, max((MAX_OUTPUT_CHARS - used) // max(len(row), 1) - 20, 20)))
            return kept, True
        kept.append(row)
        used += size
    return kept, False


def run_query(db: Any, spec: QuerySpec, *, default_order: bool = True, extra: Optional[dict] = None) -> dict:
    stmt, labels = build(spec, default_order=default_order)
    try:
        with ro_connection(db) as conn:
            raw = conn.execute(stmt).all()
    except DBAPIError as exc:
        if "statement timeout" in str(exc.orig).lower() or "canceling statement" in str(exc.orig).lower():
            raise QueryError("the query took too long; narrow the period or add filters") from None
        raise
    more = len(raw) > spec.limit
    rows = [[_json_safe(c) for c in r] for r in raw[: spec.limit]]
    rows, cut = fit_rows(labels, rows, extra)
    truncated = more or cut
    return {
        "columns": labels,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "next_offset": spec.offset + len(rows) if truncated else None,
    }


# ------------------------------------------------------------------ catalog tools


def list_datasets(db: Any) -> dict:
    out = []
    with ro_connection(db) as conn:
        for ds in CATALOG.values():
            v = _view(ds)
            d = v.c[ds.date_column]
            n, first, last = conn.execute(select(func.count(), func.min(d), func.max(d)).select_from(v)).one()
            out.append({
                "key": ds.key, "description": ds.description, "row_count": n,
                "first_date": _json_safe(first), "last_date": _json_safe(last), "date_column": ds.date_column,
            })
    return {"datasets": out}


def describe_dataset(db: Any, key: str) -> Optional[dict]:
    ds = CATALOG.get(key)
    if ds is None:
        return None
    sample = run_query(db, QuerySpec(dataset=key, limit=3), default_order=False)
    return {
        "key": ds.key,
        "description": ds.description,
        "date_column": ds.date_column,
        "columns": [
            {"key": c.key, "type": c.type, "description": c.description, "filterable": c.filterable,
             "groupable": c.groupable, "measurable": c.measurable}
            for c in ds.columns
        ],
        "group_by_buckets": [f"{ds.date_column}:{b}" for b in BUCKETS],
        "sample": {"columns": sample["columns"], "rows": sample["rows"]},
    }


# ------------------------------------------------------------------ message tools

_MESSAGE_COLS = [
    "id", "sent_at", "direction", "contact_name", "customer_name", "text", "reply_to_text", "delivery_status",
]


def _period(date_from: Optional[date], date_to: Optional[date]) -> Optional[Period]:
    return Period(**{"from": date_from, "to": date_to}) if (date_from or date_to) else None


def search_messages(
    db: Any, *, text_: Optional[str], contact: Optional[str], customer: Optional[str],
    direction: Optional[str], date_from: Optional[date], date_to: Optional[date], limit: int, offset: int,
) -> dict:
    filters = []
    if text_:
        filters.append(Filter(column="text", op="contains", value=text_))
    if contact:
        filters.append(Filter(column="contact_name", op="contains", value=contact))
    if customer:
        filters.append(Filter(column="customer_name", op="contains", value=customer))
    if direction:
        filters.append(Filter(column="direction", op="eq", value=direction))
    spec = QuerySpec(
        dataset="messages", columns=_MESSAGE_COLS, filters=filters, period=_period(date_from, date_to),
        order_by=[OrderBy(column="sent_at", dir="desc")], limit=limit, offset=offset,
    )
    return run_query(db, spec)


def resolve_contact(db: Any, ref: str) -> list[dict]:
    """Contacts matching ``ref``: an exact id or Respond.io id first, else a name search."""
    ref = ref.strip()
    cols = ["id", "respond_io_id", "name", "customer_name"]

    def find(filters: list[Filter]) -> list[dict]:
        res = run_query(db, QuerySpec(dataset="contacts", columns=cols, filters=filters, limit=20,
                                      order_by=[OrderBy(column="last_message_at", dir="desc")]))
        return [dict(zip(cols, r)) for r in res["rows"]]

    exact = find([Filter(column="id", op="eq", value=ref)]) or find([Filter(column="respond_io_id", op="eq", value=ref)])
    return exact or find([Filter(column="name", op="contains", value=ref)])


def get_conversation(db: Any, contact: dict, date_from: Optional[date], date_to: Optional[date],
                     limit: int, offset: int) -> dict:
    cols = ["id", "sent_at", "direction", "text", "reply_to_text", "chatbot_turn_id", "delivery_status"]
    spec = QuerySpec(
        dataset="messages", columns=cols,
        filters=[Filter(column="contact_id", op="eq", value=contact["id"])],
        period=_period(date_from, date_to), order_by=[OrderBy(column="sent_at", dir="asc")],
        limit=limit, offset=offset,
    )
    out = run_query(db, spec, extra={"contact": contact})
    # Bot turn status inline: one lookup for the turns the page mentions.
    idx = cols.index("chatbot_turn_id")
    turn_ids = sorted({r[idx] for r in out["rows"] if r[idx]})
    status_by_turn: dict[str, str] = {}
    if turn_ids:
        turns = run_query(db, QuerySpec(dataset="bot_turns", columns=["id", "status"],
                                        filters=[Filter(column="id", op="in", value=turn_ids)], limit=MAX_ROWS))
        status_by_turn = {r[0]: r[1] for r in turns["rows"]}
    out["columns"] = [*out["columns"], "turn_status"]
    out["rows"] = [[*r, status_by_turn.get(r[idx])] for r in out["rows"]]
    out["contact"] = contact
    return out
