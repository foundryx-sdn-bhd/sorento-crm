"""OI-CLOSED-LINE: a CS decision applies on a closed SO line (Phase 2 red tests, AC-1 to AC-10).

Contract: `documentation/plans/scm/oi-closed-line-acceptance-criteria.md`; owner ruling
11 Oct 2026: AutoCount's delivered qty and the plan / OI are decoupled, so a line closing
(fully delivered) must not decide whether a confirmed planning decision applies.

AC-4 (the rest of the order still applies) lives in
`tests/scm/test_board_received_stock_s1_apply.py::test_ac_s1_3_*`, which carries the two-line
order; AC-5 sits beside it. Postgres only, every FK seeded here.

Handover is observed by capturing `AutomationService.dispatch_event` after the root commit
(the queue in `session.info["oi_handover_pending"]` is drained by that commit).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.planning_change import (
    PlanningChangeRow,
    PLANNING_CHANGE_STATE_APPLIED,
)
from app.models.project_so import (
    INQUIRY_CANCELLED,
    INQUIRY_RAISED,
    IV_ORDER,
    OrderInquiryRow,
    SOLineAllocation,
)
from app.services import planning_change_service

from tests.test_order_inquiry_handover_automation import (
    _captured_dispatches,
    _handover_calls,
    _register,
)
from tests.test_planning_changes import (  # noqa: F401  (api is the fixture)
    BASE,
    _core_line,
    _core_so,
    _held_line_advanced_replan_row,
    _line_payload,
    _project_line,
    _project_so,
    _stock,
    _warehouse,
    api,
)
from tests.test_ingest_documents import env  # noqa: F401  (fixture)

IN_WINDOW = date.today() + timedelta(days=30)


def _close_core_line(db, core_line) -> None:
    core_line.qty_delivered = core_line.qty_ordered
    core_line.line_status = "closed"
    db.commit()


def _amend_to(client, batch, row, composition) -> None:
    response = client.put(
        f"{BASE}/planning-changes/{batch.id}/rows/{row['id']}",
        json={"decision": "amend", "composition": composition},
    )
    assert response.status_code == 200, response.text


def _raised_order_row(db, line):
    db.expire_all()
    return (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )


def _stock_dry(stock) -> None:
    """Every unit physically there but claimed by someone else: 0 free for this line."""
    stock.quantity_reserved = stock.quantity_on_hand


def _assert_applied_and_order_row_cancelled(world, batch, line, calls):
    db = world.db
    db.expire_all()
    pcr = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id).one()
    assert pcr.applied_state == PLANNING_CHANGE_STATE_APPLIED, (
        pcr.applied_state, pcr.applied_reason,
    )
    assert "the sales-order line is closed" not in (pcr.applied_reason or "")
    order_row = _raised_order_row(db, line)
    assert order_row.state == INQUIRY_CANCELLED, order_row.state
    handover = _handover_calls(calls)
    assert len(handover) == 1, [c["trigger_type"] for c in calls]
    lines = handover[0]["context"]["handover"]["lines"]
    assert [entry["item_code"] for entry in lines] == [order_row.item_code]
    assert lines[0]["qty"] == "0"  # kind cancelled, the line the email prints as CANCEL


# ------------------------------------------------------------------- AC-1 / AC-2


def test_ac_1_2_confirm_on_a_closed_line_applies_and_cancels_the_raised_order_row(
    api, monkeypatch
):
    """AC-1 + AC-2: line bought 72 (raised ORDER row), CS amends to Reserve 72 at the pool,
    buy 0; AutoCount then delivers and closes the line and the pool shows 0 free; Apply
    still applies (not superseded "closed"), cancels the ORDER row and queues it."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    stock = _stock(world.db, world.product, world.pool_wh, on_hand=100)
    batch, order, line, row = _held_line_advanced_replan_row(client, world)
    _amend_to(client, batch, row, _line_payload(
        line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "72"}],
        amend_reason="Use BRW, the pool has it.",
    ))
    core_line = world.db.get(type(_core_line_of(world, line)), line.core_sales_order_line_id)
    _close_core_line(world.db, core_line)
    _stock_dry(stock)
    world.db.commit()
    calls.clear()

    planning_change_service.apply(world.db, str(batch.id), world.actor)
    world.db.commit()

    _assert_applied_and_order_row_cancelled(world, batch, line, calls)


def _core_line_of(world, line):
    from app.models.order import SalesOrderLine

    return world.db.get(SalesOrderLine, line.core_sales_order_line_id)


# ------------------------------------------------------------------------- AC-3


def test_ac_3_borrow_on_a_closed_line_applies_and_cancels_the_raised_order_row(
    api, monkeypatch
):
    """AC-3: same as AC-2 with a Borrow from another location (buy 0)."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    donor_wh = _warehouse(world.db, f"ZZT-DON-{line_tag()}", segment="dealer")
    stock = _stock(world.db, world.product, donor_wh, on_hand=100)
    batch, order, line, row = _held_line_advanced_replan_row(client, world)
    _amend_to(client, batch, row, _line_payload(
        line.id,
        borrow=[{
            "source": "other_location", "warehouse_id": donor_wh.id, "qty": "72",
            "reason": "Borrow the other location, owed back.",
        }],
        amend_reason="Borrow instead of buying.",
    ))
    _close_core_line(world.db, _core_line_of(world, line))
    _stock_dry(stock)
    world.db.commit()
    calls.clear()

    planning_change_service.apply(world.db, str(batch.id), world.actor)
    world.db.commit()

    world.db.expire_all()
    pcr = world.db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id).one()
    assert pcr.applied_state == PLANNING_CHANGE_STATE_APPLIED, (
        pcr.applied_state, pcr.applied_reason,
    )
    assert _raised_order_row(world.db, line).state == INQUIRY_CANCELLED
    assert any(
        entry["item_code"] == world.product.product_code
        for call in _handover_calls(calls)
        for entry in call["context"]["handover"]["lines"]
    )


def line_tag() -> str:
    import uuid

    return uuid.uuid4().hex[:4]


# -------------------------------------------------------------- AC-6 .. AC-9


def _confirm_world(api, *, ordered, delivered, on_hand, reserved):
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=on_hand, reserved=reserved)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered=ordered,
        qty_delivered=delivered, required_date=IN_WINDOW,
    )
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()
    return client, world, order, line


def _reserve(client, world, order, line, qty):
    return client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(
            line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": qty}],
        )]},
    )


def _held_qty(db, line) -> Decimal:
    db.expire_all()
    return sum(
        (a.qty for a in db.query(SOLineAllocation).filter(SOLineAllocation.so_line_id == line.id)),
        Decimal("0"),
    )


def test_ac_6_9_reserve_covered_by_delivered_qty_needs_no_free_stock_and_holds_nothing(api):
    """AC-6 + AC-9: ordered 10, fully delivered, the pool shows 0 free: Confirm succeeds and
    no hold is written (the hold stays capped at what is still owed: 0)."""
    client, world, order, line = _confirm_world(
        api, ordered="10", delivered="10", on_hand=10, reserved=10
    )

    response = _reserve(client, world, order, line, "10")

    assert response.status_code == 200, response.text
    assert _held_qty(world.db, line) == Decimal("0")


def test_ac_7_short_undelivered_part_confirms_as_decided_with_a_warning(api):
    """AC-7, owner rule 12 Oct 2026 (an undelivered shortfall is a warning, never a block):
    ordered 10, delivered 4, Reserve 10 at 0 free CONFIRMS as decided, holds nothing, and
    the result and the decision both say 6 were short."""
    client, world, order, line = _confirm_world(
        api, ordered="10", delivered="4", on_hand=10, reserved=10
    )

    response = _reserve(client, world, order, line, "10")

    assert response.status_code == 200, response.text
    assert _held_qty(world.db, line) == Decimal("0")
    _assert_short(response, world, order, line, short="6", free="0")


def test_ac_7_9_only_the_delivered_part_is_exempt_six_free_succeeds_and_holds_six(api):
    """AC-7 (success half) + AC-9: ordered 10, delivered 4, 6 free: Reserve 10 succeeds and
    the hold is the 6 still owed, not 10."""
    client, world, order, line = _confirm_world(
        api, ordered="10", delivered="4", on_hand=10, reserved=4
    )

    response = _reserve(client, world, order, line, "10")

    assert response.status_code == 200, response.text
    assert _held_qty(world.db, line) == Decimal("6")


def test_ac_8_nothing_delivered_short_reserve_confirms_holding_what_is_free(api):
    """AC-8, owner rule 12 Oct 2026: nothing delivered, 5 free, Reserve 10 confirms as
    decided, holds the 5 that exist and records 5 short."""
    client, world, order, line = _confirm_world(
        api, ordered="10", delivered="0", on_hand=10, reserved=5
    )

    response = _reserve(client, world, order, line, "10")

    assert response.status_code == 200, response.text
    assert _held_qty(world.db, line) == Decimal("5")
    _assert_short(response, world, order, line, short="5", free="5")


def test_ac_6_borrow_covered_by_delivered_qty_needs_no_free_stock(api):
    """AC-6 (Borrow half): ordered 10 fully delivered, the donor location shows 0 free."""
    client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-DON-{line_tag()}", segment="dealer")
    _stock(db, world.product, donor_wh, on_hand=10, reserved=10)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered="10", qty_delivered="10",
        required_date=IN_WINDOW,
    )
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(
            line.id,
            borrow=[{
                "source": "other_location", "warehouse_id": donor_wh.id, "qty": "10",
                "reason": "Already delivered from there.",
            }],
        )]},
    )

    assert response.status_code == 200, response.text


# ------------------------------------------------------------------------ AC-10


def test_ac_10_ingest_closing_a_line_changes_no_oi_row_and_queues_no_handover(
    env, monkeypatch
):
    """AC-10 (regression lock): AutoCount ingest sets a line `closed` because delivered >=
    ordered; the OI row of that line keeps its state, qty, note and ack_state, and no
    handover is queued or dispatched. Purchasing hears only through Confirm."""
    from sqlalchemy import text

    from app.models.project_so import (
        OrderInquiry,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
    )
    from tests.test_ingest_documents import INGEST_SO, _so_line, _so_record
    from tests.test_order_inquiry_handover_automation import (
        _captured_dispatches,
        _register,
    )

    _register(env)
    calls = _captured_dispatches(monkeypatch)
    line_payload = _so_line(env, qty_ordered=10, qty_delivered=0)
    record = _so_record(env, lines=[line_payload])
    assert env.post(INGEST_SO, [record]).json()["summary"]["created"] == 1

    header = env.header("sales_orders", record["source_ref"])
    core = env.so_lines(header["id"])[0]
    mirror = ProjectSalesOrder(
        provisional_ref=f"ZZTDOC-PSO-{uuid_hex()}", company_id=env.company_a,
    )
    env.db.add(mirror)
    env.db.flush()
    project_line = ProjectSalesOrderLine(
        company_id=env.company_a, project_sales_order_id=mirror.id,
        core_sales_order_line_id=core["id"], line_no=1, product_id=core["product_id"],
        description="ZZTDOC line", qty=Decimal("10"), uom="SET",
        unit_price=Decimal("1"), amount=Decimal("0"),
    )
    env.db.add(project_line)
    env.db.flush()
    inquiry = OrderInquiry(
        company_id=env.company_a, project_sales_order_id=mirror.id, state=INQUIRY_RAISED,
    )
    env.db.add(inquiry)
    env.db.flush()
    row = OrderInquiryRow(
        company_id=env.company_a, order_inquiry_id=inquiry.id, so_line_id=project_line.id,
        item_code="ZZTDOC-ITEM", qty=Decimal("10"), verb=IV_ORDER, state=INQUIRY_RAISED,
        ack_state="awaiting", note="ZZTDOC note",
    )
    env.db.add(row)
    env.db.commit()
    before = (row.state, row.qty, row.note, row.ack_state, row.verb)
    calls.clear()

    delivered = dict(line_payload, qty_delivered=10)
    res = env.post(INGEST_SO, [dict(record, lines=[delivered])])

    assert res.json()["records"][0]["outcome"] == "updated", res.text
    status = env.db.execute(
        text("SELECT line_status FROM sales_order_lines WHERE id = :i"), {"i": str(core["id"])}
    ).scalar()
    assert status == "closed"
    env.db.expire_all()
    fresh = env.db.get(OrderInquiryRow, row.id)
    assert (fresh.state, fresh.qty, fresh.note, fresh.ack_state, fresh.verb) == before
    assert not env.db.info.get("oi_handover_pending")
    assert _handover_calls(calls) == []


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


# ------------------------------------------------------------------ round 2 (reviewer)


def _order_with_line(world, *, ordered, delivered="0", line_no=10):
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered=ordered,
        qty_delivered=delivered, required_date=IN_WINDOW,
    )
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=line_no, product=world.product, core_line=core_line)
    db.commit()
    return order, line


def _holder_world(api, *, holder_qty, ordered_a, delivered_a):
    """Pool on hand 10; ANOTHER order B confirms a real Reserve of `holder_qty` there (the
    holder is an allocation, not `quantity_reserved`), then line A is set up."""
    client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=10)
    order_b, line_b = _order_with_line(world, ordered=holder_qty, line_no=1)
    held = _reserve(client, world, order_b, line_b, holder_qty)
    assert held.status_code == 200, held.text
    order_a, line_a = _order_with_line(world, ordered=ordered_a, delivered=delivered_a)
    return client, world, order_a, line_a


def test_ac_6_r14_guard_exempts_the_delivered_part_when_another_order_holds_the_pool(api):
    """AC-6 via the R14 on-hand guard: order B holds all 10 on hand; closed line A (ordered 10,
    delivered 10) Reserve 10 must still confirm."""
    client, world, order_a, line_a = _holder_world(
        api, holder_qty="10", ordered_a="10", delivered_a="10"
    )

    response = _reserve(client, world, order_a, line_a, "10")

    assert response.status_code == 200, response.text


def test_ac_7_r14_guard_partial_delivery_with_six_left_by_the_holder_succeeds(api):
    """AC-7 via R14: A ordered 10 delivered 4; holder B leaves 6 on hand free: Reserve 10 ok."""
    client, world, order_a, line_a = _holder_world(
        api, holder_qty="4", ordered_a="10", delivered_a="4"
    )

    response = _reserve(client, world, order_a, line_a, "10")

    assert response.status_code == 200, response.text


def test_ac_7_r14_guard_partial_delivery_with_five_left_by_the_holder_warns(api):
    """AC-7 via R14, owner rule 12 Oct 2026: holder B leaves only 5 free, 6 undelivered
    units asked: confirms, holds 5, records 1 short, never refuses the order."""
    client, world, order_a, line_a = _holder_world(
        api, holder_qty="5", ordered_a="10", delivered_a="4"
    )

    response = _reserve(client, world, order_a, line_a, "10")

    assert response.status_code == 200, response.text
    assert _held_qty(world.db, line_a) == Decimal("5")
    _assert_short(response, world, order_a, line_a, short="1", free="5")


def _holds_by_warehouse(db, line):
    db.expire_all()
    out = {}
    for a in db.query(SOLineAllocation).filter(SOLineAllocation.so_line_id == line.id):
        out[a.warehouse_id] = out.get(a.warehouse_id, Decimal("0")) + a.qty
    return out


def _reserve_and_borrow_world(api, *, pool_free, donor_free):
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=10, reserved=10 - pool_free)
    donor = _warehouse(db, f"ZZT-DON-{line_tag()}", segment="dealer")
    _stock(db, world.product, donor, on_hand=10, reserved=10 - donor_free)
    order, line = _order_with_line(world, ordered="10", delivered="4")
    body = {"lines": [_line_payload(
        line.id,
        reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}],
        borrow=[{
            "source": "other_location", "warehouse_id": donor.id, "qty": "4",
            "reason": "Take the rest from the other location.",
        }],
        amend_reason="Mixed composition.",
    )]}
    return client, world, donor, line, client.post(f"{BASE}/sales-orders/{order.id}/confirm", json=body)


def test_credit_front_first_the_written_holds_match_what_was_checked(api):
    """Ordered 10, delivered 4, Reserve 6 (pool 2 free) + Borrow 4 (donor 10 free). The 4
    delivered units are credited to the Reserve first, so the recheck judged Reserve 2 and
    Borrow 4; the holds written must be pool 2 and donor 4 (not pool 6, donor 0)."""
    client, world, donor, line, response = _reserve_and_borrow_world(
        api, pool_free=2, donor_free=10
    )

    assert response.status_code == 200, response.text
    holds = _holds_by_warehouse(world.db, line)
    assert holds.get(world.pool_wh.id) == Decimal("2"), holds
    assert holds.get(donor.id) == Decimal("4"), holds


def test_credit_front_first_borrow_beyond_the_donor_warns(api):
    """Same shape, donor has only 3 free for the 4 borrowed. Owner rule 12 Oct 2026: the
    line confirms, the donor holds the 3 it has, and 1 is recorded short."""
    client, world, donor, line, response = _reserve_and_borrow_world(
        api, pool_free=2, donor_free=3
    )

    assert response.status_code == 200, response.text
    holds = _holds_by_warehouse(world.db, line)
    assert holds.get(world.pool_wh.id) == Decimal("2"), holds
    assert holds.get(donor.id) == Decimal("3"), holds
    notices = response.json()["stock_short_notices"]
    assert notices and "only 3 free" in notices[0]["reason"], notices


def test_ac_9_an_adopted_unowned_hold_never_pushes_the_total_hold_past_what_is_owed(api):
    """AC-9: line owes 6 (ordered 10, delivered 4) and already holds 6 under no revision
    (a reallocation). Reserve 10 adopts that hold; the total held must stay <= 6."""
    from app.models.project_so import ALLOC_SOURCE_BRW

    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=20)
    order, line = _order_with_line(world, ordered="10", delivered="4")
    from datetime import datetime

    db.add(SOLineAllocation(
        company_id=world.company_id, so_line_id=line.id, source_type=ALLOC_SOURCE_BRW,
        warehouse_id=world.pool_wh.id, qty=Decimal("6"), decision_id=None,
        reason="Reallocated from ZZT-OTHER line 1", confirmed_by=world.actor,
        confirmed_at=datetime.utcnow(),
    ))
    db.commit()

    response = _reserve(client, world, order, line, "10")

    assert response.status_code == 200, response.text
    assert _held_qty(db, line) <= Decimal("6"), _held_qty(db, line)


# ------------------------------------------------- owner rule 12 Oct: warn, never block


def _assert_short(response, world, order, line, *, short, free):
    """The shortfall is said in three places: the Confirm result, the decision snapshot,
    and (see the email test) the purchasing handover."""
    from app.models.project_so import SOSupplyDecision

    notices = response.json()["stock_short_notices"]
    assert notices and len(notices) == 1, response.text
    assert notices[0]["line_no"] == line.line_no
    assert f"only {free} free" in notices[0]["reason"], notices
    assert f"{short} short" in notices[0]["reason"], notices
    world.db.expire_all()
    active = (
        world.db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == "active",
        )
        .one()
    )
    snap = next(
        s for s in active.line_snapshots if s["project_line_id"] == str(line.id)
    )
    assert snap.get("stock_short"), snap
    assert Decimal(str(snap["stock_short"][0]["short"])) == Decimal(short), snap


def test_owner_example_delivered_line_reserve_98_and_order_back_2_over_77_free(api):
    """SO424764 line 144's shape (fake codes): plan 100, delivered 100, the pool shows 77
    free. CS decides Reserve 98 + order back 2. The delivered part is never capped, so this
    confirms with no warning and holds nothing (nothing is still owed)."""
    client, world, order, line = _confirm_world(
        api, ordered="100", delivered="100", on_hand=77, reserved=0
    )
    payload = _line_payload(
        line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "98"}], buy_qty="2",
        amend_reason="98 used from BB, 2 order back.",
    )
    payload["order_back"] = True

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm", json={"lines": [payload]}
    )

    assert response.status_code == 200, response.text
    assert not response.json().get("stock_short_notices"), response.text
    # No STOCK is held (nothing is still owed); the order back's own Buy row is not stock.
    assert _holds_by_warehouse(world.db, line).get(world.pool_wh.id) in (None, Decimal("0"))


def test_a_short_reserve_is_listed_in_the_purchasing_email(api, monkeypatch):
    """Owner rule 12 Oct 2026: the shortfall is listed in the purchasing email that the
    Confirm already sends, as its own line (no purchasing row is raised for it)."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    client, world, order, line = _confirm_world(
        api, ordered="10", delivered="0", on_hand=10, reserved=5
    )
    calls.clear()

    response = _reserve(client, world, order, line, "10")

    assert response.status_code == 200, response.text
    handover = _handover_calls(calls)
    assert len(handover) == 1, [c["trigger_type"] for c in calls]
    context = handover[0]["context"]["handover"]
    assert "STOCK SHORT" in context["verbs"], context["verbs"]
    short_lines = [e for e in context["lines"] if "short" in (e.get("remark") or "").lower()]
    assert len(short_lines) == 1, context["lines"]
    assert short_lines[0]["qty"] == "5", short_lines[0]
    world.db.expire_all()
    assert (
        world.db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id).count()
        == 0
    ), "a shortfall raises no purchasing row"


def test_one_line_that_cannot_be_written_is_held_back_and_the_rest_confirms(api):
    """Owner rule 12 Oct 2026: one bad line never refuses the whole order. Line 10 is a
    good Reserve; line 20's components do not add up. Line 10 confirms and holds; line 20
    is named in `lines_held_back`."""
    client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    core_so = _core_so(world.db, world.company_id)
    order = _project_so(world.db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    lines = []
    for line_no in (10, 20):
        core = _core_line(
            world.db, core_so, world.product, world.own_wh, qty_ordered="10",
            qty_delivered="0", required_date=IN_WINDOW,
        )
        lines.append(_project_line(world.db, order, line_no=line_no, product=world.product, core_line=core))
    world.db.commit()
    good, bad = lines

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [
            _line_payload(good.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}]),
            _line_payload(bad.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "3"}]),
        ]},
    )

    assert response.status_code == 200, response.text
    held_back = response.json()["lines_held_back"]
    assert [entry["line_no"] for entry in held_back] == [20], held_back
    assert _held_qty(world.db, good) == Decimal("10")
    assert _held_qty(world.db, bad) == Decimal("0")


def test_a_lone_line_that_cannot_be_written_is_still_refused(api):
    """Nothing left to confirm: the one bad line is refused by name, as before."""
    client, world, order, line = _confirm_world(
        api, ordered="10", delivered="0", on_hand=10, reserved=0
    )

    response = _reserve(client, world, order, line, "3")

    assert response.status_code in (409, 422), response.text
    assert response.json()["failing_lines"], response.text


def test_an_own_bin_reserve_with_a_reason_past_on_hand_is_recorded_short(api):
    """Browser check finding (12 Oct): with a reason, an own-group bin's capacity is lifted to
    the ask (the 8 Sep TEMPORARY LIFT), so the free-stock recheck never fired and nobody else
    held there for R14 to name. The floor still bounds it: plan 10, nothing delivered, 5 on
    hand at the line's own bin, Reserve 10 with a reason confirms, holds 5, records 5 short."""
    client, world = api
    _stock(world.db, world.product, world.own_wh, on_hand=5)
    order, line = _order_with_line(world, ordered="10")

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_line_payload(
            line.id, reserve=[{"warehouse_id": world.own_wh.id, "qty": "10"}],
            amend_reason="Own bin, take what is there.",
        )]},
    )

    assert response.status_code == 200, response.text
    assert _holds_by_warehouse(world.db, line).get(world.own_wh.id) == Decimal("5")
    _assert_short(response, world, order, line, short="5", free="5")
