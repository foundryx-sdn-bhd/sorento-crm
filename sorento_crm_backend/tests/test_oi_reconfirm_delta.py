"""ORDERBACK-DELTA (owner, 13 Oct 2026): a Confirm sends only what it decided.

`documentation/plans/scm/PLAN-orderback-delta-13oct.md`. Owner case SO424764 rev 3: the
board showed Saved (2), and the ORDER BACK email listed 3. The third, CB2519SS-DIY 5, had
been confirmed in Revision 2 and was unchanged. `_raise_borrow_shortfalls` cancelled every
open borrow hole and raised it again on every Confirm, and each re-raise was emailed.

The rule (owner, verbatim): "saved = sent in order inquiry email". An order-back or Buy row
that an earlier Confirm already raised, and that this Confirm leaves unchanged, keeps its
row: it is not cancelled, not raised again and not emailed. A changed one keeps its row and
the email shows old -> new. A removed one is cancelled and emailed.

Postgres via the board's own Confirm fixtures (`test_planning_change_apply_on_board`). Every
row is seeded here, because CI's database has no data.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_CHANGED,
    DECISION_ACTIVE,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_RAISED,
    IV_CANCEL_BALANCE,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiryRow,
    SOSupplyDecision,
)
from app.services.project_order_inquiry_service import (
    _HANDOVER_PENDING_KEY,
    ProjectOrderInquiryService,
)

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    _confirm,
    _order_row,
    api,
    world,
)
from .test_planning_changes import _core_line, _core_so, _line_payload, _project_line, _project_so

D_144 = date(2026, 11, 2)
D_336 = date(2026, 11, 9)
D_DIY = date(2026, 11, 16)

#: Every handover line queued during the current test, captured as `_record_handover`
#: writes it (the `db.info` queue drains whenever a root transaction concludes).
_TOLD: list = []


@pytest.fixture(autouse=True)
def _spy_handover(monkeypatch):
    original = ProjectOrderInquiryService._record_handover

    def spy(self, row, **kwargs):
        before = len(self.db.info.get(_HANDOVER_PENDING_KEY, []))
        original(self, row, **kwargs)
        _TOLD.extend(self.db.info.get(_HANDOVER_PENDING_KEY, [])[before:])

    _TOLD.clear()
    monkeypatch.setattr(ProjectOrderInquiryService, "_record_handover", spy)
    yield
    _TOLD.clear()


def _told_about(row):
    return [item for item in _TOLD if item.get("row_id") == str(row.id)]


def _remarks(items):
    return [item["line"]["remark"] for item in items]


# --------------------------------------------------------------------------- borrow holes


def _three_lines(api):
    """SO424764's shape: three lines of one order, each confirmed as a Buy so the order
    has its order inquiry header (lines 144, 336 and the CB2519SS-DIY line)."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    cores = [
        _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10",
                   required_date=d)
        for d in (D_144, D_336, D_DIY)
    ]
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    lines = [
        _project_line(db, order, line_no=no, product=world.product, core_line=core)
        for no, core in zip((144, 336, 400), cores)
    ]
    db.commit()
    response = _confirm(client, order.id, [_line_payload(l.id, buy_qty="10") for l in lines])
    assert response.status_code == 200, response.text
    db.commit()
    return order, lines


def _hole(line, item_code, qty, location, required_date, ref="SO424764"):
    """One `borrow_shortfalls` entry, in `ProjectSupplyService._location_order_backs`'
    own shape."""
    return {
        "line": line,
        "item_code": item_code,
        "qty": Decimal(str(qty)),
        "stock_location": location,
        "required_date": required_date,
        "note": f"Order-back: {location} lent {qty} to {ref} line {line.line_no}",
    }


def _raise(world, order, inquiry_id, revision_no, entries):
    """One Confirm's borrow-hole pass, under a fresh revision id."""
    service = ProjectOrderInquiryService(world.db)
    decision = SimpleNamespace(id=_decision_id(world, order), revision_no=revision_no)
    created = service._raise_borrow_shortfalls(
        order, SimpleNamespace(id=inquiry_id), decision, entries,
        covered_line_ids={str(entry["line"].id) for entry in entries},
    )
    world.db.flush()
    return created, decision


def _decision_id(world, order):
    """The order's active revision. `_raise_borrow_shortfalls` only stamps it on the rows."""
    return (
        world.db.query(SOSupplyDecision.id)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == DECISION_ACTIVE,
        )
        .scalar()
    )


def _holes(world, inquiry_id, *, live=True):
    query = world.db.query(OrderInquiryRow).filter(
        OrderInquiryRow.order_inquiry_id == inquiry_id,
        OrderInquiryRow.verb == IV_ORDER_BACK,
    )
    if live:
        query = query.filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
    return query.order_by(OrderInquiryRow.created_at, OrderInquiryRow.id).all()


def test_owner_case_so424764_saved_two_sends_two_not_three(api):
    """Rev 2 confirmed CB2519SS-DIY 5. Rev 3 saves two new order-backs (line 144 borrow 2
    at BRW-BB, line 336 C-FH14 7) and leaves the DIY one unchanged. The email lists 2."""
    _client, world = api
    order, (line_144, line_336, line_diy) = _three_lines(api)
    inquiry_id = _order_row(world, line_144).order_inquiry_id

    _raise(world, order, inquiry_id, 2, [_hole(line_diy, "CB2519SS-DIY", 5, "BRW-AA", D_DIY)])
    (diy,) = _holes(world, inquiry_id)
    diy.ack_state = ACK_ACKNOWLEDGED
    diy.acknowledged_by = world.actor
    world.db.flush()
    _TOLD.clear()

    created, rev3 = _raise(world, order, inquiry_id, 3, [
        _hole(line_144, "CGB9032B", 2, "BRW-BB", D_144),
        _hole(line_336, "C-FH14", 7, "BRW-CC", D_336),
        _hole(line_diy, "CB2519SS-DIY", 5, "BRW-AA", D_DIY),
    ])

    world.db.expire_all()
    diy = world.db.get(OrderInquiryRow, diy.id)
    assert diy.state == INQUIRY_RAISED, f"the unchanged order-back was retired: {diy.note!r}"
    assert diy.ack_state == ACK_ACKNOWLEDGED, "purchasing's acknowledgement stands"
    assert str(diy.supply_decision_id) == str(rev3.id), "filed under the new revision"
    assert not _told_about(diy), _remarks(_told_about(diy))

    live = _holes(world, inquiry_id)
    assert sorted((r.item_code, r.qty) for r in live) == [
        ("C-FH14", Decimal("7")), ("CB2519SS-DIY", Decimal("5")), ("CGB9032B", Decimal("2")),
    ]
    all_rows = _holes(world, inquiry_id, live=False)
    assert len(all_rows) == 3, "no cancelled copy of the DIY order-back was left behind"
    assert sorted(item["item_code"] for item in _TOLD) == ["C-FH14", "CGB9032B"], _TOLD
    assert _remarks(_TOLD) == ["ORDER BACK", "ORDER BACK"]
    assert created == 2, "only the two new order-backs count as raised"


def test_a_changed_order_back_keeps_its_row_and_tells_old_to_new(api):
    _client, world = api
    order, (_line_144, _line_336, line_diy) = _three_lines(api)
    inquiry_id = _order_row(world, line_diy).order_inquiry_id

    _raise(world, order, inquiry_id, 2, [_hole(line_diy, "CB2519SS-DIY", 5, "BRW-AA", D_DIY)])
    (diy,) = _holes(world, inquiry_id)
    diy.ack_state = ACK_ACKNOWLEDGED
    world.db.flush()
    _TOLD.clear()

    created, _rev3 = _raise(world, order, inquiry_id, 3,
                            [_hole(line_diy, "CB2519SS-DIY", 8, "BRW-AA", D_DIY)])

    world.db.expire_all()
    live = _holes(world, inquiry_id)
    assert [str(r.id) for r in live] == [str(diy.id)], "the same row, not a new one"
    row = live[0]
    assert row.qty == Decimal("8")
    assert row.previous_qty == Decimal("5"), "the Was / Now table reads the old figure"
    assert row.ack_state == ACK_CHANGED, "a change to an acknowledged row goes back to them"
    assert row.note.startswith("Order-back: BRW-AA lent 8"), row.note
    told = _told_about(row)
    assert len(told) == 1, _remarks(told)
    assert told[0]["line"]["qty"] == "8"
    assert told[0]["line"]["was"] == {"qty": "5"}
    assert _holes(world, inquiry_id, live=False) == live, "nothing cancelled"
    assert created == 0, "nothing new was raised"


def test_a_removed_order_back_is_cancelled_and_told(api):
    _client, world = api
    order, (line_144, _line_336, line_diy) = _three_lines(api)
    inquiry_id = _order_row(world, line_diy).order_inquiry_id

    _raise(world, order, inquiry_id, 2, [
        _hole(line_diy, "CB2519SS-DIY", 5, "BRW-AA", D_DIY),
        _hole(line_144, "CGB9032B", 2, "BRW-BB", D_144),
    ])
    diy, kept = sorted(_holes(world, inquiry_id), key=lambda r: r.item_code != "CB2519SS-DIY")
    _TOLD.clear()

    _raise(world, order, inquiry_id, 3, [_hole(line_144, "CGB9032B", 2, "BRW-BB", D_144)])

    world.db.expire_all()
    assert world.db.get(OrderInquiryRow, diy.id).state == INQUIRY_CANCELLED
    assert _remarks(_told_about(diy)) == ["CANCEL ALL"]
    assert world.db.get(OrderInquiryRow, kept.id).state == INQUIRY_RAISED
    assert not _told_about(kept)


def test_a_moved_location_cancels_the_old_pile_and_raises_the_new(api):
    _client, world = api
    order, (_l144, _l336, line_diy) = _three_lines(api)
    inquiry_id = _order_row(world, line_diy).order_inquiry_id

    _raise(world, order, inquiry_id, 2, [_hole(line_diy, "CB2519SS-DIY", 5, "BRW-AA", D_DIY)])
    (old,) = _holes(world, inquiry_id)
    _TOLD.clear()

    _raise(world, order, inquiry_id, 3, [_hole(line_diy, "CB2519SS-DIY", 5, "BRW-ZZ", D_DIY)])

    world.db.expire_all()
    assert world.db.get(OrderInquiryRow, old.id).state == INQUIRY_CANCELLED
    (new,) = _holes(world, inquiry_id)
    assert new.stock_location == "BRW-ZZ"
    assert _remarks(_told_about(old)) == ["CANCEL ALL"]
    assert _remarks(_told_about(new)) == ["ORDER BACK"], [
        (i["row_id"], i["line"]["remark"]) for i in _TOLD
    ]


def test_a_refused_order_back_restated_is_raised_fresh(api):
    """AC-H6 still holds: purchasing refused it, so restating it is a new instruction."""
    from app.models.project_so import ACK_REJECTED

    _client, world = api
    order, (_l144, _l336, line_diy) = _three_lines(api)
    inquiry_id = _order_row(world, line_diy).order_inquiry_id

    _raise(world, order, inquiry_id, 2, [_hole(line_diy, "CB2519SS-DIY", 5, "BRW-AA", D_DIY)])
    (refused,) = _holes(world, inquiry_id)
    refused.ack_state = ACK_REJECTED
    world.db.flush()

    _raise(world, order, inquiry_id, 3, [_hole(line_diy, "CB2519SS-DIY", 5, "BRW-AA", D_DIY)])

    world.db.expire_all()
    assert world.db.get(OrderInquiryRow, refused.id).state == INQUIRY_CANCELLED
    (fresh,) = _holes(world, inquiry_id)
    assert fresh.id != refused.id


# --------------------------------------------------------------------------- carried Buy lines


def _two_lines(api, *, qty_a="25", qty_b="10"):
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_a = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty_a,
                        required_date=D_144)
    core_b = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty_b,
                        required_date=D_336)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line_a = _project_line(db, order, line_no=10, product=world.product, core_line=core_a)
    line_b = _project_line(db, order, line_no=20, product=world.product, core_line=core_b)
    db.commit()
    response = _confirm(client, order.id, [
        _line_payload(line_a.id, buy_qty=qty_a),
        _line_payload(line_b.id, buy_qty=qty_b),
    ])
    assert response.status_code == 200, response.text
    db.commit()
    return order, line_a, line_b


def _rows_on(world, line):
    return (
        world.db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id)
        .order_by(OrderInquiryRow.created_at, OrderInquiryRow.id)
        .all()
    )


def test_a_carried_buy_row_is_kept_not_cancelled_and_re_raised(api):
    """Confirming line B carries line A. Line A's raised ORDER row is the same row after,
    filed under the new revision, with nothing cancelled and nothing told."""
    client, world = api
    order, line_a, line_b = _two_lines(api)
    row_a = _order_row(world, line_a)
    row_a.ack_state = ACK_ACKNOWLEDGED
    world.db.commit()
    _TOLD.clear()

    response = _confirm(client, order.id, [_line_payload(line_b.id, buy_qty="10")])
    assert response.status_code == 200, response.text
    world.db.commit()
    world.db.expire_all()

    rows = _rows_on(world, line_a)
    assert [str(r.id) for r in rows] == [str(row_a.id)], [
        (r.state, r.qty, r.note) for r in rows
    ]
    kept = rows[0]
    assert kept.state == INQUIRY_RAISED
    assert kept.qty == Decimal("25")
    assert kept.ack_state == ACK_ACKNOWLEDGED
    assert str(kept.supply_decision_id) == str(_decision_id(world, order)), (
        "re-filed under the new revision, so `confirmed_unplaced_buy_rows` still finds it"
    )
    assert not _told_about(kept)
    told_on_a = {str(r.id) for r in rows}
    assert not [item for item in _TOLD if item.get("row_id") in told_on_a], _remarks(_TOLD)


def test_a_carried_cancel_balance_is_not_re_raised_or_re_told(api):
    """Line A: purchasing actioned 25, CS cut the Buy to 20, so a CANCEL BALANCE 5 was
    raised and told. Confirming line B afterwards carries line A: the exception stays the
    same row and is not told again."""
    client, world = api
    order, line_a, line_b = _two_lines(api)
    row_a = _order_row(world, line_a)
    row_a.state = INQUIRY_ACTIONED
    # AutoCount cut line A 25 -> 20 after purchasing actioned the 25.
    core_a = world.db.get(type(line_a), line_a.id).core_sales_order_line_id
    from app.models.order import SalesOrderLine

    world.db.get(SalesOrderLine, core_a).qty_ordered = Decimal("20")
    line_a.qty = Decimal("20")
    world.db.commit()

    response = _confirm(client, order.id, [_line_payload(line_a.id, buy_qty="20")])
    assert response.status_code == 200, response.text
    world.db.commit()
    (exception,) = [
        r for r in _rows_on(world, line_a)
        if r.verb == IV_CANCEL_BALANCE and r.state == INQUIRY_RAISED
    ]
    assert exception.qty == Decimal("5"), "premise"
    _TOLD.clear()

    response = _confirm(client, order.id, [_line_payload(line_b.id, buy_qty="10")])
    assert response.status_code == 200, response.text
    world.db.commit()
    world.db.expire_all()

    live = [
        r for r in _rows_on(world, line_a)
        if r.verb == IV_CANCEL_BALANCE and r.state != INQUIRY_CANCELLED
    ]
    assert [str(r.id) for r in live] == [str(exception.id)], [
        (r.state, r.qty, r.note) for r in _rows_on(world, line_a)
    ]
    assert not [
        r for r in _rows_on(world, line_a)
        if r.verb == IV_CANCEL_BALANCE and r.state == INQUIRY_CANCELLED
    ], "no cancelled copy left behind"
    assert not [
        item for item in _TOLD
        if item.get("row_id") in {str(r.id) for r in _rows_on(world, line_a)}
    ], _remarks(_TOLD)
    assert str(live[0].supply_decision_id) == str(_decision_id(world, order))


def test_a_carried_line_whose_row_drifted_is_still_restated(api):
    """Control: a carried line's row that no longer matches what the revision needs (a
    partial place moved what is outstanding) still goes through the ordinary carry path,
    and the email shows the move."""
    client, world = api
    order, line_a, line_b = _two_lines(api)
    row_a = _order_row(world, line_a)
    # Purchasing actioned 10 of it through a path that wrote a second row.
    world.db.add(OrderInquiryRow(
        company_id=row_a.company_id, order_inquiry_id=row_a.order_inquiry_id,
        so_line_id=line_a.id, item_code=row_a.item_code, qty=Decimal("10"),
        delivery_date=row_a.delivery_date, stock_location=row_a.stock_location,
        verb=IV_ORDER, supply_decision_id=row_a.supply_decision_id, state=INQUIRY_ACTIONED,
    ))
    world.db.commit()
    _TOLD.clear()

    response = _confirm(client, order.id, [_line_payload(line_b.id, buy_qty="10")])
    assert response.status_code == 200, response.text
    world.db.commit()
    world.db.expire_all()

    live_raised = [
        r for r in _rows_on(world, line_a)
        if r.verb == IV_ORDER and r.state == INQUIRY_RAISED
    ]
    assert len(live_raised) == 1 and live_raised[0].qty == Decimal("15"), [
        (r.state, r.qty) for r in _rows_on(world, line_a)
    ]
    told = [item for item in _TOLD if item.get("line_no") == line_a.line_no]
    assert [(t["line"]["remark"], t["line"]["was"]) for t in told] == [
        ("CANCEL 10", {"qty": "25"})
    ], _remarks(_TOLD)


# --------------------------------------------------------------------------- nothing to act on


def test_a_confirm_with_nothing_for_purchasing_to_do_sends_no_email(api, monkeypatch):
    """Owner, 13 Oct (Q3 = a): purchasing hears only when they have an action to take.
    Re-approving line A unchanged (a saved line) while line B rides along carried leaves
    them nothing to do, so the Confirm queues no line and dispatches no handover email."""
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    register_order_inquiry_post_commit_dispatch()
    calls: list = []

    def _fake_dispatch(self, trigger_type, *, context, source_kind, source_id):
        calls.append(trigger_type)
        return {"trigger_type": trigger_type, "fired": 0, "results": []}

    monkeypatch.setattr(
        "app.services.automation_service.AutomationService.dispatch_event", _fake_dispatch
    )
    client, world = api
    order, line_a, line_b = _two_lines(api)
    assert "order_inquiry_handover" in calls, "control: the first Confirm is told"
    row_a, row_b = _order_row(world, line_a), _order_row(world, line_b)
    calls.clear()
    _TOLD.clear()

    response = _confirm(client, order.id, [_line_payload(line_a.id, buy_qty="25")])
    assert response.status_code == 200, response.text
    world.db.commit()
    world.db.expire_all()

    assert _TOLD == [], _remarks(_TOLD)
    assert "order_inquiry_handover" not in calls, calls
    for row in (row_a, row_b):
        kept = world.db.get(OrderInquiryRow, row.id)
        assert kept.state == INQUIRY_RAISED, kept.note
