"""The multi-order planning board (PLAN-fulfilment-planning-from-autocount-so section 13).

TEST-FIRST: every assertion below is written from the contract the Phase 1 mock was built
against (`_shared/types/fulfilmentPlanning.types.ts` and `_shared/lib/fulfilmentBoard.ts`),
before the service exists.

What the board is: dates across the top, products down the side, one cell per (product, date
bucket) anybody owes, with the contributing sales-order lines underneath it. It is a LENS - it
writes nothing - so every test here reads.

The four things worth arguing about, and so the four things pinned here:

  * which column a line lands in (13.3): its OWN period, past or future, with No date pinned
    last as the only column that is not a period. There is no aggregate for the past - an
    earlier build had one and it swallowed 160 of 160 lines into a single column - so what a
    past date carries instead is a flag (`is_past`) on its bucket, its cell count and its line;
  * that several orders owing one product by one date aggregate into ONE cell;
  * that scarce free stock is served per (product, LOCATION) and never across - moving stock
    between locations is a transfer, and a non-goal here (13.7);
  * that the row which loses the contest is REPORTED as contested rather than being shown a
    clean Reserve that the confirmation would later refuse (13.5.1).

Postgres, blank scratch schema, every FK target seeded here (PRINCIPLES).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.inventory import Stock, Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.scm import PriorityPolicy
from app.models.user import User
from app.services.scm import priority

from ._pg_fixture import blank_session
from ._borrow_mode import borrow_from_order_on

MARKER = "zzt-board"
BASE = "/api/v1/project-sales"
VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"

TODAY = date(2026, 8, 18)


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _mocha(db) -> str:
    """A SECOND company, for the one case that needs two: `products` is unique on
    `(company_id, product_code)`, so two products behind one item code - the live
    `B2155-NL-BLUE` pair - can only be seeded across companies."""
    from app.models.company import Company

    row = Company(id=_uid(), name=f"{MARKER} Mocha", code=f"ZM{_uid()[:8]}")
    db.add(row)
    db.flush()
    return row.id


def _ensure_payment_terms_column(db) -> None:
    """`customers.payment_terms_days` is in the production database but not on the ORM model.

    So a scratch schema built from the models alone does not have it, and the credit factor
    would be absent here for a reason that has nothing to do with the behaviour under test.
    The DDL is idempotent and inside the rolled-back transaction, exactly like importing a
    migration and running its `upgrade()` in one.
    """
    db.execute(
        text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS payment_terms_days integer")
    )


def _customer(db, name: str, *, terms: int | None = None) -> Customer:
    row = Customer(
        id=_uid(),
        customer_code=f"ZZT-{_uid()[:8]}",
        customer_name=name,
    )
    db.add(row)
    db.flush()
    if terms is not None:
        _ensure_payment_terms_column(db)
        db.execute(
            text("UPDATE customers SET payment_terms_days = :t WHERE id = :id"),
            {"t": terms, "id": str(row.id)},
        )
    return row


def _product(db, code: str) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=f"{MARKER} {code}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(db, code: str) -> Warehouse:
    row = Warehouse(
        id=_uid(), warehouse_code=code, warehouse_name=code, is_active=True,
        segment="project", fulfilment_planning=True,
    )
    db.add(row)
    db.flush()
    return row


def _stock(db, product: Product, warehouse: Warehouse, *, on_hand: int, reserved: int = 0):
    row = Stock(
        id=_uid(),
        product_id=product.id,
        warehouse_id=warehouse.id,
        quantity_on_hand=on_hand,
        quantity_reserved=reserved,
    )
    db.add(row)
    db.flush()
    return row


def _order(
    db,
    *,
    so_number: str,
    customer: Customer | None = None,
    order_date: date | None = None,
    demand_class: str = "project",
    status: str = "open",
) -> SalesOrder:
    row = SalesOrder(
        id=_uid(),
        so_number=so_number,
        customer_id=customer.id if customer else None,
        order_date=order_date,
        demand_class=demand_class,
        status=status,
        internal_note=f"Order Inquiry project: {MARKER} tower",
    )
    db.add(row)
    db.flush()
    return row


def _line(
    db,
    order: SalesOrder,
    product: Product,
    *,
    qty: str,
    required_date: date | None,
    warehouse: Warehouse | None,
    delivered: str = "0",
    line_status: str = "open",
    purchasing_status: str = "not_reviewed",
) -> SalesOrderLine:
    row = SalesOrderLine(
        id=_uid(),
        sales_order_id=order.id,
        product_id=product.id,
        warehouse_id=warehouse.id if warehouse else None,
        qty_ordered=Decimal(qty),
        qty_delivered=Decimal(delivered),
        required_date=required_date,
        line_status=line_status,
        purchasing_status=purchasing_status,
    )
    db.add(row)
    db.flush()
    return row


def _policy(
    db, factors: dict, class_weights: dict | None = None, *, name: str | None = None,
    active: bool = True, overdue_grace_days: int | None = None,
    overdue_dead_days: int | None = None,
) -> PriorityPolicy:
    row = PriorityPolicy(
        id=_uid(),
        name=name or f"{MARKER}-policy-{_uid()[:6]}",
        is_active=active,
        factors=factors,
        demand_class_weights=class_weights or {"project": 1.0},
        # None leaves the column's own SHIPPED default (0 / 0, captain's ruling 3 Sep
        # 2026) - a caller proving R-O's grace RULE passes both explicitly.
        **(
            {"overdue_grace_days": overdue_grace_days}
            if overdue_grace_days is not None else {}
        ),
        **(
            {"overdue_dead_days": overdue_dead_days}
            if overdue_dead_days is not None else {}
        ),
    )
    db.add(row)
    db.flush()
    return row


def _service(db):
    from app.services.project_fulfilment_board_service import FulfilmentBoardService

    return FulfilmentBoardService(db)


def _cell(board, item_code: str, bucket_key: str) -> dict:
    return next(
        c for c in board["cells"]
        if c["item_code"] == item_code and c["bucket_key"] == bucket_key
    )


# --------------------------------------------------------------------------- #
# 13.3 - which column a line lands in
# --------------------------------------------------------------------------- #


def test_bucket_assignment_for_day_week_and_month():
    from app.services.project_fulfilment_board_service import bucket_key_for

    # A Thursday, so the week bucket has to be the Monday before it and not the date itself.
    when = date(2026, 9, 3)
    assert bucket_key_for(when, TODAY, "day") == "2026-09-03"
    assert bucket_key_for(when, TODAY, "week") == "2026-08-31"
    assert bucket_key_for(when, TODAY, "month") == "2026-09-01"
    # AC-B1-9 (`PLAN-board-oi-mechanical-22sep.md`, S1): `date` keys exactly like `day` -
    # the line's own required date, never a week/month bucket. Today `bucket_key_for` only
    # special-cases `"month"` and `"day"`, so an unrecognised granularity value falls
    # through to the week branch - red on the mismatch below, not on a missing symbol.
    assert bucket_key_for(when, TODAY, "date") == bucket_key_for(when, TODAY, "day")


def test_a_past_date_keeps_its_own_period_and_only_no_date_is_special():
    """The captain, on seeing 160 of 160 lines collapse into one column: "don't put overdue
    together, still split by the date, don't put under overdue".

    So a date in the past buckets by ITS OWN period, exactly like a future one. The only
    non-period column left is No date, because an absent date is a different thing from an old
    one and there is no period to put it in.
    """
    from app.services.project_fulfilment_board_service import bucket_key_for

    long_ago = date(2022, 7, 6)  # a Wednesday, so the week key must be the Monday before it
    assert bucket_key_for(long_ago, TODAY, "day") == "2022-07-06"
    assert bucket_key_for(long_ago, TODAY, "week") == "2022-07-04"
    assert bucket_key_for(long_ago, TODAY, "month") == "2022-07-01"
    for granularity in ("day", "week", "month"):
        assert bucket_key_for(None, TODAY, granularity) == "no_date"


def test_no_aggregate_bucket_exists_and_a_years_old_line_keeps_its_own_column():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="5", required_date=date(2022, 7, 6), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2025, 6, 15), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=None, warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="month", as_of=TODAY)

        keys = [bucket["key"] for bucket in board["dateBuckets"]]
        assert "overdue" not in keys, "the aggregate column is gone, not renamed"
        # Chronological, earliest first, and the two past months are two columns.
        assert keys == ["2022-07-01", "2025-06-01", "2026-09-01", "no_date"]
        kinds = {b["key"]: b["kind"] for b in board["dateBuckets"]}
        assert kinds["2022-07-01"] == "dated"
        assert kinds["no_date"] == "no_date"
        # And the line is IN its own column, carrying its own real date.
        cell = _cell(board, product.product_code, "2022-07-01")
        assert cell["total_qty"] == "5"
        assert cell["contributions"][0]["required_date"] == date(2022, 7, 6)


def test_a_bucket_whose_period_has_ended_says_so_and_the_current_one_does_not():
    """The information the Overdue column used to carry, without the lumping.

    A bucket is past when its whole period ended before the as-of date. The period CONTAINING
    the as-of date is not past: some of its dates are still to come, and tinting it would tell
    the planner this week is already lost.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        # TODAY is 2026-08-18, a Tuesday, so its week runs 17 to 23 August.
        _line(db, order, product, qty="5", required_date=date(2025, 6, 15), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2026, 8, 17), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=None, warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        past = {bucket["key"]: bucket["is_past"] for bucket in board["dateBuckets"]}
        assert past["2025-06-09"] is True
        assert past["2026-08-17"] is False, "the week we are in has not ended"
        assert past["2026-08-31"] is False  # w/c 31 Aug, which holds the 3 September line
        assert past["no_date"] is False, "no date is not a date that has passed"


def test_every_contribution_says_whether_its_own_date_has_passed():
    """What the "160 of 160 lines are past their delivery date" summary counts.

    Per LINE, not per bucket: the line dated 17 August is past even though the week it sits in
    has not ended, and a summary built off the bucket flag alone would miss it.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="5", required_date=date(2026, 8, 17), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2026, 8, 19), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=None, warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        by_date = {
            contribution["required_date"]: contribution["is_past"]
            for cell in board["cells"]
            for contribution in cell["contributions"]
        }
        assert by_date[date(2026, 8, 17)] is True
        assert by_date[date(2026, 8, 19)] is False
        assert by_date[None] is False, "an absent date has not passed; it is simply absent"
        # And the cell counts them, so the summary needs no second pass over contributions.
        this_week = _cell(board, product.product_code, "2026-08-17")
        assert this_week["past_count"] == 1


def test_day_granularity_is_a_thirty_day_window_not_a_column_per_distinct_date():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)
        # Two years out: far outside the window, and it must not stretch the axis to reach it.
        _line(db, order, product, qty="5", required_date=date(2028, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="day", as_of=TODAY)

        dated = [b for b in board["dateBuckets"] if b["kind"] == "dated"]
        assert len(dated) == 30
        assert dated[0]["key"] == "2026-09-03"
        # Empty days inside the window are still columns: a calendar that hides its empty days
        # is not a calendar, and the gap is the information.
        assert dated[1]["key"] == "2026-09-04"


# --------------------------------------------------------------------------- #
# S1 (`PLAN-board-oi-mechanical-22sep.md`) - AC-B1-6/7/8: `date` granularity
# --------------------------------------------------------------------------- #


def test_date_granularity_bucket_keys_are_required_date_iso_and_only_populated_dates():
    """AC-B1-6/AC-B1-9. `date` granularity keys each bucket by the line's own
    `required_date` ISO string - never a week bucket, never the day-window's 30-column
    calendar fill - and `dateBuckets` lists only the dates somebody actually owes,
    sorted ascending with `No date` last. `granularity="date"` is not in `GRANULARITIES`
    today and `bucket_key_for` does not special-case it, so it falls through to the week
    branch: three lines a week apart collapse onto fewer than three columns, and none of
    those columns' keys match the lines' own dates - red on the mismatch, not on a
    missing route (the service is called directly, the same way every other test in
    this file calls it)."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="5", required_date=date(2026, 11, 1), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2027, 2, 1), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2027, 4, 1), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=None, warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="date", as_of=TODAY)

        keys = [b["key"] for b in board["dateBuckets"]]
        assert keys == ["2026-11-01", "2027-02-01", "2027-04-01", "no_date"], keys
        for bucket in board["dateBuckets"]:
            assert "is_past" in bucket

        cell = _cell(board, product.product_code, "2026-11-01")
        assert cell["contributions"][0]["required_date"] == date(2026, 11, 1)


def test_date_granularity_label_is_dd_mm_yyyy():
    """AC-B1-7. `date` granularity's own bucket label is `DD/MM/YYYY` - not the week's
    `w/c ...` phrasing `_bucket_label` falls back to for any granularity value it does
    not recognise (only `"month"` and `"day"` are special-cased today)."""
    from app.services.project_fulfilment_board_service import _bucket_label

    assert _bucket_label("2026-11-01", "date") == "01/11/2026"


def test_day_granularity_unchanged():
    """AC-B1-8, pinned: `day` granularity is untouched by the new `date` option.
    `test_day_granularity_is_a_thirty_day_window_not_a_column_per_distinct_date` above
    already covers the 30-column window end to end; this is the narrower `bucket_key_
    for` pin the AC names directly."""
    from app.services.project_fulfilment_board_service import bucket_key_for

    when = date(2026, 9, 3)
    assert bucket_key_for(when, TODAY, "day") == "2026-09-03"


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #


def test_several_orders_owing_one_product_by_one_date_aggregate_into_one_cell():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        first = _order(db, so_number="ZZT-SO-AAA1", order_date=date(2026, 1, 1))
        second = _order(db, so_number="ZZT-SO-BBB2", order_date=date(2026, 2, 1))
        third = _order(db, so_number="ZZT-SO-CCC3", order_date=date(2026, 3, 1))
        _line(db, first, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, second, product, qty="20", required_date=date(2026, 9, 4), warehouse=warehouse)
        _line(db, third, product, qty="30", required_date=date(2026, 9, 2), warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-AAA1", "ZZT-SO-BBB2", "ZZT-SO-CCC3"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        assert cell["total_qty"] == "60"
        assert len(cell["contributions"]) == 3
        assert {c["so_number"] for c in cell["contributions"]} == {
            "ZZT-SO-AAA1", "ZZT-SO-BBB2", "ZZT-SO-CCC3"
        }
        # One row per product, and one standing per selected order.
        assert [row["item_code"] for row in board["productRows"]] == [product.product_code]
        assert {o["so_number"] for o in board["orders"]} == {
            "ZZT-SO-AAA1", "ZZT-SO-BBB2", "ZZT-SO-CCC3"
        }


def test_the_contribution_key_is_the_one_the_frontend_recomputes():
    """The FE rebuilds this key to count what the planner has decided (`standingsFor`).

    So it is part of the contract, not an implementation detail: `${sales_order_id}|${line_no}|
    ${item_code}|${bucket_key}`.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = board["cells"][0]["contributions"][0]
        assert contribution["key"] == (
            f"{order.id}|{contribution['line_no']}|{product.product_code}|2026-08-31"
        )


def test_only_undecided_lines_reach_the_board():
    """AC-S2-12, rewritten from `test_only_open_demand_of_an_open_project_order_reaches_the
    _board` for the 14 September 2026 ruling.

    THE BOARD ASKS WHO DECIDED, NOT WHETHER DELIVERY IS OUTSTANDING. A line the book says
    shipped, that nobody ever sourced, is a unit to put back - so it is on the board at its
    ORDERED quantity, and the closed-by-delivery line with it. What is still out is what
    somebody RULED on: `purchasing_status = covered` is a person saying no purchase is
    needed, and a cancelled line is not owed at all.

    `is_open_demand()` is untouched (AC-S2-11). The netting engine, the reorder plan and the
    worklist go on answering "what is still owed"; only this predicate answers "what is
    undecided", and the two diverge on purpose.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        # Still owed, nobody has decided it.
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
        # Shipped whole and CLOSED by that delivery. Undecided, so it is demand here - and at
        # 10, the ordered quantity, not at the 0 that is still owed.
        _line(
            db, order, product, qty="10", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="10", line_status="closed",
        )
        # A person ruled "no purchase needed": decided, and not the board's business.
        _line(
            db, order, product, qty="7", required_date=date(2026, 9, 3),
            warehouse=warehouse, purchasing_status="covered",
        )
        # Cancelled: owed to nobody, whatever the delivered column says.
        _line(
            db, order, product, qty="9", required_date=date(2026, 9, 3),
            warehouse=warehouse, line_status="cancelled", delivered="4",
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, product.product_code, "2026-08-31")
        assert cell["total_qty"] == "20", "the delivered line is planned at its ordered qty"
        assert len(cell["contributions"]) == 2
        assert sorted(c["qty"] for c in cell["contributions"]) == ["10", "10"]


# --------------------------------------------------------------------------- #
# 13.7 - allocation is per (product, location), never across
# --------------------------------------------------------------------------- #


def test_a_cell_spanning_two_locations_allocates_per_location():
    """Ladder v2: the own location is never a Reserve source at all (section E rule 7), and
    neither location has a shared pool here, so BOTH lines are bought - free stock at one
    location still cannot cover a line fulfilled from another; that is a transfer, and a
    non-goal here. What still tells the two apart is `contested`: `here` bought while its OWN
    site had 10 idle, `there` never held anything at all."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        here = _warehouse(db, f"ZZTA{_uid()[:6]}"[:20])
        there = _warehouse(db, f"ZZTB{_uid()[:6]}"[:20])
        _stock(db, product, here, on_hand=10)
        _stock(db, product, there, on_hand=0)
        first = _order(db, so_number="ZZT-SO-LOC1", order_date=date(2026, 1, 1))
        second = _order(db, so_number="ZZT-SO-LOC2", order_date=date(2026, 1, 1))
        _line(db, first, product, qty="10", required_date=date(2026, 9, 3), warehouse=here)
        _line(db, second, product, qty="10", required_date=date(2026, 9, 3), warehouse=there)

        board = _service(db).build(
            ["ZZT-SO-LOC1", "ZZT-SO-LOC2"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        assert cell["total_qty"] == "20"
        assert {(loc["location"], loc["qty"]) for loc in cell["locations"]} == {
            (here.warehouse_code, "10"), (there.warehouse_code, "10")
        }
        by_order = {c["so_number"]: c for c in cell["contributions"]}
        assert [
            (s["kind"], s["qty"]) for s in by_order["ZZT-SO-LOC1"]["sources"]
        ] == [("buy", "10")]
        assert [
            (s["kind"], s["qty"]) for s in by_order["ZZT-SO-LOC2"]["sources"]
        ] == [("buy", "10")]
        assert by_order["ZZT-SO-LOC1"]["contested"] is True, (
            "it bought while its own site had free stock sitting idle"
        )
        assert by_order["ZZT-SO-LOC2"]["contested"] is False, (
            "a location that never held stock is a plain Buy, not a contest"
        )


def test_free_stock_is_what_the_supply_service_computes_not_a_second_opinion():
    """On hand minus reserved: the board must not invent its own availability figure.

    Ladder v2: the own location is never a Reserve source, so the figure is exercised on the
    site POOL instead, and the line asks for exactly what is left (6) so the whole-line rule
    still proposes it - "reserve 6" here, never a mixed "reserve 6, buy 4"."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        # 20 on hand with 14 reserved: FREE is still 6, which is what this case is about,
        # and the pile's availability (20) is enough for ladder v8's half-share (10) to
        # cover the line - so what bounds the draw is the free pile, exactly as pinned.
        _stock(db, product, pool, on_hand=20, reserved=14)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="6", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        sources = board["cells"][0]["contributions"][0]["sources"]
        assert [(s["kind"], s["qty"]) for s in sources] == [("reserve", "6")]


# --------------------------------------------------------------------------- #
# 13.5 / 13.5.1 - the ranking, and the contest it makes visible
# --------------------------------------------------------------------------- #


def test_both_lines_of_an_oversold_group_buy_and_are_reported_as_contested():
    """LADDER V7.1 (R24, 29 August 2026): 10 on hand against 20 owed, served FIRST-COME BY
    REQUIRED DATE - so the line due 1 September takes the 10 and the line due the 4th goes
    without.

    v4 refused both of them, on the reading that a group which cannot cover its own book
    promises its stock to nobody in particular. R24 replaces that: the pile is date-aware,
    the earlier line takes it, and the later one is short in its own month. `contested`
    still means what it always meant - this line is buying while its location holds stock -
    and under v7.1 that is true of the LOSER alone.
    """
    with blank_session() as db:
        _policy(db, {"need_by_date": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        winner = _order(db, so_number="ZZT-SO-WIN", order_date=date(2026, 1, 1))
        loser = _order(db, so_number="ZZT-SO-LOSE", order_date=date(2026, 1, 1))
        # Same cell (same ISO week), different real dates: the sooner one outranks.
        _line(db, winner, product, qty="10", required_date=date(2026, 9, 1), warehouse=warehouse)
        _line(db, loser, product, qty="10", required_date=date(2026, 9, 4), warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-WIN", "ZZT-SO-LOSE"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        assert [c["so_number"] for c in cell["contributions"]] == [
            "ZZT-SO-WIN", "ZZT-SO-LOSE"
        ], "contributions are served in rank order, highest first"
        won, lost = cell["contributions"]
        assert won["rank_score"] > lost["rank_score"], "the ranking is still computed"
        assert [(s["kind"], s["qty"]) for s in won["sources"]] == [("reserve", "10")]
        assert [(s["kind"], s["qty"]) for s in lost["sources"]] == [("buy", "10")]
        assert lost["contested"] is True
        assert won["contested"] is False, "it is not buying: it took the pile"
        assert cell["contested_count"] == 1
        # A ranking nobody can inspect is a ranking nobody will trust: the row still carries
        # the factors behind its score.
        assert {f["key"] for f in lost["rank_factors"]} >= {
            "need_by_date", "document_age", "customer_credit",
            "demand_class", "po_document_sequence",
        }


def test_shorter_payment_terms_rank_higher_and_take_the_pool_first():
    """What the ranking still decides, now that own-location stock follows the book.

    A line's share of its OWN location is the projection `confirm` judges against - required
    date first, across every outstanding line in the book - because a board that redistributed
    that share by its own ranking proposed Reserves the confirmation refused (the SO396351
    defect). What the ranking still decides is the order the rows are served in, and with it
    who draws the SHARED POOL first, which is contested among the selected orders and nowhere
    else. So the policy is pinned here on the pool.

    Making the ranking decide own-location stock as well means sorting the book-wide projection
    by the policy instead of by required date - which changes the per-order sheet too, and is a
    business decision rather than a coder's.
    """
    with blank_session() as db:
        _policy(db, {"customer_credit": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=0)
        # 20 in the pool: a project line may take half of it (ladder v8, R-B), and this
        # case is about WHICH of the two lines draws it first.
        _stock(db, product, pool, on_hand=20)
        prompt = _customer(db, f"{MARKER} pays in 30", terms=30)
        slow = _customer(db, f"{MARKER} pays in 90", terms=90)
        # The slower payer is FIRST by sales-order number, so an alphabetical answer loses.
        a = _order(db, so_number="ZZT-SO-AAA", customer=slow, order_date=date(2026, 1, 1))
        b = _order(db, so_number="ZZT-SO-BBB", customer=prompt, order_date=date(2026, 1, 1))
        _line(db, a, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
        _line(db, b, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build(
            ["ZZT-SO-AAA", "ZZT-SO-BBB"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        assert [c["so_number"] for c in cell["contributions"]] == ["ZZT-SO-BBB", "ZZT-SO-AAA"]
        winner, loser = cell["contributions"]
        assert winner["qty_proposed_reserve"] == "10", "the prompt payer draws the pool first"
        assert [s["location"] for s in winner["sources"] if s["kind"] == "reserve"] == [
            pool.warehouse_code
        ]
        assert loser["qty_proposed_buy"] == "10"


def test_the_older_sales_order_ranks_higher_and_takes_the_pool_first():
    """The prototype had this inverted, with the NEWEST document winning. 2024 beats 2026.

    Pinned on the pool draw for the same reason as the test above: own-location stock follows
    the book's projection, which is what the confirmation enforces.
    """
    with blank_session() as db:
        _policy(db, {"document_age": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=0)
        # 20 in the pool: a project line may take half of it (ladder v8, R-B), and this
        # case is about WHICH of the two lines draws it first.
        _stock(db, product, pool, on_hand=20)
        # The NEWER order is first alphabetically, so an accidental sort cannot pass this.
        new = _order(db, so_number="ZZT-SO-AAA", order_date=date(2026, 7, 28))
        old = _order(db, so_number="ZZT-SO-BBB", order_date=date(2024, 1, 9))
        _line(db, new, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
        _line(db, old, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build(
            ["ZZT-SO-AAA", "ZZT-SO-BBB"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        assert [c["so_number"] for c in cell["contributions"]] == ["ZZT-SO-BBB", "ZZT-SO-AAA"]
        assert cell["contributions"][0]["qty_proposed_reserve"] == "10"
        assert cell["contributions"][1]["qty_proposed_buy"] == "10"


def test_a_policy_that_ranks_nothing_is_reported_flat_rather_than_dressed_up():
    """No policy row at all falls back to `po_document_sequence`, which no board row has."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        a = _order(db, so_number="ZZT-SO-AAA", order_date=date(2026, 1, 1))
        b = _order(db, so_number="ZZT-SO-BBB", order_date=date(2026, 7, 1))
        _line(db, a, product, qty="10", required_date=date(2026, 9, 1), warehouse=warehouse)
        _line(db, b, product, qty="10", required_date=date(2026, 9, 4), warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-AAA", "ZZT-SO-BBB"], granularity="week", as_of=TODAY
        )

        assert board["policy"]["discriminates_nothing"] is True
        assert board["policy"]["is_preview"] is False
        cell = _cell(board, product.product_code, "2026-08-31")
        assert [c["rank_score"] for c in cell["contributions"]] == [0.0, 0.0]
        # Flat, so the tie-break is what orders them, and it is TOTAL: sales-order number then
        # line number, so the same facts give the same answer on every refresh.
        assert [c["so_number"] for c in cell["contributions"]] == [
            "ZZT-SO-AAA", "ZZT-SO-BBB"
        ]


def test_the_named_preview_ranks_without_activating_anything():
    with blank_session() as db:
        live = _policy(db, {"po_document_sequence": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        soon = _order(db, so_number="ZZT-SO-BBB", order_date=date(2026, 1, 1))
        late = _order(db, so_number="ZZT-SO-AAA", order_date=date(2026, 1, 1))
        _line(db, soon, product, qty="10", required_date=date(2026, 9, 1), warehouse=warehouse)
        _line(db, late, product, qty="10", required_date=date(2026, 9, 4), warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-AAA", "ZZT-SO-BBB"],
            granularity="week",
            as_of=TODAY,
            preview_policy=priority.BOARD_PREVIEW_NAME,
        )

        assert board["policy"]["is_preview"] is True
        assert board["policy"]["name"] == priority.BOARD_PREVIEW_NAME
        assert board["policy"]["discriminates_nothing"] is False
        cell = _cell(board, product.product_code, "2026-08-31")
        assert [c["so_number"] for c in cell["contributions"]] == ["ZZT-SO-BBB", "ZZT-SO-AAA"]
        # Nothing was written and nothing was switched on.
        assert str(priority.active_policy(db).id) == str(live.id)
        assert (
            db.query(PriorityPolicy).filter(PriorityPolicy.name == priority.BOARD_PREVIEW_NAME)
            .count() == 0
        )


def test_an_unknown_preview_policy_name_is_refused_by_name():
    from app.services.error_handler import AppException

    with blank_session() as db:
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))

        with pytest.raises(AppException) as excinfo:
            _service(db).build(
                [order.so_number], as_of=TODAY, preview_policy=f"{MARKER}-nope"
            )

        assert excinfo.value.status_code == 404
        assert f"{MARKER}-nope" in excinfo.value.detail["message"]


# --------------------------------------------------------------------------- #
# AC-FP16 on the board (13.7)
# --------------------------------------------------------------------------- #


def test_a_line_whose_order_states_no_location_is_counted_but_unplannable():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=100)
        stated = _order(db, so_number="ZZT-SO-HASWH", order_date=date(2026, 1, 1))
        silent = _order(db, so_number="ZZT-SO-NOWH", order_date=date(2026, 1, 1))
        _line(db, stated, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, silent, product, qty="4", required_date=date(2026, 9, 3), warehouse=None)

        board = _service(db).build(
            ["ZZT-SO-HASWH", "ZZT-SO-NOWH"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        # Counted, so the demand is not hidden by the source record being incomplete.
        assert cell["total_qty"] == "14"
        assert cell["unplannable_count"] == 1
        blocked = next(c for c in cell["contributions"] if c["so_number"] == "ZZT-SO-NOWH")
        assert blocked["unplannable"] is True
        assert blocked["fulfilment_location"] is None
        assert [s["kind"] for s in blocked["sources"]] == ["unplannable"]
        # Nothing is proposed for it: a Reserve of zero presented as a plan is worse than a
        # refusal, and no location is ever guessed.
        assert {(loc["location"], loc["qty"]) for loc in cell["locations"]} == {
            (warehouse.warehouse_code, "10"), (None, "4")
        }


# --------------------------------------------------------------------------- #
# the selection itself (13.2)
# --------------------------------------------------------------------------- #


def test_more_than_fifty_orders_is_refused_and_the_cap_is_stated():
    from app.services.error_handler import AppException

    with blank_session() as db:
        with pytest.raises(AppException) as excinfo:
            _service(db).build([f"ZZT-SO-{n}" for n in range(51)], as_of=TODAY)

        assert excinfo.value.status_code == 422
        assert "50" in excinfo.value.detail["message"]


def test_an_empty_selection_is_an_empty_board_not_the_whole_book():
    with blank_session() as db:
        board = _service(db).build([], as_of=TODAY)

        assert board["cells"] == []
        assert board["productRows"] == []
        assert board["orders"] == []


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _client(db, user_id: str, permissions):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    granted = list(permissions)
    UserPermissionService.check_user_has_permission = (
        lambda self, uid, slug: slug in granted
    )
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(granted)
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


def _board_world(db):
    """Ladder v2: `warehouse` keeps its own on-hand (the strip still reads it, read-only), and
    a site pool ALSO holds enough to cover the line whole, since the own location is never a
    Reserve source any more."""
    product = _product(db, f"ZZT-{_uid()[:6]}")
    warehouse, pool = _pooled_warehouses(db)
    _stock(db, product, warehouse, on_hand=10)
    # 20 in the pool: ladder v8 lets a project line have HALF of it (R-B), so covering a
    # line of 10 whole from the pool takes 20 in it.
    _stock(db, product, pool, on_hand=20)
    order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
    _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
    return order, product


def test_the_board_route_answers_the_selection_it_was_given():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        order, product = _board_world(db)
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={
                        "orders": order.so_number,
                        "granularity": "week",
                        # Pinned, like every service-level build: `past_line_count` below
                        # is judged against this date, not against the clock.
                        "as_of": TODAY.isoformat(),
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["granularity"] == "week"
        assert body["policy"]["name"]
        assert [row["item_code"] for row in body["productRows"]] == [product.product_code]
        assert body["cells"][0]["contributions"][0]["so_number"] == order.so_number
        # The selection-scoped totals reach the wire. A field the service returns but the
        # response model does not declare is dropped silently, and the banner would then read
        # zero rather than fail.
        assert body["line_count"] == 1
        assert body["past_line_count"] == 0
        assert body["unplannable_line_count"] == 0
        assert body["contested_line_count"] == 0
        # The named quantities and the raw facts reach the wire too: a field the service
        # returns but the response model does not declare is dropped silently.
        contribution = body["cells"][0]["contributions"][0]
        assert contribution["qty_ordered"] == "10"
        assert contribution["qty_outstanding"] == "10"
        assert contribution["qty_proposed_reserve"] == "10"
        location = body["cells"][0]["locations"][0]
        assert location["qty_on_hand"] == "10" and location["qty_free"] == "10"
        # The PER-LINE stock block reaches the wire, on the cell's rows and on the top-level
        # list the List view reads: it is what the decision panel prints "N available" from,
        # and a field the response model does not declare is dropped silently.
        assert contribution["locations"][0]["location"] == location["location"]
        assert body["contributions"][0]["locations"][0]["qty_on_hand"] == "10"
        assert {f["key"]: f["raw"] for f in contribution["rank_factors"]}["need_by_date"] == (
            "2026-09-03"
        )
        # No identifier a person has to resolve is rendered anywhere: the human keys are the
        # sales-order number, the item code and the warehouse code.
        assert body["cells"][0]["contributions"][0]["item_code"] == product.product_code


def test_the_board_route_refuses_a_caller_without_the_view_permission():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        order, _product = _board_world(db)
        db.commit()
        client, originals = _client(db, actor, [])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": order.so_number},
                )
        finally:
            _restore(originals)

        assert response.status_code == 403


def test_an_unknown_granularity_is_a_422_not_a_silently_weekly_board():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        order, _product = _board_world(db)
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": order.so_number, "granularity": "fortnight"},
                )
        finally:
            _restore(originals)

        assert response.status_code == 422


def test_a_named_policy_row_that_is_actually_live_is_not_labelled_a_preview():
    """Whether it is a preview is a fact about the ROW, not about how it was asked for."""
    with blank_session() as db:
        live = _policy(db, {"need_by_date": 1.0}, name=f"{MARKER}-live-by-name")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build(
            [order.so_number], as_of=TODAY, preview_policy=live.name
        )

        assert board["policy"]["name"] == live.name
        assert board["policy"]["is_preview"] is False


def test_preview_policy_1_means_the_modules_own_board_preview():
    """The frontend sends `preview_policy=1`, and that translation belongs to the service.

    Split between route and service it would be untested on one side of the seam and the two
    would come to disagree about what "preview" was asked for.
    """
    with blank_session() as db:
        _policy(db, {"po_document_sequence": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], as_of=TODAY, preview_policy="1")

        assert board["policy"]["name"] == priority.BOARD_PREVIEW_NAME
        assert board["policy"]["is_preview"] is True


def test_the_day_window_opens_on_the_work_still_to_come_not_three_years_ago():
    """With the Overdue column gone, "the earliest dated bucket" can be years in the past.

    The prototype's day view opened on the earliest STILL-FUTURE date, and it has to keep
    doing that: a 30-day window that opens in December 2024 shows the planner one line and an
    empty month. Past demand is reached by moving the window, which is what the control is for.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="5", required_date=date(2024, 12, 3), warehouse=warehouse)
        _line(db, order, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="day", as_of=TODAY)

        dated = [b for b in board["dateBuckets"] if b["kind"] == "dated"]
        assert dated[0]["key"] == "2026-09-03"
        # And the past line is still reachable: the window is a display control, so asking for
        # it by date brings it back rather than the plan having lost it.
        back = _service(db).build(
            [order.so_number],
            granularity="day",
            as_of=TODAY,
            day_window_start=date(2024, 12, 1),
        )
        assert [b["key"] for b in back["dateBuckets"]][0] == "2024-12-01"
        assert _cell(back, product.product_code, "2024-12-03")["past_count"] == 1


def test_the_day_window_falls_back_to_the_earliest_owed_when_everything_is_past():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="5", required_date=date(2025, 6, 15), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="day", as_of=TODAY)

        dated = [b for b in board["dateBuckets"] if b["kind"] == "dated"]
        assert dated[0]["key"] == "2025-06-15", "open where the work is, not on an empty today"
        assert dated[0]["is_past"] is True


# --------------------------------------------------------------------------- #
# board-level totals: the selection's truth, not the window's
# --------------------------------------------------------------------------- #


def _past_world(db):
    """One order, three products, most of it already past, spread over three years."""
    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
    order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
    for offset, when in enumerate(
        [date(2024, 3, 4), date(2025, 6, 15), date(2026, 8, 17), date(2026, 9, 3)]
    ):
        product = _product(db, f"ZZT-{_uid()[:6]}")
        _stock(db, product, warehouse, on_hand=1)
        _line(db, order, product, qty="5", required_date=when, warehouse=warehouse)
    # One line nobody stated a location for, and one with no date at all.
    spare = _product(db, f"ZZT-{_uid()[:6]}")
    _line(db, order, spare, qty="5", required_date=date(2024, 3, 4), warehouse=None)
    _line(db, order, spare, qty="5", required_date=None, warehouse=warehouse)
    return order


def test_the_board_totals_are_the_selections_and_do_not_move_with_the_granularity():
    """The FE banner reads "143 of 153 lines are already past their delivery date".

    Summed off the cells ON SCREEN that sentence is true at week and month and vanishes at day,
    because the day window opens on work still to come and so holds no past cell at all - the
    planner switching to the closest view silently loses the most important number on the
    board. So the total is the SELECTION's, computed before any window is applied.
    """
    with blank_session() as db:
        order = _past_world(db)

        boards = {
            gran: _service(db).build([order.so_number], granularity=gran, as_of=TODAY)
            for gran in ("day", "week", "month")
        }

        for gran, board in boards.items():
            assert board["line_count"] == 6, gran
            assert board["past_line_count"] == 4, gran
            assert board["unplannable_line_count"] == 1, gran
        # And the day board really does show none of them, which is the whole point.
        day_cells_past = sum(cell["past_count"] for cell in boards["day"]["cells"])
        assert day_cells_past == 0
        assert boards["day"]["past_line_count"] == 4


def test_moving_the_day_window_does_not_change_the_board_totals():
    with blank_session() as db:
        order = _past_world(db)

        here = _service(db).build([order.so_number], granularity="day", as_of=TODAY)
        long_ago = _service(db).build(
            [order.so_number],
            granularity="day",
            as_of=TODAY,
            day_window_start=date(2024, 3, 1),
        )
        empty = _service(db).build(
            [order.so_number],
            granularity="day",
            as_of=TODAY,
            day_window_start=date(2029, 1, 1),
        )

        for board in (here, long_ago, empty):
            assert board["line_count"] == 6
            assert board["past_line_count"] == 4
            assert board["unplannable_line_count"] == 1
        # The windows genuinely differ, so the totals holding still means something. The
        # dateless column survives the window because it is not on the timeline at all, which
        # is why this counts DATED cells rather than all of them.
        assert [c for c in empty["cells"] if c["bucket_key"] != "no_date"] == []
        assert sum(c["past_count"] for c in long_ago["cells"]) == 2


def test_a_line_outside_the_day_window_is_still_served_from_its_pile():
    """The window is a display bound, so it must not change what anybody is proposed.

    Two lines at one location holding 10 against 20 owed: under ladder v7.1 the earlier one
    takes the pile and the later one buys, contested. Look at that through a day window
    containing neither of them, and the count still has to be reported - the board's totals
    describe the selection, not the columns that happen to be on screen.
    """
    with blank_session() as db:
        _policy(db, {"need_by_date": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        winner = _order(db, so_number="ZZT-SO-WINDOW1", order_date=date(2026, 1, 1))
        loser = _order(db, so_number="ZZT-SO-WINDOW2", order_date=date(2026, 1, 1))
        _line(db, winner, product, qty="10", required_date=date(2026, 9, 1), warehouse=warehouse)
        _line(db, loser, product, qty="10", required_date=date(2026, 9, 4), warehouse=warehouse)

        week = _service(db).build(
            ["ZZT-SO-WINDOW1", "ZZT-SO-WINDOW2"], granularity="week", as_of=TODAY
        )
        far = _service(db).build(
            ["ZZT-SO-WINDOW1", "ZZT-SO-WINDOW2"],
            granularity="day",
            as_of=TODAY,
            day_window_start=date(2029, 1, 1),
        )

        assert week["contested_line_count"] == 1
        assert [c for c in far["cells"] if c["bucket_key"] != "no_date"] == [], (
            "the window shows nothing of the timeline"
        )
        assert far["contested_line_count"] == 1, "and the contest is still reported"


def test_top_level_contributions_carry_every_line_even_outside_the_day_window():
    """`board["contributions"]` is the unwindowed population Approve all and the List view act
    on (review finding, PLAN 13.5's own reasoning applied to a NEW field).

    Two lines outside a day window that opens far away: `cells` (dated) is empty for them, but
    `contributions` still carries both - full proposals included, since `_allocate` already ran
    over every bucket regardless of what the window shows. Week and month need no window at all,
    so their `contributions` list is the same set either way.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        order = _order(db, so_number="ZZT-SO-CONTRIB1", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 1), warehouse=warehouse)
        _line(db, order, product, qty="6", required_date=date(2026, 9, 4), warehouse=warehouse)

        week = _service(db).build(["ZZT-SO-CONTRIB1"], granularity="week", as_of=TODAY)
        day = _service(db).build(
            ["ZZT-SO-CONTRIB1"],
            granularity="day",
            as_of=TODAY,
            day_window_start=date(2029, 1, 1),
        )

        # The window shows nothing of the timeline...
        assert [c for c in day["cells"] if c["bucket_key"] != "no_date"] == []
        # ...but both lines are still in the unwindowed list, fully proposed.
        assert len(day["contributions"]) == 2
        assert {c["qty"] for c in day["contributions"]} == {"4", "6"}
        assert all(c["sources"] for c in day["contributions"]), (
            "allocation ran over the whole selection, not only the window"
        )

        # Week has no window at all, so the same LINES come back either way - compared by the
        # line id rather than the draft key, whose bucket half differs between day and week.
        week_lines = {c["line_id"] for c in week["contributions"]}
        day_lines = {c["line_id"] for c in day["contributions"]}
        assert week_lines == day_lines
        assert len(week_lines) == 2


def test_each_orders_standing_counts_the_whole_order_not_the_window():
    """`orders[]` is the other place the window could lie, and it must not.

    The screen prints "N of M lines decided" per order. Rebuilt from the cells on screen, M
    shrinks to the window at day granularity and an order of forty lines reads as three - so
    the standings are counted from the selection here, and the screen overlays only its own
    draft count on top.
    """
    with blank_session() as db:
        order = _past_world(db)

        day = _service(db).build([order.so_number], granularity="day", as_of=TODAY)
        month = _service(db).build([order.so_number], granularity="month", as_of=TODAY)

        assert [(s["so_number"], s["line_count"], s["unplannable_count"]) for s in day["orders"]] == [
            (order.so_number, 6, 1)
        ]
        assert day["orders"] == month["orders"]
        # And the window really is narrower than the order, so the equality means something.
        assert sum(len(c["contributions"]) for c in day["cells"]) < 6


# --------------------------------------------------------------------------- #
# the numbers, by name
#
# The captain, on the live board: "how do i see the available quantity for each stock, is it
# BRW-BB - 22?, we need to be clear on the quantity, like what is the quantity on hand, what is
# the SO quantity, what is the PO qty, what is the incoming quantity". `22` was the DEMAND at
# that location, which is the one reading nobody guessed.
#
# The fixture below deliberately makes EVERY number different, so a field crossed with its
# neighbour fails instead of passing:
#   ordered 28, delivered 8    -> outstanding 20
#   on hand 30, reserved 25    -> free 5
#   SPO allocated 9, received 2 -> incoming 7, arriving before the required date
#   so the proposal must be    -> reserve 5 + incoming 7 + buy 8
# --------------------------------------------------------------------------- #


def _incoming(db, product, warehouse, *, spo_number: str, allocated: int, received: int,
              arrives: date):
    from app.models.procurement import InboundShipment, SPOAllocation

    shipment = InboundShipment(
        id=_uid(),
        shipment_number=f"ZZT-SHIP-{_uid()[:8]}",
        shipment_date=date(2026, 1, 1),
        estimated_arrival_date=arrives,
    )
    db.add(shipment)
    db.flush()
    row = SPOAllocation(
        id=_uid(),
        spo_number=spo_number,
        spo_line_number=1,
        inbound_shipment_id=shipment.id,
        warehouse_id=warehouse.id,
        product_id=product.id,
        allocated_quantity=allocated,
        quantity_received=received,
        receipt_status="pending",
    )
    db.add(row)
    db.flush()
    return row


def _quantity_world(db):
    product = _product(db, f"ZZT-{_uid()[:6]}")
    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
    _stock(db, product, warehouse, on_hand=30, reserved=25)
    _incoming(
        db, product, warehouse,
        spo_number="ZZT-SPO-0001", allocated=9, received=2, arrives=date(2026, 8, 25),
    )
    order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
    _line(
        db, order, product, qty="28", delivered="8",
        required_date=date(2026, 9, 3), warehouse=warehouse,
    )
    return order, product, warehouse


def test_a_contribution_states_the_sales_order_quantities_by_name():
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["qty_ordered"] == "28"
        assert contribution["qty_delivered"] == "8"
        assert contribution["qty_outstanding"] == "20"
        # AC-S2-4: the PLAN quantity is what the board plans against, and it is the ordered
        # (or required) figure, not the still-owed one - a delivered unit nobody sourced is a
        # unit to put back. The two are asserted BY NAME rather than against each other,
        # because an equality between them is the retired rule restated: they differ the
        # moment a delivery is part-made, which is the whole of this slice.
        assert contribution["qty"] == "28"
        assert contribution["qty"] != contribution["qty_outstanding"]


def test_a_contribution_states_the_proposal_by_name():
    """Ladder v2's whole-line rule (section E rule 6): `_quantity_world` has no pool, so the
    5 free at the own location and the 7 arriving in time can never together reach the whole
    28 the line PLANS for - every partial component is dropped, not mixed in, and the WHOLE
    line is bought, including the incoming portion that would have covered part of it on its
    own.

    28, not the 20 still owed (AC-S2-4): 8 of those units have shipped with nothing behind
    them, and buying only what is still to ship would leave them never put back."""
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["qty_proposed_reserve"] == "0"
        assert contribution["qty_proposed_incoming"] == "0"
        assert contribution["qty_proposed_buy"] == "28"
        # And they still add up to the quantity being PLANNED, which is the invariant the
        # sheet also keeps - stated against `qty` now, since that is what the ladder was
        # asked about.
        assert (
            int(contribution["qty_proposed_reserve"])
            + int(contribution["qty_proposed_incoming"])
            + int(contribution["qty_proposed_buy"])
        ) == int(contribution["qty"])


def test_a_cells_location_states_the_availability_not_only_the_demand():
    """"is it BRW-BB - 22?" - no. 22 was the demand, and the stock facts were nowhere.

    The raw stock/incoming facts are read-only strip figures and stay exactly what the pile
    holds, whether or not the ladder ever draws on them. Ladder v2's whole-line rule (no pool
    here) means it never does: 5 free plus 7 incoming still falls short of the 28 being
    planned, so the whole line is bought and the PROPOSED figures read 0/0/28 while the STOCK
    figures stay 30/25/5/7.

    `qty_demand` is the PLAN quantity too (AC-S2-4). The strip answers "how much is wanted
    here", and what is wanted at this bin is every unit nobody decided a source for - the 8
    already shipped included.
    """
    with blank_session() as db:
        order, product, warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["location"] == warehouse.warehouse_code
        assert location["qty_demand"] == "28"
        assert location["qty_on_hand"] == "30"
        assert location["qty_reserved"] == "25"
        # What this engine may actually use, after existing holds - the number the strip was
        # silently NOT showing.
        assert location["qty_free"] == "5"
        assert location["qty_incoming"] == "7"
        assert location["qty_proposed_reserve"] == "0"
        assert location["qty_proposed_incoming"] == "0"
        assert location["qty_proposed_buy"] == "28"


def test_a_cells_location_names_the_incoming_document_and_its_arrival_date():
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["incoming"] == [
            {
                "spo_number": "ZZT-SPO-0001",
                "arrival_date": date(2026, 8, 25),
                "qty": "7",
            }
        ]


def test_the_free_figure_says_what_was_left_when_this_cell_was_served():
    """Stock is not per date, so the same free figure appears in every column of a product.

    That is the truth and it is also the reading most likely to mislead, because an earlier
    date has already drawn the pile down. So each cell also states what was still unclaimed
    when ITS lines were served.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="6", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, order, product, qty="6", required_date=date(2026, 12, 1), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="month", as_of=TODAY)

        september = _cell(board, product.product_code, "2026-09-01")["locations"][0]
        december = _cell(board, product.product_code, "2026-12-01")["locations"][0]
        assert september["qty_free"] == december["qty_free"] == "10"
        assert september["qty_free_remaining"] == "10"
        assert december["qty_free_remaining"] == "4", "September took six of the ten"
        # Ladder v2: the own location is never a Reserve source and there is no pool here, so
        # both lines are bought whole - the "September took six of the ten" fact survives as
        # the read-only `qty_free_remaining` figure above, even though neither line reserves.
        assert december["qty_proposed_reserve"] == "0"
        assert december["qty_proposed_buy"] == "6"


def test_a_line_with_no_location_states_no_availability_rather_than_zeroes():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=None)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["location"] is None
        assert location["qty_demand"] == "4"
        # Null, not zero: nobody said where this is fulfilled from, so there is no location
        # whose stock could be counted. A zero would read as "that location is empty".
        assert location["qty_on_hand"] is None
        assert location["qty_free"] is None
        assert location["qty_incoming"] is None


# --------------------------------------------------------------------------- #
# the rank, made readable
# --------------------------------------------------------------------------- #


def test_every_factor_carries_the_absolute_fact_that_produced_it():
    """A normalised 0.00 next to a normalised 1.00 explains nothing on its own.

    The planner needs the fact: this line's required date, this order's document date, this
    customer's terms. So each factor carries its raw value beside the normalised one.
    """
    with blank_session() as db:
        _policy(db, dict(priority.BOARD_PREVIEW_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        customer = _customer(db, f"{MARKER} pays in 45", terms=45)
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", customer=customer,
            order_date=date(2025, 4, 16),
        )
        _line(db, order, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        factors = {
            f["key"]: f
            for f in _cell(board, product.product_code, "2026-08-31")["contributions"][0][
                "rank_factors"
            ]
        }
        assert factors["need_by_date"]["raw"] == "2026-09-03"
        assert factors["document_age"]["raw"] == "2025-04-16"
        assert factors["customer_credit"]["raw"] == "45 days"
        assert factors["demand_class"]["raw"] == "project"
        # An absent factor has no fact to state, and must not invent one.
        assert factors["po_document_sequence"]["raw"] is None
        assert factors["po_document_sequence"]["present"] is False


def test_the_sign_of_every_factor_is_verified_against_its_raw_fact():
    """The prototype had `document_age` inverted once, so each sign is pinned to real facts.

    One cell, three competing lines, and for each factor the row whose RAW fact should win is
    asserted to be the row that scores 1.0 - read off the raw value rather than off knowledge
    of the seeding order.
    """
    with blank_session() as db:
        _policy(db, dict(priority.BOARD_PREVIEW_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        rows = [
            ("ZZT-SO-SIGN1", date(2024, 1, 9), 90, date(2026, 9, 1)),
            ("ZZT-SO-SIGN2", date(2025, 6, 30), 60, date(2026, 9, 2)),
            ("ZZT-SO-SIGN3", date(2026, 7, 28), 30, date(2026, 9, 4)),
        ]
        for so_number, order_date, terms, required in rows:
            customer = _customer(db, f"{MARKER} {terms}d", terms=terms)
            order = _order(
                db, so_number=so_number, customer=customer, order_date=order_date
            )
            _line(db, order, product, qty="5", required_date=required, warehouse=warehouse)

        board = _service(db).build(
            [r[0] for r in rows], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        facts = {
            contribution["so_number"]: {
                f["key"]: (f["raw"], f["value"]) for f in contribution["rank_factors"]
            }
            for contribution in cell["contributions"]
        }

        def top(key):
            return max(facts.items(), key=lambda pair: pair[1][key][1])

        # SOONER required date is higher: the winner's raw date is the earliest of the three.
        winner, values = top("need_by_date")
        assert values["need_by_date"] == ("2026-09-01", 1.0)
        assert winner == "ZZT-SO-SIGN1"
        # OLDER document is higher: the winner's raw order date is the earliest.
        winner, values = top("document_age")
        assert values["document_age"] == ("2024-01-09", 1.0)
        # SHORTER terms are higher: the winner pays in 30 days, not 90.
        winner, values = top("customer_credit")
        assert values["customer_credit"] == ("30 days", 1.0)
        assert winner == "ZZT-SO-SIGN3"


# --------------------------------------------------------------------------- #
# the source ladder: the board must give the SAME answer as the sheet
#
# The captain, reading a breakdown line by line: "so for this one, no free stock available, so
# did it go through the process of whether want to borrow / use BRW (depending on the hot
# selling / cold selling or discontinued) then only arrive at buy?"
#
# It did not, and that made the board's Buy overstate what has to be bought - purchasing acting
# on the board and purchasing acting on the sheet would disagree about the same line. The
# ladder is `project_supply_service`'s, and the board runs THAT rather than a reduced copy.
# --------------------------------------------------------------------------- #


def _pooled_warehouses(db):
    """A project location whose shortfall may draw on a shared pool, as BRW-BB draws on BRW."""
    pool = _warehouse(db, f"ZZTP{_uid()[:6]}"[:20])
    own = _warehouse(db, f"ZZTO{_uid()[:6]}"[:20])
    own.pool_warehouse_id = pool.id
    db.flush()
    return own, pool


def test_the_board_covers_a_shortfall_from_the_pool_instead_of_buying_it():
    """Ladder v2 (section E rule 7): the own location is never a Reserve source, so the whole
    10 is covered from the shared pool alone - `own`'s 2 units sit untouched, read-only."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=2)
        _stock(db, product, pool, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        kinds = [(s["kind"], s["qty"], s["location"]) for s in contribution["sources"]]
        assert kinds == [
            ("reserve", "10", pool.warehouse_code),
        ], "the shared pool covers the whole shortfall before anything is bought"
        assert contribution["qty_proposed_buy"] == "0"


def test_a_dealer_hot_selling_product_is_offered_the_pools_share_like_any_other():
    """Amended 19 August 2026 (ladder v2, section E rule 7): the own-location Reserve rung is
    GONE, self-pool or not - `own`'s 20 units are never touched, dealer hot-selling or not.

    RE-BLESSED BY LADDER V8 (R-A): dealer hot-selling no longer gates the pool at all. What
    keeps stock for dealers is the SHARE (R-B) - half of the pool's 12 is 6, so the line of
    10 takes 6 and buys the other 4, where v4 to v7.1 refused the step and bought all 10.
    """
    from app.models.scm import ItemClassification

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=20)
        _stock(db, product, pool, on_hand=12)
        db.add(
            ItemClassification(
                id=_uid(), product_id=product.id, warehouse_id=own.id, abc_class_retail="A"
            )
        )
        db.flush()
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        kinds = [(s["kind"], s["qty"], s["location"]) for s in contribution["sources"]]
        # The dealer's OWN stock is still never touched; the pool spares its half.
        assert kinds == [
            ("reserve", "6", pool.warehouse_code),
            ("buy", "4", None),
        ]
        assert not any(s["location"] == own.warehouse_code for s in contribution["sources"])


def test_the_board_proposes_exactly_what_the_sheet_proposes_for_the_same_line():
    """The point of the whole change: one ladder, two surfaces, the same answer.

    Not "the same shape" - the same quantities, from the same code, for a line the pool
    cannot fully cover: ladder v2's whole-line rule (section E rule 6) drops the pool's
    partial contribution and buys the line whole, on BOTH surfaces alike.
    """
    from datetime import datetime

    from app.models.project_so import (
        SO_STATUS_PUBLISHED,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
    )
    from app.services.project_supply_service import ProjectSupplyService

    with blank_session() as db:
        company_id = _sorento(db)
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=3)
        _stock(db, product, pool, on_hand=4)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core_line = _line(
            db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own
        )
        planning = ProjectSalesOrder(
            id=_uid(),
            company_id=company_id,
            project_id=None,
            provisional_ref=order.so_number,
            autocount_doc_no=order.so_number,
            so_id=order.id,
            status=SO_STATUS_PUBLISHED,
            published_at=datetime.utcnow(),
            grouping_origin="area",
        )
        db.add(planning)
        db.flush()
        db.add(
            ProjectSalesOrderLine(
                id=_uid(),
                company_id=company_id,
                project_sales_order_id=planning.id,
                line_no=1,
                product_id=product.id,
                description=f"{MARKER} line",
                qty=Decimal("10"),
                uom="UNIT",
                unit_price=Decimal("10.00"),
                amount=Decimal("100.00"),
                delivery_date=date(2026, 9, 3),
                core_sales_order_line_id=core_line.id,
            )
        )
        db.flush()

        sheet = ProjectSupplyService(db).proposal_for(planning)
        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        sheet_components = [
            (c["kind"], c["qty"], c["source_location"])
            for c in sheet["lines"][0]["components"]
        ]
        board_sources = [
            (s["kind"], s["qty"], s["location"])
            for s in _cell(board, product.product_code, "2026-08-31")["contributions"][0][
                "sources"
            ]
        ]
        assert board_sources == sheet_components
        assert board_sources == [
            ("reserve", "2", pool.warehouse_code),
            ("buy", "8", None),
        ]


def test_a_buy_the_board_cannot_avoid_still_says_borrowing_is_possible():
    """Borrow is never PROPOSED - not on the sheet either, because it needs a donor and a
    reason from a person. What the sheet does do is OFFER it, and a board that prints a bare
    Buy hides the fact that the stock exists somewhere."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTO{_uid()[:6]}"[:20])
        elsewhere = _warehouse(db, f"ZZTE{_uid()[:6]}"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, elsewhere, on_hand=25)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["qty_proposed_buy"] == "10"
        assert contribution["qty_borrow_available"] == "25"
        assert [c["warehouse_code"] for c in contribution["borrow_candidates"]] == [
            elsewhere.warehouse_code
        ]
        assert "borrow" in contribution["sources"][-1]["reason"].lower()
        # AC-V4: the note and step 1 are ONE statement. This line's location carries no
        # ownership group, so step 1 has no "our locations" to draw from - and the donor is
        # still on the row's own candidate list, which is what the Buy's sentence names.
        step = _step(contribution, "own")
        assert step["answer"] == "no"
        assert "no ownership group" in step["why"]


def test_a_board_borrow_candidate_carries_what_it_takes_to_confirm_it():
    """A donor the planner can only READ is a donor they cannot use.

    The board's Amend now composes a Borrow, and `ConfirmBorrowComponent` takes a
    `warehouse_id` and a `donor_project_id` - neither of which a warehouse CODE can be
    resolved into on the client without guessing at an id. The sheet's candidate has carried
    both since Stage 1C; the board's was a narrower copy of it, so the same donor was
    offerable on one screen and not on the other.

    `donor_impact` travels for the same reason it does on the sheet: borrowing is decided
    with the holder's position in front of the person deciding (AC-B09).
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTO{_uid()[:6]}"[:20])
        elsewhere = _warehouse(db, f"ZZTE{_uid()[:6]}"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, elsewhere, on_hand=25)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        candidate = contribution["borrow_candidates"][0]
        assert candidate["warehouse_id"] == str(elsewhere.id)
        # Free stock at another location has no project holding it, so there is no donor to
        # ask and no project to name. Absent, never invented.
        assert candidate["donor_project_id"] is None
        assert candidate["donor_impact"] == {
            "free_before": "25",
            "free_after_full_borrow": "0",
            "committed_qty": "0",
        }


def _lead_time(db, product, days: int) -> None:
    """State a supplier agreement, which is where the ATP window reads its lead time from."""
    from app.models.procurement import ProductSupplier, Supplier

    supplier = Supplier(
        id=_uid(),
        supplier_code=f"ZZT-SUP-{_uid()[:8]}".upper(),
        supplier_name=f"{MARKER} lead-time supplier",
    )
    db.add(supplier)
    db.flush()
    db.add(
        ProductSupplier(
            id=_uid(),
            product_id=product.id,
            supplier_id=supplier.id,
            standard_lead_time_days=days,
        )
    )
    db.flush()


def test_a_line_beyond_the_reserve_window_says_so_and_offers_no_donor():
    """The captain, on SO414341: two lines due 15 February and 15 March 2027, roughly 174
    days out on a product whose lead time is well inside that, read "Nothing free at BRW-BB
    by the delivery date, so the quantity is bought. Borrowing is possible from BRW-IB,
    BRW-SMC, BRW-AM" - and the Suggestion card offered exactly the borrow the ATP reserve
    window exists to refuse.

    Both halves are pinned here, because both were wrong for the same reason:

      * the SENTENCE is the engine's own, naming the window. The board wrote its own contest
        sentence over the top of it, and "nothing free at L" is not why this line is bought -
        it is bought because purchasing can still get it here in time and the stock at L is
        kept for nearer orders;
      * the DONORS are not offered at all. Rungs 4 and 5 are not walked for this line, so a
        donor list beside it is an offer of the one thing the rule forbids.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTOW{_uid()[:5]}-BB"[:20])
        elsewhere = _warehouse(db, f"ZZTEW{_uid()[:5]}-IR"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, elsewhere, on_hand=650)
        _lead_time(db, product, 90)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        # 174 days out, against 90 days of lead time plus the 14-day buffer: well beyond.
        far = TODAY + timedelta(days=174)
        _line(db, order, product, qty="441", required_date=far, warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, far.isoformat())["contributions"][0]
        assert contribution["qty_proposed_buy"] == "441"
        buy = contribution["sources"][-1]
        assert buy["kind"] == "buy"
        # The engine's own wording, verbatim: `boardSuggestion.ts` reads this exact string to
        # tell a "beyond the window" Buy from a "nothing free anywhere" one on the card.
        assert buy["reason"] == (
            "Delivery date beyond the lead time window; stock kept for nearer orders"
        )
        assert "Borrowing is possible" not in buy["reason"]

        # Nothing to borrow is OFFERED either - not on the row, not in its total.
        assert contribution["borrow_candidates"] == []
        assert contribution["qty_borrow_available"] == "0"

        # And the trail says the two rungs were not walked, rather than pretending they were
        # checked and found empty.
        for kind in ("order_borrow", "supply_borrow"):
            step = _step(contribution, kind)
            assert step["answer"] == "no", kind
            assert step["answer"] == "no", kind
            assert "lead time window" in step["why"], kind
        assert _step(contribution, "buy")["took"] == "441"


# --------------------------------------------------------------------------- #
# the two nonsense sentences
# --------------------------------------------------------------------------- #


def test_a_contribution_never_names_its_own_sales_order_as_the_winner():
    """From the captain's paste: "Free stock at BRW-BB went to SO396563, which outranks this
    line 0.00 to 0.00" - printed on a contribution FROM SO396563. An order cannot outrank
    itself; when its own earlier line took the stock, that is what the sentence has to say."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        order = _order(db, so_number="ZZT-SO-SELF", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 1), warehouse=warehouse)
        _line(db, order, product, qty="10", required_date=date(2026, 9, 2), warehouse=warehouse)

        board = _service(db).build(["ZZT-SO-SELF"], granularity="week", as_of=TODAY)

        loser = _cell(board, product.product_code, "2026-08-31")["contributions"][1]
        buy = loser["sources"][-1]
        assert buy["kind"] == "buy"
        assert "ZZT-SO-SELF" not in buy["reason"], buy["reason"]
        assert "outranks" not in buy["reason"], buy["reason"]
        assert "earlier line of this sales order" in buy["reason"]


def test_equal_ranks_are_never_described_as_one_outranking_the_other():
    """"outranks this line 0.00 to 0.00" describes a TIE as a ranking. When the scores are
    equal the tiebreaker decided it, and the sentence has to say which."""
    with blank_session() as db:
        # No policy row at all, so every score is 0.0 - the live board's situation exactly.
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        first = _order(db, so_number="ZZT-SO-TIE1", order_date=date(2026, 1, 1))
        second = _order(db, so_number="ZZT-SO-TIE2", order_date=date(2026, 1, 1))
        _line(db, first, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, second, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-TIE1", "ZZT-SO-TIE2"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        assert [c["rank_score"] for c in cell["contributions"]] == [0.0, 0.0]
        buy = cell["contributions"][1]["sources"][-1]
        assert "outranks" not in buy["reason"], buy["reason"]
        assert "ZZT-SO-TIE1" in buy["reason"]
        assert "rank" in buy["reason"].lower() and "sales order number" in buy["reason"]


# --------------------------------------------------------------------------- #
# addressing: the board must be able to reach the confirm endpoint
#
# `POST /project-sales/sales-orders/{pso_id}/confirm` names its subject twice over, and
# neither name was on the board payload:
#   * `{pso_id}` is the PLANNING RECORD id (`projects.sales_orders.id`), not the core sales
#     order's - `ProjectSupplyService.get_order` looks it up by that id;
#   * `lines[].project_line_id` is the MIRROR line id (`projects.sales_order_lines.id`) -
#     `confirm` builds `by_id` from `lines_of(order.id)` and refuses anything else with
#     "That line is not on this sales order any more.";
#   * `lines[].reserve[].warehouse_id` is a warehouse UUID, checked against the line's
#     reserve reach (own location, or the pool, or the pool alone when hot-selling).
#
# So the test below is the contract: it builds the whole body from FIELDS PRESENT ON THE
# BOARD RESPONSE and nothing else. If it can be written, the frontend can do it.
# --------------------------------------------------------------------------- #


def _adopt(db, sales_order_id: str) -> str:
    from app.services.project_so_adoption_service import ProjectSOAdoptionService

    result = ProjectSOAdoptionService(db).adopt(sales_order_id)
    db.flush()
    return result["project_sales_order_id"]


def test_a_confirmation_can_be_built_from_the_board_payload_alone():
    from app.models.base import company_scope
    from app.models.project_so import SOSupplyDecision

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=3)
        _stock(db, product, pool, on_hand=10)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
        _adopt(db, str(order.id))
        db.commit()

        client, originals = _client(db, actor, [VIEW, EDIT])
        try:
            with company_scope(db, frozenset({company_id})):
                board = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": order.so_number, "granularity": "week"},
                ).json()

                # --- everything below comes off the board response, nothing else ---
                standing = board["orders"][0]
                pso_id = standing["project_sales_order_id"]
                assert pso_id, "the board must name the record the confirmation posts to"

                lines = []
                for cell in board["cells"]:
                    for contribution in cell["contributions"]:
                        lines.append(
                            {
                                "project_line_id": contribution["project_line_id"],
                                "timely_spo_qty": contribution["qty_proposed_incoming"],
                                "reserve": [
                                    {
                                        "warehouse_id": source["warehouse_id"],
                                        "qty": source["qty"],
                                    }
                                    for source in contribution["sources"]
                                    if source["kind"] == "reserve"
                                ],
                                "buy_qty": contribution["qty_proposed_buy"],
                            }
                        )

                response = client.post(
                    f"{BASE}/sales-orders/{pso_id}/confirm", json={"lines": lines}
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["revision_no"] == 1
        assert body["review_state"] == "confirmed"

        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == pso_id)
            .first()
        )
        covered = {
            snapshot["project_line_id"] for snapshot in decision.line_snapshots
        }
        assert covered == {line["project_line_id"] for line in lines}
        # The pool Reserve component was addressed by id (never `own`, which ladder v2 never
        # draws), which is the part a display code could never have carried.
        assert len(lines[0]["reserve"]) == 1
        assert lines[0]["reserve"][0]["warehouse_id"] == str(pool.id)


def test_an_unadopted_sales_order_says_so_with_a_null_rather_than_a_placeholder():
    """A board row nobody has started planning has no record to confirm against.

    Null, not absent and not an invented id: that IS the state of a not-started row, and it
    is what lets the screen say "Nobody has started planning this sales order yet" as a fact
    rather than as a guess about a missing field.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        standing = board["orders"][0]
        assert "project_sales_order_id" in standing
        assert standing["project_sales_order_id"] is None
        contribution = board["cells"][0]["contributions"][0]
        assert "project_line_id" in contribution
        assert contribution["project_line_id"] is None


def test_an_adopted_order_addresses_every_one_of_its_lines():
    with blank_session() as db:
        product_a = _product(db, f"ZZT-{_uid()[:6]}")
        product_b = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product_a, qty="4", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, order, product_b, qty="6", required_date=date(2026, 12, 1), warehouse=warehouse)
        pso_id = _adopt(db, str(order.id))

        board = _service(db).build([order.so_number], granularity="month", as_of=TODAY)

        assert board["orders"][0]["project_sales_order_id"] == pso_id
        line_ids = {
            contribution["project_line_id"]
            for cell in board["cells"]
            for contribution in cell["contributions"]
        }
        assert None not in line_ids
        assert len(line_ids) == 2, "one mirror line per contributing core line, all named"


def test_a_reserve_source_carries_the_warehouse_by_id_and_the_pool_names_the_pool():
    """Ladder v2: `own` is never touched, so the pool alone (which fully covers the line)
    carries the id."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=3)
        _stock(db, product, pool, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        sources = _cell(board, product.product_code, "2026-08-31")["contributions"][0][
            "sources"
        ]
        assert [(s["kind"], s["qty"], s["warehouse_id"]) for s in sources] == [
            ("reserve", "10", str(pool.id)),
        ]
        # A Buy is not held anywhere, so it names no warehouse.
        buy_only = [s for s in sources if s["kind"] == "buy"]
        assert all(s["warehouse_id"] is None for s in buy_only)


def test_a_core_line_added_since_adoption_is_mirrored_on_the_board_read_and_addressable():
    """Issue #969, owner ruling B2 (17 Sep 2026): the board READ self-heals the mirror.

    Adoption mirrors the order's open lines at the time it runs; next week's upload can add a
    core line that has no mirror yet. This used to be the one state where the two ids
    disagreed: `orders[].project_sales_order_id` was set but that LINE's `project_line_id`
    came back null, the frontend derived `no_mirror` from it and left the line out of the
    confirm body, and a manual Re-sync on the sheet was the only fix. Now `build` runs
    `ProjectSOAdoptionService.mirror_missing_lines` for each adopted, unauthored record
    (`status = 'adopted'`, `project_id IS NULL`) before it collects contributions, so the
    late line comes back with a real mirror id and the first Confirm posts every line.

    `_adopt` is the real `ProjectSOAdoptionService.adopt`, which leaves `project_id` NULL,
    so the seeded order already sits inside the heal's gate.
    """
    from app.models.project_so import ProjectSalesOrderLine

    with blank_session() as db:
        product_a = _product(db, f"ZZT-{_uid()[:6]}")
        product_b = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product_a, qty="4", required_date=date(2026, 9, 3), warehouse=warehouse)
        pso_id = _adopt(db, str(order.id))
        # The upload lands, and it carries a line adoption never saw.
        late = _line(
            db, order, product_b, qty="6", required_date=date(2026, 9, 3), warehouse=warehouse
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        assert board["orders"][0]["project_sales_order_id"] == pso_id
        named = {
            contribution["item_code"]: contribution["project_line_id"]
            for cell in board["cells"]
            for contribution in cell["contributions"]
        }
        assert named[product_a.product_code] is not None
        mirror_id = named[product_b.product_code]
        assert mirror_id is not None, "the board read must mirror the late line (#969)"

        mirrors = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == pso_id,
                ProjectSalesOrderLine.core_sales_order_line_id == str(late.id),
            )
            .all()
        )
        assert [m.id for m in mirrors] == [mirror_id], (
            "exactly one mirror row for the late core line, and it is the id the board named"
        )


# --------------------------------------------------------------------------- #
# the pressure on the pile
#
# The captain, reading a cell: "why is everything free, are you sure there is no SO occupying
# this?" They were right to doubt it. `_free_stock` subtracts `stock.quantity_reserved` (zero
# on most rows) and holds belonging to CONFIRMED decisions (two orders have ever been
# confirmed), so "free" is effectively raw on-hand - and the strip printed 478 free beside 482
# owed IN THIS CELL while 47,009 was owed at that location across 289 open lines.
#
# Nothing about allocation changes here. What changes is that the strip stops implying the pile
# is uncommitted: the demand on it is stated beside it.
# --------------------------------------------------------------------------- #


def _pressure_world(db):
    """One board order, and a book full of other orders wanting the same pile."""
    product = _product(db, f"ZZT-{_uid()[:6]}")
    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
    _stock(db, product, warehouse, on_hand=100)

    planned = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
    _line(db, planned, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

    # Not on the board, and both kinds count: the stock is occupied by whoever ordered it,
    # not only by the project orders this screen can plan.
    other_project = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
    _line(db, other_project, product, qty="40", required_date=date(2026, 10, 1), warehouse=warehouse)
    dealer = _order(
        db, so_number=f"ZZT-SO-C{_uid()[:6]}", order_date=date(2026, 1, 1), demand_class=None
    )
    _line(db, dealer, product, qty="25", required_date=date(2026, 10, 1), warehouse=warehouse)

    # None of these is still owed, so none of them may add to the pressure.
    spent = _order(db, so_number=f"ZZT-SO-D{_uid()[:6]}", order_date=date(2026, 1, 1))
    _line(db, spent, product, qty="50", delivered="50", required_date=date(2026, 10, 1),
          warehouse=warehouse)
    _line(db, spent, product, qty="30", required_date=date(2026, 10, 1), warehouse=warehouse,
          purchasing_status="covered")
    _line(db, spent, product, qty="20", required_date=date(2026, 10, 1), warehouse=warehouse,
          line_status="closed")
    closed_order = _order(
        db, so_number=f"ZZT-SO-E{_uid()[:6]}", order_date=date(2026, 1, 1), status="closed"
    )
    _line(db, closed_order, product, qty="70", required_date=date(2026, 10, 1),
          warehouse=warehouse)
    return planned, other_project, product, warehouse


def test_a_location_states_what_the_whole_book_owes_it_not_only_this_board():
    with blank_session() as db:
        planned, _other, product, _warehouse = _pressure_world(db)

        board = _service(db).build([planned.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["qty_demand"] == "10", "this cell's own demand is unchanged"
        # 10 on the board + 40 another project order + 25 a dealer order. The delivered,
        # covered, closed-line and closed-order quantities are not owed and do not count.
        assert location["qty_owed_all_orders"] == "75"


def test_the_pressure_figure_and_the_free_figure_are_read_from_the_same_facts():
    """"478 free" beside "47,009 owed" is the whole point: both, together, or neither."""
    with blank_session() as db:
        planned, _other, product, _warehouse = _pressure_world(db)

        board = _service(db).build([planned.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["qty_on_hand"] == "100"
        assert location["qty_reserved"] == "0"
        assert location["qty_free"] == "100"
        assert location["qty_owed_all_orders"] == "75"
        # Nothing is committed yet, so the two decision figures are zero rather than absent.
        assert location["qty_held_by_decisions"] == "0"
        assert location["qty_owed_confirmed"] == "0"


def test_a_confirmed_decision_shows_up_as_both_a_hold_and_covered_demand():
    """Two different facts, and the strip owes both.

    `qty_held_by_decisions` is the STOCK side - the quantity a confirmed decision is holding
    at this location, which is exactly what `_free_stock` subtracts, so on hand minus reserved
    minus held is the free figure and the arithmetic on screen closes.

    `qty_owed_confirmed` is the DEMAND side - how much of what is owed here sits on lines a
    confirmed decision already covers, so a planner can tell committed pressure from
    uncommitted pressure.
    """
    from datetime import datetime

    from app.models.project_so import (
        DECISION_ACTIVE,
        SO_STATUS_ADOPTED,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
        SOLineAllocation,
        SOSupplyDecision,
    )

    with blank_session() as db:
        company_id = _sorento(db)
        planned, other, product, warehouse = _pressure_world(db)
        core_line = (
            db.query(SalesOrderLine)
            .filter(SalesOrderLine.sales_order_id == other.id)
            .first()
        )
        record = ProjectSalesOrder(
            id=_uid(), company_id=company_id, project_id=None,
            provisional_ref=other.so_number, autocount_doc_no=other.so_number,
            so_id=other.id, status=SO_STATUS_ADOPTED, grouping_origin="area",
        )
        db.add(record)
        db.flush()
        mirror = ProjectSalesOrderLine(
            id=_uid(), company_id=company_id, project_sales_order_id=record.id, line_no=1,
            product_id=product.id, description=f"{MARKER} mirror", qty=Decimal("40"),
            uom="UNIT", unit_price=Decimal("1.00"), amount=Decimal("40.00"),
            delivery_date=date(2026, 10, 1), core_sales_order_line_id=core_line.id,
        )
        decision = SOSupplyDecision(
            id=_uid(), company_id=company_id, project_sales_order_id=record.id,
            revision_no=1, state=DECISION_ACTIVE, confirmed_at=datetime.utcnow(),
            # A line is covered iff `line_snapshots` holds an object for it, carrying the
            # CORE line id - that JSONB is what `committed_v` reads and therefore what
            # "already confirmed" has to mean here too (13.4, migration 384). An allocation
            # row alone holds stock but says nothing about which demand is covered.
            line_snapshots=[{"core_line_id": str(core_line.id), "line_no": 1}],
        )
        db.add_all([mirror, decision])
        db.flush()
        db.add(
            SOLineAllocation(
                id=_uid(), company_id=company_id, so_line_id=mirror.id,
                source_type="own", warehouse_id=warehouse.id, qty=Decimal("15"),
                decision_id=decision.id, confirmed_at=datetime.utcnow(),
            )
        )
        db.flush()

        board = _service(db).build([planned.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["qty_on_hand"] == "100"
        assert location["qty_held_by_decisions"] == "15"
        # On hand, less reserved, less held, IS the free figure the proposal was computed from.
        assert location["qty_free"] == "85"
        assert location["qty_owed_all_orders"] == "75"
        # Of that 75, the 40 owed on the confirmed order is committed pressure.
        assert location["qty_owed_confirmed"] == "40"


def test_the_pressure_is_stated_for_every_location_of_a_multi_location_cell():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        here = _warehouse(db, f"ZZTA{_uid()[:6]}"[:20])
        there = _warehouse(db, f"ZZTB{_uid()[:6]}"[:20])
        _stock(db, product, here, on_hand=10)
        _stock(db, product, there, on_hand=10)
        planned = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, planned, product, qty="4", required_date=date(2026, 9, 3), warehouse=here)
        _line(db, planned, product, qty="6", required_date=date(2026, 9, 3), warehouse=there)
        crowd = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, crowd, product, qty="500", required_date=date(2026, 10, 1), warehouse=there)

        board = _service(db).build([planned.so_number], granularity="week", as_of=TODAY)

        pressure = {
            entry["location"]: entry["qty_owed_all_orders"]
            for entry in _cell(board, product.product_code, "2026-08-31")["locations"]
        }
        assert pressure[here.warehouse_code] == "4"
        assert pressure[there.warehouse_code] == "506", "per location, never pooled together"


def _agent(db, code: str, *, location_group: str):
    from app.models.sales_agent import SalesAgent

    row = SalesAgent(
        id=_uid(), sales_agent=code, source="manual", is_active=True,
        location_group=location_group,
    )
    db.add(row)
    db.flush()
    return row


def test_the_locations_include_the_site_pool_the_suggestion_cites():
    """The captain, on SO415472: the card read "Use own location 71 from BRW - Pool BRW has
    1716 available" while the table listed BRW-BB / DC1-BB / MWH-BB / RSW-BB / WH3-BB, every
    one of them "Not stated" on hand. So the 1716 the decision rests on was nowhere on screen.

    The pool is a WAREHOUSE of its own (`warehouses.pool_warehouse_id` points at it; on the
    live book BRW-BB's pool is the warehouse coded BRW, which held 1728), not a roll-up of the
    BRW-* codes - so the figure reconciles to exactly one row, once that row is listed. The
    table lists every location the ladder actually consulted for this cell: the lines' own,
    the agent's ownership group, and the pool a proposal cites - each tagged with where it
    stands, so the reader can tell the group from the pool.
    """
    with blank_session() as db:
        group = f"Z{_uid()[:3]}".upper()
        product = _product(db, f"ZZT-{_uid()[:6]}")
        pool = _warehouse(db, f"ZZTPOOL{_uid()[:5]}"[:20])
        own = _warehouse(db, f"ZZTA{_uid()[:4]}-{group}"[:20])
        sibling = _warehouse(db, f"ZZTB{_uid()[:4]}-{group}"[:20])
        own.pool_warehouse_id = pool.id
        sibling.pool_warehouse_id = pool.id
        db.flush()
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=1728)
        agent = _agent(db, f"ZZT-CINDY-{_uid()[:4]}", location_group=group)

        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        order.sales_agent_id = agent.id
        db.flush()
        _line(db, order, product, qty="71", required_date=date(2026, 9, 3), warehouse=own)
        # Somebody else's open demand AT THE POOL, so the pool's Available is 1728 - 12 and
        # not simply its on-hand: the figure the card cites has to be the netted one.
        crowd = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, crowd, product, qty="12", required_date=date(2026, 10, 1), warehouse=pool)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, product.product_code, "2026-08-31")
        where = {entry["location"]: entry["where"] for entry in cell["locations"]}
        assert where[own.warehouse_code] == "own"
        assert where[sibling.warehouse_code] == "group"
        assert where[pool.warehouse_code] == "site_pool", "the pool row was missing entirely"

        pool_row = next(
            entry for entry in cell["locations"]
            if entry["location"] == pool.warehouse_code
        )
        assert pool_row["qty_on_hand"] == "1728"
        assert pool_row["so_qty"] == "12"
        # The number the card quotes, on a row of the table: 1728 - 12.
        assert pool_row["available_qty"] == "1716"
        source = cell["contributions"][0]["sources"][0]
        assert source["location"] == pool.warehouse_code
        # Ladder v8: the quantity is a share of the ALLOWANCE - what the pool may lend a
        # project once the dealers' half is kept back - and the sentence names that rather
        # than the whole pile, which would invite the next planner to ask for the other
        # half. The reason names what was TAKEN beside it: the sentence travels with the
        # quantity next to it.
        assert source["reason"] == (
            f"Pool {pool.warehouse_code} spares 71 of the 858 it may lend a project."
        )


def test_a_line_with_no_location_states_no_pressure_either():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=None)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["qty_owed_all_orders"] is None
        assert location["qty_held_by_decisions"] is None
        assert location["qty_owed_confirmed"] is None


# --------------------------------------------------------------------------- #
# the board and the confirmation must agree about how much may be reserved
#
# Live defect, SO396351 (ORIONIS TECHNOLOGY): the board proposed Reserve at BRW-BB and named
# BRW-BB's own warehouse id, and the confirmation refused every line with "Reserve may only come
# from this line's own location or the shared pool. Move that quantity to Borrow."
#
# Both sides agreed about the LOCATIONS - measured on that order, the confirm's reserve reach is
# {BRW-BB 75842575..., BRW 21608757...} and the board posted 75842575..., which is in it. What
# they disagreed about is HOW MUCH BRW-BB may contribute to that line: the board offered its
# share of the pile as contested among the SELECTED orders, and the confirmation computes it as
# the line's share across the WHOLE BOOK, which for that product at that location is 0 (47,009
# owed against 478 on hand). `reserve_capacity` omits a location contributing nothing, so the
# id fell through the "not in capacity" branch and reported a location error for a quantity
# problem.
# --------------------------------------------------------------------------- #


def _crowded_pile(db, *, on_hand: int, mine: str, theirs: str, their_date: date):
    """Free stock at one location, and an earlier-dated order elsewhere in the book wanting it."""
    product = _product(db, f"ZZT-{_uid()[:6]}")
    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
    _stock(db, product, warehouse, on_hand=on_hand)
    mine_order = _order(db, so_number=f"ZZT-SO-M{_uid()[:6]}", order_date=date(2026, 1, 1))
    _line(db, mine_order, product, qty=mine, required_date=date(2026, 12, 29),
          warehouse=warehouse)
    # Not on the board, dated earlier, and it claims the pile first.
    theirs_order = _order(db, so_number=f"ZZT-SO-T{_uid()[:6]}", order_date=date(2026, 1, 1))
    _line(db, theirs_order, product, qty=theirs, required_date=their_date, warehouse=warehouse)
    return mine_order, product, warehouse


def test_the_board_does_not_propose_a_reserve_the_confirmation_would_refuse():
    """The live defect, as arithmetic: 100 free, 400 owed ahead of us, we are owed 23.

    The confirmation gives this line a share of 0 at its own location, so a board that offers
    it 23 there is offering something that cannot be committed. Nothing about what the system
    RESERVES changes here - the confirmation already refused it. What changes is that the board
    stops proposing it.
    """
    with blank_session() as db:
        mine, product, warehouse = _crowded_pile(
            db, on_hand=100, mine="23", theirs="400", their_date=date(2026, 6, 1)
        )

        board = _service(db).build([mine.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-12-28")["contributions"][0]
        assert contribution["qty_proposed_reserve"] == "0", (
            "the whole pile is already claimed by earlier-dated demand"
        )
        assert contribution["qty_proposed_buy"] == "23"
        assert contribution["contested"] is True
        # And the strip still states the pile, so the planner can see WHY it got nothing.
        location = _cell(board, product.product_code, "2026-12-28")["locations"][0]
        assert location["qty_free"] == "100"
        assert location["qty_owed_all_orders"] == "423"


# `test_the_board_still_reserves_what_the_book_leaves_for_this_line` DELETED (ladder v2,
# section E rule 7): it asserted an own-location Reserve sized by what the book's queue left
# behind (100 on hand, 70 claimed first, 30 reserved here) - the own location is never a
# Reserve source any more, own or otherwise, so that composition cannot occur. The informal
# "what the queue left" figures survive as read-only strip fields and are covered instead by
# `test_the_line_ranked_first_at_a_pile_still_gets_its_whole_reserve` and
# `test_demand_a_confirmed_decision_already_holds_is_not_subtracted_twice` below.


def test_every_reserve_the_board_proposes_is_accepted_by_the_confirmation():
    """The contract, extended to the case that broke: a crowded OWN location and a pool that
    covers the line whole.

    Ladder v3: `own` is crowded by an earlier order that claims all 30 of it, so it offers
    this line nothing and the whole 40 has to come from the pool alone, or nothing - the
    whole-line rule leaves no room for a mixed "reserve part, buy part" composition, so the
    pool here holds enough to cover it completely.

    The body is built only from board fields, as the frontend builds it, and the assertion is
    that the confirmation takes it. If the board can propose something unconfirmable, this
    fails.
    """
    from app.models.base import company_scope
    from app.models.project_so import SOSupplyDecision

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        # Nothing free at the line's own location once the earlier order is counted - own is
        # never a Reserve source anyway - and a pool that covers the whole of it.
        _stock(db, product, own, on_hand=30)
        # 80 in the pool: ladder v8 lets a project line have half of it (R-B), so covering
        # the 40 whole from the pool alone takes 80 in it.
        _stock(db, product, pool, on_hand=80)
        # A year of lead time, so the December line is INSIDE its reserve window and the
        # ladder actually runs: v3 buys a line beyond the window whole (section 1b rung 0).
        _lead_time(db, product, 365)
        mine = _order(db, so_number=f"ZZT-SO-M{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, mine, product, qty="40", required_date=date(2026, 12, 29), warehouse=own)
        crowd = _order(db, so_number=f"ZZT-SO-T{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, crowd, product, qty="30", required_date=date(2026, 6, 1), warehouse=own)
        _adopt(db, str(mine.id))
        db.commit()

        client, originals = _client(db, actor, [VIEW, EDIT])
        try:
            with company_scope(db, frozenset({company_id})):
                board = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": mine.so_number, "granularity": "week"},
                ).json()
                pso_id = board["orders"][0]["project_sales_order_id"]
                lines = [
                    {
                        "project_line_id": contribution["project_line_id"],
                        "timely_spo_qty": contribution["qty_proposed_incoming"],
                        "reserve": [
                            {"warehouse_id": source["warehouse_id"], "qty": source["qty"]}
                            for source in contribution["sources"]
                            if source["kind"] == "reserve"
                        ],
                        "buy_qty": contribution["qty_proposed_buy"],
                    }
                    for cell in board["cells"]
                    for contribution in cell["contributions"]
                ]
                response = client.post(
                    f"{BASE}/sales-orders/{pso_id}/confirm", json={"lines": lines}
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        # The pool covered the whole line, addressed by its own id rather than the line's
        # (crowded, never-drawn) own location id.
        reserve = lines[0]["reserve"]
        assert [(item["warehouse_id"], item["qty"]) for item in reserve] == [
            (str(pool.id), "40")
        ]
        assert lines[0]["buy_qty"] == "0"
        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == pso_id)
            .first()
        )
        assert decision.revision_no == 1


def test_a_reserve_at_a_stranger_warehouse_is_refused_naming_the_pool_and_an_empty_pool_reserve_is_recorded_short():
    """The message the captain read was "Reserve may only come from this line's own location or
    the shared pool. Move that quantity to Borrow." - printed about their own location, naming
    no warehouse, and pointing at a control the board does not have.

    Ladder v2 (section E rule 7): the line's own location is NEVER an allowed Reserve location
    any more, so "what IS allowed" is the shared pool - the two refusals below are "wrong
    place entirely" (a stranger warehouse) versus "the right place, but nothing free there"
    (the pool itself). The first stays a structural refusal (the location is not allowed);
    the second is no longer refused: the order confirms as decided and the notice names the
    warehouse, what was asked and how much is short.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        stranger = _warehouse(db, f"ZZTX{_uid()[:6]}"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=0)
        _stock(db, product, stranger, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
        pso_id = _adopt(db, str(order.id))
        # Through the ORM, never raw SQL naming `projects.*`: a schema-translated scratch
        # session resolves that qualified name to the REAL projects schema.
        from app.models.project_so import ProjectSalesOrderLine

        line_id = str(
            db.query(ProjectSalesOrderLine.id)
            .filter(ProjectSalesOrderLine.project_sales_order_id == pso_id)
            .scalar()
        )
        db.commit()

        client, originals = _client(db, actor, [VIEW, EDIT])
        try:
            with company_scope(db, frozenset({company_id})):
                # 1. a warehouse that is neither the line's own nor its pool
                elsewhere = client.post(
                    f"{BASE}/sales-orders/{pso_id}/confirm",
                    json={
                        "lines": [
                            {
                                "project_line_id": line_id,
                                "reserve": [
                                    {"warehouse_id": str(stranger.id), "qty": "10"}
                                ],
                                "buy_qty": "0",
                            }
                        ]
                    },
                )
                # 2. the pool itself, which simply has nothing free for it
                empty = client.post(
                    f"{BASE}/sales-orders/{pso_id}/confirm",
                    json={
                        "lines": [
                            {
                                "project_line_id": line_id,
                                "reserve": [{"warehouse_id": str(pool.id), "qty": "10"}],
                                "buy_qty": "0",
                            }
                        ]
                    },
                )
        finally:
            _restore(originals)

        wrong_place = elsewhere.json()["failing_lines"][0]["reason"]
        assert stranger.warehouse_code in wrong_place, wrong_place
        assert pool.warehouse_code in wrong_place, "it must name what IS allowed"
        assert own.warehouse_code not in wrong_place, "the own location is never allowed"
        assert "Borrow" not in wrong_place, "the board has no Borrow control"
        assert "borrow it from that location on the order's own sheet" in wrong_place

        assert empty.status_code == 200, empty.text
        [notice] = empty.json()["stock_short_notices"]
        assert notice["reason"] == (
            f"Reserve 10 at {pool.warehouse_code} confirmed as decided; only 0 free at "
            f"{pool.warehouse_code} when confirmed, 10 short"
        ), notice
        assert _holds_at(db, pso_id, pool) == {}, "nothing is held where nothing is free"


# --------------------------------------------------------------------------- #
# THE ASKING LINE IS NOT ITS OWN COMPETITION
# (`PLAN-scm-planning-inline-decisions.md` section 3.B, ruling R1)
#
# The captain, walking SO404352 line 22 (SRTWB7518, BRW-AM): the drawer said "Use own location,
# 9 from BRW-AM" while the AM group row read Available -15. Same data, two definitions - the
# engine's offer is `max(group net + this line's own quantity, 0)` and nets every OTHER line,
# while the drawer's SO qty counted the asking line's own 24 as demand competing with itself.
#
# So SO qty on a location row is the OTHER open lines there, full stop (R15: no tooltip, no
# second field), and the group subtotal prints the same figure the ladder obeyed.
# --------------------------------------------------------------------------- #


def _group_world(
    db,
    *,
    on_hand: int,
    other_qty: int,
    own_qty: str,
    pool_qty: int = 16,
    second_qty: str | None = None,
):
    """SO404352's own shape: one ownership group of one warehouse, with the site pool
    behind it, this cell's line, and an earlier order's demand at the same location.

    The pool matters to the assertions and not only to the scenery: a line is either wholly
    met from stock or wholly bought, so a group offering 9 of a 24 with nowhere else to go
    buys the whole line and the offer never reaches a component. 16 in the pool is the
    fixture's own figure, and 9 + 15 is what covers the line.

    `second_qty` puts a SECOND line of the same order in the same cell, wanted on the same
    date and at the same location - the case the netting has to answer per contribution
    rather than per cell.
    """
    group = f"Z{_uid()[:3]}".upper()
    product = _product(db, f"ZZT-{_uid()[:6]}")
    pool = _warehouse(db, f"ZZTP{_uid()[:5]}"[:20])
    own = _warehouse(db, f"ZZTA{_uid()[:4]}-{group}"[:20])
    own.pool_warehouse_id = pool.id
    db.flush()
    _stock(db, product, own, on_hand=on_hand)
    _stock(db, product, pool, on_hand=pool_qty)
    agent = _agent(db, f"ZZT-AM-{_uid()[:4]}", location_group=group)

    mine = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
    mine.sales_agent_id = agent.id
    db.flush()
    _line(db, mine, product, qty=own_qty, required_date=date(2026, 9, 3), warehouse=own)
    if second_qty is not None:
        _line(
            db, mine, product, qty=second_qty, required_date=date(2026, 9, 3), warehouse=own
        )

    # SO383850's shape: an earlier order holding demand at the same location, not on the board.
    theirs = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
    _line(db, theirs, product, qty=str(other_qty), required_date=date(2026, 4, 1),
          warehouse=own)
    return group, product, own, mine


def test_a_location_row_counts_the_other_lines_demand_and_not_the_asking_lines_own():
    """SO404352's own numbers: 10 on hand, 1 held by another order, 24 asked for here.

    SO qty reads 1 (the other order), Available reads 9, and the group subtotal reads the
    SAME 9 the ladder drew on - so the drawer and the suggestion cannot disagree again.
    """
    with blank_session() as db:
        group, product, own, mine = _group_world(db, on_hand=10, other_qty=1, own_qty="24")

        board = _service(db).build([mine.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, product.product_code, "2026-08-31")
        row = next(e for e in cell["locations"] if e["location"] == own.warehouse_code)
        assert row["qty_on_hand"] == "10"
        assert row["so_qty"] == "1", "the asking line's own 24 is not demand against itself"
        assert row["spo_qty"] == "0"
        assert row["available_qty"] == "9", "on hand - the other lines + SPO"
        # The whole book's pressure is unchanged and still stated: 1 + 24 owed here.
        assert row["qty_owed_all_orders"] == "25"
        # The subtotal the table prints for the group, which is the engine's own offer.
        assert row["net_of"] == group
        assert row["net"] == "9"
        # And step 1 was OFFERED exactly that: the sentence names the 9 it had for this
        # line. Nothing is drawn FROM THE GROUP, because a step covers what it is asked
        # about whole or gives nothing (R33) - 9 of a 24 is not the answer to anything.
        contribution = cell["contributions"][0]
        taken = sum(
            Decimal(source["qty"]) for source in contribution["sources"]
            if source.get("rung") == "group_take"
        )
        assert taken == Decimal("0")
        used = next(step for step in contribution["trail"] if step["kind"] == "own")
        assert "9" in used["why"], used["why"]
        # LADDER V8 (R-B/R-C): the site pool's SHARE is its own sub-unit and does answer -
        # half of its 16 - and what is left of the line is bought. The group is still
        # whole-or-nothing and still gives nothing.
        assert [
            (source["kind"], source["qty"], source.get("rung"))
            for source in contribution["sources"]
        ] == [("reserve", "8", "pool"), ("buy", "16", "buy")]


def test_a_group_the_other_lines_alone_oversell_offers_nothing_and_says_so():
    """10 on hand against 12 held by other lines: the subtotal reads -2, and NOTHING is
    offered from the group - a negative subtotal and a `group_take` component can never
    stand side by side (R1, AC-B3/B4)."""
    with blank_session() as db:
        _group, product, own, mine = _group_world(db, on_hand=10, other_qty=12, own_qty="24")

        board = _service(db).build([mine.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, product.product_code, "2026-08-31")
        row = next(e for e in cell["locations"] if e["location"] == own.warehouse_code)
        assert row["so_qty"] == "12"
        assert row["available_qty"] == "-2", "oversold by the other lines, never clamped"
        assert row["net"] == "-2"
        assert not [
            source
            for source in cell["contributions"][0]["sources"]
            if source.get("rung") == "group_take"
        ], "a group that cannot cover its own book offers this line nothing"


def test_the_group_subtotal_is_the_offer_the_ladder_obeyed_on_every_contribution():
    """The pin (AC-B4): for the cell's line, the own group's subtotal Available IS the
    engine's `group_offer`, and a negative subtotal never coexists with a group take."""
    with blank_session() as db:
        _group, product, own, mine = _group_world(db, on_hand=10, other_qty=1, own_qty="24")

        service = _service(db)
        board = service.build([mine.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, "2026-08-31")
        row = next(e for e in cell["locations"] if e["location"] == own.warehouse_code)

        # The engine's own reader, off the same request's pile facts the board built on.
        netting = service.supply.netting()
        group_net = netting.group_net(str(product.id), netting.group_of(str(own.id))).net
        offer = max(group_net + Decimal("24"), Decimal("0"))

        assert offer > 0
        assert Decimal(row["net"]) == offer
        # The subtotal IS the offer step 1 obeyed; whether it was DRAWN is the whole-unit
        # rule's business (R33), and 9 cannot finish 24, so nothing is.
        contribution = cell["contributions"][0]
        used = next(step for step in contribution["trail"] if step["kind"] == "own")
        assert str(int(offer)) in used["why"], used["why"]
        taken = sum(
            Decimal(source["qty"])
            for source in contribution["sources"]
            if source.get("rung") == "group_take"
        )
        assert taken == Decimal("0")
        # The cell's own table IS that line's table when the cell holds one line: one set of
        # figures, not a cell reading and a line reading of the same pile.
        assert cell["locations"] == cell["contributions"][0]["locations"]


def test_each_line_of_a_two_line_cell_nets_only_its_own_quantity():
    """AC-B4 where the cell holds TWO lines: 10 on hand, 1 held elsewhere, 24 + 24 asked.

    The netting used to come out of the WHOLE cell, so the row printed SO qty 1 and
    Available 9 while the ladder offered each line nothing - `_group_offer` is
    `max(group net + THIS line's own quantity, 0)`, and with 49 owed at the location that is
    `max(-39 + 24, 0)`. A planner reading 9 beside a suggestion to buy the lot is back at
    the two-definitions defect R1 was raised to end.

    So every figure is per CONTRIBUTION: SO qty is the pressure less the asking line's own
    open quantity, and the group subtotal is the group's net plus that same quantity.
    """
    with blank_session() as db:
        group, product, own, mine = _group_world(
            db, on_hand=10, other_qty=1, own_qty="24", second_qty="24"
        )

        service = _service(db)
        board = service.build([mine.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, "2026-08-31")
        assert len(cell["contributions"]) == 2

        netting = service.supply.netting()
        group_net = netting.group_net(str(product.id), netting.group_of(str(own.id))).net
        assert group_net == Decimal("-39"), "10 on hand against 49 owed at the location"

        for contribution in cell["contributions"]:
            row = next(
                entry
                for entry in contribution["locations"]
                if entry["location"] == own.warehouse_code
            )
            assert row["qty_owed_all_orders"] == "49", "the whole book, unchanged"
            assert row["so_qty"] == "25", "49 owed here, less this line's own 24"
            assert row["available_qty"] == "-15"
            assert row["net_of"] == group
            own_qty = Decimal(contribution["qty_outstanding"])
            assert Decimal(row["net"]) == group_net + own_qty
            # The pin itself: the subtotal IS the offer, and a negative one offers nothing.
            offer = max(group_net + own_qty, Decimal("0"))
            assert offer == Decimal("0")
            assert not [
                source
                for source in contribution["sources"]
                if source.get("rung") == "group_take"
            ], "a subtotal below zero and a group take can never stand side by side"


def test_a_second_product_behind_one_item_code_is_not_netted_out_of_the_first():
    """Two products share the item code `B2155-NL-BLUE` on the live book, and they land in
    ONE cell (cells are keyed by item code).

    Their demand is not each other's: netting by warehouse alone took the second product's
    30 out of the first product's SO qty and printed stock nobody has. The key is
    `(product, warehouse)`, and the lookup uses the row's OWN product.
    """
    with blank_session() as db:
        code = f"ZZT-{_uid()[:6]}"
        first = _product(db, code)
        # One code, two products - which `uq_products_company_product_code` allows only
        # across companies, and is how the live pair came to exist. Seeded under its own
        # code and moved, because the helper flushes before the company can be set.
        second = _product(db, f"ZZT-{_uid()[:6]}")
        second.company_id = _mocha(db)
        second.product_code = code
        db.flush()
        warehouse = _warehouse(db, f"ZZT{_uid()[:6]}"[:20])
        _stock(db, first, warehouse, on_hand=10)
        _stock(db, second, warehouse, on_hand=10)

        mine = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, mine, first, qty="24", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, mine, second, qty="30", required_date=date(2026, 9, 3), warehouse=warehouse)
        # Another order's demand against the FIRST product only.
        theirs = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, theirs, first, qty="50", required_date=date(2026, 4, 1), warehouse=warehouse)

        board = _service(db).build([mine.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, code, "2026-08-31")
        asking = next(
            entry for entry in cell["contributions"] if entry["product_id"] == str(first.id)
        )
        row = next(
            entry
            for entry in asking["locations"]
            if entry["location"] == warehouse.warehouse_code
            and entry["product_id"] == str(first.id)
        )
        assert row["qty_owed_all_orders"] == "74", "50 + this line's 24, on this product"
        assert row["so_qty"] == "50", "the other ORDER's demand, not the other product's line"
        assert row["available_qty"] == "-40"


# --------------------------------------------------------------------------- #
# AutoCount's vocabulary on the strip
#
# The captain, reading "BRW-BB - 80 owed - 1015 on hand - 1015 free - 0 incoming": "i am trying
# to make sense of the 1015 on hand and 1015 free, cause that doesn't make sense... in autocount,
# when we check stock we will see on hand quantity, SO quantity, PO quantity... so when a SO is
# created, it already flows to the outstanding quantity... can we show something like autocount
# to justify the net quantity i.e. on hand - so + spo quantity or available quantity".
#
# So the strip leads with the four columns AutoCount's Stock Status screen leads with, and
# `available_qty` is allowed to be NEGATIVE, because "oversold here by 632" is the signal.
# --------------------------------------------------------------------------- #


def test_the_strip_states_the_autocount_four_and_they_reconcile():
    with blank_session() as db:
        planned, _other, product, warehouse = _pressure_world(db)
        _incoming(
            db, product, warehouse,
            spo_number="ZZT-SPO-STRIP", allocated=30, received=10, arrives=date(2026, 9, 1),
        )

        board = _service(db).build([planned.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        # On hand 100, owed across the book 75 - of which this cell's own line owes 10, and a
        # line does not compete with itself (R1) - incoming 20 (30 allocated, 10 received).
        assert location["qty_on_hand"] == "100"
        assert location["qty_owed_all_orders"] == "75", "the whole book, unchanged"
        assert location["so_qty"] == "65", "the OTHER lines: 75 owed here less this cell's 10"
        assert location["spo_qty"] == "20"
        assert location["available_qty"] == "55", "on hand - SO + SPO, AutoCount's own sum"
        # The engine's own figure stays, under its own name, and its reconciliation still
        # closes for anyone who needs it.
        assert location["qty_free"] == "100"
        assert location["qty_reserved"] == "0"
        assert location["qty_held_by_decisions"] == "0"


def test_available_goes_negative_rather_than_being_clamped():
    """A clamp would turn "we are oversold by 300" into "we have nothing", which is a
    different fact and the less useful one."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=100)
        mine = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, mine, product, qty="50", required_date=date(2026, 9, 3), warehouse=warehouse)
        crowd = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, crowd, product, qty="350", required_date=date(2026, 10, 1), warehouse=warehouse)

        board = _service(db).build([mine.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        # 400 owed here across the book, 50 of it this cell's own line: 350 is the demand it
        # is actually up against, and 100 - 350 is what it can have.
        assert location["qty_owed_all_orders"] == "400"
        assert location["so_qty"] == "350"
        assert location["available_qty"] == "-250"


def test_the_strip_carries_the_ids_the_drill_down_is_addressed_by():
    """The drill-down takes ids, and the screen must not have to derive one from a code."""
    with blank_session() as db:
        planned, _other, product, warehouse = _pressure_world(db)

        board = _service(db).build([planned.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["product_id"] == str(product.id)
        assert location["warehouse_id"] == str(warehouse.id)


def test_a_line_with_no_location_carries_no_ids_and_no_autocount_figures():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=None)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        assert location["warehouse_id"] is None
        assert location["so_qty"] is None
        assert location["spo_qty"] is None
        assert location["available_qty"] is None


# --------------------------------------------------------------------------- #
# the drill-down: the documents that justify the totals
# --------------------------------------------------------------------------- #


def test_the_drill_down_lists_the_documents_behind_the_totals():
    with blank_session() as db:
        planned, other, product, warehouse = _pressure_world(db)
        _incoming(
            db, product, warehouse,
            spo_number="ZZT-SPO-DRILL", allocated=30, received=10, arrives=date(2026, 9, 1),
        )

        detail = _service(db).stock_detail(str(product.id), str(warehouse.id))

        assert detail["item_code"] == product.product_code
        assert detail["location"] == warehouse.warehouse_code
        assert detail["qty_on_hand"] == "100"
        assert detail["so_qty"] == "75"
        assert detail["spo_qty"] == "20"
        assert detail["available_qty"] == "45"
        # Every open sales-order line at that product and location, whatever its demand class,
        # because that is what occupies the stock.
        assert {row["so_number"] for row in detail["sales_orders"]} == {
            planned.so_number, other.so_number,
            *[r["so_number"] for r in detail["sales_orders"]
              if r["so_number"] not in (planned.so_number, other.so_number)],
        }
        assert sum(float(row["so_qty"]) for row in detail["sales_orders"]) == 75.0
        incoming = detail["incoming"]
        assert [(row["spo_number"], row["spo_qty"], row["expected_date"]) for row in incoming] == [
            ("ZZT-SPO-DRILL", "20", date(2026, 9, 1))
        ]


def test_the_drill_down_states_the_assumed_arrival_of_a_late_document():
    """AC-O.3's Stock tab half (R-O, 3 Sep 2026, #586).

    A document whose arrival has passed is planned against `today + overdue_grace_days`, so
    the ledger row carries that day BESIDE the one the paperwork states - the panel prints
    "assumed 17 Sep 2026, stated 24 Jul". A document past `overdue_dead_days` is counted as
    nothing (R31) and its row says so instead of naming a date nobody can plan on.

    R-O SHIPS at 0 / 0 (captain's ruling, 3 Sep 2026), so this fixture activates the
    RECOMMENDED 14 / 90 itself - it is proving the grace RULE, not the number production
    starts at.

    Both fields are asserted BY NAME: `response_model` drops what a schema does not
    declare, and this is the second time a field on this very row went out silently.
    """
    from datetime import timedelta

    today = date.today()
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _policy(db, {}, overdue_grace_days=14, overdue_dead_days=90)
        _stock(db, product, warehouse, on_hand=0)
        _incoming(
            db, product, warehouse, spo_number="ZZT-SPO-LATE", allocated=100,
            received=0, arrives=today - timedelta(days=41),
        )
        _incoming(
            db, product, warehouse, spo_number="ZZT-SPO-DEAD", allocated=500,
            received=0, arrives=today - timedelta(days=241),
        )

        rows = {
            row["spo_number"]: row
            for row in _service(db).stock_detail(str(product.id), str(warehouse.id))[
                "incoming"
            ]
        }

    late = rows["ZZT-SPO-LATE"]
    assert late["expected_date"] == today - timedelta(days=41), "the paperwork's own date"
    assert late["assumed_date"] == today + timedelta(days=14), "the day the walk plans on"
    assert late["counted"] is True
    assert late["overdue_days"] == 41

    dead = rows["ZZT-SPO-DEAD"]
    assert dead["assumed_date"] is None, "nothing is assumed about a document counted as nothing"
    assert dead["counted"] is False


def test_an_empty_settings_read_still_uses_the_walks_own_grace_and_dead_defaults():
    """Review fix round, S4: `_fulfilment_settings()` returns `{}` on its own defensive
    except (no policy row, or a mid-migration branch). The ledger used to read that with a
    bare `or 0` rather than `supply_assignment.DEFAULT_OVERDUE_GRACE_DAYS` /
    `DEFAULT_OVERDUE_DEAD_DAYS` - the same constants the walk itself falls back to
    (`compute_overdue_event`) - so a document could be "Not counted" on the ledger while the
    walk itself still counted it as supply. The two disagreeing about the same book is
    exactly what R-O exists to prevent.

    Read off the SAME constants here rather than a hardcoded pair, so this keeps proving
    the invariant (ledger and walk fall back to identical numbers) whatever the SHIPPED
    default happens to be - 0 / 0 as of the captain's 3 Sep 2026 ruling, which makes ANY
    lateness dead (R31), so the 41-day-late document below reads "not counted" on BOTH
    sides rather than the 14/90-days "still counts" this test asserted before that ruling.
    """
    from datetime import timedelta

    from app.services.scm import supply_assignment

    today = date.today()
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=0)
        _incoming(
            db, product, warehouse, spo_number="ZZT-SPO-LATE", allocated=100,
            received=0, arrives=today - timedelta(days=41),
        )

        service = _service(db)
        service.supply.fulfilment_settings = lambda: {}
        rows = {
            row["spo_number"]: row
            for row in service.stock_detail(str(product.id), str(warehouse.id))["incoming"]
        }

    late = rows["ZZT-SPO-LATE"]
    grace = supply_assignment.DEFAULT_OVERDUE_GRACE_DAYS
    dead = supply_assignment.DEFAULT_OVERDUE_DEAD_DAYS
    is_dead = 41 > dead
    assert late["counted"] is (not is_dead), (
        "the ledger's fallback is the SAME constant the walk itself falls back to"
    )
    assert late["assumed_date"] == (
        None if is_dead else today + timedelta(days=grace)
    )


def test_the_drill_down_total_is_the_same_number_the_cell_printed():
    """The list has to ADD UP to the strip, or the drill-down justifies nothing.

    The drill-down is the WHOLE book at that location - it lists this cell's own line too,
    tagged as this line - so it reconciles against `qty_owed_all_orders`. The row's SO qty
    is that total less this cell's own demand (R1), and the drill-down's own rows say so.
    """
    with blank_session() as db:
        planned, _other, product, warehouse = _pressure_world(db)

        board = _service(db).build([planned.so_number], granularity="week", as_of=TODAY)
        location = _cell(board, product.product_code, "2026-08-31")["locations"][0]
        detail = _service(db).stock_detail(str(product.id), str(warehouse.id))

        assert detail["so_qty"] == location["qty_owed_all_orders"]
        assert sum(float(row["so_qty"]) for row in detail["sales_orders"]) == float(
            location["qty_owed_all_orders"]
        )
        # And what the row prints is the same list with this cell's own 10 taken out.
        assert float(location["so_qty"]) == float(detail["so_qty"]) - float(
            location["qty_demand"]
        )


def test_the_drill_down_names_each_document_the_way_a_person_reads_it():
    with blank_session() as db:
        customer = _customer(db, f"{MARKER} ORIONIS TECHNOLOGY")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        order = _order(
            db, so_number="ZZT-SO-DOC1", customer=customer, order_date=date(2026, 3, 6)
        )
        _line(db, order, product, qty="12", required_date=date(2026, 12, 29),
              warehouse=warehouse)

        detail = _service(db).stock_detail(str(product.id), str(warehouse.id))

        row = next(r for r in detail["sales_orders"] if r["so_number"] == "ZZT-SO-DOC1")
        assert row["customer_name"] == f"{MARKER} ORIONIS TECHNOLOGY"
        assert row["doc_date"] == date(2026, 3, 6)
        assert row["delivery_date"] == date(2026, 12, 29)
        assert row["so_qty"] == "12"
        assert row["project_label"] == f"{MARKER} tower"
        # No identifier a person has to resolve: the sales order is its number, the customer is
        # its name. The ids that travel are addressing only.
        assert row["sales_order_id"] == str(order.id)


def test_the_drill_down_lists_the_documents_by_delivery_date_and_marks_the_asking_line():
    """R5, 27 August 2026: no `#` rank and no queue state in this list - the documents in
    DELIVERY DATE order, with the line the drawer was opened for tagged "this line".

    The rank column was the captain's own earlier ask and it is now answered by the queue
    screen, which is the place that exists to explain a ranking. Here it competed with the
    one question this list is for: what else is claiming this stock, and when.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        # SO383850's shape: wanted in April, so it leads the list.
        earlier = _order(db, so_number="ZZT-SO-EARLY", order_date=date(2026, 3, 1))
        _line(db, earlier, product, qty="1", required_date=date(2026, 4, 1),
              warehouse=warehouse)
        mine = _order(db, so_number="ZZT-SO-ASKING", order_date=date(2026, 1, 1))
        asking = _line(db, mine, product, qty="24", required_date=date(2026, 6, 29),
                       warehouse=warehouse)

        detail = _service(db).stock_detail(
            str(product.id), str(warehouse.id), line_ids=[str(asking.id)]
        )

        rows = detail["sales_orders"]
        assert [row["so_number"] for row in rows] == ["ZZT-SO-EARLY", "ZZT-SO-ASKING"]
        assert [row["is_this_line"] for row in rows] == [False, True]
        # The two columns R5 removed are gone from the payload, not merely hidden by the
        # screen: a figure nobody may show is a figure nobody should compute.
        for row in rows:
            assert "rank_position" not in row
            assert "rank_score" not in row
            assert "rank_factors" not in row
            assert "is_covered" not in row
        # And the total the panel prints still adds up to the position above it.
        assert sum(float(row["so_qty"]) for row in rows) == 25.0


def test_the_drill_down_marks_nothing_when_nobody_asked():
    """Opened from a location row of a cell that names no line - every row is somebody
    else's, and saying "this line" of one of them would be a guess."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="7", required_date=date(2026, 9, 3), warehouse=warehouse)

        detail = _service(db).stock_detail(str(product.id), str(warehouse.id))

        assert [row["is_this_line"] for row in detail["sales_orders"]] == [False]


def test_an_empty_product_location_drills_down_to_an_honest_nothing():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])

        detail = _service(db).stock_detail(str(product.id), str(warehouse.id))

        assert detail["qty_on_hand"] == "0"
        assert detail["so_qty"] == "0"
        assert detail["spo_qty"] == "0"
        assert detail["available_qty"] == "0"
        assert detail["sales_orders"] == []
        assert detail["incoming"] == []


# --------------------------------------------------------------------------- #
# the drill-down over a whole ownership GROUP (captain, 30 August 2026)
# --------------------------------------------------------------------------- #
#
# The board proposed "use own location - 60 from BRW-IB" beside an Available of -24,186 and
# the two could not be reconciled on screen. Step 1 of the ladder draws the GROUP's pile at
# the line's own date - a `BRW-IB` line is fed by `MWH-IB` stock - so the drill a planner
# checks a proposal against is read over the set, not over one bin.


def _qq_group(db):
    """Two bins of one ownership group, a third in another, and a book across all three."""
    product = _product(db, f"ZZT-{_uid()[:6]}")
    first = _warehouse(db, f"ZZA{_uid()[:4]}-QQ")
    second = _warehouse(db, f"ZZB{_uid()[:4]}-QQ")
    outside = _warehouse(db, f"ZZC{_uid()[:4]}-RR")
    _stock(db, product, first, on_hand=100)
    _stock(db, product, second, on_hand=20)
    _stock(db, product, outside, on_hand=500)
    mine = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 5))
    _line(db, mine, product, qty="30", required_date=date(2026, 8, 24), warehouse=first)
    early = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 2))
    _line(db, early, product, qty="30", required_date=date(2026, 8, 1), warehouse=second)
    elsewhere = _order(db, so_number="ZZT-SO-OTHERGROUP", order_date=date(2026, 1, 2))
    _line(db, elsewhere, product, qty="999", required_date=date(2026, 8, 2), warehouse=outside)
    return product, first, second, outside


def test_the_drill_down_reads_a_whole_ownership_group_when_asked_for_one():
    with blank_session() as db:
        product, first, second, _outside = _qq_group(db)
        _incoming(
            db, product, first,
            spo_number="ZZT-SPO-GROUP", allocated=30, received=0, arrives=date(2026, 9, 15),
        )

        detail = _service(db).stock_detail(str(product.id), None, group="QQ")

        # No single bin is the answer, and the set states its own members.
        assert detail["warehouse_id"] is None
        assert detail["location"] is None
        assert detail["group"] == "QQ"
        assert [(row["location"], row["qty_on_hand"]) for row in detail["bins"]] == [
            (first.warehouse_code, "100"), (second.warehouse_code, "20"),
        ]
        # The totals are the SET's: 120 on hand, 60 owed, 30 on the water.
        assert detail["qty_on_hand"] == "120"
        assert detail["so_qty"] == "60"
        assert detail["spo_qty"] == "30"
        assert detail["available_qty"] == "90"
        # Every row says which bin it sits at, and the bin in another group is not in the set.
        assert {row["location"] for row in detail["sales_orders"]} == {
            first.warehouse_code, second.warehouse_code,
        }
        assert "ZZT-SO-OTHERGROUP" not in {row["so_number"] for row in detail["sales_orders"]}
        assert [row["location"] for row in detail["incoming"]] == [first.warehouse_code]


def test_one_bin_still_reads_one_bin():
    """The group reading is opt-in: nothing about the per-bin drill changes."""
    with blank_session() as db:
        product, first, _second, _outside = _qq_group(db)

        detail = _service(db).stock_detail(str(product.id), str(first.id))

        assert detail["group"] is None
        assert detail["location"] == first.warehouse_code
        assert detail["qty_on_hand"] == "100"
        assert detail["so_qty"] == "30"
        assert [row["location"] for row in detail["sales_orders"]] == [first.warehouse_code]


def test_a_hold_taken_from_outside_the_group_is_listed_and_one_from_inside_is_not():
    """R40: cross-group stock moves only as a PINNED hold.

    Such a hold is in no sales-order row of the group whose pile it draws from, so a walk
    without it counts a pile nobody can draw on. A hold whose own line IS in the set is
    already one of those rows, and listing it again would subtract it twice.
    """
    from datetime import datetime

    from app.models.project_so import (
        DECISION_ACTIVE,
        SO_STATUS_ADOPTED,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
        SOLineAllocation,
        SOSupplyDecision,
    )

    def _hold(db, company_id, product, order, line, warehouse, qty):
        record = ProjectSalesOrder(
            id=_uid(), company_id=company_id, project_id=None,
            provisional_ref=order.so_number, autocount_doc_no=order.so_number,
            so_id=order.id, status=SO_STATUS_ADOPTED, grouping_origin="area",
        )
        db.add(record)
        db.flush()
        mirror = ProjectSalesOrderLine(
            id=_uid(), company_id=company_id, project_sales_order_id=record.id, line_no=1,
            product_id=product.id, description=f"{MARKER} mirror", qty=Decimal(qty),
            uom="UNIT", unit_price=Decimal("1.00"), amount=Decimal("1.00"),
            delivery_date=date(2026, 8, 2), core_sales_order_line_id=line.id,
        )
        decision = SOSupplyDecision(
            id=_uid(), company_id=company_id, project_sales_order_id=record.id,
            revision_no=1, state=DECISION_ACTIVE, confirmed_at=datetime.utcnow(),
            line_snapshots=[{"core_line_id": str(line.id), "line_no": 1}],
        )
        db.add_all([mirror, decision])
        db.flush()
        db.add(
            SOLineAllocation(
                id=_uid(), company_id=company_id, so_line_id=mirror.id, source_type="own",
                warehouse_id=warehouse.id, qty=Decimal(qty), decision_id=decision.id,
                confirmed_at=datetime.utcnow(),
            )
        )
        db.flush()

    with blank_session() as db:
        company_id = _sorento(db)
        product, first, second, outside = _qq_group(db)
        # An order booked in the OTHER group, holding 6 of this group's stock.
        theirs = db.query(SalesOrder).filter(SalesOrder.so_number == "ZZT-SO-OTHERGROUP").one()
        their_line = (
            db.query(SalesOrderLine)
            .filter(SalesOrderLine.sales_order_id == theirs.id)
            .one()
        )
        _hold(db, company_id, product, theirs, their_line, first, "6")
        # And one of this group's own lines holding 5 of it, which its own row already states.
        ours = _order(db, so_number="ZZT-SO-INSIDE", order_date=date(2026, 1, 2))
        our_line = _line(
            db, ours, product, qty="5", required_date=date(2026, 8, 3), warehouse=second
        )
        _hold(db, company_id, product, ours, our_line, second, "5")

        detail = _service(db).stock_detail(str(product.id), None, group="QQ")

        assert [
            (row["so_number"], row["location"], row["qty"], row["required_date"])
            for row in detail["holds"]
        ] == [("ZZT-SO-OTHERGROUP", first.warehouse_code, "6", date(2026, 8, 2))]
        # The per-bin reading raises none of this: it is read beside its own Available, which
        # nets no holds either.
        assert _service(db).stock_detail(str(product.id), str(first.id))["holds"] == []


def test_the_group_drill_down_answers_over_the_wire_with_every_field_it_states():
    """`response_model` drops what it does not declare, so the new fields are asserted HERE.

    `overdue_days` is in the list because it was dropped exactly that way: the service has
    stated it since 26 August and the schema never declared it, so every arrival reached the
    screen reading as fresh.
    """
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        product, first, second, _outside = _qq_group(db)
        _incoming(
            db, product, first,
            spo_number="ZZT-SPO-LATE", allocated=30, received=0, arrives=date(2026, 8, 1),
        )
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/stock-detail",
                    params={"product_id": str(product.id), "group": "QQ"},
                )
                neither = client.get(
                    f"{BASE}/fulfilment-planning/stock-detail",
                    params={"product_id": str(product.id)},
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["group"] == "QQ"
        assert body["warehouse_id"] is None
        assert [row["location"] for row in body["bins"]] == [
            first.warehouse_code, second.warehouse_code,
        ]
        assert body["holds"] == []
        assert {row["location"] for row in body["sales_orders"]} == {
            first.warehouse_code, second.warehouse_code,
        }
        assert body["incoming"][0]["location"] == first.warehouse_code
        assert body["incoming"][0]["overdue_days"] > 0
        # Neither a bin nor a set is a question with no answer, and it is refused as one.
        assert neither.status_code == 422, neither.text


def test_the_drill_down_route_answers_over_the_wire():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        planned, _other, product, warehouse = _pressure_world(db)
        # The board order's own (single) line, which is the one the drawer would be asking on.
        asking = str(
            db.query(SalesOrderLine.id)
            .filter(SalesOrderLine.sales_order_id == planned.id)
            .scalar()
        )
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/stock-detail",
                    params={
                        "product_id": str(product.id),
                        "warehouse_id": str(warehouse.id),
                        "line_ids": asking,
                    },
                )
                denied, _denied_originals = _client(db, actor, [])
                refused = denied.get(
                    f"{BASE}/fulfilment-planning/stock-detail",
                    params={
                        "product_id": str(product.id),
                        "warehouse_id": str(warehouse.id),
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["so_qty"] == "75"
        assert body["available_qty"] == "25"
        assert len(body["sales_orders"]) == 3
        # `response_model` drops what it does not declare, so the marking is asserted on the
        # WIRE and not only on the service's own dict.
        marked = [row for row in body["sales_orders"] if row["is_this_line"]]
        assert [row["line_id"] for row in marked] == [asking]
        assert "rank_position" not in body["sales_orders"][0]
        assert refused.status_code == 403, refused.text


def test_the_drill_down_refuses_a_line_ids_value_that_is_not_an_id():
    """`line_ids` goes through the shared `parse_uuid_list`, exactly like every other
    `*_ids` filter: a typed-in value comes back as a stated refusal naming the parameter,
    never as a database error."""
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        _planned, _other, product, warehouse = _pressure_world(db)
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/stock-detail",
                    params={
                        "product_id": str(product.id),
                        "warehouse_id": str(warehouse.id),
                        "line_ids": "not-an-id",
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 400, response.text
        assert "line_ids" in response.text


def test_the_drill_down_route_refuses_a_caller_without_the_view_permission():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        _planned, _other, product, warehouse = _pressure_world(db)
        db.commit()
        client, originals = _client(db, actor, [])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/stock-detail",
                    params={
                        "product_id": str(product.id),
                        "warehouse_id": str(warehouse.id),
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 403


def test_the_supply_sheet_route_refuses_a_caller_without_the_view_permission():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        order, _product = _board_world(db)
        pso_id = _adopt(db, str(order.id))
        db.commit()
        client, originals = _client(db, actor, [])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(f"{BASE}/sales-orders/{pso_id}/supply")
        finally:
            _restore(originals)

        assert response.status_code == 403, response.text


def test_the_confirm_route_refuses_a_caller_with_the_view_permission_alone():
    """Reading the sheet is `projects.projects.view`; confirming it is a WRITE and takes
    `projects.projects.edit`. A reader must not be able to promise stock."""
    from app.models.base import company_scope
    from app.models.project_so import ProjectSalesOrderLine, SOSupplyDecision

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        order, _product = _board_world(db)
        pso_id = _adopt(db, str(order.id))
        db.commit()
        line = (
            db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.project_sales_order_id == pso_id)
            .first()
        )
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.post(
                    f"{BASE}/sales-orders/{pso_id}/confirm",
                    json={
                        "lines": [
                            {"project_line_id": str(line.id), "buy_qty": "10"}
                        ]
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 403, response.text
        assert (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == pso_id)
            .count()
            == 0
        )


# --------------------------------------------------------------------------- #
# why a cell is not ranked, said in the right words
# --------------------------------------------------------------------------- #


def test_a_cell_says_how_many_orders_are_in_it_and_whether_rank_separated_them():
    with blank_session() as db:
        _policy(db, {"need_by_date": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=100)
        alone = _order(db, so_number="ZZT-SO-ONE", order_date=date(2026, 1, 1))
        _line(db, alone, product, qty="5", required_date=date(2026, 9, 1), warehouse=warehouse)
        _line(db, alone, product, qty="5", required_date=date(2026, 9, 2), warehouse=warehouse)
        other = _order(db, so_number="ZZT-SO-TWO", order_date=date(2026, 1, 1))
        _line(db, other, product, qty="5", required_date=date(2026, 12, 1), warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-ONE", "ZZT-SO-TWO"], granularity="week", as_of=TODAY
        )

        # Two lines of ONE order, and the required dates differ so the rank did separate them.
        shared = _cell(board, product.product_code, "2026-08-31")
        assert shared["distinct_order_count"] == 1
        assert shared["rank_separates"] is True
        # One line on its own: nothing to rank, and the screen should say so in those words
        # rather than reporting a policy failure.
        solo = _cell(board, product.product_code, "2026-11-30")
        assert solo["distinct_order_count"] == 1
        assert solo["rank_separates"] is False


def test_a_genuinely_tied_cell_reports_more_than_one_order_and_no_separation():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=100)
        for number in ("ZZT-SO-T1", "ZZT-SO-T2"):
            order = _order(db, so_number=number, order_date=date(2026, 1, 1))
            _line(db, order, product, qty="5", required_date=date(2026, 9, 3),
                  warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-T1", "ZZT-SO-T2"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        assert cell["distinct_order_count"] == 2
        assert cell["rank_separates"] is False


# --------------------------------------------------------------------------- #
# pivoting the board by order, customer or project
# --------------------------------------------------------------------------- #


def test_a_contribution_can_be_pivoted_by_customer_and_project_without_guessing():
    """Two different customers can share a name, so a pivot must group by id, not by label."""
    with blank_session() as db:
        first = _customer(db, "ZZT SAME NAME SDN BHD")
        second = _customer(db, "ZZT SAME NAME SDN BHD")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        a = _order(db, so_number="ZZT-SO-C1", customer=first, order_date=date(2026, 1, 1))
        b = _order(db, so_number="ZZT-SO-C2", customer=second, order_date=date(2026, 1, 1))
        _line(db, a, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, b, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-C1", "ZZT-SO-C2"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        by_order = {c["so_number"]: c for c in cell["contributions"]}
        assert by_order["ZZT-SO-C1"]["customer_name"] == by_order["ZZT-SO-C2"]["customer_name"]
        assert by_order["ZZT-SO-C1"]["customer_id"] == str(first.id)
        assert by_order["ZZT-SO-C2"]["customer_id"] == str(second.id)
        assert by_order["ZZT-SO-C1"]["customer_id"] != by_order["ZZT-SO-C2"]["customer_id"]
        # And the project a pivot would group by, stable for the same project string.
        assert by_order["ZZT-SO-C1"]["project_key"] == by_order["ZZT-SO-C2"]["project_key"]
        assert by_order["ZZT-SO-C1"]["project_key"] is not None


# --------------------------------------------------------------------------- #
# fair share: a Reserve only out of what is left for THIS line
#
# The captain, reading their own strip: "okay if the available is negative then i can't really
# reserve, right? i need to find other way". Their card said Available -8013 at BRW-BB and
# proposed Reserve 80 out of it, because the ladder reserved against `free` (on hand less
# reserved less confirmed holds) while the strip read the whole book.
#
# The rule (option (c) of the quantification, chosen by the captain): a line may reserve from
# its own location only what is left after the demand the ACTIVE POLICY ranks AHEAD of it at
# that pile. Not the whole SO qty - somebody gets the 1015, and it should be the lines ranked
# first.
# --------------------------------------------------------------------------- #


def _oversold_pile(db, *, on_hand: int, ahead_lines: int, ahead_each: str, mine: str):
    """One pile, `ahead_lines` earlier-required lines in front of ours, and ours behind them."""
    product = _product(db, f"ZZT-{_uid()[:6]}")
    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
    _stock(db, product, warehouse, on_hand=on_hand)
    for index in range(ahead_lines):
        earlier = _order(
            db, so_number=f"ZZT-SO-A{index:02d}{_uid()[:4]}", order_date=date(2026, 1, 1)
        )
        _line(db, earlier, product, qty=ahead_each,
              required_date=date(2026, 3, 1) + timedelta(days=index), warehouse=warehouse)
    ours = _order(db, so_number=f"ZZT-SO-MINE{_uid()[:4]}", order_date=date(2026, 1, 1))
    _line(db, ours, product, qty=mine, required_date=date(2026, 12, 29), warehouse=warehouse)
    return ours, product, warehouse


def test_a_line_behind_more_demand_than_the_pile_holds_reserves_nothing():
    """The captain's own case, to scale: 1015 on hand, 12 earlier lines wanting all of it."""
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        ours, product, _warehouse = _oversold_pile(
            db, on_hand=1015, ahead_lines=12, ahead_each="1015", mine="80"
        )

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-12-28")["contributions"][0]
        assert contribution["qty_proposed_reserve"] == "0"
        assert contribution["qty_proposed_buy"] == "80"
        # The arithmetic, in the strip's own words, so the planner can check it against the
        # drill-down: 1015 on hand, all of it owed to lines ranked ahead, nothing left here.
        assert contribution["so_qty_ahead"] == "12180"
        assert contribution["lines_ahead"] == 12
        assert contribution["available_to_this_line"] == "0"


def test_while_the_group_is_short_the_earliest_line_still_gets_the_pile_it_reaches():
    """LADDER V7.1 (R24, AC-S3-1b), rewritten off the v4 test this used to be.

    1015 on hand against 9080 owed. Under v4 the group's own NET decided - `max(-8065 + 80,
    0)` - so a group that could not cover its book promised its stock to nobody and both
    lines bought. Under v7.1 the pile is read AT THE ASKER'S OWN DATE: the line due first
    reaches 1015 that nothing earlier has claimed and reserves its 80, and the 9000 due in
    December reaches the 935 that is left, which is not its whole unit, so it buys entire.
    That is exactly SRTWB242's case, where plain Available is -1,156 and JEREMY still
    reserves his 27.

    The queue arithmetic is still computed and still printed - `so_qty_ahead`,
    `lines_ahead` and `available_to_this_line` say who is in front of whom - it just no
    longer sizes the answer."""
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=1015)
        _lead_time(db, product, 365)
        first = _order(db, so_number="ZZT-SO-FIRST", order_date=date(2026, 1, 1))
        _line(db, first, product, qty="80", required_date=date(2026, 3, 1), warehouse=warehouse)
        later = _order(db, so_number="ZZT-SO-LATER", order_date=date(2026, 1, 1))
        _line(db, later, product, qty="9000", required_date=date(2026, 12, 29),
              warehouse=warehouse)

        board = _service(db).build(
            ["ZZT-SO-FIRST", "ZZT-SO-LATER"], granularity="week", as_of=TODAY
        )

        earliest = _cell(board, product.product_code, "2026-02-23")["contributions"][0]
        assert earliest["so_qty_ahead"] == "0"
        assert earliest["lines_ahead"] == 0
        assert earliest["available_to_this_line"] == "1015"
        assert earliest["qty_proposed_reserve"] == "80"
        assert earliest["qty_proposed_buy"] == "0"
        # And the queue still names what is left of the pile for the line behind it.
        behind = _cell(board, product.product_code, "2026-12-28")["contributions"][0]
        assert behind["so_qty_ahead"] == "80"
        assert behind["available_to_this_line"] == "935"
        assert behind["qty_proposed_reserve"] == "0"
        assert behind["qty_proposed_buy"] == "9000"


def test_demand_a_confirmed_decision_already_holds_is_not_subtracted_twice():
    """The double-count rule, decided and pinned.

    A line ranked ahead that a confirmed decision already covers has its claim on the pile
    expressed ONCE, as a hold that has already been taken out of free stock. Counting its
    outstanding quantity in `so_qty_ahead` as well would subtract the same units twice and
    understate what is left for everybody behind it.

    So `so_qty_ahead` counts only demand that is NOT yet covered by an active decision - the
    same per-line test `scm.committed_v` applies.
    """
    from datetime import datetime

    from app.models.project_so import (
        DECISION_ACTIVE,
        SO_STATUS_ADOPTED,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
        SOLineAllocation,
        SOSupplyDecision,
    )

    with blank_session() as db:
        company_id = _sorento(db)
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=100)
        # Ranked ahead of us, owed 40, and already confirmed: 40 of the 100 is held for it.
        ahead = _order(db, so_number="ZZT-SO-AHEAD", order_date=date(2026, 1, 1))
        ahead_line = _line(db, ahead, product, qty="40", required_date=date(2026, 3, 1),
                           warehouse=warehouse)
        record = ProjectSalesOrder(
            id=_uid(), company_id=company_id, project_id=None,
            provisional_ref=ahead.so_number, autocount_doc_no=ahead.so_number,
            so_id=ahead.id, status=SO_STATUS_ADOPTED, grouping_origin="area",
        )
        db.add(record)
        db.flush()
        mirror = ProjectSalesOrderLine(
            id=_uid(), company_id=company_id, project_sales_order_id=record.id, line_no=1,
            product_id=product.id, description=f"{MARKER} mirror", qty=Decimal("40"),
            uom="UNIT", unit_price=Decimal("1.00"), amount=Decimal("40.00"),
            delivery_date=date(2026, 3, 1), core_sales_order_line_id=ahead_line.id,
        )
        decision = SOSupplyDecision(
            id=_uid(), company_id=company_id, project_sales_order_id=record.id,
            revision_no=1, state=DECISION_ACTIVE, confirmed_at=datetime.utcnow(),
            line_snapshots=[{"core_line_id": str(ahead_line.id), "line_no": 1}],
        )
        db.add_all([mirror, decision])
        db.flush()
        db.add(
            SOLineAllocation(
                id=_uid(), company_id=company_id, so_line_id=mirror.id, source_type="own",
                warehouse_id=warehouse.id, qty=Decimal("40"), decision_id=decision.id,
                confirmed_at=datetime.utcnow(),
            )
        )
        ours = _order(db, so_number="ZZT-SO-OURS", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="70", required_date=date(2026, 12, 29),
              warehouse=warehouse)
        db.flush()

        board = _service(db).build(["ZZT-SO-OURS"], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-12-28")["contributions"][0]
        # 100 on hand, 40 held by the confirmed decision -> 60 free. The confirmed line is NOT
        # counted again in what is ranked ahead, so 60 is what is left for us - read-only, since
        # ladder v2 (section E rule 7) never reserves at the own location: with no pool here the
        # whole 70 is bought instead.
        assert contribution["so_qty_ahead"] == "0"
        assert contribution["available_to_this_line"] == "60"
        assert contribution["qty_proposed_reserve"] == "0"
        assert contribution["qty_proposed_buy"] == "70"


def test_the_pool_is_netted_against_its_own_book_too():
    """The shared pool is a pile like any other: its own outstanding demand claims it first.

    Ladder v3's whole-line rule (section 1b rung 5): our own ask is sized to exactly what the
    pool's own book leaves (5), so it is still proposed as a pure Reserve - a bigger ask here
    would collapse to a single Buy instead of a partial "reserve 5, buy the rest" mix.
    """
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=0)
        # 55, so the pool's own book leaves 10 and ladder v8's half of that is the 5 this
        # line asks for (R-B). The case is about the pool's own book claiming it first.
        _stock(db, product, pool, on_hand=55)
        _lead_time(db, product, 365)
        # The pool's own book wants 45 of its 55, and wants it sooner than we do.
        pool_demand = _order(db, so_number="ZZT-SO-POOLBOOK", order_date=date(2026, 1, 1))
        _line(db, pool_demand, product, qty="45", required_date=date(2026, 3, 1),
              warehouse=pool)
        ours = _order(db, so_number="ZZT-SO-OURS", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="5", required_date=date(2026, 12, 29), warehouse=own)

        board = _service(db).build(["ZZT-SO-OURS"], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-12-28")["contributions"][0]
        assert contribution["qty_proposed_reserve"] == "5", (
            "only the project's half of the pool's leftover 10"
        )
        assert contribution["qty_proposed_buy"] == "0"


def test_the_confirmation_accepts_what_the_ladder_proposes_over_an_oversold_pile():
    """The contract, over the shape that broke it: a pile the book has already sold twice."""
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=1015)
        for index in range(3):
            earlier = _order(
                db, so_number=f"ZZT-SO-E{index}{_uid()[:4]}", order_date=date(2026, 1, 1)
            )
            _line(db, earlier, product, qty="3000",
                  required_date=date(2026, 3, 1) + timedelta(days=index), warehouse=warehouse)
        ours = _order(db, so_number=f"ZZT-SO-M{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="80", required_date=date(2026, 12, 29),
              warehouse=warehouse)
        _adopt(db, str(ours.id))
        db.commit()

        client, originals = _client(db, actor, [VIEW, EDIT])
        try:
            with company_scope(db, frozenset({company_id})):
                board = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": ours.so_number, "granularity": "week"},
                ).json()
                pso_id = board["orders"][0]["project_sales_order_id"]
                lines = [
                    {
                        "project_line_id": contribution["project_line_id"],
                        "timely_spo_qty": contribution["qty_proposed_incoming"],
                        "reserve": [
                            {"warehouse_id": source["warehouse_id"], "qty": source["qty"]}
                            for source in contribution["sources"]
                            if source["kind"] == "reserve"
                        ],
                        "buy_qty": contribution["qty_proposed_buy"],
                    }
                    for cell in board["cells"]
                    for contribution in cell["contributions"]
                ]
                response = client.post(
                    f"{BASE}/sales-orders/{pso_id}/confirm", json={"lines": lines}
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        assert lines[0]["reserve"] == [], "nothing was left at that pile for this line"
        assert lines[0]["buy_qty"] == "80"


# --------------------------------------------------------------------------- #
# the decision trail: the ladder, walked in the open
#
# The captain, on a Buy: "can you justify how you arrive at the buy, like what's the process
# you have gone through: checking the available quantity first, deciding whether to reserve it
# or not, then checking the SPO quantity, then checking whether can borrow ... need more
# justification", and then: "the justification needs to be STRUCTURED instead of plain text".
#
# So every plannable line carries the whole ladder as STEPS, including the ones that gave
# nothing: "the pool was checked and had none" is the answer to the question, and a step that
# is silently omitted reads as a step that was never taken.
# --------------------------------------------------------------------------- #


def _trail(contribution) -> list:
    return contribution["trail"]


def _step(contribution, kind: str) -> dict:
    return next(step for step in contribution["trail"] if step["kind"] == kind)


def test_the_trail_asks_the_four_questions_in_order_and_then_buy():
    """AC-V1 / AC-V7 (ladder v5, section 1e): FIVE rows, one per question plus Buy, in the
    captain's own order - our own location, the pool, another location, the same agent's
    other order, Buy.

    Two rows fewer than v4. `reserve_own` was a read-only strip whose whole job was naming
    the queue, which is one of question 1's facts rather than a question of its own; and
    `incoming` is not a question at all any more, because an SPO is inside the group's net.
    """
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        # LADDER V8 (R-A) and review round 1 (S5): the proof asks its questions in the
        # WALK's own order, and the walk asks the site pool first. `OPTION_STEPS` is that
        # order; a proof in a different one made the reader reconcile two screens.
        assert [step["kind"] for step in _trail(contribution)] == [
            "pool",
            "own",
            "order_borrow",
            "supply_borrow",
            "buy",
        ]
        assert [step["step"] for step in _trail(contribution)] == [1, 2, 3, 4, 5]
        assert [step["question"] for step in _trail(contribution)] == [
            "Can we take from the pool?",
            "Can we use our locations?",
            "Can we borrow on hand from a later order?",
            "Can we borrow incoming from a later order?",
            "Buy",
        ]


def test_every_row_of_the_proof_answers_yes_or_no_and_says_what_it_took():
    """AC-V1's shape: one word, one quantity, one place, one sentence. `_quantity_world` has
    7 on the water against the 28 the line plans for (AC-S2-4, not the 20 still owed) and no
    pool, so every question but Buy answers No."""
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        for step in _trail(contribution):
            assert step["answer"] in ("yes", "no"), step["kind"]
            assert step["why"], step["kind"]
            if step["answer"] == "no":
                assert step["took"] == "0", step["kind"]
                assert step["from"] is None, step["kind"]

        assert [step["kind"] for step in _trail(contribution) if step["answer"] == "yes"] == [
            "buy"
        ]
        buy = _step(contribution, "buy")
        assert buy["location"] is None
        assert buy["took"] == "28"
        assert not any(step["kind"] == "incoming" for step in _trail(contribution)), (
            "there is no row named Incoming under ladder v5"
        )


def test_the_trail_adds_up_to_the_proposal_it_explains():
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        reserved = sum(
            Decimal(step["took"])
            for step in _trail(contribution)
            if step["kind"] in ("own", "pool")
        )
        assert reserved == Decimal(contribution["qty_proposed_reserve"])
        assert Decimal(_step(contribution, "buy")["took"]) == Decimal(
            contribution["qty_proposed_buy"]
        )


# `test_the_trail_says_the_queue_ahead_emptied_the_pile_and_the_residual_is_bought` DELETED
# (ladder v2, section E rule 7): its whole point - a `reserve_own` step naming the pile's
# opening balance and the queue that emptied it before this line was reached - lived only on
# the own-location rung, which is now gone. There is no rung left that ever draws on the own
# location, so no trail step can carry an "opening"/"ahead" narrative about it any more; the
# same read-only queue facts survive on the CONTRIBUTION (`so_qty_ahead`, `available_to_this_
# line`), covered by `test_the_line_ranked_first_at_a_pile_still_gets_its_whole_reserve` and
# `test_demand_a_confirmed_decision_already_holds_is_not_subtracted_twice`.


def test_a_line_covered_before_the_buy_step_says_the_buy_was_not_needed():
    """Ladder v3: the group holds nothing here, so full cover comes from the POOL, and every
    rung after it - group/cross-group borrow, buy - is walked and reported unnecessary, never
    omitted."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        assert _step(contribution, "pool")["answer"] == "yes"
        # Every step is walked and reported, before the cover and after it: an omitted row
        # reads as a question nobody asked (AC-S3-14).
        for kind in ("own", "order_borrow", "supply_borrow", "buy"):
            step = _step(contribution, kind)
            assert step["took"] == "0"
            assert step["answer"] == "no", kind


# `test_the_trail_says_a_hot_selling_line_reserves_its_own_location_and_no_pool` DELETED
# (ladder v2, section E rule 7): its own-location half asserted the removed `reserve_own`
# step; its pool half is now covered on its own by
# `test_the_pool_rung_names_the_classification_that_keeps_the_pool_for_retail` below.


def test_the_pool_step_is_walked_even_where_there_is_no_pool():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=4)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        step = _step(contribution, "pool")
        assert step["answer"] == "no"
        assert step["location"] is None
        assert step["note"] == "no shared pool"


def test_a_location_with_no_pool_of_its_own_still_draws_another_active_site_pool():
    """S6 of the review findings: `_pool_chain` (the same chain `compose_line` composes
    from) draws every OTHER active site pool regardless of whether THIS line's own
    location has one of its own (section E rule 2 - "own site pool first, THEN the
    others"; a location with none of its own simply starts the chain at the others,
    section 8's cross-site pooling). The old short-circuit on `not pool_code` claimed "no
    shared pool" even though a real one existed - and drew from it - elsewhere."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        _own2, pool2 = _pooled_warehouses(db)
        lonely = _warehouse(db, f"ZZTL{_uid()[:6]}"[:20])
        _stock(db, product, lonely, on_hand=0)
        _stock(db, product, pool2, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=lonely)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        step = _step(contribution, "pool")
        assert step["answer"] == "yes"
        assert step["took"] == "10"
        assert pool2.warehouse_code in (step["note"] or "")
        # AC-N.10: the note is a sentence of its own - capitalized and full-stopped -
        # rather than a lowercase fragment that reads as the `why` sentence above it
        # running on into "checked ...".
        assert step["note"] == f"Checked {pool2.warehouse_code}."
        kinds = [(s["kind"], s["qty"], s["location"]) for s in contribution["sources"]]
        assert kinds == [("reserve", "10", pool2.warehouse_code)]


def test_the_proof_offers_what_ANOTHER_site_pool_actually_gave():
    """Review round 2 (S5): the proof's pool question knew only the FIRST pool's allowance.

    It read `available_for_project` off `pool_chain[0]` and then spread that one number
    across every location in the chain, so on the R-L path - where the first pool is
    oversold and a LATER one answers - it printed "no other active site pool has anything
    to offer" beside a Reserve of 300 it had just made at that very pool. Each pool's own
    allowance is now walked the way step 0 itself walks it, bounded by the one net.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        _own1, oversold = _pooled_warehouses(db)
        _own2, sparing = _pooled_warehouses(db)
        lonely = _warehouse(db, f"ZZTL{_uid()[:6]}"[:20])
        _stock(db, product, lonely, on_hand=0)
        # The FULLEST pool leads the chain (by on hand) and its own book owes every unit of
        # it, so its allowance is 0 - while the second pool has 800 and may spare 400.
        _stock(db, product, oversold, on_hand=1000)
        _stock(db, product, sparing, on_hand=800)
        owed = _order(db, so_number=f"ZZT-SO-OWED{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, owed, product, qty="1000", required_date=date(2026, 3, 1), warehouse=oversold)

        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="300", required_date=date(2026, 9, 3), warehouse=lonely)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        step = _step(contribution, "pool")
        sparing_code = sparing.warehouse_code

    assert step["answer"] == "yes"
    assert step["took"] == "300"
    assert sparing_code in step["why"], step["why"]
    assert "has anything to offer" not in step["why"], (
        "the proof offered 0 beside a draw of 300 it had just made at that pool",
        step["why"],
    )


def test_a_cell_whose_pool_step_answered_from_two_pools_shows_both_taken_figures():
    """S2 of `PLAN-scm-pool-chain-first.md` (R-N): the proof agrees with a MULTI-POOL step 0.

    Step 0 walks the whole chain now, so the ordinary answer is two pools at once. Three
    things have to say the same thing about it: the pool question's `offered`, which reads
    `pool_share_capacity` over the chain; its `took`; and the Taken column on the cell's own
    site pool rows, which is where a planner checks WHERE the units come from. A cell that
    printed one pool's take against a two-pool composition would be the round-2 S5 defect
    again, one rung further up.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, own_pool = _pooled_warehouses(db)
        _far_own, far_pool = _pooled_warehouses(db)
        # The asking bin's own pool may spare 4 of its 8 (half is kept for dealers) and the
        # second pool holds plenty, so a line of 8 is 4 + 4 across the two of them.
        _stock(db, product, own, on_hand=0)
        _stock(db, product, own_pool, on_hand=8)
        _stock(db, product, far_pool, on_hand=687)

        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="8", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, product.product_code, "2026-08-31")
        contribution = cell["contributions"][0]
        step = _step(contribution, "pool")
        sources = [(s["kind"], s["qty"], s["location"]) for s in contribution["sources"]]
        pool_rows = [row for row in cell["locations"] if row["where"] == "site_pool"]
        listed = {row["location"]: row["available_for_project"] for row in pool_rows}
        own_code, far_code = own_pool.warehouse_code, far_pool.warehouse_code

    assert sources == [
        ("reserve", "4", own_code),
        ("reserve", "4", far_code),
    ], sources
    assert step["answer"] == "yes"
    # `offered` is internal since the trail was cut back to a sentence and an answer; what
    # a reader can check is that the question TOOK the whole 8 and that its sentence names
    # both pools it took them from. A chain capped by the first pool's allowance would have
    # answered 4 here, which is the round-2 S5 defect one rung further up.
    assert step["took"] == "8"
    # BOTH pools are rows of the cell's own table, each stating the allowance the walk
    # obeyed - which is what the Stock tab's Taken column is checked against (the column
    # itself is summed on the client off these very components, `takenByLocation`).
    assert listed[own_code] == "4", listed
    assert Decimal(listed[far_code]) >= Decimal("4"), listed
    assert own_code in step["why"] and far_code in step["why"], step["why"]


def test_a_dealer_hot_selling_line_still_reads_the_pools_own_later_order_in_the_proof():
    """Review round 2 (S4): the proof gated `pool_borrow` on dealer hot-selling while
    `ProjectSupplyService.walk` built it for every item (LADDER V8, R-A retired the gate).

    A hot item with an empty pool FLOOR and a later pool order holding stock is exactly the
    shape the divergence hid: the walk composes the borrow, and the proof's own reading of
    the step has to be made from the same donor list rather than from an empty one.
    """
    with blank_session() as db:
        borrow_from_order_on(db)
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="A")
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=60)
        # The pool's OWN book owes all 60 to a LATER order, so its free share is nothing and
        # the only thing left to answer with is that order's on hand (R34).
        later = _order(db, so_number=f"ZZT-SO-LATE{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, later, product, qty="60", required_date=date(2027, 1, 15), warehouse=pool)

        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="60", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        step = _step(contribution, "pool")
        kinds = [(s["kind"], s["qty"], s["location"]) for s in contribution["sources"]]
        pool_code = pool.warehouse_code

    assert kinds == [("borrow", "60", pool_code)], kinds
    assert step["answer"] == "yes"
    assert step["took"] == "60"
    assert later.so_number in step["why"], step["why"]


def test_the_borrow_step_offers_what_it_found_and_takes_none_of_it():
    """Ladder v2 splits Borrow into two rungs (group / cross-group), and with no policy row
    (the default caps closed) neither auto-composes here - but the donor is still OFFERED, on
    the contribution's own donor list, exactly as the old single Borrow step used to say."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTO{_uid()[:6]}"[:20])
        elsewhere = _warehouse(db, f"ZZTE{_uid()[:6]}"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, elsewhere, on_hand=25)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        step = _step(contribution, "own")
        assert step["took"] == "0", "an ungrouped bin is outside step 1 (v7.1)"
        assert step["answer"] == "no"
        assert _step(contribution, "buy")["took"] == "10"
        # The donor is still OFFERED, on the contribution's own list.
        assert contribution["qty_borrow_available"] == "25"
        assert [c["warehouse_code"] for c in contribution["borrow_candidates"]] == [
            elsewhere.warehouse_code
        ]


def test_the_cross_group_rung_is_asked_against_the_true_residual_not_the_whole_line():
    """Once the whole-line rule fires (section E rule 6), `components` drops every partial
    rung and carries ONLY a Buy of the whole line - so the trail's own bookkeeping
    (`remaining`) never moved off the full quantity by the time cross-group borrow is
    reached. The engine's own residual (`_ladder_residual_before_cross_group`) accounts for
    what the earlier rungs WOULD have covered, whether or not the whole-line rule keeps that
    partial composition, and that is the number the rung is asked about.

    30 arrives in time, leaving a TRUE residual of 70; the donor holds only 5, never enough
    to cover the line in full, so the whole-line rule still buys 100 whole and question 3
    says so with the donor named. (v7.1, R5: there is no cross-group CAP left for the
    residual to be measured against - what the residual still decides is whether the rung is
    asked at all.)"""
    with blank_session() as db:
        from app.services.scm import priority

        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTOCG{_uid()[:4]}-BB"[:20])
        # ANOTHER PROJECT GROUP, not an ungrouped bin: step 1's second half is about groups,
        # and a bin with no group at all is outside the step in both halves (the test above
        # pins that). `-IR` is what makes this donor step 1 material at all.
        elsewhere = _warehouse(db, f"ZZTECG{_uid()[:4]}-IR"[:20])
        _stock(db, product, own, on_hand=0)
        _incoming(
            db, product, own,
            spo_number="ZZT-SPO-CG", allocated=30, received=0, arrives=date(2026, 8, 20),
        )
        _stock(db, product, elsewhere, on_hand=5)
        priority.create_revision(
            db, name=f"zzt-cg-{_uid()[:6]}", factors={}, demand_class_weights={},
            reorder_coverage_until=None,
        )
        db.commit()
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="100", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        # The whole-line rule fired: nothing partial survived, the whole 100 is bought.
        assert contribution["qty_proposed_buy"] == "100"
        step = _step(contribution, "own")
        why = step["why"] or ""
        # The donor is SEEN and named - another project group's free pile is step 1's second
        # half since v7.1 (R5) - and the sentence is the whole-unit one rather than a cap
        # refusal: there is no cap left to refuse anything.
        assert elsewhere.warehouse_code in why
        assert "cross-group borrow limit" not in why, why
        assert "is still needed" not in why, why


# --------------------------------------------------------------------------- #
# WHY each rung ended the way it did
#
# The captain, reading a trail whose first rung said "478 | 18730 across 142 lines | 0 | 0 | 21 |
# Nothing left": "what does this mean? why do the orders stand ahead of me? why? and why is the
# donor offered but I did not take, why?"
#
# Numbers alone answered none of those. So every rung carries ONE plain sentence, and the rung
# with a queue in front of it also names who is in that queue and what put them there.
# --------------------------------------------------------------------------- #


def _queued_pile(db):
    """One pile, six lines ahead of ours: five other orders, then our own earlier line.

    The five differ from us by REQUIRED DATE alone (same order date, no customer, same demand
    class), so the factor that puts them ahead is unambiguous. The sixth shares our order AND
    our required date, so nothing separates it on score at all and only line order does.
    """
    _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
    product = _product(db, f"ZZT-{_uid()[:6]}")
    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
    _stock(db, product, warehouse, on_hand=100)
    for index in range(5):
        earlier = _order(
            db, so_number=f"ZZT-SO-A{index:02d}{_uid()[:4]}", order_date=date(2026, 1, 1)
        )
        _line(
            db,
            earlier,
            product,
            qty="30",
            required_date=date(2026, 3, 1) + timedelta(days=index),
            warehouse=warehouse,
        )
    ours = _order(db, so_number=f"ZZT-SO-MINE{_uid()[:4]}", order_date=date(2026, 1, 1))
    # Both of ours are the same size: nothing has been mirrored onto a planning record, so the
    # two carry no line number and the queue separates them by line id. Which of them ends up
    # behind the other is therefore arbitrary, and the totals must not depend on it.
    _line(db, ours, product, qty="20", required_date=date(2026, 12, 29), warehouse=warehouse)
    _line(db, ours, product, qty="20", required_date=date(2026, 12, 29), warehouse=warehouse)
    return ours, product, warehouse


def _behind(board, item_code: str) -> dict:
    """Whichever of our two same-dated lines the queue put LAST: the one with six ahead of it."""
    cell = _cell(board, item_code, "2026-12-28")
    return max(cell["contributions"], key=lambda c: c["lines_ahead"])


def test_every_rung_says_why_it_ended_the_way_it_did():
    with blank_session() as db:
        ours, product, _warehouse = _queued_pile(db)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        contribution = _behind(board, product.product_code)

        for step in _trail(contribution):
            assert (step.get("why") or "").strip(), step["kind"]
            # A sentence, not a restatement of the identifiers beside it.
            assert "reserve_own" not in (step["why"] or "")


# Restored in v2 terms (review finding S4, 20 August 2026): the rich "who is ahead of me
# and why" breakdown - the named top-3 queue, `ahead_more`, `ahead_by_factor`, its own
# `why` sentence - lives on the `reserve_own` trail step, re-added read-only (`_trail`'s
# rung 0): "the own-location strip stays read-only but its queue explanation stays." It is
# the ONE rung `BoardTrailStep.ahead` et al are populated for - "the pool nets its own book
# before it is offered, and incoming and Buy have no queue" per the schema's own docstring
# - and the ONE rung `QueueLink`'s dialog can open, because that dialog always names
# `fulfilment_warehouse_id`, which is this rung's own location and nowhere else's.


def test_the_own_rung_names_the_queue_ahead_of_this_line():
    with blank_session() as db:
        ours, product, _warehouse = _queued_pile(db)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        contribution = _behind(board, product.product_code)

        own = _step(contribution, "own")
        assert own["ahead_lines"] == 6
        assert own["ahead_qty"] == "170"
        assert len(own["ahead"]) == 3, "named beats counted - the top three, not the whole six"
        assert own["ahead_more"] == 3
        for line in own["ahead"]:
            assert line["so_number"]
            assert line["leading_factor"]


def test_the_own_rung_counts_the_whole_queue_by_what_put_each_line_there():
    with blank_session() as db:
        ours, product, _warehouse = _queued_pile(db)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        contribution = _behind(board, product.product_code)

        own = _step(contribution, "own")
        # Every one of the six ahead is counted by SOME factor - "139 by required date, 2 by
        # document age, and one is an earlier line of your own order" in the captain's words.
        assert sum(own["ahead_by_factor"].values()) == 6


def test_the_own_rung_why_names_the_queue_in_words_a_planner_uses():
    with blank_session() as db:
        ours, product, _warehouse = _queued_pile(db)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        contribution = _behind(board, product.product_code)

        own = _step(contribution, "own")
        assert own["answer"] == "no"
        # LADDER V5: the queue is CARRIED on question 1 (the read-only strip that used to
        # name it is folded in), but it is not what the sentence is about - under ladder v4
        # the queue decides no availability, so the sentence names the rule that refused
        # this line and the queue stays beside it as fields the popover can open.
        assert own["ahead_lines"] and own["ahead_lines"] > 0
        assert own["ahead_qty"] and Decimal(own["ahead_qty"]) > 0
        assert own["ahead"], "the queue is named, not merely counted"


def test_a_line_first_in_the_queue_says_so_rather_than_naming_nobody():
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        own = _step(contribution, "own")
        assert own["ahead_lines"] == 0
        assert own["ahead"] == []
        assert own["ahead_more"] == 0
        assert own["ahead_lines"] == 0
        assert own["ahead"] == []

# `test_the_borrow_rung_says_borrowing_is_a_persons_decision_and_names_the_donors` DELETED:
# identical setup to `test_the_borrow_step_offers_what_it_found_and_takes_none_of_it` above,
# whose own rewrite already covers where this information now lives (the cross-group-borrow
# rung's `why` is generic when nothing qualifies for auto-composition; the donor is offered on
# the contribution's own `borrow_candidates` / `qty_borrow_available`, not named in a trail
# step's `note` any more - there is no single "borrow" rung left to carry it).


def test_a_hot_selling_rung_still_names_the_classification_in_the_pool_sentence():
    """Ladder v2 (section E rule 7): own-location Reserve is gone entirely, so only the POOL
    rung's sentence survives. LADDER V8 (R-A) retires the gate the sentence used to explain,
    and the classification stays in front of it: "hot or cold" is what the captain asked to
    be told, whether or not it changes the arithmetic."""
    from app.models.scm import ItemClassification

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        db.flush()
        _stock(db, product, own, on_hand=20)
        _stock(db, product, pool, on_hand=0)
        db.add(
            ItemClassification(
                id=_uid(), product_id=product.id, warehouse_id=own.id, abc_class_retail="A"
            )
        )
        db.flush()
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        # LADDER V8 (R-A): the gate is retired, so the sentence is the ordinary one - and
        # it still NAMES the classification, because that is what the captain reads. The
        # pool holds nothing here, which is now the only reason it offers nothing.
        assert _step(contribution, "pool")["why"] == (
            f"Dealer hot-selling at {own.warehouse_code}, so {pool.warehouse_code} is "
            f"offered, but there is no stock at {pool.warehouse_code}."
        )
        assert _step(contribution, "buy")["took"] == "10"


def test_the_supply_on_the_water_is_inside_question_ones_own_offer():
    """AC-V2 on the board. `_quantity_world` has 7 open on ZZT-SPO-0001 against the 28 the
    line plans for (AC-S2-4, not the 20 still owed).

    The group HAS stock to give - it nets 17 and leaves 12 for this line, 5 free at the bin
    and the 7 on the water - and 12 cannot cover 28, so the whole-line rule takes none of it
    and buys the lot. Said this way because the sentence matters: while `owed_qty` was never
    wired through to the board the offer read 0 and this test passed for the wrong reason,
    "the group cannot cover it" (review round 2, N1). There is no pool, which is why the
    only stock in play is the group's own.

    What is pinned is that there is NO row named Incoming and no rung of its own for the
    document: the water is inside question 1's offer, and question 1's sentence names the
    group's net. Which SPO it is stands on the cell's location table and on the order-inquiry
    row, where Link SPO ties one document to one line.
    """
    with blank_session() as db:
        order, product, _warehouse = _quantity_world(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        assert [step["kind"] for step in contribution["trail"]] == [
            "pool", "own", "order_borrow", "supply_borrow", "buy",
        ]
        assert "group nets" in _step(contribution, "own")["why"]
        assert _step(contribution, "buy")["took"] == "28"


# --------------------------------------------------------------------------- #
# the WHOLE queue at a pile
#
# The captain, after the top three: "I need to know what is ahead of me to have the visibility,
# and why they are ahead of me, meaning I need to know their rank also."
#
# So the same queue the trail counted is readable in full, with each line's rank and the factors
# behind it. The SAME queue, from `_pile_book`: a second ranking of one pile would eventually
# disagree with the one the proposal was computed from, and then the screen would be arguing
# with the plan.
# --------------------------------------------------------------------------- #


def test_the_pile_queue_is_exactly_the_queue_the_trail_counted():
    with blank_session() as db:
        ours, product, warehouse = _queued_pile(db)
        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        contribution = _behind(board, product.product_code)

        queue = _service(db).pile_queue(
            str(product.id), str(warehouse.id), contribution["line_id"]
        )

        assert queue["item_code"] == product.product_code
        assert queue["location"] == warehouse.warehouse_code
        assert queue["qty_free_opening"] == "100"
        assert [line["position"] for line in queue["lines"]] == [1, 2, 3, 4, 5, 6, 7]
        assert queue["this_line_position"] == 7
        mine = next(line for line in queue["lines"] if line["is_this_line"])
        assert mine["line_id"] == contribution["line_id"]
        ahead = [line for line in queue["lines"] if line["position"] < mine["position"]]
        # The two numbers the trail printed, recomputed from the rows it printed them about.
        assert len(ahead) == contribution["lines_ahead"]
        assert sum(Decimal(line["qty"]) for line in ahead) == Decimal(
            contribution["so_qty_ahead"]
        )
        assert mine["cumulative_ahead_qty"] == "190", "the running sum includes this row"


def test_the_pile_queue_is_ordered_exactly_as_the_pile_book_orders_it():
    from app.services.project_supply_service import ProjectSupplyService

    with blank_session() as db:
        ours, product, warehouse = _queued_pile(db)
        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        contribution = _behind(board, product.product_code)

        queue = _service(db).pile_queue(
            str(product.id), str(warehouse.id), contribution["line_id"]
        )
        book = ProjectSupplyService(db).pile_book(str(product.id), str(warehouse.id))

        assert [line["line_id"] for line in queue["lines"]] == [
            row["line_id"] for row in book
        ]


def test_a_pool_draw_is_queued_by_the_same_rule_the_pile_book_orders_the_pool_by():
    """`pool_claims` puts the asking line INTO the pool's own queue and ranks the lot with
    `_pile_book`'s one rule. When the policy separates nobody (every score 0.0), the queue is
    by required date and then sales-order number - so a line due on the 3rd stands behind
    the pool's own line due on the 1st and AHEAD of its line due on the 5th, whatever the
    sales-order numbers say. Ranked by sales-order number alone the asker read both pool
    lines as ahead of it, and the trail's "claimed ahead" disagreed with the queue dialog.
    """
    from app.services.project_supply_service import ProjectSupplyService

    with blank_session() as db:
        # A factor no sales-order line carries: every demand row scores 0.0, so the queue is
        # decided by the tie-break alone.
        _policy(db, {"po_document_sequence": 1.0})
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, pool, on_hand=100)
        later = _order(db, so_number="ZZT-SO-AAA", order_date=date(2026, 1, 1))
        sooner = _order(db, so_number="ZZT-SO-BBB", order_date=date(2026, 1, 1))
        _line(db, later, product, qty="10", required_date=date(2026, 9, 5), warehouse=pool)
        _line(db, sooner, product, qty="20", required_date=date(2026, 9, 1), warehouse=pool)

        supply = ProjectSupplyService(db)
        book = supply.pile_book(str(product.id), str(pool.id))
        assert [row["so_number"] for row in book] == ["ZZT-SO-BBB", "ZZT-SO-AAA"], (
            "the pool's own queue is by required date, not by sales-order number"
        )

        claims = supply.pool_claims(
            [str(product.id)],
            [str(pool.id)],
            [
                {
                    "key": "asker",
                    "product_id": str(product.id),
                    "pool_id": str(pool.id),
                    "required_date": date(2026, 9, 3),
                    "order_date": date(2026, 1, 1),
                    "payment_terms_days": None,
                    "demand_class": "project",
                    "so_number": "ZZT-SO-ZZZ",
                    "line_no": 1,
                }
            ],
        )

        assert claims["asker"] == {"qty": Decimal("20"), "lines": 1}, (
            "only the pool line due before the 3rd is ahead of the asker"
        )


def test_every_queued_line_carries_its_rank_and_the_factors_behind_it():
    with blank_session() as db:
        ours, product, warehouse = _queued_pile(db)
        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        contribution = _behind(board, product.product_code)

        queue = _service(db).pile_queue(
            str(product.id), str(warehouse.id), contribution["line_id"]
        )

        assert queue["policy_name"], "the rule that produced this order, named"
        first = queue["lines"][0]
        assert first["so_number"]
        assert first["required_date"] == date(2026, 3, 1)
        assert first["order_date"] == date(2026, 1, 1)
        assert first["is_covered_excluded"] is False
        keys = {factor["key"] for factor in first["rank_factors"]}
        assert "need_by_date" in keys and "document_age" in keys
        # The FACT behind the factor, not only its normalised value.
        need_by = next(f for f in first["rank_factors"] if f["key"] == "need_by_date")
        assert need_by["raw"] == "2026-03-01"
        # Why THAT line is in front of the one that asked. The asked line outranks nobody.
        assert first["leading_factor"] == "need_by_date"
        mine = next(line for line in queue["lines"] if line["is_this_line"])
        assert mine["leading_factor"] is None


def test_the_pile_queue_answers_over_the_wire_and_refuses_a_caller_without_the_view():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        ours, product, warehouse = _queued_pile(db)
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/queue",
                    params={
                        "product_id": str(product.id),
                        "warehouse_id": str(warehouse.id),
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["lines"]) == 7
        assert body["this_line_position"] is None, "nobody asked about a line"

        denied, originals = _client(db, actor, [])
        try:
            with company_scope(db, frozenset({company_id})):
                refused = denied.get(
                    f"{BASE}/fulfilment-planning/queue",
                    params={
                        "product_id": str(product.id),
                        "warehouse_id": str(warehouse.id),
                    },
                )
        finally:
            _restore(originals)

        assert refused.status_code == 403


def test_a_contribution_names_its_core_line_so_its_queue_can_be_asked_for():
    with blank_session() as db:
        ours, product, _warehouse = _queued_pile(db)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)

        for contribution in _cell(board, product.product_code, "2026-12-28")["contributions"]:
            assert contribution["line_id"]


def test_a_covered_line_carries_no_ahead_list_because_it_carries_no_trail():
    with blank_session() as db:
        ours, product, _warehouse = _queued_pile(db)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)

        for contribution in _cell(board, product.product_code, "2026-12-28")["contributions"]:
            for step in contribution["trail"]:
                # Question 1 is the one that carries the queue (the old read-only strip is
                # folded into it under ladder v5); no other question has one.
                if step["kind"] != "own":
                    assert step["ahead"] == [], step["kind"]
                    assert step["ahead_by_factor"] == {}, step["kind"]


def test_an_unplannable_line_carries_no_trail_at_all():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=None)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        assert contribution["unplannable"] is True
        assert contribution["trail"] == []


def test_a_contribution_names_its_fulfilment_location_by_id_as_well_as_by_code():
    """The confirm payload addresses a warehouse by id, and an amendment on a line the engine
    proposed nothing for has no Reserve source to read one off."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, "2026-08-31")

        assert cell["contributions"][0]["fulfilment_warehouse_id"] == str(warehouse.id)


def test_an_unplannable_line_has_no_fulfilment_warehouse_id():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=None)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, "2026-08-31")

        assert cell["contributions"][0]["fulfilment_warehouse_id"] is None


# --------------------------------------------------------------------------- #
# a line an ACTIVE decision already covers (13.4)
#
# The defect, live on SO403765 line 1: a planner confirmed "borrow 10 from MWH-IB, buy 33"
# and the board went on showing the line with a FRESH proposal of Buy 43, a share note
# reading "First in the queue at BRW-BB - 0 left for this line", and a trail whose first rung
# offered nothing. Two rules had come apart:
#
#   * `_demand_rows` keeps every open line, including one an active decision covers;
#   * `_pile_book` / `_decided_elsewhere` EXCLUDE a covered line from the queue, which is the
#     double-count rule and is right - its claim is already expressed once, as a hold.
#
# So the covered line asked the projection for a share it is deliberately not in, got nothing
# back, and every share field defaulted to zero - indistinguishable from "genuinely nothing
# ahead of you". The ladder then re-proposed a line that had already been decided.
#
# The fix is not to put it back in the queue. A covered line is not competing for anything:
# it states what was FROZEN for it, and no ladder is walked for it at all.
# --------------------------------------------------------------------------- #


def _confirm(db, pso_id: str, actor_user_id: str, lines: list) -> dict:
    """Confirm through the real service, so the snapshot is a real snapshot.

    Hand-writing `line_snapshots` would make this suite agree with itself about a shape only
    `ProjectSupplyService._snapshot` actually writes.
    """
    from app.schemas.project_supply import ConfirmSupplyBody
    from app.services.project_supply_service import ProjectSupplyService

    service = ProjectSupplyService(db)
    order = service.get_order(pso_id)
    body = service.confirm(
        order, ConfirmSupplyBody(lines=lines), actor_user_id=actor_user_id
    )
    db.flush()
    return body


def _holds_at(db, pso_id, warehouse) -> dict:
    """{project line id: qty held at `warehouse`} by the order's ACTIVE decision, fresh (a
    superseded revision's rows stay for audit and would double-count a carried hold)."""
    from app.models.project_so import ProjectSalesOrderLine, SOLineAllocation, SOSupplyDecision

    db.expire_all()
    ids = [
        row.id
        for row in db.query(ProjectSalesOrderLine.id).filter(
            ProjectSalesOrderLine.project_sales_order_id == pso_id
        )
    ]
    out: dict = {}
    for row in (
        db.query(SOLineAllocation)
        .join(SOSupplyDecision, SOSupplyDecision.id == SOLineAllocation.decision_id)
        .filter(
            SOLineAllocation.so_line_id.in_(ids),
            SOLineAllocation.warehouse_id == warehouse.id,
            SOSupplyDecision.state == "active",
        )
    ):
        out[row.so_line_id] = out.get(row.so_line_id, Decimal("0")) + row.qty
    return out


def _covered_world(db):
    """One order with two lines at one pile, one line of an earlier order ahead of both.

    Quantities are the live ones: line 1 owes 43 and is decided as a whole-line borrow of 43
    from the donor (AC-L5: a line is met wholly from stock or wholly bought, never a mix),
    line 2 owes 21 and is left undecided. The queue in front of them is a third order's line for 15,
    dated earlier, so the sibling's share of the pile is a real number rather than the whole
    of it.

    Nothing is confirmed here. The caller confirms line 1 when it wants the covered state, so
    the same world answers "before" and "after".
    """
    actor = _user(db, f"{MARKER} planner")
    product = _product(db, f"ZZT-{_uid()[:6]}")
    own = _warehouse(db, f"ZZTO{_uid()[:6]}"[:20])
    donor = _warehouse(db, f"ZZTD{_uid()[:6]}"[:20])
    _stock(db, product, own, on_hand=20)
    _stock(db, product, donor, on_hand=43)

    ahead = _order(db, so_number="ZZT-SO-AHEAD", order_date=date(2026, 1, 1))
    _line(db, ahead, product, qty="15", required_date=date(2026, 8, 30), warehouse=own)

    order = _order(db, so_number="ZZT-SO-COVER", order_date=date(2026, 1, 1))
    # Line 1 is dated AFTER line 2 on purpose: it is behind its sibling in the queue either
    # way, so removing it from that queue when it becomes covered cannot move the sibling.
    first = _line(db, order, product, qty="43", required_date=date(2026, 9, 4), warehouse=own)
    _line(db, order, product, qty="21", required_date=date(2026, 9, 1), warehouse=own)
    pso_id = _adopt(db, str(order.id))

    from app.models.project_so import ProjectSalesOrderLine

    mirror = (
        db.query(ProjectSalesOrderLine)
        .filter(
            ProjectSalesOrderLine.project_sales_order_id == pso_id,
            ProjectSalesOrderLine.core_sales_order_line_id == first.id,
        )
        .first()
    )
    return {
        "actor": actor,
        "product": product,
        "own": own,
        "donor": donor,
        "order": order,
        "ahead": ahead,
        "pso_id": pso_id,
        "mirror_line_id": str(mirror.id),
    }


def _decide_line_one(db, world) -> None:
    """Borrow the whole 43 from the donor, with a reason - the live composition."""
    _confirm(
        db,
        world["pso_id"],
        world["actor"],
        [
            {
                "project_line_id": world["mirror_line_id"],
                "timely_spo_qty": "0",
                "reserve": [],
                "borrow": [
                    {
                        "source": "other_location",
                        "warehouse_id": str(world["donor"].id),
                        "qty": "43",
                        "reason": "The other site can wait a week.",
                    }
                ],
                "buy_qty": "0",
                "amend_reason": "Borrowed rather than bought, agreed with the other site.",
            }
        ],
    )


def _covered_contribution(board, world) -> dict:
    return next(
        contribution
        for cell in board["cells"]
        for contribution in cell["contributions"]
        if contribution["so_number"] == "ZZT-SO-COVER" and contribution["qty"] == "43"
    )


def _sibling_contribution(board) -> dict:
    return next(
        contribution
        for cell in board["cells"]
        for contribution in cell["contributions"]
        if contribution["so_number"] == "ZZT-SO-COVER" and contribution["qty"] == "21"
    )


def test_a_line_an_active_decision_covers_says_so_and_carries_what_was_frozen():
    with blank_session() as db:
        world = _covered_world(db)
        _decide_line_one(db, world)

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        contribution = _covered_contribution(board, world)
        assert contribution["covered"] is True
        decision = contribution["decision"]
        assert decision["revision_no"] == 1
        assert decision["confirmed_at"] is not None
        assert decision["timely_spo_qty"] == "0"
        assert decision["reserve"] == []
        assert decision["buy_qty"] == "0"
        assert decision["amend_reason"] == (
            "Borrowed rather than bought, agreed with the other site."
        )
        assert decision["borrow"] == [
            {
                "source": "other_location",
                "warehouse_id": str(world["donor"].id),
                "location": world["donor"].warehouse_code,
                "donor_project_id": None,
                "qty": "43",
                # The person's own reason, not the rule's sentence: it is what the
                # confirmation demands back when this composition is re-posted.
                "reason": "The other site can wait a week.",
                # A plain free-stock borrow names no donor SALES-ORDER line - that is a
                # group-borrow fact (section E.4), and this composition is not one.
                "rung": None,
                "donor_so_number": None,
                "donor_line_no": None,
                "donor_agent_code": None,
                "same_agent": False,
                "donor_core_line_id": None,
                "donor_required_date": None,
                "order_back_qty": None,
                # Ladder v7.1 step 3 (S4): the document a `supply_borrow` names. This
                # composition is a plain free-stock borrow, so all three are absent - and
                # the keys are still here, because they are what the editor re-posts.
                "supply_key": None,
                "supply_document": None,
                "arrival_date": None,
            }
        ]
        # A covered line was not bought against a discontinued product, so no reason was
        # given; the key is still there for Amend to seed from.
        assert decision["buy_reason"] is None
        # Amend on a covered line reads the same flags a proposal states (this world seeds
        # no retail classification, so it is stated unavailable, not guessed).
        assert contribution["item_flags"] == {
            "dealer_hot_selling": False,
            "dealer_hot_selling_where": [],
            "project_hot_selling": False,
            "project_hot_selling_where": [],
            "dealer_classified": False,
            "project_classified": False,
            "discontinued": False,
            "retail_classification_available": False,
        }


def test_a_confirmed_group_borrow_round_trips_through_the_board_decision_dict():
    """Review finding B2: the frozen decision dict used to drop every group-borrow donor
    field (rung, donor_so_number, donor_line_no, donor_agent_code, same_agent,
    donor_core_line_id, donor_required_date, order_back_qty), so `boardAmend.frozenDraft`
    read `row.donor_core_line_id ?? null` back as null on Amend, and re-posted a covered
    group-borrow line as a plain free-stock donor - which the own-location check (rule 7)
    then refused, or which silently posted a sibling location's stock with the order-back
    dropped.
    """
    with blank_session() as db:
        borrow_from_order_on(db)
        actor = _user(db, f"{MARKER} planner")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        # A hyphenated code, so it carries an ownership group (`group_of_warehouse_code`):
        # the confirmation refuses a group borrow at a location with none.
        own = _warehouse(db, f"ZZT-OWN{_uid()[:4]}"[:20])
        _stock(db, product, own, on_hand=0)

        # The donor: another sales order's own line at the same location, lent to this one.
        donor_order = _order(
            db, so_number=f"ZZT-SO-DONOR{_uid()[:4]}", order_date=date(2026, 1, 1)
        )
        donor_line = _line(
            db, donor_order, product, qty="90", required_date=date(2026, 9, 10), warehouse=own,
        )

        order = _order(db, so_number=f"ZZT-SO-BORROW{_uid()[:4]}", order_date=date(2026, 1, 1))
        line = _line(db, order, product, qty="90", required_date=date(2026, 9, 3), warehouse=own)
        pso_id = _adopt(db, str(order.id))

        from app.models.project_so import ProjectSalesOrderLine

        mirror = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == pso_id,
                ProjectSalesOrderLine.core_sales_order_line_id == line.id,
            )
            .first()
        )

        _confirm(
            db,
            pso_id,
            actor,
            [
                {
                    "project_line_id": str(mirror.id),
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "borrow": [
                        {
                            "source": "other_location",
                            "warehouse_id": str(own.id),
                            "qty": "90",
                            "reason": "Group borrow, auto-proposed.",
                            "donor_core_line_id": str(donor_line.id),
                            "donor_so_number": donor_order.so_number,
                            "donor_line_no": 4,
                            "donor_agent_code": "JEREMY",
                        }
                    ],
                    "buy_qty": "0",
                }
            ],
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = next(
            c for cell in board["cells"] for c in cell["contributions"]
            if c["so_number"] == order.so_number
        )
        assert contribution["covered"] is True
        borrow = contribution["decision"]["borrow"][0]
        assert borrow["donor_core_line_id"] == str(donor_line.id)
        assert borrow["donor_so_number"] == donor_order.so_number
        assert borrow["donor_line_no"] == 4
        assert borrow["donor_agent_code"] == "JEREMY"
        assert borrow["same_agent"] is False
        assert borrow["order_back_qty"] == "90"
        assert borrow["rung"] == "group_borrow"


def test_an_undecided_line_of_the_same_order_is_not_marked_covered():
    with blank_session() as db:
        world = _covered_world(db)
        _decide_line_one(db, world)

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        sibling = _sibling_contribution(board)
        assert sibling["covered"] is False
        assert sibling["decision"] is None


def test_a_covered_line_is_not_run_through_the_ladder_again():
    """The heart of the defect: it was, and it re-proposed a Buy for a decided line."""
    with blank_session() as db:
        world = _covered_world(db)
        _decide_line_one(db, world)

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        contribution = _covered_contribution(board, world)
        # The FROZEN composition, in the frozen order, and never a fresh Buy 43.
        assert [
            (source["kind"], source["qty"], source["location"])
            for source in contribution["sources"]
        ] == [
            ("borrow", "43", world["donor"].warehouse_code),
        ]
        assert contribution["qty_proposed_reserve"] == "0"
        assert contribution["qty_proposed_incoming"] == "0"
        assert contribution["qty_proposed_buy"] == "0"
        # No ladder was walked for it, so it carries none - rather than an invented one whose
        # first rung reads "had 20, offered 0", which is what a queue it is not in produces.
        assert contribution["trail"] == []
        assert contribution["contested"] is False


def test_a_covered_line_says_nothing_about_a_queue_it_is_not_in():
    """Absent, never zero.

    "0 left for this line" is a claim about a contest. A covered line is not in the contest:
    its claim on the pile is already expressed as a hold, which is exactly why `_pile_book`
    leaves it out. So the three share fields are null and the screen says nothing.
    """
    with blank_session() as db:
        world = _covered_world(db)
        _decide_line_one(db, world)

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        contribution = _covered_contribution(board, world)
        assert contribution["so_qty_ahead"] is None
        assert contribution["lines_ahead"] is None
        assert contribution["available_to_this_line"] is None


def test_covering_a_line_does_not_move_what_is_left_for_the_lines_behind_it():
    """The queue is the book's, and the covered line was never ahead of its sibling.

    Line 1 is dated after line 2, so it sits BEHIND it in the queue whether it is covered or
    not. Its removal from that queue (the double-count rule) must therefore leave the
    sibling's share exactly as it was, and the line of the earlier order must still be in
    front of it.
    """
    with blank_session() as db:
        world = _covered_world(db)

        before = _sibling_contribution(
            _service(db).build(
                ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
            )
        )
        _decide_line_one(db, world)
        after = _sibling_contribution(
            _service(db).build(
                ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
            )
        )

        for field in ("so_qty_ahead", "lines_ahead", "available_to_this_line"):
            assert after[field] == before[field], field
        # And the numbers are real ones, so the equality means something: the earlier order's
        # 15 is still ahead of this line, leaving 5 of the 20 on hand for it.
        assert after["lines_ahead"] == 1
        assert after["so_qty_ahead"] == "15"
        assert after["available_to_this_line"] == "5"


def test_an_uncovered_line_of_a_partly_confirmed_order_is_still_proposed():
    """Ladder v3: the own location holds 5 against the sibling's 21, so the whole-line rule
    buys the lot - the seven-rung trail (the read-only own-location strip first) is still
    walked in full."""
    with blank_session() as db:
        world = _covered_world(db)
        _decide_line_one(db, world)

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        sibling = _sibling_contribution(board)
        assert sibling["qty_proposed_reserve"] == "0"
        assert sibling["qty_proposed_buy"] == "21"
        assert [step["kind"] for step in sibling["trail"]] == [
            "pool", "own", "order_borrow", "supply_borrow", "buy",
        ]


def test_a_cell_totals_the_frozen_numbers_for_a_covered_line():
    """The strip and the counts read what was decided, not what would have been proposed."""
    with blank_session() as db:
        world = _covered_world(db)
        _decide_line_one(db, world)

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, world["product"].product_code, "2026-08-31")
        assert {c["qty"] for c in cell["contributions"]} == {"43", "21"}
        location = next(
            entry for entry in cell["locations"]
            if entry["location"] == world["own"].warehouse_code
        )
        # Line 1's whole 43 is borrowed from another location, so the only Buy at the own
        # location is the sibling's 21; nothing is reserved (its 20 are owed to the queue).
        assert location["qty_proposed_reserve"] == "0"
        assert location["qty_proposed_buy"] == "21"
        # Only the sibling is in a contest. A decided line is not competing for anything.
        assert cell["contested_count"] == 1


def test_the_covered_state_reaches_the_wire():
    """A field the service returns and the response model does not declare is dropped."""
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        world = _covered_world(db)
        _decide_line_one(db, world)
        db.commit()

        client, originals = _client(db, world["actor"], [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": "ZZT-SO-COVER,ZZT-SO-AHEAD", "granularity": "week"},
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        contribution = _covered_contribution(response.json(), world)
        assert contribution["covered"] is True
        assert contribution["decision"]["revision_no"] == 1
        assert contribution["decision"]["buy_qty"] == "0"
        assert contribution["decision"]["borrow"][0]["qty"] == "43"
        assert [source["kind"] for source in contribution["sources"]] == ["borrow"]
        assert contribution["so_qty_ahead"] is None


# --------------------------------------------------------------------------- #
# the flags the ladder judged the item on, and the pool pile behind rung 2
#
# The captain, 19 August 2026, reading the trail popover: "where is the consideration of
# dealer hot selling / project hot selling / discontinued, to see if we can take from BRW?"
# and, on a rung reading `Pool BRW | Had 0` beside an Inventory screen showing `Available 1`:
# "why it shows 0?" and, on a rung that took 4 from the pool: "is it reached to take from BRW
# because it is project hot selling and not dealer hot selling? ... cause when we take from
# BRW, first we need to see its available quantity also".
#
# Three answers. The flags WERE consulted and were simply never printed, so every contribution
# now states them and the rungs they decide say so in words. There is no "project hot-selling"
# concept - only the dealer one - and saying that plainly is the answer to the question. And
# the pool's `Had` is what is left after the POOL'S OWN book ranked ahead of this line, which
# is a different number from the pile's availability, so rung 2 carries the whole pile beside
# it.
# --------------------------------------------------------------------------- #


def _flags(contribution) -> dict:
    return contribution["item_flags"]


def _dealer_pool(
    db,
    product,
    *,
    abc_class_retail: str | None = "A",
    abc_class_project: str | None = None,
    level: int | None = None,
):
    """A project location whose pool warehouse the item is classified at, by demand class.

    The live shape: BRW-BB fulfils, BRW is the shared pool, and hot-selling is read off the
    DEMAND CLASS the ABC figure was computed against - `abc_class_retail` for dealer,
    `abc_class_project` for project (PLAN-scm-front-planning 3.3a, amended 19 August 2026) -
    never the warehouse's own segment.
    """
    from app.models.scm import ItemClassification, ReorderLevel

    pool = _warehouse(db, f"ZZTP{_uid()[:6]}"[:20])
    own = _warehouse(db, f"ZZTO{_uid()[:6]}"[:20])
    own.pool_warehouse_id = pool.id
    db.flush()
    db.add(
        ItemClassification(
            id=_uid(),
            product_id=product.id,
            warehouse_id=pool.id,
            abc_class_retail=abc_class_retail,
            abc_class_project=abc_class_project,
        )
    )
    if level is not None:
        db.add(
            ReorderLevel(id=_uid(), product_id=product.id, warehouse_id=pool.id, level=level)
        )
    db.flush()
    return own, pool


def test_a_contribution_states_the_flags_the_ladder_judged_the_item_on():
    """Not hot-selling, not discontinued, and classified - each said, none implied."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="B")
        _stock(db, product, own, on_hand=10)
        _stock(db, product, pool, on_hand=10)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert _flags(contribution) == {
            "dealer_hot_selling": False,
            "dealer_hot_selling_where": [],
            "project_hot_selling": False,
            "project_hot_selling_where": [],
            "dealer_classified": True,
            "project_classified": False,
            "discontinued": False,
            "retail_classification_available": True,
        }


def test_an_item_nobody_has_classified_says_so_rather_than_reading_as_cold():
    """"Not hot-selling" and "nobody has classified it" are different answers.

    The second one is the PLAN's "Retail classification unavailable" state, and a screen that
    printed it as the first would claim evidence that does not exist.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        flags = _flags(_cell(board, product.product_code, "2026-08-31")["contributions"][0])
        assert flags["retail_classification_available"] is False
        assert flags["dealer_hot_selling"] is False


def test_a_row_with_both_letters_null_is_unclassified_not_cold_and_the_pool_offers_as_normal():
    """A row EXISTS (the classification job ran) but both `abc_class_project` and
    `abc_class_retail` are NULL - no delivered demand of either class in the trailing-12mo
    window, "unknown", never a computed "not hot". This is the whole book today: reading
    "row present" as "seen, therefore cold" would print a false "Not dealer hot-selling"
    for stock nobody has actually judged (captain, 19 August 2026). The pool rung offers
    the pool as it would for a classified, non-hot item, and says why in words.
    """
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail=None, abc_class_project=None)
        _stock(db, product, own, on_hand=0)
        # 12: ladder v8 offers a project HALF the pool (R-B), and this case is about the
        # sentence a not-classified item reads, not about the size of the pile.
        _stock(db, product, pool, on_hand=12)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        flags = _flags(contribution)
        assert flags["retail_classification_available"] is False
        assert flags["dealer_hot_selling"] is False
        assert flags["project_hot_selling"] is False

        pool_step = _step(contribution, "pool")
        # "the pool offers its balance as for a non-hot item" - said in the sentence below,
        # which is where every pool figure lives under ladder v5.
        assert pool_step["took"] == "4"
        assert pool_step["why"] == (
            "Not classified (no retail or project deliveries of this item in the last 12 "
            f"months), so {pool.warehouse_code} is offered as for a cold item. This line "
            "takes 4."
        )


def test_a_hot_selling_item_names_the_dealer_locations_that_made_it_hot():
    """"ABC A at BRW" is checkable; a bare boolean is something to take on trust."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="A")
        _stock(db, product, own, on_hand=20)
        _stock(db, product, pool, on_hand=12)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        flags = _flags(_cell(board, product.product_code, "2026-08-31")["contributions"][0])
        assert flags["dealer_hot_selling"] is True
        assert flags["dealer_hot_selling_where"] == [pool.warehouse_code]


# `test_the_own_rung_never_states_a_hot_selling_verdict_a_classified_item_earned` and
# `test_the_own_rung_reserves_a_dealer_hot_selling_items_own_stock_without_saying_so` DELETED
# (ladder v2, section E rule 7): both asserted the removed `reserve_own` step's `why`/outcome.
# Dealer hot-selling gating only the POOL, never the (now nonexistent) own-location rung, is
# covered by `test_the_pool_rung_names_the_classification_that_keeps_the_pool_for_retail` and
# `test_a_hot_selling_rung_says_why_the_pool_was_off_limits` below.


def test_the_pool_rung_names_the_classification_beside_what_it_gave():
    """The sentence that used to sit on rung 1 moved to rung 2 (PLAN 3.3a). LADDER V8 (R-A):
    dealer hot-selling no longer gates the pool, and the sentence still names it - the
    classification is evidence a planner reads, not only a rule that fired."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="A")
        _stock(db, product, own, on_hand=20)
        # 24: half of it is the 4 this line asks for (ladder v8, R-B).
        _stock(db, product, pool, on_hand=24)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )
        # LADDER V8 (R-A): hot at retail no longer refuses the pool - the SHARE keeps
        # stock for dealers now - so the step answers with what it gave, and the sentence
        # still names the classification the captain reads.
        assert step["answer"] == "yes"
        assert step["took"] == "4"
        assert step["why"].startswith(f"Dealer hot-selling at {pool.warehouse_code}")


def test_a_discontinued_item_no_longer_names_a_reason_requirement_on_the_buy_rung():
    """AC-43: `is_discontinued` no longer forces a reason at confirm (the gate in
    `project_supply_service.py` is removed), so the buy-rung explanation drops the
    "Discontinued: the buy needs a reason." suffix - the sentence is the plain buy
    explanation, unchanged from any other Buy. `item_flags` still reports the product
    as discontinued; that flag is informational (drives the frozen `lifecycle_warning`),
    not a confirmation requirement."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        product.is_discontinued = True
        db.flush()
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert _flags(contribution)["discontinued"] is True
        why = _step(contribution, "buy")["why"]
        assert why == "Nothing left to take, so the remainder is bought."


def test_a_line_the_ladder_never_walked_carries_no_flags_rather_than_false_ones():
    """An unplannable line was never judged against anything, and `false` would say it was."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="4", required_date=date(2026, 9, 3), warehouse=None)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["unplannable"] is True
        assert contribution["item_flags"] is None


# ------------------------------------------------------- the pool pile behind rung 2


def test_the_pool_rung_never_overstates_another_pools_offer_past_its_own_availability():
    """S5 of the review findings: `pool_offered_total` used to add every OTHER pool's raw
    `free` stock, uncapped by that pool's own signed Available - the same cap
    `pool_reserve_capacity` applies to the primary pool. A second pool oversold by its own
    book (free stock reads positive, but Available is negative) offers the ladder nothing
    and must offer the trail nothing either - `pool_reserve_capacity` run over the WHOLE
    chain, not merely the primary pool."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool1 = _pooled_warehouses(db)
        pool2 = _warehouse(db, f"ZZTP2{_uid()[:6]}"[:20])
        other_own = _warehouse(db, f"ZZTX{_uid()[:6]}"[:20])
        other_own.pool_warehouse_id = pool2.id
        db.flush()

        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool1, on_hand=0)
        _stock(db, product, pool2, on_hand=100)
        # pool2's own book oversells it: on hand 100, SO qty 150 -> Available -50, even
        # though `free` (on hand less reserved less confirmed holds) still reads 100.
        oversell = _order(
            db, so_number=f"ZZT-SO-OVER{_uid()[:6]}", order_date=date(2026, 1, 1)
        )
        _line(db, oversell, product, qty="150", required_date=date(2026, 3, 1), warehouse=pool2)

        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        step = _step(contribution, "pool")
        assert step["answer"] == "no", "pool2 is oversold, so it must offer nothing"
        # The SENTENCE, not only the verdict: a No with no arithmetic in it is the trail
        # this whole rewrite replaced. -50 at pool2 against 0 at pool1 is the pile's net,
        # and that number is what refused the draw.
        assert "the site pools net -50 between them" in step["why"], step["why"]
        assert pool2.warehouse_code in (step["note"] or ""), (
            "an oversold pool was still opened, and a pool nobody names looks like one "
            "nobody checked"
        )


def test_the_pool_rung_carries_the_piles_own_autocount_triple():
    """The captain, on `Pool BRW | Had 0` beside `Available 1` in Inventory: "why it shows 0?"

    Because `Had` is what is left after the pool's own book, and the pile's position is a
    different number. Both are now on the rung, and they reconcile.
    """
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="B")
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=1)
        # The pool's OWN book: one dealer line owed at BRW, due before ours.
        theirs = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="1", required_date=date(2026, 9, 1), warehouse=pool)
        ours = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="1", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        assert step["pool"] is not None
        pile = step["pool"]
        assert pile["location"] == pool.warehouse_code
        assert pile["on_hand"] == "1"
        assert pile["so_qty"] == "1"
        assert pile["spo_qty"] == "0"
        # AutoCount's own arithmetic, signed and never clamped: the 1 on hand is already
        # sold to the pool's own line, so the pile's position is 0. (The `Available 1` the
        # captain saw is Inventory's on hand less reserved, which the sentence quotes.)
        assert pile["available"] == "0"
        assert pile["reserved"] == "0"
        assert Decimal(pile["available"]) == (
            Decimal(pile["on_hand"]) - Decimal(pile["so_qty"]) + Decimal(pile["spo_qty"])
        )
        # ... and the pool's own line ranks ahead of ours, so nothing is left here.
        assert pile["claimed_ahead_qty"] == "1"
        assert pile["claimed_ahead_lines"] == 1
        assert step["took"] == "0"


def test_the_pool_rung_says_in_words_why_the_pile_had_stock_and_the_line_got_none():
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="B")
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=1)
        theirs = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="1", required_date=date(2026, 9, 1), warehouse=pool)
        ours = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="1", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        code = pool.warehouse_code
        assert step["why"] == (
            f"Cold at retail, so {code} is offered. {code} holds 1 on hand (Available 1 in "
            f"stock), but {code}'s own orders ranked ahead of this line claim 1, so 0 is left."
        )


def test_the_pool_rung_says_what_was_left_and_what_this_line_took():
    """The captain's CB2807-DIY rung: `Pool BRW | 4 | took 4`."""
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="B")
        _stock(db, product, own, on_hand=0)
        # 8 on hand: ladder v8 lends a project half of it, which is the 4 asked for (R-B).
        _stock(db, product, pool, on_hand=8)
        ours = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="4", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        assert step["took"] == "4"
        assert step["pool"]["claimed_ahead_qty"] == "0"
        assert step["pool"]["claimed_ahead_lines"] == 0
        assert step["why"] == (
            f"Cold at retail, so {pool.warehouse_code} is offered: 8 left after its own "
            "queue ahead of this line; this line takes 4."
        )


def test_a_dealer_hot_selling_pool_rung_never_states_a_cap():
    """The old reorder-level cap is gone (19 August 2026): `cap` stays null on the wire and
    the reorder level is still shown as evidence even though it no longer drives the
    arithmetic. LADDER V8 (R-A) retires the other half of this case - dealer hot-selling no
    longer empties the step; the SHARE is what keeps stock for dealers."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="A", level=10)
        _stock(db, product, own, on_hand=20)
        _stock(db, product, pool, on_hand=12)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        assert step["pool"]["reorder_level"] == "10"
        assert step["pool"]["cap"] is None
        # LADDER V8 (R-A): the reorder-level cap is still gone AND the dealer gate with it,
        # so the step answers with the pool's share - half of 12 - and buys the rest.
        assert step["answer"] == "yes"
        assert step["took"] == "6"
        assert step["why"].startswith(f"Dealer hot-selling at {pool.warehouse_code}")


def test_a_project_hot_selling_pool_rung_caps_the_draw_at_the_pools_availability():
    """PLAN 3.3a: project hot-selling draws the pool only up to its own SIGNED
    availability (`on hand - SO qty + SPO qty`). Another order's demand sitting directly
    at the pool - ranked BEHIND ours, so it claims nothing in the queue-ahead sense -
    still shrinks the pool's own position, and the draw stops there rather than at the
    higher balance THIS line's own queue would otherwise leave.
    """
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail=None, abc_class_project="A")
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=12)
        ours = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
        theirs = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="2", required_date=date(2026, 9, 10), warehouse=pool)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        assert step["pool"]["on_hand"] == "12"
        assert step["pool"]["so_qty"] == "2"
        assert step["pool"]["available"] == "10"
        # Bounded by the POOLS' own net and not by this pool's balance - the sentence below
        # is where that number is stated. LADDER V8 (R-B) bounds it once more: half of the
        # pile's 10 is what a project may take, so the line of 10 takes 5 and buys 5.
        assert step["took"] == "5"
        # Ladder v4: the classification leads, and the number after it is the PILE's.
        assert step["why"] == (
            f"Project hot-selling at {pool.warehouse_code}: the site pools net 10 between "
            "them, and 5 is offered here. This line takes 5."
        )


def test_a_project_hot_selling_pool_rung_offers_nothing_when_availability_is_not_positive():
    """The other side of the boundary: an oversold pool (signed availability <= 0) offers
    nothing at all, never a floor read as "some" (PLAN 3.3a)."""
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail=None, abc_class_project="A")
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=4)
        theirs = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="9", required_date=date(2026, 9, 10), warehouse=pool)
        ours = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="5", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        assert step["pool"]["available"] == "-5"
        assert step["answer"] == "no"
        assert step["took"] == "0"
        # Ladder v4: the classification is still named first, and the number after it is
        # the PILE's - 3.3a's per-pool availability cap is gone, the pools' own net bounds
        # every draw, and here that net is -5.
        assert step["why"] == (
            f"Project hot-selling at {pool.warehouse_code}: the site pools net -5 between "
            "them, so no pool is offered."
        )


def test_a_cold_pool_rung_says_the_pile_is_oversold_rather_than_offering_its_stale_balance():
    """Ladder v4: one cap for every item, and it is the PILE's net.

    The queue-netted balance ("Had") is still 4 - nothing of THIS line's own book ranks
    ahead of it - but the five pools' signed position is oversold by the wider book, so
    `offered` must read 0 rather than the stale balance, and the sentence must say WHY
    rather than silently printing a zero."""
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="B")
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=4)
        theirs = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="9", required_date=date(2026, 9, 10), warehouse=pool)
        ours = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="5", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        # The pile is oversold, so it offers nothing and says why - which is what this
        # case's own docstring has always claimed and what ladder v8's share rule makes
        # unambiguous: a share of a negative position is nothing, never a stale balance.
        assert step["pool"]["available"] == "-5"
        assert step["answer"] == "no"
        assert step["took"] == "0"
        assert step["why"] == (
            "Cold at retail: the site pools net -5 between them, so no pool is offered."
        )


def test_the_pool_rung_never_offers_more_than_the_pile_had_left():
    """The invariant behind the sub-table: `taken` can never exceed what was left.

    Ladder v2's whole-line rule (section E rule 6): `ours` asks for exactly what is left (2),
    so it is still fully covered - a bigger ask would collapse to a single Buy instead of
    reporting a capped partial take."""
    with blank_session() as db:
        _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _dealer_pool(db, product, abc_class_retail="B")
        _stock(db, product, own, on_hand=0)
        # 8 on hand with 4 claimed ahead leaves 4, and ladder v8 lends a project half of
        # it (R-B) - which is the 2 this line asks for.
        _stock(db, product, pool, on_hand=8)
        theirs = _order(db, so_number=f"ZZT-SO-A{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="4", required_date=date(2026, 9, 1), warehouse=pool)
        ours = _order(db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="2", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        assert step["pool"]["claimed_ahead_qty"] == "4"
        assert step["pool"]["claimed_ahead_lines"] == 1
        assert step["took"] == "2"


def test_a_line_with_no_pool_carries_no_pool_facts_rather_than_zeroes():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=4)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        step = _step(
            _cell(board, product.product_code, "2026-08-31")["contributions"][0],
            "pool",
        )

        assert step["pool"] is None
        assert step["why"] == "No shared pool holds this product."


def test_the_flags_and_the_pool_facts_reach_the_wire():
    """A field the service returns and the response model does not declare is dropped."""
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        product = _product(db, "ZZT-WIRE-FLAGS")
        own, pool = _dealer_pool(db, product, abc_class_retail="A", level=10)
        _stock(db, product, own, on_hand=20)
        _stock(db, product, pool, on_hand=12)
        order = _order(db, so_number="ZZT-SO-FLAGS", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
        actor = _user(db, f"{MARKER} planner")
        db.commit()

        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": "ZZT-SO-FLAGS", "granularity": "week"},
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        contribution = _cell(response.json(), "ZZT-WIRE-FLAGS", "2026-08-31")[
            "contributions"
        ][0]
        assert contribution["item_flags"]["dealer_hot_selling"] is True
        assert contribution["item_flags"]["dealer_hot_selling_where"] == [
            pool.warehouse_code
        ]
        assert contribution["item_flags"]["project_hot_selling"] is False
        assert contribution["item_flags"]["project_hot_selling_where"] == []
        pile = _step(contribution, "pool")["pool"]
        assert pile["on_hand"] == "12"
        assert pile["reorder_level"] == "10"
        assert pile["cap"] is None


# --------------------------------------------------------------------------- #
# review findings F3 / F6 / F8, 19 August 2026
#
# F3: a dealer hot-selling item whose fulfilment location IS its own pool (a bare site code
#     points at itself, migration 311). The engine reserves there above the reorder level, but
#     rung 1 said "hot-selling, not eligible", rung 2 said "the pool is this location, already
#     checked above", and neither counted the Reserve: the trail never reached 0 while the
#     source strip said Reserve 3.
# F6: donors were only looked up when the engine proposed a Buy, and cleared on a covered
#     line, so Amend on a Reserve-met or covered line said nobody held any stock and offered no
#     Add-a-borrow (the captain's flow: borrow instead of taking the reserved stock).
# F8: the queue dialog printed "Ahead because <factor>" on rows BEHIND the asked line.
# --------------------------------------------------------------------------- #


# `test_a_hot_selling_line_at_its_own_pool_reserves_in_full_on_the_own_rung` and
# `test_a_cold_line_at_its_own_pool_still_reserves_on_the_own_rung_and_not_twice` (the F3
# self-pool special case) DELETED (ladder v2, section E rule 7): a location that is its own
# pool is no longer special-cased at all - it is judged purely as a POOL, subject to the same
# dealer-hot-selling gate as any other pool, and the own-location rung both tests asserted is
# gone. Verified live: a self-pool dealer-hot-selling location with stock sitting on it now
# proposes a plain Buy, exactly as an ordinary hot-selling pool does - there is no "reserves in
# full regardless of hot-selling" case left to pin.


def test_a_reserve_met_line_still_carries_its_donors():
    """F6, restated for ladder v2 (section E rule 7): the own location is never a Reserve
    source at all now, so `own`'s 50 units are never touched and the whole line is bought -
    but the donor at `elsewhere` is still offered, ranked against what is actually being
    bought (10), so Amend can offer a Borrow instead."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTO{_uid()[:6]}"[:20])
        elsewhere = _warehouse(db, f"ZZTE{_uid()[:6]}"[:20])
        _stock(db, product, own, on_hand=50)
        _stock(db, product, elsewhere, on_hand=25)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        assert contribution["qty_proposed_reserve"] == "0"
        assert contribution["qty_proposed_buy"] == "10"
        assert [c["warehouse_code"] for c in contribution["borrow_candidates"]] == [
            elsewhere.warehouse_code
        ]
        candidate = contribution["borrow_candidates"][0]
        assert candidate["warehouse_id"] == str(elsewhere.id)
        assert candidate["need_qty"] == "10"
        assert candidate["available_after_need"] == "15"
        assert candidate["recommended"] is True
        assert contribution["qty_borrow_available"] == "25"
        # The trail still takes nothing from this donor: the whole-unit rule refused it.
        borrow_step = _step(contribution, "own")
        assert borrow_step["took"] == "0"


def test_a_covered_line_still_carries_the_donors_it_could_be_amended_to():
    """F6. Line 1 is decided as a whole-line borrow of 43 from the donor. A third location
    holds 7 free, so it is still offered on the covered line - a decision can be amended, and
    a covered line that offered nothing would say the stock exists nowhere."""
    with blank_session() as db:
        world = _covered_world(db)
        spare = _warehouse(db, f"ZZTX{_uid()[:6]}"[:20])
        _stock(db, world["product"], spare, on_hand=7)
        _decide_line_one(db, world)

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        contribution = _covered_contribution(board, world)
        assert contribution["covered"] is True
        # Still no ladder, no contest, no queue share: only the donors are read for it.
        assert contribution["trail"] == []
        assert contribution["so_qty_ahead"] is None
        candidates = contribution["borrow_candidates"]
        # The frozen donor's 43 is now a confirmed hold and out of its free stock, so it is
        # not offered again; the spare location is. The line's own location is inside its
        # Reserve reach and is never a donor.
        assert [c["warehouse_code"] for c in candidates] == [spare.warehouse_code]
        assert candidates[0]["warehouse_id"] == str(spare.id)
        assert candidates[0]["free_qty"] == "7"
        # Nothing is still being bought on a wholly borrowed line, so the ranking has no
        # residual to measure damage against - stated as 0, never invented.
        assert candidates[0]["need_qty"] == "0"
        assert contribution["qty_borrow_available"] == "7"


def test_the_pile_queue_names_no_leading_factor_on_a_row_behind_the_asked_line():
    """F8. "Ahead because" is a claim about the rows ABOVE the mark; below it the answer is
    null, which the screen reads as "Behind this line"."""
    with blank_session() as db:
        ours, product, warehouse = _queued_pile(db)
        board = _service(db).build([ours.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, "2026-12-28")
        # The one of our two lines that the queue put FIRST: its sibling is behind it.
        front = min(cell["contributions"], key=lambda c: c["lines_ahead"])

        queue = _service(db).pile_queue(str(product.id), str(warehouse.id), front["line_id"])

        mine = next(line for line in queue["lines"] if line["is_this_line"])
        assert mine["position"] == 6
        assert mine["leading_factor"] is None
        ahead = [line for line in queue["lines"] if line["position"] < mine["position"]]
        behind = [line for line in queue["lines"] if line["position"] > mine["position"]]
        assert len(behind) == 1
        assert all(line["leading_factor"] for line in ahead)
        assert behind[0]["leading_factor"] is None


# --------------------------------------------------------------------------- #
# the board's proposal and the confirm's recheck read ONE projection (13.5, 13.7)
#
# Live, 19 August 2026, SO403765 line 8 (CB2807-DIY, 43 owed, BRW-BB drawing on BRW): the
# board proposed "Reserve 4 at BRW, Buy 39" with a pool sub-table reading "on hand 7, claimed
# ahead 3 (1 line), left 4", and confirming exactly that returned 409 "BRW has nothing free
# for this line now". Two readers of the same pool were ranking the asking line differently.
# --------------------------------------------------------------------------- #


def _board_payload_for(board, so_number: str) -> tuple[str, list]:
    """The confirmation body built from FIELDS ON THE BOARD RESPONSE, for one order."""
    standing = next(o for o in board["orders"] if o["so_number"] == so_number)
    lines = []
    for cell in board["cells"]:
        for contribution in cell["contributions"]:
            if contribution["so_number"] != so_number or contribution["covered"]:
                continue
            lines.append(
                {
                    "project_line_id": contribution["project_line_id"],
                    "timely_spo_qty": contribution["qty_proposed_incoming"],
                    "reserve": [
                        {"warehouse_id": s["warehouse_id"], "qty": s["qty"]}
                        for s in contribution["sources"]
                        if s["kind"] == "reserve"
                    ],
                    "buy_qty": contribution["qty_proposed_buy"],
                }
            )
    return standing["project_sales_order_id"], lines


def _pool_with_its_own_queue(db, *, ask_qty="4"):
    """The live shape, trimmed to fit ladder v2's whole-line rule. The pool's own book carries
    one line, dated EARLIER than ours: 3 due in April, no demand class on the sales order.
    Ours is a project-class line due in December on a document raised in May, so the fair
    policy ranks it behind the April line - 3 of the pool's 7 is claimed ahead of us and 4 is
    left. (The live shape also had a second donor order totalling 25 more at the pool, which
    would drive its SIGNED availability negative and cap the pool to nothing regardless of the
    queue - a real ladder v2 rule (`pool_reserve_capacity` now caps every pool this way, not
    only a hot-selling one) but a second fact this fixture does not need to carry at once.)
    """
    _policy(db, dict(priority.FAIR_WEIGHTS), dict(priority.FAIR_CLASS_WEIGHTS))
    actor = _user(db, f"{MARKER} planner")
    product = _product(db, f"ZZT-{_uid()[:6]}")
    own, pool = _pooled_warehouses(db)
    _stock(db, product, own, on_hand=0)
    # 14, not 7: ladder v8 lends a project half of what the pool's own queue leaves (R-B),
    # so the ask of 4 fits inside the share of the 11 left rather than falling outside it.
    _stock(db, product, pool, on_hand=14)
    _lead_time(db, product, 365)
    dealer = _customer(db, f"{MARKER} dealer", terms=30)
    ours = _customer(db, f"{MARKER} project customer", terms=30)

    early = _order(
        db, so_number="ZZT-SO-EARLY", customer=dealer, order_date=date(2026, 4, 10),
        demand_class=None,
    )
    _line(db, early, product, qty="3", required_date=date(2026, 4, 10), warehouse=pool)

    asking = _order(
        db, so_number="ZZT-SO-ASK", customer=ours, order_date=date(2026, 5, 15),
        demand_class="project",
    )
    _line(db, asking, product, qty=ask_qty, required_date=date(2026, 12, 28), warehouse=own)
    _adopt(db, str(asking.id))
    db.flush()
    return {"actor": actor, "product": product, "own": own, "pool": pool, "asking": asking}


def test_the_confirm_accepts_the_pool_reserve_the_board_proposed():
    """The ask (4) is sized to exactly what the pool's own book leaves, so ladder v3's
    whole-line rule still proposes it as a pure Reserve - the case where the board's own
    proposal and the confirm's recheck have to agree stays exercisable without a partial mix.
    """
    from app.services.project_supply_service import SupplyLinesRefused

    with blank_session() as db:
        world = _pool_with_its_own_queue(db)
        pool, product = world["pool"], world["product"]

        board = _service(db).build(["ZZT-SO-ASK"], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-12-28")["contributions"][0]
        assert [
            (s["kind"], s["qty"], s["location"]) for s in contribution["sources"]
        ] == [("reserve", "4", pool.warehouse_code)]
        pile = _step(contribution, "pool")["pool"]
        assert (pile["free"], pile["claimed_ahead_qty"], pile["claimed_ahead_lines"], pile["left"]) == (
            "14", "3", 1, "11"
        )

        pso_id, lines = _board_payload_for(board, "ZZT-SO-ASK")
        assert lines[0]["reserve"] == [{"warehouse_id": str(pool.id), "qty": "4"}]

        # Exactly the board's proposal, accepted: the confirm ranks this line against the
        # pool's own book on the SAME factors the board did.
        try:
            _confirm(db, pso_id, world["actor"], lines)
        except SupplyLinesRefused as refused:
            pytest.fail(f"the confirm refused what the board proposed: {refused.detail}")

        from app.models.project_so import SOSupplyDecision

        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == pso_id)
            .first()
        )
        components = decision.line_snapshots[0]["components"]
        assert [(c["kind"], c["qty"]) for c in components] == [("reserve", "4")]


def test_the_confirm_never_holds_more_at_the_pool_than_the_board_proposed():
    """The reverse: asking the pool for one more than it has left holds only what is left and
    records the one unit short - by quantity, not by location: the pool IS this line's pool,
    it simply has 11 left for it.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.

    The line owes 5 here (more than the pool's 4), so ladder v3's whole-line rule proposes a
    plain Buy for the whole of it - the wholly-from-stock composition below is hand-composed
    (an Amend), which is exactly the case `_check_line`'s recheck exists to police."""
    with blank_session() as db:
        world = _pool_with_its_own_queue(db, ask_qty="12")
        pool = world["pool"]

        board = _service(db).build(["ZZT-SO-ASK"], granularity="week", as_of=TODAY)
        pso_id, lines = _board_payload_for(board, "ZZT-SO-ASK")
        # Wholly from stock (AC-L5), and one unit more than the pool's own book leaves
        # (14 on hand, 3 claimed ahead of us, so 11 is left and 12 is asked for).
        lines[0]["reserve"] = [{"warehouse_id": str(pool.id), "qty": "12"}]
        lines[0]["buy_qty"] = "0"

        body = _confirm(db, pso_id, world["actor"], lines)

        [notice] = body["stock_short_notices"]
        assert f"only 11 free at {pool.warehouse_code}" in notice["reason"], notice
        assert ", 1 short" in notice["reason"], notice
        assert list(_holds_at(db, pso_id, pool).values()) == [Decimal("11")]


def _order_already_holding_at_the_pool(db):
    """Our OWN order's earlier line already holds 3 of the pool's 7 under its active revision,
    and is not being named again. The board nets that hold off the pool and leaves the covered
    line out of the queue, so the sibling is offered the 4 that is left. The confirm has to
    read the same 4: the hold is carried into the new revision verbatim (the union is the
    server's), so un-netting it because it belongs to "the order being composed" hands the
    sibling stock its own order is still holding.

    The sibling owes 5, more than the 4 left at the pool, so ladder v3's whole-line rule
    auto-proposes a plain Buy for the whole of it - which is itself the proof that the hold is
    netted on the READ side, since an un-netted pool would show 7 and cover the line.
    """
    actor = _user(db, f"{MARKER} planner")
    product = _product(db, f"ZZT-{_uid()[:6]}")
    own, pool = _pooled_warehouses(db)
    _stock(db, product, own, on_hand=0)
    _stock(db, product, pool, on_hand=7)
    _lead_time(db, product, 365)
    order = _order(db, so_number="ZZT-SO-HOLDS", order_date=date(2026, 5, 15))
    first = _line(db, order, product, qty="3", required_date=date(2026, 9, 3), warehouse=own)
    _line(db, order, product, qty="5", required_date=date(2026, 12, 28), warehouse=own)
    pso_id = _adopt(db, str(order.id))
    db.flush()

    from app.models.project_so import ProjectSalesOrderLine

    mirror = (
        db.query(ProjectSalesOrderLine)
        .filter(
            ProjectSalesOrderLine.project_sales_order_id == pso_id,
            ProjectSalesOrderLine.core_sales_order_line_id == first.id,
        )
        .first()
    )
    # Line 1 owes 3 and reserves all 3 at the pool - wholly from stock (AC-L5) - so the
    # sibling has the pool's other 4 left to be offered.
    _confirm(
        db,
        pso_id,
        actor,
        [
            {
                "project_line_id": str(mirror.id),
                "timely_spo_qty": "0",
                "reserve": [{"warehouse_id": str(pool.id), "qty": "3"}],
                "buy_qty": "0",
            }
        ],
    )
    return {"actor": actor, "product": product, "own": own, "pool": pool, "pso_id": pso_id}


def test_a_hold_the_same_order_carries_forward_is_netted_by_the_confirm_as_the_board_nets_it():
    """Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a
    refusal. The sibling asks 5 of a pool whose other 3 its own order still holds: it is
    held 4 and recorded 1 short, so the order together never holds more than the 7 on hand.
    """

    with blank_session() as db:
        world = _order_already_holding_at_the_pool(db)
        pool, product = world["pool"], world["product"]

        board = _service(db).build(["ZZT-SO-HOLDS"], granularity="week", as_of=TODAY)
        sibling = _cell(board, product.product_code, "2026-12-28")["contributions"][0]
        assert sibling["covered"] is False
        # Ladder v3's whole-line rule: 4 of the sibling's 5 is all the pool has left, so the
        # auto-ladder proposes a plain Buy for the whole line. An un-netted pool would show 7
        # and reserve the lot, so this assertion IS the read-side proof.
        assert [
            (s["kind"], s["qty"], s["location"]) for s in sibling["sources"]
        ] == [("buy", "5", None)]
        pile = _step(sibling, "pool")["pool"]
        assert pile["left"] == "4"

        pso_id, lines = _board_payload_for(board, "ZZT-SO-HOLDS")
        assert len(lines) == 1, "the covered line is carried, not re-posted"

        # And the write side agrees: a hand-composed Amend taking the whole 5 from the pool
        # holds only the 4 that are left, and the notice says the 3 are line 1's own hold,
        # carried by this same confirmation (7 on hand, 3 held, so only 4 free).
        lines[0]["reserve"] = [{"warehouse_id": str(pool.id), "qty": "5"}]
        lines[0]["buy_qty"] = "0"
        body = _confirm(db, pso_id, world["actor"], lines)
        [notice] = body["stock_short_notices"]
        assert notice["line_no"] == 2, notice
        assert f"only 4 free at {pool.warehouse_code}" in notice["reason"], notice
        assert ", 1 short" in notice["reason"], notice
        held = _holds_at(db, pso_id, pool)
        assert sorted(held.values()) == [Decimal("3"), Decimal("4")], held
        assert sum(held.values()) <= Decimal("7")


# --------------------------------------------------------------------------- #
# what a donor line lost (AC-L6)
#
# The captain, 25 August 2026: the donor's board cell reads "71 lent to SO415472". A borrow
# is visible on the borrowing side and was invisible on the giving side, so the agent whose
# stock moved found out when the delivery did not.
# --------------------------------------------------------------------------- #


def test_a_donor_line_says_what_was_lent_off_it_and_to_which_order():
    with blank_session() as db:
        actor = _user(db, f"{MARKER} planner")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTL{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=100)

        donor = _order(db, so_number="ZZT-SO-DONOR", order_date=date(2026, 1, 1))
        donor_line = _line(
            db, donor, product, qty="71", required_date=date(2026, 9, 4), warehouse=own
        )
        borrower = _order(db, so_number="ZZT-SO-TAKER", order_date=date(2026, 1, 1))
        borrow_line = _line(
            db, borrower, product, qty="71", required_date=date(2026, 9, 1), warehouse=own
        )
        pso_id = _adopt(db, str(borrower.id))
        db.flush()

        from app.models.project_so import ProjectSalesOrderLine

        mirror = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == pso_id,
                ProjectSalesOrderLine.core_sales_order_line_id == borrow_line.id,
            )
            .first()
        )
        _confirm(
            db,
            pso_id,
            actor,
            [
                {
                    "project_line_id": str(mirror.id),
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "borrow": [
                        {
                            "source": "other_location",
                            "warehouse_id": str(own.id),
                            "qty": "71",
                            "reason": "Authorised by agent CYNDI: the site is waiting.",
                            "donor_core_line_id": str(donor_line.id),
                            "donor_so_number": "ZZT-SO-DONOR",
                            "donor_line_no": 1,
                        }
                    ],
                    "buy_qty": "0",
                }
            ],
        )
        db.commit()

        board = _service(db).build(
            ["ZZT-SO-DONOR", "ZZT-SO-TAKER"], granularity="week", as_of=TODAY
        )

        contributions = [
            contribution
            for cell in board["cells"]
            for contribution in cell["contributions"]
        ]
        given = next(c for c in contributions if c["so_number"] == "ZZT-SO-DONOR")
        assert given["lent_to"] == [
            {"qty": "71", "so_number": "ZZT-SO-TAKER", "line_no": mirror.line_no}
        ]
        # The borrowing line lent nothing; the field is an empty list, never absent, so the
        # cell has one shape to read.
        taken = next(c for c in contributions if c["so_number"] == "ZZT-SO-TAKER")
        assert taken["lent_to"] == []

        # And it reaches the wire: a field the service returns and the response model does
        # not declare is dropped silently.
        from app.models.base import company_scope

        company_id = _sorento(db)
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={"orders": "ZZT-SO-DONOR,ZZT-SO-TAKER", "granularity": "week"},
                )
        finally:
            _restore(originals)
        assert response.status_code == 200, response.text
        on_the_wire = [
            contribution
            for cell in response.json()["cells"]
            for contribution in cell["contributions"]
            if contribution["so_number"] == "ZZT-SO-DONOR"
        ]
        assert on_the_wire and on_the_wire[0]["lent_to"] == [
            {"qty": "71", "so_number": "ZZT-SO-TAKER", "line_no": mirror.line_no}
        ]


# --------------------------------------------------------------------------- #
# a line beyond its reserve window (section 1e, AC-L1 / AC-V2)
#
# Ladder v3 walked no STOCK rung for a far line but still ran rung 1, on the grounds that
# incoming supply is already bought and a blanket "beyond the window means Buy" bought it a
# second time. LADDER V5 has no rung 1: an SPO is inside the ownership group's own net, and
# the one that covers a particular line reaches it through Link SPO on its order-inquiry
# row, after purchasing has read the buy. So a far line is bought whole, and the proof says
# so in four Noes and a Yes.
# --------------------------------------------------------------------------- #


def test_a_line_beyond_its_window_is_bought_whole_even_with_an_spo_landing_in_time():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTIW{_uid()[:5]}-BB"[:20])
        elsewhere = _warehouse(db, f"ZZTIE{_uid()[:5]}-IR"[:20])
        # Plenty of stock next door, none of which a far line may take.
        _stock(db, product, own, on_hand=0)
        _stock(db, product, elsewhere, on_hand=900)
        _lead_time(db, product, 90)
        far = TODAY + timedelta(days=174)
        _incoming(
            db, product, own,
            spo_number="ZZT-SPO-FAR1", allocated=441, received=0, arrives=far - timedelta(days=5),
        )
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="441", required_date=far, warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, far.isoformat())["contributions"][0]
        assert contribution["qty_proposed_incoming"] == "0"
        assert contribution["qty_proposed_buy"] == "441"
        assert [(s["kind"], s["qty"]) for s in contribution["sources"]] == [("buy", "441")]

        # Four Noes and a Yes, and every No names the window rather than reporting an empty
        # search that never happened.
        assert not any(step["kind"] == "incoming" for step in contribution["trail"])
        for kind in ("own", "order_borrow", "supply_borrow", "pool"):
            step = _step(contribution, kind)
            assert step["answer"] == "no", kind
            assert step["took"] == "0", kind
            assert "lead time window" in step["why"], kind
        buy = _step(contribution, "buy")
        assert buy["answer"] == "yes"
        assert buy["took"] == "441"
        assert "lead time window" in buy["why"]


def test_a_superseded_revisions_borrow_is_not_reported_as_lent():
    """`lent_to` reads the ACTIVE revision only. A borrow that was superseded is a record
    of what was once promised, not of stock that is gone - reporting it would tell the
    donor's agent their stock had moved when the order that took it has since given it
    back."""
    with blank_session() as db:
        actor = _user(db, f"{MARKER} planner")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTS{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=200)

        donor = _order(db, so_number="ZZT-SO-GAVE", order_date=date(2026, 1, 1))
        donor_line = _line(
            db, donor, product, qty="71", required_date=date(2026, 9, 4), warehouse=own
        )
        borrower = _order(db, so_number="ZZT-SO-TOOK", order_date=date(2026, 1, 1))
        borrow_line = _line(
            db, borrower, product, qty="71", required_date=date(2026, 9, 1), warehouse=own
        )
        pso_id = _adopt(db, str(borrower.id))
        db.flush()

        from app.models.project_so import ProjectSalesOrderLine

        mirror = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == pso_id,
                ProjectSalesOrderLine.core_sales_order_line_id == borrow_line.id,
            )
            .first()
        )
        # Revision 1 borrows the whole line off the donor ...
        _confirm(
            db, pso_id, actor,
            [
                {
                    "project_line_id": str(mirror.id),
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "borrow": [
                        {
                            "source": "other_location",
                            "warehouse_id": str(own.id),
                            "qty": "71",
                            "reason": "The site is waiting.",
                            "donor_core_line_id": str(donor_line.id),
                            "donor_so_number": "ZZT-SO-GAVE",
                            "donor_line_no": 1,
                        }
                    ],
                    "buy_qty": "0",
                }
            ],
        )
        # ... and revision 2 gives it back, buying the line instead.
        _confirm(
            db, pso_id, actor,
            [
                {
                    "project_line_id": str(mirror.id),
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "borrow": [],
                    "buy_qty": "71",
                    "amend_reason": "The other site needs its stock back.",
                }
            ],
        )
        db.commit()

        board = _service(db).build(
            ["ZZT-SO-GAVE", "ZZT-SO-TOOK"], granularity="week", as_of=TODAY
        )

        given = next(
            contribution
            for cell in board["cells"]
            for contribution in cell["contributions"]
            if contribution["so_number"] == "ZZT-SO-GAVE"
        )
        assert given["lent_to"] == []


def test_two_orders_borrowing_off_one_donor_line_both_appear_on_its_cell():
    """One donor line can lend to several orders, and the cell has to name each of them:
    "40 lent to ZZT-SO-A · 20 lent to ZZT-SO-B" is the whole of what left."""
    with blank_session() as db:
        actor = _user(db, f"{MARKER} planner")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTT{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=300)

        donor = _order(db, so_number="ZZT-SO-LENDER", order_date=date(2026, 1, 1))
        donor_line = _line(
            db, donor, product, qty="100", required_date=date(2026, 9, 25), warehouse=own
        )

        from app.models.project_so import ProjectSalesOrderLine

        def borrow(so_number: str, qty: str, required: date) -> None:
            taker = _order(db, so_number=so_number, order_date=date(2026, 1, 1))
            core = _line(db, taker, product, qty=qty, required_date=required, warehouse=own)
            pso_id = _adopt(db, str(taker.id))
            db.flush()
            mirror = (
                db.query(ProjectSalesOrderLine)
                .filter(
                    ProjectSalesOrderLine.project_sales_order_id == pso_id,
                    ProjectSalesOrderLine.core_sales_order_line_id == core.id,
                )
                .first()
            )
            _confirm(
                db, pso_id, actor,
                [
                    {
                        "project_line_id": str(mirror.id),
                        "timely_spo_qty": "0",
                        "reserve": [],
                        "borrow": [
                            {
                                "source": "other_location",
                                "warehouse_id": str(own.id),
                                "qty": qty,
                                "reason": "The site is waiting.",
                                "donor_core_line_id": str(donor_line.id),
                                "donor_so_number": "ZZT-SO-LENDER",
                                "donor_line_no": 1,
                            }
                        ],
                        "buy_qty": "0",
                    }
                ],
            )

        borrow("ZZT-SO-TAKER-A", "40", date(2026, 9, 1))
        borrow("ZZT-SO-TAKER-B", "20", date(2026, 9, 2))
        db.commit()

        board = _service(db).build(
            ["ZZT-SO-LENDER", "ZZT-SO-TAKER-A", "ZZT-SO-TAKER-B"],
            granularity="week", as_of=TODAY,
        )

        given = next(
            contribution
            for cell in board["cells"]
            for contribution in cell["contributions"]
            if contribution["so_number"] == "ZZT-SO-LENDER"
        )
        assert [(row["qty"], row["so_number"]) for row in given["lent_to"]] == [
            ("40", "ZZT-SO-TAKER-A"),
            ("20", "ZZT-SO-TAKER-B"),
        ]


# --------------------------------------------------------------------------- #
# AC-D1 / AC-D2: suggested BESIDE decided
#
# The board could only ever say what was DECIDED for a covered line - the frozen
# composition is what `_apply_frozen` prints as its sources - so "what did the engine
# suggest before somebody changed it" had no answer on screen. The snapshot now freezes
# the proposal too, and the contribution carries it under `proposed`.
# --------------------------------------------------------------------------- #


def _suggested_world(db):
    """One order, two lines at one pooled location: one to decide, one to leave alone."""
    actor = _user(db, f"{MARKER} planner")
    product = _product(db, f"ZZT-{_uid()[:6]}")
    own, pool = _pooled_warehouses(db)
    _stock(db, product, pool, on_hand=80)
    order = _order(db, so_number=f"ZZT-SO-SUGG-{_uid()[:6]}", order_date=date(2026, 1, 1))
    decided = _line(db, order, product, qty="10", required_date=date(2026, 9, 3),
                    warehouse=own)
    _line(db, order, product, qty="7", required_date=date(2026, 9, 3), warehouse=own)
    pso_id = _adopt(db, str(order.id))

    from app.models.project_so import ProjectSalesOrderLine

    mirror = (
        db.query(ProjectSalesOrderLine)
        .filter(
            ProjectSalesOrderLine.project_sales_order_id == pso_id,
            ProjectSalesOrderLine.core_sales_order_line_id == decided.id,
        )
        .first()
    )
    return {
        "actor": actor,
        "product": product,
        "own": own,
        "pool": pool,
        "order": order,
        "pso_id": pso_id,
        "mirror_line_id": str(mirror.id),
    }


def _contribution(board, qty: str) -> dict:
    return next(
        contribution
        for cell in board["cells"]
        for contribution in cell["contributions"]
        if contribution["qty"] == qty
    )


def test_a_covered_line_says_what_the_engine_had_suggested_beside_what_was_decided():
    """AC-D1/AC-D2: the frozen proposal reaches the board under `proposed`, rung and all,
    while `decision` goes on being the decided side. The planner bought a line the pool
    could have covered; a day later the board still shows both halves of that."""
    with blank_session() as db:
        world = _suggested_world(db)
        _confirm(
            db,
            world["pso_id"],
            world["actor"],
            [
                {
                    "project_line_id": world["mirror_line_id"],
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "buy_qty": "10",
                    "amend_reason": "Site asked for new stock.",
                }
            ],
        )

        board = _service(db).build(
            [world["order"].so_number], granularity="week", as_of=TODAY
        )
        contribution = _contribution(board, "10")

        assert contribution["covered"] is True
        assert contribution["decision"]["buy_qty"] == "10"
        # The engine had the pool, and says so, on the rung it drew from.
        assert [
            (c["kind"], c["qty"], c["location"], c["rung"])
            for c in contribution["proposed"]["components"]
        ] == [("reserve", "10", world["pool"].warehouse_code, "pool")]


def test_an_undecided_line_proposes_the_live_ladder_under_the_same_key():
    """One key, both states: the strip sums `proposed` over every contribution and must not
    have to ask which of two fields to read per line."""
    with blank_session() as db:
        world = _suggested_world(db)

        board = _service(db).build(
            [world["order"].so_number], granularity="week", as_of=TODAY
        )
        contribution = _contribution(board, "7")

        assert contribution["covered"] in (False, None)
        assert contribution["proposed"]["components"] == contribution["sources"]
        assert [c["rung"] for c in contribution["proposed"]["components"]] == ["pool"]


def test_a_covered_lines_frozen_reserve_carries_the_rung_it_was_drawn_from():
    """The board used to rebuild a covered line's composition WITHOUT the rung, so every
    reserve row of a decided line arrived as `rung: null` and the screen had to guess the
    vocabulary back from the warehouse code. The rung is frozen; it travels."""
    with blank_session() as db:
        world = _suggested_world(db)
        _confirm(
            db,
            world["pso_id"],
            world["actor"],
            [
                {
                    "project_line_id": world["mirror_line_id"],
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": str(world["pool"].id), "qty": "10"}],
                    "buy_qty": "0",
                }
            ],
        )

        board = _service(db).build(
            [world["order"].so_number], granularity="week", as_of=TODAY
        )
        contribution = _contribution(board, "10")

        assert contribution["decision"]["reserve"] == [
            {
                "warehouse_id": str(world["pool"].id),
                "location": world["pool"].warehouse_code,
                "qty": "10",
                "rung": "pool",
            }
        ]
        assert [(s["kind"], s["rung"]) for s in contribution["sources"]] == [
            ("reserve", "pool")
        ]


def test_a_covered_line_is_suggested_live_even_when_its_snapshot_recorded_nothing():
    """The snapshot is the record, not the suggestion (captain, 28 Aug 2026, ruling 1): a
    revision written before the proposal was frozen still gets today's ladder under
    `proposed`, because that is what a Save has to be able to commit."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.project_so import SOSupplyDecision

    with blank_session() as db:
        world = _suggested_world(db)
        _confirm(
            db,
            world["pso_id"],
            world["actor"],
            [
                {
                    "project_line_id": world["mirror_line_id"],
                    "timely_spo_qty": "0",
                    "reserve": [{"warehouse_id": str(world["pool"].id), "qty": "10"}],
                    "buy_qty": "0",
                }
            ],
        )
        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == world["pso_id"])
            .first()
        )
        decision.line_snapshots = [
            {k: v for k, v in snapshot.items() if k != "proposed_components"}
            for snapshot in decision.line_snapshots
        ]
        flag_modified(decision, "line_snapshots")
        db.flush()

        board = _service(db).build(
            [world["order"].so_number], granularity="week", as_of=TODAY
        )
        proposed = _contribution(board, "10")["proposed"]["components"]
        assert [(c["kind"], c["qty"], c["location"], c["rung"]) for c in proposed] == [
            ("reserve", "10", world["pool"].warehouse_code, "pool")
        ]


def test_a_covered_lines_suggestion_is_todays_ladder_not_the_snapshot():
    """SO381895, 28 August 2026: the snapshot froze "Pool BRW lends 30" on three dates by
    an engine with no pool ledger, revision 1 decided Reserve / Buy / Buy, and Approve
    resubmitted the snapshot until the confirm refused it. The board reads the ladder
    TODAY for a covered line, so a stale snapshot cannot reach the Save button."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.project_so import SOSupplyDecision

    with blank_session() as db:
        world = _suggested_world(db)
        _confirm(
            db,
            world["pso_id"],
            world["actor"],
            [
                {
                    "project_line_id": world["mirror_line_id"],
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "buy_qty": "10",
                    "amend_reason": "Site asked for new stock.",
                }
            ],
        )
        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == world["pso_id"])
            .first()
        )
        stale = [
            {
                "kind": "buy", "qty": "10", "location": None, "warehouse_id": None,
                "reason": "an engine that has since been corrected", "rung": "buy",
            }
        ]
        decision.line_snapshots = [
            {**snapshot, "proposed_components": stale}
            for snapshot in decision.line_snapshots
        ]
        flag_modified(decision, "line_snapshots")
        db.flush()

        board = _service(db).build(
            [world["order"].so_number], granularity="week", as_of=TODAY
        )
        contribution = _contribution(board, "10")

    assert contribution["covered"] is True
    assert contribution["decision"]["buy_qty"] == "10", "the decided side is untouched"
    assert [
        (c["kind"], c["qty"], c["location"], c["rung"])
        for c in contribution["proposed"]["components"]
    ] == [("reserve", "10", world["pool"].warehouse_code, "pool")]


def test_the_suggested_side_reaches_the_wire():
    """A field the service returns and the response model does not declare is dropped."""
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        world = _suggested_world(db)
        _confirm(
            db,
            world["pso_id"],
            world["actor"],
            [
                {
                    "project_line_id": world["mirror_line_id"],
                    "timely_spo_qty": "0",
                    "reserve": [],
                    "buy_qty": "10",
                    "amend_reason": "Site asked for new stock.",
                }
            ],
        )
        db.commit()

        client, originals = _client(db, world["actor"], [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={
                        "orders": world["order"].so_number,
                        "granularity": "week",
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        contribution = _contribution(response.json(), "10")
        assert contribution["proposed"] is not None
        proposed = contribution["proposed"]["components"]
        assert [(c["kind"], c["qty"], c["rung"]) for c in proposed] == [
            ("reserve", "10", "pool")
        ]
        assert proposed[0]["location"] == world["pool"].warehouse_code


# --------------------------------------------------------------------------- #
# Ladder v4 (section 1d): the trail says whose number refused the draw
# --------------------------------------------------------------------------- #


def test_the_group_rung_names_the_group_net_when_the_group_has_nothing_for_this_line():
    """AC-L7, in the words a planner reads. `MWH-IB` holding 7000 is not an answer to "why
    nothing" while `BRW-IB` owes more than the group holds, so the sentence is about the
    GROUP's net and never about one warehouse."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        group = f"IB{_uid()[:4]}"
        own = _warehouse(db, f"ZZTB{_uid()[:4]}-{group}"[:20])
        sibling = _warehouse(db, f"ZZTM{_uid()[:4]}-{group}"[:20])
        _stock(db, product, own, on_hand=10)
        _stock(db, product, sibling, on_hand=70)
        theirs = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="200", required_date=date(2026, 9, 1), warehouse=own)
        ours = _order(db, so_number="ZZT-SO-V4WHY", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="20", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build(["ZZT-SO-V4WHY"], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        step = _step(contribution, "own")
        assert step["answer"] == "no"
        # 10 + 70 on hand less 220 owed - this line's own 20 included, which is what the
        # net IS. What the line may take un-nets its own 20 and is still nothing.
        assert f"The {group.upper()} group nets -140" in step["why"]
        assert "nothing left for this line" in step["why"]
        # And the sibling's 70 is not named as though it were available.
        assert sibling.warehouse_code not in step["why"]


def test_question_one_offers_the_groups_spo_and_names_both_of_its_numbers():
    """AC-V2 through the board. The group's SPO is INSIDE its net and there is no rung
    above this question for it to be spent on, so the water is part of what question 1
    offers - and the sentence names the group's net AND what that net leaves for this line,
    because they are different facts (AC-L14).
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        group = f"IB{_uid()[:4]}"
        own = _warehouse(db, f"ZZTS{_uid()[:4]}-{group}"[:20])
        _incoming(
            db, product, own,
            spo_number="ZZT-SPO-V4G", allocated=40, received=0, arrives=date(2026, 8, 20),
        )
        # 60 asked for against 40 on the water: the group nets -20, which leaves 40 for this
        # line, and all 40 of it is the SPO. The line is NOT fully covered, so the
        # whole-line rule drops the partial and buys the 60 - and question 1 has to explain
        # what it had and why none of it was taken.
        ours = _order(db, so_number="ZZT-SO-V4SPO", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="60", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build(["ZZT-SO-V4SPO"], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        step = _step(contribution, "own")
        assert step["answer"] == "no"
        assert f"The {group.upper()} group nets -20, leaving 40 for this line" in step["why"]
        assert "could not cover the whole line" in step["why"]
        assert not any(s["kind"] == "incoming" for s in contribution["trail"])
        # And the whole-line rule then buys the 60, since 40 is not all of it.
        assert contribution["qty_proposed_buy"] == "60"


def test_the_pool_rung_names_the_pools_net_and_the_classification_in_front_of_it():
    """AC-L8. One pool oversold and another holding 1: per-pool arithmetic would offer the
    1, and the five pools net -102 between them, so nothing is offered.

    The sentence names the pile's net, and the classification first, because "just tell me
    hot selling or cold selling" is the captain's own instruction about this rung and the
    net is a different fact from it.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        other_pool = _warehouse(db, f"ZZTQ{_uid()[:6]}"[:20])
        second = _warehouse(db, f"ZZTR{_uid()[:6]}"[:20])
        second.pool_warehouse_id = other_pool.id
        db.flush()
        # The line's own pool is oversold; the other pool holds 1.
        _stock(db, product, other_pool, on_hand=1)
        theirs = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="103", required_date=date(2026, 9, 1), warehouse=pool)
        ours = _order(db, so_number="ZZT-SO-V4POOL", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build(["ZZT-SO-V4POOL"], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]

        step = _step(contribution, "pool")
        assert step["answer"] == "no"
        assert "the site pools net -102 between them" in step["why"]
        assert step["why"].startswith("Not classified"), (
            "the classification leads the sentence, per the captain's own instruction"
        )
        assert contribution["qty_proposed_buy"] == "10"


# --------------------------------------------------------------------------- #
# R17 / AC-S1-6 - the fulfilment-planning flag on the board
# --------------------------------------------------------------------------- #


def test_a_covered_row_at_a_bin_outside_planning_is_covered_not_unplannable():
    """The flag verdict belongs to UNDECIDED lines only (review of S2, 30 Aug).

    A line an active decision covers is covered: the stock was found, promised and
    confirmed. Flagging the bin off afterwards says what may be PROPOSED next; it does not
    retract what was decided. Reading `unplannable` off the flag alone turned a settled
    order into `Needs a location` / `blocked`, took it out of the board's own
    `unplannable_count` standing, and had the frontend's `confirmLinesFor` drop it.

    It also kept its location in the counted read set only by a special case, because the
    board fixes that set off the plannable rows - and a location never asked about prints
    dashes, not zeroes (`_counted_warehouses`), so the frozen decision rendered beside a row
    of `-`, which reads as "we lost the figures" rather than as "nobody plans here any more".
    """
    with blank_session() as db:
        world = _covered_world(db)
        _decide_line_one(db, world)
        # Taken out of planning AFTER the decision was frozen, which is the live case: a
        # location stops being planned against while decisions already stand at it.
        world["own"].fulfilment_planning = False
        db.flush()

        board = _service(db).build(
            ["ZZT-SO-COVER", "ZZT-SO-AHEAD"], granularity="week", as_of=TODAY
        )

        contribution = _covered_contribution(board, world)
        cell = next(c for c in board["cells"] if contribution in c["contributions"])
        row = next(
            location for location in cell["locations"]
            if location["location"] == world["own"].warehouse_code
        )
        standing = next(
            entry for entry in board["orders"]
            if entry["so_number"] == "ZZT-SO-COVER"
        )
        cell_unplannable = cell["unplannable_count"]
        # The order's OTHER line, at the same bin and never decided: the verdict is still
        # its verdict, which is what makes the covered line's exemption a rule rather than
        # a hole.
        sibling = next(
            c for c in cell["contributions"]
            if c["so_number"] == "ZZT-SO-COVER" and c["qty"] == "21"
        )

    # The decision is still what it says it is, and the row is not blocked.
    assert contribution["covered"] is True
    assert contribution["unplannable"] is False
    assert contribution["decision"]["borrow"][0]["qty"] == "43"
    # The count and the standing agree with it: the undecided sibling, and only it.
    assert sibling["unplannable"] is True
    assert cell_unplannable == 1
    assert standing["unplannable_count"] == 1
    # And its location was READ: every figure is a fact, none of them a dash.
    assert row["qty_on_hand"] == "20"
    assert row["qty_owed_all_orders"] is not None
    assert row["qty_owed_confirmed"] is not None
    assert row["qty_incoming"] is not None


def test_a_deactivated_bin_is_not_told_it_is_outside_fulfilment_planning():
    """The verdict belongs to an ACTIVE bin with the flag off, and to nothing else.

    An inactive warehouse was already outside every read this branch narrowed - they were
    all `is_active` filtered - so it keeps the verdict it carried before the flag existed.
    Handing it `Outside fulfilment planning` instead sends a planner to the Warehouses
    screen to turn on a switch that is already on, when what actually happened is that the
    location was retired.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        retired = _warehouse(db, f"ZZTRET{_uid()[:5]}"[:20])
        retired.is_active = False
        off = _warehouse(db, f"ZZTOFF{_uid()[:5]}"[:20])
        off.fulfilment_planning = False
        db.flush()
        _stock(db, product, retired, on_hand=100)
        _stock(db, product, off, on_hand=100)

        retired_order = _order(db, so_number="ZZT-SO-RETIRED", order_date=date(2026, 1, 1))
        _line(
            db, retired_order, product, qty="10", required_date=date(2026, 9, 3),
            warehouse=retired,
        )
        off_order = _order(db, so_number="ZZT-SO-OFFPLAN", order_date=date(2026, 1, 1))
        _line(
            db, off_order, product, qty="10", required_date=date(2026, 9, 3), warehouse=off,
        )

        board = _service(db).build(
            ["ZZT-SO-RETIRED", "ZZT-SO-OFFPLAN"], granularity="week", as_of=TODAY
        )

        cell = _cell(board, product.product_code, "2026-08-31")
        by_order = {c["so_number"]: c for c in cell["contributions"]}

    on_retired = by_order["ZZT-SO-RETIRED"]
    assert on_retired["unplannable"] is False
    # Nothing at the retired location is drawable, so the line is bought - the same answer
    # it got before the flag existed.
    assert on_retired["qty_proposed_buy"] == "10"
    assert "unplannable" not in {s["kind"] for s in on_retired["sources"]}

    on_off_plan = by_order["ZZT-SO-OFFPLAN"]
    assert on_off_plan["unplannable"] is True
    assert [s["kind"] for s in on_off_plan["sources"]] == ["unplannable"]
    assert on_off_plan["sources"][0]["reason"] == "Outside fulfilment planning"


def test_a_saved_decision_is_attached_to_its_own_contribution_and_to_no_other():
    """S4 (R-F): `build()` stamps the drafts table onto the contribution whose key it was
    saved under, and leaves every other line alone.

    A draft is read by sales order and matched by CORE LINE (C2, code review round 4), so
    the sibling line of the SAME order and the same product - one row away in the same cell
    - is the case that would catch a match made on too little.
    """
    from app.models.base import company_scope
    from app.models.project_so import SOSupplyDecisionDraft

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own, pool = _pooled_warehouses(db)
        _stock(db, product, pool, on_hand=200)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
        _line(db, order, product, qty="20", required_date=date(2026, 9, 10), warehouse=own)
        db.flush()

        with company_scope(db, frozenset({company_id})):
            board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
            mine, sibling = sorted(
                board["contributions"], key=lambda row: row["line_no"]
            )
            db.add(
                SOSupplyDecisionDraft(
                    id=_uid(),
                    sales_order_id=str(order.id),
                    # What the draft IS (C2): the core line, which `_attach_drafts` matches
                    # on. The three below are what it was CALLED when it was saved.
                    core_line_id=mine["line_id"],
                    line_no=mine["line_no"],
                    item_code=mine["item_code"],
                    bucket_key=mine["key"].split("|")[3],
                    decision={"verdict": "approved"},
                    # S1 (code review round 3): the LINE's own facts, not the proposal -
                    # `mine["qty"]` is already `qty_text`-formatted, the same function
                    # `_attach_drafts` recomputes from at read time.
                    line_snapshot={
                        "open_qty": mine["qty"],
                        "required_date": mine["required_date"].isoformat()
                        if mine["required_date"]
                        else None,
                    },
                    saved_by=actor,
                )
            )
            db.flush()

            again = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        saved, untouched = sorted(again["contributions"], key=lambda row: row["line_no"])
        assert saved["key"] == mine["key"]
        assert saved["draft"]["decision"] == {"verdict": "approved"}
        assert saved["draft"]["saved_by"] == f"{MARKER} Eling"
        # Saved against the suggestion it is still being shown beside.
        assert saved["draft"]["stale"] is False
        assert untouched["draft"] is None


# --------------------------------------------------------------------------- #
# C3 (code review round 4) - the proof may not advertise stock the walk has spent
#
# `compose_lines` keeps two ledgers the proof never saw: `share_left`, what is left of each
# site pool's project share in this walk, and the unit's own ownership-group pile. The proof
# recomputed both from scratch, so from the SECOND line of a product or a unit on it offered
# stock an earlier line had already taken - "offered 500" beside "took 0", and the 135 at
# BRW-BB offered to the 1,305 line that could never have had it.
# --------------------------------------------------------------------------- #


def test_the_proof_never_offers_a_pool_share_an_earlier_line_has_spent():
    """C3(a). One pool of 20 spares 10 to project work (R-B), and the first line takes all
    of it; the second line's pool question must not name that pool as offering anything.

    Read through `_pool_why_no_own`, which is the sentence a line at a location with no pool
    of its own gets - it names every pool the chain could still offer from, and it named
    this one over a step that had nothing left to give.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        _own, pool = _pooled_warehouses(db)
        lonely = _warehouse(db, f"ZZTL{_uid()[:6]}"[:20])
        _stock(db, product, lonely, on_hand=0)
        # 20 in the pool, half of it kept for dealers: 10 is the whole project allowance.
        _stock(db, product, pool, on_hand=20)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=lonely)
        _line(db, order, product, qty="10", required_date=date(2026, 9, 10), warehouse=lonely)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        first = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        second = _cell(board, product.product_code, "2026-09-07")["contributions"][0]
        pool_code = pool.warehouse_code

    took = _step(first, "pool")
    assert took["answer"] == "yes" and took["took"] == "10", (
        "sanity: the first line takes the whole project share"
    )
    spent = _step(second, "pool")
    assert spent["answer"] == "no" and spent["took"] == "0"
    assert pool_code not in spent["why"], (
        "the share was spent by the earlier line, so the pool has nothing to offer this one",
        spent["why"],
    )
    assert "has anything to offer" in spent["why"], spent["why"]


def _unit_world(db, *, on_hand: int, first_qty: str, second_qty: str):
    """SO419208's own shape: two lines of one order, one product, one location, one date.

    Ladder v8 (R-E) walks them one at a time, smallest first, so the small line takes the
    floor and the big one buys - and the big one's proof must say the floor is gone. The
    site pool is empty here, so question 2 answers nothing and question 1 is the whole story.
    """
    group = f"Z{_uid()[:3]}".upper()
    product = _product(db, f"ZZT-{_uid()[:6]}")
    pool = _warehouse(db, f"ZZTP{_uid()[:5]}"[:20])
    own = _warehouse(db, f"ZZTA{_uid()[:4]}-{group}"[:20])
    own.pool_warehouse_id = pool.id
    db.flush()
    _stock(db, product, own, on_hand=on_hand)
    _stock(db, product, pool, on_hand=0)
    agent = _agent(db, f"ZZT-AM-{_uid()[:4]}", location_group=group)
    order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
    order.sales_agent_id = agent.id
    db.flush()
    _line(db, order, product, qty=first_qty, required_date=date(2026, 9, 3), warehouse=own)
    _line(db, order, product, qty=second_qty, required_date=date(2026, 9, 3), warehouse=own)
    return product, own, order


def test_the_proof_never_offers_the_units_own_floor_twice():
    """C3(b), AC-2.6's own numbers: 135 free at the bin, lines of 1,305 and 135.

    The 135 line walks first (R-E) and takes the floor; the 1,305 line then buys whole. Its
    question 1 was still recomputing the candidates without the unit's own ledger, so it
    named the bin and offered the same 135 the line before it had just taken - the one place
    a planner reading that sentence would go and find nothing.
    """
    with blank_session() as db:
        product, own, order = _unit_world(
            db, on_hand=135, first_qty="1305", second_qty="135"
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, product.product_code, "2026-08-31")
        small = next(c for c in cell["contributions"] if c["qty"] == "135")
        big = next(c for c in cell["contributions"] if c["qty"] == "1305")
        own_code = own.warehouse_code

    assert [(s["kind"], s["qty"], s["location"]) for s in small["sources"]] == [
        ("reserve", "135", own_code)
    ], "sanity: the small line takes the floor (R-E walks it first)"
    assert [(s["kind"], s["qty"]) for s in big["sources"]] == [("buy", "1305")]
    spent = _step(big, "own")
    assert spent["answer"] == "no" and spent["took"] == "0"
    assert own_code not in spent["why"], (
        "the floor went to the line before it, so question 1 has no location to name",
        spent["why"],
    )
    assert spent["note"] is None, (
        "and the row's own note must not list a location the walk has emptied"
    )


# --------------------------------------------------------------------------- #
# D1 (captain, 3 Sep, on SO381895 / B2155-NL-BLUE) - one five-pool net, two readers
#
# The cell's site pool subtotal read Available 142 / Available for Project 71 while the
# EXPANDED ledger under it read Available for Project 0 on every row. The ledger's cap is
# `stock-detail`'s `five_pool_net`, which is a different reader from the one the board gave
# the cell - and R-K says the two never disagree.
# --------------------------------------------------------------------------- #


def _pool_ledger_world(db):
    """One product, one site pool holding 152, and a DEALER order at the pool wanted long
    after the board's own window.

    The dealer line is the point: it is netted by the whole book and by the board alike (the
    netting is not windowed), so a disagreement between the two readers cannot be blamed on
    the window - and its 10 is what makes the pool's net a number worth stating.
    """
    product = _product(db, f"ZZT-{_uid()[:6]}")
    own, pool = _pooled_warehouses(db)
    _stock(db, product, own, on_hand=0)
    _stock(db, product, pool, on_hand=152)
    dealer = _order(
        db, so_number=f"ZZT-SO-DLR{_uid()[:5]}", order_date=date(2026, 1, 1),
        demand_class="dealer",
    )
    _line(db, dealer, product, qty="10", required_date=date(2027, 6, 1), warehouse=pool)
    order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
    _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)
    return product, own, pool, order


def test_the_stock_drill_states_the_same_five_pool_net_the_cell_did():
    """D1. `stock_detail` read the net through `supply.netting()` on a service that had been
    asked about no product at all, so the pile span was empty and every pool netted 0 - the
    ledger then capped "Available for Project" at 0 on every row while the subtotal beside it
    printed 71 off the same pile.

    The board's own reader is spanned by the products the request is about; this one has to
    be spanned by the product it was opened for.
    """
    from app.services.scm.front_planning_engine import available_for_project

    with blank_session() as db:
        product, _own, pool, order = _pool_ledger_world(db)

        service = _service(db)
        board = service.build([order.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, "2026-08-31")
        subtotal = next(
            row for row in cell["locations"] if row["location"] == pool.warehouse_code
        )
        detail = _service(db).stock_detail(
            str(product.id), None, group="pools",
            line_ids=[cell["contributions"][0]["line_id"]],
        )
        share = board["pool_share_pct"]

    # 152 on hand at the pool, 10 owed there by the dealer order: the pile nets 142.
    assert subtotal["net"] == "142"
    assert detail["five_pool_net"] == subtotal["net"], (
        "the ledger's cap and the subtotal beside it are one number",
        detail["five_pool_net"],
    )
    assert detail["pool_share_pct"] == share == 50
    # And the cap actually admits a share, which is the symptom: the ledger's first row
    # (the pool's on hand) reads 76, not 0.
    assert available_for_project(
        Decimal("152"), Decimal(detail["five_pool_net"]), share
    ) == Decimal("76")
    # The row the BOARD computed was right all along, and still is.
    assert subtotal["available_for_project"] == "71"


def test_the_pools_drill_lists_every_site_pool_even_for_an_agent_with_no_group():
    """D6 (captain, 3 Sep, SO374906, agent LEENA). The line's agent carries no location
    group at all, and the expanded pools ledger came back with nothing in it - no on-hand
    row, no documents - under a subtotal reading On hand 77.

    The set a `group=pools` drill covers is the FIVE SITE POOLS, which is a fact about the
    warehouses and not about who sold the line: the same rows the board's own site pool
    section is built from. So every pool is listed, an empty one included, and the pool
    holding the stock states it.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        # Two site pools exist; only one holds anything, and the asking line's own bin is
        # ungrouped - the shape an agent with no location group leaves behind.
        _own1, brw = _pooled_warehouses(db)
        _own2, empty_pool = _pooled_warehouses(db)
        lonely = _warehouse(db, f"ZZTL{_uid()[:6]}"[:20])
        _stock(db, product, lonely, on_hand=0)
        _stock(db, product, brw, on_hand=77)
        _stock(db, product, empty_pool, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=lonely)

        service = _service(db)
        board = service.build([order.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, "2026-08-31")
        subtotal = next(
            row for row in cell["locations"] if row["location"] == brw.warehouse_code
        )
        detail = _service(db).stock_detail(
            str(product.id), None, group="pools",
            line_ids=[cell["contributions"][0]["line_id"]],
        )
        codes = {row["location"] for row in detail["bins"]}
        brw_code, empty_code = brw.warehouse_code, empty_pool.warehouse_code

    assert {brw_code, empty_code} <= codes, (
        "the drill covers the SITE POOLS, whatever group the asking line's agent carries",
        codes,
    )
    assert detail["qty_on_hand"] == "77", "and the pool holding the stock states it"
    assert next(row for row in detail["bins"] if row["location"] == brw_code)[
        "qty_on_hand"
    ] == "77"
    assert next(row for row in detail["bins"] if row["location"] == empty_code)[
        "qty_on_hand"
    ] == "0", "a pool holding nothing is LISTED reading 0, never left out"
    assert detail["five_pool_net"] == subtotal["net"] == "77"
    assert detail["available_qty"] == "77"


# --------------------------------------------------------------------------- #
# S2 - the board plans what NOBODY DECIDED, delivered or not
#
# `PLAN-fulfilment-board-plans-delivered-lines.md` S2, UAC AC-S2-1 to AC-S2-13. Written
# TEST-FIRST: `plan_qty()` / `is_undecided_demand()` in `app.services.scm.demand`,
# `plan_qty_of()` in `project_supply_service`, and `_Row.inquiry_decided` do not exist yet.
#
# The rule in one line: A LINE IS PLANNED WHEN NOBODY HAS DECIDED IT. DELIVERY IS NOT A
# DECISION. Decided means an active decision on the core line OR a live order inquiry row on
# it (state not `cancelled`, ack_state not `rejected`) - the migrated book is the decision on
# the buying side (#875).
# --------------------------------------------------------------------------- #


def _mirror(db, order, core_lines, *, status=None):
    """The planning record and its mirror lines for `order`, seeded by hand.

    BY HAND, deliberately. `ProjectSOAdoptionService.adopt` refuses a sales order whose status
    is not `open` and mirrors only `is_open_demand()` lines - so the very lines this slice puts
    on the board (delivered, closed, on a completed order) get no mirror from it, and a
    contribution with no `project_line_id` has nothing for `confirm` to name. That gap is the
    adoption service's to answer, not this test's; what is seeded here is the shape a board
    confirmation needs, so the assertions below are about the board and the confirm rules
    rather than about how the record came to exist.
    """
    from app.models.project_so import (
        SO_STATUS_ADOPTED,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
    )

    company_id = _sorento(db)
    record = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=None,
        provisional_ref=order.so_number,
        autocount_doc_no=order.so_number,
        so_id=order.id,
        status=status or SO_STATUS_ADOPTED,
        grouping_origin="area",
    )
    db.add(record)
    db.flush()
    mirrors = []
    for index, core_line in enumerate(core_lines, start=1):
        row = ProjectSalesOrderLine(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=record.id,
            line_no=index,
            product_id=core_line.product_id,
            description=f"{MARKER} mirror {index}",
            qty=core_line.qty_ordered,
            uom="UNIT",
            unit_price=Decimal("100.00"),
            amount=Decimal("100.00"),
            delivery_date=core_line.required_date,
            core_sales_order_line_id=core_line.id,
        )
        db.add(row)
        mirrors.append(row)
    db.flush()
    return record, mirrors


def _inquiry_row(db, mirror_line, *, state, ack_state, inquiry_no=None, rejected_at=None):
    """What purchasing was already TOLD about this line - the migrated book's own row (#875).

    Returns the inquiry number, which is what the board has to name on the decided row.
    """
    from app.models.project_so import (
        ACK_AWAITING,
        IV_ORDER,
        OrderInquiry,
        OrderInquiryRow,
    )

    company_id = _sorento(db)
    number = inquiry_no or f"ZZT-OI-{_uid()[:8]}"
    inquiry = OrderInquiry(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=mirror_line.project_sales_order_id,
        state="raised",
        inquiry_no=number,
    )
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        id=_uid(),
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        so_line_id=mirror_line.id,
        item_code=f"{MARKER}-ITEM",
        qty=mirror_line.qty,
        verb=IV_ORDER,
        state=state,
        ack_state=ack_state or ACK_AWAITING,
    )
    if rejected_at is not None:
        row.rejected_at = rejected_at
    db.add(row)
    db.flush()
    return number


def _confirm_composition(db, record, lines, actor):
    """Post a composition the way the board's Confirm does, through the service itself."""
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
    from app.services.project_supply_service import ProjectSupplyService

    return ProjectSupplyService(db).confirm(
        record,
        ConfirmSupplyBody(lines=[ConfirmLine(**line) for line in lines]),
        actor_user_id=actor,
    )


def _free_at(db, product, warehouse) -> Decimal:
    """Free stock as the supply service computes it, off a FRESH service - the per-request
    caches on an instance that has already answered would hand back the pre-confirm figure."""
    from app.services.project_supply_service import ProjectSupplyService

    free = ProjectSupplyService(db)._free_stock([str(product.id)], exclude_line_ids=None)
    return free.get((str(product.id), str(warehouse.id)), _ZERO_DEC)


_ZERO_DEC = Decimal("0")


# --------------------------------------------------------------------------- AC-S2-1


def test_closed_order_delivered_undecided_line_is_admitted_at_ordered_qty():
    """SO421404's own shape: Completed, 3 ordered, 3 delivered, 0 outstanding, no plan ever
    made. The stock left the bin with nothing behind it, so the units are owed back."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=0)
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1),
            status="closed",
        )
        _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="3", line_status="closed",
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        cell = _cell(board, product.product_code, "2026-08-31")
        assert len(cell["contributions"]) == 1
        contribution = cell["contributions"][0]
        assert contribution["qty"] == "3", "the PLAN quantity, not the 0 still owed"
        assert contribution["qty_ordered"] == "3"
        assert contribution["qty_delivered"] == "3"
        assert contribution["covered"] is False
        # The ladder walked it: a proposal, not a read-only row.
        assert contribution["sources"], "an undecided line is proposed for"


# --------------------------------------------------------------------------- AC-S2-2


def test_delivered_undecided_line_with_empty_bin_proposes_buy():
    """The delivery emptied the bin, so there is nothing to source from: Buy the whole 3, and
    the confirmation raises the ORDER row that tells purchasing to put them back."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        _stock(db, product, pool, on_hand=0)
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1),
            status="closed",
        )
        _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="3", line_status="closed",
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert [(s["kind"], s["qty"]) for s in contribution["sources"]] == [("buy", "3")]
        assert contribution["qty_proposed_buy"] == "3"
        assert board["line_count"] == 1


# --------------------------------------------------------------------------- AC-S2-3


def test_delivered_undecided_line_with_stock_uses_own_location_and_holds_nothing():
    """The location still holds the units, so the board sources from it - and the hold that
    confirmation writes counts as ZERO, because the line owes nothing (AC-S1-2).

    "Own location" here is the line's own supply chain, which since ladder v2 rule 7 means its
    SITE POOL rather than the bin itself - the same shape every other Reserve case in this file
    is built on. The point of the criterion is the hold, not which rung found the stock.
    """
    from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision

    with blank_session() as db:
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        _stock(db, product, pool, on_hand=10)
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1),
            status="closed",
        )
        core_line = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="3", line_status="closed",
        )
        record, mirrors = _mirror(db, order, [core_line])
        db.commit()

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert [(s["kind"], s["qty"]) for s in contribution["sources"]] == [("reserve", "3")]

        assert _free_at(db, product, pool) == Decimal("10")
        _confirm_composition(
            db, record,
            [{
                "project_line_id": str(mirrors[0].id),
                "reserve": [{"warehouse_id": str(pool.id), "qty": "3"}],
            }],
            actor,
        )
        db.commit()

        assert (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == record.id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .count()
            == 1
        )
        assert _free_at(db, product, pool) == Decimal("10"), (
            "the units already shipped, so the confirmed allocation holds nothing"
        )


# --------------------------------------------------------------------------- AC-S2-4


def test_partial_delivered_undecided_line_asks_full_qty_and_holds_only_owed():
    """3 ordered, 1 delivered: the board plans the WHOLE 3 - one still to ship, one to put
    back - while the hold a confirmation writes is capped at the 2 still owed.

    So a Reserve of 1 on this line takes 1 out of free stock for everybody else, not 0 and
    not 3.
    """
    with blank_session() as db:
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        _stock(db, product, pool, on_hand=10)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core_line = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="1",
        )
        record, mirrors = _mirror(db, order, [core_line])
        db.commit()

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["qty"] == "3", "the plan quantity, not the 2 still owed"
        assert contribution["qty_ordered"] == "3"
        assert contribution["qty_delivered"] == "1"

        _confirm_composition(
            db, record,
            [{
                "project_line_id": str(mirrors[0].id),
                "reserve": [{"warehouse_id": str(pool.id), "qty": "1"}],
                "buy_qty": "2",
                "buy_reason": "ZZT put the delivered unit back",
                "amend_reason": "ZZT one still to ship, one to put back",
            }],
            actor,
        )
        db.commit()

        assert _free_at(db, product, pool) == Decimal("9"), (
            "min(reserve 1, owed 2) = 1 comes out of free stock, and nothing more"
        )


# --------------------------------------------------------------------------- AC-S2-5


def test_covered_line_is_not_admitted():
    """`purchasing_status = covered` is a person saying "no purchase needed". That is a
    decision, and the board does not re-open it."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1),
            status="closed",
        )
        _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="3", line_status="closed",
            purchasing_status="covered",
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        assert board["line_count"] == 0
        assert board["cells"] == []


# --------------------------------------------------------------------------- AC-S2-6


def test_cancelled_line_and_cancelled_order_are_not_admitted():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        live = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(
            db, live, product, qty="9", required_date=date(2026, 9, 3),
            warehouse=warehouse, line_status="cancelled", delivered="4",
        )
        dead = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1),
            status="cancelled",
        )
        _line(db, dead, product, qty="5", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _service(db).build(
            [live.so_number, dead.so_number], granularity="week", as_of=TODAY
        )

        assert board["line_count"] == 0, "a cancelled line owes nothing; a cancelled order too"
        assert board["cells"] == []


# --------------------------------------------------------------------------- AC-S2-7


def test_live_inquiry_row_reads_covered_without_decision():
    """The migrated book already told purchasing to buy for this line (#875), so it is DECIDED
    on the buying side: read-only, naming the inquiry, no composition, never proposed for.

    `covered` true with `decision` null is the whole shape - the board has no frozen
    composition to print, because the instruction predates any board.
    """
    from app.models.project_so import ACK_ACKNOWLEDGED, INQUIRY_RAISED

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        _stock(db, product, pool, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core_line = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3), warehouse=warehouse
        )
        _record, mirrors = _mirror(db, order, [core_line])
        number = _inquiry_row(
            db, mirrors[0], state=INQUIRY_RAISED, ack_state=ACK_ACKNOWLEDGED
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["covered"] is True, "a live inquiry row decides the line"
        assert contribution["decision"] is None, "there is no board composition to print"
        assert contribution["order_inquiry"]["inquiry_no"] == number
        assert contribution["proposed"] is None
        assert contribution["sources"] == []
        assert contribution["trail"] == []
        assert contribution["qty_proposed_buy"] == "0"
        assert contribution["qty_proposed_reserve"] == "0"
        # Not in the pile's queue either: a decided line does not stand in the ranking that
        # decides who gets the last unit, and `null` is how the board says so.
        assert contribution["so_qty_ahead"] is None


def test_the_order_inquiry_dict_carries_its_documents_and_whether_it_was_redirected():
    """AC-RL-07 (`PLAN-oi-replan-received-links.md`): the board contribution's `order_
    inquiry` dict names what is BEHIND the instruction, not only its number - the
    line's live row's own documents (`{document, kind, received}`) and whether that
    row was itself redirected off a document that had already landed (AC-RL-10),
    the exact shape S2's replan writes."""
    from app.models.procurement import SPOAllocation
    from app.models.project_so import (
        ACK_ACKNOWLEDGED,
        INQUIRY_PARTLY_LINKED,
        IV_ORDER,
        OrderInquiry,
        OrderInquiryLink,
        OrderInquiryRow,
    )

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core_line = _line(
            db, order, product, qty="182", required_date=date(2026, 9, 3), warehouse=warehouse
        )
        _record, mirrors = _mirror(db, order, [core_line])

        company_id = _sorento(db)
        inquiry = OrderInquiry(
            id=_uid(), company_id=company_id,
            project_sales_order_id=mirrors[0].project_sales_order_id,
            state="raised", inquiry_no=f"ZZT-OI-{_uid()[:8]}",
        )
        db.add(inquiry)
        db.flush()
        # A CLOSED, fully-received allocation - the AC-RL-10 shape a redirected row's
        # own history link stands on.
        allocation = SPOAllocation(
            id=_uid(), company_id=company_id, spo_number=f"ZZT-SPO-{_uid()[:8]}",
            spo_line_number=1, product_id=product.id, warehouse_id=warehouse.id,
            allocated_quantity=158, quantity_received=158, receipt_status="fully_received",
            line_status="closed",
        )
        db.add(allocation)
        db.flush()
        row = OrderInquiryRow(
            id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id,
            so_line_id=mirrors[0].id, item_code=f"{MARKER}-ITEM", qty=Decimal("182"),
            verb=IV_ORDER, state=INQUIRY_PARTLY_LINKED, ack_state=ACK_ACKNOWLEDGED,
            redirected_to_pool=True,
        )
        db.add(row)
        db.flush()
        db.add(OrderInquiryLink(
            id=_uid(), company_id=company_id, row_id=row.id,
            spo_allocation_id=allocation.id, document=allocation.spo_number,
            qty=Decimal("158"),
        ))
        db.flush()
        db.commit()

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        order_inquiry = contribution["order_inquiry"]
        assert order_inquiry["inquiry_no"] == inquiry.inquiry_no
        assert order_inquiry["documents"] == [
            {"document": allocation.spo_number, "kind": "spo", "received": True},
        ]
        assert order_inquiry["redirected"] is True


def test_the_order_inquiry_dict_reaches_the_wire_with_its_inquiry_and_row_ids():
    """S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-15/AC-B6-9): the List view's own
    OI column links to `/project-sales/order-inquiries/<inquiry_id>?row=<row_id>`, so
    the contribution has to carry BOTH ids - and carry them to the WIRE, which is why
    this is asserted off the route rather than off `build()`: `response_model` silently
    drops any field the service returns that `BoardLineOrderInquiry` does not declare,
    and the cell would then fall back to plain text with nothing failing."""
    from app.models.base import company_scope
    from app.models.project_so import (
        ACK_ACKNOWLEDGED,
        INQUIRY_RAISED,
        IV_ORDER,
        OrderInquiry,
        OrderInquiryRow,
    )

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        _stock(db, product, pool, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core_line = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3), warehouse=warehouse
        )
        _record, mirrors = _mirror(db, order, [core_line])
        inquiry = OrderInquiry(
            id=_uid(), company_id=company_id,
            project_sales_order_id=mirrors[0].project_sales_order_id,
            state="raised", inquiry_no=f"ZZT-OI-{_uid()[:8]}",
        )
        db.add(inquiry)
        db.flush()
        row = OrderInquiryRow(
            id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id,
            so_line_id=mirrors[0].id, item_code=f"{MARKER}-ITEM", qty=Decimal("3"),
            verb=IV_ORDER, state=INQUIRY_RAISED, ack_state=ACK_ACKNOWLEDGED,
        )
        db.add(row)
        db.flush()
        db.commit()

        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={
                        "orders": order.so_number,
                        "granularity": "week",
                        "as_of": TODAY.isoformat(),
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        order_inquiry = response.json()["contributions"][0]["order_inquiry"]
        assert order_inquiry["inquiry_no"] == inquiry.inquiry_no
        assert order_inquiry["inquiry_id"] == str(inquiry.id), order_inquiry
        assert order_inquiry["row_id"] == str(row.id), order_inquiry


# --------------------------------------------------------------------------- AC-S2-8


def test_cancelled_or_rejected_inquiry_row_leaves_line_undecided():
    """A row that went away, and a row purchasing REFUSED that CS has not answered, are both
    the absence of an instruction. The line is undecided, and the board proposes for it."""
    from datetime import datetime

    from app.models.project_so import (
        ACK_AWAITING,
        ACK_REJECTED,
        INQUIRY_CANCELLED,
        INQUIRY_RAISED,
    )

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        _stock(db, product, pool, on_hand=0)

        withdrawn = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        withdrawn_line = _line(
            db, withdrawn, product, qty="3", required_date=date(2026, 9, 3), warehouse=warehouse
        )
        _record_a, mirrors_a = _mirror(db, withdrawn, [withdrawn_line])
        _inquiry_row(db, mirrors_a[0], state=INQUIRY_CANCELLED, ack_state=ACK_AWAITING)

        refused = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        refused_line = _line(
            db, refused, product, qty="4", required_date=date(2026, 9, 3), warehouse=warehouse
        )
        _record_b, mirrors_b = _mirror(db, refused, [refused_line])
        _inquiry_row(
            db, mirrors_b[0], state=INQUIRY_RAISED, ack_state=ACK_REJECTED,
            rejected_at=datetime(2026, 8, 1, 9, 0),
        )

        board = _service(db).build(
            [withdrawn.so_number, refused.so_number], granularity="week", as_of=TODAY
        )

        by_order = {
            c["so_number"]: c
            for c in _cell(board, product.product_code, "2026-08-31")["contributions"]
        }
        assert by_order[withdrawn.so_number]["covered"] is False
        assert by_order[withdrawn.so_number]["sources"], "a withdrawn row decides nothing"
        assert by_order[refused.so_number]["covered"] is False
        assert by_order[refused.so_number]["sources"], (
            "purchasing refusing it puts the line back in CS's hands, undecided"
        )


# --------------------------------------------------------------------------- AC-S2-9


def test_confirm_sum_check_compares_to_plan_qty():
    """The balance check is against the PLAN quantity now, so a delivered line can be
    confirmed at all - and the refusal, when the composition does not add up, names that
    figure rather than the zero still owed."""
    from app.services.project_supply_service import SupplyLinesRefused

    with blank_session() as db:
        actor = _user(db, f"{MARKER} Eling")
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        _stock(db, product, pool, on_hand=0)
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1),
            status="closed",
        )
        core_line = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="3", line_status="closed",
        )
        record, mirrors = _mirror(db, order, [core_line])
        db.commit()

        # Nothing at all is not a composition for a line that plans 3, and the sentence has
        # to say 3 or the planner is told to balance against a number nobody can see.
        with pytest.raises(SupplyLinesRefused) as refusal:
            _confirm_composition(
                db, record, [{"project_line_id": str(mirrors[0].id)}], actor
            )
        reasons = " ".join(
            entry["reason"] for entry in (refusal.value.failing_lines or [])
        )
        assert "3" in reasons, reasons
        db.rollback()

        # And the whole 3 IS confirmable: this is the Buy that raises the order-back row.
        result = _confirm_composition(
            db, record,
            [{
                "project_line_id": str(mirrors[0].id),
                "buy_qty": "3",
                "buy_reason": "ZZT the delivery emptied the bin",
            }],
            actor,
        )
        assert result["revision_no"] == 1


# --------------------------------------------------------------------------- AC-S2-10


def test_frozen_decision_on_undelivered_line_is_not_drifted():
    """A GUARD, not a red test. Every decided line on the 0907 copy has nothing delivered (97
    of 97, measured 14 Sep), so moving `_LineFacts.open_qty` from the still-owed figure to the
    plan quantity must not make a single existing revision look stale."""
    from app.services.project_supply_service import ProjectSupplyService

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse, _pool = _pooled_warehouses(db)
        _stock(db, product, warehouse, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core_line = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3), warehouse=warehouse
        )
        record, mirrors = _mirror(db, order, [core_line])
        db.commit()

        facts = ProjectSupplyService(db)._facts_for(record, mirrors)
        fact = facts[str(mirrors[0].id)]
        snapshot = {
            "core_line_id": str(core_line.id),
            "open_qty": "3",
            "required_date": core_line.required_date.isoformat(),
        }

        assert fact.open_qty == Decimal("3")
        assert (
            ProjectSupplyService._carry_snapshot_has_drifted(mirrors[0], snapshot, fact)
            is False
        )


# --------------------------------------------------------------------------- AC-S2-11


def test_netting_predicate_still_excludes_delivered_lines():
    """A GUARD. `is_open_demand()` and `demand_qty()` are the NETTING predicates, and the
    netting engine, the reorder plan and the worklist go on asking "what is still owed". Only
    the board's own predicate changed."""
    from app.services.scm.demand import demand_qty, is_open_demand

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        owed = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3), warehouse=warehouse
        )
        shipped = _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="3",
        )

        still_owed = {
            str(row.id)
            for row in db.query(SalesOrderLine)
            .filter(SalesOrderLine.sales_order_id == order.id, is_open_demand())
            .all()
        }
        assert still_owed == {str(owed.id)}

        qty = (
            db.query(demand_qty())
            .filter(SalesOrderLine.id == shipped.id)
            .scalar()
        )
        assert Decimal(str(qty)) == Decimal("0")


# --------------------------------------------------------------------------- AC-S2-13


def test_selection_of_only_cancelled_or_covered_lines_is_an_empty_board():
    """The one board that is genuinely empty, and the only one the "No lines to plan on these
    sales orders" state belongs to (AC-S3-1). `line_count` is what the copy is chosen off, so
    it has to be 0 here and nonzero on a day window that merely shows nothing."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1),
            status="closed",
        )
        _line(
            db, order, product, qty="3", required_date=date(2026, 9, 3),
            warehouse=warehouse, delivered="3", line_status="cancelled",
        )
        _line(
            db, order, product, qty="4", required_date=date(2026, 9, 3),
            warehouse=warehouse, purchasing_status="covered",
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        assert board["line_count"] == 0
        assert board["cells"] == []


def test_a_delivered_line_does_not_inflate_its_groups_offer():
    """Review round 1, S3. `_group_offer` un-nets THIS line's own quantity because the group
    net has already subtracted it - and what the net subtracted is `demand_qty()`, which is
    what is STILL OWED.

    So the figure added back has to be the owed one. This line is 24 ordered and 24
    delivered, so the net never counted it at all and the group reads a clean 9 (10 on hand
    against an earlier order's 1). Un-netting the PLAN quantity would add a free 24 on top:
    `max(9 + 24, 0)` = 33 offered where the group holds 10.

    Asserted on `group_offer` itself, because that is the number the mistake changes - a
    proposal read off it can come out the same by other constraints and hide the defect.
    """
    from app.services.project_supply_service import ProjectSupplyService

    with blank_session() as db:
        _group, product, own, mine = _group_world(
            db, on_hand=10, other_qty=1, own_qty="24"
        )
        line = (
            db.query(SalesOrderLine)
            .filter(SalesOrderLine.sales_order_id == mine.id)
            .one()
        )
        line.qty_delivered = Decimal("24")
        line.line_status = "closed"
        db.flush()
        record, mirrors = _mirror(db, mine, [line])
        db.commit()

        fact = ProjectSupplyService(db)._facts_for(record, mirrors)[str(mirrors[0].id)]

        assert fact.open_qty == Decimal("24"), "it is still PLANNED at its ordered quantity"
        assert fact.owed_qty == Decimal("0"), "and owed nothing, because it all shipped"
        assert fact.group_net == Decimal("9"), (
            "10 on hand against the other order's 1; this line weighs nothing"
        )
        assert fact.group_offer == Decimal("9"), (
            "the group offers what it holds; un-netting the plan quantity offered 33"
        )
        assert str(own.warehouse_code).endswith(fact.group_code or "")


def _board_facts(service, so_numbers):
    """The `_LineFacts` the board actually built, captured off the call it makes.

    Through the real `build()` and the real payload, because the defect N1 names lives in
    the WIRING between the two: a fact constructed here by hand would carry every field the
    board forgot to send and prove nothing.
    """
    from unittest.mock import patch

    from app.services.project_supply_service import ProjectSupplyService

    seen = {}
    original = ProjectSupplyService.demand_facts

    def spy(self, rows, **kw):
        built = original(self, rows, **kw)
        seen["rows"] = rows
        seen["facts"] = built
        return built

    with patch.object(ProjectSupplyService, "demand_facts", spy):
        service.build(list(so_numbers), granularity="week", as_of=TODAY)
    return seen["rows"], seen["facts"]


def test_the_board_un_nets_each_lines_own_owed_quantity_from_its_group():
    """Review round 2, N1. The `owed_qty` fix was INERT on the board.

    `_group_offer` adds this line's own demand back because the group net already subtracted
    it, and review round 1 moved that un-net from `open_qty` to `owed_qty`. The board builds
    its facts through `demand_facts`, which carried no `owed_qty` at all - so every board row
    un-netted ZERO and the offer collapsed to the raw group net, offering a group's own line
    nothing it could actually have.

    `_quantity_world` is 28 ordered and 8 delivered: 20 owed, a group netting 17, and an
    offer of 17 + 20 = 37.
    """
    with blank_session() as db:
        order, _product, _warehouse = _quantity_world(db)

        service = _service(db)
        rows, facts = _board_facts(service, [order.so_number])
        fact = next(iter(facts.values()))

        assert "owed_qty" in rows[0], "the board has to SEND it, not only read it"
        assert fact.open_qty == Decimal("28"), "the line asks for its ordered quantity"
        assert fact.owed_qty == Decimal("20"), (
            "and owes 20 - the figure the netting engine actually subtracted"
        )
        assert fact.group_net == Decimal("17")
        assert fact.group_offer == Decimal("37"), (
            "the group's net plus this line's own owed quantity; 17 was the defect"
        )


def test_a_board_group_offer_covers_a_line_the_group_can_actually_meet():
    """Review round 2, N1, on the ownership-group world: 10 on hand, 1 owed by an earlier
    order, 9 asked for here and all 9 still owed.

    The group nets 0 - its whole 10 is spoken for once this line's own 9 is counted - and
    offers this line 9 once its own demand is added back. Un-netting nothing left the offer
    at 0, which says a group holding 10 can give one of its own lines nothing.
    """
    with blank_session() as db:
        _group, _product, own, mine = _group_world(
            db, on_hand=10, other_qty=1, own_qty="9"
        )

        service = _service(db)
        _rows, facts = _board_facts(service, [mine.so_number])
        fact = next(iter(facts.values()))

        assert fact.owed_qty == Decimal("9")
        assert fact.group_net == Decimal("0")
        assert fact.group_offer == Decimal("9")
        assert str(own.warehouse_code).endswith(fact.group_code or "")
