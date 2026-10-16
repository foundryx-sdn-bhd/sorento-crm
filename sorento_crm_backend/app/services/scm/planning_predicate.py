"""ONE spelling of "this bin takes part in fulfilment planning", for every reader of it.

**The rule.** A warehouse is IN fulfilment planning when it is ACTIVE and its
`fulfilment_planning` flag is true. Nothing else: not the code's suffix, not the segment,
not `counts_as_available`. Migration 443 seeds the flag from the client's own group
convention once, and from then on it is configuration an admin edits on the Warehouses
screen - deriving it at runtime would bake one client's naming into the engine, which is
the mistake `pool_warehouse_id` and `segment` were both stored to avoid.

**What being OFF means.** Everything (R17). A bin that is off contributes no on hand, no
incoming and no sales-order line to the ladder, the fulfilment board, the donor lists or
the Stock Debt view. That is stronger than `counts_as_available`, where the stock is merely
not sellable while the location's demand still counts against the group; here the location
is not part of the question at all.

**What being OFF does NOT change: what a location HOLDS.** `free_stock_by_location`,
`stock_levels_by_location` and `held_stock_by_location` are the public stock seams the
board's stock detail and the location-stock screen print, and they answer for every ACTIVE
bin. A bin nobody plans against still holds its stock, and 1,928 on hand printed beside 0
free reads as a defect rather than as a policy. The narrowing belongs to what a proposal
may DRAW (`project_supply_service._drawable_free_stock`) and to what the ownership groups
NET, never to the figures a screen states.

**Why one module.** The same test is applied by `project_supply_service`
(`_planning_warehouses`, the group siblings, the cross-group donor walk), by the ownership
group index it hands `group_netting`, and by the board (its group index and its demand
rows). Four files agreeing by hand is a rule that holds until one of them is edited - the
exact reasoning `pool_predicate.py` records for the site-pool test, and it is this module's
whole justification.

**Site pools are OFF, and that is not a bug.** A pool is reached through
`pool_warehouse_id`, never as an ownership group, so the pool rung reads its own set
(`project_supply_service._site_pool_warehouses`) and is unaffected by this predicate.
Anything that nets the two together has to union the two sets explicitly, which is what
`_pile_facts` does.
"""
from __future__ import annotations

from typing import Any, Optional

#: The verdict a line at a flagged-off bin carries, on the board and at confirm
#: (AC-S1-6). One string, shared, because the board renders it and the confirm refuses
#: with it, and two spellings would read as two different rules.
OUTSIDE_FULFILMENT_PLANNING = "Outside fulfilment planning"


def fulfilment_planning_predicate(model: Optional[Any] = None):
    """The same rule as an ORM clause, for a caller writing a SQLAlchemy query.

    `model` defaults to `app.models.inventory.Warehouse`; pass an alias when the query
    joins the table twice. Imported lazily so this module stays importable from anywhere
    without dragging the model graph in behind it.
    """
    if model is None:
        from app.models.inventory import Warehouse as model
    return (model.is_active.is_(True)) & (model.fulfilment_planning.is_(True))


def in_fulfilment_planning(warehouse: Any) -> bool:
    """The same rule for a caller holding an ORM row rather than writing a query.

    A row that predates the column (a stub built in a test, a detached object) reads as
    False, which is the column's own default and the safe answer: a location nobody has
    decided about is not planned against.
    """
    if warehouse is None:
        return False
    return bool(getattr(warehouse, "is_active", False)) and bool(
        getattr(warehouse, "fulfilment_planning", False)
    )


#: Documents whose stock sits at a bin the ladder does not plan (a consignment at CON). Their
#: lines are planned at that bin, so they never earn the OUTSIDE_FULFILMENT_PLANNING verdict.
#: The bin stays flagged off, so no DRAW reads it and sales order lines never see it.
PLANNED_AT_OWN_BIN_DOC_TYPES = frozenset({"consignment"})


def planned_at_own_bin(warehouse: Any, doc_type: Optional[str]) -> bool:
    """Whether this line is planned AT its own bin: its doc type is in
    `PLANNED_AT_OWN_BIN_DOC_TYPES` AND the bin is not in fulfilment planning.

    Regardless of the bin's `is_active`: the bin is the document's own location, and the dev
    CON is inactive as well as flagged off. A consignment line at a bin that IS in planning
    (active, flag on) is planned like a sales order line (CONSIGN-PLAN card R6), so the doc
    type alone is never enough.
    """
    return (
        doc_type in PLANNED_AT_OWN_BIN_DOC_TYPES
        and warehouse is not None
        and not in_fulfilment_planning(warehouse)
    )


def outside_fulfilment_planning(warehouse: Any, doc_type: Optional[str] = None) -> bool:
    """Whether this bin earns the `OUTSIDE_FULFILMENT_PLANNING` verdict: ACTIVE, flag off.

    A line whose `doc_type` is in `PLANNED_AT_OWN_BIN_DOC_TYPES` never earns it.

    Deliberately not `not in_fulfilment_planning(...)`. An INACTIVE warehouse was already
    outside everything the ladder reads before this flag existed, and it keeps the verdict
    it carried then - telling a planner that a retired bin is "outside fulfilment planning"
    points them at a switch that is not the one to flip.
    """
    if warehouse is None or doc_type in PLANNED_AT_OWN_BIN_DOC_TYPES:
        return False
    return bool(getattr(warehouse, "is_active", False)) and not bool(
        getattr(warehouse, "fulfilment_planning", False)
    )


__all__ = [
    "OUTSIDE_FULFILMENT_PLANNING",
    "PLANNED_AT_OWN_BIN_DOC_TYPES",
    "fulfilment_planning_predicate",
    "in_fulfilment_planning",
    "planned_at_own_bin",
    "outside_fulfilment_planning",
]
