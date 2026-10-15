"""The Stock Debt snapshot payloads (STOCK-DEBT-SNAPSHOT, AC-1 .. AC-16).

A snapshot row is a `StockDebtRow` and a snapshot line is a `StockDebtDemandLine`, extended;
nothing about those shapes is restated here. `response_model` drops undeclared fields, so every
served field is declared and asserted by name in `tests/scm/test_stock_debt_snapshots.py`.
"""
from __future__ import annotations

from datetime import date as DateType, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.stock_debt import (
    ExportSplit,
    StockDebtDemandLine,
    StockDebtFilters,
    StockDebtPagination,
    StockDebtRow,
    StockDebtTotals,
)

Decision = Literal["reserve", "buy", "ignore"]
#: The `decision` filter of the rows listing.
DecisionFilter = Literal["decided", "undecided", "reserve", "buy", "ignore"]


class SnapshotCreate(BaseModel):
    name: str
    filters: StockDebtFilters = Field(default_factory=StockDebtFilters)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("name must be 1 to 120 characters")
        return value


class SnapshotDecisionIn(BaseModel):
    decision: Optional[Decision] = None
    warehouse_id: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class SnapshotExportIn(BaseModel):
    split: ExportSplit = "none"
    show_aux: bool = False


class SnapshotSummary(BaseModel):
    """The header fields the listing and the page both serve (AC-8, AC-9)."""

    id: str
    name: str
    #: The filters as taken, plus the location codes and supplier names they named then
    #: (AC-6), so a later rename does not change what the snapshot says it was taken under.
    filters: Dict[str, Any]
    filters_label: str
    row_count: int
    decided_count: int
    created_by_name: Optional[str] = None
    created_at: datetime
    can_delete: bool


class SnapshotDetail(SnapshotSummary):
    months: List[str]
    tba_month: Optional[str] = None
    totals: StockDebtTotals
    prev_id: Optional[str] = None
    next_id: Optional[str] = None


class SnapshotList(BaseModel):
    data: List[SnapshotSummary]
    pagination: StockDebtPagination


class SnapshotRow(StockDebtRow):
    row_id: str
    decision: Optional[Decision] = None
    decision_warehouse_id: Optional[str] = None
    decision_warehouse_code: Optional[str] = None
    decision_note: Optional[str] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None


class SnapshotRowList(BaseModel):
    data: List[SnapshotRow]
    pagination: StockDebtPagination


class SnapshotOiRow(BaseModel):
    """One order inquiry row on a line: the frozen fields (AC-5) and the live reading
    (AC-12). `after_snapshot` marks a row linked to the line only after the snapshot."""

    oi_id: str
    inquiry_no: str
    row_id: str
    qty: float
    delivery_date: Optional[DateType] = None
    stock_location: Optional[str] = None
    verb: str
    state: str
    now_state: Optional[str] = None
    reserve_state: Optional[Literal["requested", "parked", "reserved", "cancelled"]] = None
    remaining: Optional[float] = None
    requestable: bool = False
    after_snapshot: bool = False


class SnapshotLineNow(BaseModel):
    so_status: Optional[str] = None
    open_qty: float = 0.0


class SnapshotLine(StockDebtDemandLine):
    bucket: str
    sales_order_line_id: str
    oi_rows: List[SnapshotOiRow] = []
    now: SnapshotLineNow


class SnapshotLineList(BaseModel):
    data: List[SnapshotLine]
