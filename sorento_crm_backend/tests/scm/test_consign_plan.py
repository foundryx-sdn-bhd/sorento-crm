"""CONSIGN-PLAN (`documentation/plans/scm/PLAN-consign-plan-15oct.md`), UAC-01..09.

Written test-first, before the code. The owner's rule (15-16 Oct 2026): a consignment is
planned like a sales order, but its goods sit at CON, a bin that is flagged OUT of fulfilment
planning. So for a consignment line only, the "Outside fulfilment planning" verdict is lifted,
CON is offered under Borrow as the line's own location (BORROW-OWN-LOC), Confirm writes a
decision with no Buy and no purchasing row, and the plan quantity nets `qty_returned`. A sales
order line never sees CON.

Postgres via `blank_session`; every chain seeded here (CI's database is empty). Board fixtures
come from `tests/test_fulfilment_board.py`, confirm fixtures from the ladder suite, as in
`test_borrow_own_location.py`.

UAC-01, 04, 05 and 08 describe behaviour main already has; they are guards and may be green.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.order import SalesOrderLine
from app.models.project_so import OrderInquiryRow, ProjectSalesOrderLine, SOSupplyDecision
from app.models.project_so import ProjectSalesOrder
from app.services.project_so_adoption_service import ProjectSOAdoptionService
from app.services.project_supply_service import ProjectSupplyService, plan_qty_of
from app.services.scm.demand import plan_qty
from app.services.scm.planning_predicate import OUTSIDE_FULFILMENT_PLANNING
from app.services.scm.sales_order_service import SalesOrderService

from .._pg_fixture import blank_session
from ..test_fulfilment_board import (
    TODAY,
    _line,
    _order,
    _product,
    _service,
    _stock as _board_stock,
    _uid,
    _warehouse as _board_warehouse,
)
from ..test_so_supply_confirmation import _stock, _warehouse
from .test_borrow_own_location import _confirm, _own_borrow
from .test_project_supply_service_ladder import (
    REQUIRED_DATE as LADDER_REQUIRED_DATE,
    _core_line,
    _core_so,
    _group_sites,
    _seed_line,
    _world,
)

REQUIRED = date(2026, 9, 3)


# --------------------------------------------------------------------------- helpers


def _planned_state(db, core_so):
    rows = SalesOrderService(db).with_planning_state([{"id": str(core_so.id)}])
    return rows[0]["planned_lines"], rows[0]["plannable_lines"]


def _contributions(board, so_number):
    return [
        c
        for cell in board["cells"]
        for c in cell["contributions"]
        if c["so_number"] == so_number
    ]


def _board_world(db, *, doc_type="consignment", demand_class="project", status="closed",
                 qty="10", delivered="10", returned="0", con_stock=10,
                 planned_stock=10):
    """A product, the flagged-off CON bin, a planned -BB bin, and one order of one line at CON.

    Mirrors AutoCount's consignment location: CON is not one of the -BB/-IB/-IR/-NTC/-AM
    planned bins, so it is active with `fulfilment_planning` false.
    """
    sfx = _uid()[:6]
    product = _product(db, f"ZZT-{sfx}")
    con = _board_warehouse(db, f"CON-{sfx}")
    con.fulfilment_planning = False
    planned = _board_warehouse(db, f"{sfx}-BB")
    db.flush()
    _board_stock(db, product, con, on_hand=con_stock)
    _board_stock(db, product, planned, on_hand=planned_stock)
    order = _order(
        db, so_number=f"CG-{sfx}-{_uid()[:4]}", order_date=date(2026, 1, 1),
        demand_class=demand_class, status=status,
    )
    order.doc_type = doc_type
    line = _line(
        db, order, product, qty=qty, required_date=REQUIRED, warehouse=con,
        delivered=delivered,
    )
    line.qty_returned = Decimal(returned)
    db.flush()
    return {"sfx": sfx, "product": product, "con": con, "planned": planned,
            "order": order, "line": line}


def _build(db, world, extra=()):
    numbers = [world["order"].so_number, *extra]
    return _service(db).build(numbers, granularity="week", as_of=TODAY)


def _consign_world(db, *, qty="10", returned="0", con_stock=10):
    """The confirm chain: a core consignment (closed, delivered, project class) at CON, its
    project mirror order, and CON stock."""
    company_id, eling, project, product = _world(db)
    sfx = _uid()[:6]
    con = _warehouse(db, f"CON-{sfx}", fulfilment_planning=False)
    _stock(db, product, con, on_hand=con_stock)
    order, line, core_so, core_line = _seed_line(
        db, company_id, project, product, con, qty_ordered=qty, line_no=1,
    )
    core_so.doc_type = "consignment"
    core_so.status = "closed"
    core_line.qty_delivered = Decimal(qty)
    core_line.qty_returned = Decimal(returned)
    order.so_id = core_so.id
    db.commit()
    return {
        "eling": eling, "product": product, "own": con, "pool": None,
        "order": order, "line": line, "core_so": core_so, "core_line": core_line,
    }


# --------------------------------------------------------------------------- UAC-01


def test_uac01_a_closed_delivered_project_consignment_is_admitted_to_the_board():
    with blank_session() as db:
        world = _board_world(db)
        board = _build(db, world)
        rows = _contributions(board, world["order"].so_number)

    assert len(rows) == 1, "one row per undecided line"
    assert rows[0]["so_doc_type"] == "consignment"


# --------------------------------------------------------------------------- UAC-02


def test_uac02_the_consignment_row_does_not_read_outside_fulfilment_planning():
    with blank_session() as db:
        world = _board_world(db)
        board = _build(db, world)
        row = _contributions(board, world["order"].so_number)[0]
        reasons = [s.get("reason") for s in row.get("sources", [])]

    assert row["unplannable"] is False, row.get("sources")
    assert OUTSIDE_FULFILMENT_PLANNING not in reasons


def test_uac02_the_supply_service_has_no_unplannable_reason_for_it():
    with blank_session() as db:
        world = _consign_world(db)
        service = ProjectSupplyService(db)
        facts = service._facts_for(world["order"], service.lines_of(str(world["order"].id)))
        fact = next(iter(facts.values()))

    assert fact.unplannable_reason is None


# --------------------------------------------------------------------------- UAC-03


def test_uac03_borrow_candidates_include_con_as_a_plain_location_row():
    with blank_session() as db:
        world = _board_world(db)
        board = _build(db, world)
        row = _contributions(board, world["order"].so_number)[0]
        con_rows = [
            c for c in row["borrow_candidates"]
            if c.get("warehouse_id") == str(world["con"].id)
        ]

    assert len(con_rows) == 1, row["borrow_candidates"]
    assert con_rows[0]["source"] == "other_location"
    assert con_rows[0]["warehouse_code"] == world["con"].warehouse_code
    assert not con_rows[0].get("donor_so_number")


# --------------------------------------------------------------------------- UAC-04


def test_uac04_a_sales_order_line_never_sees_con_stock():
    with blank_session() as db:
        # The -BB bin holds nothing for the sales order line, so the ladder wants other
        # stock; CON holds 10 and must still stay out of reach.
        world = _board_world(db, planned_stock=0)
        so = _order(
            db, so_number=f"SO-{world['sfx']}-{_uid()[:4]}", order_date=date(2026, 1, 1),
            status="open",
        )
        _line(db, so, world["product"], qty="10", required_date=REQUIRED,
              warehouse=world["planned"])
        board = _build(db, world, extra=[so.so_number])
        row = _contributions(board, so.so_number)[0]
        con_code = world["con"].warehouse_code
        con_id = str(world["con"].id)
        candidate_ids = [c.get("warehouse_id") for c in row["borrow_candidates"]]
        candidate_codes = [c.get("warehouse_code") for c in row["borrow_candidates"]]
        source_locations = [s.get("location") for s in row.get("sources", [])]

    assert con_id not in candidate_ids, row["borrow_candidates"]
    assert con_code not in candidate_codes
    assert con_code not in source_locations, "the proposal draws nothing from CON"


# --------------------------------------------------------------------------- UAC-05


def test_uac05_a_sales_order_line_at_a_flagged_off_bin_still_reads_outside_planning():
    with blank_session() as db:
        world = _board_world(db, doc_type="sales_order", status="open", delivered="0")
        board = _build(db, world)
        row = _contributions(board, world["order"].so_number)[0]
        reasons = [s.get("reason") for s in row.get("sources", [])]

    assert row["unplannable"] is True
    assert OUTSIDE_FULFILMENT_PLANNING in reasons


# --------------------------------------------------------------------------- UAC-06


def test_uac06_confirming_borrow_from_con_succeeds_with_no_buy_and_no_purchasing_row():
    with blank_session() as db:
        world = _consign_world(db)
        body = _confirm(db, world, borrow=[_own_borrow(world, "10", "Goods are the CON stock.")])
        decision = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == world["order"].id,
                SOSupplyDecision.state == "active",
            )
            .one()
        )
        buys = [Decimal(str(s.get("buy_qty") or 0)) for s in decision.line_snapshots]
        inquiry_rows = (
            db.query(OrderInquiryRow)
            .join(ProjectSalesOrderLine, ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id)
            .filter(ProjectSalesOrderLine.id == world["line"].id)
            .count()
        )
        planned, plannable = _planned_state(db, world["core_so"])

    assert body["lines_decided"] == 1
    assert len(buys) == 1 and buys == [Decimal("0")]
    assert inquiry_rows == 0, "nothing goes to purchasing"
    assert (planned, plannable) == (1, 1)


# --------------------------------------------------------------------------- UAC-07


def test_uac07_the_board_plans_ordered_less_returned():
    with blank_session() as db:
        world = _board_world(db, qty="10", delivered="10", returned="4")
        board = _build(db, world)
        row = _contributions(board, world["order"].so_number)[0]

    assert Decimal(row["qty"]) == Decimal("6")


def test_uac07_a_fully_returned_line_is_not_admitted_and_not_plannable():
    with blank_session() as db:
        world = _board_world(db, qty="10", delivered="10", returned="10")
        board = _build(db, world)
        rows = _contributions(board, world["order"].so_number)
        planned, plannable = _planned_state(db, world["order"])

    assert rows == []
    assert (planned, plannable) == (0, 0)


def test_uac07_plan_qty_of_nets_returns_and_leaves_sales_order_lines_alone():
    def line(returned):
        return SimpleNamespace(
            line_status="open", qty_required=None, qty_ordered=Decimal("10"),
            qty_cancelled=Decimal("0"), qty_returned=Decimal(returned),
        )

    assert plan_qty_of(line("4")) == Decimal("6")
    assert plan_qty_of(line("0")) == Decimal("10")
    assert plan_qty_of(line("12")) == Decimal("0"), "floored at zero"
    required = line("4")
    required.qty_required = Decimal("8")
    assert plan_qty_of(required) == Decimal("4"), "qty_required leads, then less returned"


def test_uac07_the_sql_plan_qty_nets_returns_too():
    with blank_session() as db:
        returned = _board_world(db, qty="10", delivered="10", returned="4")
        plain = _board_world(db, doc_type="sales_order", status="open", delivered="0",
                             returned="0")
        got_returned = db.query(plan_qty()).filter(
            SalesOrderLine.id == returned["line"].id).scalar()
        got_plain = db.query(plan_qty()).filter(
            SalesOrderLine.id == plain["line"].id).scalar()

    assert Decimal(got_returned) == Decimal("6")
    assert Decimal(got_plain) == Decimal("10")


# --------------------------------------------------------------------------- UAC-08


def test_uac08_a_retail_class_consignment_is_not_admitted():
    with blank_session() as db:
        world = _board_world(db, demand_class="retail")
        board = _build(db, world)
        rows = _contributions(board, world["order"].so_number)

    assert rows == []


# --------------------------------------------------------------------------- UAC-09
#
# Not tested here: `planning_reset_service.reset_planning` issues raw SQL against a hard-coded
# `projects.` schema, which `blank_session`'s scratch schemas cannot redirect (it would read and
# write the REAL projects schema). The service has no doc-type filter, so a consignment resets
# exactly like a sales order; the browser pass exercises Reset planning on a consignment.


# --------------------------------------------------------------------------- never adopted


def test_a_never_adopted_consignment_adopts_then_confirms_a_con_borrow_with_no_buy():
    """Real consignments arrive from AutoCount with no planning record. The board press
    adopts first (`/fulfilment-planning/adopt`, then confirm on the new mirror), so this
    runs that same pair: adopt the closed project-class consignment at flagged-off CON, then
    confirm a Borrow from CON."""
    with blank_session() as db:
        company_id, eling, _project, product = _world(db)
        sfx = _uid()[:6]
        con = _warehouse(db, f"CON-{sfx}", fulfilment_planning=False)
        _stock(db, product, con, on_hand=10)
        core_so = _core_so(db, company_id)
        core_so.doc_type = "consignment"
        core_so.status = "closed"
        core_line = _core_line(db, core_so, product, con, qty_ordered="10",
                               required_date=LADDER_REQUIRED_DATE)
        core_line.qty_delivered = Decimal("10")
        db.commit()
        assert db.query(ProjectSalesOrder).filter(
            ProjectSalesOrder.so_id == core_so.id).count() == 0

        adopted = ProjectSOAdoptionService(db).adopt(str(core_so.id), actor_user_id=eling)
        db.commit()
        order = db.query(ProjectSalesOrder).filter(
            ProjectSalesOrder.id == adopted["project_sales_order_id"]).one()
        line = (
            db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.project_sales_order_id == order.id)
            .one()
        )
        world = {"eling": eling, "product": product, "own": con, "pool": None,
                 "order": order, "line": line}
        body = _confirm(db, world, borrow=[_own_borrow(world, "10", "Goods are the CON stock.")])
        inquiry_rows = (
            db.query(OrderInquiryRow).filter(OrderInquiryRow.so_line_id == line.id).count()
        )
        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == order.id,
                    SOSupplyDecision.state == "active")
            .one()
        )
        buys = [Decimal(str(x.get("buy_qty") or 0)) for x in decision.line_snapshots]
        planned, plannable = _planned_state(db, core_so)

    assert body["lines_decided"] == 1
    assert buys == [Decimal("0")]
    assert inquiry_rows == 0, "nothing goes to purchasing"
    assert (planned, plannable) == (1, 1)
