"""The chatbot's sales report on the delivered basis (lane SALES-REPORT, PR #1401).

`documentation/plans/chatbot/PLAN-chatbot-selfref-scope-30sep.md` "Design note: one
dimension/measure model"; UAC `selfref-scope-acceptance-criteria.md` AC-SR-20 to AC-SR-26;
mock `documentation/mockups/sales-report/index.html`.

Built ON the reports engine, not beside it: every figure is `engine.run_summary` over the
`delivery_order_lines` dataset, one view per question (the periods: grain x `all`; a drill:
`group_by` x `all`). This module only decides which views to run and shapes the body
`GET /order-management/sales-report` returns; it never sums a line itself.

The engine reads an EMPTY select as "no filter" (`engine._predicates`), so every case whose
answer is "nothing" (a contact whose policy allows no location, a customer query that matches
nobody, codes that resolve to no warehouse) is answered HERE, before the engine runs, and
never handed to it as `[]`.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.order import Customer
from app.services.error_handler import handle_not_found
from app.services.outstanding_report_service import _customer_echo
from app.services.sales_report_service import (
    _LIKE_ESCAPE,
    _escape_like,
    _money_edge,
    _qty,
    _resolve_products,
)
from app.services.scm.demand_class import class_of

#: The `group_by` values the route accepts (AC-SR-23): the dataset dimensions a reply can
#: list. Any other value is 422 `unknown_group_by`.
GROUP_BYS = ("customer", "product", "delivery_order", "sales_agent")

#: Printed rows per drill; the rest is counted as `more`.
ROW_CAP = 10

#: The option labels, in the order the reply offers them (AC-SR-24).
OPTION_LABELS = (
    ("customer", "By customer"),
    ("product", "By product"),
    ("delivery_order", "Delivery orders"),
)

#: The route's channel vocabulary -> the dataset's (`demand_class.class_of`).
_CHANNEL_CLASS = {"dealer": "retail", "project": "project"}

#: The open ends of a window with one bound (or none): the engine's period is always closed.
_EARLIEST = date(1900, 1, 1)
_LATEST = date(2199, 12, 31)


def refusal_message(location: str) -> str:
    return f"Sorry, {location} isn't one of the locations you can check."


def grain_for(date_from: Optional[date], date_to: Optional[date]) -> str:
    """Day for a window of at most 7 days, week for at most 31, month otherwise (and for a
    window with an open end)."""
    if date_from is None or date_to is None:
        return "month"
    days = (date_to - date_from).days + 1
    if days <= 7:
        return "day"
    if days <= 31:
        return "week"
    return "month"


def _period_bounds(grain: str, key: str, date_from: Optional[date], date_to: Optional[date]) -> tuple[date, date]:
    """A period's first and last day, clipped to the window."""
    if grain == "day":
        start = end = date.fromisoformat(key)
    elif grain == "week":
        start = date.fromisoformat(key)
        end = start + timedelta(days=6)
    else:
        year, month = (int(p) for p in key.split("-"))
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
    if date_from is not None and start < date_from:
        start = date_from
    if date_to is not None and end > date_to:
        end = date_to
    return start, end


def _figures(totals: dict[str, Any]) -> dict[str, Any]:
    return {
        "qty": _qty(totals.get("qty")),
        "amount": _money_edge(totals.get("amount")),
    }


def _summary(db: Session, params: dict[str, Any], rows: str, cols: str = "all") -> Any:
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine
    from app.services.reports.datasets.delivery_order_lines import DEFINITION

    view = ReportViewConfig.model_validate(
        {
            "params": params,
            "detail": {"columns": [], "order": []},
            "pivot": {"rows": rows, "cols": cols, "measures": ["qty", "amount"]},
        }
    )
    return engine.run_summary(db, DEFINITION, params, view)


def _customer_ids_for_query(db: Session, customer_query: str) -> list[str]:
    pattern = f"%{_escape_like(customer_query)}%"
    return [
        str(row[0])
        for row in db.query(Customer.id)
        .filter(Customer.customer_name.ilike(pattern, escape=_LIKE_ESCAPE))
        .all()
    ]


def _segment_classes(db: Session, customer_ids: list[str]) -> set[str]:
    """The demand classes the scoped accounts span (an account with no segment adds none)."""
    rows = db.query(Customer.market_segment_code).filter(Customer.id.in_(customer_ids)).all()
    return {c for c in (class_of(row[0]) for row in rows) if c}


def _warehouse_ids_by_code(db: Session, codes: list[str]) -> dict[str, list[str]]:
    """Each named code's warehouse ids, case-insensitively (the order routes' own rule)."""
    rows = (
        db.query(Warehouse.id, Warehouse.warehouse_code)
        .filter(func.lower(Warehouse.warehouse_code).in_([c.lower() for c in codes]))
        .all()
    )
    out: dict[str, list[str]] = {c: [] for c in codes}
    for wid, code in rows:
        for named in codes:
            if str(code).lower() == named.lower():
                out[named].append(str(wid))
    return out


def _allowed(policy: Any, warehouse_id: str) -> bool:
    include = policy.warehouse_ids
    excluded = policy.excluded_warehouse_ids
    if include is not None and warehouse_id not in include:
        return False
    if excluded is not None and warehouse_id in excluded:
        return False
    return True


def _policy_location_ids(db: Session, policy: Any) -> Optional[list[str]]:
    """The locations an unqualified ask is capped to: None = no cap, a list = those."""
    include = policy.warehouse_ids
    excluded = policy.excluded_warehouse_ids or frozenset()
    if include is not None:
        return sorted(w for w in include if w not in excluded)
    if excluded:
        from app.models.base import company_scope

        with company_scope(db, None):
            every = [str(row[0]) for row in db.query(Warehouse.id).all()]
        return sorted(w for w in every if w not in excluded)
    return None


def resolve_locations(
    db: Session,
    named_codes: list[str],
    policy: Any,
    *,
    capped_by_policy: bool = True,
    token: Optional[str] = None,
) -> tuple[Optional[list[str]], Optional[str], bool]:
    """The one location-policy rule, shared by the sales report and the report ask.

    Returns (location ids or None for no cap, a refusal message or None, nothing). `nothing`
    means the answer is empty and the engine must not run (it reads `[]` as no filter). A
    named location outside the policy is a refusal; with `capped_by_policy` and no policy
    (nobody resolved) the answer is nothing (fail closed)."""
    if named_codes:
        by_code = _warehouse_ids_by_code(db, named_codes)
        if capped_by_policy:
            if policy is None:
                return None, None, True
            for named in named_codes:
                if any(not _allowed(policy, wid) for wid in by_code[named]):
                    return None, refusal_message(token or named), False
        ids = sorted({wid for found in by_code.values() for wid in found})
        return (ids, None, False) if ids else (None, None, True)
    if capped_by_policy:
        if policy is None:
            return None, None, True
        ids = _policy_location_ids(db, policy)
        if ids is not None and not ids:
            return None, None, True
        return ids, None, False
    return None, None, False


def _ranked(pivot: Any) -> list[tuple[str, Decimal, Decimal]]:
    """(name, qty, amount) per row value, by amount desc, qty desc, then name."""
    out = []
    for value in pivot.row_values:
        totals = pivot.row_totals.get(value) or {}
        out.append((value, Decimal(totals.get("qty") or 0), Decimal(totals.get("amount") or 0)))
    out.sort(key=lambda r: (-r[2], -r[1], r[0]))
    return out


def _do_rows(pivot: Any) -> list[tuple[str, str, Decimal, Decimal]]:
    """(number, date, qty, amount) per DO from a delivery_order x day pivot, latest first,
    then by number descending."""
    out = []
    for number in pivot.row_values:
        by_day = pivot.cells.get(number) or {}
        day = max(by_day) if by_day else ""
        totals = pivot.row_totals.get(number) or {}
        out.append((number, day, Decimal(totals.get("qty") or 0), Decimal(totals.get("amount") or 0)))
    out.sort(key=lambda r: (r[1], r[0]), reverse=True)
    return out


def delivered_sales_report(
    db: Session,
    *,
    product_code: Optional[str] = None,
    customer_query: Optional[str] = None,
    customer_ids: Optional[list[str]] = None,
    channel: Optional[str] = None,
    warehouse_codes: Optional[list[str]] = None,
    location_token: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    group_by: Optional[str] = None,
    policy: Any = None,
    capped_by_policy: bool = False,
) -> dict[str, Any]:
    """The route's body. `channel` is `dealer` / `project` / None, `group_by` one of
    `GROUP_BYS` or None (both validated by the route). `capped_by_policy` says a contact
    asked: `policy` is then that contact's stock visibility policy, and None (nobody
    resolved) answers nothing (fail closed)."""
    token = (location_token or "").strip() or None
    named_codes = [str(c).strip() for c in (warehouse_codes or []) if str(c).strip()]

    code = (product_code or "").strip()
    products = _resolve_products(db, code) if code else []
    if code and not products:
        raise handle_not_found("Product", product_code)
    product_ids = [str(p.id) for p in products]

    given_ids = [str(c).strip() for c in (customer_ids or []) if str(c).strip()]
    query = (customer_query or "").strip()
    if given_ids:
        scoped_ids: Optional[list[str]] = given_ids
    elif query:
        scoped_ids = _customer_ids_for_query(db, query)
    else:
        scoped_ids = None

    grain = grain_for(date_from, date_to)
    body: dict[str, Any] = {
        "status": "ok",
        "message": None,
        "basis": "delivered",
        "customer_name": _customer_echo(db, None if given_ids else (query or None), given_ids or None),
        "customer_count": len(scoped_ids) if scoped_ids is not None else 0,
        "product_code": code.upper() if code else None,
        "product_codes": sorted(p.product_code for p in products),
        "channel": None,
        "channel_shown": False,
        "location_token": token,
        "warehouse_codes": named_codes,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "grain": grain,
        "total": {"qty": 0, "amount": 0.0},
        "periods": [],
        "group_by": group_by,
        "rows": [],
        "more": 0,
        "options": [],
    }

    # ---------------------------------------------------------------- locations
    location_ids, refusal, nothing = resolve_locations(
        db, named_codes, policy, capped_by_policy=capped_by_policy, token=token
    )
    if refusal is not None:
        body["status"] = "refused"
        body["message"] = refusal
        return body
    if nothing:
        return body

    # ---------------------------------------------------------------- channel
    channel_used: Optional[str] = None
    if scoped_ids is not None:
        if not scoped_ids:
            return body
        spans = len(_segment_classes(db, scoped_ids)) > 1
        body["channel_shown"] = spans
        if spans and channel in _CHANNEL_CLASS:
            channel_used = channel
    elif channel in _CHANNEL_CLASS:
        body["channel_shown"] = True
        channel_used = channel
    body["channel"] = channel_used

    params: dict[str, Any] = {
        "date_basis": "order_date",
        "period": {
            "kind": "custom",
            "from": (date_from or _EARLIEST).isoformat(),
            "to": (date_to or _LATEST).isoformat(),
        },
    }
    if scoped_ids:
        params["customer"] = scoped_ids
    if product_ids:
        params["product"] = product_ids
    if location_ids is not None:
        params["location"] = location_ids
    if channel_used:
        params["channel"] = [_CHANNEL_CLASS[channel_used]]

    # ---------------------------------------------------------------- the periods
    periods_pivot = _summary(db, params, grain)
    periods = []
    for key in periods_pivot.row_values:
        start, end = _period_bounds(grain, key, date_from, date_to)
        periods.append({"from": start.isoformat(), "to": end.isoformat(),
                        **_figures(periods_pivot.row_totals.get(key) or {})})
    periods.sort(key=lambda p: p["from"], reverse=True)
    if not periods:
        return body
    body["periods"] = periods
    body["total"] = _figures(periods_pivot.grand_total)

    if scoped_ids is None:
        body["customer_count"] = len(_summary(db, params, "customer").row_values)
    several_accounts = body["customer_count"] > 1

    # ---------------------------------------------------------------- the drill
    product_count: Optional[int] = None
    if group_by == "delivery_order":
        ranked_dos = _do_rows(_summary(db, params, "delivery_order", "day"))
        names: dict[str, str] = {}
        if several_accounts:
            by_customer = _summary(db, params, "delivery_order", "customer")
            for number in by_customer.row_values:
                cells = by_customer.cells.get(number) or {}
                names[number] = ", ".join(sorted(cells))
        body["rows"] = [
            {
                "rank": i,
                "name": number,
                "qty": _qty(qty),
                "amount": _money_edge(amount),
                "date": day or None,
                "customer_name": names.get(number) if several_accounts else None,
            }
            for i, (number, day, qty, amount) in enumerate(ranked_dos[:ROW_CAP], start=1)
        ]
        body["more"] = max(len(ranked_dos) - ROW_CAP, 0)
    elif group_by:
        ranked = _ranked(_summary(db, params, group_by))
        if group_by == "product":
            product_count = len(ranked)
        body["rows"] = [
            {
                "rank": i,
                "name": name,
                "qty": _qty(qty),
                "amount": _money_edge(amount),
                "date": None,
                "customer_name": None,
            }
            for i, (name, qty, amount) in enumerate(ranked[:ROW_CAP], start=1)
        ]
        body["more"] = max(len(ranked) - ROW_CAP, 0)

    # ---------------------------------------------------------------- the options
    if product_count is None:
        product_count = len(_summary(db, params, "product").row_values)
    offered = {
        "customer": several_accounts,
        "product": product_count > 1,
        "delivery_order": True,
    }
    body["options"] = [
        {"key": key, "label": label}
        for key, label in OPTION_LABELS
        if offered[key] and key != group_by
    ]
    return body
