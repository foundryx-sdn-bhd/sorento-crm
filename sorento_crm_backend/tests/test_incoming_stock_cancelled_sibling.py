"""AC-CONSIGN-CRM round 2, UAC-15 / UAC-16: a fully or partly cancelled SPO sibling.

UAC-15 (incoming edge): one container line, product P, shipped 20. SPO allocation A (10, a
GRN picked 10, stated_received lagging) and sibling B (10, stated_received 10, cancelled by XP).
The goods A's GRN brought plus B's cancelled quantity account for the whole line, so nothing
is still incoming. A partial cancel (B cancelled 4) leaves B's other 6 still to come.

UAC-16 (group spread): the group's approved pick total is spread over each live line's
allocated quantity LESS what was cancelled, never onto a cancelled part.

Postgres only (blank schema), every row seeded here. Harness = tests/test_grn_spo_forward_matching.py.
"""
from __future__ import annotations

import uuid

from app.models.procurement import InboundShipmentLine, SPOAllocation
from app.services.rules import shipping_order_rules
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.incoming_stock_service import IncomingStockService
from app.services.procurement_service import InboundShipmentService, PickingHeaderService

from .test_grn_spo_forward_matching import SPO, _scope_listeners, world  # noqa: F401

AC = shipping_order_rules.AUTOCOUNT_SOURCE_SYSTEM


def _alloc(world, product, wh, *, qty, line_no, stated=None, cancelled=0, company=None):
    aid = world.allocation(
        product_id=product, warehouse_id=wh, quantity=qty, stated=stated, cancelled=cancelled,
        source_system=AC, company_id=company or DEFAULT_COMPANY_ID,
    )
    row = world.db.get(SPOAllocation, aid)
    row.spo_line_number = line_no
    world.db.flush()
    return aid


def _remaining_incoming(world, product) -> int:
    db = world.db
    InboundShipmentService(db).refresh_shipment_line_statuses(world.shipment)
    res = IncomingStockService(db).incoming_for_product(product_ids=[product])
    if res.get("empty"):
        return 0
    return sum(int(s["remaining_incoming_quantity"]) for b in res["data"] for s in b["shipments"])


def _scenario(world, *, b_cancelled, b_stated):
    product, wh = world.product(), world.warehouse()
    db = world.db
    db.add(InboundShipmentLine(
        id=str(uuid.uuid4()), shipment_id=world.shipment, product_id=product,
        quantity_shipped=20, quantity_received=0, line_status="in_transit",
        company_id=DEFAULT_COMPANY_ID,
    ))
    db.flush()
    a = _alloc(world, product, wh, qty=10, line_no=2)
    _alloc(world, product, wh, qty=10, line_no=1, stated=b_stated, cancelled=b_cancelled)
    grn = world.grn(status="approved", spo_number=SPO, company_id=DEFAULT_COMPANY_ID)
    world.line(header_id=grn, product_id=product, warehouse_id=wh, quantity=10,
               allocation_id=a, company_id=DEFAULT_COMPANY_ID)
    db.commit()
    return product


def test_fully_cancelled_sibling_leaves_nothing_incoming(world):
    product = _scenario(world, b_cancelled=10, b_stated=10)
    assert _remaining_incoming(world, product) == 0


def test_partly_cancelled_sibling_leaves_its_uncancelled_part_incoming(world):
    product = _scenario(world, b_cancelled=4, b_stated=4)
    assert _remaining_incoming(world, product) == 6


def _group(world, *, b_cancelled, b_stated):
    product, wh = world.product(), world.warehouse()
    a = _alloc(world, product, wh, qty=10, line_no=2)
    b = _alloc(world, product, wh, qty=10, line_no=1, stated=b_stated, cancelled=b_cancelled)
    grn = world.grn(status="approved", spo_number=SPO, company_id=DEFAULT_COMPANY_ID)
    world.line(header_id=grn, product_id=product, warehouse_id=wh, quantity=10,
               allocation_id=a, company_id=DEFAULT_COMPANY_ID)
    world.db.commit()
    alloc_a = world.db.get(SPOAllocation, a)
    PickingHeaderService(world.db)._sync_received_for_allocations([alloc_a])
    world.db.flush()  # expire_all() would discard the unflushed writes
    world.db.expire_all()
    return world.db.get(SPOAllocation, a), world.db.get(SPOAllocation, b)


def test_group_spread_skips_a_fully_cancelled_earlier_line(world):
    a, b = _group(world, b_cancelled=10, b_stated=10)
    assert a.quantity_received == 10
    assert b.quantity_received == 10  # keeps its stated figure


def test_group_spread_gives_a_partly_cancelled_line_only_its_uncancelled_capacity(world):
    a, b = _group(world, b_cancelled=4, b_stated=4)
    # received INCLUDES the cancelled qty: B = max(stated, min(share 6 + cancelled 4, allocated))
    assert b.quantity_received == 10
    assert a.quantity_received == 4
    balance = (a.allocated_quantity - a.quantity_received) + (b.allocated_quantity - b.quantity_received)
    assert balance == 6  # 20 - 4 cancelled - 10 received
