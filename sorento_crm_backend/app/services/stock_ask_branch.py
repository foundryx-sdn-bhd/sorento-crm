"""The four-branch stock ask decision (chatbot stock ask v2 S3, R6, R14).

`branch()` is the one seam every "Availability only" dealer answer passes through
(replaces `app/services/stock_verdict.py`, which judged a richer available/incoming/
purchase mix for the old dealer-stock-verdict path). Pure, no I/O: `x` and `available`
are already resolved by the caller (`app.services.stock_ask_limits.effective` for x,
`StockService._apply_stock_visibility` for available), and `shipment_date` is the R5
read's own result - the earliest still-incoming shipment with a packing list, any
location, or None when no such shipment qualifies.

R2: X unset is read by the caller as 0, so `q > x` is true for every q >= 1 until a
category opts in - there is no separate "cap unset" branch here, `cap_unset` is a fact
the caller carries alongside the branch (whether x was NULL), not something this
function derives from a bare 0.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

Branch = Literal["too_big", "in_stock", "incoming", "no_incoming"]


def branch(
    q: int, x: int, available: int, shipment_date: Optional[date]
) -> Branch:
    """R6: `q > x` -> `too_big`; `available >= q` -> `in_stock`; a shipment exists ->
    `incoming`; else `no_incoming`.

    AVAIL-MODE-REPLIES rule 2 (owner, 2 Oct 2026): some stock short of `q`, with `q`
    within `x`, is `in_stock` too - the dealer is told "got stock" and how many
    (`short_of`), never "no stock" while some is there."""
    if q > x:
        return "too_big"
    if available >= min(q, 1):
        # Covered, or some stock short of `q` (rule 2); `q` is always >= 1 here.
        return "in_stock"
    if shipment_date is not None:
        return "incoming"
    return "no_incoming"


def short_of(q: int, x: int, available: int) -> Optional[int]:
    """The available count an `in_stock` answer names: only when there is some stock,
    less than `q`, and `q` is within `x` (rule 2). None in every other case, so a count
    of ours is never shown above the category max (owner Q5 (a)) or when `q` is covered."""
    if q > x or available < 1 or available >= q:
        return None
    return available
