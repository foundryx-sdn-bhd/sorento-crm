"""S3 - Reserve add-location (manual BRW), AC-3.2 / AC-3.3 server side.

`ReserveAddDialog.tsx` (Phase 1 FE, lane commit c5426c118) lets a planner add ANY location
with free stock to a line's Reserve section, the site pool included - candidates are the
cell's own `BoardCellLocation[]` filtered to free stock > 0, site pool sorted first (R-A).
`ConfirmReserveComponent` already accepts any warehouse and needed no server change for S3
(PLAN, S3): "Server: no change ... Add one test that a pool warehouse is accepted in
Reserve." What is pinned here is that the EXISTING guard covers a site-pool warehouse
exactly as it covers an own location:

* AC-3.2 - a Reserve at the site pool (not the line's own bin) within what it holds is
  accepted and lands in `so_supply_decisions` naming that warehouse.
* AC-3.3 - a Reserve at the site pool above what is on hand there, once another order
  already holds part of it, confirms holding only what is left and records the rest short
  (owner rule 12 Oct 2026, OI-CLOSED-LINE) - the same guard `tests/test_confirm_reserve_guard.py`
  pins for an own location, retargeted at the pool a planner would add by hand.

Postgres via `tests/_pg_fixture.py::blank_session`, never sqlite. Every FK target is seeded
here or through the shared helper functions in `tests/test_so_supply_confirmation.py`,
never borrowed off an existing row - CI's database is empty.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.base import company_scope
from app.models.project_so import SOLineAllocation, SOSupplyDecision
from app.services import project_seed_service

from tests._pg_fixture import blank_session
from tests.test_so_supply_confirmation import (
    BASE,
    _active_snapshots,
    _client,
    _core_line,
    _core_so,
    _line_payload,
    _product,
    _project_line,
    _project_so,
    _restore,
    _shape,
    _sorento,
    _stock,
    _suffix,
    _user,
    _warehouse,
)

MARKER = "zzt-reserve-add"


class _World:
    def __init__(self, db, company_id, actor, project, product, own_wh, pool_wh):
        self.db = db
        self.company_id = company_id
        self.actor = actor
        self.project = project
        self.product = product
        self.own_wh = own_wh
        self.pool_wh = pool_wh


@pytest.fixture()
def api():
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        actor = _user(db, f"{MARKER} Eling")
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=actor,
            developer_party_id=None,
            title=f"{MARKER} Tuju Residences",
        )
        product = _product(db)
        stem = _suffix()
        own_wh = _warehouse(db, f"ZZT{stem}-AM", segment="project")
        pool_wh = _warehouse(db, f"ZZT{stem}-BRW", segment="dealer")
        own_wh.pool_warehouse_id = pool_wh.id
        db.flush()
        db.commit()
        client, originals = _client(db, actor)
        world = _World(db, company_id, actor, project, product, own_wh, pool_wh)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, world
        finally:
            _restore(originals)


def _order_with_line(world, *, qty, so_number=None, line_no=10):
    """A sales order whose one line names the line's OWN bin, exactly as a live order would -
    the planner is about to add the SITE POOL to Reserve by hand (S3, R-G), which has
    nothing to do with which location the demand line itself sits at."""
    db = world.db
    core_so = _core_so(db, world.company_id)
    if so_number:
        core_so.so_number = so_number
    order = _project_so(db, world.project, so_id=core_so.id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty)
    line = _project_line(
        db, order, line_no=line_no, product=world.product, core_line=core_line
    )
    db.commit()
    return order, core_so, line


def test_a_manually_added_pool_reserve_within_on_hand_confirms_and_names_the_pool(api):
    """AC-3.2: picking the site pool in `ReserveAddDialog` and saving a Reserve within what
    it holds writes an active decision naming THAT warehouse, not the line's own bin."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    order, _core_so, line = _order_with_line(world, qty="20")

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "20"}]
                )
            ]
        },
    )

    assert response.status_code == 200, response.text
    snapshot = _active_snapshots(db, order.id)[0]
    assert _shape(snapshot["components"]) == [
        ("reserve", "20", world.pool_wh.warehouse_code, "pool")
    ]


def test_a_manually_added_pool_reserve_above_on_hand_holds_what_is_left_and_records_the_short(api):
    """AC-3.3: the server's on-hand guard is the same one `test_confirm_reserve_guard.py`
    pins for an own location, retargeted at a SITE POOL a planner just added by hand - it
    caps a Reserve at what is on hand there less what another order already holds: the
    earlier order's 1 of the 10 stays, this line holds the 9 left and is recorded 6 short.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=10)

    held_by_order, held_by_core, held_by_line = _order_with_line(
        world, qty="1", so_number="ZZT-SO383850", line_no=7
    )
    held = client.post(
        f"{BASE}/sales-orders/{held_by_order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    held_by_line.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "1"}],
                )
            ]
        },
    )
    assert held.status_code == 200, held.text

    order, _core_so, line = _order_with_line(world, qty="15", line_no=22)

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "15"}]
                )
            ]
        },
    )

    assert response.status_code == 200, response.text
    notices = response.json()["stock_short_notices"]
    assert [notice["line_no"] for notice in notices] == [22], notices
    assert f"only 9 free at {world.pool_wh.warehouse_code}" in notices[0]["reason"], notices
    assert ", 6 short" in notices[0]["reason"], notices
    db.expire_all()
    held = {
        (row.so_line_id, row.warehouse_id): row.qty
        for row in db.query(SOLineAllocation).filter(
            SOLineAllocation.so_line_id.in_([held_by_line.id, line.id])
        )
    }
    assert held == {
        (held_by_line.id, world.pool_wh.id): Decimal("1"),
        (line.id, world.pool_wh.id): Decimal("9"),
    }, held
    assert (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id,
                SOSupplyDecision.state == "active")
        .count()
        == 1
    )
