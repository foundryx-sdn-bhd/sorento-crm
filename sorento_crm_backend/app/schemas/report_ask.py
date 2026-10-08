"""`GET /api/v1/order-management/report-ask` (lane REPORT-ENGINE, PLAN-report-engine.md
section 10).

Every field is declared on purpose: `response_model` silently drops any field the schema does
not name (LESSONS-LEARNT.md).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class ReportAskFilter(BaseModel):
    key: str
    label: str
    values: List[str]


class ReportAskRow(BaseModel):
    rank: int
    name: str
    qty: int
    amount: float
    # REPORT-DRILLDOWN: the ids the row's name stands for (a location's code; "none" for the
    # "(no agent)" row), so a follow-up can rank within exactly the rows printed. Empty for a
    # channel or month row (not a drill source).
    keys: List[str] = []


class ReportAskTotal(BaseModel):
    qty: int
    amount: float


class ReportAskResponse(BaseModel):
    status: str  # ok | refused | busy
    message: Optional[str] = None
    basis: str
    basis_label: str
    measure: str
    sort: str = "desc"
    group_by: Optional[str] = None
    group_label: Optional[str] = None
    date_from: str
    date_to: str
    filters: List[ReportAskFilter] = []
    rows: List[ReportAskRow] = []
    more: int = 0
    total_count: int = 0
    total: ReportAskTotal
