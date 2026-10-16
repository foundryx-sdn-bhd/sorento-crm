"""OI-LINE-SPLIT slice 3: Confirm honours the splits staff linked (UAC E1, E2).

SO402757 (prod, 16 Oct 2026): line 2 was 51 @ 15 Jul and line 18 82 @ 1 Nov. AutoCount cut
line 2 to 2 and raised line 18 to 121; CS planned 49 of line 2's qty onto line 18. Confirmed
together, purchasing must not be told to cancel the 49 on line 2 and order it again on line
18: the 49's PO link and acknowledgement move to line 18's row. Only what is left is told
(the assertions here are about what is NOT told; how line 18's remaining 10 is netted is the
engine's own business).

Runs on Postgres through the board's own Confirm; every row is seeded here.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.planning_change import PlanningChangeRow  # noqa: F401
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_CHANGED,
    INQUIRY_CANCELLED,
    IV_CANCEL_BALANCE,
    IV_ORDER,
    OrderInquiryRow,
)
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.scm.outstanding_diff import QTY_CHANGED
from tests.scm.test_line_splits_api import _put, _sp
from tests.test_oi_cancel_bugs_so422492 import (  # noqa: F401 - fixtures reused
    _TOLD,
    _handovers_for,
    _spy_handover,
)
from tests.test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    _build,
    _change,
    _confirm,
    _link,
    _order_row,
    _po_line,
    _supplier,
    api,
    world,
)
from tests.test_planning_changes import (
    _core_line,
    _core_so,
    _line_payload,
    _project_line,
    _project_so,
)

D_2 = date(2026, 7, 15)
D_18 = date(2026, 11, 1)


class _S:
    pass


def _so402757(api, *, with_splits: bool):
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_2 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="51",
                        required_date=D_2)
    core_18 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="82",
                         required_date=D_18)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line_2 = _project_line(db, order, line_no=2, product=world.product, core_line=core_2)
    line_18 = _project_line(db, order, line_no=18, product=world.product, core_line=core_18)
    db.commit()
    r = _confirm(client, order.id, [_line_payload(line_2.id, buy_qty="51"),
                                    _line_payload(line_18.id, buy_qty="82")])
    assert r.status_code == 200, r.text
    row_2, row_18 = _order_row(world, line_2), _order_row(world, line_18)
    supplier = _supplier(world)
    _po, po_line = _po_line(world, supplier, qty=51, expected_date=date(2026, 8, 20))
    _link(world, row_2, po_line, qty=51, document="202607-S0402")
    for row in (row_2, row_18):
        row.ack_state = ACK_ACKNOWLEDGED
        row.acknowledged_by = world.actor
        row.acknowledged_at = datetime.utcnow()
    db.commit()

    # AutoCount: line 2 51 -> 2, line 18 82 -> 121, dates unchanged
    core_2.qty_ordered, line_2.qty = Decimal("2"), Decimal("2")
    core_18.qty_ordered, line_18.qty = Decimal("121"), Decimal("121")
    db.commit()
    batch = _build(world, [
        _change(QTY_CHANGED, core_2, so_number=core_so.so_number,
                old_date=D_2, new_date=D_2, old_qty="51", new_qty="2"),
        _change(QTY_CHANGED, core_18, so_number=core_so.so_number,
                old_date=D_18, new_date=D_18, old_qty="82", new_qty="121"),
    ], core_so, [str(core_2.id), str(core_18.id)])
    assert batch is not None, "premise: the pending change batch exists"

    s = _S()
    s.client, s.world, s.db, s.order, s.batch = client, world, db, order, batch
    s.line_2, s.line_18, s.row_2, s.row_18 = line_2, line_18, row_2, row_18
    s.po_line = po_line
    if with_splits:
        r = _put(s, [{"line_id": str(line_2.id), "splits": [
            _sp(2, D_2, on=str(line_2.id)),
            _sp(49, D_18, on=str(line_18.id)),
        ]}])
        assert r.status_code == 200, f"premise: the splits save ({r.status_code}) {r.text}"
    return s


def _confirm_both(s):
    before = len(_TOLD)
    r = _confirm(s.client, s.order.id, [
        _line_payload(s.line_2.id, buy_qty="2"),
        _line_payload(s.line_18.id, buy_qty="121"),
    ], batch_id=str(s.batch.id))
    assert r.status_code == 200, r.text
    s.db.commit()
    s.db.expire_all()
    return _TOLD[before:]


def _live(s, line, verb=None):
    q = s.db.query(OrderInquiryRow).filter(
        OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.state != INQUIRY_CANCELLED)
    if verb:
        q = q.filter(OrderInquiryRow.verb == verb)
    return q.all()


def _linked(s, row) -> Decimal:
    links = ProjectOrderInquiryService(s.db)._links_of(row.id)
    return sum((Decimal(str(l.qty)) for l in links if l.po_line_id == s.po_line.id), Decimal("0"))


def test_a_linked_split_is_not_cancelled_on_the_origin_nor_re_ordered_on_the_target(api):
    s = _so402757(api, with_splits=True)
    told = _confirm_both(s)

    assert _live(s, s.line_2, IV_CANCEL_BALANCE) == [], (
        "no CANCEL BALANCE for the 49 linked to line 18"
    )
    cancels_49 = [r for r in _live(s, s.line_18, IV_CANCEL_BALANCE) if Decimal(str(r.qty)) == 49]
    assert cancels_49 == []

    row_2 = s.db.get(OrderInquiryRow, s.row_2.id)
    assert Decimal(str(row_2.qty)) == Decimal("2")
    # the 49's PO link follows the quantity to line 18's row; line 2 keeps the 2
    assert _linked(s, row_2) == Decimal("2")
    live_18 = _live(s, s.line_18, IV_ORDER)
    assert len(live_18) == 1, "no second ORDER row for the 49 on line 18"
    assert _linked(s, live_18[0]) == Decimal("49")
    # Line 18's own qty also changed (82 -> 121 with 49 in = its own CANCEL 10), so the owner's
    # 17 Sep 2026 handshake ruling sends the restated acknowledged row back to To confirm.
    assert live_18[0].ack_state == ACK_CHANGED

    remarks = [(h.get("line_no"), h["line"]["remark"], h["line"]["qty"]) for h in told]
    delays_49 = [h for h in told if h.get("line_no") == 2 and h["line"]["qty"] == "49"]
    assert len(delays_49) == 1 and delays_49[0]["line"]["remark"].startswith("DELAY"), (
        "UAC D1: the split origin tells its 49 as a DELAY line", remarks)
    assert not [r for r in remarks if r[0] == 2 and "CANCEL" in r[1]], remarks
    assert not [r for r in remarks if r[0] == 18 and r[1].startswith("ORDER")], (
        "the 49 is not ordered again on line 18", remarks)


def test_without_stored_splits_confirm_tells_the_cancel_balance_as_today(api):
    s = _so402757(api, with_splits=False)
    told = _confirm_both(s)
    remarks = [(h.get("line_no"), h["line"]["remark"]) for h in told]
    assert (2, "CANCEL 49") in remarks, f"E2: today's result, CANCEL 49 on line 2: {remarks}"
    assert (18, "ORDER 39") in remarks, remarks
    row_2 = s.db.get(OrderInquiryRow, s.row_2.id)
    assert Decimal(str(row_2.qty)) == Decimal("2")
    assert _linked(s, row_2) == Decimal("2")


def test_a_move_alone_does_not_ask_purchasing_again(api):
    """SO417636 shape: line A 417 -> 14 with 403 linked to line B 192 -> 595. B's restated
    qty equals AutoCount, so nothing is left to tell and B's acknowledgement stands."""
    client, world = api
    db = world.db
    d_a, d_b = date(2026, 10, 20), date(2026, 11, 20)
    core_so = _core_so(db, world.company_id)
    core_a = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="417",
                        required_date=d_a)
    core_b = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="192",
                        required_date=d_b)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line_a = _project_line(db, order, line_no=20, product=world.product, core_line=core_a)
    line_b = _project_line(db, order, line_no=30, product=world.product, core_line=core_b)
    db.commit()
    r = _confirm(client, order.id, [_line_payload(line_a.id, buy_qty="417"),
                                    _line_payload(line_b.id, buy_qty="192")])
    assert r.status_code == 200, r.text
    row_a, row_b = _order_row(world, line_a), _order_row(world, line_b)
    _po, po_line = _po_line(world, _supplier(world), qty=417, expected_date=date(2026, 9, 30))
    _link(world, row_a, po_line, qty=417, document="202609-S0417")
    for row in (row_a, row_b):
        row.ack_state = ACK_ACKNOWLEDGED
        row.acknowledged_by = world.actor
        row.acknowledged_at = datetime.utcnow()
    db.commit()

    core_a.qty_ordered, line_a.qty = Decimal("14"), Decimal("14")
    core_b.qty_ordered, line_b.qty = Decimal("595"), Decimal("595")
    db.commit()
    batch = _build(world, [
        _change(QTY_CHANGED, core_a, so_number=core_so.so_number,
                old_date=d_a, new_date=d_a, old_qty="417", new_qty="14"),
        _change(QTY_CHANGED, core_b, so_number=core_so.so_number,
                old_date=d_b, new_date=d_b, old_qty="192", new_qty="595"),
    ], core_so, [str(core_a.id), str(core_b.id)])
    assert batch is not None, "premise: the pending change batch exists"
    s = _S()
    s.client, s.world, s.db, s.order, s.batch = client, world, db, order, batch
    s.line_2, s.line_18, s.row_2, s.row_18, s.po_line = line_a, line_b, row_a, row_b, po_line
    r = _put(s, [{"line_id": str(line_a.id), "splits": [
        _sp(14, d_a, on=str(line_a.id)),
        _sp(403, d_b, on=str(line_b.id)),
    ]}])
    assert r.status_code == 200, f"premise: the splits save ({r.status_code}) {r.text}"

    before = len(_TOLD)
    r = _confirm(client, order.id, [_line_payload(line_a.id, buy_qty="14"),
                                    _line_payload(line_b.id, buy_qty="595")],
                 batch_id=str(batch.id))
    assert r.status_code == 200, r.text
    db.commit()
    db.expire_all()
    remarks = [(h.get("line_no"), h["line"]["remark"]) for h in _TOLD[before:]]

    assert _live(s, line_a, IV_CANCEL_BALANCE) == []
    assert not [x for x in remarks if x[0] == 20 and "CANCEL" in x[1]], remarks
    assert not [x for x in remarks if x[0] == 30 and x[1].startswith("ORDER")], remarks
    live_b = _live(s, line_b, IV_ORDER)
    assert len(live_b) == 1
    assert _linked(s, live_b[0]) == Decimal("403"), "B holds A's moved links"
    assert live_b[0].ack_state == ACK_ACKNOWLEDGED, "nothing left to tell, no re-confirm"
