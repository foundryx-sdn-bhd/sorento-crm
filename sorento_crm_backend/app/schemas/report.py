"""Wire shapes for the reporting foundation (PLAN-reporting-foundation).

These models ARE the contract documented in `sorento_crm_frontend/services/reportService.ts`,
field for field. A `response_model` silently drops anything it does not declare, so a field
missing here is a field the screen never sees - which is why every one of them is asserted
in tests/test_report_routes.py rather than trusted.

Money always travels as a DECIMAL STRING with two places ("1166830.70") or is ABSENT.
Absent is not zero: a form with no lines has no sample price and the workbook prints "-".
Every total in here is computed by the engine, so the screen and the exported workbook
cannot disagree.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

ReportColumnType = Literal["text", "money", "integer", "date", "bool"]
ReportColumnTag = Literal["dimension", "measure", "date", "text"]


class ReportColumn(BaseModel):
    key: str
    label: str
    type: ReportColumnType
    # Suggested DataGrid width; the user's saved sizing wins once they resize it.
    size: Optional[int] = None


class ReportCatalogColumn(ReportColumn):
    tag: ReportColumnTag
    unavailable_for: List[str] = []


class ReportColumnGroup(BaseModel):
    """A merged header cell over a run of detail columns (e.g. Expected year of delivery).

    ``source`` names the catalog dimension the group was derived from. A saved view stores
    the SOURCE, because the member keys are data-dependent (one tick column per year present)
    and a view written in 2026 must still mean "show the delivery years" in 2027.
    """

    label: str
    source: str
    keys: List[str]


ReportRowValue = Union[str, int, float, bool, None]


class ReportDetailLayout(BaseModel):
    key: str
    title: str
    columns: List[ReportColumn]
    column_groups: List[ReportColumnGroup]
    rows: List[Dict[str, ReportRowValue]]
    # measure key -> decimal string. A measure with nothing to total is absent.
    totals: Dict[str, str]
    # True when the run had more lines than the sync cap and the definition truncates
    # rather than refuses: `rows` are the first ones, `totals` still the whole set's.
    truncated: bool = False


class ReportPivotDimension(BaseModel):
    key: str
    label: str


class ReportPivotColumnDimension(ReportPivotDimension):
    # Ordered naturally (months chronological).
    values: List[str]
    # Optional display label per value; the frontend falls back to the raw value.
    value_labels: Optional[Dict[str, str]] = None


class ReportPivotLayout(BaseModel):
    key: str
    title: str
    row_dim: ReportPivotDimension
    col_dim: ReportPivotColumnDimension
    measures: List[ReportColumn]
    # Ordered row values, so the screen never has to rank a dimension it cannot rank.
    row_values: List[str]
    # row value -> column value -> measure key -> decimal string. Sparse: a missing cell
    # is absent, not zero.
    cells: Dict[str, Dict[str, Dict[str, str]]]
    row_totals: Dict[str, Dict[str, str]]
    col_totals: Dict[str, Dict[str, str]]
    grand_total: Dict[str, str]
    # The VARIANCE row (last row minus the one before, per column), when the definition
    # asks for one: column value -> measure key -> decimal string, sparse like `cells`.
    variance_row: Optional[Dict[str, Dict[str, str]]] = None
    variance_total: Optional[Dict[str, str]] = None
    variance_label: Optional[str] = None
    # Display label per ROW value when the row axis names its values ("01" -> "JAN");
    # absent otherwise, and the raw value is the label.
    row_value_labels: Optional[Dict[str, str]] = None
    # "line" or "bar" when the screen draws this table as a chart under it.
    chart: Optional[str] = None
    # The screen prints whole ringgit; the workbook keeps the sen.
    whole_units: bool = False
    # False when the column-totals row means nothing (years added to years).
    show_column_totals: bool = True


class ReportBlock(BaseModel):
    """One block of a split summary: the pivot for one value of the split param."""

    key: str
    title: str
    summary: ReportPivotLayout


class ReportLayouts(BaseModel):
    detail: ReportDetailLayout
    summary: ReportPivotLayout
    # One per chosen value of the definition's `sheet_per` param, in option order.
    # Absent when the definition does not split.
    blocks: Optional[List[ReportBlock]] = None


class ReportResult(BaseModel):
    """A run that came back. A run that did NOT is the 422 with `capped: true` on it, so
    there is no flag here: a result the caller is holding was never capped."""

    key: str
    period_label: str
    row_count: int
    layouts: ReportLayouts
    # A line the screen prints under the filter bar (the basis of a sales report).
    note: Optional[str] = None


class ReportSelectOption(BaseModel):
    value: str
    label: str


class ReportDateBasisParam(BaseModel):
    kind: Literal["date_basis"] = "date_basis"
    key: str
    label: str
    default: str
    options: List[ReportSelectOption]


class ReportPeriodParam(BaseModel):
    kind: Literal["period"] = "period"
    key: str
    label: str
    default: Dict[str, Any]
    # Years the dataset actually holds rows for, newest first.
    years: List[int]


class ReportSelectParamMeta(BaseModel):
    kind: Literal["select"] = "select"
    key: str
    label: str
    multi: bool
    clearable: bool
    default: List[str]
    options: List[ReportSelectOption]


ReportParamMeta = Union[ReportDateBasisParam, ReportPeriodParam, ReportSelectParamMeta]


class ReportViewDetailConfig(BaseModel):
    # Empty means THE WHOLE CATALOG: the screen asks for everything and hides client-side,
    # so ticking a column is instant. The export asks for exactly the visible columns.
    columns: List[str] = Field(default_factory=list)
    order: List[str] = Field(default_factory=list)


class ReportViewPivotConfig(BaseModel):
    rows: str
    cols: str
    measures: List[str] = Field(default_factory=list)


class ReportViewConfig(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)
    detail: ReportViewDetailConfig = Field(default_factory=ReportViewDetailConfig)
    pivot: ReportViewPivotConfig


class ReportView(BaseModel):
    id: str
    name: str
    is_shared: bool
    is_default: bool
    # Display name of the owner, never the user id (no UUID reaches the UI).
    owner_name: Optional[str] = None
    view: ReportViewConfig


class ReportViews(BaseModel):
    """Mine = the views the caller OWNS, published ones included (badged Shared).
    Shared = OTHER users' published views."""

    mine: List[ReportView]
    shared: List[ReportView]


class ReportMeta(BaseModel):
    key: str
    title: str
    permission: str
    params: List[ReportParamMeta]
    catalog: List[ReportCatalogColumn]
    default_view: ReportViewConfig
    # True when the caller holds `reports.views.publish`. Publish + Set as default are
    # ABSENT without it, never disabled.
    can_publish: bool
    # "detail" or "summary": the tab the screen opens on.
    opens_on: str = "detail"
    # The select param that picks the row set (null when the report reads one).
    dataset_param: Optional[str] = None
    # Per value of `dataset_param`, the pivot rows column the screen switches to (or null).
    pivot_rows_by: Optional[Dict[str, str]] = None


class ReportCatalogEntry(BaseModel):
    key: str
    title: str
    permission: str


class ReportCatalogResponse(BaseModel):
    reports: List[ReportCatalogEntry]


class ReportRunRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)
    # The view AS IT STANDS on screen, saved or not, so an unsaved change runs without
    # being saved first. null = the report default.
    view: Optional[ReportViewConfig] = None


class ReportExportResult(BaseModel):
    download_id: str
    filename: str


class ReportViewCreate(BaseModel):
    name: str
    view: ReportViewConfig


class ReportViewPublish(BaseModel):
    is_shared: bool = True
