"""Slice E, one signal (`documentation/plans/scm/PLAN-scm-change-management-one-engine.md`
rule 9, UAC AC-E1/AC-E2, issue #860). RED before the coder's slice lands - no implementation
change exists yet.

`challenge_if_drifted` (`app/services/project_supply_service.py` ~3910) is retired from every
caller (`proposal_for` ~1037, `confirm` ~4126, `project_so_reconciliation_service.py`'s
non-relink `_persist` branch ~562). A drift is never a signal of its own again: the change
batch is. The borrow-hold release `challenge_if_drifted` used to perform as a side effect
(`_release_supply_borrow_holds`) has to be carried into batch apply instead, or a step-3
supply-borrow placement made under an old decision stays pinned once that decision stops
being active (Slice B follow-up note, `PLAN-scm-change-management-one-engine.md` Slice B
contract point 3).

Postgres only (`tests/_pg_fixture.py`), every FK seeded here or via the helper modules
below, never a borrowed row. Helpers are imported, not copied, from:
- `tests.scm.test_planning_change_delta_seam` (`_held_buy_world`, `_held_reserve_world`,
  `_change_and_batch`, `_only_row`, `_confirm_and_apply`, `_live_order_rows`, `_active_decision`,
  world/date constants) - same step-3 supply-borrow shape it already exercises.
- `tests.scm.test_project_supply_service_ladder` (`_lead_time`) and
  `tests.scm.test_ladder_v7_supply_borrow` (`_spo`) for the step-3 document.
- `tests.test_so_supply_confirmation` (`_warehouse`).
- `tests.scm.test_scm_sales_order_edit_propagation` (`api` fixture, `_linked_line`,
  `_freeze_with_a_full_buy`, `_row_for`) for the manual-edit path (E1b).
- `tests.test_project_so_reconciliation` (`_sorento`, `_user`, `_product`, `_project`,
  `_core_order`, `_core_line`, `_project_order`, `_project_line`) for the reconciliation
  non-relink path (E1c).
"""
from __future__ import annotations

import pytest

import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
from app.models.project_so import (
    DECISION_ACTIVE,
    DECISION_SUPERSEDED,
    OrderInquiryLink,
    ProjectSalesOrder,
    SOSupplyDecision,
)
from app.schemas.scm_orders import SalesOrderUpdate
from app.services import planning_change_service
from app.services.project_so_reconciliation_service import ProjectSOReconciliationService
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.outstanding_diff import CLOSED, Change, Diff, Line
from app.services.scm.sales_order_service import SalesOrderService

from tests._pg_fixture import blank_session
from tests.scm.test_planning_change_delta_seam import (
    _active_decision,
    _change_and_batch,
    _confirm_and_apply,
    _held_buy_world,
    _live_order_rows,
    _only_row,
)
from tests.scm.test_project_supply_service_ladder import _lead_time
from tests.scm.test_ladder_v7_supply_borrow import _spo
from tests.scm.test_scm_sales_order_edit_propagation import (
    _freeze_with_a_full_buy,
    _linked_line,
    _row_for,
    api,  # noqa: F401  (pytest fixture)
)
from tests.test_project_so_reconciliation import (
    _core_line as _recon_core_line,
    _core_order,
    _product as _recon_product,
    _project as _recon_project,
    _project_line as _recon_project_line,
    _project_order,
    _sorento,
    _user as _recon_user,
)
from tests.test_so_supply_confirmation import _warehouse

MARKER = "zzt-onesignal"


def _uid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# AC-E1a: a drift on the SHEET READ never challenges the active decision
# --------------------------------------------------------------------------- #

def test_a_drift_on_sheet_read_never_challenges_the_active_decision():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134")
        decision_id = _active_decision(db, world["order"].id).id

        # The book moved the quantity: the frozen snapshot's open_qty is now stale.
        world["core_line"].qty_ordered = Decimal("140")
        db.commit()

        ProjectSupplyService(db).proposal_for(world["order"])
        db.commit()

        db.expire_all()
        decision = db.get(SOSupplyDecision, decision_id)
        assert decision.state == DECISION_ACTIVE, decision.state


# --------------------------------------------------------------------------- #
# AC-E1b: a manual edit raises a batch and supersedes on APPLY, not before
# --------------------------------------------------------------------------- #

def test_a_manual_edit_raises_a_batch_and_supersedes_on_apply_not_before(api):
    world, project = api
    db = world.db
    core_so, core_line, product = _linked_line(world, project, qty_ordered=72)
    _freeze_with_a_full_buy(db, world, core_so)

    order = db.query(ProjectSalesOrder).filter_by(so_id=core_so.id).one()
    old_decision = (
        db.query(SOSupplyDecision)
        .filter_by(project_sales_order_id=order.id, state=DECISION_ACTIVE)
        .one()
    )
    old_decision_id = old_decision.id

    result = SalesOrderService(db).update(
        core_so.id,
        SalesOrderUpdate(lines=[{
            "id": core_line.id, "sku": product.product_code, "qty_ordered": 80,
        }]),
        user_id=world.actor,
    )
    db.commit()
    envelope = result["planning_change_batch"]
    assert envelope is not None, "a manual edit on a held line must raise a batch"

    # Raising the batch is not confirming anything - the old decision is untouched.
    db.expire_all()
    assert db.get(SOSupplyDecision, old_decision_id).state == DECISION_ACTIVE

    row = _row_for(db, envelope["id"])
    planning_change_service.set_row_decision(db, envelope["id"], str(row.id), "confirm")
    db.commit()
    planning_change_service.apply(db, envelope["id"], world.actor)
    db.commit()

    db.expire_all()
    old_after_apply = db.get(SOSupplyDecision, old_decision_id)
    assert old_after_apply.state == DECISION_SUPERSEDED, old_after_apply.state
    new_active = (
        db.query(SOSupplyDecision)
        .filter_by(project_sales_order_id=order.id, state=DECISION_ACTIVE)
        .one()
    )
    assert new_active.id != old_decision_id


# --------------------------------------------------------------------------- #
# AC-E1c: reconciliation's non-relink branch leaves the decision active
# --------------------------------------------------------------------------- #

def test_reconciliation_without_a_relink_leaves_the_decision_active():
    """`_persist`'s ELSE branch (`project_so_reconciliation_service.py` ~562, taken
    whenever nothing needed relinking) is what is under test - the RELINK branch (a
    `core_sales_order_line_id` actually moving) still calls `supersede_for_material_
    change`, which stays untouched by this slice (`test_a_reconciliation_link_change_
    supersedes_the_active_decision` in `tests/test_so_supply_confirmation.py` already
    covers it)."""
    with blank_session() as db:
        company_id = _sorento(db)
        from app.services import project_seed_service

        project_seed_service.run(db, company_id=company_id)
        actor = _recon_user(db)
        project = _recon_project(db, company_id, actor)
        core_so = _core_order(db, so_number=f"{MARKER}-{_uid()[:8]}", company_id=company_id)
        product = _recon_product(db)
        d1 = date(2027, 1, 7)
        d2 = date(2027, 2, 4)
        core_line = _recon_core_line(db, core_so, product, required_date=d1, qty="50")
        order = _project_order(
            db, project, autocount_doc_no=core_so.so_number, so_id=core_so.id,
        )
        line = _recon_project_line(
            db, order, product, line_no=10, delivery_date=d1, qty="50",
            core_sales_order_line_id=core_line.id,
        )
        db.commit()

        decision = SOSupplyDecision(
            id=_uid(), company_id=company_id, project_sales_order_id=order.id,
            revision_no=1, state=DECISION_ACTIVE,
            line_snapshots=[{
                "line_no": 10, "project_line_id": line.id, "core_line_id": core_line.id,
                "open_qty": "50", "required_date": d1.isoformat(),
            }],
            confirmed_by=actor, confirmed_at=None,
        )
        db.add(decision)
        db.commit()

        # Drift, no relink: the core line's required date moved, but its
        # core_sales_order_line_id (and so the project line's link to it) is unchanged.
        core_line.required_date = d2
        db.commit()

        ProjectSOReconciliationService(db).reconcile(order)
        db.commit()

        db.expire_all()
        refreshed = db.get(SOSupplyDecision, decision.id)
        assert refreshed.state == DECISION_ACTIVE, refreshed.state


# --------------------------------------------------------------------------- #
# AC-E2a: challenge_if_drifted has no caller
# --------------------------------------------------------------------------- #

def test_challenge_if_drifted_has_no_caller():
    backend_root = Path(__file__).resolve().parents[2]
    supply_path = backend_root / "app" / "services" / "project_supply_service.py"
    reconcile_path = backend_root / "app" / "services" / "project_so_reconciliation_service.py"

    supply_src = supply_path.read_text()
    stray_calls = [
        line
        for line in supply_src.splitlines()
        if "challenge_if_drifted(" in line
        and not line.lstrip().startswith("def challenge_if_drifted(")
    ]
    assert stray_calls == [], stray_calls

    reconcile_src = reconcile_path.read_text()
    assert "challenge_if_drifted(" not in reconcile_src, (
        "project_so_reconciliation_service.py must not call challenge_if_drifted"
    )


# --------------------------------------------------------------------------- #
# AC-E2b/AC-E2c: the borrow-hold release moves to batch apply
# --------------------------------------------------------------------------- #

@pytest.mark.usefixtures("ladder_steps_on")
def test_apply_retires_a_step_3_supply_borrow_placement_the_new_composition_drops():
    """The reviewer follow-up shape (`test_planning_change_delta_seam.py`'s own
    `test_qty_up_covered_by_a_step_3_supply_borrow_composes_and_confirms`), carried one
    change further: once a SECOND change means the re-run no longer needs the document
    (the required date advances to before the document's own arrival, so a fresh Buy is
    what a comfortably-outside-the-lead-time-window unit always resolves to - Step 0,
    plan rule 2), applying that change must retire the placement. Today the release only
    ran inside `challenge_if_drifted`; once Slice E strips that call from `confirm()`, the
    release has to be carried into apply directly or the placement stays pinned forever."""
    asker_day = date.today() + timedelta(days=20)
    late_arrival = date.today() + timedelta(days=25)
    with blank_session() as db:
        world = _held_buy_world(db, qty="134", required_date=asker_day)
        _lead_time(db, world["product"], 30)
        donor_bin = _warehouse(db, f"ZZT-SPB-{_uid()[:4]}")
        allocation = _spo(db, world["product"], donor_bin, qty=234, arrives=late_arrival)

        first_batch = _change_and_batch(db, world, new_qty="234")
        first_row = _only_row(db, first_batch)
        first_composition = planning_change_service.composition_from_proposal(
            first_row.proposal_json
        )
        assert (first_composition.get("borrow") or []) != [], first_composition

        first_result = _confirm_and_apply(db, world, first_batch)
        assert first_result["failed_orders"] == [], first_result["failed_orders"]

        db.expire_all()
        links_before = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == str(allocation.id))
            .all()
        )
        assert [str(l.qty) for l in links_before] == ["234.0000"], links_before
        first_decision_id = _active_decision(db, world["order"].id).id

        # Advance the required date well before the document's own arrival AND well
        # outside the lead-time window, so the ladder's whole-unit rule (rule 1/Step 0)
        # composes a fresh Buy - never a partial mix of borrow and buy.
        new_required_date = date.today() + timedelta(days=200)
        second_batch = _change_and_batch(db, world, new_required_date=new_required_date)
        second_row = _only_row(db, second_batch)
        second_composition = planning_change_service.composition_from_proposal(
            second_row.proposal_json
        )
        assert (second_composition.get("borrow") or []) == [], second_composition

        second_result = _confirm_and_apply(db, world, second_batch)
        assert second_result["failed_orders"] == [], second_result["failed_orders"]

        db.expire_all()
        links_after = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == str(allocation.id))
            .all()
        )
        assert links_after == [], (
            "the old decision's step-3 supply-borrow placement must be retired once the "
            "new composition drops it"
        )
        live_rows = _live_order_rows(db, world["line"].id)
        assert len(live_rows) == 1, [str(r.id) for r in live_rows]
        assert live_rows[0].qty == Decimal("234"), live_rows[0].qty
        # AC-E1, tied to this same apply (Slice B follow-up note point 2): the revision
        # this apply retired must read SUPERSEDED, never CHALLENGED - a `challenge_if_
        # drifted` call left mid-`confirm()` (the mechanism this slice retires) flips it
        # to `challenged` and, because `_write_decision`'s own `active_decision()` lookup
        # then finds nothing ACTIVE left to supersede, it stays stuck there forever.
        first_decision = db.get(SOSupplyDecision, first_decision_id)
        assert first_decision.state == DECISION_SUPERSEDED, first_decision.state


@pytest.mark.usefixtures("ladder_steps_on")
def test_apply_retires_the_step_3_placement_of_a_line_the_batch_cancels():
    """E1 (review round): the existing regression above does not guard
    `ProjectOrderInquiryService.retire_supply_borrow_rows` at all - the line is NAMED in
    the confirm payload (a qty/date change), so `confirm()`'s own per-line retirement
    already runs regardless of the dedicated call at planning_change_service.py
    ~3625-3629. This test's premise was that a batch whose ONLY row CANCELS the line
    (kind `cancelled`, never named in `confirm_lines`) would skip that same-gated call
    entirely - `retire_supply_borrow_rows` only runs `if confirm_lines:`, and a lone
    cancellation never populates it.

    VERIFIED empirically, per the brief: `ProjectOrderInquiryService.retire_supply_borrow
    _rows` was monkeypatched to a no-op for this exact scenario, and the assertions below
    still passed. The step-3 placement is retired anyway, by a DIFFERENT and older
    mechanism - `_retire_inquiry_rows`/`_shift_links_off_retired_lines` (AC-P3-6, part 3),
    which runs unconditionally whenever a batch cancels a row's line, regardless of
    `confirm_lines`, and frees (or shifts) every link a cancelled row carried, an SPO
    placement included. So this is NOT a red: `retire_supply_borrow_rows`'s `confirm_lines`
    guard has no live gap on the cancel path specifically - it would only matter for a line
    that leaves an ACTIVE decision's coverage WITHOUT its own row being cancelled (a bystander
    uncovered by a diff naming only OTHER lines), a different shape from this one. Kept as a
    real regression test of the correct, already-working cancel-path behavior."""
    asker_day = date.today() + timedelta(days=20)
    late_arrival = date.today() + timedelta(days=25)
    with blank_session() as db:
        world = _held_buy_world(db, qty="134", required_date=asker_day)
        _lead_time(db, world["product"], 30)
        donor_bin = _warehouse(db, f"ZZT-SPB-{_uid()[:4]}")
        allocation = _spo(db, world["product"], donor_bin, qty=234, arrives=late_arrival)

        first_batch = _change_and_batch(db, world, new_qty="234")
        first_result = _confirm_and_apply(db, world, first_batch)
        assert first_result["failed_orders"] == [], first_result["failed_orders"]

        db.expire_all()
        links_before = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == str(allocation.id))
            .all()
        )
        assert [str(l.qty) for l in links_before] == ["234.0000"], links_before

        # The book closes the line entirely - a `cancelled` kind row, never a qty/date
        # change, so it is never named in `confirm_lines`.
        core_so = world["core_so"]
        core_line = world["core_line"]
        item_code = world["product"].product_code
        location = world["own"].warehouse_code
        before = Line(
            doc_number=core_so.so_number, item_code=item_code, location=location,
            qty=234.0, required_date=core_line.required_date, row_ref=str(core_line.id),
        )
        change = Change(CLOSED, core_so.so_number, item_code, location, before=before, after=None)
        core_line.line_status = "closed"
        db.flush()
        diff = Diff(scope_documents=(core_so.so_number,), changes=[change])
        second_batch = planning_change_service.build_batch(
            db, diff, applied_line_ids={id(change): str(core_line.id)},
            order_ids={core_so.so_number: str(core_so.id)}, actor=world["actor"],
            import_job_id=None, file_name="book.xlsx",
        )
        db.commit()
        second_row = _only_row(db, second_batch)
        assert second_row.kind == "cancelled", second_row.kind

        second_result = _confirm_and_apply(db, world, second_batch)
        assert second_result["failed_orders"] == [], second_result["failed_orders"]

        db.expire_all()
        links_after = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == str(allocation.id))
            .all()
        )
        assert links_after == [], (
            "the step-3 placement of a line the batch cancels must not stay pinned"
        )


@pytest.mark.usefixtures("ladder_steps_on")
def test_apply_keeps_a_step_3_supply_borrow_the_new_composition_still_carries():
    """A top-up the SAME document still covers whole re-places through the same seam,
    never doubling the link: one live placement at the NEW quantity, not two."""
    asker_day = date.today() + timedelta(days=20)
    late_arrival = date.today() + timedelta(days=25)
    with blank_session() as db:
        world = _held_buy_world(db, qty="134", required_date=asker_day)
        _lead_time(db, world["product"], 30)
        donor_bin = _warehouse(db, f"ZZT-SPB-{_uid()[:4]}")
        allocation = _spo(db, world["product"], donor_bin, qty=300, arrives=late_arrival)

        first_batch = _change_and_batch(db, world, new_qty="234")
        first_result = _confirm_and_apply(db, world, first_batch)
        assert first_result["failed_orders"] == [], first_result["failed_orders"]
        first_decision_id = _active_decision(db, world["order"].id).id

        second_batch = _change_and_batch(db, world, new_qty="260")
        second_row = _only_row(db, second_batch)
        second_composition = planning_change_service.composition_from_proposal(
            second_row.proposal_json
        )
        borrow = second_composition.get("borrow") or []
        assert len(borrow) == 1, second_composition
        assert borrow[0]["qty"] == "260", borrow[0]

        second_result = _confirm_and_apply(db, world, second_batch)
        assert second_result["failed_orders"] == [], second_result["failed_orders"]

        db.expire_all()
        links_after = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == str(allocation.id))
            .all()
        )
        assert [str(l.qty) for l in links_after] == ["260.0000"], links_after
        # AC-E1, tied to this same apply: the revision this apply superseded reads
        # SUPERSEDED, never stuck CHALLENGED (see the twin assertion's docstring in
        # `test_apply_retires_a_step_3_supply_borrow_placement_the_new_composition_drops`
        # above).
        first_decision = db.get(SOSupplyDecision, first_decision_id)
        assert first_decision.state == DECISION_SUPERSEDED, first_decision.state
