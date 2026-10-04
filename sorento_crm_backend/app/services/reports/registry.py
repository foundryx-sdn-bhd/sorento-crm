"""What a report IS: a dataset, a few params, two layouts and a workbook spec.

The foundation's whole claim is that report #2 costs one dataset plus one definition and
a two-line route wrapper. So everything a report varies is declared here as data, and
nothing about any particular report lives in the engine, the renderer or the routes.

Deliberately small (PRINCIPLES.md, "simplest thing that works"):

- **Two layouts.** Detail (a flat table) and Pivot (row dim x col dim x measures). The
  workbook is 12 identical detail tables plus one pivot, and every operational report the
  client has asked for decomposes the same way.
- **Three param types.** Date basis, period, select. A fourth is added when a report needs
  one, not before.
- **One derived column shape.** ``TickGroup`` - a dimension rendered as one tick column per
  value present, under a merged header. That is the workbook's "EXPECTED YEAR OF DELIVERY".

An expression is a function of the query context, because the interesting ones depend on
which date basis the user picked (the month bucket is ``date_trunc(<basis>)``). A date
basis is a plain column - it cannot depend on itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, func
from sqlalchemy.sql import ColumnElement, Select

COLUMN_TYPES = frozenset({"text", "money", "integer", "date", "bool"})
COLUMN_TAGS = frozenset({"dimension", "measure", "date", "text"})
SCOPES = frozenset({"company", "none"})

#: Every date in this system means Malaysia. Datetimes are STORED naive UTC, so a report
#: that buckets them raw files a 31 July 17:30 approval under July when the office that
#: approved it was already in August. Shifted once, here, so the period predicate, the
#: month bucket, the year list and the printed date can never disagree.
MALAYSIA_TZ = "Asia/Kuala_Lumpur"
_MY_TZINFO = ZoneInfo(MALAYSIA_TZ)


def to_malaysia(expr: ColumnElement) -> ColumnElement:
    """A datetime expression as Malaysia wall clock. A DATE is returned untouched.

    A DATE carries no time to shift, and shifting one would move every row back a day.
    """
    type_ = getattr(expr, "type", None)
    if not isinstance(type_, DateTime):
        return expr
    if getattr(type_, "timezone", False):
        return func.timezone(MALAYSIA_TZ, expr)
    # naive UTC -> aware -> Malaysia wall clock (`AT TIME ZONE 'UTC' AT TIME ZONE '...'`).
    return func.timezone(MALAYSIA_TZ, func.timezone("UTC", expr))


def today_malaysia():
    """Today where the users are, not where the server is."""
    return datetime.now(_MY_TZINFO).date()


def current_year_period() -> Dict[str, Any]:
    """This calendar year in Malaysia, resolved when it is ASKED FOR.

    Evaluated at import instead, a process booted on 31 December 2026 keeps opening the
    report on 2026 for as long as it stays up.
    """
    return {"kind": "year", "year": today_malaysia().year}


_MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
#: A month-of-year axis's fixed values ("01", "JAN") .. ("12", "DEC"). The kernel reads a
#: column carrying exactly these as months of the year (the VARIANCE window, G5 (a)).
MONTHS_OF_YEAR: Tuple[Tuple[str, str], ...] = tuple(
    (f"{index:02d}", name) for index, name in enumerate(_MONTH_NAMES, start=1)
)


@dataclass(frozen=True)
class Column:
    """One catalog column: what it is called, what it holds, and how to select it."""

    key: str
    label: str
    type: str
    tag: str
    # The SQL expression, given the query context (which carries the chosen date basis).
    expr: Callable[[Any], ColumnElement]
    size: Optional[int] = None
    # A dimension whose values are the MONTHS OF THE PERIOD rather than the months the
    # data happens to contain: an empty month still gets a column, as the workbook has.
    period_months: bool = False
    # Renders a raw dimension value for the screen ("2026-01" -> "Jan'26"). The frontend
    # must not invent a formatting rule per dimension, so the dataset supplies one.
    value_label: Optional[Callable[[Any], str]] = None
    #: An axis that prints EVERY value, in this order, whether the data holds it or not:
    #: (value, label) pairs. A month-of-year axis must print JAN to DEC in September too,
    #: and a cell with nothing in it stays blank, never 0.
    fixed_values: Optional[Tuple[Tuple[str, str], ...]] = None
    #: The period_months idea for years: every year of the period is a value, so a year
    #: with no sales is a blank row rather than a missing one.
    period_years: bool = False

    def __post_init__(self) -> None:
        if self.type not in COLUMN_TYPES:
            raise ValueError(
                f"Report column '{self.key}' has type '{self.type}'; "
                f"expected one of {sorted(COLUMN_TYPES)}"
            )
        if self.tag not in COLUMN_TAGS:
            raise ValueError(
                f"Report column '{self.key}' has tag '{self.tag}'; "
                f"expected one of {sorted(COLUMN_TAGS)}"
            )


@dataclass(frozen=True)
class DateBasis:
    """A date column the period can bind to (approved / form date / submitted)."""

    key: str
    label: str
    expr: ColumnElement


@dataclass(frozen=True)
class Dataset:
    """The rows a report runs over, plus the catalog of what can be shown about them."""

    key: str
    scope: Optional[str]
    columns: Tuple[Column, ...]
    date_bases: Tuple[DateBasis, ...]
    # FROM + joins + the fixed predicates that define the row set. Carries no columns;
    # the engine adds whichever the layout asks for.
    base: Callable[[Any], Select]
    # Required when scope == "company": the column the session's company scope filters on.
    company_column: Optional[ColumnElement] = None
    # Years the dataset actually holds rows for, newest first. Optional: without it the
    # filter bar offers the current year and the four before it.
    years: Optional[Callable[[Any], List[int]]] = None
    #: The select param whose single value is the company a run reads (scope="company").
    #: The engine checks the value against the caller's grant (403 outside it), defaults
    #: it to the caller's current company, and names the company in the title block.
    company_param: Optional[str] = None
    #: The line's product id column, when the rows are product lines. A brand-scoped session
    #: (CONTACT-BRAND-SCOPE) keeps only lines of in-scope products: the datasets outer-join
    #: Product, where the ORM criterion would blank the product but still count the line.
    product_id_column: Optional[ColumnElement] = None

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError(
                f"Dataset '{self.key}' must declare scope='company' or scope='none' "
                "(a dataset that says nothing about scope is how one company's rows "
                "end up in another company's report)"
            )
        if self.scope == "company" and self.company_column is None:
            raise ValueError(
                f"Dataset '{self.key}' declares scope='company' but names no company_column"
            )
        if not self.date_bases:
            raise ValueError(f"Dataset '{self.key}' declares no date basis")
        keys = [c.key for c in self.columns]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"Dataset '{self.key}' repeats column key(s): {sorted(duplicates)}")

    def column(self, key: str) -> Optional[Column]:
        return next((c for c in self.columns if c.key == key), None)

    def basis(self, key: str) -> Optional[DateBasis]:
        return next((b for b in self.date_bases if b.key == key), None)


# ------------------------------------------------------------------------- params


@dataclass(frozen=True)
class DateBasisParam:
    key: str
    label: str
    default: str


@dataclass(frozen=True)
class PeriodParam:
    key: str
    label: str
    #: The period the screen opens on. A CALLABLE (e.g. ``current_year_period``) when it
    #: means "now", so the answer is the user's year rather than the process's boot year.
    default: Union[Dict[str, Any], Callable[[], Dict[str, Any]]]

    def resolved_default(self) -> Dict[str, Any]:
        return dict(self.default() if callable(self.default) else self.default)


@dataclass(frozen=True)
class SelectParam:
    """A filter the user picks values for. The param IS the predicate."""

    key: str
    label: str
    multi: bool
    default: Tuple[str, ...]
    # (value, label) pairs; a callable so an options list can come from the database.
    options: Callable[[Any], Sequence[Tuple[str, str]]]
    # Given the query context and the chosen values (never empty), the WHERE fragment.
    condition: Callable[[Any, List[str]], Optional[ColumnElement]]
    clearable: bool = True


Param = Any  # DateBasisParam | PeriodParam | SelectParam


def default_value(param: Param) -> Any:
    """What a param means when the caller names no value for it."""
    if isinstance(param, PeriodParam):
        return param.resolved_default()
    if isinstance(param, SelectParam):
        return list(param.default)
    return param.default


# ------------------------------------------------------------------------ layouts


@dataclass(frozen=True)
class TickColumn:
    """One member of a tick group: the column id, its header, and the value it ticks on."""

    key: str
    label: str
    value: str


def period_year_span(count: int) -> Callable[[Any, str], List[TickColumn]]:
    """A FIXED band of year columns: the period's year and the ``count - 1`` after it.

    The workbook this foundation mirrors prints 2025..2028 whether or not a row falls in
    any of them, and a year with nothing in it is a visible empty column rather than a
    missing one. The ids are STABLE (``<source>_1`` .. ``<source>_4``) and only the labels
    move with the period: a derived id (``expected_delivery_year__2026``) outlives the
    result it came from, so the user's saved column order goes on naming a column that no
    longer exists the moment the period changes.
    """

    def members(ctx: Any, source: str) -> List[TickColumn]:
        first = ctx.period.start.year
        return [
            TickColumn(key=f"{source}_{index}", label=str(first + index - 1), value=str(first + index - 1))
            for index in range(1, count + 1)
        ]

    return members


@dataclass(frozen=True)
class TickGroup:
    """A dimension rendered as tick columns under a merged header.

    ``members`` is a callable of (query context, source key). Unset, the group renders one
    column per value PRESENT in the result - which is all a report needs until the columns
    have to stay put across periods (see ``period_year_span``).
    """

    source: str
    label: str
    members: Optional[Callable[[Any, str], Sequence[TickColumn]]] = None


@dataclass(frozen=True)
class DetailLayout:
    #: The detail tab's heading; a callable of the query context when it follows the
    #: dataset the run reads (the Yearly comparison's Invoiced basis lists documents).
    title: Union[str, Callable[[Any], str]]
    order_by: Callable[[Any], Sequence[ColumnElement]]
    groups: Tuple[TickGroup, ...] = ()
    key: str = "detail"
    #: Over the sync cap: "refuse" answers 422 (the register must be read whole), and
    #: "truncate" returns the first rows with `truncated` set, so a years-long summary
    #: is not refused because of the lines under it.
    cap: str = "refuse"


PIVOT_VARIANCES = frozenset({"last_two_rows"})
PIVOT_CHARTS = frozenset({"line"})


@dataclass(frozen=True)
class PivotLayout:
    title: str
    key: str = "summary"
    #: "last_two_rows": a VARIANCE row, the last row minus the one before it, over the
    #: columns the last row has (a year to date against the same months a year earlier).
    variance: Optional[str] = None
    #: "line": the screen draws the pivot as a line chart under the table, and the
    #: workbook writes a native chart under the block.
    chart: Optional[str] = None
    #: The screen prints whole ringgit; the workbook keeps the sen.
    whole_units: bool = False
    #: The column-totals row. A year-by-month table has no use for 2024 + 2025 + 2026.
    column_totals: bool = True

    def __post_init__(self) -> None:
        if self.variance is not None and self.variance not in PIVOT_VARIANCES:
            raise ValueError(f"Pivot '{self.title}' declares an unknown variance '{self.variance}'")
        if self.chart is not None and self.chart not in PIVOT_CHARTS:
            raise ValueError(f"Pivot '{self.title}' declares an unknown chart '{self.chart}'")


@dataclass(frozen=True)
class WorkbookSpec:
    """How the exported workbook READS: the title block, the header words, the widths.

    Every field here is a value a report supplies, never a rule the renderer knows. The
    sponsorship report mirrors a register the client keeps by hand, down to their own
    spelling of SPONSHER PROJECT; report #2 says nothing and gets its column labels
    uppercased, which is the plain version of the same layout (AC-G7, AC-G8, AC-G10).
    """

    #: The name printed on the letterhead, and it WINS: the client's legal name belongs to
    #: the report, not to a settings row (the live install's still says "Metronic").
    #: `system_settings.name` is the fallback, for a report that names no company (AC-G7).
    company_name: str
    department: Optional[str] = None
    #: What the SHEET calls this report. The client's own file says SPONSORSHIP where the
    #: screen says Sponsorship report; unset, the definition's title is used.
    report_title: Optional[str] = None
    #: Catalog key or tick-group source -> the header the client's own sheet prints.
    #: Anything unnamed falls back to the column's label, uppercased.
    headers: Dict[str, str] = field(default_factory=dict)
    #: Catalog key or tick-group source -> Excel column width (characters).
    column_widths: Dict[str, float] = field(default_factory=dict)
    default_width: float = 16.0
    #: The summary's last column group: every measure totalled across the row.
    summary_row_total_label: str = "TOTAL"
    #: The summary's column-totals row ("TOTAL SALES" on the client's own sheet).
    summary_total_row_label: str = "TOTAL"
    #: One sheet per month of the period. A three-year comparison must not write 33.
    month_sheets: bool = True
    #: A select param key: the summary sheet writes one block per chosen value, one under
    #: the other (both channels of the yearly comparison on one page, as the PDF has them).
    sheet_per: Optional[str] = None
    #: The period line reads "AS AT <last day>" rather than the range.
    period_as_at: bool = False
    #: The money cells' number format; unset, the accounting RM format.
    money_format: Optional[str] = None
    #: What a money cell with no value holds. "" leaves it empty, which a chart skips.
    no_value: str = "-"


# --------------------------------------------------------------------- definition


@dataclass(frozen=True)
class ReportDefinition:
    key: str
    title: str
    permission: str
    dataset: Dataset
    params: Tuple[Param, ...]
    detail: DetailLayout
    pivot: PivotLayout
    # {"params": {...}, "detail": {"columns": [...], "order": [...]},
    #  "pivot": {"rows": ..., "cols": ..., "measures": [...]}}
    default_view: Dict[str, Any]
    workbook: WorkbookSpec
    #: The installable module that owns this report. The routes check it per request, and
    #: a definition that names none is refused (fail closed).
    module_key: Optional[str] = None
    #: A line printed under the filter bar and in the workbook's title block, given the
    #: query context (the basis of a sales report).
    note: Optional[Callable[[Any], str]] = None
    #: Which tab the screen opens on.
    opens_on: str = "detail"
    #: One report over more than one row set: (select param key, {value: dataset}). A run
    #: whose param names a value here reads that dataset; any other value reads `dataset`.
    #: The Yearly comparison's Basis = Invoiced reads billing documents, not sales order
    #: lines (finance S1). The datasets share the company param, the date basis key and the
    #: default view's pivot axes, so one saved view runs on any of them.
    datasets_by: Optional[Tuple[str, Dict[str, "Dataset"]]] = None

    def dataset_for(self, values: Dict[str, Any]) -> Dataset:
        """The dataset a run with these bound param values reads."""
        if self.datasets_by is None:
            return self.dataset
        key, by_value = self.datasets_by
        chosen = values.get(key) or []
        return by_value.get(chosen[0], self.dataset) if len(chosen) == 1 else self.dataset

    def datasets(self) -> List[Dataset]:
        """Every dataset this report can read, `dataset` first."""
        extra = list(self.datasets_by[1].values()) if self.datasets_by else []
        return [self.dataset] + [d for d in extra if d is not self.dataset]

    def catalog(self) -> List[Column]:
        """Every column of every dataset, once per key, `dataset`'s own first: what the
        Columns panel offers. A column the chosen dataset lacks is left out of that run."""
        seen: Dict[str, Column] = {}
        for dataset in self.datasets():
            for column in dataset.columns:
                seen.setdefault(column.key, column)
        return list(seen.values())


def validate(definition: ReportDefinition) -> None:
    """Fail at import time rather than on the first run of the screen."""
    for dataset in definition.datasets():
        _validate_against(definition, dataset)
    if definition.datasets_by is not None:
        key = definition.datasets_by[0]
        if not any(isinstance(p, SelectParam) and p.key == key for p in definition.params):
            raise ValueError(
                f"Report '{definition.key}' picks its dataset by '{key}', which is not a select param"
            )
        for dataset in definition.datasets():
            if (dataset.company_param, dataset.scope) != (
                definition.dataset.company_param,
                definition.dataset.scope,
            ):
                raise ValueError(
                    f"Report '{definition.key}' dataset '{dataset.key}' names another company param or scope"
                )


def _validate_against(definition: ReportDefinition, dataset: Dataset) -> None:
    view = definition.default_view
    pivot = view.get("pivot") or {}

    for role in ("rows", "cols"):
        key = pivot.get(role)
        column = dataset.column(key) if key else None
        if column is None or column.tag != "dimension":
            raise ValueError(
                f"Report '{definition.key}' default view names '{key}' as its pivot {role}, "
                "which is not a catalog dimension"
            )
    if pivot.get("rows") == pivot.get("cols"):
        raise ValueError(
            f"Report '{definition.key}' default view pivots '{pivot.get('rows')}' against itself"
        )
    for key in pivot.get("measures") or ():
        column = dataset.column(key)
        if column is None or column.tag != "measure":
            raise ValueError(
                f"Report '{definition.key}' default view names '{key}' as a measure, "
                "which is not a catalog measure"
            )
    catalog = {c.key for c in definition.catalog()}
    for key in (view.get("detail") or {}).get("columns") or ():
        if key not in catalog:
            raise ValueError(
                f"Report '{definition.key}' default view names detail column '{key}', "
                "which the dataset catalog does not hold"
            )
    for group in definition.detail.groups:
        column = dataset.column(group.source)
        if column is None or column.tag != "dimension":
            raise ValueError(
                f"Report '{definition.key}' groups on '{group.source}', "
                "which is not a catalog dimension"
            )
    if definition.opens_on not in ("detail", "summary"):
        raise ValueError(f"Report '{definition.key}' opens on unknown tab '{definition.opens_on}'")
    if definition.detail.cap not in ("refuse", "truncate"):
        raise ValueError(f"Report '{definition.key}' declares an unknown detail cap")
    split = definition.workbook.sheet_per
    if split is not None and not any(
        isinstance(p, SelectParam) and p.key == split for p in definition.params
    ):
        raise ValueError(f"Report '{definition.key}' splits by '{split}', which is not a select param")
    company_param = dataset.company_param
    if company_param is not None and not any(
        isinstance(p, SelectParam) and p.key == company_param for p in definition.params
    ):
        raise ValueError(f"Report '{definition.key}' names company param '{company_param}' it lacks")
    basis = (view.get("params") or {}).get("date_basis")
    if basis is not None and dataset.basis(basis) is None:
        raise ValueError(
            f"Report '{definition.key}' default view uses date basis '{basis}', "
            "which the dataset does not offer"
        )
    for param in definition.params:
        if not isinstance(param, PeriodParam):
            continue
        # Local import: the engine imports THIS module, and a period a report cannot open
        # on should fail at import rather than 422 every time somebody opens the page.
        from app.services.reports.engine import resolve_period

        for candidate in (param.resolved_default(), (view.get("params") or {}).get(param.key)):
            if candidate is None:
                continue
            try:
                resolve_period(candidate)
            except Exception as exc:  # noqa: BLE001 - re-raised as a definition error
                raise ValueError(
                    f"Report '{definition.key}' param '{param.key}' declares a period the "
                    f"engine cannot resolve ({candidate!r}): {exc}"
                )


_REGISTRY: Dict[str, ReportDefinition] = {}


def register(definition: ReportDefinition) -> ReportDefinition:
    validate(definition)
    _REGISTRY[definition.key] = definition
    return definition


def get(key: str) -> Optional[ReportDefinition]:
    return _REGISTRY.get(key)


def all_definitions() -> List[ReportDefinition]:
    return sorted(_REGISTRY.values(), key=lambda d: d.title)
