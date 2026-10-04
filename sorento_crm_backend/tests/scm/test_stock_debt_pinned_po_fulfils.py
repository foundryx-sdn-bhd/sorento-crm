"""R43 (owner, 28 Sep 2026, #1346): a PO line pinned to a sales order fulfils it, the pure half.

No database. The owner's production case after #1332, walked on the day it was reported
(`AS_OF`, 28 Sep 2026):

* SO419208 x CSK14A-NL at BRW-BB, two open lines, both due 14/09/2026: 4 outstanding
  (ordered 135, delivered 131) and 1,305 outstanding.
* PO 202609-S0029, Delivery date 10/09/2026 (18 days past): line 1 41 with no S/O, line 2
  4 naming SO419208, line 3 1,305 naming SO419208. Nothing received, nothing placed.

What #1332 did with it, and why (the diagnosis these tests pin):

* At the shipped 0-day rule the PO is past its date, so `counted_event` refuses it and
  `_book_so_holds` asked only about counted PO lines: no pin at all, both rows Short.
* Raised to 30 days the PO counts on `as_of + 30`, AFTER the rows' own date, so the pin
  was booked `late_pinned` into `short_at_date`: every row Pinned and still Short 1,309.
* Both PO lines name the same ORDER (the S/O is resolved at document level) and each
  filled that order's lines in required-date order, so line 3's 1,305 spilt over the 4-unit
  row and line 2's 4 landed on the 1,305 row.

The route half is `test_stock_debt_pinned_po_fulfils_routes.py`.
"""
from __future__ import annotations

from datetime import date

from app.services.scm.stock_debt_service import book_so_pins
from app.services.scm.supply_assignment import (
    KIND_PO,
    STATUS_PINNED,
    STATUS_SHORT,
    DemandLine,
    Hold,
    SupplyEvent,
    assign,
)

AS_OF = date(2026, 9, 28)
TBA_FROM = date(2029, 1, 1)
DUE = date(2026, 9, 14)
DELIVERY = date(2026, 9, 10)
ORDER = "id-SO419208"


def _po(key, qty, *, line_no, at=DELIVERY, warehouse="BRW-BB"):
    return SupplyEvent(
        key=f"po:{key}",
        kind=KIND_PO,
        warehouse=warehouse,
        at=at,
        qty=qty,
        ref=f"PO 202609-S0029 line {line_no}",
        po_number="202609-S0029",
        po_line_number=line_no,
        purchase_order_id="po-202609-S0029",
    )


def _line(key, qty, *, core_line_no, source_ref=None, due=DUE, order=ORDER):
    return DemandLine(
        key=key,
        so_number="SO419208",
        line_no=None,
        warehouse="BRW-BB",
        agent_code="CONTACT Y",
        required_date=due,
        open_qty=qty,
        sales_order_id=order,
        core_line_no=core_line_no,
        source_ref=source_ref,
    )


def _walk(supply, demand, pinned, *, grace=0, dead=0):
    return assign(
        "CSK14A-NL",
        as_of=AS_OF,
        tba_from=TBA_FROM,
        lead_days=90,
        supply=supply,
        demand=demand,
        pinned=pinned,
        overdue_grace_days=grace,
        overdue_dead_days=dead,
    )


def _production(*, line_refs=True, reversed_keys=False):
    """The owner's case. `reversed_keys` names the 1,305 PO line so it sorts FIRST, which
    is the order that sent its quantity over the 4-unit row in production."""
    row_4 = _line("row4", 4, core_line_no=1, source_ref="AED:SO419208:d1")
    row_1305 = _line("row1305", 1305, core_line_no=2, source_ref="AED:SO419208:d2")
    free_41 = _po("l1", 41, line_no=1)
    line_2 = _po("b-line2" if reversed_keys else "a-line2", 4, line_no=2)
    line_3 = _po("a-line3" if reversed_keys else "b-line3", 1305, line_no=3)
    refs = {line_2.key: "AED:SO419208:d1", line_3.key: "AED:SO419208:d2"}
    pins = book_so_pins(
        [("P", line_2, ORDER, 0.0), ("P", line_3, ORDER, 0.0)],
        {"P": [row_1305, row_4]},
        [],
        tba_from=TBA_FROM,
        line_refs=refs if line_refs else None,
    )
    return [free_41, line_2, line_3], [row_4, row_1305], pins


def _covered_by(result):
    return {
        row.line.key: sorted((item.event.po_line_number, item.qty) for item in row.assigned)
        for row in result.lines
    }


def _months(result):
    return {month.key: month.balance for month in result.months}


# ---------------------------------------------------------------- the production case


def test_production_case_each_po_line_lands_on_its_own_row():
    """AC-PO-12: line 2 (4) covers the 4-unit row only, line 3 (1,305) the 1,305 row only,
    each through the sales-order LINE its S/O names (`from_so_line_ref` = the line's own
    `source_ref`)."""
    _supply, _demand, pins = _production(reversed_keys=True)

    assert sorted((pin.line_key, pin.supply_key, pin.qty) for pin in pins) == [
        ("row1305", "po:a-line3", 1305),
        ("row4", "po:b-line2", 4),
    ]


def test_production_case_at_the_0_day_rule_is_fulfilled_short_0():
    """AC-PO-10 + AC-PO-11: at the shipped 0-day rule the PO is 18 days past its date and
    still fulfils both rows: Assigned 1,309, each row Pinned, Short 0, and the month the
    rows sit in reads 0 (line 1's unpinned 41 is past the rule, so it counts as nothing)."""
    supply, demand, pins = _production(reversed_keys=True)
    result = _walk(supply, demand, pins, grace=0, dead=0)

    assert _covered_by(result) == {"row4": [(2, 4)], "row1305": [(3, 1305)]}
    assert all(row.status == STATUS_PINNED for row in result.lines)
    assert [row.short_at_date for row in result.lines] == [0, 0]
    assert sum(item.qty for row in result.lines for item in row.assigned) == 1309
    assert _months(result)["2026-09"] == 0
    assert all(balance == 0 for balance in _months(result).values())


def test_production_case_at_30_days_is_not_short_1309():
    """AC-PO-11: the owner's second screen. At 30 / 30 the PO counts on 28 Oct, after the
    rows' date; the pin still fulfils them, so nothing is booked Short in September."""
    supply, demand, pins = _production(reversed_keys=True)
    result = _walk(supply, demand, pins, grace=30, dead=30)

    assert _covered_by(result) == {"row4": [(2, 4)], "row1305": [(3, 1305)]}
    assert all(row.status == STATUS_PINNED for row in result.lines)
    assert [row.short_at_date for row in result.lines] == [0, 0]
    months = _months(result)
    assert months["2026-09"] == 0
    assert months["2026-10"] == 41, "only line 1's unpinned 41 is spare, on its assumed date"


def test_document_level_s_o_matches_each_po_line_to_the_row_of_its_own_quantity():
    """AC-PO-12, when the S/O resolves only to the ORDER (the line ref names no line held
    here): a PO line whose outstanding is exactly what one row needs covers that row,
    whatever order the PO lines sort in."""
    for reversed_keys in (False, True):
        supply, demand, pins = _production(line_refs=False, reversed_keys=reversed_keys)
        result = _walk(supply, demand, pins)

        assert _covered_by(result) == {"row4": [(2, 4)], "row1305": [(3, 1305)]}, (
            reversed_keys
        )


# ---------------------------------------------------------------- the rulings


def test_a_partly_covered_row_is_short_outstanding_less_assigned():
    """AC-PO-11: a row of 1,305 pinned 1,000 reads Short 305, and its month books 305."""
    po = _po("x", 1000, line_no=3)
    row = _line("row", 1305, core_line_no=2)
    pins = book_so_pins([("P", po, ORDER, 0.0)], {"P": [row]}, [], tba_from=TBA_FROM)
    result = _walk([po], [row], pins)

    [line] = result.lines
    assert line.status == STATUS_SHORT
    assert line.uncovered == 305
    assert line.short_at_date == 305
    assert _months(result)["2026-09"] == -305


def test_an_undated_po_naming_the_order_still_fulfils_it():
    """AC-PO-10: "regardless of the PO's delivery date", so a PO line with no date at all
    pins and fulfils too."""
    po = _po("x", 30, line_no=1, at=None)
    row = _line("row", 30, core_line_no=1, due=date(2026, 10, 20))
    pins = book_so_pins([("P", po, ORDER, 0.0)], {"P": [row]}, [], tba_from=TBA_FROM)
    result = _walk([po], [row], pins)

    [line] = result.lines
    assert line.status == STATUS_PINNED
    assert line.short_at_date == 0
    assert all(month.balance == 0 for month in result.months)


def test_the_unpinned_rest_of_a_past_due_po_still_follows_the_overdue_rule():
    """AC-PO-10: the rule keeps governing UNPINNED PO supply. A PO of 100, past its date at
    0 days, pins the 30 its order needs; the other 70 counts as nothing, so another order
    at the same bin stays short."""
    po = _po("x", 100, line_no=1)
    named = _line("named", 30, core_line_no=1)
    other = _line("other", 50, core_line_no=1, order="id-OTHER")
    pins = book_so_pins(
        [("P", po, ORDER, 0.0)], {"P": [named, other]}, [], tba_from=TBA_FROM
    )
    result = _walk([po], [named, other], pins)

    by_key = {row.line.key: row for row in result.lines}
    assert by_key["named"].status == STATUS_PINNED
    assert by_key["named"].short_at_date == 0
    assert by_key["other"].status == STATUS_SHORT
    assert by_key["other"].short_at_date == 50
    assert [event.key for event in result.uncounted] == ["po:x"]


def test_a_placement_on_a_past_due_po_line_fulfils_in_the_view():
    """AC-PO-11: the view marks a placement on a PO line `fulfils` too (`_assignments`), so a
    pinned PO line reads the same whether the book or a Confirm pinned it."""
    po = _po("x", 4, line_no=2)
    row = _line("row", 4, core_line_no=1)
    placement = Hold(line_key="row", supply_key=po.key, qty=4, kind=KIND_PO, fulfils=True)
    result = _walk([po], [row], [placement])

    [line] = result.lines
    assert line.status == STATUS_PINNED
    assert line.short_at_date == 0


def test_a_hold_that_does_not_fulfil_keeps_the_spo_precedent():
    """The board path is unchanged: a hold on a dead document that is NOT marked `fulfils`
    (every hold the board reads) still books its quantity short (AC-S2-7)."""
    po = _po("x", 4, line_no=2)
    row = _line("row", 4, core_line_no=1)
    placement = Hold(line_key="row", supply_key=po.key, qty=4, kind=KIND_PO)
    result = _walk([po], [row], [placement])

    [line] = result.lines
    assert line.status == STATUS_PINNED
    assert line.short_at_date == 4


def test_the_s_o_line_ref_wins_over_quantity_and_date():
    """AC-PO-12, the named pass on its own: two rows of 4 and two PO lines of 4 with CROSSED
    refs (the first PO line names the second row). Quantity and date cannot tell them
    apart, so only the S/O line ref puts each PO line on the row it names. Trailing
    whitespace on the ref does not change the answer."""
    row_1 = _line("r1", 4, core_line_no=1, source_ref="AED:SO419208:d1")
    row_2 = _line("r2", 4, core_line_no=2, source_ref="AED:SO419208:d2 ")
    first = _po("a", 4, line_no=2)
    second = _po("b", 4, line_no=3)
    pins = book_so_pins(
        [("P", first, ORDER, 0.0), ("P", second, ORDER, 0.0)],
        {"P": [row_1, row_2]},
        [],
        tba_from=TBA_FROM,
        line_refs={first.key: "AED:SO419208:d2", second.key: " AED:SO419208:d1"},
    )

    assert sorted((pin.line_key, pin.supply_key) for pin in pins) == [
        ("r1", "po:b"),
        ("r2", "po:a"),
    ]


def test_a_lone_po_line_naming_only_the_order_keeps_date_order():
    """Review of this lane: the exact-quantity pass tells two PO lines of one order apart.
    A LONE PO line of 100 naming only the order fills its September row of 60 first and
    gives the December row of 100 the other 40, rather than jumping to the row whose need
    happens to equal 100 and leaving September red."""
    po = _po("x", 100, line_no=1, at=date(2026, 10, 1))
    september = _line("sep", 60, core_line_no=1, due=date(2026, 9, 30))
    december = _line("dec", 100, core_line_no=2, due=date(2026, 12, 15))
    pins = book_so_pins(
        [("P", po, ORDER, 0.0)], {"P": [september, december]}, [], tba_from=TBA_FROM
    )

    assert [(pin.line_key, pin.qty) for pin in pins] == [("sep", 60), ("dec", 40)]


def _received_placement(dead):
    """A PO line of 10 with 6 received (4 outstanding), a placement of 10 on it, and 6 on
    hand at the same bin, for a line needing 10."""
    po = _po("x", 4, line_no=1)
    on_hand = SupplyEvent(key="on_hand:bb", kind="on_hand", warehouse="BRW-BB",
                          at=AS_OF, qty=6)
    row = _line("row", 10, core_line_no=1)
    placement = Hold(line_key="row", supply_key=po.key, qty=10, kind=KIND_PO, fulfils=True)
    return _walk([po, on_hand], [row], [placement], dead=dead)


def test_a_fulfilling_hold_on_a_past_due_po_is_capped_by_its_outstanding():
    """Review of this lane: a pin fulfils what the PO still brings, never more. At 0 days
    (the PO past the rule) and at 60 days (counted) the line gets the same answer: 4 from
    the PO, 6 from the on hand, nothing short and nothing spare."""
    for dead in (0, 60):
        result = _received_placement(dead)
        [line] = result.lines
        assert sorted((item.event.key, item.qty) for item in line.assigned) == [
            ("on_hand:bb", 6),
            ("po:x", 4),
        ], dead
        assert line.short_at_date == 0
        assert all(month.balance == 0 for month in result.months), dead


def test_two_fulfilling_holds_on_one_past_due_po_share_its_outstanding():
    """Two placements of 4 on one past-due PO with 4 outstanding: the first is fulfilled,
    the second finds nothing left and its line stays short."""
    po = _po("x", 4, line_no=1)
    first = _line("r1", 4, core_line_no=1)
    second = _line("r2", 4, core_line_no=2)
    holds = [
        Hold(line_key=key, supply_key=po.key, qty=4, kind=KIND_PO, fulfils=True)
        for key in ("r1", "r2")
    ]
    result = _walk([po], [first, second], holds)

    by_key = {row.line.key: row for row in result.lines}
    assert by_key["r1"].status == STATUS_PINNED
    assert by_key["r2"].status == STATUS_SHORT
    assert by_key["r2"].short_at_date == 4
