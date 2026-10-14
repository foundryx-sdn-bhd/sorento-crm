"""STOCK-DEBT-STALE-PIN: a pin never counts as cover for stock the bin does not hold.

Owner, 14 Oct 2026 (prod, Stock Debt > CB6633-NL > Oct 26): SO424555 line 16 read
"Covered by: On hand BRW-IB", status Pinned, Short 0, while BRW-IB held 0 on hand, 0
reserved. The 'own' allocation (126 held of 336 saved) was CONFIRMED on 8 Oct, two days
after the stock row last moved to 0 - choosing the own location is allowed whatever the
stock - so a pin can be empty from the moment it is made, not only after the stock leaves.

The mechanism: `_supply` drops a bin whose on hand less reserved is <= 0, the allocation
hold then names an event `assign()` cannot find, and the stand-in branch (AC-S2-1b, meant
for bins OUTSIDE the read's span) stood the whole 126 up out of the pin itself. The prod
check (owner, 14 Oct 12:20) found 8 such pins hiding about 406 units of shortfall:
SRTSH1040 SO402757 pinned 150 with 2 free, SRTWCX7604 SO382618 100 with 21 free, CSH2073
SO419595 + SO419598 (8 + 2) at BRW with 0 free, and four more at 0.

The rule now: a pin covers at most the bin's real free stock (on hand less reserved), in the
span or outside it, and the rest is short with no "Covered by" claim.

Postgres only, every row seeded here (the seeding helpers are `test_stock_debt_routes`'s).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.services.scm.supply_assignment import month_key
from tests.scm.conftest import SORENTO_COMPANY_ID, requires_pg
from tests.scm.test_stock_debt_routes import (
    BASE,
    TODAY,
    _client,
    _demand,
    _months_ahead,
    _order_back_link_on_spo,
    _product,
    _project_line_for,
    _row_of,
    _spo,
    _u,
    _warehouse,
)

pytestmark = requires_pg


def _stock(db, product, warehouse, *, on_hand, reserved=0):
    """A stock row with its reserved quantity stated - the prod rows are 0/0, and the
    SRTSH1040 shape is "free 2", which is on hand less reserved."""
    from app.models.inventory import Stock

    db.add(
        Stock(
            id=_u(),
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity_on_hand=Decimal(str(on_hand)),
            quantity_reserved=Decimal(str(reserved)),
            company_id=SORENTO_COMPANY_ID,
        )
    )
    db.flush()


def _own_pin(db, project_line, warehouse, qty):
    """A CONFIRMED 'own' allocation - the prod row's own source - holding `qty` at the bin."""
    from datetime import datetime

    from app.models.project_so import ALLOC_SOURCE_OWN, SOLineAllocation

    row = SOLineAllocation(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        so_line_id=project_line.id,
        source_type=ALLOC_SOURCE_OWN,
        warehouse_id=warehouse.id,
        qty=Decimal(str(qty)),
        confirmed_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _pinned_line(db, product, warehouse, *, qty, due, so_number, pin_at=None):
    """One open sales-order line at `warehouse`, pinned for its whole quantity at `pin_at`
    (its own bin unless given)."""
    _order, core_line = _demand(
        db, product, warehouse, qty=qty, required_date=due, so_number=so_number
    )
    _pso, project_line = _project_line_for(db, core_line)
    _own_pin(db, project_line, pin_at or warehouse, qty)
    return core_line


def _on_hand_entry(warehouse, qty):
    return {
        "kind": "on_hand", "ref": f"On hand {warehouse.warehouse_code}",
        "spo_number": None, "spo_line_number": None, "qty": qty,
        "lent_from_so_number": None,
    }


def _cell(c, product, month, **params):
    return c.get(f"{BASE}/{product.id}/cell", params={"month": month, **params}).json()


def _balance(c, marker, product, month):
    board = c.get(BASE, params={"query": marker, "only_debt": False}).json()
    row = _row_of(board, product.product_code)
    return {m["key"]: m["balance"] for m in row["months"]}[month]


# ------------------------------------------------------------- a bin IN the span


@pytest.mark.parametrize(
    "on_hand,reserved",
    [(0, 0), (5, 5)],
    ids=["cb6633-bin-0-of-0", "bin-all-reserved"],
)
def test_a_pin_on_an_empty_bin_covers_nothing(scm_app, on_hand, reserved):
    """CB6633-NL / SO424555: pinned 126 at BRW-IB, the bin holds 0 free. The line is short
    126 with no "Covered by", the month owes 126, and the Supply tab has nothing to list."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bin_ib = _warehouse(db, f"ZZTBRW{_u()[:4]}-IB")
    product = _product(db, f"{marker}-NL")
    _stock(db, product, bin_ib, on_hand=on_hand, reserved=reserved)
    due = _months_ahead(1)
    _pinned_line(db, product, bin_ib, qty=126, due=due, so_number=f"{marker}-SO1")

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))
        balance = _balance(c, marker, product, month_key(due))

    [line] = cell["demand"]
    assert line["status"] == "short"
    assert line["assigned_qty"] == 0
    assert line["short_qty"] == 126
    assert line["assigned_from"] == []
    assert cell["supply"] == []
    assert balance == -126


@pytest.mark.parametrize(
    "pinned,on_hand,reserved,covered",
    [(126, 50, 0, 50), (150, 10, 8, 2)],
    ids=["cb6633-bin-50", "srtsh1040-free-2"],
)
def test_a_pin_above_the_bins_free_stock_covers_only_what_is_there(
    scm_app, pinned, on_hand, reserved, covered
):
    """Pinned 126 with 50 on hand: covered 50, short 76. SRTSH1040 / SO402757: pinned 150
    with 2 free (on hand less reserved): covered 2, short 148."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bin_ib = _warehouse(db, f"ZZTBRW{_u()[:4]}-IB")
    product = _product(db, f"{marker}-NL")
    _stock(db, product, bin_ib, on_hand=on_hand, reserved=reserved)
    due = _months_ahead(1)
    _pinned_line(db, product, bin_ib, qty=pinned, due=due, so_number=f"{marker}-SO1")

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))
        balance = _balance(c, marker, product, month_key(due))

    [line] = cell["demand"]
    assert line["status"] == "short"
    assert line["assigned_qty"] == covered
    assert line["short_qty"] == pinned - covered
    assert line["assigned_from"] == [_on_hand_entry(bin_ib, covered)]
    assert balance == -(pinned - covered)


def test_a_pin_within_the_bins_stock_still_reads_pinned(scm_app):
    """Regression guard: pinned 126 with 200 on hand is covered in full, as before."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bin_ib = _warehouse(db, f"ZZTBRW{_u()[:4]}-IB")
    product = _product(db, f"{marker}-NL")
    _stock(db, product, bin_ib, on_hand=200)
    due = _months_ahead(1)
    _pinned_line(db, product, bin_ib, qty=126, due=due, so_number=f"{marker}-SO1")

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))
        balance = _balance(c, marker, product, month_key(due))

    [line] = cell["demand"]
    assert line["status"] == "pinned"
    assert line["assigned_qty"] == 126
    assert line["short_qty"] == 0
    assert line["assigned_from"] == [_on_hand_entry(bin_ib, 126)]
    assert balance == 0


def test_two_pins_on_one_empty_bin_both_read_short(scm_app):
    """CSH2073: SO419595 (8) and SO419598 (2) both pinned at BRW, 0 free."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    brw = _warehouse(db, f"ZZTBRW{_u()[:4]}")
    product = _product(db, f"{marker}-CSH")
    _stock(db, product, brw, on_hand=0)
    due = _months_ahead(1)
    _pinned_line(db, product, brw, qty=8, due=due, so_number=f"{marker}-SO95")
    _pinned_line(
        db, product, brw, qty=2, due=due + timedelta(days=3), so_number=f"{marker}-SO98"
    )

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))
        balance = _balance(c, marker, product, month_key(due))

    by_so = {line["so_number"]: line for line in cell["demand"]}
    assert (by_so[f"{marker}-SO95"]["status"], by_so[f"{marker}-SO95"]["short_qty"]) == (
        "short", 8,
    )
    assert (by_so[f"{marker}-SO98"]["status"], by_so[f"{marker}-SO98"]["short_qty"]) == (
        "short", 2,
    )
    assert balance == -10


# --------------------------------------------------------- a bin OUTSIDE the span


@pytest.mark.parametrize(
    "on_hand,covered",
    [(0, 0), (10, 10), (500, 40)],
    ids=["empty", "partly-stocked", "stocked"],
)
def test_a_pin_at_a_bin_outside_the_span_is_capped_at_its_free_stock(
    scm_app, on_hand, covered
):
    """AC-S2-1b still honours a pin on a bin this read cannot see (an unflagged bin), but
    never for more than that bin's real free stock: 40 pinned at a bin holding 0 covers
    nothing, at a bin holding 10 covers 10, at a bin holding 500 covers 40."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    unflagged = _warehouse(db, f"ZZTBRW{_u()[:4]}-HP", planning=False)
    product = _product(db, f"{marker}-A")
    _stock(db, product, unflagged, on_hand=on_hand)
    due = _months_ahead(1)
    _pinned_line(
        db, product, bb, qty=40, due=due, so_number=f"{marker}-SO1", pin_at=unflagged
    )

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))
        balance = _balance(c, marker, product, month_key(due))

    [line] = cell["demand"]
    assert line["assigned_qty"] == covered
    assert line["short_qty"] == 40 - covered
    assert line["status"] == ("pinned" if covered == 40 else "short")
    assert line["assigned_from"] == (
        [_on_hand_entry(unflagged, covered)] if covered else []
    )
    assert balance == -(40 - covered)


def test_two_pins_at_one_bin_outside_the_span_share_its_free_stock_nearest_first(scm_app):
    """Two pins (8 due first, 2 due later) on an unflagged bin holding 5: the bin covers 5
    in all, to the nearer line first, whatever order the pins were written in."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    unflagged = _warehouse(db, f"ZZTBRW{_u()[:4]}-HP", planning=False)
    product = _product(db, f"{marker}-A")
    _stock(db, product, unflagged, on_hand=5)
    due = _months_ahead(1)
    # The LATER line's pin is written first, so a read in row order would serve it first.
    _pinned_line(
        db, product, bb, qty=2, due=due + timedelta(days=3),
        so_number=f"{marker}-SO2", pin_at=unflagged,
    )
    _pinned_line(
        db, product, bb, qty=8, due=due, so_number=f"{marker}-SO1", pin_at=unflagged
    )

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))

    by_so = {line["so_number"]: line for line in cell["demand"]}
    first, later = by_so[f"{marker}-SO1"], by_so[f"{marker}-SO2"]
    assert (first["assigned_qty"], first["short_qty"]) == (5, 3)
    assert (later["assigned_qty"], later["short_qty"]) == (0, 2)


@pytest.mark.parametrize(
    "on_hand,covered,status",
    [(0, 76, "short"), (50, 126, "pinned")],
    ids=["bin-0", "bin-50"],
)
def test_a_partly_stocked_pin_leaves_the_rest_to_the_orders_own_spo(
    scm_app, on_hand, covered, status
):
    """Reviewer, round 1 (the SRTWCX7604 / SO382618 shape: a pin above the bin's stock on a
    line that also has its own purchase). Pinned 126 at a bin holding 50, and an SPO of 76
    whose S/O names the line: the pin holds the 50 the bin has, the SPO the 76 left, and
    the line owes nothing. The pin's own quantity must not read as covered before the
    book's S/O is read, or the SPO is left free in its month beside a red cell."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTDC1{_u()[:4]}-BB")
    product = _product(db, f"{marker}-WEPLS")
    _stock(db, product, bb, on_hand=on_hand)
    due = _months_ahead(2)
    order, core_line = _demand(
        db, product, bb, qty=126, required_date=due, so_number=f"{marker}-SO1"
    )
    order.source_ref = f"ZZTBOOK:{marker}"
    core_line.source_ref = f"ZZTBOOK:{marker}:1"
    _pso, project_line = _project_line_for(db, core_line)
    _own_pin(db, project_line, bb, 126)
    allocation = _spo(db, product, bb, qty=76, arrives=_months_ahead(1))
    allocation.from_so_line_ref = f"ZZTBOOK:{marker}:1"
    db.flush()

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))

    [line] = cell["demand"]
    assert line["assigned_qty"] == covered
    assert line["short_qty"] == 126 - covered
    assert line["status"] == status
    assert sorted((entry["kind"], entry["qty"]) for entry in line["assigned_from"]) == sorted(
        ([("on_hand", on_hand)] if on_hand else []) + [("spo", 76)]
    )


def test_received_spo_placements_share_a_thin_bin_outside_the_span_nearest_first(scm_app):
    """Reviewer, round 1: placements on a received SPO, at a bin outside the span holding 5,
    share it nearer line first whatever order they were written in (the links read had no
    order, so the split could move between refreshes)."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    unflagged = _warehouse(db, f"ZZTBRW{_u()[:4]}-HP", planning=False)
    product = _product(db, f"{marker}-A")
    _stock(db, product, unflagged, on_hand=5)
    due = _months_ahead(1)
    allocation = _spo(
        db, product, unflagged, qty=20, arrives=TODAY - timedelta(days=20),
        received=20, receipt_status="fully_received",
    )
    # The LATER line's placement is written first.
    for so_number, required in (
        (f"{marker}-SO2", due + timedelta(days=3)),
        (f"{marker}-SO1", due),
    ):
        _order, core_line = _demand(
            db, product, bb, qty=4, required_date=required, so_number=so_number
        )
        project_order, project_line = _project_line_for(db, core_line)
        _order_back_link_on_spo(
            db, project_order, project_line, allocation=allocation, qty=4
        )

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))

    by_so = {line["so_number"]: line for line in cell["demand"]}
    assert by_so[f"{marker}-SO1"]["assigned_qty"] == 4
    assert by_so[f"{marker}-SO2"]["assigned_qty"] == 1
    assert by_so[f"{marker}-SO2"]["short_qty"] == 3


# ------------------------------------------------------------------ the Supply tab


def test_the_supply_tab_lists_the_bin_a_pin_covers_a_later_month_from(scm_app):
    """A line due next month pinned on stock held today: that month's Supply tab lists the
    bin beside it (Free 0 there - its free part is credited to today's column), so the tab
    is never empty next to a row it covers."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bin_ib = _warehouse(db, f"ZZTBRW{_u()[:4]}-IB")
    product = _product(db, f"{marker}-NL")
    _stock(db, product, bin_ib, on_hand=200)
    due = _months_ahead(1)
    _pinned_line(db, product, bin_ib, qty=126, due=due, so_number=f"{marker}-SO1")

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))
        now = _cell(c, product, month_key(TODAY))

    [row] = cell["supply"]
    assert (row["kind"], row["warehouse_code"], row["qty"]) == (
        "on_hand", bin_ib.warehouse_code, 200,
    )
    assert row["free_qty"] == 0
    assert row["assigned_to"] == [
        {"so_number": f"{marker}-SO1", "line_no": None, "qty": 126},
    ]
    # Today's column still owns the free part.
    [home] = now["supply"]
    assert home["free_qty"] == 74


def test_the_supply_tab_lists_a_pinned_bin_outside_the_span(scm_app):
    """A pin on a bin the read cannot see is listed in its line's column too, for the part
    the order holds, Free 0."""
    app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    unflagged = _warehouse(db, f"ZZTBRW{_u()[:4]}-HP", planning=False)
    product = _product(db, f"{marker}-A")
    _stock(db, product, unflagged, on_hand=500)
    due = _months_ahead(1)
    _pinned_line(
        db, product, bb, qty=30, due=due, so_number=f"{marker}-SO1", pin_at=unflagged
    )

    with TestClient(app) as c:
        cell = _cell(c, product, month_key(due))

    [row] = cell["supply"]
    assert (row["kind"], row["warehouse_code"], row["qty"], row["free_qty"]) == (
        "on_hand", unflagged.warehouse_code, 30, 0,
    )
    assert row["assigned_to"] == [
        {"so_number": f"{marker}-SO1", "line_no": None, "qty": 30},
    ]
    assert cell["supply_total_qty"] == 30


# --------------------------------------------- the Fulfilment Planning board's read


@pytest.mark.parametrize(
    "flagged,on_hand,covered",
    [(True, 0, 0), (True, 50, 50), (False, 10, 10)],
    ids=["own-bin-empty", "own-bin-50", "unflagged-bin-10"],
)
def test_the_board_reads_the_same_capped_pin(scm_app, flagged, on_hand, covered):
    """`_holds` is shared with the board (`planning_assignments`, R21): the same pin gives
    the board's line the same capped cover, so a stale pin is no longer offered as stock
    the line already has (rung 1) or as a donor's on hand (Borrow)."""
    from app.services.project_supply_service import ProjectSupplyService

    _app, db = _client(scm_app)
    marker = f"ZZTSP{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    pin_bin = bb if flagged else _warehouse(db, f"ZZTBRW{_u()[:4]}-HP", planning=False)
    product = _product(db, f"{marker}-NL")
    _stock(db, product, pin_bin, on_hand=on_hand)
    due = _months_ahead(1)
    core_line = _pinned_line(
        db, product, bb, qty=126, due=due, so_number=f"{marker}-SO1", pin_at=pin_bin
    )

    result = ProjectSupplyService(db).planning_assignments([str(product.id)])[
        str(product.id)
    ]
    [line] = [row for row in result.lines if row.line.key == str(core_line.id)]
    assert round(sum(item.qty for item in line.assigned), 4) == covered
    assert line.status == "short"
    assert line.uncovered == 126 - covered


def test_a_pinned_reserve_bin_outside_the_span_reads_free_0_under_the_projection(scm_app):
    """Reviewer, round 1: under `group=BB` the HQ project reserve is outside the span, so a
    BB line pinned there is covered off a stand-in. With the projection switch on, its
    Supply row states what the pin holds and Free 0, never the reserve's whole projected
    remainder beside a Qty of only the pinned part."""
    from app.models.inventory import Warehouse
    from tests.scm.test_stock_debt_projection_routes import _seed

    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    marker, product, hq = seed["marker"], seed["product"], seed["hq"]
    bb = db.query(Warehouse).filter(Warehouse.warehouse_code == f"{marker}BRW-BB").one()
    _stock(db, product, hq, on_hand=50)
    _pinned_line(
        db, product, bb, qty=30, due=TODAY, so_number=f"{marker}-SO-PIN", pin_at=hq
    )

    with TestClient(app) as c:
        cell = _cell(
            c, product, month_key(TODAY), group="BB", book="project", projected="true"
        )

    pinned = [line for line in cell["demand"] if line["so_number"] == f"{marker}-SO-PIN"]
    assert pinned and pinned[0]["assigned_qty"] == 30
    rows = [
        row for row in cell["supply"]
        if row["kind"] == "on_hand" and row["warehouse_code"] == hq.warehouse_code
    ]
    assert [(row["qty"], row["free_qty"]) for row in rows] == [(30, 0)]
