"""S1, apply never fails over a vanished placement (D2, R3 minimum).

`documentation/plans/scm/PLAN-board-received-stock-own-arrival.md` S1, UAC
`board-received-stock-own-arrival-acceptance-criteria.md` AC-S1-1 to AC-S1-3. RED before the
coder's slice lands - written from the CONTRACT (the plan + UAC), with no implementation to
look at.

`planning_change_service._redeal_document` (~:3258) today raises `AppException(409,
code="planning_change_reallocation_no_document")` whenever `available < freed`, whatever
`available` is - including the AC-S1-1 case where it is ZERO (the placement this suggestion
named is no longer on the line at all). The fix splits that one condition in two: `available
<= 0` on a non-cancelled row records "nothing to move" in `released` and returns, `0 <
available < freed` keeps the 409 unchanged (AC-S1-2).

`_apply_one_order` (~:4169+) has no check today for a live row's CORE line having been
closed since the batch was built - AC-S1-3 is new.

Fixtures imported rather than copied, the same convention every other Slice A-D file in this
domain uses:
- `tests.test_planning_changes` (`api` fixture: `(client, world)` with `world.company_id`/
  `.actor`/`.project`/`.product`/`.own_wh`/`.pool_wh`/`.db`; `_confirm`/`_line_payload`/
  `_diff_change`/`_core_so`/`_core_line`/`_project_so`/`_project_line`/`_uid`).
- `tests.scm.test_planning_change_reallocation` (`_drop_line_to_100`: S2's own shape, 234
  wholly Buy, 134 placed on a real PO, 100 raised unlinked, dropped to 100 - composes a
  `reallocate` component for the freed 34 against a REAL `PurchaseOrderLine`; `_links_of`).

Postgres only (`tests/_pg_fixture.py`), every FK seeded here, never a borrowed row.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.planning_change import (
    PLANNING_CHANGE_STATE_PENDING,
    PLANNING_CHANGE_STATE_SUPERSEDED,
)
from app.models.project_so import OrderInquiryLink, OrderInquiryRow
from app.services import planning_change_service
from app.services.scm.outstanding_diff import QTY_CHANGED, Diff

from tests.test_planning_changes import (  # noqa: F401  (api is the fixture)
    api,
    _confirm,
    _core_line,
    _core_so,
    _diff_change,
    _line_payload,
    _project_line,
    _project_so,
)
from tests.scm.test_planning_change_reallocation import _drop_line_to_100


def _only_row(db, batch):
    from app.models.planning_change import PlanningChangeRow

    return (
        db.query(PlanningChangeRow)
        .filter(PlanningChangeRow.batch_id == batch.id)
        .one()
    )


def test_ac_s1_1_apply_never_fails_over_a_placement_that_vanished_by_apply_time(api):
    """AC-S1-1: "A pending batch row (kind delayed, undecided) whose line has no OI row
    and no link at apply time: Confirm succeeds; `result_json["released_documents"]` for
    that row contains "nothing to move"; no `planning_change_reallocation_no_document`."

    Built off `_drop_line_to_100`'s own qty_down shape (composes a `reallocate` for the
    freed 34 against a real placed PO line), confirmed, and then - simulating the row this
    AC names, "no OI row and no link at apply time" - the OWN row's link is deleted between
    compose and apply, the same as `_redeal_document`'s own docstring: "the placement this
    suggestion named is no longer on the line". `available` reads 0 at apply, not merely
    less than `freed` (34) - AC-S1-2 is the partial case, kept apart below.
    """
    world, core_so, core_line, order, line, po, batch = _drop_line_to_100(api)
    db = world.db

    own_row = db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id).one()
    db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == own_row.id).delete()
    db.commit()

    row = _only_row(db, batch)
    planning_change_service.set_row_decision(db, str(batch.id), str(row.id), "confirm")
    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()

    assert result["failed_orders"] == [], (
        "a vanished placement must not fail the whole order: "
        f"{result}"
    )
    db.expire_all()
    from app.models.planning_change import PlanningChangeRow

    fresh = db.get(PlanningChangeRow, row.id)
    released = (fresh.result_json or {}).get("released_documents") or []
    assert any("nothing to move" in text for text in released), fresh.result_json


def test_ac_s1_2_a_partial_placement_records_what_is_left_and_never_fails_the_order(api):
    """AC-S1-2, re-read under the owner's 29 Sep 2026 ruling (PR #1369, option (c)): the
    planning side re-deals nothing, so there is no re-deal to refuse. "Same row but the
    line still holds part of the frozen quantity (0 < available < freed)": Confirm
    succeeds; the row's `released_documents` records the intent for the part still on the
    line (AutoCount) and says the rest is no longer on the line, nothing to move. The 409
    `planning_change_reallocation_no_document` is retired.

    The same `_drop_line_to_100` shape (freed = 34), but only PART of the placement is
    left by apply time: the link is trimmed to 10 rather than removed outright, so
    `0 < available (10) < freed (34)`.
    """
    world, core_so, core_line, order, line, po, batch = _drop_line_to_100(api)
    db = world.db

    own_row = db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id).one()
    link = (
        db.query(OrderInquiryLink)
        .filter(OrderInquiryLink.row_id == own_row.id)
        .one()
    )
    link.qty = Decimal("10")
    db.commit()

    row = _only_row(db, batch)
    planning_change_service.set_row_decision(db, str(batch.id), str(row.id), "confirm")
    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()

    assert result["failed_orders"] == [], result
    db.expire_all()
    from app.models.planning_change import PlanningChangeRow

    fresh = db.get(PlanningChangeRow, row.id)
    released = (fresh.result_json or {}).get("released_documents") or []
    assert any(
        po.po_number in text and "10" in text and "AutoCount" in text for text in released
    ), fresh.result_json
    assert any(
        "24" in text and "no longer on the line" in text and "nothing to move" in text
        for text in released
    ), fresh.result_json
    assert not (fresh.result_json or {}).get("executed_reallocations"), fresh.result_json


def _two_line_batch(api, *, close_line_1_as):
    """One order, two lines of the same product, both qty_down in the SAME batch; line 1's
    core row is then set to `close_line_1_as` between compose and apply, line 2 stays open.
    Returns `(world, batch, line1, line2, row1, row2)`."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line1 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="234",
                             required_date=date(2027, 3, 1))
    core_line2 = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50",
                             required_date=date(2027, 2, 1))
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line1 = _project_line(db, order, line_no=1, product=world.product, core_line=core_line1)
    line2 = _project_line(db, order, line_no=2, product=world.product, core_line=core_line2)
    db.commit()

    _confirm(client, order.id, {"lines": [
        _line_payload(line1.id, buy_qty="234", buy_reason="ZZT no stock anywhere"),
        _line_payload(line2.id, buy_qty="50", buy_reason="ZZT no stock anywhere"),
    ]})

    core_line1.qty_ordered = Decimal("100")
    line1.qty = Decimal("100")
    core_line2.qty_ordered = Decimal("30")
    line2.qty = Decimal("30")
    db.flush()

    changed1 = _diff_change(
        QTY_CHANGED, core_line1, doc_number=core_so.so_number,
        item_code=world.product.product_code, location=world.own_wh.warehouse_code,
        old_date=date(2027, 3, 1), new_date=date(2027, 3, 1), old_qty="234", new_qty="100",
    )
    changed2 = _diff_change(
        QTY_CHANGED, core_line2, doc_number=core_so.so_number,
        item_code=world.product.product_code, location=world.own_wh.warehouse_code,
        old_date=date(2027, 2, 1), new_date=date(2027, 2, 1), old_qty="50", new_qty="30",
    )
    diff = Diff(scope_documents=(core_so.so_number,), changes=[changed1, changed2])
    batch = planning_change_service.build_batch(
        db, diff,
        applied_line_ids={id(changed1): str(core_line1.id), id(changed2): str(core_line2.id)},
        order_ids={core_so.so_number: str(core_so.id)}, actor=world.actor,
        import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    assert batch is not None
    assert batch.line_count == 2

    core_line1.line_status = close_line_1_as
    db.commit()

    from app.models.planning_change import PlanningChangeRow

    rows = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id).all()
    row1 = next(r for r in rows if r.line_no == 1)
    row2 = next(r for r in rows if r.line_no == 2)
    planning_change_service.set_row_decision(db, str(batch.id), str(row1.id), "confirm")
    planning_change_service.set_row_decision(db, str(batch.id), str(row2.id), "confirm")
    return world, batch, line1, line2, row1, row2


def test_ac_s1_3_a_row_whose_core_line_closed_now_applies_and_the_rest_of_the_order_applies(api):
    """AC-S1-3, REVERSED by the owner ruling of 11 Oct 2026 (OI-CLOSED-LINE AC-1 + AC-4):
    line status must not decide whether a confirmed decision applies. A pending row whose
    core line became `closed` after the batch was built applies like an open line (no
    "the sales-order line is closed" reason anywhere), and line 2's own change in the SAME
    batch still applies ("the rest of the order applies").
    """
    world, batch, line1, line2, row1, row2 = _two_line_batch(api, close_line_1_as="closed")
    db = world.db

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()

    assert result["failed_orders"] == [], result["failed_orders"]

    from app.models.planning_change import (
        PLANNING_CHANGE_STATE_APPLIED,
        PlanningChangeRow,
    )

    db.expire_all()
    fresh1 = db.get(PlanningChangeRow, row1.id)
    assert fresh1.applied_state == PLANNING_CHANGE_STATE_APPLIED, (
        fresh1.applied_state, fresh1.applied_reason,
    )
    assert "the sales-order line is closed" not in (fresh1.applied_reason or "")
    line1_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line1.id, OrderInquiryRow.qty == Decimal("100"))
        .one_or_none()
    )
    assert line1_row is not None, "the closed line's own qty_down was applied to its OI row"

    # Line 2's own change still applied - "the rest of the order applies" (AC-4).
    fresh2 = db.get(PlanningChangeRow, row2.id)
    assert fresh2.applied_state == PLANNING_CHANGE_STATE_APPLIED, fresh2.applied_state
    line2_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line2.id, OrderInquiryRow.qty == Decimal("30"))
        .one_or_none()
    )
    assert line2_row is not None, "line 2's own qty_down was applied"


def test_ac_5_a_row_whose_core_line_was_cancelled_is_still_superseded(api):
    """OI-CLOSED-LINE AC-5: a pending row whose core line became `cancelled` between compose
    and apply is superseded, reason "the sales-order line is cancelled"; the rest of the
    order still applies."""
    world, batch, line1, line2, row1, row2 = _two_line_batch(api, close_line_1_as="cancelled")
    db = world.db

    result = planning_change_service.apply(db, str(batch.id), world.actor)
    db.commit()

    assert result["failed_orders"] == [], result["failed_orders"]

    from app.models.planning_change import PlanningChangeRow

    db.expire_all()
    fresh1 = db.get(PlanningChangeRow, row1.id)
    assert fresh1.applied_state == PLANNING_CHANGE_STATE_SUPERSEDED, fresh1.applied_state
    assert fresh1.applied_reason == "the sales-order line is cancelled", fresh1.applied_reason
    fresh2 = db.get(PlanningChangeRow, row2.id)
    assert fresh2.applied_state != PLANNING_CHANGE_STATE_PENDING, fresh2.applied_state
