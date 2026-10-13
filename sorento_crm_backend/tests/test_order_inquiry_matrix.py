"""S3 - the Schedule matrix, its own read (R-I second half, AC-X1 to AC-X4).

`GET /order-inquiries/matrix?axis=product|sales_order|customer|agent&by=day|week|month|year`
does not exist yet - every test here 404s until the coder builds it. The route is
declared nowhere in `app/api/v1/projects/order_inquiries.py` today (grepped before writing
this file), so a 404 IS the honest red state, not a fixture bug.

Postgres only, via `tests/_pg_fixture.py::blank_session`.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.order import Customer, SalesOrder
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, SPOAllocation, Supplier
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_RAISED,
    IV_ORDER,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.sales_agent import SalesAgent
from app.models.user import User

from ._pg_fixture import blank_session

MARKER = "zzt-oi-matrix"
BASE = "/api/v1/project-sales"
MATRIX = f"{BASE}/order-inquiries/matrix"
#: The LIST, for the cell drilldown's own `axis`/`axis_key` pair - the matrix names a
#: cell, the list is what opens it.
LIST = f"{BASE}/order-inquiries"

READ_ONLY = ["projects.projects.view"]
NO_GRANTS: list[str] = []


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, code: str, name: str) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(), product_code=code, product_name=name, category_id=category.id,
        base_uom_id=uom.id, list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _customer(db, company_id: str, name: str) -> Customer:
    row = Customer(id=_uid(), company_id=company_id, customer_code=f"ZZT-{_uid()[:8]}",
                    customer_name=name)
    db.add(row)
    db.flush()
    return row


def _agent(db, company_id: str, code: str, label: str) -> SalesAgent:
    row = SalesAgent(id=_uid(), company_id=company_id, sales_agent=code, person_label=label)
    db.add(row)
    db.flush()
    return row


def _core_order(db, company_id: str, *, customer: Customer, agent: SalesAgent,
                 order_date: date) -> SalesOrder:
    row = SalesOrder(
        id=_uid(), company_id=company_id, so_number=f"ZZTSO{_uid()[:8]}",
        customer_id=customer.id, sales_agent_id=agent.id, order_date=order_date,
    )
    db.add(row)
    db.flush()
    return row


def _adopted_pso(db, company_id: str, core: SalesOrder) -> ProjectSalesOrder:
    row = ProjectSalesOrder(
        id=_uid(), company_id=company_id, project_id=None, so_id=core.id,
        provisional_ref=core.so_number, autocount_doc_no=core.so_number, status="adopted",
    )
    db.add(row)
    db.flush()
    return row


def _pso_line(db, company_id: str, pso: ProjectSalesOrder, product: Product) -> ProjectSalesOrderLine:
    row = ProjectSalesOrderLine(
        id=_uid(), company_id=company_id, project_sales_order_id=pso.id, line_no=1,
        product_id=product.id, description=f"{MARKER} line", qty=Decimal("999"),
        uom="UNIT", unit_price=Decimal("10.00"), amount=Decimal("0"),
        delivery_date=date(2026, 1, 1),
    )
    db.add(row)
    db.flush()
    return row


def _inquiry_for(db, company_id: str, pso: ProjectSalesOrder) -> OrderInquiry:
    row = OrderInquiry(id=_uid(), company_id=company_id, project_sales_order_id=pso.id,
                        state=INQUIRY_RAISED)
    db.add(row)
    db.flush()
    return row


def _row(db, company_id: str, inquiry: OrderInquiry, so_line: ProjectSalesOrderLine, *,
          item_code: str, qty: str, delivery_date: date, stock_location=None,
          state: str = INQUIRY_RAISED) -> OrderInquiryRow:
    row = OrderInquiryRow(
        id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id,
        so_line_id=so_line.id, item_code=item_code, qty=Decimal(str(qty)),
        delivery_date=delivery_date, verb=IV_ORDER, state=state,
        ack_state="acknowledged", stock_location=stock_location,
    )
    db.add(row)
    db.flush()
    return row


def _spo_link(db, company_id: str, row: OrderInquiryRow, product: Product, qty: str):
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}",
        allocated_quantity=int(Decimal(str(qty))), product_id=product.id,
    )
    db.add(allocation)
    db.flush()
    db.add(OrderInquiryLink(id=_uid(), company_id=company_id, row_id=row.id,
                             spo_allocation_id=allocation.id, document=allocation.spo_number,
                             qty=Decimal(str(qty))))
    db.flush()


def _po_link(db, company_id: str, row: OrderInquiryRow, product: Product, qty: str):
    supplier = Supplier(id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
                         supplier_name=f"{MARKER} supplier")
    db.add(supplier)
    db.flush()
    po = PurchaseOrder(id=_uid(), company_id=company_id, po_number=f"ZZT-PO-{_uid()[:8]}",
                        supplier_id=supplier.id)
    db.add(po)
    db.flush()
    line = PurchaseOrderLine(id=_uid(), company_id=company_id, purchase_order_id=po.id,
                              product_id=product.id, qty_ordered=Decimal("999"),
                              source_ref=f"{po.po_number}:1")
    db.add(line)
    db.flush()
    db.add(OrderInquiryLink(id=_uid(), company_id=company_id, row_id=row.id,
                             po_line_id=line.id, document=po.po_number, qty=Decimal(str(qty))))
    db.flush()
    return po


def _open_spo_allocation(db, company_id: str, *, spo_number: str, from_po_number: str,
                           product: Product, allocated_quantity: int):
    """An OPEN SPO allocation matching a row's own PO link (from_po_number + the line
    `_po_link` seeds + product), never itself linked to any row - the "derived cover"
    AC-D11/AC-X6 measure. OI-SPO-EXACT: it names the PO LINE, as AutoCount's do."""
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=spo_number,
        allocated_quantity=allocated_quantity, quantity_received=0, product_id=product.id,
        from_po_number=from_po_number, from_po_line_ref=f"{from_po_number}:1",
        line_status="open", receipt_status="pending",
    )
    db.add(allocation)
    db.flush()
    return allocation


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


def _seed(db, company_id: str) -> dict:
    """A small, richly distinguishable world (week/year bucketing, two axes' worth of
    grouping, a location split, a PO-linked row) plus a separate isolated pair for the
    year bucket, so no test's arithmetic depends on another test's rows."""
    customer_a = _customer(db, company_id, f"{MARKER} Alpha Sdn Bhd")
    customer_b = _customer(db, company_id, f"{MARKER} Beta Sdn Bhd")
    agent_a = _agent(db, company_id, f"ZZT{_uid()[:6].upper()}", f"{MARKER} Agent A")
    agent_b = _agent(db, company_id, f"ZZT{_uid()[:6].upper()}", f"{MARKER} Agent B")
    product_p = _product(db, f"ZZT-MATRIX-P-{_uid()[:6]}", f"{MARKER} product P")
    product_q = _product(db, f"ZZT-MATRIX-Q-{_uid()[:6]}", f"{MARKER} product Q")

    core_a = _core_order(db, company_id, customer=customer_a, agent=agent_a,
                          order_date=date(2026, 4, 1))
    core_b = _core_order(db, company_id, customer=customer_b, agent=agent_b,
                          order_date=date(2026, 4, 1))
    pso_a = _adopted_pso(db, company_id, core_a)
    pso_b = _adopted_pso(db, company_id, core_b)
    line_a = _pso_line(db, company_id, pso_a, product_p)
    line_b = _pso_line(db, company_id, pso_b, product_q)
    inquiry_a = _inquiry_for(db, company_id, pso_a)
    inquiry_b = _inquiry_for(db, company_id, pso_b)

    # 2026-04-15 is a Wednesday; 2026-04-13 is the Monday of the same ISO week.
    row_wed = _row(db, company_id, inquiry_a, line_a, item_code=f"{MARKER}-WED", qty="10",
                    delivery_date=date(2026, 4, 15), stock_location="ZZTLOCX")
    row_mon = _row(db, company_id, inquiry_a, line_a, item_code=f"{MARKER}-MON", qty="8",
                    delivery_date=date(2026, 4, 13), stock_location="ZZTLOCX")
    _po_link(db, company_id, row_mon, product_p, "8")
    row_other = _row(db, company_id, inquiry_b, line_b, item_code=f"{MARKER}-OTHER", qty="7",
                      delivery_date=date(2026, 4, 15), stock_location="ZZTLOCY")

    # Isolated year-bucket pair, far enough from everything else that no other
    # filter/test can accidentally sum it in - its OWN customer and agent too, or the
    # customer/agent axis tests above would count 4 rows under customer_a/agent_a
    # instead of the 2 (row_wed, row_mon) they mean to isolate.
    product_year = _product(db, f"ZZT-MATRIX-YEAR-{_uid()[:6]}", f"{MARKER} product year")
    core_year = _core_order(
        db, company_id, customer=_seeded_customer(db, company_id),
        agent=_seeded_agent(db, company_id), order_date=date(2029, 1, 1),
    )
    pso_year = _adopted_pso(db, company_id, core_year)
    line_year = _pso_line(db, company_id, pso_year, product_year)
    inquiry_year = _inquiry_for(db, company_id, pso_year)
    row_year_early = _row(db, company_id, inquiry_year, line_year, item_code=f"{MARKER}-YEARLY1",
                           qty="6", delivery_date=date(2029, 3, 10))
    row_year_late = _row(db, company_id, inquiry_year, line_year, item_code=f"{MARKER}-YEARLY2",
                          qty="9", delivery_date=date(2029, 11, 20))

    db.commit()
    return {
        "product_p": product_p,
        "product_q": product_q,
        "customer_a": customer_a,
        "customer_b": customer_b,
        "agent_a": agent_a,
        "agent_b": agent_b,
        "core_a": core_a,
        "row_wed": row_wed,
        "row_mon": row_mon,
        "row_other": row_other,
        "product_year": product_year,
        "row_year_early": row_year_early,
        "row_year_late": row_year_late,
    }


def _api(permissions):
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        user_id = _user(db, f"{MARKER} tester")
        seeded = _seed(db, company_id)
        client, originals = _client(db, user_id, permissions)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, seeded
        finally:
            _restore(originals)


@pytest.fixture()
def api():
    yield from _api(READ_ONLY)


@pytest.fixture()
def stranger_api():
    yield from _api(NO_GRANTS)


# --------------------------------------------------------------------------- AC-X1


def test_matrix_by_product_and_month_sums_the_lists_own_qty_per_cell(api):
    client, _db, _company_id, seeded = api

    response = client.get(MATRIX, params={"axis": "product", "by": "week"})

    assert response.status_code == 200, response.text
    cells = response.json()["data"]
    cell = next(
        c for c in cells
        if c["axis_key"] == str(seeded["product_p"].id) and c["period"] == "2026-04-13"
    )
    assert cell["qty"] == "18"
    assert cell["rows"] == 2


# --------------------------------------------------------------------------- AC-X3


def test_week_buckets_start_on_monday(api):
    client, _db, _company_id, seeded = api

    body = client.get(MATRIX, params={"axis": "product", "by": "week"}).json()

    periods = {
        c["period"] for c in body["data"] if c["axis_key"] == str(seeded["product_p"].id)
    }
    # 2026-04-15 (Wednesday) has to land in the Monday-dated cell, never its own date.
    assert "2026-04-15" not in periods
    assert "2026-04-13" in periods


def test_month_and_year_buckets_start_on_the_first(api):
    client, _db, _company_id, seeded = api

    monthly = client.get(MATRIX, params={"axis": "product", "by": "month"}).json()["data"]
    yearly = client.get(MATRIX, params={"axis": "product", "by": "year"}).json()["data"]

    month_cell = next(
        c for c in monthly if c["axis_key"] == str(seeded["product_p"].id)
    )
    assert month_cell["period"] == "2026-04-01"

    year_cell = next(
        c for c in yearly if c["axis_key"] == str(seeded["product_year"].id)
    )
    assert year_cell["period"] == "2029-01-01"
    assert year_cell["qty"] == "15"
    assert year_cell["rows"] == 2


# --------------------------------------------------------------------------- AC-X4


def test_each_cell_carries_buy_po_and_spo_stage_sums(api):
    client, _db, _company_id, seeded = api

    body = client.get(MATRIX, params={"axis": "product", "by": "week"}).json()

    cell = next(
        c for c in body["data"]
        if c["axis_key"] == str(seeded["product_p"].id) and c["period"] == "2026-04-13"
    )
    # row_mon (qty 8) is fully on a purchase order line: po=8, buy=0.
    # row_wed (qty 10) is unlinked: buy=10, po=0.
    assert cell["po"] == "8"
    assert cell["buy"] == "10"
    assert cell["spo"] == "0"


# --------------------------------------------------------------------------- AC-X5


def test_the_matrix_excludes_cancelled_but_includes_actioned_rows(api):
    """AC-X5: a cancelled row's quantity is not owed any more and must not inflate a
    cell's `qty`, `rows` or stage sums - but an ACTIONED row (already answered
    somewhere else) still counts, unlike the `_kinds`/`kind=` reading which drops both
    (`_NOT_OWED_STATES`). One product, one month, three rows: cancelled (6, ignored),
    actioned and fully on an SPO (6, spo=6), raised and unlinked (4, buy=4)."""
    client, db, company_id, seeded = api

    product = _product(db, f"ZZT-MATRIX-X5-{_uid()[:6]}", f"{MARKER} product x5")
    core = _core_order(
        db, company_id, customer=seeded["customer_a"], agent=seeded["agent_a"],
        order_date=date(2026, 6, 1),
    )
    pso = _adopted_pso(db, company_id, core)
    line = _pso_line(db, company_id, pso, product)
    inquiry = _inquiry_for(db, company_id, pso)

    row_cancelled = _row(
        db, company_id, inquiry, line, item_code=f"{MARKER}-X5-CANCELLED", qty="6",
        delivery_date=date(2026, 6, 10), state=INQUIRY_CANCELLED,
    )
    row_actioned = _row(
        db, company_id, inquiry, line, item_code=f"{MARKER}-X5-ACTIONED", qty="6",
        delivery_date=date(2026, 6, 10), state=INQUIRY_ACTIONED,
    )
    _spo_link(db, company_id, row_actioned, product, "6")
    row_raised = _row(
        db, company_id, inquiry, line, item_code=f"{MARKER}-X5-RAISED", qty="4",
        delivery_date=date(2026, 6, 10), state=INQUIRY_RAISED,
    )
    db.commit()

    body = client.get(MATRIX, params={"axis": "product", "by": "month"}).json()
    cell = next(c for c in body["data"] if c["axis_key"] == str(product.id))

    assert cell["rows"] == 2, cell  # the cancelled row is not counted
    assert cell["qty"] == "10", cell  # 6 (actioned) + 4 (raised), the 6 cancelled dropped
    assert cell["spo"] == "6", cell
    assert cell["buy"] == "4", cell
    assert cell["po"] == "0", cell


# --------------------------------------------------------------------------- AC-X6


def test_ac_x6_the_matrix_stage_sums_apply_the_corrected_derived_cover_rule(api):
    """The matrix's `_INCOMING_QTY`/`_PURCHASED_QTY` are the SAME module-level
    expressions `_kinds` reads (round 2 `matrix()` docstring) - the AC-D11 case (qty
    10, PO-linked 3, one open allocation of 500) has to read the SAME cell values
    through the matrix as it does through the cards: spo 3, po 0, buy 7. Today
    (uncapped derived cover): spo 10."""
    client, db, company_id, _seeded = api
    product = _product(db, f"ZZT-MATRIX-X6-{_uid()[:6]}", f"{MARKER} product x6")
    core = _core_order(
        db, company_id, customer=_seeded_customer(db, company_id),
        agent=_seeded_agent(db, company_id), order_date=date(2026, 8, 1),
    )
    pso = _adopted_pso(db, company_id, core)
    line = _pso_line(db, company_id, pso, product)
    inquiry = _inquiry_for(db, company_id, pso)
    row = _row(db, company_id, inquiry, line, item_code=f"{MARKER}-X6", qty="10",
               delivery_date=date(2026, 8, 10))
    po = _po_link(db, company_id, row, product, "3")
    _open_spo_allocation(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", from_po_number=po.po_number,
        product=product, allocated_quantity=500,
    )
    db.commit()

    body = client.get(MATRIX, params={"axis": "product", "by": "month"}).json()
    cell = next(c for c in body["data"] if c["axis_key"] == str(product.id))

    assert cell["spo"] == "3", cell
    assert cell["po"] == "0", cell
    assert cell["buy"] == "7", cell


# ------------------------------------------------- AC-RL-16 addendum (16 Sep, 2nd round)


def test_schedule_matrix_stage_cards_exclude_redirected_row(api):
    """AC-RL-16 addendum (`PLAN-oi-replan-received-links.md` S3): the matrix reuses
    `_stage_rows` too (`matrix()`'s own docstring: "the SAME per-row arithmetic the
    cards read"), but the redirect exclusion the cards got (`_kinds`' own `extra_
    filters`) was applied only at THAT call site - a redirected row's linked quantity
    must not inflate the matrix's `po` cell either, and its own history row must not
    inflate `rows` or `qty`."""
    client, db, company_id, seeded = api
    product = _product(db, f"ZZT-MATRIX-REDIRECT-{_uid()[:6]}", f"{MARKER} product redirect")
    core = _core_order(
        db, company_id, customer=seeded["customer_a"], agent=seeded["agent_a"],
        order_date=date(2026, 7, 1),
    )
    pso = _adopted_pso(db, company_id, core)
    line = _pso_line(db, company_id, pso, product)
    inquiry = _inquiry_for(db, company_id, pso)

    redirected = OrderInquiryRow(
        id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id,
        so_line_id=line.id, item_code=f"{MARKER}-REDIRECT", qty=Decimal("158"),
        delivery_date=date(2026, 7, 15), verb=IV_ORDER, state="placed",
        ack_state="acknowledged", redirected_to_pool=True,
    )
    db.add(redirected)
    db.flush()
    _po_link(db, company_id, redirected, product, "158")
    _raised = _row(
        db, company_id, inquiry, line, item_code=f"{MARKER}-REDIRECT-FRESH", qty="220",
        delivery_date=date(2026, 7, 15), state=INQUIRY_RAISED,
    )
    db.commit()

    body = client.get(MATRIX, params={"axis": "product", "by": "month"}).json()
    cell = next(c for c in body["data"] if c["axis_key"] == str(product.id))

    assert cell["rows"] == 1, cell  # the redirected row is not counted
    assert cell["qty"] == "220", cell
    assert cell["po"] == "0", cell
    assert cell["buy"] == "220", cell


# --------------------------------------------------------------------------- SF-3


def test_sf3_an_authored_row_with_no_core_sales_order_still_appears_on_the_sales_order_axis(api):
    """`axis=sales_order` is keyed on `SalesOrder.id` - NULL for an authored
    `ProjectSalesOrder` that was never adopted from a core order (`so_id` null,
    `provisional_ref` the only identity it has). `matrix()` filters `axis_key.isnot(
    None)`, which drops the row entirely today rather than falling back to the PSO's
    own id the way the list's `_SO_NUMBER` label already falls back to
    `provisional_ref`."""
    client, db, company_id, _seeded = api
    product = _product(db, f"ZZT-MATRIX-SF3-{_uid()[:6]}", f"{MARKER} product sf3")
    pso = ProjectSalesOrder(
        id=_uid(), company_id=company_id, project_id=None, so_id=None,
        provisional_ref=f"ZZT-PSO-SF3-{_uid()[:8]}", status="draft",
    )
    db.add(pso)
    db.flush()
    line = _pso_line(db, company_id, pso, product)
    inquiry = _inquiry_for(db, company_id, pso)
    row = _row(db, company_id, inquiry, line, item_code=f"{MARKER}-SF3", qty="5",
               delivery_date=date(2026, 8, 10))
    db.commit()

    body = client.get(MATRIX, params={"axis": "sales_order", "by": "month"}).json()

    # No other seeded row in this fixture delivers in August 2026, so a cell existing
    # at all for this period is this row - today `axis_key.isnot(None)` drops it, so
    # NO cell for `2026-08-01` appears under this axis at all.
    august_rows = sum(int(c["rows"]) for c in body["data"] if c["period"] == "2026-08-01")
    assert august_rows == 1, body["data"]


# --------------------------------------------------------------------------- SF-5


def test_sf5_an_invalid_delivery_from_names_the_delivery_param_not_raised_date(api):
    client, _db, _company_id, _seeded = api

    response = client.get(MATRIX, params={"axis": "product", "by": "month",
                                            "delivery_from": "notadate"})

    assert response.status_code == 422, response.text
    code = response.json().get("code")
    assert code == "invalid_delivery_from", response.json()


# --------------------------------------------------------------------------- S1
#
# `axis_key` is compared against a UUID column on every axis (`products.id`,
# `sales_orders.id`/`project_sales_orders.id`, `customers.id`, `sales_agents.id`), so a
# malformed one has to be refused at the route rather than handed to Postgres, which
# answers `invalid input syntax for type uuid` - a 500 carrying the SQL.


def test_s1_a_malformed_axis_key_on_the_list_is_refused_not_a_500(api):
    client, _db, _company_id, _seeded = api

    response = client.get(
        LIST, params={"axis": "product", "axis_key": "not-a-uuid"}
    )

    assert response.status_code == 422, response.text


def test_s1_an_unknown_but_well_formed_axis_key_is_an_empty_page_not_an_error(api):
    """A cell whose rows have since been answered is an empty list, not a refusal -
    the key is legal, it just names nothing any more."""
    client, _db, _company_id, _seeded = api

    response = client.get(LIST, params={"axis": "product", "axis_key": _uid()})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [], body
    assert body["pagination"]["total"] == 0, body


# ------------------------------------------------------------- security: unbounded strings


@pytest.mark.parametrize("param", ["location", "po_number", "spo_number"])
def test_security_an_over_long_string_filter_is_refused(api, param):
    client, _db, _company_id, _seeded = api

    response = client.get(
        MATRIX, params={"axis": "product", "by": "month", param: "x" * 201}
    )

    assert response.status_code == 422, response.text


def test_security_an_unknown_granularity_is_refused(api):
    """`by=fortnight`: pinned rather than asserted blind, per the coordinator's own
    note - the route's `Literal["day","week","month","year"]` may already 422 this
    before the service's own silent week fallback is ever reached."""
    client, _db, _company_id, _seeded = api

    response = client.get(MATRIX, params={"axis": "product", "by": "fortnight"})

    assert response.status_code == 422, response.text


# ---------------------------------------------------------- axis grouping, parametrized


@pytest.mark.parametrize(
    "axis,key_of",
    [
        ("sales_order", lambda seeded: seeded["core_a"].id),
        ("customer", lambda seeded: seeded["customer_a"].id),
        ("agent", lambda seeded: seeded["agent_a"].id),
    ],
)
def test_every_axis_groups_by_its_own_column(api, axis, key_of):
    client, _db, _company_id, seeded = api

    body = client.get(MATRIX, params={"axis": axis, "by": "week"}).json()

    wanted = str(key_of(seeded))
    matching = [c for c in body["data"] if c["axis_key"] == wanted]
    assert matching, f"no cell grouped by {axis}={wanted}: {body['data']}"
    assert sum(int(c["rows"]) for c in matching) == 2  # row_wed + row_mon


def test_an_unknown_axis_is_refused(api):
    client, _db, _company_id, _seeded = api

    response = client.get(MATRIX, params={"axis": "whatever", "by": "week"})

    assert response.status_code == 422


# --------------------------------------------------------------- honours list filters


def test_the_matrix_honours_the_location_filter(api):
    client, _db, _company_id, seeded = api

    body = client.get(
        MATRIX, params={"axis": "product", "by": "week", "location": "ZZTLOCX"}
    ).json()

    axis_keys = {c["axis_key"] for c in body["data"]}
    assert str(seeded["product_p"].id) in axis_keys
    assert str(seeded["product_q"].id) not in axis_keys


# ------------------------------------------------------------------- AC-X2, no row cap


def test_no_row_cap_across_two_years_of_delivery_months(api):
    """1,200 rows spread across 24 delivery months. The old Schedule view fetched the
    list ONCE with `limit=1000` and grouped client-side - a delivery-filtered worklist
    already exceeds that on prod (PLAN section 0), so the matrix must have no cap at
    all."""
    client, db, company_id, _seeded = api
    product_bulk = _product(db, f"ZZT-MATRIX-BULK-{_uid()[:6]}", f"{MARKER} bulk product")
    core_bulk = _core_order(
        db, company_id, customer=_seeded_customer(db, company_id),
        agent=_seeded_agent(db, company_id), order_date=date(2026, 1, 1),
    )
    pso_bulk = _adopted_pso(db, company_id, core_bulk)
    line_bulk = _pso_line(db, company_id, pso_bulk, product_bulk)
    inquiry_bulk = _inquiry_for(db, company_id, pso_bulk)

    months = [
        (year, month)
        for year in (2026, 2027)
        for month in range(1, 13)
    ]
    rows = []
    for index in range(1200):
        year, month = months[index % 24]
        rows.append(
            {
                "id": _uid(),
                "company_id": company_id,
                "order_inquiry_id": inquiry_bulk.id,
                "so_line_id": line_bulk.id,
                "item_code": f"{MARKER}-BULK-{index}",
                "qty": Decimal("1"),
                "delivery_date": date(year, month, 10),
                "verb": IV_ORDER,
                "state": INQUIRY_RAISED,
                "ack_state": "acknowledged",
                "redirected_to_pool": False,
                "bundled_qty": Decimal("0"),
            }
        )
    db.execute(OrderInquiryRow.__table__.insert(), rows)
    db.commit()

    body = client.get(MATRIX, params={"axis": "product", "by": "month"}).json()

    cells = [c for c in body["data"] if c["axis_key"] == str(product_bulk.id)]
    periods = {c["period"] for c in cells}
    assert len(periods) == 24, sorted(periods)
    assert sum(int(c["rows"]) for c in cells) == 1200


def _seeded_customer(db, company_id):
    return _customer(db, company_id, f"{MARKER} bulk customer")


def _seeded_agent(db, company_id):
    return _agent(db, company_id, f"ZZT{_uid()[:6].upper()}", f"{MARKER} bulk agent")


# -------------------------------------------------------------------------- permission


def test_a_user_without_the_view_permission_is_refused(stranger_api):
    client, _db, _company_id, _seeded = stranger_api

    response = client.get(MATRIX, params={"axis": "product", "by": "week"})

    assert response.status_code == 403
