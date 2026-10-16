"""OI-LINE-SPLIT: the whole-SO split view's wire shapes.

Contract: `documentation/plans/scm/PLAN-oi-line-split-16oct.md` ("Build design (final)",
API) and the frontend fixture `project-sales/_shared/__mocks__/lineSplits.ts`, which reads
quantities as JSON numbers. The server owns `base_qty` / `base_date`; the client never
sends them.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


class LineSplitOut(BaseModel):
    id: str
    qty: float
    delivery_date: Optional[date] = None
    is_cancel: bool = False
    #: The line this split sits on; `None` = a new line not in AutoCount yet (also what a
    #: split reads once the line it sat on is cancelled, UAC C2).
    sits_on_line_id: Optional[str] = None
    remark: str = ""


class LineSplitLineOut(BaseModel):
    line_id: str
    line_no: Optional[int] = None
    item_code: str = ""
    location: str = ""
    base_qty: float
    base_date: Optional[date] = None
    now_qty: float
    now_date: Optional[date] = None
    #: The line carries a pending AutoCount change (it is inside Confirm).
    pending_change: bool = False
    delivered_qty: float = 0
    splits: List[LineSplitOut] = []


class LineSplitsOut(BaseModel):
    pso_id: str
    so_number: Optional[str] = None
    customer: Optional[str] = None
    project: Optional[str] = None
    lines: List[LineSplitLineOut] = []


class LineSplitIn(BaseModel):
    qty: Decimal
    delivery_date: Optional[date] = None
    is_cancel: bool = False
    sits_on_line_id: Optional[str] = None
    remark: Optional[str] = None


class LineSplitLineIn(BaseModel):
    line_id: str
    splits: List[LineSplitIn] = []


class LineSplitsBody(BaseModel):
    lines: List[LineSplitLineIn] = []
