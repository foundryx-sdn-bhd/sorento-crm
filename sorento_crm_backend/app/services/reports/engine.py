"""Run a report definition against the database and return what the screen renders.

Two layouts over one row set:

- **Detail** - the requested catalog columns, in the requested order, plus a total for
  every measure among them.
- **Pivot** - GROUP BY row dimension, column dimension in SQL; the matrix, the row and
  column totals and the grand total assembled here from Decimals.

Every total in the result is computed HERE, so the screen and the exported workbook cannot
disagree (the workbook writes values, never formulas - AC-D4). Money is a decimal string
with two places, or absent: a form with no lines has no sample price, and the workbook
prints "-" there rather than 0.00.

The sync path is capped (5,000 detail rows / 5,000 pivot cells) and answers 422 with
``capped: true``; the export path passes ``cap=False`` and has no cap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import status
from sqlalchemy import and_, false, func
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.schemas.report import (
    ReportBlock,
    ReportColumn,
    ReportColumnGroup,
    ReportDetailLayout,
    ReportLayouts,
    ReportPivotColumnDimension,
    ReportPivotDimension,
    ReportPivotLayout,
    ReportResult,
    ReportViewConfig,
)
from app.models.base import UNSET, company_scope, get_company_scope
from app.services.error_handler import AppException
from app.services.reports import registry as reg

#: `resolve(company_grants=...)` left unsaid: read the grant off the session's own scope.
FROM_SESSION = object()

# The sync caps. A run over either is refused and the user is pointed at the uncapped
# export - the refusal IS the answer, not a failure.
DETAIL_ROW_CAP = 5000
PIVOT_CELL_CAP = 5000

_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

_TWO_PLACES = Decimal("0.01")

#: Every blank dimension value groups here, on both axes, sorted last. Skipping a blank
#: value is how a Summary grand total ends up smaller than the Detail total it is meant to
#: be the same money as: the row is in the register, so it has to be in the pivot too.
BLANK_VALUE = "(blank)"

_DIGITS = re.compile(r"(\d+)")


class ReportCapped(AppException):
    """A 422 the frontend can tell apart from a failure: the body carries `capped: true`.

    AppException's envelope is `message`/`detail`/`code` and the global handler serialises it
    FLAT (app/main.py), so the flag sits alongside the message. Without it a capped run reads
    as "something went wrong" instead of "narrow this, or export instead".
    """

    def __init__(self, message: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=message,
            code="REPORT_CAPPED",
        )
        self.detail = {**self.detail, "capped": True}


def _invalid(message: str) -> AppException:
    return AppException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message=message,
        code="REPORT_INVALID_PARAMS",
    )


# ---------------------------------------------------------------------- the period


@dataclass(frozen=True)
class Period:
    kind: str
    start: date
    end_exclusive: date
    label: str
    months: Tuple[str, ...]
    #: The period as a total line names it: "JAN-DEC'25". The client's own SUMMARY closes
    #: with "GRAND TOTAL PROJECT VALUE JAN-DEC'25", so the words are part of the report.
    compact_label: str = ""


def _month_key(value: date) -> str:
    return f"{value.year}-{value.month:02d}"


def month_label(key: str) -> str:
    """"2025-01" -> "Jan'25", the period line a monthly sheet opens with."""
    year, month = key.split("-")
    return f"{_MONTH_ABBR[int(month) - 1]}'{year[2:]}"


def month_sheet_name(key: str) -> str:
    """"2025-01" -> "JAN'25", the tab name the client's own workbook uses."""
    return month_label(key).upper()


def _add_month(value: date) -> date:
    return date(value.year + 1, 1, 1) if value.month == 12 else date(value.year, value.month + 1, 1)


def _months_between(start: date, end_exclusive: date) -> Tuple[str, ...]:
    keys: List[str] = []
    cursor = date(start.year, start.month, 1)
    while cursor < end_exclusive:
        keys.append(_month_key(cursor))
        cursor = _add_month(cursor)
    return tuple(keys)


#: The calendar a report can be asked about. Outside it, `date(year, 1, 1)` raises and
#: `year + 1` overflows, so a typed or fuzzed year came back as a 500 rather than as an
#: answer about the field the user filled in.
_MIN_YEAR = 1900
_MAX_YEAR = 2200


def _checked_year(raw: Any) -> int:
    try:
        year = int(raw)
    except (TypeError, ValueError):
        raise _invalid(f"Invalid period: '{raw}' is not a year")
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        raise _invalid(f"Invalid period: year {year} is outside {_MIN_YEAR}-{_MAX_YEAR}")
    return year


def _parse_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise _invalid(f"Invalid date '{value}' for '{field_name}'")


def resolve_period(raw: Any) -> Period:
    if not isinstance(raw, dict):
        raise _invalid("Invalid period: expected an object with a 'kind'")
    kind = raw.get("kind")

    if kind == "year":
        if "year" not in raw:
            raise _invalid("Invalid period: 'year' is required for a yearly period")
        year = _checked_year(raw["year"])
        start, end = date(year, 1, 1), date(year + 1, 1, 1)
        suffix = str(year)[2:]
        return Period(
            kind,
            start,
            end,
            f"Jan'{suffix} to Dec'{suffix}",
            _months_between(start, end),
            f"JAN-DEC'{suffix}",
        )

    if kind == "month_range":
        try:
            from_month = int(raw["from_month"])
            to_month = int(raw["to_month"])
        except (KeyError, TypeError, ValueError):
            raise _invalid(
                "Invalid period: 'year', 'from_month' and 'to_month' are required for a month range"
            )
        if "year" not in raw:
            raise _invalid(
                "Invalid period: 'year', 'from_month' and 'to_month' are required for a month range"
            )
        year = _checked_year(raw["year"])
        if not 1 <= from_month <= 12 or not 1 <= to_month <= 12 or from_month > to_month:
            raise _invalid(f"Invalid period: months {from_month} to {to_month}")
        start = date(year, from_month, 1)
        end = _add_month(date(year, to_month, 1))
        suffix = str(year)[2:]
        # One month is not a range: a month chip that reads "Jan'25 to Jan'25" describes a
        # span the user never asked for (AC-G1).
        label = (
            f"{_MONTH_ABBR[from_month - 1]}'{suffix}"
            if from_month == to_month
            else f"{_MONTH_ABBR[from_month - 1]}'{suffix} to {_MONTH_ABBR[to_month - 1]}'{suffix}"
        )
        compact = (
            f"{_MONTH_ABBR[from_month - 1]}'{suffix}"
            if from_month == to_month
            else f"{_MONTH_ABBR[from_month - 1]}-{_MONTH_ABBR[to_month - 1]}'{suffix}"
        ).upper()
        return Period(kind, start, end, label, _months_between(start, end), compact)

    if kind == "custom":
        start = _parse_date(raw.get("from"), "period.from")
        last = _parse_date(raw.get("to"), "period.to")
        if last < start:
            raise _invalid("Invalid period: 'to' is before 'from'")
        end = last + timedelta(days=1)
        # DD/MM/YYYY, the way the CRM writes a date everywhere else. The ISO form leaked
        # into the title block and into the SUMMARY's own closing line.
        label = f"{start.strftime('%d/%m/%Y')} to {last.strftime('%d/%m/%Y')}"
        return Period(
            kind, start, end, label, _months_between(start, end), label.upper()
        )

    raise _invalid(f"Unknown period kind '{kind}'")


# --------------------------------------------------------------------- the context


@dataclass
class QueryContext:
    """What every expression in a dataset is a function of."""

    db: Session
    definition: reg.ReportDefinition
    date_basis_key: str
    date_basis: ColumnElement
    period: Period
    values: Dict[str, Any] = field(default_factory=dict)
    #: The companies this caller may read, for a scope="company" dataset: None = every
    #: company (a system caller), a frozenset = those, UNSET or empty = none (fail closed).
    company_grants: Any = UNSET

    @property
    def dataset(self) -> reg.Dataset:
        """The row set this run reads: the one its param values pick (`datasets_by`)."""
        return self.definition.dataset_for(self.values)

    @property
    def company_id(self) -> Optional[str]:
        key = self.dataset.company_param
        chosen = self.values.get(key) if key else None
        return chosen[0] if chosen else None


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _forbidden_company() -> AppException:
    return AppException(
        status_code=status.HTTP_403_FORBIDDEN,
        message="You do not have access to that company",
        code="COMPANY_FORBIDDEN",
    )


def _grants(db: Session, company_grants: Any) -> Any:
    """The caller's company grant: given, or read off the session's scope."""
    if company_grants is FROM_SESSION:
        return get_company_scope(db)
    if isinstance(company_grants, (list, tuple, set)):
        return frozenset(str(c) for c in company_grants)
    return company_grants


def _bind_company(db: Session, definition: reg.ReportDefinition, values: Dict[str, Any],
                  given: Dict[str, Any], grants: Any) -> None:
    """The company a run reads: the named one inside the grant (403 outside it), else the
    caller's current company, else the first granted one. No grant, no company."""
    key = definition.dataset.company_param
    if key is None:
        return
    chosen = [c for c in _as_list(given.get(key)) if c]
    if len(chosen) > 1:
        raise _invalid(f"'{key}' takes one company")
    if chosen:
        if grants is None:
            return
        if not isinstance(grants, frozenset) or chosen[0] not in grants:
            raise _forbidden_company()
        return
    if grants is None or not isinstance(grants, frozenset) or not grants:
        values[key] = []
        return
    session = get_company_scope(db)
    current = [c for c in session if c in grants] if isinstance(session, frozenset) else []
    values[key] = [current[0] if len(current) == 1 else sorted(grants)[0]]


def resolve(
    db: Session,
    definition: reg.ReportDefinition,
    params: Dict[str, Any],
    *,
    company_grants: Any = FROM_SESSION,
) -> QueryContext:
    """Validate the incoming params against the definition and bind them (AC-A3)."""
    params = dict(params or {})
    known = {p.key for p in definition.params}
    for key in params:
        if key not in known:
            raise _invalid(f"Unknown param '{key}'")

    basis_key: Optional[str] = None
    date_basis_param = ""
    period: Optional[Period] = None
    values: Dict[str, Any] = {}

    for param in definition.params:
        given = params.get(param.key, None)
        if isinstance(param, reg.DateBasisParam):
            basis_key = str(given) if given not in (None, "") else param.default
            date_basis_param = param.key
            values[param.key] = basis_key
        elif isinstance(param, reg.PeriodParam):
            period = resolve_period(given if given is not None else param.resolved_default())
            values[param.key] = period
        elif isinstance(param, reg.SelectParam):
            chosen = _as_list(given) if given is not None else list(param.default)
            values[param.key] = chosen
        else:  # pragma: no cover - a param type nobody has declared
            raise _invalid(f"Unsupported param type for '{param.key}'")

    if basis_key is None or period is None:
        raise _invalid(f"Report '{definition.key}' declares no date basis or no period param")
    # Checked once every select is bound: the dataset, and so its date bases, can follow
    # a select param (`datasets_by`).
    dataset = definition.dataset_for(values)
    if dataset.basis(basis_key) is None:
        raise _invalid(f"Unknown date basis '{basis_key}' for '{date_basis_param}'")

    grants = _grants(db, company_grants) if definition.dataset.scope == "company" else None
    _bind_company(db, definition, values, params, grants)

    return QueryContext(
        db=db,
        definition=definition,
        date_basis_key=basis_key,
        # Malaysia wall clock, once: the period predicate, the month bucket, the ordering
        # and the printed date all read this one expression, so they cannot disagree.
        date_basis=reg.to_malaysia(dataset.basis(basis_key).expr),
        period=period,
        values=values,
        company_grants=grants,
    )


def _predicates(ctx: QueryContext) -> List[ColumnElement]:
    """Period + every select filter + the company scope, in one list."""
    preds: List[ColumnElement] = [
        ctx.date_basis >= ctx.period.start,
        ctx.date_basis < ctx.period.end_exclusive,
    ]
    for param in ctx.definition.params:
        if not isinstance(param, reg.SelectParam):
            continue
        chosen = ctx.values.get(param.key) or []
        if not chosen:  # an empty multi-select means "no filter", as the screen shows
            continue
        condition = param.condition(ctx, chosen)
        if condition is not None:
            preds.append(condition)

    dataset = ctx.dataset
    if dataset.scope == "company":
        # FAIL-CLOSED (AC-R2-4): None is the deliberate all-companies caller; a grant set
        # is those companies; UNSET or an empty set is nobody's, so no rows at all.
        grants = ctx.company_grants
        if grants is None:
            pass
        elif isinstance(grants, frozenset) and grants:
            preds.append(dataset.company_column.in_(sorted(grants)))
        else:
            preds.append(false())
        if dataset.company_param is not None and not ctx.company_id:
            preds.append(false())
    if dataset.product_id_column is not None:
        from app.services.contact_brand_scope import product_in_scope_clauses

        preds.extend(product_in_scope_clauses(ctx.db, dataset.product_id_column))
    return preds


# -------------------------------------------------------------------- value shapes


def _money(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(_TWO_PLACES))


def _cell_value(column: reg.Column, value: Any) -> Any:
    if value is None:
        return None
    if column.type == "money":
        return _money(value)
    if column.type == "date":
        return value.date().isoformat() if isinstance(value, datetime) else str(value)
    if column.type == "integer":
        return int(value)
    if column.type == "bool":
        return bool(value)
    return str(value)


def _dimension_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def _bucket(value: Any) -> str:
    """A dimension value as the pivot groups it: blank is a named bucket, never a gap."""
    return _dimension_value(value) or BLANK_VALUE


def _natural_key(value: str) -> Tuple:
    """Rank a dimension value the way a reader does: "Agent 2" before "Agent 10".

    Plain lexical order puts 10 first, which reads as a sorting bug on any dimension whose
    values carry a number (year, week, agent 2). The blank bucket always sorts last.
    """
    if value == BLANK_VALUE:
        return (1,)
    chunks = [c for c in _DIGITS.split(value) if c != ""]
    return (0, tuple((1, int(c), "") if c.isdigit() else (0, 0, c.casefold()) for c in chunks))


def _totals(sums: Dict[str, Optional[Decimal]]) -> Dict[str, str]:
    return {key: str(total.quantize(_TWO_PLACES)) for key, total in sums.items() if total is not None}


# -------------------------------------------------------------------------- detail


def _column_expr(ctx: QueryContext, column: reg.Column) -> ColumnElement:
    """A catalog column's SQL. A ``date`` column reads in Malaysia time, like every other
    date the CRM prints; anything else is the dataset's expression verbatim."""
    expr = column.expr(ctx)
    return reg.to_malaysia(expr) if column.type == "date" else expr


def _select_columns(ctx: QueryContext, keys: List[str]) -> List[reg.Column]:
    """The catalog columns behind the requested keys, in the requested order (AC-A5).

    A key another of the report's datasets holds is left out rather than refused: a view
    saved on one basis names that basis's columns, and it must still run on the other."""
    dataset = ctx.dataset
    catalog = {c.key for c in ctx.definition.catalog()}
    columns: List[reg.Column] = []
    for key in keys:
        column = dataset.column(key)
        if column is None:
            if key in catalog:
                continue
            raise _invalid(f"Unknown detail column '{key}'")
        columns.append(column)
    return columns


def _detail_statement(ctx: QueryContext, columns: List[reg.Column], *extra):
    stmt = ctx.dataset.base(ctx).add_columns(
        *[_column_expr(ctx, c).label(c.key) for c in columns], *extra
    )
    stmt = stmt.where(and_(*_predicates(ctx)))
    order_by = list(ctx.definition.detail.order_by(ctx))
    if order_by:
        stmt = stmt.order_by(*order_by)
    return stmt


def _tick_values(
    ctx: QueryContext, columns: List[reg.Column], fetched: List[Any]
) -> Dict[str, List[reg.TickColumn]]:
    """The member columns of every tick group, computed ONCE over the whole result.

    A group that declares ``members`` (the workbook's fixed 2025..2028 band) gets them from
    the period, so its ids stay put when the period moves. A group that does not gets one
    column per value PRESENT.

    The workbook splits the same result across twelve sheets, so these are computed once
    and handed to every sheet: derived per sheet, January would come out with one delivery
    year and March with two, and the twelve tables would stop being the same table.
    """
    groups_by_source = {g.source: g for g in ctx.definition.detail.groups}
    values: Dict[str, List[reg.TickColumn]] = {}
    for source, group in groups_by_source.items():
        if not any(c.key == source for c in columns):
            continue
        if group.members is not None:
            values[source] = list(group.members(ctx, source))
            continue
        present = sorted({_dimension_value(row[source]) for row in fetched} - {""})
        values[source] = [reg.TickColumn(key=f"{source}__{v}", label=v, value=v) for v in present]
    return values


def _detail_layout(
    ctx: QueryContext,
    columns: List[reg.Column],
    tick_values: Dict[str, List[reg.TickColumn]],
    fetched: List[Any],
) -> ReportDetailLayout:
    """Fetched rows -> the wire shape, with a total for every measure among the columns."""
    definition = ctx.definition
    groups_by_source = {g.source: g for g in definition.detail.groups}

    out_columns: List[ReportColumn] = []
    out_groups: List[ReportColumnGroup] = []
    for column in columns:
        if column.key in groups_by_source:
            members = tick_values.get(column.key) or []
            if not members:
                continue
            out_columns.extend(
                ReportColumn(key=member.key, label=member.label, type="bool", size=52)
                for member in members
            )
            out_groups.append(
                ReportColumnGroup(
                    label=groups_by_source[column.key].label,
                    source=column.key,
                    keys=[member.key for member in members],
                )
            )
            continue
        out_columns.append(
            ReportColumn(key=column.key, label=column.label, type=column.type, size=column.size)
        )

    measures = [c for c in columns if c.tag == "measure"]
    sums: Dict[str, Optional[Decimal]] = {c.key: None for c in measures}

    rows: List[Dict[str, Any]] = []
    for fetched_row in fetched:
        row: Dict[str, Any] = {}
        for column in columns:
            value = fetched_row[column.key]
            if column.key in groups_by_source:
                rendered = _dimension_value(value)
                for member in tick_values.get(column.key) or []:
                    row[member.key] = rendered == member.value
                continue
            row[column.key] = _cell_value(column, value)
            if column.tag == "measure" and value is not None:
                sums[column.key] = (sums[column.key] or Decimal(0)) + Decimal(str(value))
        rows.append(row)

    return ReportDetailLayout(
        key=definition.detail.key,
        title=(
            definition.detail.title(ctx)
            if callable(definition.detail.title)
            else definition.detail.title
        ),
        columns=out_columns,
        column_groups=out_groups,
        rows=rows,
        totals=_totals(sums),
    )


def _detail(ctx: QueryContext, view: ReportViewConfig, cap: bool) -> ReportDetailLayout:
    columns = _select_columns(
        ctx, list(view.detail.columns) or [c.key for c in ctx.dataset.columns]
    )
    stmt = _detail_statement(ctx, columns)
    if cap:
        stmt = stmt.limit(DETAIL_ROW_CAP + 1)

    fetched = ctx.db.execute(stmt).mappings().all()
    truncated = False
    if cap and len(fetched) > DETAIL_ROW_CAP:
        if ctx.definition.detail.cap != "truncate":
            raise ReportCapped(
                f"This run returns more than {DETAIL_ROW_CAP:,} rows. "
                "Narrow the period or export to Excel instead."
            )
        fetched = fetched[:DETAIL_ROW_CAP]
        truncated = True
    layout = _detail_layout(ctx, columns, _tick_values(ctx, columns, fetched), fetched)
    if truncated:
        layout.truncated = True
        layout.totals = _whole_set_totals(ctx, columns)
    return layout


def _whole_set_totals(ctx: QueryContext, columns: List[reg.Column]) -> Dict[str, str]:
    """A truncated detail still totals the WHOLE row set, in SQL."""
    measures = [c for c in columns if c.tag == "measure"]
    if not measures:
        return {}
    stmt = ctx.dataset.base(ctx).add_columns(
        *[func.sum(m.expr(ctx)).label(m.key) for m in measures]
    ).where(and_(*_predicates(ctx)))
    row = ctx.db.execute(stmt).mappings().first() or {}
    return {m.key: _money(row[m.key]) for m in measures if row.get(m.key) is not None}


# --------------------------------------------------------------------------- pivot


def _dimension(dataset: reg.Dataset, key: str, role: str) -> reg.Column:
    column = dataset.column(key)
    if column is None or column.tag != "dimension":
        raise _invalid(f"'{key}' is not a dimension this report can group {role} by")
    return column


def _measure(dataset: reg.Dataset, key: str) -> reg.Column:
    column = dataset.column(key)
    if column is None or column.tag != "measure":
        raise _invalid(f"'{key}' is not a measure this report can total")
    return column


def _pivot(ctx: QueryContext, view: ReportViewConfig, cap: bool) -> ReportPivotLayout:
    definition = ctx.definition
    dataset = ctx.dataset
    config = view.pivot

    if config.rows == config.cols:
        raise _invalid("Rows and Columns cannot be the same dimension")
    row_column = _dimension(dataset, config.rows, "rows")
    col_column = _dimension(dataset, config.cols, "columns")

    measures: List[reg.Column] = [_measure(dataset, key) for key in config.measures]

    row_expr = _column_expr(ctx, row_column)
    col_expr = _column_expr(ctx, col_column)
    stmt = dataset.base(ctx).add_columns(
        row_expr.label("__row"),
        col_expr.label("__col"),
        *[func.sum(m.expr(ctx)).label(f"__m{i}") for i, m in enumerate(measures)],
    )
    stmt = stmt.where(and_(*_predicates(ctx))).group_by(row_expr, col_expr)
    grouped = ctx.db.execute(stmt).mappings().all()

    cells: Dict[str, Dict[str, Dict[str, str]]] = {}
    row_sums: Dict[str, Dict[str, Decimal]] = {}
    col_sums: Dict[str, Dict[str, Decimal]] = {}
    grand: Dict[str, Decimal] = {}
    row_values: List[str] = []
    present_cols: List[str] = []

    for group in grouped:
        row_value = _bucket(group["__row"])
        col_value = _bucket(group["__col"])
        if row_value not in row_values:
            row_values.append(row_value)
        if col_value not in present_cols:
            present_cols.append(col_value)
        for index, measure in enumerate(measures):
            total = group[f"__m{index}"]
            if total is None:  # every row in this cell was blank; the cell stays blank
                continue
            amount = Decimal(str(total))
            cells.setdefault(row_value, {}).setdefault(col_value, {})[measure.key] = _money(amount)
            row_sums.setdefault(row_value, {})
            row_sums[row_value][measure.key] = row_sums[row_value].get(measure.key, Decimal(0)) + amount
            col_sums.setdefault(col_value, {})
            col_sums[col_value][measure.key] = col_sums[col_value].get(measure.key, Decimal(0)) + amount
            grand[measure.key] = grand.get(measure.key, Decimal(0)) + amount

    row_values = _axis_values(ctx, row_column, row_values, rows=True)
    col_values = _axis_values(ctx, col_column, present_cols, rows=False)
    value_labels = _value_labels(col_column, col_values)

    if cap and len(row_values) * len(col_values) > PIVOT_CELL_CAP:
        raise ReportCapped(
            f"This summary is more than {PIVOT_CELL_CAP:,} cells. "
            "Group by something coarser or export to Excel instead."
        )

    layout = definition.pivot
    # Year against year means something only when the ROWS are the years: re-pivoted by
    # month or by channel (Configure summary, the chatbot's own views), the last row minus
    # the one before is DEC minus NOV, or Project minus Dealer - so no VARIANCE, and the
    # column totals come back (reviewer B1).
    # The same holds for years by channel: a channel is not a month, so there is no "same
    # months last year" to hold this year to (review round 2 B1).
    by_year = row_column.period_years and col_column.fixed_values == reg.MONTHS_OF_YEAR
    variance_row, variance_total = (
        _variance(ctx, row_values, cells, measures)
        if layout.variance == "last_two_rows" and by_year
        else (None, None)
    )

    return ReportPivotLayout(
        key=layout.key,
        title=layout.title,
        row_dim=ReportPivotDimension(key=row_column.key, label=row_column.label),
        row_value_labels=(
            _value_labels(row_column, row_values) if row_column.fixed_values else None
        ),
        col_dim=ReportPivotColumnDimension(
            key=col_column.key,
            label=col_column.label,
            values=col_values,
            value_labels=value_labels,
        ),
        measures=[
            ReportColumn(key=m.key, label=m.label, type=m.type, size=m.size) for m in measures
        ],
        row_values=row_values,
        cells=cells,
        row_totals={k: _totals(v) for k, v in row_sums.items()},
        col_totals={k: _totals(v) for k, v in col_sums.items()},
        grand_total=_totals(grand),
        variance_row=variance_row,
        variance_total=variance_total,
        variance_label="VARIANCE" if variance_row is not None else None,
        chart=layout.chart,
        whole_units=layout.whole_units,
        show_column_totals=layout.column_totals or not by_year,
    )


def _period_years(ctx: QueryContext) -> List[str]:
    last = ctx.period.end_exclusive - timedelta(days=1)
    return [str(year) for year in range(ctx.period.start.year, last.year + 1)]


def _axis_values(
    ctx: QueryContext, column: reg.Column, present: List[str], *, rows: bool
) -> List[str]:
    """The values an axis prints, in order.

    A FIXED axis (months of the year, the period's years, the period's months) prints
    every value whether the data holds it or not; any other axis prints what is present,
    ranked naturally. The blank bucket is kept and sorts last either way.
    """
    fixed: Optional[List[str]] = None
    if column.fixed_values is not None:
        fixed = [value for value, _label in column.fixed_values]
    elif column.period_years:
        fixed = _period_years(ctx)
    elif column.period_months and not rows:
        # Every month of the period, empty ones included - the workbook has twelve sheets
        # whether or not December had a form.
        fixed = list(ctx.period.months)
    if fixed is None:
        return sorted(present, key=_natural_key)
    extra = sorted((v for v in present if v not in fixed and v != BLANK_VALUE), key=_natural_key)
    values = fixed + extra
    if BLANK_VALUE in present:
        values.append(BLANK_VALUE)
    return values


def _value_labels(column: reg.Column, values: List[str]) -> Optional[Dict[str, str]]:
    if column.fixed_values is not None:
        labels = dict(column.fixed_values)
        return {value: labels.get(value, value) for value in values}
    if column.value_label:
        return {
            value: value if value == BLANK_VALUE else column.value_label(value)
            for value in values
        }
    return None


def variance_months(period_start: date, as_at: date, last_year: int, previous_year: int) -> List[str]:
    """The months ("01".."12") a year-on-year difference is taken over (G5 (a)): every month
    of the last year up to the as-at month, whose month the year before is inside the
    period too. A month after the as-at date is left out, so a September report does not
    read October to December as a collapse that has not happened."""
    first = date(period_start.year, period_start.month, 1)
    return [
        f"{month:02d}"
        for month in range(1, 13)
        if date(last_year, month, 1) <= as_at and date(previous_year, month, 1) >= first
    ]


def _variance(
    ctx: QueryContext,
    row_values: List[str],
    cells: Dict[str, Dict[str, Dict[str, str]]],
    measures: List[reg.Column],
) -> Tuple[Optional[Dict[str, Dict[str, str]]], Optional[Dict[str, str]]]:
    """The last row minus the one before, month by month from JAN to the as-at month; its
    total is this year to date minus last year over the same months (G5 (a), AC-S1-10).

    A missing cell reads as 0, so a month this year sold nothing in and last year did
    counts against it (review round 2 B1). A month neither year sold in stays blank.
    """
    real = [v for v in row_values if v != BLANK_VALUE]
    if len(real) < 2:
        return None, None
    last, previous = real[-1], real[-2]
    as_at = ctx.period.end_exclusive - timedelta(days=1)
    months = variance_months(ctx.period.start, as_at, int(last), int(previous))
    row: Dict[str, Dict[str, str]] = {}
    total: Dict[str, Decimal] = {}
    for month in months:
        for measure in measures:
            now = (cells.get(last) or {}).get(month, {}).get(measure.key)
            before = (cells.get(previous) or {}).get(month, {}).get(measure.key)
            if now is None and before is None:
                continue
            diff = Decimal(now or 0) - Decimal(before or 0)
            row.setdefault(month, {})[measure.key] = _money(diff)
            total[measure.key] = total.get(measure.key, Decimal(0)) + diff
    return row, _totals(total)


# ----------------------------------------------------------------------------- run


def view_config(definition: reg.ReportDefinition) -> ReportViewConfig:
    """The definition's own default view, as the wire shape.

    A param the default view does not name falls back to the PARAM's default, resolved
    now: that is how "this year" stays the user's year rather than the year the process
    booted in.
    """
    raw = dict(definition.default_view)
    params = dict(raw.get("params") or {})
    for param in definition.params:
        if params.get(param.key) is None:
            params[param.key] = reg.default_value(param)
    raw["params"] = params
    return ReportViewConfig.model_validate(raw)


def _unscoped(ctx: QueryContext):
    """A scope="company" dataset owns its company arm (`_predicates`), so its statements
    run with the ORM listener's own per-entity scope OFF: the session's scope is the
    caller's ACTIVE company, and a user granted Sorento and Mocha must be able to read
    Mocha from a Sorento session. Every other dataset runs exactly as before."""
    if ctx.dataset.scope == "company":
        return company_scope(ctx.db, None)
    from contextlib import nullcontext

    return nullcontext()


def _split_values(ctx: QueryContext) -> Optional[List[Tuple[str, str]]]:
    """(value, label) of every chosen value of the definition's `sheet_per` param, in the
    param's own option order. None when the definition does not split or nothing is
    chosen (an empty multi-select means "no filter", so there is one block: the summary)."""
    key = ctx.definition.workbook.sheet_per
    if key is None:
        return None
    chosen = ctx.values.get(key) or []
    if not chosen:
        return None
    param = next(p for p in ctx.definition.params if p.key == key)
    options = list(param.options(ctx.db))
    ordered = [(v, label) for v, label in options if v in chosen]
    ordered += [(v, v) for v in chosen if v not in {o[0] for o in options}]
    return ordered


def _blocks(ctx: QueryContext, view: ReportViewConfig, cap: bool) -> Optional[List[ReportBlock]]:
    split = _split_values(ctx)
    if split is None:
        return None
    key = ctx.definition.workbook.sheet_per
    company = company_name(ctx.db, ctx.definition, ctx).upper()
    blocks: List[ReportBlock] = []
    for value, label in split:
        one = QueryContext(
            db=ctx.db,
            definition=ctx.definition,
            date_basis_key=ctx.date_basis_key,
            date_basis=ctx.date_basis,
            period=ctx.period,
            values={**ctx.values, key: [value]},
            company_grants=ctx.company_grants,
        )
        title = f"{company} - {label.upper()}" if company else label.upper()
        blocks.append(ReportBlock(key=value, title=title, summary=_pivot(one, view, cap)))
    return blocks


def _note(ctx: QueryContext) -> Optional[str]:
    return ctx.definition.note(ctx) if ctx.definition.note else None


def run(
    db: Session,
    definition: reg.ReportDefinition,
    params: Dict[str, Any],
    view: Optional[ReportViewConfig] = None,
    *,
    cap: bool = True,
    company_grants: Any = FROM_SESSION,
) -> ReportResult:
    """Both layouts over one row set. ``cap=False`` is the export path (AC-A7)."""
    ctx = resolve(db, definition, params, company_grants=company_grants)
    effective = view or view_config(definition)

    with _unscoped(ctx):
        detail = _detail(ctx, effective, cap)
        summary = _pivot(ctx, effective, cap)
        blocks = _blocks(ctx, effective, cap)
        row_count = _row_count(ctx) if detail.truncated else len(detail.rows)

    return ReportResult(
        key=definition.key,
        period_label=ctx.period.label,
        row_count=row_count,
        layouts=ReportLayouts(detail=detail, summary=summary, blocks=blocks),
        note=_note(ctx),
    )


def run_summary(
    db: Session,
    definition: reg.ReportDefinition,
    params: Dict[str, Any],
    view: ReportViewConfig,
    *,
    company_grants: Any = FROM_SESSION,
) -> ReportPivotLayout:
    """The summary (pivot) alone, uncapped: for a caller that prints no detail table and
    ranks the whole grouping itself (the chatbot's sales report, one view per reply).
    Same resolve, same predicates, same company arm as ``run``."""
    ctx = resolve(db, definition, params, company_grants=company_grants)
    with _unscoped(ctx):
        return _pivot(ctx, view, cap=False)


def _row_count(ctx: QueryContext) -> int:
    stmt = ctx.dataset.base(ctx).add_columns(func.count().label("n")).where(
        and_(*_predicates(ctx))
    )
    return int(ctx.db.execute(stmt).scalar() or 0)


# ------------------------------------------------------------------------ workbook


@dataclass(frozen=True)
class WorkbookSheet:
    """One tab of the export: a month of the period, and that month's detail table."""

    #: The tab name, as the client's own file writes it: JAN'25.
    name: str
    #: The period line of the title block: Jan'25.
    label: str
    detail: ReportDetailLayout
    #: The first of the month, so the title block can carry a real DATE cell rather than a
    #: caption (the client's own A4 is a date formatted mmm-yy).
    month_start: Optional[date] = None


@dataclass(frozen=True)
class WorkbookData:
    """What the renderer turns into bytes. Never serialised, so it is a plain dataclass."""

    key: str
    period_label: str
    summary: ReportPivotLayout
    sheets: List[WorkbookSheet]
    #: How the company writes its own name on a document: the DEFINITION's name when it
    #: has one, and system settings only for a definition that names none (AC-G7).
    company_name: str = ""
    #: "JAN-DEC'25", for the labelled total rows the client's SUMMARY closes with.
    period_compact_label: str = ""
    #: One summary block per value of the definition's `sheet_per` param, when it splits.
    blocks: Optional[List[ReportBlock]] = None
    #: The line the title block carries under the period (the basis of a sales report).
    note: Optional[str] = None
    #: The last day of the period, for an "AS AT" period line.
    period_end: Optional[date] = None


def workbook_columns(definition: reg.ReportDefinition, view: ReportViewConfig) -> List[str]:
    """The columns a WORKBOOK carries, which is not what the screen asks for.

    An empty ``detail.columns`` means the whole catalog to the SCREEN: it asks for
    everything and hides client-side, which is what makes ticking a column instant and
    keeps a hidden column offerable in the Columns panel (AC-B7). A file has no Columns
    panel and a twenty-column sheet is unreadable, so here an empty list means the
    DEFINITION'S default columns - the shape the report was designed around. The two
    differ on purpose (PLAN, contract points settled while building S4).
    """
    requested = list(view.detail.columns)
    if requested:
        return requested
    return list((definition.default_view.get("detail") or {}).get("columns") or [])


def validate_view(
    definition: reg.ReportDefinition,
    view: ReportViewConfig,
    dataset: Optional[reg.Dataset] = None,
) -> None:
    """Answer a bad view at the button, not in a download row a minute later.

    ``run`` finds these faults on the way to the screen, but ``export`` hands the view to a
    worker: an unknown column there is a failed row in My Downloads with no way back to the
    press that caused it. Same resolver the workbook uses, same messages.
    """
    catalog = {c.key for c in definition.catalog()}
    for key in workbook_columns(definition, view):
        if key not in catalog:
            raise _invalid(f"Unknown detail column '{key}'")
    # The pivot must hold on the dataset the run will read: the caller's resolved one
    # (the export's filter-bar params win over the view's own), else the view's params'.
    if dataset is None:
        dataset = definition.dataset_for(
            {k: _as_list(v) for k, v in (view.params or {}).items()}
        )

    pivot = view.pivot
    if pivot.rows == pivot.cols:
        raise _invalid("Rows and Columns cannot be the same dimension")
    _dimension(dataset, pivot.rows, "rows")
    _dimension(dataset, pivot.cols, "columns")
    for key in pivot.measures:
        _measure(dataset, key)


def _month_sheets(ctx: QueryContext, view: ReportViewConfig) -> List[WorkbookSheet]:
    """One sheet per month OF THE PERIOD, empty months included (AC-D1).

    One query, split in Python. The month bucket is the same expression the ``month``
    dimension uses - ``date_trunc`` on whichever date basis the user is reading by - so a
    row lands on the sheet the summary counts it in, whatever the basis.
    """
    columns = _select_columns(
        ctx, workbook_columns(ctx.definition, view) or [c.key for c in ctx.dataset.columns]
    )
    bucket = func.to_char(func.date_trunc("month", ctx.date_basis), "YYYY-MM")
    stmt = _detail_statement(ctx, columns, bucket.label("__month"))
    fetched = ctx.db.execute(stmt).mappings().all()

    ticks = _tick_values(ctx, columns, fetched)
    by_month: Dict[str, List[Any]] = {}
    for row in fetched:
        by_month.setdefault(str(row["__month"]), []).append(row)

    return [
        WorkbookSheet(
            name=month_sheet_name(key),
            label=month_label(key),
            detail=_detail_layout(ctx, columns, ticks, by_month.get(key, [])),
            month_start=date(int(key[:4]), int(key[5:7]), 1),
        )
        for key in ctx.period.months
    ]


def run_workbook(
    db: Session,
    definition: reg.ReportDefinition,
    params: Dict[str, Any],
    view: Optional[ReportViewConfig] = None,
    *,
    company_grants: Any = FROM_SESSION,
) -> WorkbookData:
    """The export shape: the summary, then the period's months. Never capped.

    The caps exist to keep a runaway run off the REQUEST path, and this runs on the worker
    (AC-A7). Both layouts still come out of one row set, so the file cannot disagree with
    the screen the user exported it from.
    """
    ctx = resolve(db, definition, params, company_grants=company_grants)
    effective = view or view_config(definition)
    with _unscoped(ctx):
        summary = _pivot(ctx, effective, cap=False)
        sheets = _month_sheets(ctx, effective) if definition.workbook.month_sheets else []
        blocks = _blocks(ctx, effective, cap=False)
    return WorkbookData(
        key=definition.key,
        period_label=ctx.period.label,
        summary=summary,
        sheets=sheets,
        company_name=company_name(db, definition, ctx),
        period_compact_label=ctx.period.compact_label,
        blocks=blocks,
        note=_note(ctx),
        period_end=ctx.period.end_exclusive - timedelta(days=1),
    )


def company_name(
    db: Session, definition: reg.ReportDefinition, ctx: Optional[QueryContext] = None
) -> str:
    """How this installation writes its own name, for the title block (AC-G7).

    The definition's value wins when it names one: it is the legal name the client puts on
    the paper ("SORENTO SDN BHD"), and system_settings.name still carries the template's
    "Metronic" on the live install, which would otherwise print on every export. System
    settings is the fallback for a definition that names no company.
    """
    from app.models.user import SystemSetting

    named = (definition.workbook.company_name or "").strip()
    if named:
        return named
    if ctx is not None and ctx.company_id:
        # A company-scoped report names the company it READ, not the installation.
        from app.models.company import Company

        stored = db.query(Company.name).filter(Company.id == ctx.company_id).scalar()
        if stored:
            return str(stored).strip()
    try:
        stored = db.query(SystemSetting.name).order_by(SystemSetting.id).first()
    except Exception:  # noqa: BLE001 - a report must not fail over its own letterhead
        stored = None
    return ((stored[0] if stored else None) or "").strip()
