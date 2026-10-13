"""BORROW-OWN-LOC (`documentation/plans/scm/PLAN-board-borrow-own-location-13oct.md`).

Owner, 13 Oct 2026: the line's OWN location always appears under Borrow, whatever holds the
stock there, whatever the "Borrow from another order" switch says. The typed quantity is not
capped: Confirm passes. What is actually HELD at the own location never exceeds what is free
there after this line's own Reserve; the rest is confirmed as decided and recorded short with
the OI-CLOSED-LINE sentence, so the same units are never counted twice.

Postgres via `blank_session`; every chain seeded here. Board fixtures from
`tests/test_fulfilment_board.py`, confirm fixtures from the ladder suite, with the
justification that earned them there.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
from app.services.error_handler import AppException
from app.services.project_supply_service import ProjectSupplyService

from .._pg_fixture import blank_session
from ..test_fulfilment_board import (
    TODAY,
    _cell,
    _line,
    _order,
    _product,
    _service,
    _stock as _board_stock,
    _uid,
    _warehouse as _board_warehouse,
)
from ..test_so_supply_confirmation import _stock
from .test_borrow_location_only import _set_borrow_order
from .test_project_supply_service_ladder import _group_sites, _seed_line, _world

REQUIRED = date(2026, 9, 3)
BUCKET = "2026-08-31"


def _own_rows(contribution, own):
    return [
        c for c in contribution["borrow_candidates"] if c.get("warehouse_id") == str(own.id)
    ]


# --------------------------------------------------------------------------- the offer


@pytest.mark.parametrize("enabled", [False, True])
def test_the_board_offers_the_lines_own_location_under_borrow(enabled):
    """The owner's case shape: free stock at the own location (Reserve's) and somewhere else
    to borrow from. The own location is a Borrow choice too, in both switch states."""
    with blank_session() as db:
        _set_borrow_order(db, enabled)
        sfx = _uid()[:6]
        product = _product(db, f"ZZT-{sfx}")
        own = _board_warehouse(db, f"ZA{sfx}-ZZ")
        other = _board_warehouse(db, f"ZB{sfx}-YY")
        _board_stock(db, product, own, on_hand=8)
        _board_stock(db, product, other, on_hand=25)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=REQUIRED, warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, BUCKET)["contributions"][0]
        rows = _own_rows(contribution, own)
        assert len(rows) == 1, contribution["borrow_candidates"]
        row = rows[0]
        assert row["source"] == "other_location"
        assert row["warehouse_code"] == own.warehouse_code
        # A plain location row: it names no donor order and no donor project.
        assert not row.get("donor_core_line_id")
        assert not row.get("donor_so_number")
        assert not row.get("donor_project_id")
        # Never the pre-selected choice: the server's ranked donors keep the head.
        assert not row.get("recommended")
        assert contribution["borrow_candidates"][-1] is row or (
            contribution["borrow_candidates"][-1]["warehouse_id"] == str(own.id)
        ), "the own location is offered last, behind the ranked donors"
        # The dialog's location figures, like every other donor row.
        assert row["location"]["warehouse_id"] == str(own.id)
        # The 8 free there are Reserve's: never counted again as borrowable.
        assert Decimal(row["free_qty"]) == 0
        assert contribution["qty_borrow_available"] == "25"


def test_the_own_location_is_offered_even_with_nothing_there_and_nowhere_else():
    """"Even if no stock, we also should allow" - and a line with no other donor at all still
    gets an "Add a borrow" (the panel hides it only on an empty list)."""
    with blank_session() as db:
        sfx = _uid()[:6]
        product = _product(db, f"ZZT-{sfx}")
        own = _board_warehouse(db, f"ZA{sfx}-ZZ")
        _board_stock(db, product, own, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=REQUIRED, warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, BUCKET)["contributions"][0]
        rows = _own_rows(contribution, own)
        assert len(rows) == 1, contribution["borrow_candidates"]
        assert Decimal(rows[0]["free_qty"]) == 0


# --------------------------------------------------------------------------- confirm


def _own_world(db, *, on_hand: int, qty: str = "10"):
    company_id, eling, project, product = _world(db)
    _group, sites = _group_sites(db)
    own, pool = sites["BRW"]
    _stock(db, product, own, on_hand=on_hand)
    order, line, _cso, _cline = _seed_line(
        db, company_id, project, product, own, qty_ordered=qty, line_no=1,
    )
    return {
        "eling": eling, "product": product, "own": own, "pool": pool,
        "order": order, "line": line,
    }


def _confirm(db, world, *, reserve=(), borrow=(), buy_qty=None):
    line = ConfirmLine(
        project_line_id=world["line"].id,
        reserve=[{"warehouse_id": str(w.id), "qty": q} for w, q in reserve],
        borrow=list(borrow),
        **(
            {"buy_qty": Decimal(str(buy_qty)), "amend_reason": "Buy the rest."}
            if buy_qty
            else {}
        ),
    )
    body = ProjectSupplyService(db).confirm(
        world["order"], ConfirmSupplyBody(lines=[line]), actor_user_id=world["eling"]
    )
    db.commit()
    return body


def _own_borrow(world, qty, reason="Another order's units at our own bin."):
    return {
        "source": "other_location",
        "warehouse_id": str(world["own"].id),
        "qty": qty,
        "reason": reason,
    }


def _held_at(db, world, warehouse) -> Decimal:
    held = ProjectSupplyService(db).held_stock_by_location([str(world["product"].id)])
    return held.get((str(world["product"].id), str(warehouse.id)), Decimal("0"))


@pytest.mark.parametrize("enabled", [False, True])
def test_confirm_reserve_plus_an_over_asked_own_location_borrow_passes_short(enabled):
    """The owner's flow: Reserve what is free at the own location, Borrow the rest from the
    same location. Confirm passes; the borrow beyond what is free is confirmed as decided and
    recorded short, and nothing is held twice."""
    with blank_session() as db:
        world = _own_world(db, on_hand=8)
        _set_borrow_order(db, enabled)
        db.commit()
        body = _confirm(
            db, world, reserve=[(world["own"], "8")], borrow=[_own_borrow(world, "2")]
        )
        code = world["own"].warehouse_code
        notices = body["stock_short_notices"]
        assert len(notices) == 1, notices
        assert notices[0]["reason"].startswith(
            f"Borrow 2 at {code} confirmed as decided; only 0 free at {code}"
        ), notices[0]["reason"]
        assert _held_at(db, world, world["own"]) == Decimal("8"), "never more than on hand"


def test_re_confirming_the_same_reserve_and_own_borrow_never_holds_more_than_on_hand():
    """Review B1: on a re-confirm the line's own hold is un-netted, so an unchanged
    (carried) Reserve must still come off what the own-location Borrow can take."""
    with blank_session() as db:
        world = _own_world(db, on_hand=8)
        body = dict(reserve=[(world["own"], "8")], borrow=[_own_borrow(world, "2")])
        _confirm(db, world, **body)
        again = _confirm(db, world, **body)
        assert _held_at(db, world, world["own"]) == Decimal("8"), "never more than on hand"
        notices = again["stock_short_notices"]
        assert len(notices) == 1 and "2 short" in notices[0]["reason"], notices


def test_confirm_an_own_location_borrow_with_free_stock_holds_it():
    with blank_session() as db:
        world = _own_world(db, on_hand=10)
        body = _confirm(db, world, borrow=[_own_borrow(world, "5")], buy_qty="5")
        assert not body.get("stock_short_notices"), body.get("stock_short_notices")
        assert _held_at(db, world, world["own"]) == Decimal("5")


def test_confirm_an_own_location_borrow_with_no_stock_at_all_still_passes():
    """Planning can be done after delivery: no stock is not a refusal."""
    with blank_session() as db:
        world = _own_world(db, on_hand=0)
        body = _confirm(db, world, borrow=[_own_borrow(world, "10")])
        notices = body["stock_short_notices"]
        assert len(notices) == 1 and "10 short" in notices[0]["reason"], notices
        assert _held_at(db, world, world["own"]) == Decimal("0")


def test_an_own_location_borrow_still_needs_a_reason():
    with blank_session() as db:
        world = _own_world(db, on_hand=10)
        with pytest.raises(AppException) as raised:
            _confirm(db, world, borrow=[_own_borrow(world, "10", reason="  ")])
        assert "takes a reason" in str(raised.value.detail), raised.value.detail


def test_a_plain_borrow_at_the_lines_site_pool_is_still_refused():
    """Only the OWN location is lifted. The pool stays a Reserve source, never a free-stock
    Borrow (`_reserve_location_ids`)."""
    with blank_session() as db:
        world = _own_world(db, on_hand=0)
        _stock(db, world["product"], world["pool"], on_hand=20)
        db.commit()
        with pytest.raises(AppException) as raised:
            _confirm(
                db, world,
                borrow=[{**_own_borrow(world, "10"), "warehouse_id": str(world["pool"].id)}],
            )
        assert "inside this line's Reserve pool" in str(raised.value.detail), (
            raised.value.detail
        )
