"""CONSIGN-DELIVERED-FIX: a consignment's goods are already at the customer (prod, 14 Oct).

Plan: documentation/plans/autocount/PLAN-consign-delivered-fix-14oct.md.

Owner rulings (14 Oct, 22:00): an AutoCount CG is a stock transfer to the CON location, so its
goods left the warehouse when it was issued. A CG line is DELIVERED in full at issue
(qty_delivered = Qty), and its TransferedQty is the qty already RETURNED (Consignment Return,
incl. 'CVT TO DO'), never the delivered qty. So a consignment is never open demand: not on the
board, not in Stock Debt, not in the outstanding report, not in the reorder run.

The orders ss#125 already projected the old way are re-projected from the stored raw documents
by `scripts/reproject_consignments.py`.

Reuses the consignment suite's builders and the DO / GRN suite's `env`. Every record is fake.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from app.models.order import SalesOrder, SalesOrderLine
from app.services.scm.demand import (
    demand_qty,
    horizon_committed_select_sql,
    is_open_demand,
)

from tests.test_ingest_autocount_consignments import (
    CG_INGEST,
    CR_INGEST,
    _cg,
    _cg_line,
    _cr,
    _cr_line,
    _line,
    _order,
)
from tests.test_ingest_autocount_do_grn import _records, env  # noqa: F401 - env is a fixture


# ======================================================================= projection
def test_a_consignment_is_delivered_in_full_at_issue_and_closed(env):
    res = env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0),
                                            _cg_line(981002, "ZZAC-P2", 5, transferred=0)])])
    assert _records(res)["db1:CSGN:980001"]["outcome"] == "created"
    order = _order(env)
    assert (order.doc_type, order.status) == ("consignment", "closed")
    for ref, qty in (("db1:CSGN:980001:981001", Decimal("10")),
                     ("db1:CSGN:980001:981002", Decimal("5"))):
        line = _line(env, ref)
        assert (line.qty_ordered, line.qty_delivered, line.qty_returned, line.line_status) == (
            qty, qty, Decimal("0"), "closed")


def test_transfered_qty_is_the_returned_qty_not_delivered(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=4)])])
    line = _line(env, "db1:CSGN:980001:981001")
    assert (line.qty_ordered, line.qty_delivered, line.qty_returned) == (
        Decimal("10"), Decimal("10"), Decimal("4"))
    assert _order(env).status == "closed"


def test_a_later_push_moves_returned_and_never_reopens(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0)])])
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=6)],
                             modified="2026-10-03T10:00:00.000")])
    line = _line(env, "db1:CSGN:980001:981001")
    assert (line.qty_delivered, line.qty_returned, line.line_status) == (
        Decimal("10"), Decimal("6"), "closed")
    # A return voided in AutoCount lowers TransferedQty, and returned follows it down.
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=2)],
                             modified="2026-10-04T10:00:00.000")])
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("2")


def test_returned_is_never_below_the_matched_return_documents(env):
    """The return documents and the CG's TransferedQty are two pushes of one fact; whichever
    lands first, returned reads the larger, so a CG not yet re-pushed still shows its return."""
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0)])])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 3, from_dtl=981001)])])
    line = _line(env, "db1:CSGN:980001:981001")
    assert (line.qty_delivered, line.qty_returned, line.line_status) == (
        Decimal("10"), Decimal("3"), "closed")


def test_returned_is_capped_at_ordered(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=12)])])
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("10")


def test_a_voided_consignment_is_still_cancelled(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0)])])
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0)], cancelled="T",
                             modified="2026-10-05T10:00:00.000")])
    assert _order(env).status == "cancelled"


# ======================================================================= demand readers
def _seed_consignment(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=1)])])
    line = _line(env, "db1:CSGN:980001:981001")
    assert line is not None
    return line


def test_a_consignment_is_not_open_demand_on_the_board_predicate(env):
    """Board (`project_fulfilment_board_service`), group netting and Stock Debt's candidate
    list all filter `SalesOrder.status == 'open'` with `is_open_demand()`."""
    line = _seed_consignment(env)
    hits = (env.db.query(SalesOrderLine.id, demand_qty())
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.id == line.id, SalesOrder.status == "open",
                    is_open_demand())
            .all())
    assert hits == []


def test_a_consignment_is_not_in_stock_debt_demand(env):
    from app.services.scm.stock_debt_service import StockDebtService

    line = _seed_consignment(env)
    demand = StockDebtService(env.db)._demand(
        [str(env.p1)], [str(env.wh1)], {str(env.wh1): "ZZAC-WH1"}, set())
    assert line.qty_ordered == Decimal("10")
    assert [d.so_number for rows in demand.values() for d in rows] == []


def test_a_consignment_is_not_in_the_outstanding_report(env):
    from app.services.outstanding_report_service import outstanding_report

    _seed_consignment(env)
    report = outstanding_report(env.db, product_code="ZZAC-P1")
    assert [r for r in report["so_rows"] if "ZZCG" in str(r)] == []
    assert not report["so_by_location"]


def test_a_consignment_is_not_in_the_reorder_committed_figure(env):
    """`reorder_run_service._planning_rows` reads `horizon_committed_select_sql`, whose book
    leg is `scm.committed_v`'s own retail leg."""
    _seed_consignment(env)
    rows = env.db.execute(
        text(horizon_committed_select_sql(demand_class="retail")),
        {"horizon": None, "horizon_start": None},
    ).mappings().all()
    assert not [r for r in rows if str(r["product_id"]) == str(env.p1)]


# ======================================================================= re-projection
def _as_ss125_left_it(env, transferred: int = 1):
    """The state prod holds since ss#125: the order open, delivered = TransferedQty."""
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=transferred)])])
    order = _order(env)
    env.db.execute(text("UPDATE sales_orders SET status = 'open' WHERE id = :id"),
                   {"id": order.id})
    env.db.execute(text(
        "UPDATE sales_order_lines SET qty_delivered = :t, qty_returned = 0, "
        "line_status = 'open' WHERE sales_order_id = :id"), {"id": order.id, "t": transferred})
    # Committed like the route's own writes, so the dry run's rollback cannot undo it.
    env.db.commit()
    env.db.expire_all()
    return order


def test_reproject_rewrites_a_stored_consignment_and_is_idempotent(env):
    from scripts import reproject_consignments

    _as_ss125_left_it(env, transferred=1)
    preview = reproject_consignments.run(env.db, env.company, dry_run=True)
    assert preview["docs"] == 1 and preview["changed"] == 1
    # The dry run wrote nothing.
    assert _order(env).status == "open"

    summary = reproject_consignments.run(env.db, env.company, dry_run=False, commit=False)
    assert summary["changed"] == 1
    assert _order(env).status == "closed"
    line = _line(env, "db1:CSGN:980001:981001")
    assert (line.qty_delivered, line.qty_returned, line.line_status) == (
        Decimal("10"), Decimal("1"), "closed")

    again = reproject_consignments.run(env.db, env.company, dry_run=False, commit=False)
    assert again["changed"] == 0


def test_reproject_keeps_a_voided_consignment_cancelled(env):
    from scripts import reproject_consignments

    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0)])])
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0)], cancelled="T",
                             modified="2026-10-05T10:00:00.000")])
    reproject_consignments.run(env.db, env.company, dry_run=False, commit=False)
    assert _order(env).status == "cancelled"

