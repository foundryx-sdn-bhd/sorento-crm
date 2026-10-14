"""Delivered qty consumes the Reserve/Borrow FIRST, then the Buy (owner rule, 14 Oct 2026).

SO424555 line 16, CB6633-NL (prod): decision Own 336 at BRW-IB + Buy 126, plan 462, 336
delivered, 126 outstanding. `delivered_credits` credited the delivered units to the Buy
first, so 126 of the Own stayed "held" (`held_qty_expr` = least(336, owed 126)) and Stock
Debt read "Covered by On hand BRW-IB 126" at a bin the goods had already left. Owner: the
goods left from BRW-IB, so the Own 336 is used up and the Buy covers the 126.

The rule applies on the WRITE (confirm, `_write_allocations`) and on the READ
(`held_qty_expr`, behind every `_hold_query` reader: the board's free stock and Stock
Debt's pins), so the existing prod row (qty 336, written before the delivery) reads 0 with
no data change. Fake codes; the reserve sits at the pool bin the confirm path accepts (the
rule is the same for an own-bin Reserve). Postgres only, every FK seeded here.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.project_so import (
    ALLOC_SOURCE_BRW,
    ALLOC_SOURCE_ORDER,
    SOLineAllocation,
)
from app.services.project_supply_service import (
    ProjectSupplyService,
    delivered_credits,
    held_qty_expr,
)
from app.services.scm.stock_debt_service import StockDebtService

from tests.test_planning_changes import (  # noqa: F401  (api is the fixture)
    BASE,
    _core_line,
    _core_so,
    _line_payload,
    _project_line,
    _project_so,
    _stock,
    _warehouse,
    api,
)

IN_WINDOW = date.today() + timedelta(days=30)


def _world(api, *, ordered, delivered, on_hand):
    client, world = api
    db = world.db
    stock = _stock(db, world.product, world.pool_wh, on_hand=on_hand)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered=ordered,
        qty_delivered=delivered, required_date=IN_WINDOW,
    )
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=16, product=world.product, core_line=core_line)
    db.commit()
    return client, world, order, line, core_line, stock


def _own_336_buy_126(world, line):
    return _line_payload(
        line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "336"}],
        buy_qty="126", buy_reason="ZZT order back the rest",
        amend_reason="ZZT own 336, buy 126",
    )


def _confirm(client, order, payload):
    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm", json={"lines": [payload]}
    )
    assert response.status_code == 200, response.text
    return response


def _written_stock_qty(db, line) -> Decimal:
    """What the confirm WROTE as stock holds (the Buy row is not stock)."""
    db.expire_all()
    return sum(
        (
            a.qty
            for a in db.query(SOLineAllocation).filter(
                SOLineAllocation.so_line_id == line.id,
                SOLineAllocation.source_type != ALLOC_SOURCE_ORDER,
            )
        ),
        Decimal("0"),
    )


def _held(db, world, line) -> Decimal:
    """What the shared hold read says this line holds, every bin: `_hold_query` with
    `held_qty_expr()`, the figure the board's free stock and Stock Debt both net."""
    db.expire_all()
    rows = (
        ProjectSupplyService(db)
        ._hold_query(
            [str(world.product.id)],
            exclude_line_ids=None,
            entities=(SOLineAllocation.so_line_id, held_qty_expr()),
        )
        .all()
    )
    return sum(
        (Decimal(str(qty)) for line_id, qty in rows if str(line_id) == str(line.id)),
        Decimal("0"),
    )


def _held_by_bin(db, world, line) -> dict:
    """`_held`, per bin: `{warehouse_id: held}` for this line."""
    db.expire_all()
    rows = (
        ProjectSupplyService(db)
        ._hold_query(
            [str(world.product.id)],
            exclude_line_ids=None,
            entities=(
                SOLineAllocation.so_line_id, SOLineAllocation.warehouse_id, held_qty_expr(),
            ),
        )
        .all()
    )
    out: dict = {}
    for line_id, warehouse_id, qty in rows:
        if str(line_id) == str(line.id):
            out[str(warehouse_id)] = out.get(str(warehouse_id), Decimal("0")) + Decimal(str(qty))
    return out


def _free_at_pool(db, world) -> Decimal:
    db.expire_all()
    free = ProjectSupplyService(db).free_stock_by_location([str(world.product.id)])
    return free.get((str(world.product.id), str(world.pool_wh.id)), Decimal("0"))


def _stock_debt_pins(db, world, core_line):
    """The on-hand holds Stock Debt pins this line with (`StockDebtService._holds`)."""
    db.expire_all()
    holds = StockDebtService(db)._holds([str(world.product.id)], {str(core_line.id)})
    return [h for h in holds if h.line_key == str(core_line.id)]


# ------------------------------------------------------------------ the credit itself


def test_delivered_credits_spend_the_reserve_before_the_buy():
    """Plan 462 = Reserve 336 + Buy 126, 336 delivered: all 336 credit the Reserve."""
    core = SimpleNamespace(
        qty_delivered=Decimal("336"), qty_cancelled=Decimal("0"), line_status="open",
    )
    entry = SimpleNamespace(
        reserve=[SimpleNamespace(qty=Decimal("336"))], borrow=[],
        buy_qty=Decimal("126"), timely_spo_qty=Decimal("0"),
    )

    reserve, borrow = delivered_credits(core, Decimal("462"), entry)

    assert reserve == [Decimal("336")]
    assert borrow == []


def test_delivered_credits_spend_reserve_then_borrow_and_only_then_the_buy():
    """Reserve 100 + Borrow 50 + Buy 20, 120 delivered: Reserve 100, Borrow 20, Buy 0."""
    core = SimpleNamespace(
        qty_delivered=Decimal("120"), qty_cancelled=Decimal("0"), line_status="open",
    )
    entry = SimpleNamespace(
        reserve=[SimpleNamespace(qty=Decimal("100"))],
        borrow=[SimpleNamespace(qty=Decimal("50"))],
        buy_qty=Decimal("20"), timely_spo_qty=Decimal("0"),
    )

    reserve, borrow = delivered_credits(core, Decimal("170"), entry)

    assert reserve == [Decimal("100")]
    assert borrow == [Decimal("20")]


def test_a_document_borrow_takes_no_delivered_credit():
    """A Borrow on a document (`supply_key`) is no bin the goods left from: the delivered
    units pass it by. Reserve 10, Borrow on an SPO 20, Borrow from stock 30, 25 delivered:
    Reserve 10, document Borrow 0, stock Borrow 15."""
    core = SimpleNamespace(
        qty_delivered=Decimal("25"), qty_cancelled=Decimal("0"), line_status="open",
    )
    entry = SimpleNamespace(
        reserve=[SimpleNamespace(qty=Decimal("10"))],
        borrow=[
            SimpleNamespace(qty=Decimal("20"), supply_key="spo:ZZT-ALLOC"),
            SimpleNamespace(qty=Decimal("30"), supply_key=None),
        ],
        buy_qty=Decimal("0"), timely_spo_qty=Decimal("0"),
    )

    reserve, borrow = delivered_credits(core, Decimal("60"), entry)

    assert reserve == [Decimal("10")]
    assert borrow == [Decimal("0"), Decimal("15")]


# ------------------------------------------------------------------------ the write


def test_confirm_after_delivery_writes_no_hold_for_the_reserve_the_goods_left_from(api):
    """CB6633-NL confirmed AFTER the 336 shipped: the Own 336 is used up, so no stock hold
    is written and the pool stays fully free; the Buy covers the 126 outstanding."""
    client, world, order, line, _core, _stock_row = _world(
        api, ordered="462", delivered="336", on_hand=500
    )

    _confirm(client, order, _own_336_buy_126(world, line))

    assert _written_stock_qty(world.db, line) == Decimal("0")
    assert _held(world.db, world, line) == Decimal("0")
    assert _free_at_pool(world.db, world) == Decimal("500")


# ------------------------------------------------------------------------- the read


def test_cb6633_nl_existing_hold_reads_zero_after_delivery_with_no_data_change(api):
    """The prod row: confirmed before delivery (Own row qty 336), then AutoCount delivers
    336 from that bin. The row is NOT rewritten, yet every reader sees no hold: the board's
    free stock at the bin is its on hand, and Stock Debt pins nothing on it."""
    client, world, order, line, core_line, stock = _world(
        api, ordered="462", delivered="0", on_hand=500
    )
    _confirm(client, order, _own_336_buy_126(world, line))
    assert _written_stock_qty(world.db, line) == Decimal("336")

    # AutoCount delivers 336 out of the bin; nothing touches the allocation row.
    core_line.qty_delivered = Decimal("336")
    stock.quantity_on_hand = Decimal("164")
    world.db.commit()

    assert _written_stock_qty(world.db, line) == Decimal("336"), "no data change"
    assert _held(world.db, world, line) == Decimal("0")
    assert _free_at_pool(world.db, world) == Decimal("164")
    assert _stock_debt_pins(world.db, world, core_line) == []


def test_partial_delivery_holds_the_reserve_less_what_was_delivered(api):
    """Reserve 336 + Buy 126, 100 delivered before the confirm: the Reserve still holds
    236 (not 336, and not 136 - the delivery is credited once). A later delivery of 50 more
    takes it to 186."""
    client, world, order, line, core_line, _stock_row = _world(
        api, ordered="462", delivered="100", on_hand=500
    )

    _confirm(client, order, _own_336_buy_126(world, line))

    assert _held(world.db, world, line) == Decimal("236")

    core_line.qty_delivered = Decimal("150")
    world.db.commit()

    assert _held(world.db, world, line) == Decimal("186")
    pins = _stock_debt_pins(world.db, world, core_line)
    assert sum((Decimal(str(h.qty)) for h in pins), Decimal("0")) == Decimal("186")


def test_reserve_and_borrow_rows_together_hold_only_what_delivery_left(api):
    """Reserve 100 at the pool + Borrow 50 at another location, plan 150, confirmed before
    delivery; 120 then delivered: the two rows together hold 30, not 30 each."""
    client, world, order, line, core_line, _stock_row = _world(
        api, ordered="150", delivered="0", on_hand=500
    )
    donor_wh = _warehouse(world.db, "ZZT-DON-ODF", segment="dealer")
    _stock(world.db, world.product, donor_wh, on_hand=100)
    world.db.commit()

    _confirm(client, order, _line_payload(
        line.id,
        reserve=[{"warehouse_id": world.pool_wh.id, "qty": "100"}],
        borrow=[{
            "source": "other_location", "warehouse_id": donor_wh.id, "qty": "50",
            "reason": "ZZT borrow the rest from the other location.",
        }],
        amend_reason="ZZT reserve 100, borrow 50",
    ))
    assert _written_stock_qty(world.db, line) == Decimal("150")

    core_line.qty_delivered = Decimal("120")
    world.db.commit()

    assert _held(world.db, world, line) == Decimal("30")


def test_a_hold_under_no_decision_keeps_todays_cap_at_what_is_owed(api):
    """Guard: a reallocated hold that belongs to no revision has no decision to read a
    Reserve from, so it keeps today's read, least(qty, still owed)."""
    from datetime import datetime

    _client, world, _order, line, _core_line, _stock_row = _world(
        api, ordered="10", delivered="4", on_hand=20
    )
    world.db.add(SOLineAllocation(
        company_id=world.company_id, so_line_id=line.id, source_type=ALLOC_SOURCE_BRW,
        warehouse_id=world.pool_wh.id, qty=Decimal("8"), decision_id=None,
        reason="Reallocated from ZZT-OTHER line 1", confirmed_by=world.actor,
        confirmed_at=datetime.utcnow(),
    ))
    world.db.commit()

    assert _held(world.db, world, line) == Decimal("6")


def test_a_revision_that_planned_only_the_owed_qty_keeps_its_hold(api):
    """Guard (dev SO389799 SRT2403-RG shape): a revision decided before the 14 Sep 2026
    ruling planned only what was then still owed - Reserve 3 for the 3 of 20 left after 17
    shipped (`open_qty` "3" in its snapshot). Those 17 were never its to consume, so the
    Reserve still holds 3, as before this rule."""
    from app.models.project_so import SOSupplyDecision
    from sqlalchemy.orm.attributes import flag_modified

    client, world, order, line, core_line, _stock_row = _world(
        api, ordered="20", delivered="0", on_hand=50
    )
    _confirm(client, order, _line_payload(
        line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "3"}],
        buy_qty="17", buy_reason="ZZT buy the rest", amend_reason="ZZT reserve 3",
    ))
    db = world.db
    db.expire_all()
    decision = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == "active",
        )
        .one()
    )
    snapshots = [dict(s) for s in decision.line_snapshots]
    for snap in snapshots:
        if snap["project_line_id"] == str(line.id):
            snap["open_qty"] = "3"
            snap["components"] = [c for c in snap["components"] if c["kind"] == "reserve"]
    decision.line_snapshots = snapshots
    flag_modified(decision, "line_snapshots")
    core_line.qty_delivered = Decimal("17")
    db.commit()

    assert _held(db, world, line) == Decimal("3")


@pytest.mark.parametrize("reserve_written_first", [True, False])
def test_delivery_uses_the_reserve_first_so_the_borrow_bin_keeps_what_is_left(
    api, reserve_written_first
):
    """Reserve 100 at the pool + Borrow 50 at another location, 120 delivered after the
    confirm: the delivery used the Reserve up first, so the POOL holds 0 and the donor bin
    holds the 30 left - whichever row the database happens to list first."""
    from datetime import datetime, timedelta as _td

    client, world, order, line, core_line, _stock_row = _world(
        api, ordered="150", delivered="0", on_hand=500
    )
    donor_wh = _warehouse(world.db, "ZZT-DON-ODF2", segment="dealer")
    _stock(world.db, world.product, donor_wh, on_hand=100)
    world.db.commit()
    _confirm(client, order, _line_payload(
        line.id,
        reserve=[{"warehouse_id": world.pool_wh.id, "qty": "100"}],
        borrow=[{
            "source": "other_location", "warehouse_id": donor_wh.id, "qty": "50",
            "reason": "ZZT borrow the rest from the other location.",
        }],
        amend_reason="ZZT reserve 100, borrow 50",
    ))
    db = world.db
    db.expire_all()
    rows = {
        str(a.warehouse_id): a
        for a in db.query(SOLineAllocation).filter(
            SOLineAllocation.so_line_id == line.id,
            SOLineAllocation.source_type != ALLOC_SOURCE_ORDER,
        )
    }
    base = datetime(2026, 10, 1, 9, 0, 0)
    first, second = (
        (world.pool_wh.id, donor_wh.id) if reserve_written_first
        else (donor_wh.id, world.pool_wh.id)
    )
    rows[str(first)].created_at = base
    rows[str(second)].created_at = base + _td(seconds=1)
    core_line.qty_delivered = Decimal("120")
    db.commit()

    held = _held_by_bin(db, world, line)
    assert held.get(str(world.pool_wh.id), Decimal("0")) == Decimal("0"), held
    assert held.get(str(donor_wh.id), Decimal("0")) == Decimal("30"), held


def test_a_revision_on_the_ordered_qty_with_a_lower_required_qty_keeps_its_hold(api):
    """Guard (reviewer round 1), a known limit of the read: ordered 20, CS required 15,
    Reserve 15, 5 delivered. The snapshot (`open_qty` 15) cannot tell this revision from one
    decided before 14 Sep 2026, which planned `qty_ordered` less the 5 already delivered
    (20 - 5). Read the conservative way, as such an old revision: the 5 are not consumed,
    the Reserve 15 still holds 15, the read before this rule. Never seen on the dev copy
    (0 decided lines have `qty_required` below `qty_ordered`)."""
    from app.models.order import SalesOrderLine

    client, world, order, line, core_line, _stock_row = _world(
        api, ordered="20", delivered="0", on_hand=50
    )
    core_line.qty_required = Decimal("15")
    world.db.commit()
    _confirm(client, order, _line_payload(
        line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "15"}],
        amend_reason="ZZT reserve 15",
    ))
    core_line = world.db.get(SalesOrderLine, core_line.id)
    core_line.qty_delivered = Decimal("5")
    world.db.commit()

    assert _held(world.db, world, line) == Decimal("15")


def test_an_autocount_cancel_before_the_confirm_is_not_taken_off_twice(api):
    """Reviewer round 2: the CB6633-NL shape on a line AutoCount had already cancelled 10
    of before the confirm. Ordered 472, cancelled 10 (plan 462), Own 336 + Buy 126, then 336
    delivered (TransferedQty 346, the cancel included): the Own holds 0, not 10."""
    from app.models.order import SalesOrderLine

    client, world, order, line, core_line, stock = _world(
        api, ordered="472", delivered="10", on_hand=500
    )
    core_line.qty_cancelled = Decimal("10")
    world.db.commit()
    _confirm(client, order, _own_336_buy_126(world, line))
    assert _written_stock_qty(world.db, line) == Decimal("336")

    core_line = world.db.get(SalesOrderLine, core_line.id)
    core_line.qty_delivered = Decimal("346")
    world.db.commit()

    assert _held(world.db, world, line) == Decimal("0")
