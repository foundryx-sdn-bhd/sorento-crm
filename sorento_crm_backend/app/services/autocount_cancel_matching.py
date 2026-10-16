"""Exact attribution of key-less AutoCount cancel lines (AC-CANCEL-CRM, owner rule 13 Oct).

Pure: no database, so the rule is testable on its own. A line with a `FromDocDtlKey` is matched
by the caller to that exact line; this decides the rest, one group at a time. A group is one
order + item, plus the location for a PO / SPO (a Cancel SO groups on SO + item whatever the
location, owner 14 Oct): its order lines i with transferred qty T_i (AutoCount TransferedQty
less the live line-key cancels on that line), the group's moved qty D (DO qty for an SO, GRN qty
for a PO / SPO) and the key-less cancel lines, whose qty sums to C.

Only arithmetic that leaves one answer decides anything (no tie-break, no lowest-Seq fallback,
no guess):
1. No line in the group: unmatched (`item_not_on_order`).
2. One line: every cancel is that line's (`item`).
3. C = 0: nothing was cancelled (`group_none`).
4. D = 0 and C = sum(T): every line's whole transfer is its cancel, so each line's cancelled
   qty is its own T (`group_whole`). The cancel lines name the group, not a line: one cancel
   document can span lines, so only the per-line totals are known.
5. Anything else (D > 0, or C != sum(T)): which line was cancelled cannot be known. Unmatched
   (`undetermined`), kept at order + item level with no line.

The answer is a function of the whole group, never of the order its lines arrived in.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

MATCHED = "matched"
UNMATCHED = "unmatched"

REASON_ITEM = "item"
REASON_GROUP_NONE = "group_none"
REASON_GROUP_WHOLE = "group_whole"
REASON_UNDETERMINED = "undetermined"
REASON_ITEM_NOT_ON_ORDER = "item_not_on_order"


@dataclass(frozen=True)
class CancelIn:
    id: str
    qty: Decimal


@dataclass(frozen=True)
class CandidateIn:
    id: str
    transferred: Decimal  # T_i: TransferedQty less the live line-key cancels on the line


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    target_id: Optional[str]
    candidates: tuple[str, ...]


def resolve_group(cancels: list[CancelIn], candidates: list[CandidateIn],
                  moved: Decimal) -> dict[str, Outcome]:
    """Every key-less cancel line of one group, with how it was decided."""
    ids = tuple(sorted(c.id for c in candidates))
    if not candidates:
        answer = Outcome(UNMATCHED, REASON_ITEM_NOT_ON_ORDER, None, ())
    elif len(candidates) == 1:
        answer = Outcome(MATCHED, REASON_ITEM, candidates[0].id, ids)
    else:
        total = sum((c.qty for c in cancels), Decimal("0"))
        if total == 0:
            answer = Outcome(MATCHED, REASON_GROUP_NONE, None, ids)
        elif moved == 0 and total == sum((c.transferred for c in candidates), Decimal("0")):
            answer = Outcome(MATCHED, REASON_GROUP_WHOLE, None, ids)
        else:
            answer = Outcome(UNMATCHED, REASON_UNDETERMINED, None, ids)
    return {c.id: answer for c in cancels}


def as_decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))
