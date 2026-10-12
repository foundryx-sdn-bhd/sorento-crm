"""Availability is the OWNERSHIP GROUP'S, never one warehouse's (ladder v4, captain 26
August 2026 - `PLAN-scm-cs-planning-uat.md` section 1d).

Sorento books every sales order at `BRW-<group>` while the stock sits at any
`<site>-<group>`, so a per-warehouse reading misleads twice in one breath: on
`B2155-NL-BLUE` the book shows `BRW-IB` 5290 on hand against 27,804 owed and `MWH-IB`
7000 on hand against nothing, and the two together are ONE pile that nets -15,514. Read
warehouse by warehouse, `MWH-IB` looks like 7000 units free to promise; read as the group
it belongs to, there is nothing to promise at all.

So one function answers "how much of this product does that set of locations actually
have", and every surface that needs the answer calls it: the ladder's own-group rung, its
pool rung, its cross-group borrow rung, the cell popover's subtotals, the order-inquiry
link walk, and (S12) the WhatsApp stock answer. None of them can disagree, because there
is nothing for them to disagree with.

The arithmetic is AutoCount's own, per location and then summed:

    net = sum(on hand) - sum(SO qty) + sum(SPO qty)

signed and never floored - "the IB group nets -15514" is the fact a planner needs, and a
floor of zero reports it as "nothing left", which is a different and much weaker thing to
know. SPO sits INSIDE the net deliberately (section 1d, rung 1): an SPO arriving at
`BRW-IB` is owed to the IB backlog before it is owed to any one line, and counting it
beside the net as separate "incoming" would promise the same units twice. Overdue rows are
in it too, by the 26 August ruling that the book is trusted: a supplier being late is not
evidence the goods stopped existing.

NO DATABASE HERE except through the one constructor at the bottom. The class is fed the
three figures it needs as a plain mapping, so the caller that has already read them (the
board prefetches the same triple for its own cell table) pays for no second read, and the
caller that has not (`netting_for_products`) gets them in one batched pass with no board,
no supply service and no import cycle in sight.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

ZERO = Decimal("0")

#: The three figures a location states, in AutoCount's own vocabulary. The keys of the
#: triple mapping this module is fed, and the keys `project_supply_service._pile_read`
#: already produces.
ON_HAND = "on_hand"
SO_QTY = "so_qty"
SPO_QTY = "spo_qty"

_EMPTY_TRIPLE = {ON_HAND: ZERO, SO_QTY: ZERO, SPO_QTY: ZERO}


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return ZERO
    return Decimal(str(value))


def group_of_warehouse_code(code: Optional[str]) -> Optional[str]:
    """The ownership-group suffix a warehouse code carries: `BRW-BB` -> `BB`.

    Re-exported from `sales_agent_service` rather than restated, so "which group is this
    location in" has one answer in the ladder, in the popover and here.
    """
    from app.services.scm import sales_agent_service

    return sales_agent_service.group_of_warehouse_code(code)


@dataclass(frozen=True)
class LocationNet:
    """One location's contribution to a net, with the three figures it came from.

    Carried rather than summed away because the ladder DRAWS per location - the stock has
    to physically move from somewhere - and because a subtotal a reader cannot decompose
    is a number they have to take on trust.
    """

    warehouse_id: str
    location: str
    on_hand: Decimal
    so_qty: Decimal
    spo_qty: Decimal

    @property
    def net(self) -> Decimal:
        """Signed, never floored: an oversold location says so."""
        return self.on_hand - self.so_qty + self.spo_qty


@dataclass(frozen=True)
class NetPosition:
    """What a set of locations holds between them, and where it sits."""

    #: Signed. `<= 0` means the set has nothing to offer, however much sits at any one of
    #: its locations.
    net: Decimal
    by_location: Tuple[LocationNet, ...]

    @property
    def offer(self) -> Decimal:
        """What an OUTSIDE caller may draw from the set: the net, or nothing.

        Right for a set this line is not itself booked in - a donor group, or the site
        pools - where every unit of the demand in the net belongs to somebody else. A
        line's OWN group is a different question, because its own quantity is inside that
        net: the ladder answers it with the group's supply less what is queued ahead of the
        line (`ProjectSupplyService._group_offer`), so the line at the front of a queue is
        still served from a group that is short overall.
        """
        return self.net if self.net > ZERO else ZERO

    def positive_locations(self) -> Tuple[LocationNet, ...]:
        """The locations stock can actually be taken FROM, biggest position last is not the
        order - the caller decides draw order (own location first, then by site)."""
        return tuple(entry for entry in self.by_location if entry.net > ZERO)


class GroupNetting:
    """The one reader of availability (section 1d).

    Fed a triple per `(product_id, warehouse_id)` and the warehouse codes behind those ids.
    A pair with no triple counts as three zeroes, which is what an absent `stock` row and
    an absent sales-order line together mean: the last upload counted none there.

    Every answer is memoised per instance, because one board asks the same
    `(product, group)` question once per line and a selection of 300 lines shares a handful
    of products.
    """

    def __init__(
        self,
        *,
        triples: Mapping[Tuple[str, str], Mapping[str, Any]],
        warehouse_codes: Mapping[str, str],
        pool_warehouse_ids: Iterable[str] = (),
        planning_warehouse_ids: Optional[Iterable[str]] = None,
    ) -> None:
        """`planning_warehouse_ids` narrows the OWNERSHIP GROUPS to the bins flagged into
        fulfilment planning (`planning_predicate`, R17): a bin that is off is not a member
        of its group, so its on hand, its backlog and its SPO are outside every net.

        It is a separate argument rather than a filter on `warehouse_codes` because the
        SITE POOLS have to stay in the codes - they are flagged off by design, and
        `pools_net` still has to be able to net them. `None` means "every code given is in
        planning", which is what a caller handing in one already-filtered set means.
        """
        self._triples = triples
        self._codes = {str(k): str(v) for k, v in warehouse_codes.items() if v}
        self._pool_ids = {str(pool_id) for pool_id in pool_warehouse_ids}
        planning = (
            None
            if planning_warehouse_ids is None
            else {str(wid) for wid in planning_warehouse_ids}
        )
        # warehouse ids per group code, and the pool ids, resolved once.
        self._by_group: Dict[str, list] = {}
        for warehouse_id, code in self._codes.items():
            if planning is not None and warehouse_id not in planning:
                continue
            group = group_of_warehouse_code(code)
            if group:
                self._by_group.setdefault(group, []).append(warehouse_id)
        self._memo: Dict[Tuple[str, str, str], NetPosition] = {}

    # ----------------------------------------------------------------- the questions

    def group_net(self, product_id: Optional[str], group_code: Optional[str]) -> NetPosition:
        """What the whole ownership group holds of this product (rung 2, section 1d).

        `group_code` is the suffix, `IB` / `BB` / `IR`. An unknown group nets zero over no
        locations, which is the honest answer: nothing was found to look at.

        The group's membership is the bins FLAGGED INTO FULFILMENT PLANNING (R17,
        `planning_warehouse_ids`): a bin that is off holds stock nobody plans against, so
        counting it here would put supply into a net the ladder may never draw on.
        """
        if not product_id or not group_code:
            return NetPosition(net=ZERO, by_location=())
        key = ("group", product_id, group_code.strip().upper())
        if key not in self._memo:
            self._memo[key] = self._net_over(
                product_id, self._by_group.get(key[2], ())
            )
        return self._memo[key]

    def donor_group_net(
        self, product_id: Optional[str], group_code: Optional[str]
    ) -> NetPosition:
        """What a DONOR group could lend (rung 4, section 1d).

        The same arithmetic as `group_net` under the name the cross-group borrow rung uses,
        so the call site reads as the rule it is applying. A single warehouse's on hand
        means nothing if its group nets negative, and that is exactly what this refuses to
        let a caller not notice.
        """
        return self.group_net(product_id, group_code)

    def pools_net(self, product_id: Optional[str]) -> NetPosition:
        """ALL FIVE site pools as ONE pile (rung 3, section 1d).

        BRW, DC1, MWH, RSW and WH3 are not five separate answers to "is there shared stock":
        the captain's ruling is that the shared pile is shared, so `BRW -103` and `DC1 +1`
        net to -102 and neither of them offers anything.
        """
        if not product_id:
            return NetPosition(net=ZERO, by_location=())
        key = ("pools", product_id, "")
        if key not in self._memo:
            self._memo[key] = self._net_over(product_id, self._pool_ids)
        return self._memo[key]

    def groups(self) -> Tuple[str, ...]:
        """Every ownership group these warehouses carry, for a caller walking donors."""
        return tuple(sorted(self._by_group))

    def group_of(self, warehouse_id: Optional[str]) -> Optional[str]:
        """The group a warehouse id belongs to, or `None` for a pool / ungrouped location."""
        if not warehouse_id:
            return None
        return group_of_warehouse_code(self._codes.get(str(warehouse_id)))

    def is_pool(self, warehouse_id: Optional[str]) -> bool:
        return bool(warehouse_id) and str(warehouse_id) in self._pool_ids

    # ------------------------------------------------------------------- arithmetic

    def _net_over(
        self, product_id: str, warehouse_ids: Iterable[str]
    ) -> NetPosition:
        entries = []
        for warehouse_id in sorted(warehouse_ids, key=lambda wid: self._codes.get(wid, "")):
            triple = self._triples.get((product_id, warehouse_id)) or _EMPTY_TRIPLE
            entries.append(
                LocationNet(
                    warehouse_id=warehouse_id,
                    location=self._codes.get(warehouse_id, ""),
                    on_hand=_dec(triple.get(ON_HAND)),
                    so_qty=_dec(triple.get(SO_QTY)),
                    spo_qty=_dec(triple.get(SPO_QTY)),
                )
            )
        return NetPosition(
            net=sum((entry.net for entry in entries), ZERO),
            by_location=tuple(entries),
        )


# --------------------------------------------------------------------------- reads


def netting_for_products(
    db, product_ids: Sequence[str], *, planning_only: bool = False
) -> GroupNetting:
    """A `GroupNetting` over every ACTIVE warehouse, read here and nowhere else.

    The constructor for a caller with no board and no supply service in hand - the
    WhatsApp stock answer (S12) is the one this exists for, and it must be able to say
    "the IB group nets -15514" without building a fulfilment board to find out.

    Three queries, batched over the products asked about, and the same three
    `project_supply_service._pile_read` runs - which is why the board hands its own
    already-read triple to the constructor above instead of calling this.

    `planning_only` narrows the ownership groups to the bins flagged into fulfilment
    planning (R17), and it is OPT-IN because this door is not the ladder's. The
    order-inquiry link walk reads "this group has no members" as "this group nets zero" and
    offers its purchase-order lines on the strength of it, so narrowing every caller
    silently turned 114 open lines at unflagged bins into offers no plan stands behind.
    The ladder and the board do not come through here at all: they build `GroupNetting`
    themselves off the triple they have already read, with `planning_warehouse_ids` set
    (`project_supply_service.netting`).
    """
    from app.models.inventory import Stock, Warehouse
    from app.models.order import SalesOrder, SalesOrderLine
    from app.models.procurement import InboundShipment, SPOAllocation
    from app.services.scm import spo_supply
    from app.services.scm.planning_predicate import in_fulfilment_planning
    from app.services.scm.pool_predicate import is_project_reserve
    from app.services.scm.demand import demand_qty, is_open_demand
    from sqlalchemy import func

    ids = [str(pid) for pid in product_ids if pid]
    warehouses = db.query(Warehouse).filter(Warehouse.is_active.is_(True)).all()
    codes = {str(w.id): w.warehouse_code for w in warehouses if w.warehouse_code}
    # A PROJECT RESERVE (HQ, `pool_predicate.is_project_reserve`) is no site pool here:
    # its stock is never the dealers', so it is outside `pools_net`.
    pools = {
        str(w.pool_warehouse_id) for w in warehouses if w.pool_warehouse_id
    } & set(codes)
    pools -= {
        str(w.id)
        for w in warehouses
        if is_project_reserve(w.segment, is_pool=str(w.id) in pools)
    }
    # Every ACTIVE warehouse is read (the pools have to be in `codes` for `pools_net`). Only
    # a caller asking the PLANNING question narrows the groups to the flagged bins (R17);
    # `None` leaves every code a member of its own group, which is what this seam answered
    # before the flag existed and what its callers still expect.
    planning = (
        {str(w.id) for w in warehouses if in_fulfilment_planning(w)} & set(codes)
        if planning_only
        else None
    )
    if not ids:
        return GroupNetting(
            triples={},
            warehouse_codes=codes,
            pool_warehouse_ids=pools,
            planning_warehouse_ids=planning,
        )

    triples: Dict[Tuple[str, str], Dict[str, Decimal]] = {}

    def slot(product_id: Any, warehouse_id: Any) -> Dict[str, Decimal]:
        return triples.setdefault(
            (str(product_id), str(warehouse_id)),
            {ON_HAND: ZERO, SO_QTY: ZERO, SPO_QTY: ZERO},
        )

    for stock in (
        db.query(Stock)
        .join(Warehouse, Warehouse.id == Stock.warehouse_id)
        .filter(Stock.product_id.in_(ids), Warehouse.is_active.is_(True))
        .all()
    ):
        slot(stock.product_id, stock.warehouse_id)[ON_HAND] = _dec(stock.quantity_on_hand)

    owed = demand_qty()
    for row in (
        db.query(
            SalesOrderLine.product_id,
            SalesOrderLine.warehouse_id,
            func.coalesce(func.sum(owed), 0).label("owed"),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .filter(
            SalesOrderLine.product_id.in_(ids),
            SalesOrderLine.warehouse_id.in_(list(codes)),
            SalesOrder.status == "open",
            is_open_demand(),
        )
        .group_by(SalesOrderLine.product_id, SalesOrderLine.warehouse_id)
        .all()
    ):
        slot(row.product_id, row.warehouse_id)[SO_QTY] = _dec(row.owed)

    for row in (
        db.query(
            SPOAllocation.product_id,
            SPOAllocation.warehouse_id,
            SPOAllocation.allocated_quantity,
            SPOAllocation.quantity_received,
        )
        # OUTER JOIN, and it is not optional: `open_incoming_clauses` names
        # `InboundShipment.id`, so without the join SQLAlchemy adds `inbound_shipments` to
        # the FROM list as a CROSS JOIN - every allocation is then returned once per
        # un-arrived shipment in the whole table (15 of them on the dev copy, so the SPO
        # leg read fifteen times over) and NOT AT ALL on a database with none, which is
        # every fresh CI schema. An SPO with no container booked is the ordinary case since
        # migration 420, so the join has to be outer.
        .outerjoin(
            InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id
        )
        .filter(
            SPOAllocation.product_id.in_(ids),
            SPOAllocation.warehouse_id.in_(list(codes)),
            *spo_supply.open_incoming_clauses(),
        )
        .all()
    ):
        balance = _dec(row.allocated_quantity) - _dec(row.quantity_received)
        if balance > ZERO:
            slot(row.product_id, row.warehouse_id)[SPO_QTY] += balance

    return GroupNetting(
        triples=triples,
        warehouse_codes=codes,
        pool_warehouse_ids=pools,
        planning_warehouse_ids=planning,
    )


def group_net(db, product_id: str, group_code: str) -> NetPosition:
    """One product, one group, straight off the database (S12's entry point)."""
    return netting_for_products(db, [product_id]).group_net(product_id, group_code)


def pools_net(db, product_id: str) -> NetPosition:
    """One product across every site pool, straight off the database (S12's entry point)."""
    return netting_for_products(db, [product_id]).pools_net(product_id)


__all__ = [
    "GroupNetting",
    "LocationNet",
    "NetPosition",
    "group_net",
    "group_of_warehouse_code",
    "netting_for_products",
    "pools_net",
]
