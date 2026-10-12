"""The Stock Debt running projection (STOCK-DEBT-PROJECTION, owner 12 Oct 2026).

PURE maths, no I/O, the same discipline `supply_assignment` keeps.

**The question.** "Do the open POs at the pool cover the sales orders that come after
them?" The month view cannot say: a month states its own month and nothing else (R37), and
a document covers a line only through a link (R45). The projection answers it as a DISPLAY,
never as an assignment: nothing is pinned, no line changes status.

**The rule** (owner, 12 Oct 2026: "only consider pool, those project locations are matched
already"; "pool a": pool supply first covers the dealer/retail orders due in its month, and
only the remainder goes to project shortfalls):

    available(M)  = carried_in(M) + pool_supply(M)
    remainder(M)  = available(M) - dealer(M)
    projected(M)  = project(M) + remainder(M)
    carried_in(M+1) = max(0, min(remainder(M), projected(M)))

- `project(M)` is the project bins' own month figure, matched as the page matches them:
  their spare in M less their lines' shortfalls in M.
- `pool_supply(M)` is pool supply dated in M (on hand today, a PO on its Delivery date, an
  SPO on its arrival, anything past due today), less what a project line's hold took of it.
- `dealer(M)` is the open orders at pool locations due in M.
- Pool stock is applied to the EARLIEST shortfall first and only a SURPLUS carries: a
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
    #: The project bins' own month figure (their spare less their shortfalls).
    month_balance: float
    #: Pool stock still spare from earlier months.
    carried_in: float
    #: Pool supply dated in this month.
    pool_supply: float
    #: Open orders at pool locations (dealer / retail) due in this month.
    dealer_demand: float
    projected: float
    #: Pool stock that carries into the next month.
    carried_out: float


def _round(value: float) -> float:
    value = round(value, 4)
    return 0.0 if abs(value) <= EPSILON else value


def project_months(
    keys: Sequence[str],
    project_balance: Mapping[str, float],
    *,
    pool_supply: Mapping[str, float],
    dealer_demand: Mapping[str, float],
) -> List[ProjectedMonth]:
    """One `ProjectedMonth` per key, in the order given (the caller passes consecutive
    months from the first month anything can land in)."""
    out: List[ProjectedMonth] = []
    carry = 0.0
    for key in keys:
        own = float(project_balance.get(key, 0.0))
        supply = float(pool_supply.get(key, 0.0))
        dealer = float(dealer_demand.get(key, 0.0))
        carried_in = carry
        remainder = carried_in + supply - dealer
        projected = own + remainder
        carry = max(0.0, min(remainder, projected))
        out.append(
            ProjectedMonth(
                key=key,
                month_balance=_round(own),
                carried_in=_round(carried_in),
                pool_supply=_round(supply),
                dealer_demand=_round(dealer),
                projected=_round(projected),
                carried_out=_round(carry),
            )
        )
    return out


__all__ = ["ProjectedMonth", "project_months"]
