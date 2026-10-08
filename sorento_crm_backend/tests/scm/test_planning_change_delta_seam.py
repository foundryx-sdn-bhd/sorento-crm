"""Slice B, delta seam (`documentation/plans/scm/PLAN-scm-change-management-one-engine.md`,
UAC AC-B1 to AC-B3, issue #857, scenarios S1 and S11 in
`documentation/plans/scm/mockups/so-change-management-grill-v4.html`). RED before the
coder's slice lands - no implementation exists yet.

Slice C replaces the suggestion vocabulary; these tests assert `proposal_json` /
`composition_json` and the inquiry-row / hold effects AFTER apply, never `row.suggested`
(which stays `replan`/`keep` until Slice C).

Every scenario is on one product, one own location (`BRW`'s own bin, from `_group_sites`),
one pool (`BRW`'s own pool) - the same shape the grill page's scenarios use. Dates:
`NON_IMMEDIATE` (well beyond both `RESERVE_WINDOW_DAYS=60` and `DEFAULT_IMMEDIATE_WINDOW_
DAYS=30`) unless a scenario is explicitly about the immediate window.

Helpers are imported, not copied, from the files the captain's brief points at:
`tests.test_so_supply_confirmation` (`_core_so`/`_core_line`/`_project_so`/`_project_line`/
`_product`/`_warehouse`/`_stock`/`_user`/`_sorento`/`_uid`) and
`tests.scm.test_project_supply_service_ladder` (`_world`/`_group_sites`) - the same
convention `tests/scm/test_ladder_v7_borrow.py` already uses for this exact combination.
The order/line pair is seeded locally (`_seed_order`) rather than via that file's own
`_seed_line`, because `_seed_line`'s `_project_so` call never sets `so_id` - fine for a
pure ladder/proposal test, but `build_batch`'s `order_ids` mapping (SO number -> core SO
id) needs it for the ADDED-kind path, and setting it costs nothing for the matched-line
path this file actually exercises.

Postgres only (`tests/_pg_fixture.py`), every FK seeded here, never a borrowed row.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
from app.models.project_so import (
    ALLOC_SOURCE_ORDER,
    DECISION_ACTIVE,
    DECISION_SUPERSEDED,
    INQUIRY_CANCELLED,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiryLink,
    OrderInquiryRow,
    SOLineAllocation,
    SOSupplyDecision,
)
from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
from app.services import planning_change_service
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.outstanding_diff import DATE_MOVED, QTY_CHANGED, Change, Diff

from tests._borrow_mode import borrow_from_order_on
from tests._pg_fixture import blank_session
from tests.test_so_supply_confirmation import (  # noqa: F401  (helpers, not fixtures)
    _core_line,
    _core_so,
    _product,
    _project_line,
    _project_so,
    _stock,
    _uid,
    _warehouse,
)
from tests.scm.test_project_supply_service_ladder import _group_sites, _lead_time, _world
from tests.scm.test_ladder_v7_supply_borrow import _spo

MARKER = "zzt-deltaseam"

#: Well beyond RESERVE_WINDOW_DAYS (60) and DEFAULT_IMMEDIATE_WINDOW_DAYS (30) - the
#: baseline for every AC-B1/AC-B2 scenario (S1: "line due 4 Sep, not immediate").
NON_IMMEDIATE = date.today() + timedelta(days=100)
#: Inside DEFAULT_IMMEDIATE_WINDOW_DAYS - AC-B3a/b (S11: "advance INTO the immediate
#: window").
IMMEDIATE = date.today() + timedelta(days=10)
#: Advanced from NON_IMMEDIATE but still beyond the 30-day window - AC-B3c.
STILL_OUTSIDE = date.today() + timedelta(days=45)


def _seed_order(db, company_id, project, product, warehouse, *, qty, required_date,
                 so_number=None, qty_delivered="0"):
    core_so = _core_so(db, company_id)
    if so_number:
        core_so.so_number = so_number
        db.flush()
    core_line = _core_line(
        db, core_so, product, warehouse, qty_ordered=qty, qty_delivered=qty_delivered,
        required_date=required_date,
    )
    order = _project_so(db, project, so_id=core_so.id)
    # `_so_number` (planning_change_service.py) reads `order.autocount_doc_no or
    # order.provisional_ref` - `test_so_supply_confirmation._project_so` sets neither to
    # the CORE so_number, so `_proposal_for`'s board build would otherwise look up a
    # sales order that does not exist (the project order's own provisional_ref) and find
    # nothing. Every other Slice A/B fixture in this lane sets this explicitly
    # (`test_planning_changes.py`'s own `_project_so(..., autocount_doc_no=core_so
    # .so_number)`) - matched here.
    order.autocount_doc_no = core_so.so_number
    line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
    db.commit()
    return order, line, core_so, core_line


def _held_buy_world(db, *, qty="134", required_date=NON_IMMEDIATE, qty_delivered="0"):
    """A held Buy, raised ORDER row, on one line - S1's own starting shape.

    `qty_delivered` (default "0", S1's own shape): PQ7
    (`documentation/plans/scm/PLAN-scm-planning-change-plan-qty.md`, issue #971) needs the
    SAME held-Buy shape on a line ALREADY partly delivered, to prove the confirm/amend path
    still balances against the plan quantity rather than what is left owed. The initial
    confirm still buys the WHOLE `qty` regardless: `ProjectSupplyService.confirm` validates
    against the plan quantity, not the owed one, since the 14 September ruling."""
    company_id, actor, project, product = _world(db)
    _group, sites = _group_sites(db)
    own, pool = sites["BRW"]
    order, line, core_so, core_line = _seed_order(
        db, company_id, project, product, own, qty=qty, required_date=required_date,
        qty_delivered=qty_delivered,
    )
    ProjectSupplyService(db).confirm(
        order,
        ConfirmSupplyBody(lines=[ConfirmLine(
            project_line_id=line.id, buy_qty=qty, buy_reason="ZZT no stock anywhere",
        )]),
        actor_user_id=actor,
    )
    db.commit()
    order_row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.verb == IV_ORDER)
        .one()
    )
    return {
        "company_id": company_id, "actor": actor, "project": project, "product": product,
        "own": own, "pool": pool, "order": order, "line": line, "core_so": core_so,
        "core_line": core_line, "order_row_id": str(order_row.id),
    }


def _held_reserve_world(db, *, qty="134", required_date=NON_IMMEDIATE, qty_delivered="0"):
    """A held Use-own (Reserve) line, no inquiry row raised - AC-B4's starting shape.
    Seeds exactly `qty` of own stock so the confirm has precisely enough to hold, the way
    S1's own "Use own X" shape would (any more and a later top-up test's "free stock"
    figure would quietly include some of THIS seed).

    `qty_delivered` (default "0"): PQ7's amend variant, see `_held_buy_world`'s own note."""
    company_id, actor, project, product = _world(db)
    _group, sites = _group_sites(db)
    own, pool = sites["BRW"]
    order, line, core_so, core_line = _seed_order(
        db, company_id, project, product, own, qty=qty, required_date=required_date,
        qty_delivered=qty_delivered,
    )
    _stock(db, product, own, on_hand=qty)
    ProjectSupplyService(db).confirm(
        order,
        ConfirmSupplyBody(lines=[ConfirmLine(
            project_line_id=line.id,
            reserve=[{"warehouse_id": str(own.id), "qty": qty}],
        )]),
        actor_user_id=actor,
    )
    db.commit()
    return {
        "company_id": company_id, "actor": actor, "project": project, "product": product,
        "own": own, "pool": pool, "order": order, "line": line, "core_so": core_so,
        "core_line": core_line,
    }


def _change_and_batch(db, world, *, new_qty=None, new_required_date=None):
    """Mutates the core line the way a book re-upload or a manual edit already would (the
    write happens BEFORE `build_batch` runs - `test_planning_changes.py`'s own
    `test_apply_returns_a_dropped_bystander_in_returned_to_review` docstring is explicit
    about this order of operations), then builds a batch off ONE Change for it."""
    core_so = world["core_so"]
    core_line = world["core_line"]
    old_qty = float(core_line.qty_ordered)
    old_date = core_line.required_date
    item_code = world["product"].product_code
    location = world["own"].warehouse_code

    if new_qty is not None:
        core_line.qty_ordered = Decimal(str(new_qty))
    if new_required_date is not None:
        core_line.required_date = new_required_date
    db.flush()

    kind = QTY_CHANGED if new_required_date is None else DATE_MOVED
    from app.services.scm.outstanding_diff import Line

    before = Line(doc_number=core_so.so_number, item_code=item_code, location=location,
                  qty=old_qty, required_date=old_date, row_ref=str(core_line.id))
    after = Line(
        doc_number=core_so.so_number, item_code=item_code, location=location,
        qty=float(core_line.qty_ordered), required_date=core_line.required_date,
        row_ref=str(core_line.id),
    )
    change = Change(kind, core_so.so_number, item_code, location, before=before, after=after)

    diff = Diff(scope_documents=(core_so.so_number,), changes=[change])
    batch = planning_change_service.build_batch(
        db, diff, applied_line_ids={id(change): str(core_line.id)},
        order_ids={core_so.so_number: str(core_so.id)},
        actor=world["actor"], import_job_id=None, file_name="book.xlsx",
    )
    db.commit()
    return batch


def _only_row(db, batch: PlanningChangeBatch) -> PlanningChangeRow:
    rows = db.query(PlanningChangeRow).filter_by(batch_id=batch.id).all()
    assert len(rows) == 1, [r.kind for r in rows]
    return rows[0]


def _confirm_and_apply(db, world, batch):
    planning_change_service.set_row_decision(db, str(batch.id), str(_only_row(db, batch).id),
                                              "confirm")
    db.commit()
    result = planning_change_service.apply(db, str(batch.id), world["actor"])
    db.commit()
    return result


def _live_order_rows(db, line_id):
    return (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line_id, OrderInquiryRow.verb == IV_ORDER,
                OrderInquiryRow.state != INQUIRY_CANCELLED)
        .all()
    )


def _hold_qty(db, line_id) -> Decimal:
    """The stock actually held for this line right now - `ProjectSupplyService._hold_query`'s
    own predicate (project_supply_service.py:7696-7767), restated for one line: a confirmed
    `SOLineAllocation` whose `source_type` is NOT `order` (a Buy allocation carries no
    warehouse and is not a stock hold at all) AND whose decision is either unset (every row
    written before Stage 1C) or ACTIVE - a superseded/challenged revision's rows must stop
    holding the moment the engine supersedes them. An earlier version of this helper summed
    every confirmed row with no decision-state filter at all, which double-counted a
    superseded revision alongside its replacement."""
    rows = (
        db.query(SOLineAllocation)
        .outerjoin(SOSupplyDecision, SOSupplyDecision.id == SOLineAllocation.decision_id)
        .filter(SOLineAllocation.so_line_id == line_id,
                SOLineAllocation.confirmed_at.isnot(None),
                SOLineAllocation.source_type != ALLOC_SOURCE_ORDER,
                (SOLineAllocation.decision_id.is_(None))
                | (SOSupplyDecision.state == DECISION_ACTIVE))
        .all()
    )
    return sum((Decimal(str(r.qty)) for r in rows), Decimal("0"))


def _add_stock(db, product, warehouse, extra):
    """Top up the ALREADY-SEEDED (product, warehouse) stock row rather than inserting a
    second one - `stock` carries `uq_stock_product_id_warehouse_id`, and `_held_reserve_world`
    already seeded one row for this pair when it confirmed the initial Reserve."""
    from app.models.inventory import Stock

    row = (
        db.query(Stock)
        .filter(Stock.product_id == product.id, Stock.warehouse_id == warehouse.id)
        .one()
    )
    row.quantity_on_hand = Decimal(str(row.quantity_on_hand)) + Decimal(str(extra))
    db.flush()
    return row


def _active_decision(db, order_id):
    return (
        db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == order_id,
                SOSupplyDecision.state == DECISION_ACTIVE)
        .one()
    )


# --------------------------------------------------------------------------- #
# AC-B1a: qty up on a held Buy proposes Buy for the WHOLE new quantity
# --------------------------------------------------------------------------- #

def test_qty_up_on_a_held_buy_proposes_buy_for_the_whole_new_quantity():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134")
        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None
        row = _only_row(db, batch)
        assert row.kind == "qty_up"
        composition = planning_change_service.composition_from_proposal(row.proposal_json)

    assert composition.get("buy_qty") == "234", composition
    assert composition.get("reserve") == [], composition
    assert composition.get("borrow") == [], composition


# --------------------------------------------------------------------------- #
# AC-B1b: apply of the qty_up row tops up the SAME inquiry row, in place
# --------------------------------------------------------------------------- #

def test_apply_of_a_qty_up_tops_up_the_same_inquiry_row():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134")
        original_row_id = world["order_row_id"]
        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        live_rows = _live_order_rows(db, world["line"].id)
        assert len(live_rows) == 1, [str(r.id) for r in live_rows]
        live_row = live_rows[0]
        # Identity, not just quantity - the exact defect this pins (today: cancel + raise).
        assert str(live_row.id) == original_row_id, (
            str(live_row.id), original_row_id,
        )
        assert live_row.qty == Decimal("234")
        assert live_row.previous_qty == Decimal("134")
        assert live_row.note and "Was 134" in live_row.note
        assert live_row.state == "raised"

        decision = _active_decision(db, world["order"].id)
        snapshot = next(
            s for s in decision.line_snapshots if s["project_line_id"] == str(world["line"].id)
        )
        assert Decimal(snapshot["buy_qty"]) == Decimal("234"), snapshot


# --------------------------------------------------------------------------- #
# AC-PQ7 (`PLAN-scm-planning-change-plan-qty.md`, issue #971): the same confirm-and-apply
# flow, on a line already carrying a delivery. A DELIVERED-VARIANT SIBLING rather than a
# blind `qty_delivered in (0, 638)` parametrize over the test above: its own 134/234 are
# too small to admit a real delivery of 638 without a "delivered more than ordered" edge,
# so this scales the same shape up (700 -> 900) to keep 638 a genuine PARTIAL delivery.
# --------------------------------------------------------------------------- #

def test_apply_of_a_qty_up_tops_up_the_same_inquiry_row_when_partly_delivered():
    """PQ7: `qty_delivered=638` on a held Buy line held at 700, book-changed to 900 (owed
    62 before the change, 262 after; plan 900 throughout). `_row_open_qty`'s bug (PLAN
    section 1) reads the OWED quantity rather than the plan one, so pre-fix
    `_confirm_and_apply` raises `AppException` (422 `planning_change_composition_
    mismatch`) confirming this row instead of reaching the assertions below."""
    with blank_session() as db:
        world = _held_buy_world(db, qty="700", qty_delivered="638")
        original_row_id = world["order_row_id"]
        batch = _change_and_batch(db, world, new_qty="900")
        assert batch is not None

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        live_rows = _live_order_rows(db, world["line"].id)
        assert len(live_rows) == 1, [str(r.id) for r in live_rows]
        live_row = live_rows[0]
        assert str(live_row.id) == original_row_id, (
            str(live_row.id), original_row_id,
        )
        assert live_row.qty == Decimal("900")
        assert live_row.previous_qty == Decimal("700")
        assert live_row.note and "Was 700" in live_row.note
        assert live_row.state == "raised"

        decision = _active_decision(db, world["order"].id)
        snapshot = next(
            s for s in decision.line_snapshots if s["project_line_id"] == str(world["line"].id)
        )
        assert Decimal(snapshot["buy_qty"]) == Decimal("900"), snapshot


# --------------------------------------------------------------------------- #
# AC-B1c: qty up never mixes Use own and Buy
# --------------------------------------------------------------------------- #

def test_qty_up_never_mixes_use_own_and_buy():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134")
        _stock(db, world["product"], world["own"], on_hand=100)
        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None
        row = _only_row(db, batch)
        composition = planning_change_service.composition_from_proposal(row.proposal_json)

    assert composition.get("buy_qty") == "234", composition
    assert composition.get("reserve") == [], composition


# --------------------------------------------------------------------------- #
# AC-B1d: own stock covering the WHOLE unit reserves it and cancels the Buy row
# --------------------------------------------------------------------------- #

def test_qty_up_with_own_stock_for_the_whole_unit_reserves_it_and_cancels_the_buy_row():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134")
        _stock(db, world["product"], world["own"], on_hand=300)
        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None
        row = _only_row(db, batch)
        composition = planning_change_service.composition_from_proposal(row.proposal_json)
        assert composition.get("buy_qty") == "0", composition
        assert sum(Decimal(r["qty"]) for r in composition.get("reserve") or []) == (
            Decimal("234")
        ), composition

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        live_rows = _live_order_rows(db, world["line"].id)
        assert live_rows == [], [str(r.id) for r in live_rows]

        decision = _active_decision(db, world["order"].id)
        snapshot = next(
            s for s in decision.line_snapshots if s["project_line_id"] == str(world["line"].id)
        )
        reserve_qty = sum(
            Decimal(c["qty"]) for c in snapshot["components"] if c["kind"] == "reserve"
        )
        assert reserve_qty == Decimal("234"), snapshot


# --------------------------------------------------------------------------- #
# AC-B2: a later donor holding the WHOLE unit on hand borrows whole, order-back
# --------------------------------------------------------------------------- #

def test_qty_up_with_a_later_donor_holding_the_whole_unit_borrows_whole_and_raises_order_back():
    with blank_session() as db:
        borrow_from_order_on(db)
        world = _held_buy_world(db, qty="134")
        donor_bin = _warehouse(db, f"ZZT-DONOR-{_uid()[:4]}")
        _stock(db, world["product"], donor_bin, on_hand=234)
        donor_order, donor_line, donor_core_so, donor_core_line = _seed_order(
            db, world["company_id"], world["project"], world["product"], donor_bin,
            qty=234, required_date=NON_IMMEDIATE + timedelta(days=50),
        )
        ProjectSupplyService(db).confirm(
            donor_order,
            ConfirmSupplyBody(lines=[ConfirmLine(
                project_line_id=donor_line.id,
                reserve=[{"warehouse_id": str(donor_bin.id), "qty": "234"}],
            )]),
            actor_user_id=world["actor"],
        )
        db.commit()

        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None
        row = _only_row(db, batch)
        composition = planning_change_service.composition_from_proposal(row.proposal_json)
        assert composition.get("borrow"), (
            "expected a Borrow composition off the donor's on-hand stock", composition,
        )
        assert sum(Decimal(b["qty"]) for b in composition.get("borrow") or []) == (
            Decimal("234")
        ), composition
        assert composition.get("buy_qty") == "0", composition

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        live_order_rows = _live_order_rows(db, world["line"].id)
        assert live_order_rows == [], [str(r.id) for r in live_order_rows]

        order_back = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == donor_line.id,
                    OrderInquiryRow.verb == IV_ORDER_BACK,
                    OrderInquiryRow.state != INQUIRY_CANCELLED)
            .one_or_none()
        )
        assert order_back is not None, "expected an ORDER_BACK row on the donor's line"
        assert order_back.qty == Decimal("234")


# --------------------------------------------------------------------------- #
# AC-B3a: advance into the immediate window - pool share up to its allowance,
# the remainder bought
# --------------------------------------------------------------------------- #

def test_advance_into_the_immediate_window_takes_pool_share_up_to_the_allowance_and_buys_the_rest():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134", required_date=NON_IMMEDIATE)
        # Default pool_share_pct (50%): 100 on hand in the pool -> allowance 50.
        _stock(db, world["product"], world["pool"], on_hand=100)

        batch = _change_and_batch(db, world, new_required_date=IMMEDIATE)
        assert batch is not None
        row = _only_row(db, batch)
        assert row.kind == "advanced"
        composition = planning_change_service.composition_from_proposal(row.proposal_json)

        pool_qty = sum(
            Decimal(r["qty"]) for r in (composition.get("reserve") or [])
            if r.get("warehouse_id") == str(world["pool"].id)
        )
        assert pool_qty == Decimal("50"), composition
        assert composition.get("buy_qty") == "84", composition

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        live_rows = _live_order_rows(db, world["line"].id)
        assert len(live_rows) == 1, [str(r.id) for r in live_rows]
        assert live_rows[0].qty == Decimal("84")
        assert live_rows[0].previous_qty == Decimal("134")
        assert live_rows[0].note and "Was 134" in live_rows[0].note


# --------------------------------------------------------------------------- #
# AC-B3b: advance into the immediate window - pool share covers EVERYTHING,
# the Buy row is cancelled
# --------------------------------------------------------------------------- #

def test_advance_into_the_immediate_window_where_pool_share_covers_everything_cancels_the_buy_row():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134", required_date=NON_IMMEDIATE)
        # 300 on hand at 50% -> allowance 150, comfortably covers the whole 134.
        _stock(db, world["product"], world["pool"], on_hand=300)

        batch = _change_and_batch(db, world, new_required_date=IMMEDIATE)
        assert batch is not None
        row = _only_row(db, batch)
        composition = planning_change_service.composition_from_proposal(row.proposal_json)
        assert composition.get("buy_qty") == "0", composition
        pool_qty = sum(Decimal(r["qty"]) for r in (composition.get("reserve") or []))
        assert pool_qty == Decimal("134"), composition

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        original_row = db.get(OrderInquiryRow, world["order_row_id"])
        assert original_row.state == INQUIRY_CANCELLED, original_row.state


# --------------------------------------------------------------------------- #
# AC-B3c: advanced but still outside the immediate window - no partial pool
# share, the Buy row is unchanged
# --------------------------------------------------------------------------- #

def test_advance_that_stays_outside_the_immediate_window_takes_no_partial_pool_share():
    with blank_session() as db:
        world = _held_buy_world(db, qty="134", required_date=NON_IMMEDIATE)
        # Same pool stock AC-B3a uses (allowance 50, well under 134) - immediate, this
        # would take a share; outside the window, R10 says whole or nothing.
        _stock(db, world["product"], world["pool"], on_hand=100)

        batch = _change_and_batch(db, world, new_required_date=STILL_OUTSIDE)
        assert batch is not None
        row = _only_row(db, batch)
        assert row.kind == "advanced"
        composition = planning_change_service.composition_from_proposal(row.proposal_json)
        assert composition.get("buy_qty") == "134", composition
        assert composition.get("reserve") == [], composition

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        live_rows = _live_order_rows(db, world["line"].id)
        assert len(live_rows) == 1, [str(r.id) for r in live_rows]
        assert live_rows[0].qty == Decimal("134")
        # NOT previous_qty is None: the captain's brief called this row "unchanged", but
        # the DATE still moved (NON_IMMEDIATE -> STILL_OUTSIDE) even though the quantity
        # did not, and `_settle_row_in_place`'s own `changed` test is `need != previous_qty
        # OR required_date != previous_date` - a pure date move still stamps `previous_qty`
        # (same value) alongside `previous_date`, exactly S9's own grill wording ("Keep,
        # the inquiry row carries DELAY 'Was 4 Sep'" for a Buy-held line whose QUANTITY
        # never changed). Corrected here rather than left asserting a value this scenario
        # cannot produce.
        assert live_rows[0].previous_qty == Decimal("134")
        assert live_rows[0].note and "Was 134" in live_rows[0].note


# --------------------------------------------------------------------------- #
# AC-B4a: qty up on a held Use-own takes MORE stock when the group has it
# --------------------------------------------------------------------------- #

def test_qty_up_on_a_held_use_own_takes_more_stock_when_the_group_has_it():
    with blank_session() as db:
        world = _held_reserve_world(db, qty="134")
        previous_decision_id = _active_decision(db, world["order"].id).id
        # 200 MORE free on top of the 134 already held - 334 on hand in total, enough to
        # cover the whole 234 the line moves up to.
        _add_stock(db, world["product"], world["own"], extra=200)

        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None
        row = _only_row(db, batch)
        assert row.kind == "qty_up"
        composition = planning_change_service.composition_from_proposal(row.proposal_json)
        assert composition.get("buy_qty") == "0", composition
        assert sum(Decimal(r["qty"]) for r in composition.get("reserve") or []) == (
            Decimal("234")
        ), composition

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        decision = _active_decision(db, world["order"].id)
        snapshot = next(
            s for s in decision.line_snapshots if s["project_line_id"] == str(world["line"].id)
        )
        reserve_qty = sum(
            Decimal(c["qty"]) for c in snapshot["components"] if c["kind"] == "reserve"
        )
        assert reserve_qty == Decimal("234"), snapshot
        # The real stock hold, not just the frozen JSON snapshot - `_hold_rows`' own read.
        assert _hold_qty(db, world["line"].id) == Decimal("234")
        # AC-B4c folds in here: no window where the line has neither a hold nor an
        # inquiry row - the hold above covers it whole, and Use-own raises no ORDER row.
        assert _live_order_rows(db, world["line"].id) == []
        # The previous revision's decision must stop holding stock the moment it is
        # replaced (`_hold_query`'s own reasoning: decision_id IS NULL OR state ==
        # DECISION_ACTIVE), AND its terminal state is SUPERSEDED only (Slice E, AC-E1/
        # AC-E2, issue #860): `challenge_if_drifted` is retired from `confirm`, so
        # `_write_decision`'s own `active_decision()` lookup finds the prior revision
        # still ACTIVE and correctly flips it, rather than finding nothing left to
        # supersede because a mid-`confirm()` challenge got there first.
        previous_decision = db.query(SOSupplyDecision).get(previous_decision_id)
        assert previous_decision.state == DECISION_SUPERSEDED, previous_decision.state


# --------------------------------------------------------------------------- #
# AC-B4b: qty up on a held Use-own moves the WHOLE unit to the next step when
# the group's stock is short
# --------------------------------------------------------------------------- #

def test_qty_up_on_a_held_use_own_moves_the_whole_unit_to_the_next_step_when_stock_is_short():
    with blank_session() as db:
        world = _held_reserve_world(db, qty="134")
        # Only 50 more free - 184 on hand in total, short of the 234 the line moves up to,
        # and no donor/pool seeded - the whole unit must move to Buy, never split.
        _add_stock(db, world["product"], world["own"], extra=50)

        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None
        row = _only_row(db, batch)
        assert row.kind == "qty_up"
        composition = planning_change_service.composition_from_proposal(row.proposal_json)
        assert composition.get("buy_qty") == "234", composition
        assert composition.get("reserve") == [], composition

        result = _confirm_and_apply(db, world, batch)
        assert result["failed_orders"] == [], result["failed_orders"]

        # The 134 hold is released - the whole unit moved to Buy, not 184 kept + 50 bought.
        assert _hold_qty(db, world["line"].id) == Decimal("0")
        live_rows = _live_order_rows(db, world["line"].id)
        assert len(live_rows) == 1, [str(r.id) for r in live_rows]
        assert live_rows[0].qty == Decimal("234")


# --------------------------------------------------------------------------- #
# Reviewer follow-up: a qty-up covered by a step-3 supply borrow (an SPO
# placed for nobody, arriving before the date) composes and confirms.
# --------------------------------------------------------------------------- #

def test_qty_up_covered_by_a_step_3_supply_borrow_composes_and_confirms():
    """A whole-unit cover from ladder v7.1 step 3 (`test_ladder_v7_supply_borrow.py`'s own
    `test_a_free_document_is_taken_rather_than_borrowed_and_owes_nobody` shape): an SPO
    nobody is waiting on, arriving well before the line's own date, beats buying. This is
    the first planning-change exercise of `composition_from_proposal`'s `supply_key`/
    `supply_document`/`arrival_date` carry-through (fixed for the group-borrow case in
    298388e72; step 3's OWN documents have not run through a planning change before)."""
    # Local dates, deliberately NOT `NON_IMMEDIATE`: step 3 is only reached when the
    # document lands AFTER the asker's own date yet still beats a fresh purchase
    # (`test_ladder_v7_supply_borrow.py`'s own module docstring, R32) - with a lead time
    # of 30 days, a required date 100 days out (`NON_IMMEDIATE`) already comfortably
    # outlives ANY fresh buy, so nothing ever needs to borrow a document for it (verified
    # empirically: that shape reads `kind="buy"`, "beyond the lead time window", every
    # stock rung skipped). Matching `test_ladder_v7_supply_borrow.py`'s own ASKER_DAY(20)/
    # LATE_ARRIVAL_DAY(25)/LEAD_DAYS(30) instead reproduces its rung exactly.
    asker_day = date.today() + timedelta(days=20)
    late_arrival = date.today() + timedelta(days=25)
    with blank_session() as db:
        world = _held_buy_world(db, qty="134", required_date=asker_day)
        _lead_time(db, world["product"], 30)
        # A different bin from the asking line's own - the document is free to nobody,
        # which is what routes it through step 3 rather than an ordinary reserve.
        donor_bin = _warehouse(db, f"ZZT-SPB-{_uid()[:4]}")
        allocation = _spo(db, world["product"], donor_bin, qty=234, arrives=late_arrival)

        batch = _change_and_batch(db, world, new_qty="234")
        assert batch is not None
        row = _only_row(db, batch)
        assert row.kind == "qty_up"
        composition = planning_change_service.composition_from_proposal(row.proposal_json)
        assert composition.get("buy_qty") == "0", composition
        borrow = composition.get("borrow") or []
        assert len(borrow) == 1, composition
        component = borrow[0]
        assert component["qty"] == "234", component
        assert component["supply_key"], component
        # The board's own sentence prefixes the document type ("SPO ...", `_spo_source_label`
        # or similar) - measured directly rather than assumed.
        assert component["supply_document"] == f"SPO {allocation.spo_number}", component
        assert component["arrival_date"] == late_arrival.isoformat(), component

        result = _confirm_and_apply(db, world, batch)
        # `_check_supply_borrow` refuses a partial-cover composition the same way every
        # other line-shape does; a whole-unit borrow like this one should confirm clean.
        assert result["failed_orders"] == [], result["failed_orders"]

        db.expire_all()
        links = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == str(allocation.id))
            .all()
        )
        assert [str(l.qty) for l in links] == ["234.0000"], links
        linked_row = db.get(OrderInquiryRow, links[0].row_id)
        assert linked_row.so_line_id == world["line"].id, (
            "the placement link should move to THIS line, not stay on nobody's row"
        )


def test_an_amended_reserve_posted_with_no_location_key_reads_back_carrying_one():
    """R4: an amend posted through the DECISION ROUTE (`PUT .../rows/{row_id}`) with a
    Reserve component `{"warehouse_id": <BRW id>, "qty": ...}` - no `location` key, the
    shape `ConfirmReserveComponent` accepts.

    `_validate_composition_shape` DOES backfill `location` onto the stored `composition_
    json` (measured: `fresh.composition_json["reserve"][0]["location"]` already reads
    "ZZTBRW-...") - the gap is on the WIRE. `app/schemas/planning_change.py`'s own
    `PlanningChangeRow.composition` is typed `Optional[ConfirmLine]`, the SAME schema the
    route accepts as INPUT (`ConfirmReserveComponent`: `warehouse_id` + `qty` only, no
    `location` field) - so the route's response coerces the stored dict back through the
    input shape and drops the location it just wrote, and a reader of the route's own
    response (never the row directly off the database) gets a KeyError.

    The composition must add up to the row's own `open_qty` (`_validate_composition_
    shape`'s 422 otherwise) - measured directly as the qty_up's WHOLE new quantity (149),
    not the 15-unit increment over what was already held, so the whole 149 is reserved at
    the one location rather than split across a kept 134 and a new 15.
    """
    from tests.test_planning_changes import BASE, _client, _restore

    with blank_session() as db:
        world = _held_reserve_world(db, qty="134")
        batch = _change_and_batch(db, world, new_qty="149")
        row = _only_row(db, batch)

        client, originals = _client(db, world["actor"])
        try:
            response = client.put(
                f"{BASE}/planning-changes/{batch.id}/rows/{row.id}",
                json={
                    "decision": "amend",
                    "composition": {
                        "project_line_id": str(world["line"].id),
                        "reserve": [
                            {"warehouse_id": str(world["own"].id), "qty": "149"}
                        ],
                    },
                },
            )
        finally:
            _restore(originals)
        assert response.status_code == 200, response.text

        wire_composition = response.json()["composition"]
        assert wire_composition["reserve"][0]["location"] == world["own"].warehouse_code, (
            wire_composition
        )


# --------------------------------------------------------------------------- #
# AC-PQ7 (`PLAN-scm-planning-change-plan-qty.md`, issue #971): the same amend-route shape
# as the test above, on a line already carrying a delivery. A DELIVERED-VARIANT SIBLING
# for the same reason `test_apply_of_a_qty_up_tops_up_the_same_inquiry_row_when_partly_
# delivered` is one, not a parametrize over the test above.
# --------------------------------------------------------------------------- #

def test_an_amended_reserve_posted_with_no_location_key_reads_back_carrying_one_when_partly_delivered():
    """PQ7: `qty_delivered=638` on a held Reserve line held at 700, book-changed to 900
    (owed 262 after the change, plan 900). The composition must add up to the PLAN
    quantity (900), not the owed one (262) - pre-fix `_row_open_qty` reads the owed one and
    this amendment is refused with a mismatch instead of reaching the 200 below."""
    from tests.test_planning_changes import BASE, _client, _restore

    with blank_session() as db:
        world = _held_reserve_world(db, qty="700", qty_delivered="638")
        batch = _change_and_batch(db, world, new_qty="900")
        row = _only_row(db, batch)

        client, originals = _client(db, world["actor"])
        try:
            response = client.put(
                f"{BASE}/planning-changes/{batch.id}/rows/{row.id}",
                json={
                    "decision": "amend",
                    "composition": {
                        "project_line_id": str(world["line"].id),
                        "reserve": [
                            {"warehouse_id": str(world["own"].id), "qty": "900"}
                        ],
                    },
                },
            )
        finally:
            _restore(originals)
        assert response.status_code == 200, response.text

        wire_composition = response.json()["composition"]
        assert wire_composition["reserve"][0]["location"] == world["own"].warehouse_code, (
            wire_composition
        )

