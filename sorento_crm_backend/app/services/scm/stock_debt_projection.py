"""The Stock Debt running projection (STOCK-DEBT-PROJECTION, owner 12 Oct 2026).

PURE maths, no I/O, the same discipline `supply_assignment` keeps.

**The question.** "Do the open POs cover the sales orders that come after them?" The month
view cannot say: a month states its own month and nothing else (R37), and a document
covers a line only through a link (R45). The projection answers it as a DISPLAY, never as
an assignment: nothing is pinned, no line changes status.

**The rule** (owner, 12 Oct 2026, option D; crew answer (b) the same day):

    remainder(M)    = carried_in(M) + reserve_supply(M)
    projected(M)    = project(M) + remainder(M)
    carried_in(M+1) = max(0, min(remainder(M), projected(M)))

- `project(M)` is the project lines' own month figure off the page's walk: the project
  bins' spare in M less their lines' shortfalls in M. Pool supply reaches a project line
  ONLY through a link (the S/O ref on the PO/SPO line, an OI placement, a confirmed
  decision), incoming or landed alike, and the walk already pins every link to its line
  at any date. So this term is exactly what the drill's Covered by leaves owed. Unlinked
  pool on hand and unlinked pool POs/SPOs never count.
- `reserve_supply(M)` is the one exception: a PROJECT RESERVE's unlinked incoming documents
  dated in M (a PO on its Delivery date, an SPO not landed on its ETA, past due today),
  less what a link already took. A project reserve is a pool with `segment = project`
  (HQ-PROJECT-RESERVE, #1597): its supply belongs to projects in general.
- Reserve supply is applied to the EARLIEST shortfall first and only a SURPLUS carries: a
  month's own project spare is spent on its own shortfall first (it cannot carry), and a
  month that ends short starts the next one at 0 carried. Debt never accumulates (R37).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Sequence

from app.services.scm.supply_assignment import EPSILON


@dataclass(frozen=True)
class ProjectedMonth:
    key: str
    #: The project lines' own month figure (the bins' spare less their shortfalls).
    month_balance: float
    #: Reserve supply still spare from earlier months.
    carried_in: float
    #: A project reserve's unlinked incoming supply dated in this month.
    reserve_supply: float
    projected: float
    #: Reserve supply that carries into the next month.
    carried_out: float


def _round(value: float) -> float:
    value = round(value, 4)
    return 0.0 if abs(value) <= EPSILON else value


def project_months(
    keys: Sequence[str],
    project_balance: Mapping[str, float],
    *,
    reserve_supply: Mapping[str, float],
) -> List[ProjectedMonth]:
    """One `ProjectedMonth` per key, in the order given (the caller passes consecutive
    months from the first month anything can land in)."""
    out: List[ProjectedMonth] = []
    carry = 0.0
    for key in keys:
        own = float(project_balance.get(key, 0.0))
        supply = float(reserve_supply.get(key, 0.0))
        carried_in = carry
        remainder = carried_in + supply
        projected = own + remainder
        carry = max(0.0, min(remainder, projected))
        out.append(
            ProjectedMonth(
                key=key,
                month_balance=_round(own),
                carried_in=_round(carried_in),
                reserve_supply=_round(supply),
                projected=_round(projected),
                carried_out=_round(carry),
            )
        )
    return out


__all__ = ["ProjectedMonth", "project_months"]
