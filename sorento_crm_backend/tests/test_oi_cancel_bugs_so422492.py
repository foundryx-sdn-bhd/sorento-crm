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
from app.services.project_order_inquiry_service import (
    _HANDOVER_PENDING_KEY,
    ProjectOrderInquiryService,
)
from app.services.scm.outstanding_diff import ADDED, DATE_MOVED, QTY_CHANGED, Change, Line

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
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


def test_d_a_buy_only_lines_cancel_balance_names_the_line_location(api):
    """SO422492 D: after "Save all suggested" + Confirm the email listed CANCEL BALANCE
    SRTWT2200 118 (and four more) with a BLANK location. The reaction read its location
    only off the held reserve, and a Buy-only line holds none. It is the line's own
    location."""
    from app.services.planning_change_service import _oi_demand_rows

    client, world = api
    db = world.db
    core_so, order, (core_592, _core_other), (line_592, _line_other) = _two_bought_lines(api)
    db.refresh(line_592)
    assert line_592.stock_location == world.own_wh.warehouse_code, "premise: confirm stamped it"
    change = PlanningChangeRow(
        project_sales_order_id=str(order.id),
        project_line_id=str(line_592.id),
        core_line_id=str(core_592.id),
        line_no=592,
        item_code=world.product.product_code,
        kind="qty_down",
        from_json={"qty": "136", "required_date": D_592.isoformat()},
        to_json={"qty": "18", "required_date": D_592.isoformat()},
        held_json={"revision_no": 1, "reserve": [], "borrow": [], "buy": "136"},
    )
    out, counts = _oi_demand_rows(db, [change], core_so.so_number)
    assert counts == {"CANCEL_BALANCE": 1}
    assert out[0]["qty"] == Decimal("118")
    assert out[0]["stock_location"] == world.own_wh.warehouse_code, out[0]


def test_e_switching_order_back_to_order_tells_purchasing_the_order_back_is_cancelled(api):
    """SO422492 E: line 160 was re-confirmed from ORDER BACK (from BRW-BB) to a plain ORDER.
    The ORDER BACK row was cancelled with no email line, so purchasing was never told to stop
    the order back. Every cancelled raise purchasing was told about appears in the email."""
    from app.models.project_so import IV_ORDER_BACK

    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_160 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="12",
                          required_date=D_592)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line_160 = _project_line(db, order, line_no=160, product=world.product, core_line=core_160)
    db.commit()
    order_back = {**_line_payload(line_160.id, buy_qty="12"), "order_back": True}
    response = _confirm(client, order.id, [order_back])
    assert response.status_code == 200, response.text
    db.commit()
    back_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_160.id,
                OrderInquiryRow.verb == IV_ORDER_BACK,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .one()
    )

    response = _confirm(client, order.id, [_line_payload(line_160.id, buy_qty="12")])
    assert response.status_code == 200, response.text
    db.commit()
    db.expire_all()
    back_row = db.get(OrderInquiryRow, back_row.id)
    assert back_row.state == INQUIRY_CANCELLED, "premise: the switch cancels the order back"
    assert _order_row(world, line_160) is not None
    remarks = [h["line"]["remark"] for h in _handovers_for(world, back_row)]
    assert "CANCEL ALL" in remarks, (
        f"E: the cancelled ORDER BACK row must reach the email, got {remarks}"
    )


def test_c_a_pure_split_moves_the_instruction_instead_of_cancel_and_reorder(api):
    """SO422492 C: AutoCount split B2155-NL-BLUE 139 into 22 on the same line plus new lines
    totalling 117 (same product, same location, same total). The email printed CANCEL 117 on
    the old line and ORDER on the new ones. A pure split moves the quantity, the PO links
    covering it and purchasing's acknowledgement to the new lines' rows; nothing is told as
    a cancel or a re-order."""
    from app.models.project_so import IV_CANCEL_BALANCE

    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_x = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="139",
                        required_date=D_592)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line_x = _project_line(db, order, line_no=100, product=world.product, core_line=core_x)
    db.commit()
    response = _confirm(client, order.id, [_line_payload(line_x.id, buy_qty="139")])
    assert response.status_code == 200, response.text
    row_x = _order_row(world, line_x)
    supplier = _supplier(world)
    _, po_line = _po_line(world, supplier, qty=139, expected_date=date(2026, 10, 20))
    _link(world, row_x, po_line, qty=139, document="202609-S0099")
    db.commit()

    # AutoCount: line 100 cut to 22, three new lines of the same product take the 117.
    core_x.qty_ordered = Decimal("22")
    line_x.qty = Decimal("22")
    new_lines = []
    for no, qty in ((101, "40"), (102, "40"), (103, "37")):
        core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty,
                          required_date=D_592)
        new_lines.append(
            (core, _project_line(db, order, line_no=no, product=world.product, core_line=core),
             qty)
        )
    db.commit()
    loc = world.own_wh.warehouse_code
    changes = [
        _change(QTY_CHANGED, core_x, so_number=core_so.so_number,
                old_date=D_592, new_date=D_592, old_qty="139", new_qty="22"),
    ] + [
        Change(ADDED, core_so.so_number, world.product.product_code, loc, before=None,
               after=Line(doc_number=core_so.so_number, item_code=world.product.product_code,
                          location=loc, qty=float(qty), required_date=D_592, row_ref="1"))
        for _core, _line, qty in new_lines
    ]
    batch = _build(world, changes, core_so,
                   [str(core_x.id)] + [str(core.id) for core, _line, _qty in new_lines])
    assert batch is not None

    before = len(world.db.info.get(_HANDOVER_PENDING_KEY, []))
    response = _confirm(
        client, order.id,
        [_line_payload(line_x.id, buy_qty="22")]
        + [_line_payload(line.id, buy_qty=qty) for _core, line, qty in new_lines],
        batch_id=str(batch.id),
    )
    assert response.status_code == 200, response.text
    db.commit()
    db.expire_all()

    service = ProjectOrderInquiryService(db)
    row_x = db.get(OrderInquiryRow, row_x.id)
    assert row_x.state != INQUIRY_CANCELLED
    assert Decimal(str(row_x.qty)) == Decimal("22")
    assert sum(Decimal(str(link.qty)) for link in service._links_of(row_x.id)) == Decimal("22")
    moved = 0
    for _core, line, qty in new_lines:
        live = [
            r for r in db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id)
            if r.state != INQUIRY_CANCELLED
        ]
        assert len(live) == 1 and live[0].verb == IV_ORDER, [(r.verb, r.state) for r in live]
        assert Decimal(str(live[0].qty)) == Decimal(qty)
        linked = sum(Decimal(str(link.qty)) for link in service._links_of(live[0].id))
        assert linked == Decimal(qty), "the PO quantity follows the split"
        moved += linked
    assert moved == Decimal("117")
    assert not db.query(OrderInquiryRow).filter(
        OrderInquiryRow.order_inquiry_id == row_x.order_inquiry_id,
        OrderInquiryRow.verb == IV_CANCEL_BALANCE,
        OrderInquiryRow.state != INQUIRY_CANCELLED,
    ).all()
    queued = world.db.info.get(_HANDOVER_PENDING_KEY, [])[before:]
    remarks = [
        (item["line_no"], item["line"]["remark"]) for item in queued
        if item.get("pso_id") == str(order.id)
    ]
    assert not [r for r in remarks if "CANCEL" in r[1] or r[1].startswith("ORDER")], remarks


# --------------------------------------------------------------------------- #
# Review round 1 (reviewer findings 1-8)
# --------------------------------------------------------------------------- #


def _cut_592_and_delay_other(world, core_so, core_592, core_other, line_592, line_other):
    db = world.db
    core_592.qty_ordered = Decimal("22")
    line_592.qty = Decimal("22")
    core_other.required_date = D_OTHER_NEW
    line_other.delivery_date = D_OTHER_NEW
    db.commit()
    return _build(world, [
        _change(QTY_CHANGED, core_592, so_number=core_so.so_number,
                old_date=D_592, new_date=D_592, old_qty="25", new_qty="22"),
        _change(DATE_MOVED, core_other, so_number=core_so.so_number,
                old_date=D_OTHER, new_date=D_OTHER_NEW, old_qty="10", new_qty="10"),
    ], core_so, [str(core_592.id), str(core_other.id)])


def test_review_1_an_order_back_line_waiting_on_cs_keeps_its_order_back(api):
    """Finding 1: line 592 bought as ORDER BACK, cut 25 -> 22 and left undecided. Confirming
    another line must not cancel the order back (the second writer, `_raise_borrow_
    shortfalls`, now also prints what it cancels)."""
    from app.models.project_so import IV_ORDER_BACK

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
        {**_line_payload(line_592.id, buy_qty="25"), "order_back": True},
        _line_payload(line_other.id, buy_qty="10"),
    ])
    assert response.status_code == 200, response.text
    back_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_592.id,
                OrderInquiryRow.verb == IV_ORDER_BACK,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .one()
    )
    batch = _cut_592_and_delay_other(world, core_so, core_592, core_other, line_592, line_other)
    response = _confirm(client, order.id, [_line_payload(line_other.id, buy_qty="10")],
                        batch_id=str(batch.id))
    assert response.status_code == 200, response.text
    db.commit()
    db.expire_all()
    back_row = db.get(OrderInquiryRow, back_row.id)
    assert back_row.state != INQUIRY_CANCELLED, back_row.note
    assert "CANCEL ALL" not in [h["line"]["remark"] for h in _handovers_for(world, back_row)]


def test_review_3_a_line_cs_withdraws_retires_its_row_despite_an_undecided_change(api):
    """Finding 3: CS's own reject (a withdrawal with a reason) still retires the row of a
    line whose AutoCount change is undecided; A only holds rows nobody withdrew."""
    from app.schemas.project_supply import ConfirmSupplyBody
    from app.services.project_supply_service import ProjectSupplyService

    client, world = api
    db = world.db
    core_so, order, (core_592, core_other), (line_592, line_other) = _two_bought_lines(api)
    row_592 = _order_row(world, line_592)
    _cut_592_and_delay_other(world, core_so, core_592, core_other, line_592, line_other)

    ProjectSupplyService(db).confirm(
        order,
        ConfirmSupplyBody(lines=[_line_payload(str(line_other.id), buy_qty="10")]),
        actor_user_id=world.actor,
        uncover_line_ids=[str(line_592.id)],
        uncover_reason_by_line={str(line_592.id): "Customer moved to another supplier"},
    )
    db.commit()
    db.expire_all()
    row_592 = db.get(OrderInquiryRow, row_592.id)
    assert row_592.state == INQUIRY_CANCELLED
    assert "Customer moved to another supplier" in (row_592.note or "")


def test_review_8_an_overtaken_undecided_row_holds_nothing_back(api):
    """Finding 8: a pending row held against an older revision can never be decided
    (`row_is_live`), so it does not keep the line's raised row alive."""
    client, world = api
    db = world.db
    core_so, order, (core_592, core_other), (line_592, line_other) = _two_bought_lines(api)
    row_592 = _order_row(world, line_592)
    batch = _cut_592_and_delay_other(world, core_so, core_592, core_other, line_592, line_other)
    pending_592 = (
        db.query(PlanningChangeRow)
        .filter(PlanningChangeRow.batch_id == batch.id,
                PlanningChangeRow.project_line_id == line_592.id)
        .one()
    )
    pending_592.held_json = {**(pending_592.held_json or {}), "revision_no": 999}
    db.commit()
    service = ProjectOrderInquiryService(db)
    assert service._lines_with_undecided_planning_change([str(line_592.id)]) == set()
    pending_592.held_json = {**pending_592.held_json, "revision_no": None}
    db.commit()
    assert service._lines_with_undecided_planning_change([str(line_592.id)]) == {
        str(line_592.id)
    }
    assert row_592.state != INQUIRY_CANCELLED


def _split_shape(api):
    """Line 100 bought 139 (one raised ORDER row) and three fresh lines with no row yet."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_x = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="139",
                        required_date=D_592)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line_x = _project_line(db, order, line_no=100, product=world.product, core_line=core_x)
    fresh = []
    for no in (101, 102, 103):
        core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="40",
                          required_date=D_592)
        fresh.append(_project_line(db, order, line_no=no, product=world.product, core_line=core))
    db.commit()
    response = _confirm(client, order.id, [_line_payload(line_x.id, buy_qty="139")])
    assert response.status_code == 200, response.text
    row_x = _order_row(world, line_x)

    def entry(line, qty, when=None):
        return {
            "line": line, "line_no": line.line_no, "item_code": row_x.item_code,
            "buy_qty": Decimal(qty), "required_date": when or row_x.delivery_date,
            "stock_location": row_x.stock_location, "origin": "overseas",
        }

    from types import SimpleNamespace

    inquiry = SimpleNamespace(id=row_x.order_inquiry_id)
    decision = SimpleNamespace(id=row_x.supply_decision_id, revision_no=2)
    return world, row_x, line_x, fresh, entry, inquiry, decision


def test_review_2_5_6_a_split_needs_same_date_a_kept_qty_and_no_reserve_link(api, monkeypatch):
    """Findings 2, 5, 6: `_apply_pure_splits` declines a split that also moves a date, one
    that gives the source line back whole (a live ORDER 0 would be left behind), and one
    whose source row holds a CS reserve link; the plain shape is still a split."""
    from types import SimpleNamespace

    world, row_x, line_x, fresh, entry, inquiry, decision = _split_shape(api)
    service = ProjectOrderInquiryService(world.db)
    other_day = date(2026, 12, 24)

    moved_date = [entry(line_x, "22")] + [
        entry(line, qty, other_day) for line, qty in zip(fresh, ("40", "40", "37"))
    ]
    assert service._apply_pure_splits(inquiry, decision, moved_date) == set(), "finding 2"

    given_back_whole = [entry(line_x, "0")] + [
        entry(line, qty) for line, qty in zip(fresh, ("50", "50", "39"))
    ]
    assert service._apply_pure_splits(inquiry, decision, given_back_whole) == set(), "finding 5"

    plain = [entry(line_x, "22")] + [entry(line, qty) for line, qty in zip(fresh, ("40", "40", "37"))]
    real_links_of = service._links_of
    monkeypatch.setattr(
        service, "_links_of",
        lambda row_id: [SimpleNamespace(reserve_request_row_id="rr", qty=Decimal("139"))]
        if str(row_id) == str(row_x.id) else real_links_of(row_id),
    )
    assert service._apply_pure_splits(inquiry, decision, plain) == set(), "finding 6"
    monkeypatch.setattr(service, "_links_of", real_links_of)

    handled = service._apply_pure_splits(inquiry, decision, plain)
    assert handled == {str(line_x.id)} | {str(line.id) for line in fresh}, "control"
    assert Decimal(str(row_x.qty)) == Decimal("22")
