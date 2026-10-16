"""OI-LINE-SPLIT: the whole-SO split view's wire shapes.

Contract: `documentation/plans/scm/PLAN-oi-line-split-16oct.md` ("Build design (final)",
API) and the frontend fixture `project-sales/_shared/__mocks__/lineSplits.ts`, which reads
quantities as JSON numbers. The server owns `base_qty` / `base_date`; the client never
sends them.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, condecimal


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
    #: The saved splits still act on Confirm: false once AutoCount moved on from the base
    #: they were made against (the view then warns "AutoCount changed").
    acting: bool = True
    #: `acting` / `applied` / `stale` (`LineFacts.split_state`): only a stale line warns
    #: "AutoCount changed" and reads its current base.
    split_state: str = "acting"
    delivered_qty: float = 0
    splits: List[LineSplitOut] = []


class LineSplitsOut(BaseModel):
    pso_id: str
    so_number: Optional[str] = None
    customer: Optional[str] = None
    project: Optional[str] = None
    lines: List[LineSplitLineOut] = []


#: Caps on one save (review round 1): a malformed or oversized body is a 422, never a 500.
MAX_SPLITS_PER_LINE = 50
MAX_LINES_PER_SAVE = 500
MAX_REMARK_LENGTH = 500


class LineSplitIn(BaseModel):
    # The column's own precision. "Above 0" is checked by the service, so its 422 names the
    # line (UAC B3).
    qty: condecimal(max_digits=15, decimal_places=4)  # type: ignore[valid-type]
    delivery_date: Optional[date] = None
    is_cancel: bool = False
    sits_on_line_id: Optional[str] = None
    remark: Optional[str] = Field(default=None, max_length=MAX_REMARK_LENGTH)


class LineSplitLineIn(BaseModel):
    line_id: str
    splits: List[LineSplitIn] = Field(default_factory=list, max_length=MAX_SPLITS_PER_LINE)


class LineSplitsBody(BaseModel):
    lines: List[LineSplitLineIn] = Field(default_factory=list, max_length=MAX_LINES_PER_SAVE)
