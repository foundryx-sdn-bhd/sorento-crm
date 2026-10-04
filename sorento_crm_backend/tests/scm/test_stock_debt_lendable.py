"""STOCK-DEBT-LENDABLE, the pure half: a far-dated landed pin lends to nearer lines.

Owner (30 Sep 2026): SRTSS8710, SO381065 (due 29 Mar 2027) pinned 88 on hand at BRW-BB
because its own SPO was received, while SO396071 (due 1 Sep 2026) reads short 32 and the
Sep/Oct/Dec cells are red. "would like this stock to be allocated nearer like oct, nov,
dec". Option B: the pin is LENDABLE when its line can wait (`Hold.lendable`, decided by the
caller off the board's own borrow-donor window), the lent quantity walks as free on hand at
its bin so nearer lines draw it first, and the far line keeps its claim as `order_back`.

No database: `assign()` takes plain events and answers plain numbers, the same discipline
`test_supply_assignment.py` keeps. The route half (which pins are lendable, the wire) is
`test_stock_debt_lendable_routes.py`.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.scm.supply_assignment import (
    KIND_ON_HAND,
    STATUS_COVERED,
    STATUS_ORDER_BACK,
    STATUS_PINNED,
    STATUS_SHORT,
    DemandLine,
    Hold,
    SupplyEvent,
    assign,
)

AS_OF = date(2026, 9, 30)
TBA_FROM = date(2029, 1, 1)
LEAD = 90

BIN = "on_hand:brw-bb"


def _on_hand(qty, key=BIN, warehouse="BRW-BB"):
    return SupplyEvent(key=key, kind=KIND_ON_HAND, warehouse=warehouse, at=AS_OF, qty=qty)


def _line(key, so, qty, due, warehouse="BRW-BB"):
    return DemandLine(
        key=key, so_number=so, line_no=1, warehouse=warehouse, agent_code="LEENA",
        required_date=due, open_qty=qty,
    )


def _landed(line_key, qty, *, lendable, key=BIN):
    return Hold(
        line_key=line_key, supply_key=key, qty=qty, kind=KIND_ON_HAND,
        warehouse="BRW-BB", landed=True, lendable=lendable,
    )


def _walk(supply, demand, pinned):
    return assign(
        "P", as_of=AS_OF, tba_from=TBA_FROM, lead_days=LEAD, supply=supply,
        demand=demand, pinned=pinned, overdue_grace_days=0, overdue_dead_days=0,
    )


def _by_key(result):
    return {row.line.key: row for row in result.lines}


def _months(result):
    return {month.key: month.balance for month in result.months}


# The owner's case: 88 landed for the March 2027 line, 32 short in September, 29 in
# October, 207 in December.
FAR = _line("far", "SO381065", 88, date(2027, 3, 29))
SEP = _line("sep", "SO396071", 32, date(2026, 9, 1))
OCT = _line("oct", "SO402118", 29, date(2026, 10, 10))
DEC = _line("dec", "SO404890", 207, date(2026, 12, 3))


def test_ac_1_nearer_lines_draw_a_lendable_pin_first_and_the_far_line_reads_order_back():
    result = _walk([_on_hand(88)], [FAR, SEP, OCT, DEC], [_landed("far", 88, lendable=True)])
    rows = _by_key(result)

    assert rows["sep"].status == STATUS_COVERED
    assert rows["oct"].status == STATUS_COVERED
    assert rows["dec"].status == STATUS_SHORT
    assert rows["dec"].uncovered == pytest.approx(180)

    far = rows["far"]
    assert far.status == STATUS_ORDER_BACK
    assert far.lent_qty == pytest.approx(88)
    assert far.uncovered == pytest.approx(88)
    # Who has them, in walk order, each with its own quantity.
    assert [(lent.line_key, lent.so_number, lent.qty) for lent in far.lent] == [
        ("sep", "SO396071", 32), ("oct", "SO402118", 29), ("dec", "SO404890", 27),
    ]
    assert far.assigned == ()

    # The receiving take says whose stock it was.
    take = rows["sep"].assigned[0]
    assert take.event.key == BIN
    assert take.qty == pytest.approx(32)
    assert take.lent_from_line_key == "far"
    assert take.lent_from_so == "SO381065"
    assert take.pinned is False


def test_ac_1b_the_months_move_with_the_lend():
    """Sep and Oct clear, Dec improves by the last 27, and March books the order-back."""
    result = _walk([_on_hand(88)], [FAR, SEP, OCT, DEC], [_landed("far", 88, lendable=True)])
    months = _months(result)
    assert months["2026-09"] == 0
    assert months["2026-10"] == 0
    assert months["2026-12"] == -180
    assert months["2027-03"] == -88
    assert _by_key(result)["far"].short_at_date == pytest.approx(88)


def test_ac_2_a_partly_lent_pin_stays_pinned_for_the_rest():
    """Only September is short: 32 of the 88 are lent, the other 56 stay pinned to the far
    line, which reads `order_back` (something was lent) with a pinned landed take of 56."""
    result = _walk([_on_hand(88)], [FAR, SEP], [_landed("far", 88, lendable=True)])
    rows = _by_key(result)
    far = rows["far"]
    assert far.status == STATUS_ORDER_BACK
    assert far.lent_qty == pytest.approx(32)
    assert far.uncovered == pytest.approx(32)
    assert [(item.event.key, item.qty, item.pinned, item.landed) for item in far.assigned] == [
        (BIN, 56, True, True)
    ]
    assert rows["sep"].status == STATUS_COVERED
    assert _months(result)["2027-03"] == -32
    assert result.free[BIN] == 0


def test_ac_3_a_non_lendable_landed_pin_is_exactly_todays_behaviour():
    """Inside the window (the caller decides; here it simply is not marked) the pin binds
    first at any date and nobody nearer touches it - R7's own rule, unchanged."""
    result = _walk([_on_hand(88)], [FAR, SEP, OCT, DEC], [_landed("far", 88, lendable=False)])
    rows = _by_key(result)
    assert rows["far"].status == STATUS_PINNED
    assert rows["far"].lent_qty == 0
    assert rows["far"].lent == ()
    assert rows["sep"].status == STATUS_SHORT
    assert rows["sep"].uncovered == pytest.approx(32)
    assert _months(result)["2026-09"] == -32
    assert _months(result)["2027-03"] == 0


def test_ac_4_free_stock_at_the_bin_is_drawn_before_the_lent_part():
    """100 on hand, 88 of it landed for the far line: the first 12 a nearer line takes are
    free stock and say nothing about the far line; only what goes beyond that is a lend."""
    result = _walk(
        [_on_hand(100)], [FAR, SEP], [_landed("far", 88, lendable=True)],
    )
    rows = _by_key(result)
    sep = rows["sep"]
    assert [(item.qty, item.lent_from_line_key) for item in sep.assigned] == [
        (12, None), (20, "far"),
    ]
    far = rows["far"]
    assert far.lent_qty == pytest.approx(20)
    assert far.uncovered == pytest.approx(20)
    assert far.status == STATUS_ORDER_BACK
    assert [(item.qty, item.pinned) for item in far.assigned] == [(68, True)]


def test_ac_4b_a_nearer_line_that_only_needs_the_free_part_lends_nothing():
    result = _walk(
        [_on_hand(100)], [FAR, _line("sep", "SO396071", 10, date(2026, 9, 1))],
        [_landed("far", 88, lendable=True)],
    )
    rows = _by_key(result)
    assert rows["far"].status == STATUS_PINNED
    assert rows["far"].lent_qty == 0
    assert [(item.qty, item.lent_from_line_key) for item in rows["sep"].assigned] == [(10, None)]
    assert result.free[BIN] == pytest.approx(2)


def test_ac_5_a_far_line_short_beyond_what_it_lent_reads_short():
    """Open 100 with 88 landed: 12 were short whatever happens. Once the 88 are lent the
    line is short 100, of which 88 is the lend - `short` outranks, the lend is still stated."""
    far = _line("far", "SO381065", 100, date(2027, 3, 29))
    result = _walk([_on_hand(88)], [far, SEP, OCT, DEC], [_landed("far", 88, lendable=True)])
    row = _by_key(result)["far"]
    assert row.status == STATUS_SHORT
    assert row.uncovered == pytest.approx(100)
    assert row.lent_qty == pytest.approx(88)


def test_ac_6_a_line_after_the_far_line_gets_nothing_of_the_lend():
    """Chronology still rules: the far line takes what is left of its claim at its own
    date, so a line due AFTER it cannot draw the landed goods."""
    later = _line("later", "SO410000", 50, date(2027, 6, 1))
    result = _walk([_on_hand(88)], [FAR, later], [_landed("far", 88, lendable=True)])
    rows = _by_key(result)
    assert rows["far"].status == STATUS_PINNED
    assert rows["far"].lent_qty == 0
    assert rows["later"].status == STATUS_SHORT
    assert rows["later"].uncovered == pytest.approx(50)


def test_ac_7_two_lendable_claims_on_one_bin_and_the_later_one_bears_the_lend():
    """Two far lines landed 60 and 40 on a floor of 100. The nearer line takes 32; the
    earlier-due far line still takes its whole 60 at its own step, so the LATER one is the
    one that lent - the same order the walk itself gives them."""
    far_a = _line("far-a", "SO381065", 60, date(2027, 3, 29))
    far_b = _line("far-b", "SO381999", 40, date(2027, 4, 15))
    result = _walk(
        [_on_hand(100)], [far_a, far_b, SEP],
        [_landed("far-a", 60, lendable=True), _landed("far-b", 40, lendable=True)],
    )
    rows = _by_key(result)
    assert rows["sep"].status == STATUS_COVERED
    assert rows["sep"].assigned[0].lent_from_line_key == "far-b"
    assert rows["far-a"].status == STATUS_PINNED
    assert rows["far-a"].lent_qty == 0
    assert rows["far-b"].status == STATUS_ORDER_BACK
    assert rows["far-b"].lent_qty == pytest.approx(32)
    assert rows["far-b"].uncovered == pytest.approx(32)
    assert result.free[BIN] == 0


def test_ac_7b_two_claims_are_capped_by_the_floor_in_hold_order():
    """Each far line landed 60 but the bin holds 100: the second claim is capped at 40,
    and the 20 it never got is plain `short`, not a lend."""
    far_a = _line("far-a", "SO381065", 60, date(2027, 3, 29))
    far_b = _line("far-b", "SO381999", 60, date(2027, 4, 15))
    result = _walk(
        [_on_hand(100)], [far_a, far_b],
        [_landed("far-a", 60, lendable=True), _landed("far-b", 60, lendable=True)],
    )
    rows = _by_key(result)
    assert rows["far-a"].status == STATUS_PINNED
    assert rows["far-b"].status == STATUS_SHORT
    assert rows["far-b"].lent_qty == 0
    assert rows["far-b"].uncovered == pytest.approx(20)
    assert [(item.qty, item.pinned) for item in rows["far-b"].assigned] == [(40, True)]


def test_ac_8_a_lend_never_crosses_an_ownership_group():
    """The lent quantity is free on hand AT ITS BIN, in its own group's pile (R40)."""
    ib = _line("ib", "SO420000", 30, date(2026, 9, 1), warehouse="BRW-IB")
    result = _walk([_on_hand(88)], [FAR, ib], [_landed("far", 88, lendable=True)])
    rows = _by_key(result)
    assert rows["ib"].status == STATUS_SHORT
    assert rows["far"].status == STATUS_PINNED
    assert rows["far"].lent_qty == 0


# --------------------------------------------------------------------------- review round


def test_ac_9_the_groups_free_stock_at_any_bin_is_drawn_before_a_lend():
    """Reviewer finding 1: the pile is walked in the bins' own order, so a free bin that
    sorts AFTER the claimed one used to be reached only once the lend was made. The lend
    is what the group could not cover otherwise, whatever the bins are called."""
    for free_bin in ("AAA-BB", "ZZZ-BB"):
        free = SupplyEvent(
            key=f"on_hand:{free_bin.lower()}", kind=KIND_ON_HAND, warehouse=free_bin,
            at=AS_OF, qty=30,
        )
        result = _walk([_on_hand(88), free], [FAR, SEP], [_landed("far", 88, lendable=True)])
        rows = _by_key(result)
        assert rows["sep"].status == STATUS_COVERED, free_bin
        assert sorted((item.event.warehouse, item.qty, item.lent_from_line_key) for item in rows["sep"].assigned) == sorted([
            (free_bin, 30, None), ("BRW-BB", 2, "far"),
        ]), free_bin
        far = rows["far"]
        assert far.status == STATUS_ORDER_BACK, free_bin
        assert far.lent_qty == pytest.approx(2), free_bin
        assert far.uncovered == pytest.approx(2), free_bin
        assert _months(result)["2027-03"] == -2, free_bin


def test_ac_10_the_lend_does_not_depend_on_the_order_the_holds_arrive_in():
    """Reviewer finding 3: `_landed_holds` reads an unordered query. Two far lines on one
    floor, holds given both ways round, read the same: the earlier-due line claims first,
    and nothing is "lent" between two far lines when no nearer line exists."""
    far_a = _line("far-a", "SO381065", 60, date(2027, 3, 29))
    far_b = _line("far-b", "SO381999", 60, date(2027, 4, 15))
    holds = [_landed("far-a", 60, lendable=True), _landed("far-b", 60, lendable=True)]
    forwards = _by_key(_walk([_on_hand(100)], [far_a, far_b], holds))
    backwards = _by_key(_walk([_on_hand(100)], [far_b, far_a], list(reversed(holds))))
    for rows in (forwards, backwards):
        assert rows["far-a"].status == STATUS_PINNED
        assert rows["far-a"].lent_qty == 0
        assert [(item.qty, item.lent_from_line_key) for item in rows["far-a"].assigned] == [(60, None)]
        assert rows["far-b"].status == STATUS_SHORT
        assert rows["far-b"].lent_qty == 0
        assert rows["far-b"].uncovered == pytest.approx(20)


def test_ac_10b_two_lenders_are_charged_in_walk_order_whatever_the_hold_order():
    """Reviewer kill test K3: two far lines that both lend. The nearer line's draw is
    charged to the LATER-due lender first (it is the one whose claim comes up short), the
    same either way round."""
    far_a = _line("far-a", "SO381065", 50, date(2027, 3, 29))
    far_b = _line("far-b", "SO381999", 50, date(2027, 4, 15))
    near = _line("sep", "SO396071", 70, date(2026, 9, 1))
    holds = [_landed("far-a", 50, lendable=True), _landed("far-b", 50, lendable=True)]
    for demand, pinned in (
        ([far_a, far_b, near], holds),
        ([near, far_b, far_a], list(reversed(holds))),
    ):
        rows = _by_key(_walk([_on_hand(100)], demand, pinned))
        assert rows["sep"].status == STATUS_COVERED
        # far-a (Mar) steps first and takes what is left (30); far-b gets nothing.
        assert rows["far-a"].lent_qty == pytest.approx(20)
        assert rows["far-b"].lent_qty == pytest.approx(50)
        assert sorted(
            (item.qty, item.lent_from_line_key) for item in rows["sep"].assigned
        ) == [(20, "far-a"), (50, "far-b")]
        assert rows["far-a"].status == STATUS_ORDER_BACK
        assert rows["far-b"].status == STATUS_ORDER_BACK


def test_ac_11_a_lendable_hold_on_a_pin_only_event_pins_as_any_other():
    """Reviewer nit 4: a claim on an event the walk never sees could never be taken, so
    such a hold is spent in step 1 exactly as a plain pin (no KeyError in the walk)."""
    from dataclasses import replace

    floor = replace(_on_hand(88), pin_only=True)
    result = _walk([floor], [FAR, SEP], [_landed("far", 88, lendable=True)])
    rows = _by_key(result)
    assert rows["far"].status == STATUS_PINNED
    assert rows["far"].lent_qty == 0
    assert rows["sep"].status == STATUS_SHORT
