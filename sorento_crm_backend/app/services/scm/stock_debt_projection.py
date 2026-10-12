"""The Stock Debt running projection (STOCK-DEBT-PROJECTION, owner 12 Oct 2026).

PURE maths, no I/O, the same discipline `supply_assignment` keeps.

**The question.** "Do the open POs at the pool cover the sales orders that come after
them?" The month view cannot say: a month states its own month and nothing else (R37), and
a document covers a line only through a link (R45). The projection answers it as a DISPLAY,
never as an assignment: nothing is pinned, no line changes status.

**The rule** (owner, 12 Oct 2026: "only consider pool, those project locations are matched
already"):

    carried_in(M) = carry(M-1)                 (0 before the first month)
    projected(M)  = month(M) + added(M) + carried_in(M)
    carry(M)      = clamp(projected(M), 0, carried_in(M) + pool_free(M))

- `month(M)` is the page's own figure, unchanged: the project bins are matched already.
- `added(M)` is the month figure of the site pools the page does not read (outside its
  span), from the same walk over those pools alone: their supply less their own orders.
- `pool_free(M)` is every pool's spare dated in M, in the span or outside it. It is
  already inside `month(M) + added(M)`, so it is never added again; it only caps what may
  CARRY from M.
- Only pool stock carries. A month's own spare is spent on its own debt first (project
  spare cannot carry, so it is used or lost), and the carry is capped at the pool stock
  that month could have held. Debt never carries: a month that ends short starts the next
  one at 0 carried, exactly as R37 reads it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Sequence

from app.services.scm.supply_assignment import EPSILON


@dataclass(frozen=True)
class ProjectedMonth:
    key: str
    #: What the page shows for the month without the projection (R37).
    month_balance: float
    #: Pool stock still spare from earlier months.
    carried_in: float
    #: The month figure of the pools outside the page's span (their supply less their
    #: own orders). Negative when those pools' own orders go short.
    pool_added: float
    projected: float


def _round(value: float) -> float:
    value = round(value, 4)
    return 0.0 if abs(value) <= EPSILON else value


def project_months(
    keys: Sequence[str],
    month_balance: Mapping[str, float],
    *,
    pool_free: Mapping[str, float],
    pool_added: Mapping[str, float],
) -> List[ProjectedMonth]:
    """One `ProjectedMonth` per key, in the order given (the caller passes consecutive
    months from the first month anything can land in)."""
    out: List[ProjectedMonth] = []
    carry = 0.0
    for key in keys:
        own = float(month_balance.get(key, 0.0))
        added = float(pool_added.get(key, 0.0))
        carried_in = carry
        projected = own + added + carried_in
        pool_held = carried_in + float(pool_free.get(key, 0.0))
        carry = max(0.0, min(projected, pool_held))
        out.append(
            ProjectedMonth(
                key=key,
                month_balance=_round(own),
                carried_in=_round(carried_in),
                pool_added=_round(added),
                projected=_round(projected),
            )
        )
    return out


__all__ = ["ProjectedMonth", "project_months"]
