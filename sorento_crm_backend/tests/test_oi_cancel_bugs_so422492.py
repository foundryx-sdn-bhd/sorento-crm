"""SO422492 (prod incident, 12 Oct 2026): the order-inquiry netting told purchasing to
cancel quantity the book still needed. `documentation/plans/scm/PLAN-oi-cancel-bugs-12oct.md`.

* BUG A: line 592 SRTWC8840-SC was cut 25 -> 22 by AutoCount and its planning change row
  stayed undecided. Confirming a DIFFERENT line of the order dropped line 592 from the new
  revision and `_retire_uncovered_rows` emailed CANCEL ALL 25 ("Superseded by revision 4"),
  although the book needs 22 of them. The row has to stand until CS decides the change, and
  that decision then restates it by the delta only.

Runs on Postgres through the board's own Confirm (`test_planning_change_apply_on_board`'s
fixtures); every row is seeded here, CI's database has no data.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.planning_change import PlanningChangeRow
from app.models.project_so import INQUIRY_CANCELLED, IV_ORDER, OrderInquiryRow
from app.services.project_order_inquiry_service import _HANDOVER_PENDING_KEY
from app.services.scm.outstanding_diff import DATE_MOVED, QTY_CHANGED

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    _build,
    _change,
    _confirm,
    _order_row,
    api,
    world,
)
from .test_planning_changes import _core_line, _core_so, _line_payload, _project_line, _project_so

D_592 = date(2026, 11, 2)
D_OTHER = date(2026, 11, 9)
D_OTHER_NEW = date(2026, 11, 16)


def _handovers_for(world, row):
    return [
        item for item in world.db.info.get(_HANDOVER_PENDING_KEY, [])
        if item.get("row_id") == str(row.id)
    ]


def _two_bought_lines(api):
    """Line 592 (25) and another line (10) of one order, both confirmed as Buy, so each has
    a raised ORDER row purchasing was told about."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_592 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="25",
                          required_date=D_592)
    core_other = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10",
                            required_date=D_OTHER)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line_592 = _project_line(db, order, line_no=592, product=world.product, core_line=core_592)
    line_other = _project_line(db, order, line_no=600, product=world.product,
                               core_line=core_other)
    db.commit()
    response = _confirm(client, order.id, [
        _line_payload(line_592.id, buy_qty="25"),
        _line_payload(line_other.id, buy_qty="10"),
    ])
    assert response.status_code == 200, response.text
    return core_so, order, (core_592, core_other), (line_592, line_other)


def test_bug_a_confirming_another_line_keeps_the_undecided_lines_row(api):
    client, world = api
    db = world.db
    core_so, order, (core_592, core_other), (line_592, line_other) = _two_bought_lines(api)
    row_592 = _order_row(world, line_592)
    assert Decimal(str(row_592.qty)) == Decimal("25")

    # AutoCount: line 592 cut 25 -> 22, the other line delayed a week.
    core_592.qty_ordered = Decimal("22")
    line_592.qty = Decimal("22")
    core_other.required_date = D_OTHER_NEW
    line_other.delivery_date = D_OTHER_NEW
    db.commit()
    batch = _build(world, [
        _change(QTY_CHANGED, core_592, so_number=core_so.so_number,
                old_date=D_592, new_date=D_592, old_qty="25", new_qty="22"),
        _change(DATE_MOVED, core_other, so_number=core_so.so_number,
                old_date=D_OTHER, new_date=D_OTHER_NEW, old_qty="10", new_qty="10"),
    ], core_so, [str(core_592.id), str(core_other.id)])
    assert batch is not None
    pending_592 = (
        db.query(PlanningChangeRow)
        .filter(PlanningChangeRow.batch_id == batch.id,
                PlanningChangeRow.project_line_id == line_592.id)
        .one()
    )
    assert pending_592.kind == "qty_down" and pending_592.decision is None

    # CS confirms the OTHER line only; line 592's change stays undecided.
    handovers_before = len(_handovers_for(world, row_592))
    response = _confirm(client, order.id, [_line_payload(line_other.id, buy_qty="10")],
                        batch_id=str(batch.id))
    assert response.status_code == 200, response.text
    db.commit()
    db.expire_all()

    row_592 = db.get(OrderInquiryRow, row_592.id)
    assert row_592.state != INQUIRY_CANCELLED, (
        f"BUG A: line 592's row was cancelled ({row_592.note!r}) while its AutoCount change "
        "is still undecided and the book still needs 22"
    )
    assert Decimal(str(row_592.qty)) == Decimal("25"), "the standing qty holds until CS decides"
    new_lines = [h["line"]["remark"] for h in _handovers_for(world, row_592)[handovers_before:]]
    assert "CANCEL ALL" not in new_lines, new_lines

    # CS now decides line 592: the same row is restated by the delta, never cancelled whole.
    response = _confirm(client, order.id, [_line_payload(line_592.id, buy_qty="22")],
                        batch_id=str(batch.id))
    assert response.status_code == 200, response.text
    db.commit()
    db.expire_all()
    live = [
        r for r in db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_592.id, OrderInquiryRow.verb == IV_ORDER)
        .all()
        if r.state != INQUIRY_CANCELLED
    ]
    assert [str(r.id) for r in live] == [str(row_592.id)], "one row, the same row"
    assert Decimal(str(live[0].qty)) == Decimal("22")
    remarks = [h["line"]["remark"] for h in _handovers_for(world, row_592)]
    assert "CANCEL ALL" not in remarks, remarks
