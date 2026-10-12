"""The confirm-time guard on a Reserve (R14, `PLAN-scm-planning-inline-decisions.md` D6).

The captain, 27 August 2026: the server refuses a reserve that exceeds what is ON HAND at a
location minus what OTHER lines have already confirmed there, and the message names the
location and the earlier order.

Why it is worth a rule of its own when the ladder already caps a proposal: the board lets a
planner COMPOSE an amendment by hand, and a hand-typed 15 against 10 on hand used to come
back as "BRW-AM now has 9 free for this line, and 15 was asked for" - true, and it does not
say who has the other one or where to go and look. The refusal now names SO383850.

The two cases the UAC pins (AC-E1, AC-E2): 15 asked holds the 9 left and records 6 short, 9
confirms in full. Since the owner rule of 12 Oct 2026 (OI-CLOSED-LINE) the guard no longer
refuses: the shortfall is a notice and only what is on hand less other holds is written.

Postgres, blank scratch schema, every FK target seeded here (PRINCIPLES).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.base import company_scope
from app.models.project_so import SOLineAllocation, SOSupplyDecision
from app.services import project_seed_service

from ._pg_fixture import blank_session
from .test_so_supply_confirmation import (
    _client,
    _core_line,
    _core_so,
    _line_payload,
    _product,
    _project_line,
    _project_so,
    _restore,
    _sorento,
    _stock,
    _suffix,
    _user,
    _warehouse,
)

MARKER = "zzt-guard"
BASE = "/api/v1/project-sales"


def _uid() -> str:
    return str(uuid.uuid4())


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
        own_wh = _warehouse(db, f"ZZT{stem}A-AM", segment="project")
        pool_wh = _warehouse(db, f"ZZT{stem}P", segment="dealer")
        own_wh.pool_warehouse_id = pool_wh.id
        db.flush()
        # BRW-AM's own numbers: 10 on hand, and a pool deep enough that nothing else is
        # short here - the guard, not a capacity floor, has to be what refuses.
        _stock(db, product, own_wh, on_hand=10)
        _stock(db, product, pool_wh, on_hand=500)
        db.commit()
        client, originals = _client(db, actor)
        world = _World(db, company_id, actor, project, product, own_wh, pool_wh)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, world
        finally:
            _restore(originals)


def _order_with_line(world, *, qty, so_number=None, line_no=10):
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


def _hold_one_at_the_own_location(client, world):
    """SO383850's part: an EARLIER order that has already confirmed 1 at this location."""
    earlier, core_so, line = _order_with_line(
        world, qty="1", so_number="ZZT-SO383850", line_no=7
    )
    response = client.post(
        f"{BASE}/sales-orders/{earlier.id}/confirm",
        json={
            "lines": [
                _line_payload(line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "1"}])
            ]
        },
    )
    assert response.status_code == 200, response.text
    return core_so


def _holds(db, lines, warehouse):
    """{line id: qty held at `warehouse`}, a fresh read."""
    db.expire_all()
    out = {}
    for row in db.query(SOLineAllocation).filter(
        SOLineAllocation.so_line_id.in_([line.id for line in lines]),
        SOLineAllocation.warehouse_id == warehouse.id,
    ):
        out[row.so_line_id] = out.get(row.so_line_id, Decimal("0")) + row.qty
    return out


def test_a_reserve_beyond_on_hand_less_other_lines_holds_confirms_holding_what_is_left_and_records_the_short(api):
    """AC-E1: 10 on hand, 1 already reserved by SO383850, 15 asked for. The line confirms,
    holds the 9 that are left and records 6 short; the two orders together never hold more
    than the 10 on hand.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    held_by = _hold_one_at_the_own_location(client, world)
    mine, _core_so, line = _order_with_line(world, qty="15", line_no=22)

    response = client.post(
        f"{BASE}/sales-orders/{mine.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "15"}]
                )
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # The shortfall is recorded against the right line with the right figures: 9 of the 10
    # on hand are left (1 is the earlier order's), so 6 of the 15 asked are short.
    notices = body["stock_short_notices"]
    assert [notice["line_no"] for notice in notices] == [22], notices
    assert f"only 9 free at {world.own_wh.warehouse_code}" in notices[0]["reason"], notices
    assert ", 6 short" in notices[0]["reason"], notices
    assert _holds(db, [line], world.own_wh) == {line.id: Decimal("9")}
    # Together with SO383850's 1, nothing held at the location exceeds what is on hand.
    db.expire_all()
    total = sum(
        (row.qty for row in db.query(SOLineAllocation).filter(
            SOLineAllocation.warehouse_id == world.own_wh.id
        )),
        Decimal("0"),
    )
    assert total == Decimal("10"), total
    assert (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == mine.id,
            SOSupplyDecision.state == "active",
        )
        .count()
        == 1
    )


def test_the_same_line_confirms_at_what_is_actually_left(api):
    """AC-E2: 9 is exactly on hand less the other line's hold, and it goes through."""
    client, world = api
    db = world.db
    _hold_one_at_the_own_location(client, world)
    mine, _core_so, line = _order_with_line(world, qty="9", line_no=22)

    response = client.post(
        f"{BASE}/sales-orders/{mine.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "9"}]
                )
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == mine.id,
            SOSupplyDecision.state == "active",
        )
        .count()
        == 1
    )


def test_re_confirming_a_lines_own_hold_is_not_refused_by_its_own_hold(api):
    """A line that already holds 9 here and is confirmed again unchanged must not be told
    that 9 of the 10 are taken - it is the one holding them."""
    client, world = api
    mine, _core_so, line = _order_with_line(world, qty="9", line_no=22)
    payload = {
        "lines": [
            _line_payload(line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "9"}])
        ]
    }
    assert (
        client.post(f"{BASE}/sales-orders/{mine.id}/confirm", json=payload).status_code
        == 200
    )

    again = client.post(f"{BASE}/sales-orders/{mine.id}/confirm", json=payload)

    assert again.status_code == 200, again.text


def test_two_lines_of_one_confirmation_are_never_sold_the_same_pile(api):
    """The guard runs over the whole payload, not per line in isolation: 6 and 6 against 10
    on hand is 12, so the first line holds its 6, the second holds the 4 left and is the
    one recorded 2 short. The two never hold the same units.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    order = _project_so(db, world.project, so_id=core_so.id)
    first_core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="6")
    second_core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="6")
    first = _project_line(
        db, order, line_no=10, product=world.product, core_line=first_core
    )
    second = _project_line(
        db, order, line_no=20, product=world.product, core_line=second_core
    )
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                _line_payload(
                    first.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "6"}]
                ),
                _line_payload(
                    second.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "6"}]
                ),
            ]
        },
    )

    assert response.status_code == 200, response.text
    notices = response.json()["stock_short_notices"]
    assert [notice["line_no"] for notice in notices] == [20], notices
    assert f"only 4 free at {world.own_wh.warehouse_code}" in notices[0]["reason"], notices
    assert ", 2 short" in notices[0]["reason"], notices
    assert _holds(db, [first, second], world.own_wh) == {
        first.id: Decimal("6"),
        second.id: Decimal("4"),
    }
    assert (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order.id)
        .count()
        == 1
    )
