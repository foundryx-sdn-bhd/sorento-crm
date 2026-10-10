"""The chatbot's report ask: one catalogue, one executor (lane REPORT-ENGINE, slice 1a;
`documentation/plans/chatbot/PLAN-report-engine.md` section 10).

`CATALOGUE` maps the words a caller may use (a dimension to group by, a filter to apply) onto
each basis definition's own column keys and param keys, so the SQL stays in the dataset.
`run_ask` runs ONE `engine.run_summary` (rows = the group column or `all`, cols = `all`,
measures amount and qty), ranks the whole grouping in Python and cuts it to `top_n`.

The engine reads an EMPTY select as "no filter" (`engine._predicates`), so every filter that
resolves to nothing is answered HERE with zero rows, never handed to the engine as `[]`.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.reports.datasets import chat_asks, chat_report_ask, sales_order_lines_ask
from app.services.sales_report_delivered import _CHANNEL_CLASS, resolve_locations
from app.services.sales_report_service import _money_edge, _qty

#: The words a caller may group by and filter by. Every word is named the same as the column or
#: param key behind it, so one list each serves every basis (a word that differs from its key
#: earns a mapping then).
DIMENSIONS = ("customer", "product", "brand", "category", "sales_agent", "location", "channel", "month")
FILTERS = ("customer", "product", "brand", "category", "sales_agent", "customer_agent", "location", "channel")

#: The datasets a caller may ask over; the first is the default (a call naming none is sales).
DATASETS = ("sales", "chat")

#: The chat words (CA-3). Every word is named the same as the column or param key behind it.
CHAT_DIMENSIONS = (
    "topic", "subject", "product", "customer", "outcome", "failure_reason", "week", "month", "day", "language",
    "asker", "salesperson", "category", "brand",
)
CHAT_MEASURES = ("asks", "failed", "failure_rate", "contacts")
#: The dimensions that are a run of time: a trend lists every bucket, oldest first.
CHAT_TRENDS = frozenset({"week", "month", "day"})

#: Dataset -> basis -> entry. Sales: one entry per basis, every one over sales order lines.
#: Chat: `asks` counts every ask, `turns` each message once.
CATALOGUE: dict[str, dict[str, dict[str, Any]]] = {
    "sales": {
        basis: {
            "definition": sales_order_lines_ask.DEFINITIONS[basis],
            "label": label,
            "dimensions": DIMENSIONS,
            "filters": FILTERS,
        }
        for basis, label in (
            ("delivered", "delivered sales"),
            ("ordered", "ordered sales"),
            ("outstanding", "outstanding orders"),
        )
    },
    "chat": {
        basis: {
            "definition": chat_report_ask.DEFINITIONS[basis],
            "label": label,
            "dimensions": CHAT_DIMENSIONS,
            "filters": ("subject", "product", "customer", "asker", "category", "brand"),
        }
        for basis, label in (("asks", "asks"), ("turns", "turns"))
    },
}

#: What a customer-scoped contact (a dealer) may group or filter by (PLAN section 0, Q1/Q2).
DEALER_KEYS = frozenset({"product", "brand", "category", "month"})
CHAT_DEALER_KEYS = frozenset({"topic", "subject", "product", "month"})

GROUP_LABELS = {
    "customer": "Customer",
    "product": "Product",
    "brand": "Brand",
    "category": "Category",
    "sales_agent": "Sales agent",
    "location": "Location",
    "channel": "Channel",
    "month": "Month",
}

CHAT_GROUP_LABELS = {
    "topic": "Topic",
    "subject": "Subject",
    "product": "Product",
    "customer": "Customer",
    "outcome": "Outcome",
    "failure_reason": "Failure reason",
    "week": "Week",
    "month": "Month",
    "day": "Day",
    "language": "Language",
    "asker": "Asked by",
    "salesperson": "Salesperson",
    "category": "Category",
    "brand": "Brand",
}
CHAT_FILTER_LABELS = {
    "subject": "Subject", "product": "Product", "customer": "Customer", "asker": "Asked by",
    "category": "Category", "brand": "Brand",
}
#: The axes whose blank bucket (no reason, no customer, no salesperson, no brand) is not a row.
CHAT_NAMED_AXES = frozenset({"failure_reason", "customer", "salesperson", "brand", "category"})
#: The axes read through the item grain (one row per product an ask NAMED).
CHAT_ITEM_AXES = frozenset({"product", "category", "brand"})

#: The filter words' labels, as the route echoes them: the dimensions' own, plus the customer
#: card link a drill down uses (REPORT-DRILLDOWN, owner 8 Oct 2026), named so the reply says it.
FILTER_LABELS = {**GROUP_LABELS, "customer_agent": "Customers assigned to"}

NO_AGENT_KEY = sales_order_lines_ask.NO_AGENT_KEY

# ----------------------------------------------------------------------- drill down
#
# REPORT-DRILLDOWN (`documentation/plans/chatbot/PLAN-report-drilldown-08oct.md`): a follow-up
# ranking of the rows a ranking printed. Each printed row carries `keys`, the ids its name
# stands for, and a declared link per axis pair turns those keys into the follow-up's filter.

#: The route param each drill source's keys go to: the source row's own filter on the sales
#: order line. Channel and month are not sources (a channel or a month is named in words).
DRILL_PARAMS = {
    "sales_agent": "sales_agent_ids",
    "customer": "customer_ids",
    "product": "product_ids",
    "brand": "brand_ids",
    "category": "category_ids",
    "location": "warehouse_codes",
}

#: The pairs whose link is NOT the source's own filter. Agent -> customer is the customer card
#: (owner Q1, 8 Oct 2026): the customers assigned to those agents, whoever sold the order.
DRILL_LINKS = {("sales_agent", "customer"): "customer_agent_ids"}


def drill_param(source: str, target: str) -> Optional[str]:
    """The route param a drill from `source` rows to a `target` ranking filters by; None when
    `source` is not a drill source."""
    return DRILL_LINKS.get((source, target)) or DRILL_PARAMS.get(source)


def _row_keys(db: Session, group_by: str, names: list[str], company_grants: Any) -> dict[str, list[str]]:
    """The ids each printed name stands for, inside the contact's companies (a name shared by
    several rows, one brand per company, is one row and the union of their ids). The
    "(no agent)" row is `NO_AGENT_KEY`; a location's key is its code."""
    from app.models.base import company_scope
    from app.models.order import Customer
    from app.models.product import Brand, Product, ProductCategory
    from app.models.sales_agent import SalesAgent
    from app.services.reports.datasets.delivery_order_lines import NO_AGENT

    from app.services.reports.engine import BLANK_VALUE

    if group_by == "location":
        # "(blank)" is lines with no location: no code to narrow by (the lane says so).
        return {n: ([] if n == BLANK_VALUE else [n]) for n in names}
    lookups = {
        "sales_agent": (SalesAgent, SalesAgent.sales_agent),
        "customer": (Customer, Customer.customer_name),
        "product": (Product, Product.product_code),
        "brand": (Brand, Brand.brand_name),
        "category": (ProductCategory, ProductCategory.category_name),
    }
    if group_by not in lookups:
        return {}
    model, name_column = lookups[group_by]
    out: dict[str, list[str]] = {n: [] for n in names}
    if group_by == "sales_agent" and NO_AGENT in out:
        out[NO_AGENT] = [NO_AGENT_KEY]
    grants = sorted(str(c) for c in company_grants or ())
    wanted = [n for n in names if not out[n]]
    if wanted and grants:
        with company_scope(db, None):
            rows = (
                db.query(model.id, name_column)
                .filter(name_column.in_(wanted), (model.company_id.in_(grants)) | (model.company_id.is_(None)))
                .all()
            )
        for row_id, name in rows:
            out[name].append(str(row_id))
    return {n: sorted(set(ids)) for n, ids in out.items()}


#: The delivered dataset's raw channel values, as the ordered dataset already prints them.
_CHANNEL_NAMES = {"retail": "Dealer", "project": "Project team"}

MEASURES = ("amount", "qty")


def _empty(status: str = "ok", message: Optional[str] = None) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "rows": [],
        "more": 0,
        "total_count": 0,
        "total": {"qty": 0, "amount": 0.0},
    }


def run_ask(
    db: Session,
    *,
    dataset: str = "sales",
    basis: str,
    measure: str,
    group_by: Optional[str],
    date_from: date,
    date_to: date,
    filters: dict[str, list[str]],
    warehouse_codes: list[str],
    policy: Any,
    company_grants: Any = None,
    sort: str = "desc",
    top_n: Optional[int] = None,
    scope_customers: Optional[list[str]] = None,
) -> dict[str, Any]:
    """The ranked rows, their cut and the whole set's total. `filters` maps a spec word to
    RESOLVED ids (channel: `dealer` / `project`); `warehouse_codes` are the raw codes named.
    `company_grants` is the contact's own company ids (fail closed: none, no rows)."""
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine

    if dataset == "chat":
        return _run_chat(
            db, basis=basis, measure=measure, group_by=group_by, date_from=date_from, date_to=date_to,
            filters=filters, sort=sort, top_n=top_n, scope_customers=scope_customers,
        )
    entry = CATALOGUE["sales"][basis]
    definition = entry["definition"]

    params: dict[str, Any] = {
        "date_basis": "order_date",
        "period": {"kind": "custom", "from": date_from.isoformat(), "to": date_to.isoformat()},
    }
    for word, values in filters.items():
        if not values:
            return _empty()
        if word == "channel":
            values = [_CHANNEL_CLASS[v] for v in values]
        params[word] = list(values)

    location_ids, refusal, nothing = resolve_locations(db, warehouse_codes, policy)
    if refusal is not None:
        return _empty("refused", refusal)
    if nothing:
        return _empty()
    if location_ids is not None:
        params["location"] = location_ids

    # The engine refuses rows == cols, so the one-total shape groups by month and reads the
    # grand total only.
    rows_key = group_by or "month"
    view = ReportViewConfig.model_validate(
        {
            "params": params,
            "detail": {"columns": [], "order": []},
            "pivot": {"rows": rows_key, "cols": "all", "measures": list(MEASURES)},
        }
    )
    pivot = engine.run_summary(
        db, definition, params, view, company_grants=frozenset(str(c) for c in company_grants or ())
    )

    grand = pivot.grand_total or {}
    body = _empty()
    body["total"] = {"qty": _qty(grand.get("qty")), "amount": _money_edge(grand.get("amount"))}
    if not group_by:
        return body

    ranked = []
    for value in pivot.row_values:
        totals = pivot.row_totals.get(value) or {}
        qty, amount = Decimal(totals.get("qty") or 0), Decimal(totals.get("amount") or 0)
        if not qty and not amount:
            continue
        name = _CHANNEL_NAMES.get(value, value) if group_by == "channel" else value
        ranked.append((name, qty, amount))

    sign = -1 if sort == "desc" else 1
    first, second = (2, 1) if measure == "amount" else (1, 2)
    ranked.sort(key=lambda r: (sign * r[first], sign * r[second], r[0]))

    shown = ranked[:top_n] if top_n else ranked
    keys = _row_keys(db, group_by, [name for name, _q, _a in shown], company_grants)
    body["rows"] = [
        {"rank": i, "name": name, "qty": _qty(qty), "amount": _money_edge(amount), "keys": keys.get(name, [])}
        for i, (name, qty, amount) in enumerate(shown, start=1)
    ]
    body["total_count"] = len(ranked)
    body["more"] = len(ranked) - len(shown)
    return body


# ----------------------------------------------------------------------- chat (CA-3)

NO_ASKS = f"No asks in that period. Chat data starts {chat_asks.TRACES_BEGIN.day} Sep 2026."
NO_MATCH = "No asks match those filters in that period."


def _chat_value(measure: str, totals: dict) -> Optional[dict[str, Any]]:
    """One row's (or the total's) figures, or None for a row that prints nothing: a rate over
    no turns, or a count of nothing."""
    if measure == "failure_rate":
        num, den = int(Decimal(totals.get("failed") or 0)), int(Decimal(totals.get("asks") or 0))
        if not den:
            return None
        pct = (Decimal(num) * 100 / Decimal(den)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return {"value": float(pct), "numerator": num, "denominator": den}
    value = int(Decimal(totals.get(measure) or 0))
    return {"value": value} if value else None


def _none_found(
    db: Session, definition: Any, period: dict, filters: dict[str, list[str]], scope_customers: Any = None
) -> str:
    """Nothing to show: the period holds asks but the filters match none, or the period is empty.
    "Holds asks" is read inside the caller's own scope, never across other customers."""
    if filters and chat_report_ask.period_has_asks(db, definition, period, scope_customers):
        return NO_MATCH
    return NO_ASKS


def _run_chat(
    db: Session,
    *,
    basis: str,
    measure: str,
    group_by: Optional[str],
    date_from: date,
    date_to: date,
    filters: dict[str, list[str]],
    sort: str,
    top_n: Optional[int],
    scope_customers: Optional[list[str]] = None,
) -> dict[str, Any]:
    """The chat dataset's ranked rows, their cut and the whole set's total, with the coverage
    of topic labels. The period opens no earlier than the traces do."""
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine

    date_from = max(date_from, chat_asks.TRACES_BEGIN)
    body: dict[str, Any] = {
        "status": "ok",
        "message": None,
        "rows": [],
        "more": 0,
        "total_count": 0,
        "total": {"value": 0},
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "coverage": {"percent": 0, "taxonomy_version": None, "line": chat_asks.coverage_line(0, None)},
    }
    definition = CATALOGUE["chat"][basis]["definition"]
    params: dict[str, Any] = {
        "date_basis": "asked_at",
        "period": {"kind": "custom", "from": date_from.isoformat(), "to": date_to.isoformat()},
    }
    asker_split = group_by in ("asker", "salesperson") or "asker" in filters
    body["unclassified_askers"] = 0 if asker_split else None
    for word, values in filters.items():
        if not values:  # a filter resolving to nothing is zero rows, never "no filter"
            body["message"] = _none_found(db, definition, params["period"], filters, scope_customers)
            return body
        params[word] = list(values)
    unclassified = (
        chat_report_ask.unclassified_contacts(db, definition, params) if asker_split else None
    )
    body["unclassified_askers"] = unclassified
    body["unclassified_left_out"] = None
    if "asker" not in params:
        # The default asker filter (read now, so one switch changes it): staff and unclassified
        # out. A salesperson ranking is of salespeople only.
        params["asker"] = ["salesperson"] if group_by == "salesperson" else list(chat_report_ask.DEFAULT_ASKERS)
        # Only a default that itself leaves unclassified out says so (the salesperson axis narrows to
        # salespeople by its nature, which is not a default exclusion).
        if "unclassified" not in chat_report_ask.DEFAULT_ASKERS:
            # How many unclassified contacts the default left out, under the other filters.
            body["unclassified_left_out"] = chat_report_ask.unclassified_contacts(db, definition, params)
    coverage_ctx = engine.resolve(db, definition, params)
    percent, version = chat_asks.topics_coverage(coverage_ctx)
    body["coverage"] = {
        "percent": percent, "taxonomy_version": version, "line": chat_asks.coverage_line(percent, version, ctx=coverage_ctx)
    }
    if date_to < date_from:
        body["message"] = NO_ASKS
        return body

    if group_by in CHAT_ITEM_AXES:
        params["grain"] = ["products"]
    measures = list(dict.fromkeys([measure, "asks", *(("failed",) if measure == "failure_rate" else ())]))
    view = ReportViewConfig.model_validate(
        {
            "params": params,
            "detail": {"columns": [], "order": []},
            "pivot": {
                # A customer axis groups by customer (name and id), so two customers sharing a
                # name stay two rows; the printed name is the part before the separator.
                "rows": "customer_ref" if group_by == "customer" else (group_by or "month"),
                "cols": "all",
                "measures": measures,
            },
        }
    )
    pivot = engine.run_summary(db, definition, params, view)

    grand = pivot.grand_total or {}
    if not int(Decimal(grand.get("asks") or 0)):
        body["message"] = _none_found(db, definition, params["period"], filters, scope_customers)
        return body
    body["total"] = _chat_value(measure, grand) or {"value": 0}
    if not group_by:
        return body

    ranked = []
    for name in pivot.row_values:
        if group_by in CHAT_NAMED_AXES and name == engine.BLANK_VALUE:
            continue  # no row for "nobody": only a failed ask has a reason, only some asks name a customer, a brand
        figures = _chat_value(measure, pivot.row_totals.get(name) or {})
        if figures is not None:
            ranked.append((name, figures))
    sign = -1 if sort == "desc" else 1
    if group_by in CHAT_TRENDS and top_n is None:
        ranked.sort(key=lambda r: r[0])
    else:
        ranked.sort(key=lambda r: (sign * r[1]["value"], r[0]))
    shown = ranked[:top_n] if top_n else ranked
    if group_by == "customer":
        refs = [name.partition(chat_report_ask.CUSTOMER_SEP) for name, _f in shown]
        agents = chat_report_ask.card_agents(db, [ref[2] for ref in refs])
        body["rows"] = [
            {"rank": i, "name": ref[0], **figures, "agent": agents.get(ref[2])}
            for i, ((_n, figures), ref) in enumerate(zip(shown, refs), start=1)
        ]
    else:
        body["rows"] = [{"rank": i, "name": name, **figures} for i, (name, figures) in enumerate(shown, start=1)]
    body["total_count"] = len(ranked)
    body["more"] = len(ranked) - len(shown)
    return body
