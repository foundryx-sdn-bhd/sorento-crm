"""EMAIL-HANDOVER-QTY (PR #1392): the handover email tells a LINE'S quantity change
even when `_settle_row_in_place` declined and the netting raised the remainder as its
own row - `PLAN-oi-handover-qty-change-30sep.md` S1, AC-1 to AC-7.

Owner (30 Sep): "will we show qty change in an additional column?". SO402757 /
SRTWT6808 moved 172 -> 436 in a planning-change apply; the settle in place declined
(placed row, no link), `_stamp_date_move` recorded ADVANCE with `was = {delivery_date}`
only, and the remainder went out as a bare ORDER line. Nothing on the email said the
line's quantity had changed.

Rule under test: in `_write`'s `asked_to_settle` fallback, when the line's live buy
total (`live_buy_qty`) differs from the composed `need`, the ONE settled line prints the
line's totals - QTY = what purchasing already held, QTY CHANGE TO = the new Buy - and its
remark carries the difference (`ORDER n` / `CANCEL BALANCE n NOS`) exactly as
`_settle_row_in_place`'s own line does. The netting still WRITES its rows (worklist
unchanged) but prints none of them as separate lines for that line.

Same harness as `tests/scm/test_oi_date_move_settle.py`: `_apply_date_move` drives the
real seam (book change, batch, confirm row, apply), `_captured_dispatches` reads the
dispatched context.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.models.project_so import (
    INQUIRY_CANCELLED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ADVANCE,
    IV_CANCEL_BALANCE,
    IV_ORDER,
    OrderInquiryRow,
)
from app.services import planning_change_service
from app.services.scm.outstanding_diff import DATE_AND_QTY_CHANGED, DATE_MOVED, QTY_CHANGED

from ..test_order_inquiry_handover_automation import (
    _captured_dispatches,
    _handover_calls,
    _register,
)
from ..test_planning_change_apply_on_board import (
    NOW,
    WAS_1,
    _build,
    _change,
    _confirm,
    _core_line,
    _core_so,
    _line_payload,
    _order_row,
    _project_line,
    _project_so,
    _rows_of,
    _uid,
    api,
    world,
)
from .test_oi_date_move_settle import _one_row_fixture

__all__ = ["api", "world"]  # re-exported fixtures; keeps linters from calling them unused

WAS_FMT = WAS_1.strftime("%d/%m/%Y")
NOW_FMT = NOW.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _apply_change(world, core, order, core_so, line, *, old_date, new_date, qty, new_qty=None):
    """`test_oi_date_move_settle._apply_date_move`, widened to a quantity-only change
    (`QTY_CHANGED`, same date) - the one shape that file never drives."""
    if new_date == old_date:
        kind = QTY_CHANGED
    elif new_qty is None:
        kind = DATE_MOVED
    else:
        kind = DATE_AND_QTY_CHANGED
    core.required_date = new_date
    line.delivery_date = new_date
    if new_qty is not None:
        core.qty_ordered = Decimal(new_qty)
        line.qty = Decimal(new_qty)
    world.db.commit()
    changes = [
        _change(
            kind, core, so_number=core_so.so_number,
            old_date=old_date, new_date=new_date, old_qty=qty,
            new_qty=qty if new_qty is None else new_qty,
        )
    ]
    batch = _build(world, changes, core_so, [str(core.id)])
    assert batch is not None
    out = planning_change_service.get_batch(world.db, str(batch.id))
    row_out = out["orders"][0]["rows"][0]
    planning_change_service.set_row_decision(
        world.db, str(batch.id), row_out["id"], "confirm"
    )
    result = planning_change_service.apply(world.db, str(batch.id), world.actor)
    world.db.commit()
    assert result["failed_orders"] == [], result["failed_orders"]


def _placed_no_link_fixture(api, *, qty="5"):
    """The SO349754 WESERP10B shape, and the owner's SO402757 one: a lone PLACED row with
    no link row behind it, which `_settle_row_in_place` declines outright."""
    fixture = _one_row_fixture(api, qty=qty)
    row = fixture["row"]
    row.state = INQUIRY_PLACED
    fixture["world"].db.commit()
    return fixture


def _line_entries(calls, item_code: str) -> list[dict]:
    """The printed lines for one item, off the LAST dispatch (the apply's own)."""
    matches = _handover_calls(calls)
    assert matches, "the apply must dispatch the handover"
    lines = matches[-1]["context"]["handover"]["lines"]
    return [entry for entry in lines if entry["item_code"] == item_code]


def _live_rows(world, line) -> list[OrderInquiryRow]:
    return [r for r in _rows_of(world, line) if r.state != INQUIRY_CANCELLED]


# ---------------------------------------------------------------------------
# AC-1: date AND quantity up on a declined line - one line, totals, ADVANCE + ORDER
# ---------------------------------------------------------------------------


def test_date_and_qty_up_prints_one_line_with_line_totals(api, monkeypatch):
    """AC-1. The owner's shape (172 -> 436, advanced): placed 5 with no link, the book
    moves the line to 8 on an earlier date. Worklist: the placed row keeps 5 and takes
    the new date, a fresh ORDER 3 is raised (unchanged from
    `test_a_date_and_qty_change_still_moves_the_date_on_the_existing_buy_row`). Email:
    ONE line for the item - QTY 5, QTY CHANGE TO 8, the two dates, remark
    `ADVANCE, ORDER 3` - and NO separate bare `ORDER` line."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _placed_no_link_fixture(api, qty="5")
    line = fixture["line"]
    placed_id = str(fixture["row"].id)
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="5", new_qty="8",
    )

    world.db.expire_all()
    live = _live_rows(world, line)
    placed = next(r for r in live if str(r.id) == placed_id)
    assert placed.state == INQUIRY_PLACED and Decimal(str(placed.qty)) == Decimal("5")
    assert placed.delivery_date == NOW
    fresh = [r for r in live if str(r.id) != placed_id and r.verb == IV_ORDER]
    assert len(fresh) == 1 and Decimal(str(fresh[0].qty)) == Decimal("3"), (
        "the worklist is unchanged: the remainder is still its own ORDER row"
    )

    entries = _line_entries(calls, fixture["row"].item_code)
    assert len(entries) == 1, (
        f"AC-1: exactly one printed line for the item, never a settled + ORDER pair, "
        f"got {entries}"
    )
    entry = entries[0]
    assert entry["was"] == {"qty": "5", "delivery_date": WAS_FMT}, entry
    assert entry["qty"] == "8", entry
    assert entry["delivery_date"] == NOW_FMT, entry
    assert entry["remark"] == "ADVANCE, ORDER 3", entry

    verbs = _handover_calls(calls)[-1]["context"]["handover"]["verbs"]
    assert "ADVANCE" in verbs and "ORDER" in verbs, verbs


# ---------------------------------------------------------------------------
# AC-2: date AND quantity down - one line, CANCEL BALANCE, exception row not printed
# ---------------------------------------------------------------------------


def test_date_and_qty_down_prints_one_line_and_hides_the_exception_row(api, monkeypatch):
    """AC-2. Placed 5, the book moves the line to 3 on an earlier date. The netting
    writes a CANCEL_BALANCE exception row (placed > need) - it stays in the worklist -
    but the email says it once, on the line's own totals."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _placed_no_link_fixture(api, qty="5")
    line = fixture["line"]
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="5", new_qty="3",
    )

    world.db.expire_all()
    exception_rows = [
        r for r in _live_rows(world, line) if r.verb == IV_CANCEL_BALANCE
    ]
    assert len(exception_rows) == 1 and Decimal(str(exception_rows[0].qty)) == Decimal("2"), (
        "the worklist still carries the CANCEL BALANCE exception row"
    )

    entries = _line_entries(calls, fixture["row"].item_code)
    assert len(entries) == 1, f"AC-2: one line, got {entries}"
    entry = entries[0]
    assert entry["was"] == {"qty": "5", "delivery_date": WAS_FMT}, entry
    assert entry["qty"] == "3", entry
    assert entry["remark"] == "ADVANCE, CANCEL BALANCE 2 NOS", entry


# ---------------------------------------------------------------------------
# AC-3: quantity only, date unchanged - was.qty only, ORDER n
# ---------------------------------------------------------------------------


def test_qty_only_change_prints_one_line_with_qty_was_only(api, monkeypatch):
    """AC-3. Nothing to stamp (`_stamp_date_move` returns False: same date), yet the
    total moved 5 -> 8 - the settled line is still recorded, off the placed row, with
    `was = {qty}` alone so the email shows QTY CHANGE TO and no DELIVERY DATE CHANGE TO."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _placed_no_link_fixture(api, qty="5")
    line = fixture["line"]
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=WAS_1, qty="5", new_qty="8",
    )

    world.db.expire_all()
    fresh = [
        r for r in _live_rows(world, line)
        if str(r.id) != str(fixture["row"].id) and r.verb == IV_ORDER
    ]
    assert len(fresh) == 1 and Decimal(str(fresh[0].qty)) == Decimal("3")

    entries = _line_entries(calls, fixture["row"].item_code)
    assert len(entries) == 1, f"AC-3: one line, got {entries}"
    entry = entries[0]
    assert entry["was"] == {"qty": "5"}, entry
    assert entry["qty"] == "8", entry
    assert entry["delivery_date"] == WAS_FMT, entry
    assert entry["remark"] == "ORDER 3", entry


# ---------------------------------------------------------------------------
# AC-4: two live raised rows superseded - one line, no CANCEL BALANCE pair
# ---------------------------------------------------------------------------


def test_two_raised_rows_superseded_print_one_line_with_totals(api, monkeypatch):
    """AC-4. Two still-owed RAISED rows (10 + 3), the book moves the line to 8, earlier.
    `_settle_row_in_place` declines (two live rows); both rows are cancelled by the
    netting and a fresh ORDER 8 is raised. Today that prints CANCEL BALANCE 10 NOS,
    CANCEL BALANCE 3 NOS and a bare ORDER beside the ADVANCE line - four lines for one
    line moving 13 -> 8. The email says it once."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _one_row_fixture(api, qty="10")
    line = fixture["line"]
    row_a = fixture["row"]
    row_b = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=row_a.order_inquiry_id,
        so_line_id=line.id, item_code=row_a.item_code, qty=Decimal("3"),
        delivery_date=row_a.delivery_date, stock_location=row_a.stock_location,
        verb=IV_ORDER, state=INQUIRY_RAISED, supply_decision_id=row_a.supply_decision_id,
    )
    world.db.add(row_b)
    world.db.commit()
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="13", new_qty="8",
    )

    world.db.expire_all()
    live = _live_rows(world, line)
    assert [Decimal(str(r.qty)) for r in live if r.verb == IV_ORDER] == [Decimal("8")], (
        "the worklist is unchanged: both old rows cancelled, one fresh ORDER 8"
    )

    entries = _line_entries(calls, row_a.item_code)
    assert len(entries) == 1, f"AC-4: one line for the item, got {entries}"
    entry = entries[0]
    assert entry["was"] == {"qty": "13", "delivery_date": WAS_FMT}, entry
    assert entry["qty"] == "8", entry
    assert entry["remark"] == "ADVANCE, CANCEL BALANCE 5 NOS", entry


# ---------------------------------------------------------------------------
# AC-5: pure date move - unchanged, no qty key
# ---------------------------------------------------------------------------


def test_pure_date_move_keeps_todays_line_without_a_qty_key(api, monkeypatch):
    """AC-5. Total unchanged (5 -> 5), only the date moves: exactly today's line -
    `was = {delivery_date}` and nothing else, so QTY CHANGE TO stays hidden."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _placed_no_link_fixture(api, qty="5")
    line = fixture["line"]
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="5",
    )

    entries = _line_entries(calls, fixture["row"].item_code)
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["was"] == {"delivery_date": WAS_FMT}, entry
    assert entry["qty"] == "5", entry
    assert entry["remark"] == "ADVANCE", entry


# ---------------------------------------------------------------------------
# AC-6: a fresh line (no live buy row) - unchanged, a plain raise
# ---------------------------------------------------------------------------


def test_fresh_line_still_prints_a_plain_order_line(api, monkeypatch):
    """AC-6. A wholly-reserved line that gets its first buy row on a date move
    (`test_fresh_line_gets_buy_row_only_no_notice`'s shape): there is no previous total
    to print against, so the line is a plain ORDER with no `was` - unchanged."""
    from ..test_planning_change_apply_on_board import _stock

    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=100)
    core_so = _core_so(db, world.company_id)
    core = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered="20", required_date=WAS_1
    )
    order = _project_so(
        db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number
    )
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core)
    db.commit()
    assert _confirm(client, order.id, [{
        "project_line_id": str(line.id), "timely_spo_qty": "0",
        "reserve": [{"warehouse_id": world.pool_wh.id, "qty": "20"}],
        "borrow": [], "buy_qty": "0",
    }]).status_code == 200
    calls.clear()

    far = date(2027, 3, 10)
    _apply_change(
        world, core, order, core_so, line, old_date=WAS_1, new_date=far, qty="20"
    )

    world.db.expire_all()
    entries = _line_entries(calls, _order_row(world, line).item_code)
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["remark"] == "ORDER", entry
    assert not entry["was"], entry
    assert entry["qty"] == "20", entry


# ---------------------------------------------------------------------------
# AC-7: the pure remark function reads the difference off an explicit qty
# ---------------------------------------------------------------------------


def _row(**kwargs) -> SimpleNamespace:
    base = dict(
        verb=IV_ORDER, qty=Decimal("5"), delivery_date=date(2026, 8, 25),
        previous_qty=None, previous_delivery_date=None, cited_document=None, note=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_handover_remark_reads_the_difference_off_an_explicit_qty():
    """AC-7. `row.qty` is the placed row's own 5 (it never changes on this path); the
    line's new total is what the remark has to read against."""
    from app.services.project_order_inquiry_service import (
        _handover_settle_diff,
        _handover_verb_keys,
        handover_remark,
    )

    row = _row(qty=Decimal("5"), delivery_date=date(2026, 8, 19))
    was = {"qty": Decimal("5"), "delivery_date": date(2026, 8, 25)}
    assert handover_remark("settled", row, was, qty=Decimal("8")) == "ADVANCE, ORDER 3"
    assert handover_remark("settled", row, was, qty=Decimal("3")) == (
        "ADVANCE, CANCEL BALANCE 2 NOS"
    )
    assert handover_remark("settled", row, {"qty": Decimal("13")}, qty=Decimal("8")) == (
        "CANCEL BALANCE 5 NOS"
    )
    # Without an explicit qty the row's own qty is read, as before (5 -> 5: date only).
    assert handover_remark("settled", row, was) == "ADVANCE"
    assert _handover_settle_diff(row, was, qty=Decimal("8")) == (
        IV_ADVANCE, IV_ORDER, Decimal("3")
    )
    assert _handover_verb_keys("settled", row, was, qty=Decimal("8")) == [IV_ADVANCE, IV_ORDER]
    assert _handover_verb_keys("settled", row, was) == [IV_ADVANCE]


# ---------------------------------------------------------------------------
# Review round 1 (blocking 1): a row the netting REDIRECTS changes what "before"
# means - the line falls back to today's per-row lines, never a totals line
# that understates the fresh row.
# ---------------------------------------------------------------------------


def test_redirected_row_falls_back_to_todays_per_row_lines(api, monkeypatch):
    """Two live rows (A partly linked 30 of 40 to a fully received SPO allocation, B
    raised 5), the book moves the line to 50, earlier. `_settle_row_in_place` declines
    (two live rows); the netting's own PARTLY_LINKED branch then redirects A to the
    pool (no own-arrival credit), so `placed` is 0 and the fresh row is ORDER 50 - not
    the 5 a totals line (45 -> 50) would have said. The email prints today's lines
    instead: ADVANCE (date only), CANCEL BALANCE 5 NOS for B, and the fresh ORDER 50."""
    from app.models.project_so import OrderInquiryLink
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService
    from tests.scm._own_arrival_fixture import spo_allocation_fully_received

    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    db = world.db
    fixture = _one_row_fixture(api, qty="40")
    line = fixture["line"]
    row_a = fixture["row"]
    spo = spo_allocation_fully_received(
        db, world.product, world.own_wh, qty=30, from_po_number=f"ZZT-PO-{_uid()[:8]}"
    )
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row_a.id,
            spo_allocation_id=spo.id, document=spo.spo_number, qty=Decimal("30"),
        )
    )
    db.flush()
    ProjectOrderInquiryService(db).refresh_link_state([row_a])
    row_b = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=row_a.order_inquiry_id,
        so_line_id=line.id, item_code=row_a.item_code, qty=Decimal("5"),
        delivery_date=row_a.delivery_date, stock_location=row_a.stock_location,
        verb=IV_ORDER, state=INQUIRY_RAISED, supply_decision_id=row_a.supply_decision_id,
    )
    db.add(row_b)
    db.commit()
    assert row_a.state == INQUIRY_PARTLY_LINKED, row_a.state
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="45", new_qty="50",
    )

    db.expire_all()
    db.refresh(row_a)
    assert row_a.redirected_to_pool is True, "the received link sends A to the pool"
    fresh = [
        r for r in _live_rows(world, line)
        if r.id not in (row_a.id, row_b.id) and r.verb == IV_ORDER
    ]
    assert len(fresh) == 1 and Decimal(str(fresh[0].qty)) == Decimal("50"), fresh

    entries = _line_entries(calls, row_a.item_code)
    remarks = sorted(entry["remark"] for entry in entries)
    assert remarks == ["ADVANCE", "CANCEL BALANCE 5 NOS", "ORDER"], entries
    order_line = next(entry for entry in entries if entry["remark"] == "ORDER")
    assert order_line["qty"] == "50", "the fresh row's own quantity, never need - held"
    # OI-EMAIL-PRINT (AC-P2): AC-OH-40..42's wording stays on the fresh row, not in the email.
    assert "Replaces 40 used" in (fresh[0].note or ""), fresh[0].note
    assert not any((entry["was"] or {}).get("qty") == "45" for entry in entries), (
        f"no totals line for a line the netting redirected: {entries}"
    )


# ---------------------------------------------------------------------------
# Review round 1 (should-fix 4): a raised row purchasing REFUSED is not held
# ---------------------------------------------------------------------------


def test_refused_raised_row_is_left_out_of_the_before_total(api, monkeypatch):
    """Placed 10 (no link) plus a raised 3 purchasing rejected; the book moves the
    line to 15, earlier. What purchasing HELD is 10, so the line reads 10 -> 15,
    ADVANCE, ORDER 5 - and 5 is exactly the fresh row the netting raises (need 15
    minus placed 10; the rejected row is cancelled, not netted)."""
    from app.models.project_so import ACK_REJECTED

    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _placed_no_link_fixture(api, qty="10")
    line = fixture["line"]
    row_a = fixture["row"]
    refused = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=row_a.order_inquiry_id,
        so_line_id=line.id, item_code=row_a.item_code, qty=Decimal("3"),
        delivery_date=row_a.delivery_date, stock_location=row_a.stock_location,
        verb=IV_ORDER, state=INQUIRY_RAISED, supply_decision_id=row_a.supply_decision_id,
        ack_state=ACK_REJECTED, rejected_by=world.actor, rejected_at=datetime.utcnow(),
    )
    world.db.add(refused)
    world.db.commit()
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="13", new_qty="15",
    )

    world.db.expire_all()
    fresh = [
        r for r in _live_rows(world, line)
        if r.id not in (row_a.id, refused.id) and r.verb == IV_ORDER
    ]
    assert len(fresh) == 1 and Decimal(str(fresh[0].qty)) == Decimal("5"), fresh

    entries = _line_entries(calls, row_a.item_code)
    assert len(entries) == 1, entries
    assert entries[0]["was"] == {"qty": "10", "delivery_date": WAS_FMT}, entries[0]
    assert entries[0]["qty"] == "15" and entries[0]["remark"] == "ADVANCE, ORDER 5", entries[0]


def test_refused_row_alone_never_prints_a_five_to_five_line(api, monkeypatch):
    """Review round 1 (nit): placed 5 (no link) plus a refused raised 3, the book only
    moves the date - what purchasing holds (5) already equals the need, so no totals
    line: today's date-only ADVANCE line, and the refused row's own cancelled line."""
    from app.models.project_so import ACK_REJECTED

    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _placed_no_link_fixture(api, qty="5")
    line = fixture["line"]
    row_a = fixture["row"]
    refused = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=row_a.order_inquiry_id,
        so_line_id=line.id, item_code=row_a.item_code, qty=Decimal("3"),
        delivery_date=row_a.delivery_date, stock_location=row_a.stock_location,
        verb=IV_ORDER, state=INQUIRY_RAISED, supply_decision_id=row_a.supply_decision_id,
        ack_state=ACK_REJECTED, rejected_by=world.actor, rejected_at=datetime.utcnow(),
    )
    world.db.add(refused)
    world.db.commit()
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="8", new_qty="5",
    )

    entries = _line_entries(calls, row_a.item_code)
    settled = [e for e in entries if e["remark"] == "ADVANCE"]
    assert len(settled) == 1 and settled[0]["was"] == {"delivery_date": WAS_FMT}, entries
    assert not any((e["was"] or {}).get("qty") == "5" for e in entries), entries
