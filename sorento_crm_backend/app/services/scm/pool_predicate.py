"""ONE spelling of "a site pool", for every screen whose numbers have to foot.

**The rule.** A warehouse is a SITE POOL unless its `segment` says `project`. A location
nobody has classified counts: `COALESCE(segment, 'dealer')`, because an unclassified
warehouse is a warehouse somebody has not got round to, not a project bin - and treating it
as a bin would quietly drop real stock out of every figure on these screens.

**Why the two exclusions.** A PROJECT bin holds stock already committed to a named project,
so it is not supply anybody may plan against; an INACTIVE location holds stock nobody can
pick. Neither belongs in "what we have", so neither belongs in a cell whose lightbox lists
what we have.

**Why one module.** The SQL-text form of this predicate used to be written four times -
`container_request_service._pool_predicate`, `container_request_drill._POOL`,
`spo_conversion_service._ACTIVE_POOL` and `reorder_run_service`'s `is_dealer_expr` - each with
a comment saying it was "character for character" one of the others. That is a rule that only
holds while every copy agrees, which is why it stayed in one module even after R7 (below)
retired two of the four: `container_request_drill._spo_rows` and `spo_conversion_service.
_stock_context` no longer filter on it at all. The SQL-text form survives in exactly two SQL
callers now - `container_request_service` (the site/group SPLIT, not a filter - see the R7
carve-out) and `reorder_run_service` (its own on_hand netting, which did NOT widen) - plus the
ORM-level `is_site_pool()` a few other callers (`location_stock_service`,
`project_order_inquiry_service`, `supply_claim`) use to LABEL a row rather than drop one.

`segment` is the test, never the warehouse code's naming convention (a hyphen suffix is not a
classification - `project_supply_service._site_pool_warehouses` warns the same), and never
`pool_warehouse_id`, which drives the unrelated fulfilment-pool netting opt-in whose members
are not necessarily project-segment locations.

**The R7 carve-out (captain, 8 Sep 2026).** The container-request grid and the SPO planner
(`container_request_service._stock_context`, `container_request_drill._spo_rows`,
`spo_conversion_service._stock_context`) used to net SITE POOL supply only, on the "not
supply anybody may plan against" reasoning above. That reasoning did not survive contact with
those same screens' own demand side (`open_so_need`), which already counts project demand
alongside retail: a pool-only supply total against an all-classes demand total overstated the
ask by exactly the stock a project bin already held against its own order. Those three readers
now count EVERY active location, pool and project bin alike - this predicate still runs
underneath them, but only to SPLIT the counted total into its site and group halves for the
row's breakdown, never to exclude one half from it. Nothing else moved: `reorder_run_service`'s
own netting, and the other things this module's "not supply anybody may plan against" reasoning
still governs (`project_bin_lock`, `supply_claim`, `project_order_inquiry_service`), keep
reading this predicate as an exclusion, exactly as before.
"""
from __future__ import annotations

from typing import Optional

#: The segment on a project bin. Everything else, unset included, is a site pool.
PROJECT_SEGMENT = "project"

#: What an unclassified warehouse counts as.
_DEFAULT_SEGMENT = "dealer"


def site_pool_sql(alias: str = "w") -> str:
    """The segment half of the rule, for a query that states `is_active` itself.

    Takes its alias so one rule can be written into any query without a second spelling of
    it existing anywhere.
    """
    return f"(COALESCE({alias}.segment, '{_DEFAULT_SEGMENT}') <> '{PROJECT_SEGMENT}')"


def active_site_pool_sql(alias: str = "w") -> str:
    """The whole rule: active, and not a project bin."""
    return f"({alias}.is_active AND COALESCE({alias}.segment, '{_DEFAULT_SEGMENT}') <> '{PROJECT_SEGMENT}')"


#: The two ready-made forms for the common alias, so a caller that needs no alias of its own
#: reads as a constant rather than as a call.
SITE_POOL_SQL = site_pool_sql()
ACTIVE_SITE_POOL_SQL = active_site_pool_sql()


def is_site_pool(segment: Optional[str]) -> bool:
    """The same rule for a caller holding ORM rows rather than writing SQL."""
    return (segment or _DEFAULT_SEGMENT) != PROJECT_SEGMENT


def is_project_reserve(segment: Optional[str], *, is_pool: bool) -> bool:
    """A PROJECT RESERVE: a pool (some location's `pool_warehouse_id`) that is ALSO
    `segment = project` - HQ (`PLAN-hq-project-reserve-12oct.md`). Its stock is project
    stock in general: no dealer share, outside the dealer pools' net, never a dealer line's
    cover, and not the named project bin G12's lock protects. A project BIN (`BRW-BB`) is
    `segment = project` too but nobody's pool, so it stays a bin."""
    return bool(is_pool) and (segment or _DEFAULT_SEGMENT) == PROJECT_SEGMENT


__all__ = [
    "ACTIVE_SITE_POOL_SQL",
    "PROJECT_SEGMENT",
    "SITE_POOL_SQL",
    "active_site_pool_sql",
    "is_project_reserve",
    "is_site_pool",
    "site_pool_sql",
]
