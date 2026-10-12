"""A board Confirm on a PARTLY-APPLIED planning change (BOARD-FROZEN-DISPLAY, SO422492).

`documentation/plans/scm/PLAN-board-frozen-display-12oct.md`.

The prod shape (12 Oct 2026): one book upload builds a batch with a row per changed line.
The first board press confirms only SOME of those lines, so their rows read `applied` and
the batch stays open (`applied_at` NULL) for the rows nobody decided yet (S5b, #1245). The
planner then saves a fresh decision on a line whose row is ALREADY applied and presses
Confirm again. That second press used to:

* refuse with "Nothing on this planning change could be confirmed." when the press named
  only lines whose rows were no longer pending (`set_row_decision` stored the decision on
  the applied row, `_apply_one_order` takes pending rows only, so nothing was confirmed);
* or, when the same press also named a line that DID confirm, write the new revision
  WITHOUT the line: its frozen snapshot had drifted from the book, so the carry-forward
  dropped it, the order-inquiry diff read Buy 0 and purchasing got "CANCEL ALL" for a line
  the planner had just confirmed as Buy 24 (SRTWT2201 line 688 on SO422492).

Helpers come from the same world as `tests/scm/test_board_picks_up_pending_change.py`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.planning_change import (
    PLANNING_CHANGE_STATE_APPLIED,
    PLANNING_CHANGE_STATE_PENDING,
    PlanningChangeBatch,
    PlanningChangeRow,
)
from app.models.project_so import (
    DECISION_ACTIVE,
    INQUIRY_CANCELLED,
    IV_CANCEL_BALANCE,
    OrderInquiryRow,
    SOSupplyDecision,
)
from app.services import planning_change_service
from app.services.scm.outstanding_diff import DATE_MOVED

from tests.scm.test_planning_change_gate_held_or_inquiry import (
    _adopted_line,
    _build,
    _change,
)
from tests.test_planning_changes import BASE, _confirm, _line_payload, _stock, api  # noqa: F401

MOVED_TO = date(2027, 3, 10)
MOVED_AGAIN = date(2027, 4, 2)


def _buy(line_id, qty):
    return _line_payload(line_id, buy_qty=qty, buy_reason="ZZT buy for the project")


def _reserve(world, line_id, qty):
    return _line_payload(line_id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": qty}])


def _press(client, order, batch, lines):
    return client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={"orders": [{"pso_id": order.id, "lines": lines, "batch_id": str(batch.id)}]},
    )


def _result(response, order):
    assert response.status_code == 200, response.text
    by_pso = {r["pso_id"]: r for r in response.json()["results"]}
    return by_pso[str(order.id)]


def _active(db, order):
    return (
        db.query(SOSupplyDecision)
        .filter_by(project_sales_order_id=str(order.id), state=DECISION_ACTIVE)
        .one()
    )


def _snapshot(decision, line):
    for entry in decision.line_snapshots or []:
        if str(entry.get("project_line_id")) == str(line.id):
            return entry
    return None


def _snapshot_buy(entry):
    """The Buy quantity a frozen line snapshot carries, whichever spelling it uses."""
    if entry is None:
        return None
    if entry.get("buy_qty") is not None:
        return Decimal(str(entry["buy_qty"]))
    total = Decimal("0")
    for component in entry.get("components") or []:
        if component.get("source") == "order":
            total += Decimal(str(component.get("qty") or "0"))
    return total


def _partly_applied(client, world):
    """SO422492 in miniature: two decided lines, one book upload moving both, and a first
    board press that confirms line 1 only - so line 1's row is `applied` and line 2's is
    still pending on a batch that is still open."""
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)
    so, core_1, order, line_1 = _adopted_line(db, world, qty="24", line_no=1)
    _so, core_2, _order, line_2 = _adopted_line(db, world, qty="10", line_no=2, core_so=so)
    _confirm(client, order.id, {"lines": [_buy(line_1.id, "24"), _reserve(world, line_2.id, "10")]})
    batch = _build(db, world, so, [
        (_change(DATE_MOVED, so, core_1, old_qty="24", new_qty="24", new_date=MOVED_TO), core_1),
        (_change(DATE_MOVED, so, core_2, old_qty="10", new_qty="10", new_date=MOVED_TO), core_2),
    ])
    assert batch is not None
    db.commit()

    first = _result(_press(client, order, batch, [_buy(line_1.id, "24")]), order)
    assert first["ok"] is True, (first.get("error"), first.get("failing_lines"))

    db.expire_all()
    rows = {
        str(r.project_line_id): r
        for r in db.query(PlanningChangeRow).filter_by(batch_id=batch.id).all()
    }
    assert rows[str(line_1.id)].applied_state == PLANNING_CHANGE_STATE_APPLIED
    assert rows[str(line_2.id)].applied_state == PLANNING_CHANGE_STATE_PENDING
    assert db.get(PlanningChangeBatch, batch.id).applied_at is None
    return so, order, batch, (core_1, line_1), (core_2, line_2)


def test_a_saved_line_on_an_already_applied_row_confirms_a_new_revision(api):
    """The owner's prod refusal: Confirm (4) on lines whose rows were already applied read
    "Nothing on this planning change could be confirmed." The press must confirm them as an
    ordinary decision, beside the batch, and write a new revision."""
    client, world = api
    db = world.db
    _so, order, batch, (_core_1, line_1), _line_2 = _partly_applied(client, world)
    before = _active(db, order).revision_no

    second = _result(_press(client, order, batch, [_reserve(world, line_1.id, "24")]), order)

    assert second["ok"] is True, (second.get("error"), second.get("failing_lines"))
    db.expire_all()
    after = _active(db, order)
    assert after.revision_no == before + 1
    entry = _snapshot(after, line_1)
    assert entry is not None, "line 1 must be covered by the new revision"
    assert _snapshot_buy(entry) == Decimal("0"), entry


def test_a_line_the_press_named_is_never_dropped_so_purchasing_is_never_told_cancel_all(api):
    """SRTWT2201 line 688 on SO422492: the second press named line 1 (Buy 24, row already
    applied) AND line 2 (row still pending). Line 2 confirmed, line 1 was silently left out;
    the book had moved line 1 again since its frozen snapshot, so the carry-forward dropped
    it, the new revision held no Buy for it and its ORDER row was cancelled ("CANCEL ALL").
    Now line 1 is confirmed as posted, its ORDER stays live at 24, and nothing is cancelled."""
    client, world = api
    db = world.db
    _so, order, batch, (core_1, line_1), (_core_2, line_2) = _partly_applied(client, world)

    # The book moves line 1 again after the first press (a second upload, a manual edit):
    # the frozen snapshot of line 1 no longer matches the live line.
    core_1.required_date = MOVED_AGAIN
    line_1.required_date = MOVED_AGAIN
    db.commit()
    live_order_rows_before = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == str(line_1.id))
        .filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    assert live_order_rows_before, "line 1's Buy 24 must have raised an order-inquiry row"

    second = _result(
        _press(client, order, batch, [_buy(line_1.id, "24"), _reserve(world, line_2.id, "10")]),
        order,
    )

    assert second["ok"] is True, (second.get("error"), second.get("failing_lines"))
    db.expire_all()
    after = _active(db, order)
    entry = _snapshot(after, line_1)
    assert entry is not None, "line 1 was named by the press and must be in the revision"
    assert _snapshot_buy(entry) == Decimal("24"), entry
    cancels = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == str(line_1.id))
        .filter(
            (OrderInquiryRow.verb == IV_CANCEL_BALANCE)
            | (OrderInquiryRow.state == INQUIRY_CANCELLED)
        )
        .all()
    )
    assert cancels == [], [(r.verb, r.state, r.qty) for r in cancels]
    live = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == str(line_1.id))
        .filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    assert sum((Decimal(r.qty) for r in live), Decimal("0")) == Decimal("24"), [
        (r.verb, r.state, r.qty) for r in live
    ]


def test_the_batch_closes_once_only_rows_a_newer_revision_overtook_are_left(api):
    """(b) A batch row a newer, independent revision has overtaken (`held_json.revision_no`
    behind the order's active revision) can never be applied any more - `set_row_decision`
    refuses it and `row_out` reads it as superseded. It must not keep the whole batch open:
    once only such rows are left pending, the press that writes the batch's last live row
    stamps `applied_at`, so the board stops opening the order on it."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)
    so, core_1, order, line_1 = _adopted_line(db, world, qty="24", line_no=1)
    _so, core_2, _order, line_2 = _adopted_line(db, world, qty="10", line_no=2, core_so=so)
    _confirm(client, order.id, {"lines": [_buy(line_1.id, "24"), _reserve(world, line_2.id, "10")]})
    batch = _build(db, world, so, [
        (_change(DATE_MOVED, so, core_1, old_qty="24", new_qty="24", new_date=MOVED_TO), core_1),
        (_change(DATE_MOVED, so, core_2, old_qty="10", new_qty="10", new_date=MOVED_TO), core_2),
    ])
    db.commit()
    rows = {
        str(r.project_line_id): r
        for r in db.query(PlanningChangeRow).filter_by(batch_id=batch.id).all()
    }
    # A newer revision confirmed OUTSIDE this batch overtakes line 2's row: the row is held
    # against a revision that is no longer the order's current one.
    held = dict(rows[str(line_2.id)].held_json or {})
    held["revision_no"] = (held.get("revision_no") or 1) - 1
    rows[str(line_2.id)].held_json = held
    db.commit()
    row_2_out = planning_change_service.row_out(db, rows[str(line_2.id)])
    assert row_2_out["applied_state"] == "superseded"

    result = _result(_press(client, order, batch, [_buy(line_1.id, "24")]), order)

    assert result["ok"] is True, (result.get("error"), result.get("failing_lines"))
    db.expire_all()
    reloaded = db.get(PlanningChangeBatch, batch.id)
    assert reloaded.applied_at is not None, "only an overtaken row was left; the batch is done"
    # Never marked applied on the strength of nothing: the overtaken row stays what it was.
    row_2 = db.get(PlanningChangeRow, rows[str(line_2.id)].id)
    assert row_2.applied_state != PLANNING_CHANGE_STATE_APPLIED
    board = client.get(f"{BASE}/fulfilment-planning/board", params={"orders": so.so_number})
    assert board.status_code == 200, board.text
    assert board.json()["orders"][0].get("pending_change_batch_id") is None


def test_the_batch_stays_open_while_a_genuinely_pending_row_is_left(api):
    """The S5b rule (#1245) still holds: a row nobody decided, held on the CURRENT revision,
    keeps the batch open after a partial press - (b) narrows the close, it does not drop it."""
    client, world = api
    db = world.db
    _so, _order, batch, _line_1, _line_2 = _partly_applied(client, world)
    assert db.get(PlanningChangeBatch, batch.id).applied_at is None


def test_a_named_line_the_batch_cannot_apply_fails_the_order_and_names_the_line(api):
    """Never fail silently: a line the press named through a LIVE row that the apply then
    drops (here the book cancelled the line between compose and apply, so the row is
    superseded) fails the whole order with the line named - nothing is written, rather than
    a revision quietly missing the line."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)
    so, core_1, order, line_1 = _adopted_line(db, world, qty="24", line_no=1)
    _so, core_2, _order, line_2 = _adopted_line(db, world, qty="10", line_no=2, core_so=so)
    _confirm(client, order.id, {"lines": [_buy(line_1.id, "24"), _reserve(world, line_2.id, "10")]})
    batch = _build(db, world, so, [
        (_change(DATE_MOVED, so, core_1, old_qty="24", new_qty="24", new_date=MOVED_TO), core_1),
        (_change(DATE_MOVED, so, core_2, old_qty="10", new_qty="10", new_date=MOVED_TO), core_2),
    ])
    db.commit()
    before = _active(db, order).revision_no
    core_2.line_status = "cancelled"
    db.commit()

    result = _result(
        _press(client, order, batch, [_buy(line_1.id, "24"), _reserve(world, line_2.id, "10")]),
        order,
    )

    assert result["ok"] is False
    assert "line 2" in (result.get("error") or ""), result.get("error")
    assert [f["line_no"] for f in result.get("failing_lines") or []] == [2]
    db.expire_all()
    assert _active(db, order).revision_no == before


def test_reconfirming_a_line_purchasing_was_told_to_cancel_raises_a_fresh_order(api):
    """Owner question (12 Oct): once fixed, does re-confirming SRTWT2201 line 688 as Buy 24,
    after rev 4 told purchasing CANCEL ALL for it, raise a new ORDER 24? Reproduces the prod
    state with an ordinary confirm that leaves the drifted line out, then confirms it again."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=200)
    so, core_1, order, line_1 = _adopted_line(db, world, qty="24", line_no=1)
    _so, _core_2, _order, line_2 = _adopted_line(db, world, qty="10", line_no=2, core_so=so)
    _confirm(client, order.id, {"lines": [_buy(line_1.id, "24"), _reserve(world, line_2.id, "10")]})
    core_1.required_date = MOVED_AGAIN
    line_1.required_date = MOVED_AGAIN
    db.commit()
    # The prod rev 4: a confirm that names the other line only - the drifted line 1 is not
    # carried, so it leaves the revision and its ORDER row is cancelled.
    _confirm(client, order.id, {"lines": [_buy(line_2.id, "10")]})
    db.expire_all()
    assert _snapshot(_active(db, order), line_1) is None
    live = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == str(line_1.id))
        .filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )
    assert sum((Decimal(r.qty) for r in live if r.verb != IV_CANCEL_BALANCE), Decimal("0")) == 0

    _confirm(client, order.id, {"lines": [_buy(line_1.id, "24")]})

    db.expire_all()
    assert _snapshot_buy(_snapshot(_active(db, order), line_1)) == Decimal("24")
    orders = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == str(line_1.id))
        .filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
        .filter(OrderInquiryRow.verb != IV_CANCEL_BALANCE)
        .all()
    )
    assert sum((Decimal(r.qty) for r in orders), Decimal("0")) == Decimal("24"), [
        (r.verb, r.state, r.qty) for r in orders
    ]
