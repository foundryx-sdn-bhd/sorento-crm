"""The Stock Debt view over the wire (S2, AC-S2-6 / AC-S2-7 / AC-S2-8).

    GET /api/v1/project-sales/stock-debt
    GET /api/v1/project-sales/stock-debt/{product_id}/cell?month=

`test_supply_assignment.py` owns the arithmetic; nothing is re-derived here. What is proved
is what the WIRE has to carry, and every field is asserted BY NAME because a `response_model`
silently drops what it does not declare - a field the service computes and the schema forgets
reaches the screen as `undefined`, which reads as a backend that has no answer.

Every test narrows with `query=`, and not only for speed: the shared database is a prod copy
with a thousand products in fulfilment planning, so a test that read the whole book would
assert against somebody else's data. The seeded chain is the only thing in the answer.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.scm import priority
from app.services.scm.supply_assignment import month_key
from tests.scm.conftest import (
    SORENTO_COMPANY_ID,
    _REF_CATEGORY_CODE,
    _REF_PRODUCT_CODE,
    as_user,
    ensure_reference_data,
    requires_pg,
    seed_user,
)

pytestmark = requires_pg

BASE = "/api/v1/project-sales/stock-debt"
VIEW = "projects.stock_debt.view"

TODAY = date.today()


def _u() -> str:
    return str(uuid.uuid4())


def _months_ahead(count: int) -> date:
    """The 15th, `count` months out - a date that cannot slip into a neighbouring month
    however late in the month the suite runs."""
    month = TODAY.month - 1 + count
    return date(TODAY.year + month // 12, month % 12 + 1, 15)


def _client(scm_app, *, permission: str | None = VIEW):
    app, db, gcu, gcuak = scm_app
    ensure_reference_data(db)
    uid = seed_user(db, None)
    if permission:
        role_id = _u()
        db.execute(
            text(
                "INSERT INTO user_roles (id, slug, name, is_trashed, is_protected, "
                "is_default, created_at) VALUES (:id, :slug, 'ZZT stock debt', false, "
                "false, false, now())"
            ),
            {"id": role_id, "slug": f"zzt-stock-debt-{role_id[:8]}"},
        )
        permission_id = db.execute(
            text("SELECT id FROM user_permissions WHERE slug = :s"), {"s": permission}
        ).scalar()
        assert permission_id, f"{permission} must exist - migration 443 seeds it"
        db.execute(
            text(
                "INSERT INTO user_role_permissions (id, role_id, permission_id, "
                "assigned_at) VALUES (:id, :r, :p, now())"
            ),
            {"id": _u(), "r": role_id, "p": permission_id},
        )
        from app.models.user import UserRoleAssignment

        db.add(UserRoleAssignment(user_id=uid, role_id=role_id))
        db.flush()
    as_user(app, gcu, gcuak, uid)
    return app, db


# --------------------------------------------------------------------------- seeding


def _product(db, code: str):
    from app.models.product import Product

    # Borrow from the reference product `ensure_reference_data` seeded in THIS test's
    # savepoint, never from any product. An unordered LIMIT 1 over `products` can pick a
    # row another xdist worker committed (test_spo_conversion, the outstanding import
    # batch tests) and deletes with its category before this insert lands, which fails
    # here as products_category_id_fkey.
    category_id, uom_id = db.execute(
        text("SELECT category_id, base_uom_id FROM products WHERE product_code = :code"),
        {"code": _REF_PRODUCT_CODE},
    ).one()
    row = Product(
        id=_u(),
        product_code=code,
        product_name=f"{code} basin",
        category_id=category_id,
        base_uom_id=uom_id,
        list_price=Decimal("10.00"),
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(db, code: str, *, planning: bool = True):
    from app.models.inventory import Warehouse

    row = Warehouse(
        id=_u(),
        warehouse_code=code,
        warehouse_name=code,
        location="ZZT",
        is_active=True,
        fulfilment_planning=planning,
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _stock(db, product, warehouse, qty):
    from app.models.inventory import Stock

    db.add(
        Stock(
            id=_u(),
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity_on_hand=Decimal(str(qty)),
            quantity_reserved=0,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    db.flush()


def _agent(db, code: str):
    from app.models.sales_agent import SalesAgent

    row = SalesAgent(id=_u(), sales_agent=code, source="manual", is_active=True)
    db.add(row)
    db.flush()
    return row


def _demand(
    db, product, warehouse, *, qty, required_date, so_number, agent=None,
    qty_delivered=0, qty_required=None,
):
    from app.models.order import SalesOrder, SalesOrderLine

    order = SalesOrder(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        so_number=so_number,
        status="open",
        demand_class="project",
        sales_agent_id=agent.id if agent is not None else None,
    )
    db.add(order)
    db.flush()
    line = SalesOrderLine(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        sales_order_id=order.id,
        product_id=product.id,
        warehouse_id=warehouse.id,
        qty_ordered=Decimal(str(qty)),
        qty_delivered=Decimal(str(qty_delivered)),
        qty_required=Decimal(str(qty_required)) if qty_required is not None else None,
        required_date=required_date,
        line_status="open",
    )
    db.add(line)
    db.flush()
    return order, line


def _spo(
    db, product, warehouse, *, qty, arrives, spo_number=None, received=0,
    receipt_status="pending", line_status="open",
):
    from app.models.procurement import SPOAllocation

    row = SPOAllocation(
        id=_u(),
        spo_number=spo_number or f"ZZT-SPO-{_u()[:6]}",
        spo_line_number=1,
        product_id=product.id,
        warehouse_id=warehouse.id,
        allocated_quantity=qty,
        quantity_received=received,
        receipt_status=receipt_status,
        line_status=line_status,
        expected_date=arrives,
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _landed_shipment(db, allocation, *, arrived):
    """An inbound shipment that has ARRIVED (`actual_arrival_date` set), booked onto
    `allocation` - the third leg of `spo_supply.open_incoming_clauses()`: a landed shipment
    takes its SPO line out of incoming supply even before a receipt is written."""
    from app.models.procurement import InboundShipment

    shipment = InboundShipment(
        id=_u(),
        shipment_number=f"ZZT-SHP-{_u()[:6]}",
        shipment_date=arrived - timedelta(days=30),
        estimated_arrival_date=arrived,
        actual_arrival_date=arrived,
        shipment_status="arrived",
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(shipment)
    db.flush()
    allocation.inbound_shipment_id = shipment.id
    db.flush()
    return shipment


def _row_of(body: dict, code: str) -> dict:
    rows = [row for row in body["data"] if row["product_code"] == code]
    assert rows, f"{code} is not in {[row['product_code'] for row in body['data']]}"
    return rows[0]


# --------------------------------------------------------------------------- AC-S2-6


def test_the_board_answers_one_row_per_product_with_the_whole_envelope(scm_app):
    """Every field of AC-S2-6, by name. The book: 100 on hand, 30 due next month (covered),
    200 due in three months (short 130 by then), 60 booked at 2030 and 12 with no date."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 100)
    _demand(
        db, product, warehouse, qty=30, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    _demand(
        db, product, warehouse, qty=200, required_date=_months_ahead(3),
        so_number=f"{marker}-SO2",
    )
    _demand(
        db, product, warehouse, qty=60, required_date=date(2030, 1, 1),
        so_number=f"{marker}-SO3",
    )
    _demand(
        db, product, warehouse, qty=12, required_date=None, so_number=f"{marker}-SO4",
    )
    db.flush()

    with TestClient(app) as c:
        got = c.get(BASE, params={"query": marker, "only_debt": False, "limit": 25})
    assert got.status_code == 200, got.text
    body = got.json()

    assert body["pagination"] == {"total": 1, "page": 1, "limit": 25}
    assert body["tba_month"] == "2029-01"
    assert "BB" in body["groups"]
    assert body["months"][0] == month_key(TODAY)
    assert body["months"][-1] == month_key(_months_ahead(3))

    row = _row_of(body, product.product_code)
    assert row["product_id"] == str(product.id)
    assert row["product_name"] == f"{product.product_code} basin"
    assert row["tba"] == -60
    assert row["undated"] == -12
    assert [month["key"] for month in row["months"]] == body["months"]
    balances = {month["key"]: month["balance"] for month in row["months"]}
    # R37, month by month: the 100 on hand is spent (30 to the near line, 70 to the far
    # one), so nothing is FREE in this month or the next, and the only month that owes
    # anything is the one the short line is due in.
    assert balances[month_key(TODAY)] == 0
    assert balances[month_key(_months_ahead(1))] == 0
    assert balances[month_key(_months_ahead(3))] == -130
    tones = {month["key"]: month["tone"] for month in row["months"]}
    assert tones[month_key(TODAY)] == "green"
    assert tones[month_key(_months_ahead(3))] in {"red", "amber"}


def test_only_debt_drops_the_product_that_covers_everything(scm_app):
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    covered = _product(db, f"{marker}-COVERED")
    short = _product(db, f"{marker}-SHORT")
    _stock(db, covered, warehouse, 500)
    _stock(db, short, warehouse, 5)
    for product in (covered, short):
        _demand(
            db, product, warehouse, qty=100, required_date=_months_ahead(2),
            so_number=f"{marker}-{product.product_code}",
        )
    db.flush()

    with TestClient(app) as c:
        every = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        debt = c.get(BASE, params={"query": marker, "only_debt": True}).json()

    assert sorted(row["product_code"] for row in every["data"]) == [
        covered.product_code,
        short.product_code,
    ]
    assert [row["product_code"] for row in debt["data"]] == [short.product_code]
    assert debt["pagination"]["total"] == 1


def test_the_group_filter_recomputes_rather_than_filters(scm_app):
    """`group=BB` narrows the SPAN of every read, so the balance is the BB group's own -
    and that is what makes the narrowing meaningful rather than cosmetic.

    On the WHOLE book the IB stock is IB's, and R40 says an undecided BB line does not take
    it, so the BB line is short its 40 in its own month while IB's 100 reads free in the
    month it sits in. Under `group=BB` the IB bin is not in the span at all, so there is no
    100 to be free of and the current month reads 0. Same rows, two different questions -
    which is precisely what "recomputes" means; filtering finished rows would have shown
    one answer under both.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    ib = _warehouse(db, f"ZZTBRW{_u()[:4]}-IB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, ib, 100)
    _demand(
        db, product, bb, qty=40, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    db.flush()
    due = month_key(_months_ahead(1))

    with TestClient(app) as c:
        whole = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        narrowed = c.get(
            BASE, params={"query": marker, "group": "BB", "only_debt": False}
        ).json()
        whole_cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": due}
        ).json()
        narrowed_cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": due, "group": "BB"}
        ).json()

    def balance(body, key):
        row = _row_of(body, product.product_code)
        return {month["key"]: month["balance"] for month in row["months"]}[key]

    # R37 + R40: on the whole book the whole 100 of IB's on hand stays free (a BB line may
    # not take it) and the BB line owes 40 in its own month. Narrowed to BB the IB bin is
    # out of the span entirely, so the current month has nothing to be free of at all -
    # which is the difference that proves the narrowing RECOMPUTES.
    assert balance(whole, month_key(TODAY)) == 100
    assert balance(whole, due) == -40
    assert balance(narrowed, month_key(TODAY)) == 0
    assert balance(narrowed, due) == -40

    # The reason the cell route takes `group` at all: the drill has to foot with the cell
    # that opened it, and it does under both spans. The SUPPLY half is where the two differ
    # - the whole book lists IB's 100 as free, the narrowed one lists nothing.
    assert whole_cell["demand"][0]["status"] == "short"
    assert whole_cell["demand"][0]["short_qty"] == 40
    assert narrowed_cell["demand"][0]["status"] == "short"
    assert narrowed_cell["demand"][0]["assigned_qty"] == 0


def test_rows_are_sorted_by_the_earliest_red_month_then_product_code(scm_app):
    """AC-S2-6. A product that cannot be bought in time comes first; a product with no red
    month at all sorts after every product that has one, whatever its code."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    # Codes chosen so alphabetical order and the required order disagree.
    late_debt = _product(db, f"{marker}-A-LATE")
    near_debt = _product(db, f"{marker}-B-NEAR")
    _demand(
        db, near_debt, warehouse, qty=10, required_date=TODAY + timedelta(days=3),
        so_number=f"{marker}-SO-NEAR",
    )
    _demand(
        db, late_debt, warehouse, qty=10, required_date=_months_ahead(14),
        so_number=f"{marker}-SO-LATE",
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": True}).json()

    assert [row["product_code"] for row in body["data"]] == [
        near_debt.product_code,
        late_debt.product_code,
    ]


def test_paging_returns_the_next_slice_on_the_same_axis(scm_app):
    """The envelope's axis and total are properties of the whole filtered set, not of a
    page - so a reader turning the page sees the columns hold still while the rows move."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    codes = [f"{marker}-A", f"{marker}-B", f"{marker}-C"]
    for code in codes:
        product = _product(db, code)
        _demand(
            db, product, warehouse, qty=10, required_date=TODAY,
            so_number=f"{marker}-SO-{code[-1]}",
        )
    db.flush()

    with TestClient(app) as c:
        page1 = c.get(BASE, params={"query": marker, "page": 1, "limit": 1}).json()
        page2 = c.get(BASE, params={"query": marker, "page": 2, "limit": 1}).json()

    assert page1["pagination"] == {"total": 3, "page": 1, "limit": 1}
    assert page2["pagination"] == {"total": 3, "page": 2, "limit": 1}
    assert page1["months"] == page2["months"]
    assert len(page1["data"]) == 1 and len(page2["data"]) == 1
    got = [page1["data"][0]["product_code"], page2["data"][0]["product_code"]]
    assert got == codes[:2]


def test_pool_stock_never_covers_a_project_group_line(scm_app):
    """Plan 3.1 / R2: a site pool is reached through `pool_warehouse_id`, is nobody's
    ownership group, and (AC-S1-1) is seeded OUTSIDE fulfilment planning - so a pool
    holding plenty must not silently make a flagged-bin line read covered. The pool's
    stock is not merely a separate group here, it is out of this read's span entirely
    (`_warehouses` filters `fulfilment_planning_predicate()`, which excludes pools by
    construction), which is the strongest form of "never leaks into a project line"."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    from app.models.inventory import Warehouse

    pool = Warehouse(
        id=_u(),
        warehouse_code=f"ZZTPOOL{_u()[:6]}",
        warehouse_name="ZZT pool",
        location="ZZT",
        is_active=True,
        fulfilment_planning=False,
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(pool)
    db.flush()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    bb.pool_warehouse_id = pool.id
    db.flush()
    product = _product(db, f"{marker}-A")
    _stock(db, product, pool, 500)
    _demand(
        db, product, bb, qty=40, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    row = _row_of(body, product.product_code)
    balances = {m["key"]: m["balance"] for m in row["months"]}
    # The pool's 500 never reaches this read: the BB line is short its whole 40, exactly
    # as if the pool did not exist.
    assert balances[month_key(_months_ahead(1))] == -40


def test_only_debt_keeps_a_tba_only_row_and_drops_a_fully_covered_one(scm_app):
    """`_in_debt`: a product whose every MONTH is covered but whose TBA bucket is
    negative still owes something (AC-S2-3 counts a TBA line's whole quantity as debt), so
    it must not disappear from the one screen that lists what is owed; a product with
    nothing negative anywhere drops, as the sibling test already pins for the short case."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    tba_only = _product(db, f"{marker}-TBAONLY")
    fully_covered = _product(db, f"{marker}-COVERED")
    _stock(db, tba_only, warehouse, 100)
    _demand(
        db, tba_only, warehouse, qty=20, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    _demand(
        db, tba_only, warehouse, qty=30, required_date=date(2030, 1, 1),
        so_number=f"{marker}-TBA",
    )
    _stock(db, fully_covered, warehouse, 100)
    _demand(
        db, fully_covered, warehouse, qty=20, required_date=_months_ahead(1),
        so_number=f"{marker}-SO2",
    )
    db.flush()

    with TestClient(app) as c:
        debt = c.get(BASE, params={"query": marker, "only_debt": True}).json()

    codes = [row["product_code"] for row in debt["data"]]
    assert tba_only.product_code in codes
    assert fully_covered.product_code not in codes
    row = _row_of(debt, tba_only.product_code)
    assert all(m["balance"] >= 0 for m in row["months"])
    assert row["tba"] == -30


def test_a_bin_outside_fulfilment_planning_is_not_on_the_board(scm_app):
    """R17: the flag is the whole test - the product's stock and its order are both at a bin
    nobody plans against, so there is no row at all."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    outside = _warehouse(db, f"ZZTBRW{_u()[:4]}-HP", planning=False)
    product = _product(db, f"{marker}-A")
    _stock(db, product, outside, 10)
    _demand(
        db, product, outside, qty=90, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    assert body["data"] == []
    assert body["pagination"]["total"] == 0


# --------------------------------------------------------------------------- AC-S2-2 / R21


def _project_line_for(db, core_line):
    """A minimal Project SO + line over an existing core SO line - `project_id=None`
    (the ADOPTED shape the model documents), so this stays a two-table chain rather than
    dragging in `register_project` / `project_seed_service` for a hold-plumbing test."""
    from app.models.project_so import (
        SO_STATUS_PUBLISHED,
        ProjectSalesOrder,
        ProjectSalesOrderLine,
    )

    order = ProjectSalesOrder(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        project_id=None,
        provisional_ref=f"ZZTPSO-{_u()[:8]}",
        area_group="ZZT",
        status=SO_STATUS_PUBLISHED,
        so_id=core_line.sales_order_id,
    )
    db.add(order)
    db.flush()
    line = ProjectSalesOrderLine(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        project_sales_order_id=order.id,
        core_sales_order_line_id=core_line.id,
        line_no=1,
        product_id=core_line.product_id,
        description="ZZT hold line",
        qty=core_line.qty_ordered,
        uom="SET",
        unit_price=Decimal("10.00"),
        amount=Decimal("0"),
        delivery_date=core_line.required_date,
    )
    db.add(line)
    db.flush()
    return order, line


def _po_line_for_hold(db, product, warehouse, *, qty, issue_date):
    from app.models.procurement import PurchaseOrder, PurchaseOrderLine

    po = PurchaseOrder(
        id=_u(),
        po_number=f"ZZTPO{_u()[:6]}".upper(),
        issue_date=issue_date,
        status="active",
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(po)
    db.flush()
    line = PurchaseOrderLine(
        id=_u(),
        purchase_order_id=po.id,
        product_id=product.id,
        warehouse_id=warehouse.id,
        qty_ordered=Decimal(str(qty)),
        qty_received=Decimal("0"),
        line_status="open",
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(line)
    db.flush()
    return po, line


def _order_back_link_on_po(db, project_order, so_line, *, po_line, qty):
    """An ORDER_BACK-verb inquiry row linked to a PO line - `order_inquiry_links.po_line_id`
    - the one shape `_holds()` reads besides a confirmed allocation."""
    from app.models.project_so import (
        IV_ORDER_BACK,
        OrderInquiry,
        OrderInquiryLink,
        OrderInquiryRow,
    )

    inquiry = OrderInquiry(
        id=_u(), company_id=SORENTO_COMPANY_ID, project_sales_order_id=project_order.id,
    )
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        order_inquiry_id=inquiry.id,
        so_line_id=so_line.id,
        qty=Decimal(str(qty)),
        verb=IV_ORDER_BACK,
    )
    db.add(row)
    db.flush()
    link = OrderInquiryLink(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        row_id=row.id,
        po_line_id=po_line.id,
        qty=Decimal(str(qty)),
    )
    db.add(link)
    db.flush()
    return row, link


def _order_back_link_on_spo(db, project_order, so_line, *, allocation, qty):
    """An ORDER_BACK-verb inquiry row linked to an SPO allocation -
    `order_inquiry_links.spo_allocation_id` - `_order_back_link_on_po`'s sibling for the
    other of the two legal link targets. Returns the OI HEADER too (not just the row/
    link), for R29 addendum's own `oi_number`/`oi_id` assertions."""
    from app.models.project_so import (
        IV_ORDER_BACK,
        OrderInquiry,
        OrderInquiryLink,
        OrderInquiryRow,
    )

    inquiry = OrderInquiry(
        id=_u(), company_id=SORENTO_COMPANY_ID, project_sales_order_id=project_order.id,
    )
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        order_inquiry_id=inquiry.id,
        so_line_id=so_line.id,
        qty=Decimal(str(qty)),
        verb=IV_ORDER_BACK,
    )
    db.add(row)
    db.flush()
    link = OrderInquiryLink(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        row_id=row.id,
        spo_allocation_id=allocation.id,
        qty=Decimal(str(qty)),
    )
    db.add(link)
    db.flush()
    return inquiry, row, link


def test_a_po_line_hold_pins_the_line_in_stock_debt(scm_app):
    """R42 (owner, 28 Sep 2026) supersedes R23 here. R23 had this placement link
    (`order_inquiry_links.po_line_id`) pin NOTHING in Stock Debt; R42 counts purchase
    orders as supply again, so the placement pins the PO's quantity to the line exactly as
    it does on the board, Covered by names the PO with its own link target, and the PO's
    own month lists it with kind `po`. This PO line states no Delivery date, so it is
    parked on R29's `issue + lead`.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1",
    )
    project_order, project_line = _project_line_for(db, core_line)
    po, po_line = _po_line_for_hold(db, product, warehouse, qty=50, issue_date=TODAY)
    _row, _link = _order_back_link_on_po(
        db, project_order, project_line, po_line=po_line, qty=50
    )
    db.flush()

    from app.services.project_supply_service import ProjectSupplyService

    po_rows = ProjectSupplyService(db).po_by_location(
        [str(product.id)], [str(warehouse.id)]
    )
    arrival = po_rows[(str(product.id), str(warehouse.id))][0].arrival_date

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
        supply_cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(arrival)}
        ).json()

    assert len(cell["demand"]) == 1
    demand_line = cell["demand"][0]
    assert demand_line["so_number"] == f"{marker}-SO1"
    assert demand_line["status"] == "pinned"
    assert demand_line["assigned_qty"] == 50
    [entry] = demand_line["assigned_from"]
    assert entry["kind"] == "po"
    assert entry["po_number"] == po.po_number
    assert entry["po_line_number"] == 1
    assert entry["po_id"] == str(po.id)
    assert entry["po_line_id"] == str(po_line.id)
    assert entry["qty"] == 50

    rows = [row for row in supply_cell["supply"] if row["kind"] == "po"]
    assert len(rows) == 1
    assert rows[0]["date"] == arrival.isoformat()
    assert rows[0]["po_line_id"] == str(po_line.id)
    assert rows[0]["assigned_to"] == [
        {"so_number": f"{marker}-SO1", "line_no": None, "qty": 50},
    ]


def test_an_unlinked_po_line_covers_no_line_and_stays_free_in_its_month(scm_app):
    """R45 (owner, 30 Sep 2026, PO-NO-AUTO-ASSIGN; AC-1) supersedes R42's "a FREE PO line
    covers first-come" on this page: "the PO quantity is ordered for a reason, and the user
    is yet to do linking in AutoCount, so it will be premature to allocate to other SO by
    our calculation". A PO line with no S/O and no placement covers nobody: the line reads
    short for the whole 50, the PO's own month still lists the line with Qty, Received and
    Outstanding, its whole outstanding is FREE ("it should still contribute to the stock
    debt quantity"), and the two month cells foot with their drills: +60 in the delivery
    month, -50 in the line's."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(2)
    delivery = _months_ahead(1)
    _demand(db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1")
    _po, po_line = _po_line_for_hold(db, product, warehouse, qty=80, issue_date=TODAY)
    po_line.expected_date = delivery
    po_line.qty_received = Decimal("20")
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
        supply_cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(delivery)}
        ).json()
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    line = cell["demand"][0]
    assert line["status"] == "short"
    assert line["assigned_qty"] == 0
    assert line["assigned_from"] == []
    assert line["short_qty"] == 50
    [row] = supply_cell["supply"]
    assert row["kind"] == "po"
    assert row["date"] == delivery.isoformat()
    assert (row["qty"], row["received_qty"], row["outstanding_qty"]) == (80, 20, 60)
    assert row["free_qty"] == 60
    assert row["assigned_to"] == []
    assert supply_cell["supply_total_qty"] == 60
    months = {m["key"]: m["balance"] for m in _row_of(board, product.product_code)["months"]}
    assert months[month_key(delivery)] == 60
    assert months[month_key(due)] == -50


def test_an_unlinked_spo_line_covers_no_line_either(scm_app):
    """R45, AC-2: "i think this applies for SPO also though ... we shouldn't prematurely
    auto assign the SPO". An SPO allocation with no placement and no S/O is listed free in
    its arrival month and covers nobody."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(2)
    arrives = _months_ahead(1)
    _demand(db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1")
    _spo(db, product, warehouse, qty=50, arrives=arrives)
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
        supply_cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(arrives)}
        ).json()

    line = cell["demand"][0]
    assert line["status"] == "short"
    assert line["assigned_qty"] == 0
    assert line["short_qty"] == 50
    [row] = supply_cell["supply"]
    assert row["kind"] == "spo"
    assert row["free_qty"] == 50
    assert row["assigned_to"] == []


def test_a_placement_still_pins_and_only_the_placed_part_leaves_the_free_pile(scm_app):
    """R45, AC-3: the link is the ONLY way a document reaches a line. A placement of 30 on
    a PO line of 80 pins 30 to the line; the line is short its other 20 (the walk does not
    top it up from the same document), and the PO's unplaced 50 stays free."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(2)
    delivery = _months_ahead(1)
    _order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1"
    )
    project_order, project_line = _project_line_for(db, core_line)
    po, po_line = _po_line_for_hold(db, product, warehouse, qty=80, issue_date=TODAY)
    po_line.expected_date = delivery
    _order_back_link_on_po(db, project_order, project_line, po_line=po_line, qty=30)
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
        supply_cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(delivery)}
        ).json()

    line = cell["demand"][0]
    assert line["status"] == "short"
    assert line["assigned_qty"] == 30
    assert line["short_qty"] == 20
    [entry] = line["assigned_from"]
    assert (entry["kind"], entry["po_line_id"], entry["qty"]) == ("po", str(po_line.id), 30)
    [row] = supply_cell["supply"]
    assert row["free_qty"] == 50
    assert row["assigned_to"] == [{"so_number": f"{marker}-SO1", "line_no": None, "qty": 30}]


def test_an_spo_line_naming_the_order_pins_it_through_its_s_o(scm_app):
    """R45, AC-4: "most SPO should have linkage already". The AutoCount S/O reference on a
    shipping-order line (`spo_allocations.from_so_line_ref`) is a link, read the way a PO
    line's already is (R42 point 3): the named order's line is pinned to the SPO, and what
    the order does not need stays free."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(2)
    arrives = _months_ahead(1)
    order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1"
    )
    order.source_ref = f"ZZTBOOK:{marker}"
    core_line.source_ref = f"ZZTBOOK:{marker}:1"
    _demand(
        db, product, warehouse, qty=40, required_date=_months_ahead(1) + timedelta(days=1),
        so_number=f"{marker}-SO-EARLIER",
    )
    allocation = _spo(db, product, warehouse, qty=70, arrives=arrives)
    allocation.from_so_line_ref = f"ZZTBOOK:{marker}:1"
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
        supply_cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(arrives)}
        ).json()

    by_so = {row["so_number"]: row for row in cell["demand"]}
    named = by_so[f"{marker}-SO1"]
    assert named["status"] == "pinned"
    assert named["assigned_qty"] == 50
    assert named["short_qty"] == 0
    [entry] = named["assigned_from"]
    assert (entry["kind"], entry["spo_number"], entry["qty"]) == (
        "spo", allocation.spo_number, 50,
    )
    earlier = [row for row in supply_cell["demand"] if row["so_number"].endswith("EARLIER")]
    assert earlier[0]["status"] == "short", "the S/O names SO1; the earlier line gets nothing"
    [row] = supply_cell["supply"]
    assert row["kind"] == "spo"
    assert row["free_qty"] == 20
    assert row["assigned_to"] == [{"so_number": f"{marker}-SO1", "line_no": None, "qty": 50}]


def test_a_document_landing_after_the_line_never_clears_its_shortfall_in_the_view(scm_app):
    """R45, AC-5: a line due before an unlinked document used to read `late` once the
    document landed (it cleared the group's open shortfall). A document nobody linked
    clears nothing: the line is `short`, never `late`, and the document is free in its own
    month."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    lands = _months_ahead(2)
    _demand(db, product, warehouse, qty=30, required_date=due, so_number=f"{marker}-SO1")
    _po, po_line = _po_line_for_hold(db, product, warehouse, qty=30, issue_date=TODAY)
    po_line.expected_date = lands
    _spo(db, product, warehouse, qty=30, arrives=lands)
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    assert cell["demand"][0]["status"] == "short"
    assert cell["demand"][0]["assigned_qty"] == 0
    months = {m["key"]: m["balance"] for m in _row_of(board, product.product_code)["months"]}
    assert months[month_key(due)] == -30
    assert months[month_key(lands)] == 60


def test_on_hand_is_still_drawn_first_come_by_required_date(scm_app):
    """R45, AC-6: the rule is about DOCUMENTS. Stock on the floor is drawn by the walk
    exactly as before - the earlier line takes it, the later one is short the rest."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 100)
    due = _months_ahead(1)
    _demand(db, product, warehouse, qty=60, required_date=due, so_number=f"{marker}-SO1")
    _demand(
        db, product, warehouse, qty=60, required_date=due + timedelta(days=1),
        so_number=f"{marker}-SO2",
    )
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()

    by_so = {row["so_number"]: row for row in cell["demand"]}
    assert by_so[f"{marker}-SO1"]["status"] == "covered"
    assert by_so[f"{marker}-SO1"]["assigned_qty"] == 60
    assert by_so[f"{marker}-SO2"]["status"] == "short"
    assert by_so[f"{marker}-SO2"]["assigned_qty"] == 40
    assert by_so[f"{marker}-SO2"]["short_qty"] == 20


def test_the_board_path_still_walks_an_unlinked_po_first_come(scm_app):
    """R45, AC-7: the view's own reading, never the board's. `assignments_for` (the ladder
    and the board) still nets an unlinked PO as free supply the walk hands out (plan v7
    R29; AC-PO-8 / AC-PO-15), so the same fixture reads `covered` there and `short` here."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    # Due well after `issue + lead` (the board's own PO date, 90 days by default) so the
    # board covers the line on time rather than late.
    due = _months_ahead(5)
    _demand(db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1")
    _po, po_line = _po_line_for_hold(db, product, warehouse, qty=80, issue_date=TODAY)
    po_line.expected_date = _months_ahead(1)
    db.flush()

    from app.services.scm.stock_debt_service import StockDebtService

    span = {str(warehouse.id): warehouse}
    service = StockDebtService(db)
    board = service.assignments_for([str(product.id)], span)[str(product.id)]
    view = service._assignments(
        [(str(product.id), "", None)], span, view=True
    )[str(product.id)]

    [board_line] = board.lines
    assert board_line.status == "covered"
    assert [(item.event.key, item.qty) for item in board_line.assigned] == [
        (f"po:{po_line.id}", 50.0)
    ]
    assert board.free[f"po:{po_line.id}"] == 30
    [view_line] = view.lines
    assert view_line.status == "short"
    assert view_line.assigned == ()
    assert view.free[f"po:{po_line.id}"] == 80


def test_an_spo_reaches_a_line_only_through_its_placement_in_the_view(scm_app):
    """R23's other half, as R45 (owner, 30 Sep 2026) re-reads it for this page: an SPO is
    still real supply, and it reaches a line only through a LINK. The same SPO covers the
    line once a placement names it, and covers nobody without one (its quantity stays
    free - `test_an_unlinked_spo_line_covers_no_line_either`).
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    _order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1"
    )
    project_order, project_line = _project_line_for(db, core_line)
    allocation = _spo(db, product, warehouse, qty=50, arrives=due)
    _order_back_link_on_spo(db, project_order, project_line, allocation=allocation, qty=50)
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()

    line = cell["demand"][0]
    assert line["status"] == "pinned"
    assert line["assigned_qty"] == 50
    assert [entry["kind"] for entry in line["assigned_from"]] == ["spo"]


def test_the_cell_envelope_carries_total_quantities_for_the_tab_labels(scm_app):
    """R25 (owner, 24 Sep): the tab labels state total QUANTITY, not record count -
    `demand_total_qty` (sum of `open_qty` over the cell's demand rows) and
    `supply_total_qty` (sum of `qty` over its supply rows), so the FE does not sum on its
    own. RED today: neither field exists on the envelope at all.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    _demand(db, product, warehouse, qty=30, required_date=due, so_number=f"{marker}-SO1")
    _demand(db, product, warehouse, qty=20, required_date=due, so_number=f"{marker}-SO2")
    _spo(db, product, warehouse, qty=15, arrives=due)
    _spo(db, product, warehouse, qty=25, arrives=due)
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()

    assert len(cell["demand"]) == 2
    assert cell["demand_total_qty"] == 50
    assert len(cell["supply"]) == 2
    assert cell["supply_total_qty"] == 40


def test_the_cell_states_spo_qty_received_and_outstanding(scm_app):
    """R26 (owner, 24 Sep): the drill's Supply tab states Qty (the SPO line's ordered
    quantity), Received (quantity received so far) and Outstanding (Qty minus Received) -
    the walk itself counts ONLY Outstanding as incoming supply, so received goods are not
    double-counted (they are already on hand at the bin).

    `ProjectSupplyService._spo_rows` (the R7 lane, "PO qty_received = SPO transfer")
    ALREADY nets `allocated_quantity - quantity_received` into the ONE `qty` the walk
    uses for assignment - so the ASSIGNMENT half below (`assigned_qty == 70`, capped at
    Outstanding, never the full 100) is a REGRESSION GUARD, not new behaviour. RED today
    is the two NEW WIRE FIELDS only: the route's single `qty` on a supply row is already
    the netted Outstanding value, not the SPO line's raw ordered quantity, and
    `received_qty` does not exist on the schema at all.

    R45 (30 Sep 2026): the line holds the SPO through a placement of the full 100 - on
    this page nothing but a link spends a document - and the cap at Outstanding is the
    walk's own (`assign()` caps a pin at what the event has left).
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    _order, core_line = _demand(
        db, product, warehouse, qty=100, required_date=due, so_number=f"{marker}-SO1"
    )
    project_order, project_line = _project_line_for(db, core_line)
    allocation = _spo(db, product, warehouse, qty=100, received=30, arrives=due)
    _order_back_link_on_spo(
        db, project_order, project_line, allocation=allocation, qty=100
    )
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()

    supply_row = cell["supply"][0]
    assert supply_row["qty"] == 100
    assert supply_row["received_qty"] == 30
    assert supply_row["outstanding_qty"] == 70

    demand_line = cell["demand"][0]
    assert demand_line["assigned_qty"] == 70

    assert cell["supply_total_qty"] == 70


def test_the_cell_carries_linked_documents_and_named_lines(scm_app):
    """R29 (owner, 24 Sep): documents are LINKS and lines are NAMED.

    - A supply row carries `spo_number` / `spo_line_number` so the FE can link the
      Document cell to `/procurement-management/spo-allocations/<spo_number>`.
    - Its `assigned_to` entries carry `line_no` (the SO line's own number) beside
      `so_number`, so the FE can render "SO382618 line 2 (100)".
    - A demand line carries `sales_order_id` (for the Sales order cell's own link) and
      `assigned_from` - `assigned_source` (free text) split into
      `[{kind, ref, spo_number, spo_line_number, qty, oi_number, oi_id}]`, one entry per
      source. `oi_number`/`oi_id` (addendum, same day) name the order inquiry a PLACED
      source came through; this line's own source is the book's S/O pin (R45: the SPO's
      `from_so_line_ref` names the line, no placement), so both are `None` here - the
      placed case is its own test below.

    Asserted by NAME through the route (a `response_model` that has not declared a field
    drops it silently) - RED today because none of `spo_number`, `spo_line_number`,
    `line_no`, `sales_order_id` or `assigned_from` exists on the wire at all.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    order, core_line = _demand(
        db, product, warehouse, qty=100, required_date=due, so_number=f"{marker}-SO1",
    )
    core_line.line_no = 2
    order.source_ref = f"ZZTBOOK:{marker}"
    core_line.source_ref = f"ZZTBOOK:{marker}:2"
    allocation = _spo(db, product, warehouse, qty=100, arrives=due, spo_number=f"ZZT-SPO-{_u()[:6]}")
    allocation.from_so_line_ref = f"ZZTBOOK:{marker}:2"
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()

    # `_spo_ref`'s own rule (`stock_debt_service.py`): the SPO number as-is if it already
    # reads "SPO...", else prefixed - our marker-prefixed number needs the prefix.
    expected_ref = f"SPO {allocation.spo_number}"

    demand_line = cell["demand"][0]
    assert demand_line["sales_order_id"] == str(order.id)
    assert demand_line["assigned_from"] == [
        {
            "kind": "spo", "ref": expected_ref,
            "spo_number": allocation.spo_number, "spo_line_number": allocation.spo_line_number,
            "qty": 100,
            # R29 addendum: this line drew the SPO off the plain WALK (no placement
            # link), so it names no order inquiry at all.
            "oi_number": None, "oi_id": None,
            # R42: a document entry always carries the PO link keys, null for an SPO.
            "po_number": None, "po_line_number": None, "po_id": None, "po_line_id": None,
        }
    ]

    supply_row = cell["supply"][0]
    assert supply_row["spo_number"] == allocation.spo_number
    assert supply_row["spo_line_number"] == allocation.spo_line_number
    assert supply_row["assigned_to"] == [
        {"so_number": f"{marker}-SO1", "line_no": 2, "qty": 100},
    ]


def test_a_pinned_source_names_the_order_inquiry_it_came_through(scm_app):
    """R29 addendum (owner, 24 Sep): a placement link is an `order_inquiry_links` row -
    part of an OI row's quantity, on one document line, and the OI row itself points at
    the SO line - so a PINNED source names the order inquiry it came through.
    `assigned_from` entries of kind spo/po that come from a placement carry `oi_number`
    and `oi_id` (the OI header the row belongs to). RED today: neither field exists on
    `assigned_from` at all (which itself does not exist before this same red round).
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1",
    )
    project_order, project_line = _project_line_for(db, core_line)
    allocation = _spo(db, product, warehouse, qty=50, arrives=due)
    inquiry, _row, _link = _order_back_link_on_spo(
        db, project_order, project_line, allocation=allocation, qty=50,
    )
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()

    demand_line = cell["demand"][0]
    assert demand_line["status"] == "pinned"
    assert demand_line["assigned_from"] == [
        {
            "kind": "spo", "ref": f"SPO {allocation.spo_number}",
            "spo_number": allocation.spo_number, "spo_line_number": allocation.spo_line_number,
            "qty": 50, "oi_number": inquiry.inquiry_no, "oi_id": str(inquiry.id),
            "po_number": None, "po_line_number": None, "po_id": None, "po_line_id": None,
        }
    ]


# --------------------------------------------------------------------------- AC-S2-7


def test_the_cell_lists_the_demand_with_its_bin_and_the_supply_with_its_assignment(
    scm_app,
):
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    agent = _agent(db, f"ZZT{_u()[:4]}".upper())
    _stock(db, product, warehouse, 40)
    due = _months_ahead(1)
    _demand(
        db, product, warehouse, qty=100, required_date=due,
        so_number=f"{marker}-SO1", agent=agent,
    )
    _spo(db, product, warehouse, qty=60, arrives=_months_ahead(2))
    db.flush()

    with TestClient(app) as c:
        got = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(due)}
        )
    assert got.status_code == 200, got.text
    body = got.json()

    assert len(body["demand"]) == 1
    line = body["demand"][0]
    assert line["so_number"] == f"{marker}-SO1"
    assert line["agent_code"] == agent.sales_agent
    assert line["warehouse_code"] == warehouse.warehouse_code
    assert line["required_date"] == due.isoformat()
    assert line["open_qty"] == 100
    # 40 on hand at its date; the SPO that lands a month after it is nobody's without a
    # link (R45, 30 Sep 2026), so the other 60 is short. Before R45 the SPO cleared it and
    # the line read `late` for the whole 100.
    assert line["assigned_qty"] == 40
    assert line["status"] == "short"
    assert line["short_qty"] == 60
    # R29: `assigned_source` (free text) is replaced by `assigned_from`, one linked entry
    # per source - one here, the on-hand bin.
    on_hand_sources = [e for e in line["assigned_from"] if e["kind"] == "on_hand"]
    assert len(on_hand_sources) == 1
    assert warehouse.warehouse_code in on_hand_sources[0]["ref"]

    # The on hand sits in the CURRENT month, so this month's supply is the SPO only.
    current = c_get_supply(app, product, month_key(TODAY))
    assert [event["kind"] for event in current] == ["on_hand"]
    assert current[0]["warehouse_code"] == warehouse.warehouse_code
    assert current[0]["qty"] == 40
    assert current[0]["ref"] is None
    # By NAME, because `StockDebtSupplyEvent.date` is the field a `from __future__ import
    # annotations` module very nearly could not declare (see the schema's own note), and a
    # `response_model` drops silently what it fails to declare.
    assert current[0]["date"] == TODAY.isoformat()
    assert current[0]["bought_for"] is None
    assert current[0]["overdue"] is False
    # `line_no` is the seeded core line's own AutoCount `Seq` - `None` here since `_demand`
    # never sets one. RED on purpose (owner ruling, 25 Sep): every other field on this
    # contract is ALWAYS present, null when unknown (`response_model` discipline) - a key
    # that appears and disappears is what breaks FE typing. Today the service OMITS the
    # key entirely instead (`stock_debt_service.py`:
    # `**({"line_no": line_no} if line_no is not None else {})`) and the route carries
    # `response_model_exclude_unset=True` to make that stick - the coder is to emit the
    # key unconditionally and drop `exclude_unset`.
    assert current[0]["assigned_to"] == [
        {"so_number": f"{marker}-SO1", "qty": 40, "line_no": None},
    ]


def c_get_supply(app, product, month) -> list:
    with TestClient(app) as c:
        return c.get(f"{BASE}/{product.id}/cell", params={"month": month}).json()[
            "supply"
        ]


def test_the_cell_states_ordered_delivered_and_outstanding(scm_app):
    """R22 (owner, 24 Sep): the Demand grid states Ordered/Delivered/Outstanding, not
    Outstanding (`open_qty`) alone. Delivered = `qty_delivered`; Outstanding is `open_qty`
    unchanged (Ordered minus Delivered, floored at 0) - RED today because the route's
    dict/schema carries neither `qty_ordered` nor `qty_delivered` at all, so a
    `response_model` that has not declared them drops them silently.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    _demand(
        db, product, warehouse, qty=100, qty_delivered=30, required_date=due,
        so_number=f"{marker}-SO1",
    )
    db.flush()

    with TestClient(app) as c:
        got = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)})
    assert got.status_code == 200, got.text
    line = got.json()["demand"][0]
    assert line["qty_ordered"] == 100
    assert line["qty_delivered"] == 30
    assert line["open_qty"] == 70


def test_the_cell_states_ordered_as_the_cs_required_quantity_when_set(scm_app):
    """R22: `qty_required` (the Order Inquiry sheet's own CS statement) wins over
    `qty_ordered` for the drill's Ordered column - the same coalesce `demand_qty()` and
    `plan_qty()` already apply elsewhere (`app/services/scm/demand.py`).
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    _demand(
        db, product, warehouse, qty=60, qty_required=80, required_date=due,
        so_number=f"{marker}-SO1",
    )
    db.flush()

    with TestClient(app) as c:
        got = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)})
    assert got.status_code == 200, got.text
    assert got.json()["demand"][0]["qty_ordered"] == 80


def test_a_120_day_late_document_is_still_supply_on_the_page(scm_app):
    """AC-S2-4b / R31 as R44 (owner, 29 Sep 2026, #1359) leaves it for THIS page: the
    arrival passed 120 days ago with nothing received, past any dead line, and the stock
    debt page still counts it - supply is the purchased quantity, whatever its date. It is
    listed today with `overdue: false`, named late (`days_late`), and its whole quantity is
    FREE: it covers no line without a link (R45, 30 Sep 2026), so the line reads short.
    The dead rule itself stays the board's and the ladder's (`test_overdue_grace_ladder.py`).
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    stated = TODAY - timedelta(days=120)
    _spo(db, product, warehouse, qty=50, arrives=stated)
    due = _months_ahead(1)
    _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1"
    )
    db.flush()

    with TestClient(app) as c:
        landing = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(TODAY)}
        ).json()
        cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(due)}
        ).json()

    [row] = landing["supply"]
    assert row["overdue"] is False
    assert row["qty"] == 50
    assert row["date"] == TODAY.isoformat()
    assert row["stated_date"] == stated.isoformat()
    assert row["days_late"] == 120
    assert row["free_qty"] == 50
    assert cell["demand"][0]["status"] == "short"
    assert cell["demand"][0]["assigned_qty"] == 0


def test_a_late_document_lands_today_whatever_the_policy_grace(scm_app):
    """AC-O.3's other half as R44 (#1359) leaves it for this page: 41 days late under a
    14 / 90 policy. The page ignores the grace, so the ledger row is filed TODAY (the axis
    starts today, so that is the month it arrived in), prints the date the paperwork
    states beside it, and is not marked overdue - it is supply, free in today's month.
    R45 (30 Sep 2026): free, not assigned - without a link it covers nobody, so the line
    reads short. The grace itself stays the board's (`test_overdue_grace_ladder.py`).

    `stated_date` / `days_late` are asserted BY NAME: `response_model` drops what a schema
    does not declare, and a field the walk computes and the wire never carries is a field
    the Stock tab cannot print.
    """
    app, db = _client(scm_app)
    priority.create_revision(
        db, name=f"ZZT-grace-{_u()[:6]}", factors={}, demand_class_weights={},
        reorder_coverage_until=None, overdue_grace_days=14, overdue_dead_days=90,
    )
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    stated = TODAY - timedelta(days=41)
    _spo(db, product, warehouse, qty=50, arrives=stated)
    due = _months_ahead(2)
    _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1"
    )
    db.flush()

    with TestClient(app) as c:
        landing = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(TODAY)}
        ).json()
        cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(due)}
        ).json()

    rows = [event for event in landing["supply"] if event["kind"] == "spo"]
    assert len(rows) == 1, "filed under today's month, never today + grace"
    assert rows[0]["date"] == TODAY.isoformat()
    assert rows[0]["stated_date"] == stated.isoformat()
    assert rows[0]["days_late"] == 41
    assert rows[0]["overdue"] is False
    assert rows[0]["free_qty"] == 50
    assert cell["demand"][0]["status"] == "short"
    assert cell["demand"][0]["assigned_qty"] == 0


def test_the_cell_answers_the_tba_and_the_undated_bucket(scm_app):
    """Both keys are addressable and both draw nothing (R14): the lines are listed with
    their whole quantity open and no supply beside them."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 500)
    _demand(
        db, product, warehouse, qty=60, required_date=date(2030, 1, 1),
        so_number=f"{marker}-TBA",
    )
    _demand(
        db, product, warehouse, qty=12, required_date=None, so_number=f"{marker}-NODATE"
    )
    db.flush()

    with TestClient(app) as c:
        tba = c.get(f"{BASE}/{product.id}/cell", params={"month": "tba"}).json()
        undated = c.get(f"{BASE}/{product.id}/cell", params={"month": "undated"}).json()

    assert [line["so_number"] for line in tba["demand"]] == [f"{marker}-TBA"]
    assert tba["demand"][0]["assigned_qty"] == 0
    assert tba["demand"][0]["status"] == "short"
    assert tba["supply"] == []
    assert [line["so_number"] for line in undated["demand"]] == [f"{marker}-NODATE"]
    assert undated["demand"][0]["required_date"] is None
    assert undated["supply"] == []


def test_an_unknown_month_key_is_refused(scm_app):
    app, db = _client(scm_app)
    product = _product(db, f"ZZTSD{_u()[:6]}".upper())
    db.flush()
    with TestClient(app) as c:
        assert c.get(f"{BASE}/{product.id}/cell", params={"month": "soon"}).status_code == 422


# --------------------------------------------------------------------------- AC-S2-8


def test_both_routes_need_the_stock_debt_permission(scm_app):
    app, db = _client(scm_app, permission=None)
    product_id = _u()
    with TestClient(app) as c:
        assert c.get(BASE).status_code == 403
        assert (
            c.get(f"{BASE}/{product_id}/cell", params={"month": "tba"}).status_code
            == 403
        )


def test_a_role_holding_projects_view_reaches_stock_debt_through_the_443_sweep(scm_app):
    """AC-S2-8: migration 443's sweep gives `projects.stock_debt.view` to every role that
    already holds `projects.projects.view`. This runs the SWEEP'S OWN SQL (copied from
    `443_fulfilment_planning_flag_tba_date.py`, not a hand-rolled duplicate grant) against
    a role holding only the source permission, so a 200 here proves the sweep - not a test
    fixture standing in for it."""
    app, db, gcu, gcuak = scm_app
    ensure_reference_data(db)
    uid = seed_user(db, None)
    role_id = _u()
    db.execute(
        text(
            "INSERT INTO user_roles (id, slug, name, is_trashed, is_protected, "
            "is_default, created_at) VALUES (:id, :slug, 'ZZT projects view only', "
            "false, false, false, now())"
        ),
        {"id": role_id, "slug": f"zzt-projects-view-{role_id[:8]}"},
    )
    source_id = db.execute(
        text("SELECT id FROM user_permissions WHERE slug = 'projects.projects.view'")
    ).scalar()
    assert source_id, "projects.projects.view must exist"
    db.execute(
        text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, "
            "assigned_at) VALUES (:id, :r, :p, now())"
        ),
        {"id": _u(), "r": role_id, "p": source_id},
    )
    target_id = db.execute(
        text("SELECT id FROM user_permissions WHERE slug = :s"), {"s": VIEW}
    ).scalar()
    assert target_id, f"{VIEW} must exist - migration 443 seeds it"

    # Migration 443's own sweep SQL, scoped to this one role.
    db.execute(
        text(
            """
            INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
            SELECT gen_random_uuid()::text, rp.role_id, :target, now()
            FROM user_role_permissions rp
            JOIN user_permissions src
              ON src.id = rp.permission_id AND src.slug = :source
            WHERE rp.role_id = :role
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        ),
        {"target": target_id, "source": "projects.projects.view", "role": role_id},
    )
    from app.models.user import UserRoleAssignment

    db.add(UserRoleAssignment(user_id=uid, role_id=role_id))
    db.flush()
    as_user(app, gcu, gcuak, uid)

    with TestClient(app) as c:
        got = c.get(BASE, params={"query": f"ZZTNOPE{_u()[:6]}".upper()})
    assert got.status_code == 200, got.text


# --------------------------------------------------------------------------- AC-S2-1b:
# a confirmed hold outranks the span it was read in


def _confirmed_hold(db, project_line, warehouse, qty):
    """A CONFIRMED `so_line_allocations` row - the shape `_hold_query` recognises: located,
    not an ORDER source, belonging to no decision, and confirmed."""
    from datetime import datetime

    from app.models.project_so import ALLOC_SOURCE_GROUP_TAKE, SOLineAllocation

    row = SOLineAllocation(
        id=_u(),
        company_id=SORENTO_COMPANY_ID,
        so_line_id=project_line.id,
        source_type=ALLOC_SOURCE_GROUP_TAKE,
        warehouse_id=warehouse.id,
        qty=Decimal(str(qty)),
        confirmed_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def test_a_hold_at_a_pool_or_an_unflagged_bin_still_reads_pinned(scm_app):
    """AC-S2-1b / R21: pinned means pinned.

    Both holds name a bin this read cannot see - a SITE POOL and a bin flagged out of
    fulfilment planning - so the supply event behind them is not in the span. Before this
    the hold was skipped and the board-covered line printed `short`: 16 pool holds (163
    units) and ~103 units at unflagged bins on the 30 Aug dev copy. The drill names the bin
    the HOLD carries, so the source column is a place rather than a blank.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    pool = _warehouse(db, f"ZZTPOOL{_u()[:5]}", planning=False)
    unflagged = _warehouse(db, f"ZZTBRW{_u()[:4]}-HP", planning=False)
    product = _product(db, f"{marker}-A")
    _stock(db, product, pool, 500)
    _stock(db, product, unflagged, 500)
    due = _months_ahead(1)
    _order_a, line_a = _demand(
        db, product, bb, qty=40, required_date=due, so_number=f"{marker}-SO-POOL"
    )
    _order_b, line_b = _demand(
        db, product, bb, qty=30, required_date=due, so_number=f"{marker}-SO-HP"
    )
    _pso_a, pline_a = _project_line_for(db, line_a)
    _pso_b, pline_b = _project_line_for(db, line_b)
    _confirmed_hold(db, pline_a, pool, 40)
    _confirmed_hold(db, pline_b, unflagged, 30)
    db.flush()

    with TestClient(app) as c:
        cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(due)}
        ).json()
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    by_so = {line["so_number"]: line for line in cell["demand"]}
    assert by_so[f"{marker}-SO-POOL"]["status"] == "pinned"
    assert by_so[f"{marker}-SO-POOL"]["assigned_qty"] == 40
    # R29: one `assigned_from` entry, naming the pool bin the hold pinned.
    assert by_so[f"{marker}-SO-POOL"]["assigned_from"] == [
        {
            "kind": "on_hand", "ref": f"On hand {pool.warehouse_code}",
            "spo_number": None, "spo_line_number": None, "qty": 40,
            "lent_from_so_number": None,
        }
    ]
    assert by_so[f"{marker}-SO-HP"]["status"] == "pinned"
    assert by_so[f"{marker}-SO-HP"]["assigned_from"] == [
        {
            "kind": "on_hand", "ref": f"On hand {unflagged.warehouse_code}",
            "spo_number": None, "spo_line_number": None, "qty": 30,
            "lent_from_so_number": None,
        }
    ]
    # And the month agrees with the drill: 70 pinned against 70 owed owes nothing.
    row = _row_of(board, product.product_code)
    assert {m["key"]: m["balance"] for m in row["months"]}[month_key(due)] == 0


def test_a_donor_holds_stock_in_another_group_and_group_bb_still_reads_it_pinned(scm_app):
    """The third way a hold's bin leaves the span: the READER narrowed it.

    A DC1-IB donor holds 25 for a BB line. Under `group=BB` the IB bin is out of the span
    entirely - and the hold is still honoured, because narrowing the question does not
    unmake a promise (AC-S2-1b).
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    ib = _warehouse(db, f"ZZTDC1{_u()[:4]}-IB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, ib, 25)
    due = _months_ahead(1)
    _order, core_line = _demand(
        db, product, bb, qty=25, required_date=due, so_number=f"{marker}-SO1"
    )
    _pso, project_line = _project_line_for(db, core_line)
    _confirmed_hold(db, project_line, ib, 25)
    db.flush()

    with TestClient(app) as c:
        cell = c.get(
            f"{BASE}/{product.id}/cell",
            params={"month": month_key(due), "group": "BB"},
        ).json()

    assert cell["demand"][0]["status"] == "pinned"
    assert cell["demand"][0]["assigned_qty"] == 25
    assert cell["demand"][0]["assigned_from"] == [
        {
            "kind": "on_hand", "ref": f"On hand {ib.warehouse_code}",
            "spo_number": None, "spo_line_number": None, "qty": 25,
            "lent_from_so_number": None,
        }
    ]


def test_an_allocation_that_was_never_confirmed_holds_nothing(scm_app):
    """`_hold_query`'s own predicate, which the hand-written copy had lost: a hold counts
    when it is CONFIRMED. A decision saved and not confirmed held stock on this screen and
    on no other, so the board and the view disagreed about what was free."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, bb, 100)
    due = _months_ahead(1)
    _order, core_line = _demand(
        db, product, bb, qty=40, required_date=due, so_number=f"{marker}-SO1"
    )
    _pso, project_line = _project_line_for(db, core_line)
    hold = _confirmed_hold(db, project_line, bb, 40)
    hold.confirmed_at = None
    db.flush()

    with TestClient(app) as c:
        cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(due)}
        ).json()

    # Not `pinned`: it drew from the pile first-come like anybody else.
    assert cell["demand"][0]["status"] == "covered"


def test_a_cancelled_inquiry_row_pins_nothing(scm_app):
    """The filter every other consumer of `order_inquiry_links` applies. A withdrawn
    placement is not a promise, and pinning off it held a document to a line nobody is
    waiting on."""
    app, db = _client(scm_app)
    from app.models.project_so import INQUIRY_CANCELLED

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    due = _months_ahead(1)
    _order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1"
    )
    project_order, project_line = _project_line_for(db, core_line)
    _po, po_line = _po_line_for_hold(db, product, warehouse, qty=50, issue_date=TODAY)
    row, _link = _order_back_link_on_po(
        db, project_order, project_line, po_line=po_line, qty=50
    )
    row.state = INQUIRY_CANCELLED
    db.flush()

    with TestClient(app) as c:
        cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": month_key(due)}
        ).json()

    assert cell["demand"][0]["status"] != "pinned"


# --------------------------------------------------------------- SPO-RECEIVED-PIN


def _received_spo_case(db, *, on_hand, received, receipt_status, arrived=None):
    """The SRTSS8710 / SPO-2026/05-0001 shape: SO1's placement sits on an SPO line that is
    no longer incoming, SO2 at the same bin has no placement, and the bin holds whatever
    the SPO brought in. Returns (marker, warehouse, product, allocation, due)."""
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    if on_hand:
        _stock(db, product, warehouse, on_hand)
    due = _months_ahead(1)
    _order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1",
    )
    _demand(db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO2")
    project_order, project_line = _project_line_for(db, core_line)
    allocation = _spo(
        db, product, warehouse, qty=50, arrives=TODAY - timedelta(days=20),
        received=received, receipt_status=receipt_status,
    )
    if arrived is not None:
        _landed_shipment(db, allocation, arrived=arrived)
    _order_back_link_on_spo(db, project_order, project_line, allocation=allocation, qty=50)
    db.flush()
    return marker, warehouse, product, allocation, due


def _assert_pinned_on_the_bin(c, *, marker, warehouse, product, due):
    """AC-1 / AC-2: SO1 is pinned on the bin's own on hand, SO2 is short, the month owes
    50, and the current month's Supply tab is the bin alone, assigned to SO1."""
    cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
    now = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(TODAY)}).json()
    board = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    by_so = {line["so_number"]: line for line in cell["demand"]}
    so1 = by_so[f"{marker}-SO1"]
    assert so1["status"] == "pinned"
    assert so1["assigned_qty"] == 50
    assert so1["assigned_from"] == [
        {
            "kind": "on_hand", "ref": f"On hand {warehouse.warehouse_code}",
            "spo_number": None, "spo_line_number": None, "qty": 50,
            "lent_from_so_number": None,
        }
    ]
    so2 = by_so[f"{marker}-SO2"]
    assert so2["status"] == "short"
    assert so2["assigned_qty"] == 0
    assert so2["short_qty"] == 50

    # The received document is not supply, and no stand-in is built for it: the only
    # supply row anywhere is the bin, and it is spoken for by SO1.
    assert cell["supply"] == []
    assert [row["kind"] for row in now["supply"]] == ["on_hand"]
    [on_hand] = now["supply"]
    assert on_hand["warehouse_code"] == warehouse.warehouse_code
    assert on_hand["qty"] == 50
    assert on_hand["free_qty"] == 0
    assert on_hand["assigned_to"] == [
        {"so_number": f"{marker}-SO1", "line_no": None, "qty": 50},
    ]
    assert now["supply_total_qty"] == 50

    # And the month agrees: 100 owed, 50 real units, debt 50.
    row = _row_of(board, product.product_code)
    assert {m["key"]: m["balance"] for m in row["months"]}[month_key(due)] == -50


def test_a_placement_on_a_fully_received_spo_pins_the_bins_on_hand_not_the_spo(scm_app):
    """SPO-RECEIVED-PIN (owner, 30 Sep 2026, SRTSS8710 / SPO-2026/05-0001): "when the SPO is
    received, it is on hand already, so if we still link to the received SPO, it seems like
    there are more quantities than we should have."

    The bin holds the 50 the SPO line brought in. SO1's placement is on that line, which is
    fully received and so out of incoming supply. Before this fix `_holds` handed the
    placement on as an SPO hold and `assign()` stood a synthetic SPO event up for it (the
    AC-S2-1b branch, meant for supply OUTSIDE the span, not for a document that is not
    supply), so the landed 50 covered SO2 as free on hand too: 100 covered off 50 units.
    Now the placement is an on-hand hold at the SPO's own bin, for what landed.
    """
    app, db = _client(scm_app)
    marker, warehouse, product, _allocation, due = _received_spo_case(
        db, on_hand=50, received=50, receipt_status="fully_received",
    )
    with TestClient(app) as c:
        _assert_pinned_on_the_bin(c, marker=marker, warehouse=warehouse, product=product, due=due)


def test_a_placement_on_a_landed_shipment_pins_the_bins_on_hand_before_the_receipt(scm_app):
    """The shipment's own arrival is the third leg of `open_incoming_clauses()`: a landed
    container takes its SPO line out of incoming supply before a receipt is written. With
    nothing received yet, what landed is the line's allocated quantity, and the pin is still
    capped by what the bin actually holds."""
    app, db = _client(scm_app)
    marker, warehouse, product, _allocation, due = _received_spo_case(
        db, on_hand=50, received=0, receipt_status="pending",
        arrived=TODAY - timedelta(days=2),
    )
    with TestClient(app) as c:
        _assert_pinned_on_the_bin(c, marker=marker, warehouse=warehouse, product=product, due=due)


def test_a_placement_on_a_line_received_in_full_by_quantity_alone_pins_the_bin(scm_app):
    """The quantity half of the rule, on its own: `quantity_received` has reached
    `allocated_quantity` while `receipt_status` still says `pending` and the line is still
    `open`. `_supply` drops that line (`incoming_by_location`'s `balance <= 0`), so `_holds`
    has to convert it too, or the hold and the event disagree. The statuses alone would
    keep it an SPO hold; this is the test the reviewer's kill run found nothing guarding."""
    app, db = _client(scm_app)
    marker, warehouse, product, _allocation, due = _received_spo_case(
        db, on_hand=50, received=50, receipt_status="pending",
    )
    with TestClient(app) as c:
        _assert_pinned_on_the_bin(c, marker=marker, warehouse=warehouse, product=product, due=due)


def test_a_placement_on_a_received_spo_whose_goods_are_gone_pins_nothing(scm_app):
    """A received SPO placement is a promise about STOCK, and stock that has left the bin
    covers nobody: no stand-in SPO event, no on-hand stood up out of nothing. The line
    reads short, which is the action the red cell is asking for."""
    app, db = _client(scm_app)
    marker, _warehouse_row, product, _allocation, due = _received_spo_case(
        db, on_hand=0, received=50, receipt_status="fully_received",
    )
    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()
        now = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(TODAY)}).json()

    by_so = {line["so_number"]: line for line in cell["demand"]}
    so1 = by_so[f"{marker}-SO1"]
    assert so1["status"] == "short"
    assert so1["assigned_qty"] == 0
    assert so1["short_qty"] == 50
    assert so1["assigned_from"] == []
    assert by_so[f"{marker}-SO2"]["status"] == "short"
    assert cell["supply"] == []
    assert now["supply"] == []


def test_a_placement_on_a_partly_received_open_spo_still_pins_the_spo(scm_app):
    """Regression guard: a line still incoming (something left to come, not received, no
    landed shipment) keeps its SPO hold exactly as before, pinned off the netted outstanding
    balance (R26). Only a document that has stopped being supply is converted."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 30)
    due = _months_ahead(1)
    _order, core_line = _demand(
        db, product, warehouse, qty=50, required_date=due, so_number=f"{marker}-SO1",
    )
    project_order, project_line = _project_line_for(db, core_line)
    allocation = _spo(db, product, warehouse, qty=100, received=30, arrives=due)
    inquiry, _row, _link = _order_back_link_on_spo(
        db, project_order, project_line, allocation=allocation, qty=50,
    )
    db.flush()

    with TestClient(app) as c:
        cell = c.get(f"{BASE}/{product.id}/cell", params={"month": month_key(due)}).json()

    [line] = cell["demand"]
    assert line["status"] == "pinned"
    assert line["assigned_qty"] == 50
    assert [(entry["kind"], entry["qty"], entry["oi_id"]) for entry in line["assigned_from"]] == [
        ("spo", 50, str(inquiry.id)),
    ]
    [spo_row] = cell["supply"]
    assert (spo_row["qty"], spo_row["received_qty"], spo_row["outstanding_qty"]) == (100, 30, 70)
    assert spo_row["free_qty"] == 20


def test_reserved_stock_is_not_offered_as_free_supply(scm_app):
    """`_free_stock`'s own arithmetic: on hand MINUS reserved. Reserved stock is spoken for
    by a picking already under way, and offering it to a sales-order line here promised the
    same units twice."""
    app, db = _client(scm_app)
    from app.models.inventory import Stock

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    db.add(
        Stock(
            id=_u(),
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity_on_hand=Decimal("100"),
            quantity_reserved=Decimal("70"),
            company_id=SORENTO_COMPANY_ID,
        )
    )
    _demand(
        db, product, warehouse, qty=50, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    db.flush()

    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    row = _row_of(board, product.product_code)
    balances = {m["key"]: m["balance"] for m in row["months"]}
    # The 30 that is free of the reserve is taken by the line next month, so no month has
    # it spare (R37); the line goes without 20 on its own date.
    assert balances[month_key(TODAY)] == 0
    assert balances[month_key(_months_ahead(1))] == -20


def test_a_zero_day_lead_time_is_read_as_zero_not_as_the_default(scm_app):
    """`leads.get(...) or DEFAULT` turned a stated 0-day lead into 90 days, so a product a
    supplier ships off the shelf painted three months red. `is None` is the test - the same
    one `_po_rows` and `reserve_window_end` make."""
    app, db = _client(scm_app)
    from app.models.procurement import ProductSupplier, Supplier

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    supplier = Supplier(
        id=_u(),
        supplier_code=f"ZZTSUP{_u()[:5]}".upper(),
        supplier_name="ZZT supplier",
        is_active=True,
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(supplier)
    db.flush()
    db.add(
        ProductSupplier(
            id=_u(),
            product_id=product.id,
            supplier_id=supplier.id,
            standard_lead_time_days=0,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    # Two months out: inside a 90-day horizon (red), outside a 0-day one (amber).
    _demand(
        db, product, warehouse, qty=20, required_date=_months_ahead(2),
        so_number=f"{marker}-SO1",
    )
    db.flush()

    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    row = _row_of(board, product.product_code)
    tones = {m["key"]: m["tone"] for m in row["months"]}
    assert tones[month_key(_months_ahead(2))] == "amber"


# --------------------------------------------------------------------------- unlocated


def test_unlocated_lines_are_counted_on_the_row_and_drillable(scm_app):
    """2,312 open lines on the 30 Aug dev copy name no warehouse. They are in no group's
    pile, so they draw nothing - but a screen that lists what is owed and silently omits
    them is answering a narrower question than the one it was asked. Counted in their own
    untoned total, beside TBA and No date, and addressable as a cell like any other."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 500)
    _order, line = _demand(
        db, product, warehouse, qty=15, required_date=_months_ahead(1),
        so_number=f"{marker}-NOWHERE",
    )
    line.warehouse_id = None
    db.flush()

    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        cell = c.get(
            f"{BASE}/{product.id}/cell", params={"month": "unlocated"}
        ).json()

    row = _row_of(board, product.product_code)
    assert row["unlocated"] == -15
    # It never touched the 500 on hand, and it is in no month.
    assert all(m["balance"] == 500 for m in row["months"])
    assert [entry["so_number"] for entry in cell["demand"]] == [f"{marker}-NOWHERE"]
    assert cell["demand"][0]["warehouse_code"] is None
    assert cell["demand"][0]["assigned_qty"] == 0
    assert cell["supply"] == []


def test_a_product_whose_only_demand_is_unlocated_still_gets_a_row(scm_app):
    """The candidate read reaches unlocated demand too, or the product would have no row at
    all and the count would have nowhere to be stated."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _order, line = _demand(
        db, product, warehouse, qty=8, required_date=_months_ahead(1),
        so_number=f"{marker}-ONLY",
    )
    line.warehouse_id = None
    db.flush()

    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": True}).json()

    row = _row_of(board, product.product_code)
    assert row["unlocated"] == -8


# --------------------------------------------------------------------------- R37


def test_a_debt_stays_in_the_month_it_was_raised_in(scm_app):
    """R37, and the defect that produced it (30 Aug, captain's feedback).

    `1/2" ULTRA CIRCULAR` read -4 in Aug 26 and -4 again in every column after it, while the
    drill for September was empty - the cell was a running balance and the drill was a
    month, so the screen could not be added up. A month now states its own month: the line
    books its shortfall where it is due, and the months after it read 0 because nothing is
    due and nothing arrives in them.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _demand(
        db, product, warehouse, qty=4, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    # A second product, so the axis reaches past the month the debt is in - the columns are
    # the whole filtered set's, and without it there would be no later month to read.
    later = _product(db, f"{marker}-B")
    _demand(
        db, later, warehouse, qty=1, required_date=_months_ahead(3),
        so_number=f"{marker}-SO2",
    )
    db.flush()

    due = month_key(_months_ahead(1))
    after = month_key(_months_ahead(2))
    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        drill = c.get(f"{BASE}/{product.id}/cell", params={"month": after}).json()

    row = _row_of(board, product.product_code)
    balances = {m["key"]: m["balance"] for m in row["months"]}
    assert balances[due] == -4
    assert balances[month_key(TODAY)] == 0
    assert balances[after] == 0
    assert balances[month_key(_months_ahead(3))] == 0
    # The cell that reads 0 opens onto nothing, which is now the same statement twice.
    # Not exact-equality any more: R25 added `demand_total_qty` / `supply_total_qty` to
    # every cell response, so this checks the two lists and the two new totals by name
    # rather than pinning the whole envelope shape.
    assert drill["demand"] == []
    assert drill["supply"] == []
    assert drill["demand_total_qty"] == 0
    assert drill["supply_total_qty"] == 0


def test_supply_arriving_after_the_debt_is_spare_in_its_own_month(scm_app):
    """The other half of R37: 10 arriving next month against a 4 due this one reads -4 then
    +10. R45 (30 Sep 2026): the arrival clears nothing without a link, so the line stays
    `short` in its own month and the whole 10 is spare - counted once, in the month it
    lands in. (Before R45 the walk cleared the 4 late and only 6 was spare.)"""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _demand(
        db, product, warehouse, qty=4, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    _spo(db, product, warehouse, qty=10, arrives=_months_ahead(2))
    db.flush()

    due = month_key(_months_ahead(1))
    arrives = month_key(_months_ahead(2))
    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        short_cell = c.get(f"{BASE}/{product.id}/cell", params={"month": due}).json()
        spare_cell = c.get(f"{BASE}/{product.id}/cell", params={"month": arrives}).json()

    balances = {
        m["key"]: m["balance"] for m in _row_of(board, product.product_code)["months"]
    }
    assert balances[due] == -4
    assert balances[arrives] == 10

    # The line's month owes the 4: it went without on the date it was promised, which is
    # the fact the planner acts on, and nobody linked the SPO to it.
    assert short_cell["demand"][0]["status"] == "short"
    assert short_cell["demand"][0]["short_qty"] == 4
    assert short_cell["supply"] == []
    assert spare_cell["demand"] == []
    assert spare_cell["supply"][0]["qty"] == 10
    assert spare_cell["supply"][0]["free_qty"] == 10


def test_every_cell_foots_with_its_drill(scm_app):
    """The identity R37 is worth having: for every month, the drill's free supply less its
    short-at-date demand IS the balance the cell prints. 20 on hand, 30 due next month (20
    now, 10 short), 50 arriving the month after (free: nobody linked it, R45), 5 due the
    month after that (short, for the same reason)."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 20)
    _demand(
        db, product, warehouse, qty=30, required_date=_months_ahead(1),
        so_number=f"{marker}-SO1",
    )
    _spo(db, product, warehouse, qty=50, arrives=_months_ahead(2))
    _demand(
        db, product, warehouse, qty=5, required_date=_months_ahead(3),
        so_number=f"{marker}-SO2",
    )
    db.flush()

    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        row = _row_of(board, product.product_code)
        cells = {
            month["key"]: c.get(
                f"{BASE}/{product.id}/cell", params={"month": month["key"]}
            ).json()
            for month in row["months"]
        }

    balances = {m["key"]: m["balance"] for m in row["months"]}
    for key, cell in cells.items():
        free = sum(event["free_qty"] for event in cell["supply"])
        short = sum(line["short_qty"] for line in cell["demand"])
        assert free - short == balances[key], f"{key} does not foot with its drill"

    # And the figures themselves, so the identity cannot be satisfied by two zeroes.
    assert balances[month_key(TODAY)] == 0
    assert balances[month_key(_months_ahead(1))] == -10
    assert balances[month_key(_months_ahead(2))] == 50
    assert balances[month_key(_months_ahead(3))] == -5


def test_the_tba_undated_and_unlocated_cells_are_unchanged_by_the_month_rule(scm_app):
    """R37 is about MONTHS. The three buckets are not cumulative and never were: they draw
    nothing at all, so every line in them is short its whole open quantity and there is no
    supply to be free."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 500)
    _demand(
        db, product, warehouse, qty=60, required_date=date(2030, 1, 1),
        so_number=f"{marker}-TBA",
    )
    _demand(
        db, product, warehouse, qty=12, required_date=None, so_number=f"{marker}-NODATE",
    )
    _order, line = _demand(
        db, product, warehouse, qty=15, required_date=_months_ahead(1),
        so_number=f"{marker}-NOWHERE",
    )
    line.warehouse_id = None
    db.flush()

    with TestClient(app) as c:
        board = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        cells = {
            bucket: c.get(
                f"{BASE}/{product.id}/cell", params={"month": bucket}
            ).json()
            for bucket in ("tba", "undated", "unlocated")
        }

    row = _row_of(board, product.product_code)
    assert (row["tba"], row["undated"], row["unlocated"]) == (-60, -12, -15)
    for bucket, cell in cells.items():
        assert cell["supply"] == [], bucket
        for entry in cell["demand"]:
            assert entry["short_qty"] == entry["open_qty"], bucket
    assert sum(e["short_qty"] for e in cells["tba"]["demand"]) == 60
    assert sum(e["short_qty"] for e in cells["undated"]["demand"]) == 12
    assert sum(e["short_qty"] for e in cells["unlocated"]["demand"]) == 15
    # Nothing drew on the 500, so it is spare in the month it sits in - and only there.
    balances = {m["key"]: m["balance"] for m in row["months"]}
    assert balances[month_key(TODAY)] == 500


def test_on_hand_is_stamped_with_the_callers_as_of_not_the_clock(scm_app):
    """Review finding: `_supply()` stamped every on-hand event `date.today()`.

    A board simulated at a PAST date (`as_of`, which the ladder pins so a walk is
    reproducible) then read its own floor as arriving after every line due between the two
    dates, so a line due yesterday drew nothing on its own date and came back `late` off
    stock that has been on the shelf all along. The event belongs on the day the walk
    starts, which is the caller's `as_of`.
    """
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, warehouse, 40)
    # Due BETWEEN the simulated day and today: the window the bug lived in.
    _demand(
        db, product, warehouse, qty=40, required_date=TODAY - timedelta(days=3),
        so_number=f"{marker}-SO1",
    )
    db.flush()

    from app.services.scm.stock_debt_service import StockDebtService

    service = StockDebtService(db)
    result = service.assignments_for(
        [str(product.id)],
        {str(warehouse.id): warehouse},
        as_of=TODAY - timedelta(days=10),
    )[str(product.id)]
    line = result.lines[0]
    events = [event for event in result.supply if event.kind == "on_hand"]

    assert events and events[0].at == TODAY - timedelta(days=10)
    assert line.status == "covered", "the floor was there on the simulated day"
    assert line.short_at_date == 0


# --------------------------------------------------------------------------- 24 Sep slice:
# cutoff / supplier / book filters, totals, suppliers, sheet_counts (AC-1..AC-11)
#
# WRITTEN BEFORE THE IMPLEMENTATION EXISTS (Phase 2 is test-first): `cutoff`, `supplier_id`
# and `book` are not yet accepted by either route, and `total`, `totals`, `suppliers` and
# `sheet_counts` are not yet on the schema. FastAPI ignores an undeclared query param rather
# than 422ing it, so every test below is red on a WRONG VALUE (a field missing from the
# dict, or a row/balance the filter should have dropped or kept), never on a collection or
# fixture error.


def _supplier(db, code: str):
    from app.models.procurement import Supplier

    row = Supplier(
        id=_u(), supplier_code=code, supplier_name=f"{code} supplier", is_active=True,
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _po(db, product, warehouse, supplier, *, issue_date, status="active"):
    from app.models.procurement import PurchaseOrder, PurchaseOrderLine

    po = PurchaseOrder(
        id=_u(), po_number=f"ZZTPO{_u()[:6]}".upper(),
        supplier_id=supplier.id if supplier else None,
        issue_date=issue_date, status=status, company_id=SORENTO_COMPANY_ID,
    )
    db.add(po)
    db.flush()
    line = PurchaseOrderLine(
        id=_u(), purchase_order_id=po.id, product_id=product.id, warehouse_id=warehouse.id,
        qty_ordered=Decimal("10"), qty_received=Decimal("0"), line_status="open",
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(line)
    db.flush()
    return po, line


def _category(db, code: str):
    from app.models.product import ProductCategory

    row = ProductCategory(id=_u(), category_code=code, category_name=f"{code} category")
    db.add(row)
    db.flush()
    return row


def _product_with_category(db, code: str, category_id):
    from app.models.product import Product

    uom_id = db.execute(
        text("SELECT base_uom_id FROM products WHERE base_uom_id IS NOT NULL LIMIT 1")
    ).scalar()
    row = Product(
        id=_u(), product_code=code, product_name=f"{code} basin", category_id=category_id,
        base_uom_id=uom_id, list_price=Decimal("10.00"), company_id=SORENTO_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def test_cutoff_drops_demand_due_after_it(scm_app):
    """AC-1/R14: `date_to` on the SALES ORDER's required date drops every line due after
    it - not folded into TBA, not shown at all. A product whose only open line falls after
    `date_to` owes nothing under it, so `only_debt=true` drops the whole row, and the axis
    (a property of the filtered set) never reaches into a month nothing in it needs. `cutoff`
    is REMOVED (R14), not aliased - passing it does nothing."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    inside = _product(db, f"{marker}-INSIDE")
    outside = _product(db, f"{marker}-OUTSIDE")
    _demand(
        db, inside, warehouse, qty=10, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SO1",
    )
    _demand(
        db, outside, warehouse, qty=10, required_date=date(2026, 12, 5),
        so_number=f"{marker}-SO2",
    )
    # AC-1 (reviewer round): supply landing well past date_to is still supply (AC-3) and
    # would otherwise stretch a row's own `months` past it - the axis cap must clamp the
    # COLUMN, not merely rely on there being no later demand to draw one out.
    _spo(db, inside, warehouse, qty=5, arrives=date(2027, 1, 15))
    db.flush()

    with TestClient(app) as c:
        body = c.get(
            BASE, params={"query": marker, "date_to": "2026-11-30", "only_debt": True}
        ).json()

    codes = [row["product_code"] for row in body["data"]]
    assert inside.product_code in codes
    assert outside.product_code not in codes
    assert body["months"][-1] == "2026-11"


def test_date_from_drops_demand_due_before_it(scm_app):
    """AC-1b/R14: `date_from` drops every line due BEFORE it, the mirror of `date_to`. A
    line due 20 Oct 2026 is dropped under `date_from=2026-11-01`, and the axis (a property
    of the whole filtered set) STARTS at `date_from`'s month rather than the current
    month - `max(current month, date_from's month)`, so a range that starts in the future
    does not draw an empty run of months nothing in it needs."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    early = _product(db, f"{marker}-EARLY")
    late = _product(db, f"{marker}-LATE")
    _demand(
        db, early, warehouse, qty=10, required_date=date(2026, 10, 20),
        so_number=f"{marker}-SO1",
    )
    _demand(
        db, late, warehouse, qty=10, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SO2",
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(
            BASE, params={"query": marker, "date_from": "2026-11-01", "only_debt": True}
        ).json()

    codes = [row["product_code"] for row in body["data"]]
    assert early.product_code not in codes
    assert late.product_code in codes
    assert body["months"][0] == "2026-11"


def test_cutoff_keeps_undated_and_unlocated(scm_app):
    """AC-2/A3/R14: a `date_from`/`date_to` range has no date to test an undated or
    unlocated line against, so both survive it unchanged; TBA reads 0 once the policy's
    `tba_date_from` (2029-01-01 by default) sits after `date_to`, because every TBA line
    is dated on or after it."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _demand(
        db, product, warehouse, qty=12, required_date=None, so_number=f"{marker}-NODATE"
    )
    _order, unlocated_line = _demand(
        db, product, warehouse, qty=15, required_date=date(2026, 11, 10),
        so_number=f"{marker}-NOWHERE",
    )
    unlocated_line.warehouse_id = None
    _demand(
        db, product, warehouse, qty=60, required_date=date(2029, 6, 1),
        so_number=f"{marker}-TBA",
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(
            BASE, params={"query": marker, "date_to": "2026-11-30", "only_debt": False}
        ).json()

    row = _row_of(body, product.product_code)
    assert row["undated"] == -12
    assert row["unlocated"] == -15
    assert row["tba"] == 0


def test_cutoff_supply_after_due_still_covers(scm_app):
    """AC-3/A2/R14: a line due 10 Nov and an SPO landing 20 Nov. R37 books the shortfall in
    the line's OWN month, and R45 (30 Sep 2026) leaves the unlinked SPO free in its own
    month (`test_supply_arriving_after_the_debt_is_spare_in_its_own_month` proves the same
    rule), so November reads -20 + 20 = 0 whether or not `date_to` is applied. AC-3's
    actual claim: `date_to` must not change how the walk ASSIGNS - the range leaves the
    Nov balance and the line exactly as they are without one."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _demand(
        db, product, warehouse, qty=20, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SO1",
    )
    _spo(db, product, warehouse, qty=20, arrives=date(2026, 11, 20))
    db.flush()

    with TestClient(app) as c:
        with_date_to = c.get(
            BASE, params={"query": marker, "date_to": "2026-11-30", "only_debt": False}
        ).json()
        without_date_to = c.get(
            BASE, params={"query": marker, "only_debt": False}
        ).json()
        cell = c.get(
            f"{BASE}/{product.id}/cell",
            params={"month": "2026-11", "date_to": "2026-11-30"},
        ).json()

    balances_with = {
        m["key"]: m["balance"]
        for m in _row_of(with_date_to, product.product_code)["months"]
    }
    balances_without = {
        m["key"]: m["balance"]
        for m in _row_of(without_date_to, product.product_code)["months"]
    }
    assert balances_with["2026-11"] == 0
    assert balances_without["2026-11"] == 0
    assert balances_with["2026-11"] == balances_without["2026-11"]

    assert len(cell["demand"]) == 1
    line = cell["demand"][0]
    assert line["so_number"] == f"{marker}-SO1"
    assert line["status"] == "short"
    assert line["assigned_qty"] == 0
    assert line["short_qty"] == 20
    [spo_row] = cell["supply"]
    assert spo_row["free_qty"] == 20


def test_supplier_filter_reads_newest_po_line(scm_app):
    """AC-4/A1/R15: the LAST supplier is the one on the product's newest PURCHASE ORDER
    line - newest by issue date - never the SPO's, whatever it names. A product with no PO
    falls back to its primary-flagged product supplier; a product with neither files under
    `supplier_ids=none`. `supplier_ids` is a MULTI select (R15): a product matches when its
    last supplier is ANY of the values passed, and `none` is one more value alongside real
    ids, not a sentinel that excludes them."""
    app, db = _client(scm_app)
    from app.models.procurement import ProductSupplier

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    s1 = _supplier(db, f"ZZTS1{_u()[:5]}".upper())
    s2 = _supplier(db, f"ZZTS2{_u()[:5]}".upper())
    s3 = _supplier(db, f"ZZTS3{_u()[:5]}".upper())
    s4 = _supplier(db, f"ZZTS4{_u()[:5]}".upper())
    primary = _supplier(db, f"ZZTSP{_u()[:5]}".upper())

    product_a = _product(db, f"{marker}-A")
    _demand(
        db, product_a, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SOA",
    )
    _po(db, product_a, warehouse, s2, issue_date=date(2026, 1, 1))
    _po(db, product_a, warehouse, s1, issue_date=date(2026, 6, 1))
    # AC-4 (reviewer round): a NEWER PO than S1's own, but CANCELLED - A1 names the
    # PO's own status, so a cancelled document must not outrank the newest LIVE one
    # however recent it is.
    _po(db, product_a, warehouse, s4, issue_date=date(2026, 9, 1), status="cancelled")
    spo_row = _spo(
        db, product_a, warehouse, qty=5, arrives=date(2026, 11, 5),
        spo_number=f"{marker}-SPO",
    )
    spo_row.supplier_id = s3.id

    product_b = _product(db, f"{marker}-B")
    _demand(
        db, product_b, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SOB",
    )
    db.add(
        ProductSupplier(
            id=_u(), product_id=product_b.id, supplier_id=primary.id,
            standard_lead_time_days=7, is_primary_supplier=True,
            company_id=SORENTO_COMPANY_ID,
        )
    )

    product_c = _product(db, f"{marker}-C")
    _demand(
        db, product_c, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SOC",
    )

    # A fourth product whose own LAST supplier is s2 - the "third, dropped" product for
    # the multi-select case below (s2 is otherwise only the OLDER, outranked PO on
    # product_a, never anyone's actual last supplier until now).
    product_d = _product(db, f"{marker}-D")
    _demand(
        db, product_d, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SOD",
    )
    _po(db, product_d, warehouse, s2, issue_date=date(2026, 1, 1))
    db.flush()

    with TestClient(app) as c:
        under_s1 = c.get(
            BASE, params={"query": marker, "supplier_ids": [s1.id], "only_debt": False}
        ).json()
        under_s2 = c.get(
            BASE, params={"query": marker, "supplier_ids": [s2.id], "only_debt": False}
        ).json()
        under_s3 = c.get(
            BASE, params={"query": marker, "supplier_ids": [s3.id], "only_debt": False}
        ).json()
        under_primary = c.get(
            BASE,
            params={"query": marker, "supplier_ids": [primary.id], "only_debt": False},
        ).json()
        under_none = c.get(
            BASE, params={"query": marker, "supplier_ids": ["none"], "only_debt": False}
        ).json()
        # R15: two ids selected together keeps BOTH their products, and drops the third
        # (product_d, last supplier s2, which is in neither set).
        under_multi = c.get(
            BASE,
            params={
                "query": marker, "supplier_ids": [s1.id, primary.id],
                "only_debt": False,
            },
        ).json()
        # R15: `none` alongside a real id keeps BOTH the no-supplier product and that
        # id's product.
        under_none_and_s1 = c.get(
            BASE,
            params={
                "query": marker, "supplier_ids": ["none", s1.id], "only_debt": False,
            },
        ).json()

    assert [r["product_code"] for r in under_s1["data"]] == [product_a.product_code]
    assert product_a.product_code not in [r["product_code"] for r in under_s2["data"]]
    assert product_a.product_code not in [r["product_code"] for r in under_s3["data"]]
    assert [r["product_code"] for r in under_primary["data"]] == [product_b.product_code]
    assert [r["product_code"] for r in under_none["data"]] == [product_c.product_code]

    assert sorted(r["product_code"] for r in under_multi["data"]) == sorted(
        [product_a.product_code, product_b.product_code]
    )
    assert product_d.product_code not in [r["product_code"] for r in under_multi["data"]]

    assert sorted(r["product_code"] for r in under_none_and_s1["data"]) == sorted(
        [product_a.product_code, product_c.product_code]
    )


def test_last_supplier_stays_within_company_scope(scm_app):
    """AC-4b - a REGRESSION GUARD, not a red test. A security review raised the concern
    that `_last_supplier_map`'s window query, wrapped in `.subquery()`, could lose the
    company-scope predicate and let a newer purchase-order line from ANOTHER company
    outrank the caller's own. Measured 24 Sep 2026 with a real two-company seed (a fresh
    `Company` row, a `PurchaseOrder`/`PurchaseOrderLine` explicitly stamped to it) run
    through the real `scm_app` harness, SQL traced via `before_cursor_execute`: on this
    SQLAlchemy version, `do_orm_execute`'s `with_loader_criteria(..., include_aliases=
    True)` DOES reach into the window subquery - the compiled SQL carries
    `purchase_orders.company_id IN (...)` inside the JOIN, in both directions tried
    (the other company owning the PO and its line; the other company owning only the
    PO with the line re-stamped to the caller's own company). `_last_supplier_map`
    already excludes the other company's newer PO correctly, so this test is GREEN
    today - it exists to catch a REGRESSION (a SQLAlchemy upgrade, or a rewritten query
    shape, that stops threading the criteria through the subquery), at which point an
    explicit `company_id` predicate would need adding to the query itself and this test
    would go red until it did.
    """
    app, db = _client(scm_app)
    from app.models.company import Company
    from app.models.procurement import PurchaseOrder, PurchaseOrderLine

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _demand(
        db, product, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SO1",
    )

    supplier_s = _supplier(db, f"ZZTS{_u()[:6]}".upper())

    other_company = Company(
        id=_u(), code=f"ZZT{_u()[:8]}".upper()[:20], name=f"{marker} other company",
    )
    db.add(other_company)
    db.flush()
    supplier_other = _supplier(db, f"ZZTOTH{_u()[:5]}".upper())
    # Explicitly stamped to the OTHER company - the auto-stamp leaves an already-set
    # `company_id` alone (`app.services.company_scope._stamp_scope`), so this persists
    # under `other_company` even though the active session scope is Sorento.
    supplier_other.company_id = other_company.id
    db.flush()

    # The caller's OWN, older PO - the answer this guard says must win.
    po_own = PurchaseOrder(
        id=_u(), po_number=f"ZZTPO{_u()[:6]}".upper(), supplier_id=supplier_s.id,
        issue_date=date(2026, 1, 1), status="active", company_id=SORENTO_COMPANY_ID,
    )
    db.add(po_own)
    db.flush()
    db.add(
        PurchaseOrderLine(
            id=_u(), purchase_order_id=po_own.id, product_id=product.id,
            warehouse_id=warehouse.id, qty_ordered=Decimal("10"),
            qty_received=Decimal("0"), line_status="open",
            company_id=SORENTO_COMPANY_ID,
        )
    )

    # A NEWER PO owned by the OTHER company, naming the SAME product - invisible to the
    # caller's scope, and yet a broken window query could rank it first anyway.
    po_other = PurchaseOrder(
        id=_u(), po_number=f"ZZTPO{_u()[:6]}".upper(), supplier_id=supplier_other.id,
        issue_date=date(2026, 6, 1), status="active", company_id=other_company.id,
    )
    db.add(po_other)
    db.flush()
    db.add(
        PurchaseOrderLine(
            id=_u(), purchase_order_id=po_other.id, product_id=product.id,
            warehouse_id=warehouse.id, qty_ordered=Decimal("10"),
            qty_received=Decimal("0"), line_status="open", company_id=other_company.id,
        )
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    row = _row_of(body, product.product_code)
    assert row["supplier_id"] == str(supplier_s.id), (
        "the other company's newer PO must not outrank the caller's own"
    )
    assert row["supplier_name"] == supplier_s.supplier_name
    assert body["suppliers"] == [{"id": str(supplier_s.id), "name": supplier_s.supplier_name}]


def test_row_carries_supplier_category_total(scm_app):
    """AC-5/A5/R17: every row carries `supplier_id`, `supplier_name`, `category_code` and
    a `total` that sums the row's own months plus `tba` ONLY - `undated` and `unlocated`
    are dropped from `total` (R17, since the "No date" and "No location" columns leave
    the screen and the workbook); the row still carries `undated`/`unlocated` themselves,
    unchanged. This test seeds an undated line specifically so a `total` that still folds
    it in is caught."""
    app, db = _client(scm_app)
    from app.models.procurement import ProductSupplier

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    supplier = _supplier(db, f"ZZTSUP{_u()[:5]}".upper())
    product = _product(db, f"{marker}-A")
    db.add(
        ProductSupplier(
            id=_u(), product_id=product.id, supplier_id=supplier.id,
            standard_lead_time_days=7, is_primary_supplier=True,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    _demand(
        db, product, warehouse, qty=10, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SO1",
    )
    _demand(
        db, product, warehouse, qty=5, required_date=None, so_number=f"{marker}-NODATE"
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    row = _row_of(body, product.product_code)
    assert row["supplier_id"] == str(supplier.id)
    assert row["supplier_name"] == supplier.supplier_name
    assert row["category_code"] == _REF_CATEGORY_CODE
    assert row["undated"] == -5, "undated itself is unchanged by R17"
    expected_total = sum(m["balance"] for m in row["months"]) + row["tba"]
    assert row["total"] == expected_total


def test_totals_are_whole_set_and_stable_across_pages(scm_app):
    """AC-6/A6/R17: the envelope's `totals` sum the WHOLE filtered set, not the page, so a
    reader turning the page sees the same totals twice; `totals.total` sums months + tba
    ONLY (R17), never `undated`/`unlocated` - one of the three seeded products carries an
    undated line specifically so a `totals.total` that still folds it in is caught."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    codes = [f"{marker}-A", f"{marker}-B", f"{marker}-C"]
    for index, code in enumerate(codes):
        product = _product(db, code)
        _demand(
            db, product, warehouse, qty=10 + index, required_date=date(2026, 11, 10),
            so_number=f"{marker}-SO{code[-1]}",
        )
        if index == 0:
            _demand(
                db, product, warehouse, qty=3, required_date=None,
                so_number=f"{marker}-SO{code[-1]}-NODATE",
            )
    db.flush()

    with TestClient(app) as c:
        whole = c.get(
            BASE, params={"query": marker, "only_debt": False, "limit": 50}
        ).json()
        page1 = c.get(
            BASE, params={"query": marker, "only_debt": False, "page": 1, "limit": 2}
        ).json()
        page2 = c.get(
            BASE, params={"query": marker, "only_debt": False, "page": 2, "limit": 2}
        ).json()

    expected_month_totals: dict = {}
    expected_tba = expected_undated = expected_unlocated = expected_total = 0.0
    for row in whole["data"]:
        for month in row["months"]:
            expected_month_totals[month["key"]] = (
                expected_month_totals.get(month["key"], 0.0) + month["balance"]
            )
        expected_tba += row["tba"]
        expected_undated += row["undated"]
        expected_unlocated += row["unlocated"]
        expected_total += sum(m["balance"] for m in row["months"]) + row["tba"]

    for body in (page1, page2):
        totals = body["totals"]
        assert totals["months"] == expected_month_totals
        assert totals["tba"] == expected_tba
        assert totals["undated"] == expected_undated
        assert totals["unlocated"] == expected_unlocated
        assert totals["total"] == expected_total
    assert page1["totals"] == page2["totals"]


def test_suppliers_on_envelope_distinct_sorted_by_name(scm_app):
    """AC-7: `suppliers` on the envelope is the distinct `{id, name}` of the filtered
    set's LAST suppliers, sorted by name - a product with no supplier at all contributes
    nothing to the list."""
    app, db = _client(scm_app)
    from app.models.procurement import ProductSupplier

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    supplier_z = _supplier(db, f"ZZTSZZ{_u()[:4]}".upper())
    supplier_z.supplier_name = "Zeta supplier"
    supplier_a = _supplier(db, f"ZZTSAA{_u()[:4]}".upper())
    supplier_a.supplier_name = "Alpha supplier"
    db.flush()

    product_z = _product(db, f"{marker}-Z")
    db.add(
        ProductSupplier(
            id=_u(), product_id=product_z.id, supplier_id=supplier_z.id,
            standard_lead_time_days=7, is_primary_supplier=True,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    _demand(
        db, product_z, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SOZ",
    )

    product_y = _product(db, f"{marker}-Y")
    db.add(
        ProductSupplier(
            id=_u(), product_id=product_y.id, supplier_id=supplier_a.id,
            standard_lead_time_days=7, is_primary_supplier=True,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    _demand(
        db, product_y, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SOY",
    )

    product_none = _product(db, f"{marker}-N")
    _demand(
        db, product_none, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SON",
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        under_s1 = c.get(
            BASE,
            params={
                "query": marker, "only_debt": False,
                "supplier_ids": [str(supplier_a.id)],
            },
        ).json()

    assert body["suppliers"] == [
        {"id": str(supplier_a.id), "name": "Alpha supplier"},
        {"id": str(supplier_z.id), "name": "Zeta supplier"},
    ]

    # AC-7c (reviewer round): `suppliers` is computed off the set BEFORE the
    # `supplier_ids` filter narrows it, so the select can switch supplier without first
    # clearing itself - narrowing to Alpha must not make Zeta vanish from the options,
    # even though Zeta's own product is dropped from `data`. Every entry keeps a real
    # name, never an id standing in for one.
    assert [r["product_code"] for r in under_s1["data"]] == [product_y.product_code]
    assert under_s1["suppliers"] == body["suppliers"]
    for entry in under_s1["suppliers"]:
        assert entry["name"], f"{entry['id']} has no name"


def test_sheet_counts_exact_with_none_buckets(scm_app):
    """AC-7b: `sheet_counts` states exactly how many sheets an export of the CURRENT
    filtered set would produce, none-buckets included - and it rides the envelope, so it
    is the same on every page."""
    app, db = _client(scm_app)
    from app.models.procurement import ProductSupplier

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    cat_x = _category(db, f"{marker}-CATX")
    cat_y = _category(db, f"{marker}-CATY")
    supplier_1 = _supplier(db, f"ZZTS1{_u()[:5]}".upper())
    supplier_2 = _supplier(db, f"ZZTS2{_u()[:5]}".upper())

    p1 = _product_with_category(db, f"{marker}-P1", cat_x.id)
    db.add(
        ProductSupplier(
            id=_u(), product_id=p1.id, supplier_id=supplier_1.id,
            standard_lead_time_days=7, is_primary_supplier=True,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    _demand(
        db, p1, warehouse, qty=5, required_date=date(2026, 11, 10), so_number=f"{marker}-SO1"
    )

    p2 = _product_with_category(db, f"{marker}-P2", cat_y.id)
    db.add(
        ProductSupplier(
            id=_u(), product_id=p2.id, supplier_id=supplier_2.id,
            standard_lead_time_days=7, is_primary_supplier=True,
            company_id=SORENTO_COMPANY_ID,
        )
    )
    _demand(
        db, p2, warehouse, qty=5, required_date=date(2026, 11, 10), so_number=f"{marker}-SO2"
    )

    # `products.category_id` is NOT NULL (the FK is mandatory), so "no category" is
    # represented the same way `low_stock_report_service._master_map` already treats it -
    # a BLANK `category_code` - rather than a null FK, which the schema does not allow.
    no_category = _category(db, "")
    p3 = _product_with_category(db, f"{marker}-P3", no_category.id)
    _demand(
        db, p3, warehouse, qty=5, required_date=date(2026, 11, 10), so_number=f"{marker}-SO3"
    )
    db.flush()

    with TestClient(app) as c:
        page1 = c.get(
            BASE, params={"query": marker, "only_debt": False, "page": 1, "limit": 2}
        ).json()
        page2 = c.get(
            BASE, params={"query": marker, "only_debt": False, "page": 2, "limit": 2}
        ).json()

    # 2 named suppliers + "No supplier" (p3) = 3; 2 named categories + "No category"
    # (p3) = 3; distinct (supplier, category) pairs present: (s1, catx), (s2, caty),
    # (None, None) = 3.
    for body in (page1, page2):
        assert body["sheet_counts"] == {"supplier": 3, "category": 3, "supplier_category": 3}


def test_book_all_project_retail(scm_app):
    """AC-8/R1/A4: `book=all` (the new default) spans flagged project bins AND site
    pools - a line at each shows on the SAME product row, and pool stock covers only the
    pool line while project stock covers only the project line. `book=project` reproduces
    the OLD flagged-bins-only view (the pool-booked line is out of that span entirely, not
    even as unlocated); `book=retail` shows pool demand and supply only and ignores
    `group`."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    pool = _warehouse(db, f"ZZTPOOL{_u()[:5]}", planning=False)
    bb.pool_warehouse_id = pool.id
    db.flush()
    product = _product(db, f"{marker}-A")
    due = date(2026, 11, 10)
    _demand(db, product, bb, qty=30, required_date=due, so_number=f"{marker}-SO-PROJECT")
    _demand(db, product, pool, qty=20, required_date=due, so_number=f"{marker}-SO-POOL")
    _stock(db, product, pool, 500)
    db.flush()

    with TestClient(app) as c:
        book_all = c.get(
            BASE, params={"query": marker, "book": "all", "only_debt": False}
        ).json()
        today_default = c.get(BASE, params={"query": marker, "only_debt": False}).json()
        book_project = c.get(
            BASE, params={"query": marker, "book": "project", "only_debt": False}
        ).json()
        book_retail = c.get(
            BASE, params={"query": marker, "book": "retail", "only_debt": False}
        ).json()
        book_retail_grouped = c.get(
            BASE,
            params={
                "query": marker, "book": "retail", "group": "BB", "only_debt": False,
            },
        ).json()

    # The new default (no `book` at all) is `book=all`.
    assert today_default == book_all

    row_all = _row_of(book_all, product.product_code)
    balances_all = {m["key"]: m["balance"] for m in row_all["months"]}
    # The project line is short its whole 30 (no stock in the project span); the pool's
    # 500 less the 20 its own line drew is spare in the CURRENT month, never the
    # project's.
    assert balances_all[month_key(due)] == -30
    assert balances_all[month_key(TODAY)] == 480

    row_project = _row_of(book_project, product.product_code)
    balances_project = {m["key"]: m["balance"] for m in row_project["months"]}
    # `book=project`: the pool-booked line is out of this span entirely (a named bin the
    # span does not hold, not "no bin"), so only the project shortfall shows.
    assert balances_project[month_key(due)] == -30
    assert balances_project[month_key(TODAY)] == 0

    row_retail = _row_of(book_retail, product.product_code)
    balances_retail = {m["key"]: m["balance"] for m in row_retail["months"]}
    assert balances_retail[month_key(due)] == 0
    assert balances_retail[month_key(TODAY)] == 480
    assert book_retail_grouped == book_retail


def test_pool_seal_both_directions(scm_app):
    """AC-8b (reviewer round): the previous test's project bin carries a `-BB` suffix, so
    a naive "different group label" filter would ALSO keep the two piles apart - it does
    not prove the POOL_GROUP seal itself. Here the project bin carries NO suffix at all
    (`group_of_warehouse_code` returns `None` for it, same as the pool's own code), so the
    only thing that can be separating them is `assign()`'s `is_pool` flag / POOL_GROUP
    branch. Proven in BOTH directions: stock at the pool never covers the project line,
    and stock at the project bin never covers the pool line."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    # No hyphen - `group_of_warehouse_code` returns None, exactly like the pool's own
    # code does. Any separation observed cannot be "different group suffix".
    project_bin = _warehouse(db, f"ZZTBRW{_u()[:8]}".upper())
    pool = _warehouse(db, f"ZZTPOOL{_u()[:5]}", planning=False)
    project_bin.pool_warehouse_id = pool.id
    db.flush()
    due = date(2026, 11, 10)

    # First half: stock only at the pool.
    product_a = _product(db, f"{marker}-A")
    _demand(
        db, product_a, project_bin, qty=30, required_date=due,
        so_number=f"{marker}-SO-PROJECT-A",
    )
    _demand(
        db, product_a, pool, qty=20, required_date=due, so_number=f"{marker}-SO-POOL-A",
    )
    _stock(db, product_a, pool, 500)

    # Second half: stock only at the project bin.
    product_b = _product(db, f"{marker}-B")
    _demand(
        db, product_b, project_bin, qty=30, required_date=due,
        so_number=f"{marker}-SO-PROJECT-B",
    )
    _demand(
        db, product_b, pool, qty=20, required_date=due, so_number=f"{marker}-SO-POOL-B",
    )
    _stock(db, product_b, project_bin, 500)
    db.flush()

    with TestClient(app) as c:
        body = c.get(
            BASE, params={"query": marker, "book": "all", "only_debt": False}
        ).json()

    row_a = _row_of(body, product_a.product_code)
    balances_a = {m["key"]: m["balance"] for m in row_a["months"]}
    # Pool has the stock: the project line stays short its whole 30, the pool line is
    # covered (its own month owes nothing).
    assert balances_a[month_key(due)] == -30

    row_b = _row_of(body, product_b.product_code)
    balances_b = {m["key"]: m["balance"] for m in row_b["months"]}
    # Project bin has the stock: the pool line stays short its whole 20, the project
    # line is covered - so the ONLY thing left owed in the line's own month is the
    # pool's 20.
    assert balances_b[month_key(due)] == -20


def test_product_name_null_when_equal_to_code(scm_app):
    """AC-9/R8: `product_name` is null when it equals `product_code`, case-sensitively
    and trimmed of surrounding whitespace; a genuinely different name still prints."""
    app, db = _client(scm_app)
    from app.models.product import Product

    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    category_id, uom_id = db.execute(
        text(
            "SELECT category_id, base_uom_id FROM products "
            "WHERE category_id IS NOT NULL AND base_uom_id IS NOT NULL LIMIT 1"
        )
    ).first()

    equal_code = f"{marker}-EQ"
    padded_code = f"{marker}-PAD"
    different_code = f"{marker}-DIFF"
    same = Product(
        id=_u(), product_code=equal_code, product_name=equal_code,
        category_id=category_id, base_uom_id=uom_id, list_price=Decimal("10"),
        company_id=SORENTO_COMPANY_ID,
    )
    padded = Product(
        id=_u(), product_code=padded_code, product_name=f" {padded_code} ",
        category_id=category_id, base_uom_id=uom_id, list_price=Decimal("10"),
        company_id=SORENTO_COMPANY_ID,
    )
    different = Product(
        id=_u(), product_code=different_code, product_name=f"{different_code} basin",
        category_id=category_id, base_uom_id=uom_id, list_price=Decimal("10"),
        company_id=SORENTO_COMPANY_ID,
    )
    db.add_all([same, padded, different])
    db.flush()
    for product in (same, padded, different):
        _demand(
            db, product, warehouse, qty=5, required_date=date(2026, 11, 10),
            so_number=f"{marker}-SO-{product.product_code[-4:]}",
        )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    assert _row_of(body, equal_code)["product_name"] is None
    assert _row_of(body, padded_code)["product_name"] is None
    assert _row_of(body, different_code)["product_name"] == f"{different_code} basin"


def test_new_fields_survive_response_model(scm_app):
    """AC-10: every new field is declared on the schema and reaches the wire - a
    `response_model` silently drops what it does not declare."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    warehouse = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _demand(
        db, product, warehouse, qty=5, required_date=date(2026, 11, 10),
        so_number=f"{marker}-SO1",
    )
    db.flush()

    with TestClient(app) as c:
        body = c.get(BASE, params={"query": marker, "only_debt": False}).json()

    row = _row_of(body, product.product_code)
    for field in ("supplier_id", "supplier_name", "category_code", "total"):
        assert field in row, f"{field} missing from the row - response_model dropped it"
    for field in ("totals", "suppliers", "sheet_counts"):
        assert field in body, f"{field} missing from the envelope - response_model dropped it"


def test_cell_drill_takes_cutoff_and_book(scm_app):
    """AC-11/R14: the cell drill accepts `date_from`/`date_to` and `book`, and its lines
    foot with them - a line dropped by the range is not listed, and `book=retail` lists
    the pool line only."""
    app, db = _client(scm_app)
    marker = f"ZZTSD{_u()[:6]}".upper()
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    pool = _warehouse(db, f"ZZTPOOL{_u()[:5]}", planning=False)
    bb.pool_warehouse_id = pool.id
    db.flush()
    product = _product(db, f"{marker}-A")
    due = date(2026, 11, 10)
    after_range = date(2026, 12, 5)
    _demand(db, product, bb, qty=10, required_date=due, so_number=f"{marker}-SO-INSIDE")
    _demand(
        db, product, bb, qty=10, required_date=after_range,
        so_number=f"{marker}-SO-OUTSIDE",
    )
    _demand(db, product, pool, qty=5, required_date=due, so_number=f"{marker}-SO-POOL")
    db.flush()

    with TestClient(app) as c:
        date_to_cell = c.get(
            f"{BASE}/{product.id}/cell",
            params={"month": month_key(after_range), "date_to": "2026-11-30"},
        ).json()
        retail_cell = c.get(
            f"{BASE}/{product.id}/cell",
            params={"month": month_key(due), "book": "retail"},
        ).json()

    assert date_to_cell["demand"] == []
    assert [line["so_number"] for line in retail_cell["demand"]] == [
        f"{marker}-SO-POOL"
    ]
