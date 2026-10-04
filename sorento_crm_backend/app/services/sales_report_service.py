"""Sales report: confirmed vs outstanding sales, by month.

`documentation/plans/chatbot/PLAN-chatbot-sales-report.md` ("Backend contract");
`documentation/plans/chatbot/chatbot-sales-report-acceptance-criteria.md`
AC-1620 to AC-1632, rulings S15 to S20 (S19: 19 Sep 2026 live-testing fix
round - `product_code` is now a PREFIX, `_resolve_products`, not a single
exact match; S20: same day, second live-testing round - a product filter
covering 2+ codes ALSO wants `by_product`, even alongside a named customer,
so `by_product` and `by_customer` are no longer mutually exclusive).

AGGREGATED IN SQL, not rolled up from raw rows in Python (SEC-B2/ruling S17): a
big dealer is 1,230 SOs over 37 months (UAC "Measured"), thousands of lines - too
many to read into the request process for one WhatsApp reply. Grouped queries,
each `GROUP BY`, so a total can never drift from the SAME per-line SQL
expressions:

* `_breakdown_query` - one row per (month, breakdown key), called ONCE PER
  breakdown key wanted (S20: up to two, `by_product` and `by_customer`, never
  materialising raw lines to build both from one pass). Neither wanted (both
  subjects named, one covered code) falls back to a single query grouped by
  month alone. Month TOTALS are the Python sum of whichever breakdown ran
  first - summing already-computed SQL sums, not re-deriving them from raw
  lines, and never drifting between the two breakdowns because `product_id`
  is NOT NULL/FK-RESTRICT (the `by_product` query's inner join to `Product`
  drops no row the `by_customer` query would otherwise have kept).
* `_so_count_query` - one row per month, `COUNT(DISTINCT sales_order_id)`. A
  SEPARATE query because a DISTINCT count cannot be summed from the breakdown
  rows above (the same SO can carry lines under more than one product/customer
  in the same month, and `so_count` must still count it once).
* `_so_rows_query` - one row per SO, computed ONLY when `detail == "so"`
  (a big dealer is 1,230 SOs, never built unasked).

Per line (S2, S15, S16 rulings), expressed as SQL so every aggregate is a SUM of
figures already at cent precision, never an exact fraction rounded once at the
end:

* `bucket` = `required_date`, else the SO's `order_date` (S3). A line with
  NEITHER is excluded from EVERYTHING - not just `months[]` but `so_rows[]` too
  (S16) - via a `WHERE bucket IS NOT NULL`, no further fallback to `created_at`.
* `confirmed_qty = LEAST(qty_delivered, qty_ordered)` - never more than ordered.
* `confirmed_value = ROUND(line_total * confirmed_qty / NULLIF(qty_ordered, 0), 2)`,
  0 when `qty_ordered` is 0 or `line_total` is NULL (never a division error) -
  rounded to the cent PER LINE (S15), so every total is a SUM of cents.
* `outstanding_qty` / `outstanding_value` are the REST of the line, counted
  ONLY while the header is `status='open'` AND the line is `line_status='open'`
  - a closed-but-underdelivered line (a data anomaly, not a real one in this
  schema) simply reports less than its `line_total`, rather than raising.
* `ordered = confirmed + outstanding`, by construction (S2).

Company scoping still applies: every query below is a plain ORM query over the
mapped, company-scoped classes (`SalesOrder`, `SalesOrderLine`, ...), so the
`do_orm_execute` scope listener injects its own criteria the same way a raw
Python-rollup query would have - never raw text SQL, which that listener cannot
see.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, literal_column, or_
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Brand, Product, ProductCategory
from app.models.sales_agent import SalesAgent
from app.services.error_handler import handle_not_found
from app.services.order_service import resolve_warehouse_ids
from app.services.outstanding_report_service import _customer_echo

DateLike = Optional[datetime]

# Same reasoning outstanding_report_service.py gives for its own copy: the
# canonical escaper lives on `product_code_resolution.py` but is module-private,
# so a tiny local copy beats reaching across modules for five lines of LIKE
# escaping.
_LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def _dec(v: Any) -> Decimal:
    if v is None:
        return Decimal(0)
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _qty(v: Any) -> int:
    """Every quantity on this report is a WHOLE unit (the schema declares `int`
    and the reply prints thousands-separated units), but `qty_ordered` /
    `qty_delivered` are `Numeric(15,4)`, so a fraction is storable. Rounded HALF
    UP - never Python's `round`, whose bankers' rounding would print 2 for 2.5
    and 4 for 3.5 in the same reply."""
    return int(_dec(v).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _money_edge(v: Any) -> float:
    """Quantise to 2 places AT THE RESPONSE EDGE and hand back a `float`, so it
    serialises as a JSON NUMBER (captain ruling, S2 fix round) - a `Decimal`
    field serialises as a STRING through `model_dump(mode="json")`, which is
    not the contract. Every internal accumulation before this point stays
    `Decimal` (SEC-B2/S15: the SQL layer already rounded `confirmed_value` to
    the cent PER LINE, so this is only ever formatting a SUM of already-quantised
    cents, never a fresh rounding decision)."""
    return float(_dec(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _as_date(v: DateLike) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    return v


def _resolve_products(db: Session, product_code: Optional[str]) -> list[Product]:
    """S19 (owner ruling from live testing, 19 Sep 2026): `product_code` matches
    the typed code AND every product whose code STARTS WITH it, case-
    insensitively - "Srt5674 August total sale quantity" answered "No sales
    found." because every August sale sat on the sibling SRT5674-N. The
    OUTSTANDING report keeps its own exact-code rule (AC-1119) and does not
    share this helper. LIKE metacharacters in the typed code are escaped
    (`_escape_like`, reused from the exact-match rule this replaces) so a
    literal `%` or `_` in what the customer typed can never wildcard-match."""
    code = (product_code or "").strip()
    if not code:
        return []
    pattern = _escape_like(code.lower()) + "%"
    return (
        db.query(Product)
        .filter(func.lower(Product.product_code).like(pattern, escape=_LIKE_ESCAPE))
        .all()
    )


def _rank_breakdown(rows: list[dict], *, name_key: str) -> list[dict]:
    """S6: ranked by `ordered_value` descending, ties by `ordered_qty`
    descending, then name ascending. Sorted HERE, once - the presenter's
    contract is "print them in the order given" (AC-1608), never re-sort."""
    return sorted(
        rows,
        key=lambda r: (
            -r["ordered_value"],
            -r["ordered_qty"],
            str(r.get(name_key) or ""),
        ),
    )


def _new_figures() -> dict:
    return {
        "ordered_qty": Decimal(0), "ordered_value": Decimal(0),
        "confirmed_qty": Decimal(0), "confirmed_value": Decimal(0),
        "outstanding_qty": Decimal(0), "outstanding_value": Decimal(0),
    }


def _accumulate(acc: dict, row) -> None:
    """Sums figures that are ALREADY SQL sums of per-line, cent-rounded values
    (S15) - never a fresh rounding decision, only Decimal addition."""
    acc["ordered_qty"] += _dec(row.ordered_qty)
    acc["ordered_value"] += _dec(row.ordered_value)
    acc["confirmed_qty"] += _dec(row.confirmed_qty)
    acc["confirmed_value"] += _dec(row.confirmed_value)
    acc["outstanding_qty"] += _dec(row.outstanding_qty)
    acc["outstanding_value"] += _dec(row.outstanding_value)


def _quantised_figures(acc: dict) -> dict:
    return {
        "ordered_qty": _qty(acc["ordered_qty"]), "ordered_value": _money_edge(acc["ordered_value"]),
        "confirmed_qty": _qty(acc["confirmed_qty"]), "confirmed_value": _money_edge(acc["confirmed_value"]),
        "outstanding_qty": _qty(acc["outstanding_qty"]), "outstanding_value": _money_edge(acc["outstanding_value"]),
    }


def _month_key(dt) -> str:
    d = _as_date(dt)
    return f"{d.year:04d}-{d.month:02d}"


def _bucket_expr():
    """S3/S16: a line's own `required_date`, else its SO's `order_date`. NO
    further fallback to `created_at` - a line with neither is excluded from
    everything by the `IS NOT NULL` filter this expression feeds."""
    return func.coalesce(SalesOrderLine.required_date, SalesOrder.order_date)


def _per_line_exprs():
    """The S2/S15 per-line figures, as SQL expressions - `confirmed_value`
    rounded to the cent HERE, per line, so every aggregate downstream is a SUM
    of cents rather than an exact fraction rounded once at the end."""
    confirmed_qty = func.least(SalesOrderLine.qty_delivered, SalesOrderLine.qty_ordered)
    line_total = func.coalesce(SalesOrderLine.line_total, 0)
    confirmed_value = func.coalesce(
        func.round(
            (line_total * confirmed_qty) / func.nullif(SalesOrderLine.qty_ordered, 0),
            2,
        ),
        0,
    )
    is_open = and_(SalesOrder.status == "open", SalesOrderLine.line_status == "open")
    outstanding_qty = case((is_open, SalesOrderLine.qty_ordered - confirmed_qty), else_=0)
    outstanding_value = case((is_open, line_total - confirmed_value), else_=0)
    ordered_qty = confirmed_qty + outstanding_qty
    ordered_value = confirmed_value + outstanding_value
    return {
        "confirmed_qty": confirmed_qty, "confirmed_value": confirmed_value,
        "outstanding_qty": outstanding_qty, "outstanding_value": outstanding_value,
        "ordered_qty": ordered_qty, "ordered_value": ordered_value,
    }


def _figure_sum_labels(exprs: dict) -> list:
    """`func.sum(...)` over each per-line expression - every aggregate below is a
    SUM of figures already at cent precision (S15), computed by the database."""
    return [
        func.sum(exprs["ordered_qty"]).label("ordered_qty"),
        func.sum(exprs["ordered_value"]).label("ordered_value"),
        func.sum(exprs["confirmed_qty"]).label("confirmed_qty"),
        func.sum(exprs["confirmed_value"]).label("confirmed_value"),
        func.sum(exprs["outstanding_qty"]).label("outstanding_qty"),
        func.sum(exprs["outstanding_value"]).label("outstanding_value"),
    ]


def _common_filters(
    *, product_ids, customer_query, customer_ids, channel, warehouse_ids, date_from, date_to,
    bucket_expr,
) -> list:
    filters = [
        SalesOrder.status != "cancelled",
        SalesOrderLine.line_status != "cancelled",
        # S16: a line bucketed by neither required_date nor order_date is
        # excluded from EVERYTHING, not just months[] - never falls back to
        # created_at.
        bucket_expr.isnot(None),
    ]
    if product_ids:
        # S19: the product SUBJECT is now a SET (the typed code plus every
        # sibling whose code starts with it) - filtered by id, resolved once
        # up front in `_resolve_products`, so this stays a plain IN() and no
        # query below needs its own Product join just to filter.
        filters.append(SalesOrderLine.product_id.in_(product_ids))
    if customer_query:
        filters.append(
            Customer.customer_name.ilike(
                f"%{_escape_like(customer_query)}%", escape=_LIKE_ESCAPE
            )
        )
    if customer_ids is not None:
        filters.append(SalesOrder.customer_id.in_(customer_ids))
    if warehouse_ids is not None:
        filters.append(SalesOrderLine.warehouse_id.in_(warehouse_ids))
    if channel == "dealer":
        filters.append(SalesOrder.demand_class == "retail")
    elif channel == "project":
        filters.append(SalesOrder.demand_class == "project")
    if date_from is not None:
        filters.append(bucket_expr >= _as_date(date_from))
    if date_to is not None:
        filters.append(bucket_expr <= _as_date(date_to))
    return filters


def sales_report(
    db: Session,
    *,
    product_code: Optional[str] = None,
    customer_query: Optional[str] = None,
    customer_ids: Optional[list[str]] = None,
    channel: Optional[str] = None,
    warehouse_codes: Optional[list[str]] = None,
    date_from: DateLike = None,
    date_to: DateLike = None,
    detail: Optional[str] = None,
) -> dict:
    """S7: the SUBJECT is a product, a customer, or both - never neither (the
    ROUTE raises 422 `subject_required` before calling this). `channel` is
    already normalised to `"dealer"` / `"project"` / `None` by the route (S8);
    `detail` is `"so"` or `None` - `so_rows` is computed ONLY when it is `"so"`
    (captain ruling: a big dealer is 1,230 SOs, never build that unasked).

    S19: `product_code` is a PREFIX - `_resolve_products` returns every
    product whose code starts with it, case-insensitively (the typed code
    itself included, since every code "starts with" itself). 404 only when
    NOTHING starts with it (the route's own 422 `product_code_too_short`
    guards the pathological single-character case before this ever runs)."""
    product_code_stripped = (product_code or "").strip()
    matched_products = _resolve_products(db, product_code_stripped) if product_code_stripped else []
    if product_code_stripped and not matched_products:
        raise handle_not_found("Product", product_code)
    product_ids = [p.id for p in matched_products]

    warehouse_ids = resolve_warehouse_ids(db, warehouse_codes)
    customer_ids = [str(c).strip() for c in (customer_ids or []) if str(c).strip()] or None
    has_customer = bool(customer_ids) or bool((customer_query or "").strip())
    has_product = bool(matched_products)
    # S6/AC-1628, extended by S20 (owner ruling, live testing 19 Sep 2026): a
    # customer subject alone still wants By product; a product subject alone
    # still wants By customer, REGARDLESS of how many codes it covers - the
    # extension is that a product filter covering 2+ codes (the S19 family)
    # ALSO wants By product, even alongside a named customer. Only a
    # customer+product ask whose product covers exactly one code keeps the
    # original "no breakdown at all" shape.
    #   customer only                       -> by_product
    #   product only, 1 covered code        -> by_customer
    #   product only, 2+ covered codes      -> by_product AND by_customer
    #   customer + product, 1 covered code  -> neither
    #   customer + product, 2+ covered codes -> by_product only
    want_by_product = (has_customer and not has_product) or (
        has_product and len(matched_products) >= 2
    )
    want_by_customer = has_product and not has_customer
    want_so_rows = (detail or "").strip().lower() == "so"

    bucket_expr = _bucket_expr()
    filters = _common_filters(
        product_ids=product_ids, customer_query=customer_query, customer_ids=customer_ids,
        channel=channel, warehouse_ids=warehouse_ids, date_from=date_from, date_to=date_to,
        bucket_expr=bucket_expr,
    )
    figure_exprs = _per_line_exprs()
    month_expr = func.date_trunc("month", bucket_expr)

    # ----------------------------------------------------------------- months + breakdown
    # ONE grouped query PER breakdown key wanted (S20: up to two - `by_product` and
    # `by_customer` can both be wanted at once, a product filter covering 2+ codes
    # alongside a named customer, or a product-only ask over a family), never a
    # single query materialising raw lines. Neither wanted -> ONE query grouped by
    # month alone. Month TOTALS are the Python sum of whichever breakdown ran first
    # (both tally to the cent - S20 - `product_id` is NOT NULL/FK-RESTRICT, so the
    # `by_product` query's inner join to `Product` never drops a row the
    # `by_customer` query would otherwise have kept).
    def _breakdown_query(breakdown_expr, *, needs_product_join: bool) -> dict[str, dict[str, dict]]:
        q = (
            db.query(
                month_expr.label("month_dt"),
                breakdown_expr.label("breakdown_key"),
                *_figure_sum_labels(figure_exprs),
            )
            .select_from(SalesOrderLine)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
        )
        if needs_product_join:
            q = q.join(Product, Product.id == SalesOrderLine.product_id)
        q = q.filter(*filters).group_by(month_expr, breakdown_expr)
        acc: dict[str, dict[str, dict]] = {}
        for row in q.all():
            month_key = _month_key(row.month_dt)
            fig = acc.setdefault(month_key, {}).setdefault(row.breakdown_key, _new_figures())
            _accumulate(fig, row)
        return acc

    by_product_acc: dict[str, dict[str, dict]] = {}
    by_customer_acc: dict[str, dict[str, dict]] = {}
    primary_acc: Optional[dict[str, dict[str, dict]]] = None
    if want_by_product:
        by_product_acc = _breakdown_query(Product.product_code, needs_product_join=True)
        primary_acc = by_product_acc
    if want_by_customer:
        by_customer_acc = _breakdown_query(Customer.customer_name, needs_product_join=False)
        if primary_acc is None:
            primary_acc = by_customer_acc

    months_acc: dict[str, dict] = {}
    if primary_acc is not None:
        for month_key, keyed in primary_acc.items():
            totals = months_acc.setdefault(month_key, _new_figures())
            for fig in keyed.values():
                for k in totals:
                    totals[k] += fig[k]
    else:
        q1 = (
            db.query(month_expr.label("month_dt"), *_figure_sum_labels(figure_exprs))
            .select_from(SalesOrderLine)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
            .filter(*filters)
            .group_by(month_expr)
        )
        for row in q1.all():
            fig = months_acc.setdefault(_month_key(row.month_dt), _new_figures())
            _accumulate(fig, row)

    # ----------------------------------------------------------------- so_count per month
    # A SEPARATE query (a DISTINCT count cannot be summed from the breakdown rows
    # above: the same SO can carry lines under more than one product/customer in
    # the same month, and so_count must still count it once).
    q2 = (
        db.query(month_expr.label("month_dt"), func.count(func.distinct(SalesOrder.id)).label("so_count"))
        .select_from(SalesOrderLine)
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
    )
    if want_by_product:
        q2 = q2.join(Product, Product.id == SalesOrderLine.product_id)
    q2 = q2.filter(*filters).group_by(month_expr)
    so_counts = {_month_key(r.month_dt): int(r.so_count or 0) for r in q2.all()}

    months: list[dict] = []
    for month_key in sorted(months_acc.keys(), reverse=True):
        entry = {
            "month": month_key,
            "so_count": so_counts.get(month_key, 0),
            **_quantised_figures(months_acc[month_key]),
        }
        if want_by_product:
            rows = [
                {"product_code": code, **_quantised_figures(fig)}
                for code, fig in by_product_acc.get(month_key, {}).items()
            ]
            entry["by_product"] = _rank_breakdown(rows, name_key="product_code")
        if want_by_customer:
            rows = [
                {"customer_name": name, **_quantised_figures(fig)}
                for name, fig in by_customer_acc.get(month_key, {}).items()
            ]
            entry["by_customer"] = _rank_breakdown(rows, name_key="customer_name")
        months.append(entry)

    # ------------------------------------------------------- product_codes (S19,
    # second fix round: owner ruling from live testing, 19 Sep 2026 - "why it says
    # SRT5674 (SRT5674-N) so weird, it should just be comma separated"). The header
    # echoes every code the typed stem COVERS - the family `_resolve_products`
    # already found, still company-scoped by that query's own scope listener -
    # NOT only the codes that happen to have a row in this filtered report. A
    # covered code with zero rows in this window (e.g. no sales at all, or none
    # inside a narrowed date/channel/warehouse filter) still belongs to the
    # family the customer's typed prefix names, so it still appears here; the
    # per-SO `product_codes` on a `so_rows` row (below) is a DIFFERENT thing -
    # the codes actually on that one SO - and is unaffected.
    product_codes: list[str] = sorted(p.product_code for p in matched_products)

    # ----------------------------------------------------------------- so_rows (detail=so)
    so_rows: Optional[list[dict]] = None
    if want_so_rows:
        # S9-shaped, S15's own instruction: distinct warehouse codes, comma joined,
        # ORDERED - `string_agg(DISTINCT col, sep ORDER BY col)` renders correctly
        # only with the ORDER BY wrapping the SEPARATOR argument (Postgres syntax
        # places ORDER BY after every value argument, not between DISTINCT and the
        # separator).
        location_expr = func.string_agg(
            Warehouse.warehouse_code.distinct(),
            aggregate_order_by(literal_column("', '"), Warehouse.warehouse_code),
        )
        select_cols_so: list = [
            SalesOrder.so_number,
            SalesOrder.order_date,
            Customer.customer_name,
            location_expr.label("location"),
        ]
        if has_product:
            # S19/AC-1633: this SO's own distinct matched codes, comma joined -
            # the same `string_agg(DISTINCT ...)` idiom as `location` above.
            # ONLY selected/joined when a product filter is active - a
            # customer-subject report has no matched-code family to name.
            product_codes_expr = func.string_agg(
                Product.product_code.distinct(),
                aggregate_order_by(literal_column("', '"), Product.product_code),
            )
            select_cols_so.append(product_codes_expr.label("product_codes"))
        q3 = (
            db.query(*select_cols_so, *_figure_sum_labels(figure_exprs))
            .select_from(SalesOrderLine)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
            .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
        )
        if has_product:
            q3 = q3.join(Product, Product.id == SalesOrderLine.product_id)
        q3 = q3.filter(*filters).group_by(
            SalesOrder.id, SalesOrder.so_number, SalesOrder.order_date, Customer.customer_name
        )
        rows = [
            {
                "so_number": r.so_number,
                "customer_name": r.customer_name,
                "location": r.location,
                "order_date": r.order_date,
                **({"product_codes": r.product_codes} if has_product else {}),
                **_quantised_figures({
                    "ordered_qty": _dec(r.ordered_qty), "ordered_value": _dec(r.ordered_value),
                    "confirmed_qty": _dec(r.confirmed_qty), "confirmed_value": _dec(r.confirmed_value),
                    "outstanding_qty": _dec(r.outstanding_qty), "outstanding_value": _dec(r.outstanding_value),
                }),
            }
            for r in q3.all()
        ]
        # AC-1629: order_date descending, ties by so_number descending, undated
        # last - the same two-step stable-sort idiom
        # `outstanding_report_service.py` uses for its own `so_rows`/`do_rows`.
        rows.sort(
            key=lambda r: (r["order_date"] is None, r["order_date"] or date.min, r["so_number"]),
            reverse=True,
        )
        rows.sort(key=lambda r: r["order_date"] is None)
        so_rows = rows

    return {
        "customer_name": _customer_echo(db, customer_query, customer_ids),
        # S19: echoes what the customer TYPED (upper-cased, as the exact-match
        # rule always did - a real code is stored upper-case), never a single
        # resolved product's own code - there is no longer one, the subject is
        # a whole family.
        "product_code": product_code_stripped.upper() if product_code_stripped else None,
        "product_codes": product_codes,
        "channel": channel,
        "warehouse_codes": [str(c).strip() for c in (warehouse_codes or []) if str(c).strip()],
        "date_from": _as_date(date_from),
        "date_to": _as_date(date_to),
        "months": months,
        "so_rows": so_rows,
    }


# ---------------------------------------------------------------------------
# Top selling: a whole-window ranking over the SAME source, predicate and
# per-line figures as the report above (`documentation/plans/chatbot/
# PLAN-chatbot-top-x-hot-selling-24sep.md`, slice S2, as amended by the owner's
# 26 Sep 2026 rulings on PR #1175). No subject, no months, no paging: `n` cuts
# the list when given, otherwise every ranked row comes back with
# `total_count` so the caller can ask how many to show.
# ---------------------------------------------------------------------------

_MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")

def _basis_figures(basis: str) -> tuple:
    """The (quantity, amount) per-line expressions for `basis` (AC-1931).
    "delivered" is the report's confirmed pair (transferred to DO, capped at
    ordered). "ordered" is the whole line, `qty_ordered` / `line_total`, NOT the
    report's ordered pair: that one counts a closed, short-delivered line only
    up to what was delivered."""
    if basis == "ordered":
        return SalesOrderLine.qty_ordered, func.coalesce(SalesOrderLine.line_total, 0)
    exprs = _per_line_exprs()
    return exprs["confirmed_qty"], exprs["confirmed_value"]


def current_year_window(today: Optional[date] = None) -> tuple[date, date]:
    """The owner's date default: the current calendar year, in Malaysia."""
    year = (today or datetime.now(_MY_TZ).date()).year
    return date(year, 1, 1), date(year, 12, 31)


def _names_in_order(db: Session, column_id, column_name, ids: Optional[list[str]]) -> Optional[str]:
    """Distinct names for `ids`, comma joined, in the order the ids arrived
    (the same first-seen rule `_customer_echo` keeps for customers)."""
    if not ids:
        return None
    name_by_id = {
        row[0]: row[1] for row in db.query(column_id, column_name).filter(column_id.in_(ids)).all() if row[1]
    }
    names: list[str] = []
    for i in ids:
        name = name_by_id.get(i)
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or None


def _category_echo(db: Session, ids: Optional[list[str]]) -> Optional[str]:
    """The category filter as the reply header names it. Categories that all share
    one class ("Water Closet" is SRT-WC, CBN-WC, ...) print that class, the word the
    customer typed (fix lane round 4: live category names are copies of their codes,
    and the chatbot narrows a class word to every category of that class); otherwise
    the category names, as `_names_in_order`."""
    if ids:
        labels = {
            (row[0] or "").strip()
            for row in db.query(ProductCategory.class_label).filter(ProductCategory.id.in_(ids)).all()
        }
        if len(labels) == 1 and "" not in labels:
            return labels.pop()
    return _names_in_order(db, ProductCategory.id, ProductCategory.category_name, ids)


#: The one technical safety ceiling on a named top-selling N (owner, 30 Sep 2026: "remove
#: the cap, there is a use case of 100, 200", superseding the 26 Sep "1 to 100"). Not a
#: product limit: it bounds one query and one WhatsApp reply (measured 30 Sep 2026 with
#: 9-character codes: 1000 rows render as 41,264 to 46,273 characters, 11 to 12 messages). The route 422s past it, the chatbot lane asks for it and says
#: so (`lanes/business`), and the MCP presenter (which cannot import the backend) keeps a
#: copy pinned equal by a test.
TOP_SELLING_N_CEILING = 1000


def top_selling(
    db: Session,
    *,
    rank_by: str,
    basis: str = "delivered",
    group: str = "item",
    n: Optional[int] = None,
    customer_query: Optional[str] = None,
    customer_ids: Optional[list[str]] = None,
    category_ids: Optional[list[str]] = None,
    sales_agent_ids: Optional[list[str]] = None,
    brand_ids: Optional[list[str]] = None,
    channel: Optional[str] = None,
    date_from: date,
    date_to: date,
    dealer_scoped: bool = False,
    detail_code: Optional[str] = None,
    direction: str = "top",
) -> dict:
    """Rank items (or categories) by summed quantity or amount on `basis`.

    Every value arrives validated and normalised by the route. ONE grouped
    query: window functions carry the full count and the whole-set totals
    beside the (optionally `LIMIT n`) ranked rows, so `n` never changes what
    `total_count` / `totals` say. A group whose quantity AND amount are both 0
    on the chosen basis has no sale to rank and is left out (an item nothing
    was delivered of, on the delivered basis); a zero-value line with a real
    quantity still ranks (owner ruling Q9).

    `detail_code` (AC-1935, the detail offer) narrows everything to the one
    product code (item grain) or category code (category grain), matched
    case-insensitively, and adds that code's customers and months.

    Fix lane round 4 (owner retest, 27 Sep 2026): `brand_ids` keeps products of those
    brands (`Product.brand_id`, the narrowing PR #1301 gives the outstanding report),
    and `direction="bottom"` ranks the least sold first (both metrics and the code
    ascending). A group with no sale is never ranked, whichever the direction."""
    qty_expr, amount_expr = _basis_figures(basis)
    qty_sum = func.coalesce(func.sum(qty_expr), 0)
    amount_sum = func.coalesce(func.sum(amount_expr), 0)

    bucket_expr = _bucket_expr()
    filters = _common_filters(
        product_ids=None, customer_query=customer_query, customer_ids=customer_ids,
        channel=channel, warehouse_ids=None, date_from=date_from, date_to=date_to,
        bucket_expr=bucket_expr,
    )
    if category_ids is not None:
        filters.append(Product.category_id.in_(category_ids))
    if brand_ids is not None:
        filters.append(Product.brand_id.in_(brand_ids))
    agent_filter = SalesOrder.sales_agent_id.in_(sales_agent_ids) if sales_agent_ids is not None else None

    if group == "category":
        key_cols = (ProductCategory.id, ProductCategory.category_code, ProductCategory.category_name)
    else:
        key_cols = (Product.id, Product.product_code, Product.product_name)
    code_col, name_col = key_cols[1], key_cols[2]
    if detail_code:
        filters.append(func.upper(code_col) == detail_code.upper())

    metric, other = (qty_sum, amount_sum) if rank_by == "quantity" else (amount_sum, qty_sum)
    bottom = direction == "bottom"
    ordering = (metric.asc(), other.asc()) if bottom else (metric.desc(), other.desc())

    def _base(query, *, with_customer=False):
        query = query.select_from(SalesOrderLine).join(
            SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id
        ).join(Product, Product.id == SalesOrderLine.product_id)
        if group == "category":
            query = query.outerjoin(ProductCategory, ProductCategory.id == Product.category_id)
        if customer_query or with_customer:
            query = query.outerjoin(Customer, Customer.id == SalesOrder.customer_id)
        return query.filter(*filters)

    def _ranked_by(query, *key, tiebreak):
        if agent_filter is not None:
            query = query.filter(agent_filter)
        return (
            query.group_by(*key)
            .having(or_(qty_sum != 0, amount_sum != 0))
            .order_by(*ordering, tiebreak.asc().nullslast())
        )

    ranked = _base(
        db.query(
            code_col.label("code"),
            name_col.label("name"),
            qty_sum.label("quantity"),
            amount_sum.label("amount"),
            func.count().over().label("total_count"),
            func.sum(qty_sum).over().label("total_quantity"),
            func.sum(amount_sum).over().label("total_amount"),
        )
    )
    ranked = _ranked_by(ranked, *key_cols, tiebreak=code_col)
    if n is not None:
        ranked = ranked.limit(n)
    result = ranked.all()

    rows = [
        {
            "rank": i,
            "code": r.code,
            "quantity": _qty(r.quantity),
            "amount": _money_edge(r.amount),
        }
        for i, r in enumerate(result, start=1)
    ]
    first = result[0] if result else None

    def _figures(r) -> dict:
        return {"quantity": _qty(r.quantity), "amount": _money_edge(r.amount)}

    def _top_selling_detail(row) -> dict:
        by_customer = _ranked_by(
            _base(
                db.query(
                    Customer.customer_name.label("customer_name"),
                    qty_sum.label("quantity"),
                    amount_sum.label("amount"),
                ),
                with_customer=True,
            ),
            Customer.id, Customer.customer_name, tiebreak=Customer.customer_name,
        ).all()
        month_col = func.to_char(bucket_expr, "YYYY-MM")
        by_month = _ranked_by(
            _base(db.query(month_col.label("month"), qty_sum.label("quantity"), amount_sum.label("amount"))),
            month_col, tiebreak=month_col,
        ).all()
        return {
            "code": row.code,
            "name": row.name,
            "by_customer": [{"customer_name": r.customer_name, **_figures(r)} for r in by_customer],
            "by_month": [{"month": r.month, **_figures(r)} for r in by_month],
        }

    fill_rate = None
    if sales_agent_ids is not None:
        # Share of the window's SOs (every other filter applied, the agent
        # filter NOT) that carry any agent at all: the owner's caveat that
        # agent attribution is only as good as its fill.
        so_total, so_with_agent = _base(
            db.query(
                func.count(func.distinct(SalesOrder.id)),
                func.count(func.distinct(case((SalesOrder.sales_agent_id.isnot(None), SalesOrder.id)))),
            )
        ).one()
        fill_rate = round(so_with_agent / so_total, 4) if so_total else None

    return {
        "rank_by": rank_by,
        "basis": basis,
        "group": group,
        "direction": "bottom" if bottom else "top",
        "n": n,
        "date_from": _as_date(date_from),
        "date_to": _as_date(date_to),
        "filters": {
            "customer_name": _customer_echo(db, customer_query, customer_ids),
            "category_name": _category_echo(db, category_ids),
            "brand_name": _names_in_order(db, Brand.id, Brand.brand_name, brand_ids),
            "sales_agent": _names_in_order(db, SalesAgent.id, SalesAgent.sales_agent, sales_agent_ids),
            "channel": channel,
            "dealer_scoped": dealer_scoped,
        },
        "total_count": int(first.total_count) if first else 0,
        "rows": rows,
        "totals": {
            "quantity": _qty(first.total_quantity) if first else 0,
            "amount": _money_edge(first.total_amount) if first else 0.0,
        },
        "sales_agent_fill_rate": fill_rate,
        "detail": _top_selling_detail(first) if detail_code and first else None,
    }
