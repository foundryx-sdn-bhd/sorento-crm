"""Purchasing's cross-project order inquiry: the list, the summary and the workbook.

The per-project list next door answers "what did THIS project raise". It cannot answer
purchasing's own question, and not because it is inconvenient: an order ADOPTED from the
AutoCount book has `project_id` NULL by design, so its rows belong to no project and the
per-project query can never return them. Before this list they were reachable only from
the one sales order that raised them.

So the cases worth pinning here are the ones that would let that regress:

* an adopted row and an authored row appear in the SAME list;
* the PROJECT/CUSTOMER column falls back to the CORE order's customer when there is no
  project, rather than going blank (an inner join to Project is what made it blank);
* every filter narrows, including the two that are the screen's own controls;
* the sort set is CLOSED, nulls sort LAST in both directions, and the order is total;
* the workbook is ONE sheet (every delivery month on it) with the client's own heading row.
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import event, text

from app.models.company import Company
from app.models.inventory import Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.procurement import (
    InboundShipment,
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
    Supplier,
)
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    INQUIRY_ACTIONED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ALREADY_INBOUND,
    IV_DELAY,
    IV_ORDER,
    SO_STATUS_DRAFT,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.sales_agent import SalesAgent
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-oi-worklist"
BASE = "/api/v1/project-sales"
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
        id=_uid(),
        product_code=code,
        product_name=name,
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _customer(db, company_id: str, name: str) -> Customer:
    row = Customer(
        id=_uid(),
        company_id=company_id,
        customer_code=f"ZZT-{_uid()[:8]}",
        customer_name=name,
    )
    db.add(row)
    db.flush()
    return row


def _inquiry_for(db, company_id: str, order: ProjectSalesOrder) -> OrderInquiry:
    inquiry = OrderInquiry(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        state=INQUIRY_RAISED,
    )
    db.add(inquiry)
    db.flush()
    return inquiry


def _row(db, company_id: str, inquiry: OrderInquiry, **fields) -> OrderInquiryRow:
    """One inquiry row, and its LINKS when the caller names a `po_line_id`.

    Since section 3.I a placement lives in `projects.order_inquiry_links`, not in the row's
    own `po_line_id` - which is now the derived display of the first link. Every reader
    that used to follow the scalar (the worklist's PO no and Supplier columns, "Taken from
    PO", the `linked` filter) follows the links instead, so a row built with a bare
    `po_line_id` and `state='placed'` is a shape the application can no longer produce and
    no reader answers for. The helper writes the link the service would have written, so a
    test states its intent - "this row is on that PO line" - and gets a real one.
    """
    po_line_id = fields.pop("po_line_id", None)
    row = OrderInquiryRow(
        id=_uid(),
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        verb=fields.pop("verb", IV_ORDER),
        state=fields.pop("state", INQUIRY_RAISED),
        qty=Decimal(str(fields.pop("qty", "10"))),
        po_line_id=po_line_id,
        **fields,
    )
    db.add(row)
    db.flush()
    if po_line_id:
        db.add(
            OrderInquiryLink(
                id=_uid(),
                company_id=company_id,
                row_id=row.id,
                po_line_id=po_line_id,
                document=row.po_ref,
                qty=row.qty,
            )
        )
        db.flush()
    return row


def _client(db, user_id: str, permissions):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    # Captured BEFORE this call's own overrides are set, so `_restore` can put back
    # whatever an OUTER `_client` call (an `_as(...)` block nested inside `api`, for
    # instance) had already installed - an unconditional `app.dependency_overrides.
    # clear()` here wiped the outer client's overrides too, so the outer `client`
    # object used again after the `with` block exited fell through to the REAL
    # dependencies with no override at all.
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
        previous_overrides,
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
    app.dependency_overrides.update(originals[2])


def _seed(db, company_id: str, user_id: str) -> dict:
    """One authored project order and one ADOPTED AutoCount order, both with rows.

    Deliberately the two shapes together: everything this list exists for is that they
    are one list, and a fixture with only the authored shape would pass against the
    per-project query that cannot see the other one.
    """
    from app.services.project_service import register_project

    project = register_project(
        db,
        company_id=company_id,
        actor_user_id=user_id,
        developer_party_id=None,
        title=f"{MARKER} Tuju Residence",
    )

    # --- authored: a real project registration, delivering in March.
    authored = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=project.id,
        area_group="TOWER",
        provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
        autocount_doc_no="SO363150",
        status=SO_STATUS_DRAFT,
        grouping_origin="area",
        published_at=datetime(2025, 8, 15, 9, 0),
    )
    db.add(authored)
    db.flush()
    authored_product = _product(db, f"ZZT-SRTWT107-{_uid()[:6]}", f"{MARKER} Wall hung WC")
    authored_line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=authored.id,
        line_no=1,
        product_id=authored_product.id,
        description=f"{MARKER} authored",
        qty=Decimal("91"),
        uom="UNIT",
        unit_price=Decimal("10.00"),
        amount=Decimal("910.00"),
        delivery_date=date(2026, 3, 2),
    )
    db.add(authored_line)
    db.flush()
    authored_inquiry = _inquiry_for(db, company_id, authored)
    authored_row = _row(
        db,
        company_id,
        authored_inquiry,
        so_line_id=authored_line.id,
        item_code=authored_product.product_code,
        qty="91",
        delivery_date=date(2026, 3, 2),
    )

    # --- adopted: the AutoCount book, no project registration at all.
    customer = _customer(db, company_id, f"{MARKER} Optad Sdn Bhd")
    core = SalesOrder(
        id=_uid(),
        company_id=company_id,
        so_number=f"ZZTSO{_uid()[:8]}",
        customer_id=customer.id,
        order_date=date(2026, 1, 8),
    )
    db.add(core)
    db.flush()
    adopted = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=None,
        so_id=core.id,
        provisional_ref=core.so_number,
        autocount_doc_no=core.so_number,
        status="adopted",
    )
    db.add(adopted)
    db.flush()
    adopted_product = _product(db, f"ZZT-SRTWC8605-{_uid()[:6]}", f"{MARKER} Close coupled WC")
    adopted_line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=adopted.id,
        line_no=1,
        product_id=adopted_product.id,
        description=f"{MARKER} adopted",
        qty=Decimal("85"),
        uom="UNIT",
        unit_price=Decimal("10.00"),
        amount=Decimal("850.00"),
        delivery_date=date(2026, 1, 19),
    )
    db.add(adopted_line)
    db.flush()
    adopted_inquiry = _inquiry_for(db, company_id, adopted)
    adopted_row = _row(
        db,
        company_id,
        adopted_inquiry,
        so_line_id=adopted_line.id,
        item_code=adopted_product.product_code,
        qty="85",
        delivery_date=date(2026, 1, 19),
        state=INQUIRY_ACTIONED,
    )
    # An undated row, so "nulls last in BOTH directions" has something to be about.
    undated_row = _row(
        db,
        company_id,
        adopted_inquiry,
        so_line_id=adopted_line.id,
        item_code=f"ZZT-UNDATED-{_uid()[:6]}",
        qty="6",
        delivery_date=None,
    )
    db.commit()
    return {
        "project": project,
        "authored": authored,
        "authored_row": authored_row,
        "adopted": adopted,
        "adopted_row": adopted_row,
        "undated_row": undated_row,
        "core": core,
        "customer": customer,
    }


def _placed_supply(
    db,
    company_id: str,
    row: OrderInquiryRow,
    *,
    supplier: Supplier | None = None,
    supplier_name: str = f"{MARKER} DAFUYUAN",
) -> dict:
    """A purchase order the row can actually be traced to, through its SPO reference.

    This is the ONLY link the schema holds today between an inquiry row and a placed
    order, so it is the only thing the SUPPLIER and PO NO columns are allowed to print.
    Everything else on the sheet is filled in by hand and stays blank here.
    """
    if supplier is None:
        supplier = Supplier(
            id=_uid(),
            company_id=company_id,
            supplier_code=f"ZZT-{_uid()[:8]}",
            supplier_name=supplier_name,
        )
        db.add(supplier)
    warehouse = Warehouse(
        id=_uid(),
        company_id=company_id,
        warehouse_code=f"ZZT{_uid()[:6]}",
        warehouse_name=f"{MARKER} WH",
    )
    db.add(warehouse)
    db.flush()
    order = PurchaseOrder(
        id=_uid(),
        company_id=company_id,
        po_number=f"ZZT-202601-{_uid()[:6]}",
        supplier_id=supplier.id,
    )
    db.add(order)
    db.flush()
    line = PurchaseOrderLine(
        id=_uid(),
        company_id=company_id,
        purchase_order_id=order.id,
        product_id=_product(db, f"ZZT-P-{_uid()[:6]}", f"{MARKER} po line").id,
        qty_ordered=Decimal("85"),
    )
    shipment = InboundShipment(
        id=_uid(),
        company_id=company_id,
        shipment_number=f"ZZT-{_uid()[:8]}",
        shipment_date=date(2026, 1, 2),
    )
    db.add_all([line, shipment])
    db.flush()
    spo_number = f"ZZT-SPO-{_uid()[:6]}"
    db.add(
        SPOAllocation(
            id=_uid(),
            company_id=company_id,
            spo_number=spo_number,
            inbound_shipment_id=shipment.id,
            warehouse_id=warehouse.id,
            allocated_quantity=85,
            product_id=line.product_id,
            po_line_id=line.id,
        )
    )
    row.spo_ref = spo_number
    row.verb = IV_ALREADY_INBOUND
    db.flush()
    db.commit()
    return {"supplier": supplier, "purchase_order": order}


def _purchase_order(db, company_id: str, **overrides) -> dict:
    """A standalone purchase order with one line - the "PO no" popup's own fixture,
    unlinked from any order inquiry row (unlike `_placed_supply`, which exists to make a
    ROW traceable through its SPO reference)."""
    supplier = Supplier(
        id=_uid(),
        company_id=company_id,
        supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} PO SUPPLIER",
    )
    warehouse = Warehouse(
        id=_uid(),
        company_id=company_id,
        warehouse_code=f"ZZT{_uid()[:6]}",
        warehouse_name=f"{MARKER} PO WH",
    )
    db.add_all([supplier, warehouse])
    db.flush()
    order = PurchaseOrder(
        id=_uid(),
        company_id=company_id,
        po_number=f"ZZT-PO-{_uid()[:8]}",
        supplier_id=supplier.id,
        expected_date=overrides.get("expected_date", date(2026, 5, 1)),
        status=overrides.get("status", "active"),
    )
    db.add(order)
    db.flush()
    product = _product(db, f"ZZT-POLINE-{_uid()[:6]}", f"{MARKER} po line item")
    line = PurchaseOrderLine(
        id=_uid(),
        company_id=company_id,
        purchase_order_id=order.id,
        product_id=product.id,
        warehouse_id=warehouse.id,
        qty_ordered=Decimal("40"),
        qty_received=Decimal("15"),
    )
    db.add(line)
    db.commit()
    return {
        "order": order,
        "supplier": supplier,
        "warehouse": warehouse,
        "product": product,
        "line": line,
    }


def _api(permissions):
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _user(db, f"{MARKER} Eling")
        seeded = _seed(db, company_id, user_id)
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


# ------------------------------------------------------------------ the list


def test_the_list_carries_project_rows_and_adopted_rows_together(api):
    client, _db, _company_id, seeded = api

    response = client.get(LIST)

    assert response.status_code == 200, response.text
    body = response.json()
    ids = {row["id"] for row in body["data"]}
    assert seeded["authored_row"].id in ids
    assert seeded["adopted_row"].id in ids
    assert body["pagination"]["total"] == 3


def test_an_adopted_row_names_the_core_orders_customer_rather_than_nothing(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST).json()
    adopted = next(row for row in body["data"] if row["id"] == seeded["adopted_row"].id)

    # The inner join to Project is what used to blank this column for every adopted row.
    assert seeded["customer"].customer_name in (adopted["project_customer"] or "")
    assert adopted["so_number"] == seeded["core"].so_number
    assert adopted["so_date"] == "2026-01-08"
    assert adopted["core_sales_order_id"] == seeded["core"].id
    assert adopted["is_adopted"] is True


def test_a_project_row_names_the_project_and_reaches_its_own_document(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST).json()
    authored = next(row for row in body["data"] if row["id"] == seeded["authored_row"].id)

    assert f"{MARKER} Tuju Residence" in (authored["project_customer"] or "")
    assert authored["project_id"] == seeded["project"].id
    assert authored["project_sales_order_id"] == seeded["authored"].id
    assert authored["core_sales_order_id"] is None
    assert authored["product_name"].startswith(MARKER)


def test_the_per_project_list_agrees_with_the_worklist_on_the_customer_label(api):
    """One label, computed once. Two screens disagreeing is a support call."""
    client, _db, _company_id, seeded = api

    worklist = client.get(LIST).json()["data"]
    per_project = client.get(
        f"{BASE}/projects/{seeded['project'].id}/order-inquiry-rows"
    ).json()["data"]

    mine = next(row for row in worklist if row["id"] == seeded["authored_row"].id)
    theirs = next(row for row in per_project if row["id"] == seeded["authored_row"].id)
    assert mine["project_customer"] == theirs["project_customer"]


# ----------------------------------------------------------- the inquiry number


def _inquiry_no_of(db, row: OrderInquiryRow) -> str:
    return (
        db.query(OrderInquiry.inquiry_no)
        .filter(OrderInquiry.id == row.order_inquiry_id)
        .scalar()
    )


def test_every_row_names_the_inquiry_it_belongs_to(api):
    """`OI-000123`, off the row's own header - the number a person quotes.

    The S/O no beside it cannot answer "which instruction was I given": an amendment
    raises a SECOND inquiry on the same sales order, and both rows print the same
    sales-order number.
    """
    client, db, _company_id, seeded = api

    body = client.get(LIST).json()
    by_id = {row["id"]: row for row in body["data"]}

    # It survives `response_model`, which silently drops anything undeclared.
    assert "inquiry_no" in by_id[seeded["authored_row"].id], by_id[
        seeded["authored_row"].id
    ].keys()
    assert by_id[seeded["authored_row"].id]["inquiry_no"] == _inquiry_no_of(
        db, seeded["authored_row"]
    )
    assert by_id[seeded["adopted_row"].id]["inquiry_no"] == _inquiry_no_of(
        db, seeded["adopted_row"]
    )
    # The stamp is real, not a test fixture's invention.
    assert by_id[seeded["authored_row"].id]["inquiry_no"].startswith("OI-")


def test_the_query_box_matches_the_inquiry_number(api):
    """Purchasing is asked about "OI-000123" by name, so the one search box has to find
    it - the row is otherwise reachable only by knowing which sales order raised it."""
    client, db, _company_id, seeded = api

    number = _inquiry_no_of(db, seeded["authored_row"])
    body = client.get(LIST, params={"query": number}).json()

    assert [row["id"] for row in body["data"]] == [seeded["authored_row"].id]


def test_the_list_sorts_by_the_inquiry_number(api):
    """Nulls last in both directions, and the order is total, like every other column."""
    client, db, _company_id, seeded = api

    ascending = client.get(LIST, params={"sort": "inquiry_no", "dir": "asc"}).json()
    descending = client.get(LIST, params={"sort": "inquiry_no", "dir": "desc"}).json()

    numbers = [row["inquiry_no"] for row in ascending["data"]]
    assert numbers == sorted(numbers)
    assert [row["inquiry_no"] for row in descending["data"]] == sorted(
        numbers, reverse=True
    )
    assert _inquiry_no_of(db, seeded["adopted_row"]) in numbers


# ---------------------------------------------------------------- the filters


def test_the_delivery_month_filter_is_the_sheet_tab(api):
    client, _db, _company_id, seeded = api

    january = client.get(LIST, params={"delivery_month": "2026-01"}).json()
    assert [row["id"] for row in january["data"]] == [seeded["adopted_row"].id]

    march = client.get(LIST, params={"delivery_month": "2026-03"}).json()
    assert [row["id"] for row in march["data"]] == [seeded["authored_row"].id]


def test_the_state_filter_narrows_to_one_state(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"state": INQUIRY_ACTIONED}).json()

    assert [row["id"] for row in body["data"]] == [seeded["adopted_row"].id]


def test_the_project_filter_narrows_to_one_project(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"project_id": seeded["project"].id}).json()

    assert [row["id"] for row in body["data"]] == [seeded["authored_row"].id]


def test_the_raised_date_filter_is_the_per_day_tab(api):
    client, _db, _company_id, seeded = api
    # `created_at` is naive UTC; the tab is a Malaysian day.
    raised_on = (seeded["adopted_row"].created_at + timedelta(hours=8)).date().isoformat()

    body = client.get(LIST, params={"raised_date": raised_on}).json()

    assert body["pagination"]["total"] == 3
    assert client.get(LIST, params={"raised_date": "2001-01-01"}).json()[
        "pagination"
    ]["total"] == 0


def test_the_raised_date_tab_is_a_malaysian_day_not_a_utc_one(api):
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["adopted_row"].order_inquiry_id)
    # 00:30 MYT on 20 Aug is 16:30 UTC on 19 Aug; 23:30 MYT on 19 Aug is 15:30 UTC.
    after_midnight = _row(
        db,
        company_id,
        inquiry,
        item_code=f"ZZT-MIDNIGHT-{_uid()[:6]}",
        qty="1",
        created_at=datetime(2026, 8, 19, 16, 30),
    )
    before_midnight = _row(
        db,
        company_id,
        inquiry,
        item_code=f"ZZT-EVENING-{_uid()[:6]}",
        qty="1",
        created_at=datetime(2026, 8, 19, 15, 30),
    )
    db.commit()

    on_the_20th = {
        row["id"]
        for row in client.get(LIST, params={"raised_date": "2026-08-20"}).json()["data"]
    }
    on_the_19th = {
        row["id"]
        for row in client.get(LIST, params={"raised_date": "2026-08-19"}).json()["data"]
    }

    assert after_midnight.id in on_the_20th
    assert after_midnight.id not in on_the_19th
    assert before_midnight.id in on_the_19th
    assert before_midnight.id not in on_the_20th


def test_the_query_box_matches_the_sales_order_the_item_the_product_and_the_customer(api):
    client, _db, _company_id, seeded = api

    by_so = client.get(LIST, params={"query": seeded["core"].so_number}).json()
    assert {row["id"] for row in by_so["data"]} == {
        seeded["adopted_row"].id,
        seeded["undated_row"].id,
    }

    by_item = client.get(LIST, params={"query": seeded["authored_row"].item_code}).json()
    assert [row["id"] for row in by_item["data"]] == [seeded["authored_row"].id]

    by_product = client.get(LIST, params={"query": "Wall hung WC"}).json()
    assert [row["id"] for row in by_product["data"]] == [seeded["authored_row"].id]

    by_customer = client.get(LIST, params={"query": "Optad"}).json()
    assert {row["id"] for row in by_customer["data"]} == {
        seeded["adopted_row"].id,
        seeded["undated_row"].id,
    }


def test_the_supplier_and_po_are_the_ones_the_row_actually_links_to(api):
    client, db, company_id, seeded = api
    placed = _placed_supply(db, company_id, seeded["adopted_row"])

    body = client.get(LIST).json()
    adopted = next(row for row in body["data"] if row["id"] == seeded["adopted_row"].id)
    authored = next(row for row in body["data"] if row["id"] == seeded["authored_row"].id)

    assert adopted["supplier"] == placed["supplier"].supplier_name
    assert adopted["po_number"] == placed["purchase_order"].po_number
    # Nothing is invented for a row nobody has placed yet: blank is what their sheet
    # writes and blank is what this says.
    assert authored["supplier"] is None
    assert authored["po_number"] is None

    filtered = client.get(
        LIST, params={"supplier_id": placed["supplier"].id}
    ).json()
    assert [row["id"] for row in filtered["data"]] == [seeded["adopted_row"].id]


def test_the_links_payload_pins_po_line_id_for_highlighting_the_lightbox_line(api):
    """Should-fix 6 (review of PR #1220): `po_line_id` is the field the PO lightbox's
    linked-line highlight (issue #1215 point 2) and the worklist's backing-documents
    dialog (review Blocking 1) both key off - `links_for_rows` already computed it, but
    `response_model` silently drops an undeclared field (LESSONS-LEARNT), and nothing
    asserted it actually reaches the wire."""
    client, db, company_id, seeded = api
    po = _purchase_order(db, company_id)
    # A fresh order, not `seeded["authored"]`/`seeded["adopted"]` - each already carries
    # its own inquiry, and `order_inquiries` allows exactly one per sales order.
    pso = ProjectSalesOrder(
        id=_uid(), company_id=company_id, provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
    )
    db.add(pso)
    db.flush()
    inquiry = _inquiry_for(db, company_id, pso)
    row = _row(
        db, company_id, inquiry, po_line_id=po["line"].id,
        item_code=po["product"].product_code, qty="5", state=INQUIRY_PLACED,
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row.id)
    [link] = wire_row["links"]

    assert link["po_line_id"] == po["line"].id


def test_the_links_payload_pins_spo_allocation_id_for_highlighting_the_spo_lightbox_line(api):
    """R15 (owner rulings, 25 Sep 2026, hand test on stack C): the SPO lightbox never
    highlighted its own linked line because `spo_allocation_id` - the exact mirror of
    `po_line_id` above, already computed by `links_for_rows` - never reached the wire
    either. Same response_model silent-drop gap, same fix."""
    client, db, company_id, seeded = api
    supplier = Supplier(
        id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} SPO SUPPLIER",
    )
    db.add(supplier)
    db.flush()
    product = _product(db, f"ZZT-SPOLINE-{_uid()[:6]}", f"{MARKER} spo line item")
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=f"ZZT-SPO-{_uid()[:8]}",
        spo_line_number=1, product_id=product.id, allocated_quantity=40,
        quantity_received=0, receipt_status="pending", line_status="open",
        source_system="scm_upload", supplier_id=supplier.id,
    )
    db.add(allocation)
    db.flush()
    pso = ProjectSalesOrder(
        id=_uid(), company_id=company_id, provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
    )
    db.add(pso)
    db.flush()
    inquiry = _inquiry_for(db, company_id, pso)
    row = OrderInquiryRow(
        id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id, verb=IV_ORDER,
        state=INQUIRY_PLACED, qty=Decimal("5"), item_code=product.product_code,
    )
    db.add(row)
    db.flush()
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=company_id, row_id=row.id,
            spo_allocation_id=allocation.id, document=allocation.spo_number, qty=row.qty,
        )
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row.id)
    [link] = wire_row["links"]

    assert link["spo_allocation_id"] == allocation.id
    assert link["kind"] == "spo"


def test_the_links_payload_pins_purchase_order_id_for_an_spo_link_with_a_resolved_supply_line(api):
    """R17 (owner rulings, 25 Sep 2026): the Lines tab's "<number> via SPO" cell needs
    the PO id an SPO allocation draws its supply from (`SPOAllocation.po_line_id` traced
    to its own header), so it can open that PO directly instead of guessing by number.
    `links_for_rows` already resolves this as `purchase_order_id`; nothing pinned that
    it reaches the wire."""
    client, db, company_id, seeded = api
    po = _purchase_order(db, company_id)
    supplier = Supplier(
        id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} SPO SUPPLIER 2",
    )
    db.add(supplier)
    db.flush()
    product = _product(db, f"ZZT-SPOLINE2-{_uid()[:6]}", f"{MARKER} spo line item 2")
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=f"ZZT-SPO-{_uid()[:8]}",
        spo_line_number=1, product_id=product.id, allocated_quantity=40,
        quantity_received=0, receipt_status="pending", line_status="open",
        source_system="scm_upload", supplier_id=supplier.id, po_line_id=po["line"].id,
        from_po_number=po["order"].po_number,
    )
    db.add(allocation)
    db.flush()
    pso = ProjectSalesOrder(
        id=_uid(), company_id=company_id, provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
    )
    db.add(pso)
    db.flush()
    inquiry = _inquiry_for(db, company_id, pso)
    row = OrderInquiryRow(
        id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id, verb=IV_ORDER,
        state=INQUIRY_PLACED, qty=Decimal("5"), item_code=product.product_code,
    )
    db.add(row)
    db.flush()
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=company_id, row_id=row.id,
            spo_allocation_id=allocation.id, document=allocation.spo_number, qty=row.qty,
        )
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row.id)
    [link] = wire_row["links"]

    assert link["purchase_order_id"] == po["order"].id
    assert link["source_po_number"] == po["order"].po_number


def test_the_links_payload_pins_purchase_order_id_for_an_spo_link_named_by_number_only(api):
    """Review round 2 Should fix 3. The R17 test above covers a Sorento-raised SPO
    with its own `po_line_id` FK back to the supply line - the NARROW case. Most
    book-fed allocations carry only the AutoCount pass-through PO NUMBER
    (`from_po_number`), no `po_line_id` at all, and a PO reached ONLY through an
    SPO never appears as a `po`-kind link on any row - the exact case the old
    client-side `useOrderInquiryPoIdByNumber` scan of the worklist could never
    answer for. `links_for_rows` now resolves it server side, one batched
    `PurchaseOrder.po_number IN (...)` query."""
    client, db, company_id, seeded = api
    po = _purchase_order(db, company_id)
    supplier = Supplier(
        id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} SPO SUPPLIER 3",
    )
    db.add(supplier)
    db.flush()
    product = _product(db, f"ZZT-SPOLINE3-{_uid()[:6]}", f"{MARKER} spo line item 3")
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=f"ZZT-SPO-{_uid()[:8]}",
        spo_line_number=1, product_id=product.id, allocated_quantity=40,
        quantity_received=0, receipt_status="pending", line_status="open",
        source_system="scm_upload", supplier_id=supplier.id, po_line_id=None,
        from_po_number=po["order"].po_number,
    )
    db.add(allocation)
    db.flush()
    pso = ProjectSalesOrder(
        id=_uid(), company_id=company_id, provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
    )
    db.add(pso)
    db.flush()
    inquiry = _inquiry_for(db, company_id, pso)
    row = OrderInquiryRow(
        id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id, verb=IV_ORDER,
        state=INQUIRY_PLACED, qty=Decimal("5"), item_code=product.product_code,
    )
    db.add(row)
    db.flush()
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=company_id, row_id=row.id,
            spo_allocation_id=allocation.id, document=allocation.spo_number, qty=row.qty,
        )
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row.id)
    [link] = wire_row["links"]

    assert link["purchase_order_id"] == po["order"].id
    assert link["source_po_number"] == po["order"].po_number


def test_supplier_reads_off_an_spo_only_links_own_supplier(api):
    """AC-FB-52 (measured on the prod copy: 5,156 SPO-only rows show "Not linked"
    under Supplier): a row whose ONLY link is an SPO allocation with no `po_line_id`
    at all (a genuine book-chain SPO, never resolved back to a source PO line) must
    still serialise top-level `supplier` off THAT allocation's own `supplier_id` -
    not blank. `_SPO_LINKED_PO_ID` requires `SPOAllocation.po_line_id` to be set, so
    `_PLACED_PO_ID` (the coalesce every one of its three legs reads) comes back NULL
    for this row and the Supplier outerjoin (keyed off `PurchaseOrder.supplier_id`,
    never `SPOAllocation.supplier_id` directly) never even considers it. A row with
    a PO link is unchanged (the test above)."""
    client, db, company_id, seeded = api
    line = _line_on_authored_order(db, company_id, seeded, qty="12", day=9)
    supplier = Supplier(
        id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} SPO ONLY SUPPLIER",
    )
    db.add(supplier)
    db.flush()
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}",
        spo_line_number=1, product_id=line.product_id, allocated_quantity=12,
        quantity_received=0, line_status="open", supplier_id=supplier.id,
        po_line_id=None,
    )
    db.add(allocation)
    db.flush()
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    row = _row(
        db, company_id, inquiry, so_line_id=line.id, qty="12", state="placed",
    )
    db.add(OrderInquiryLink(
        id=_uid(), company_id=company_id, row_id=row.id,
        spo_allocation_id=allocation.id, document=allocation.spo_number,
        qty=Decimal("12"),
    ))
    db.commit()

    body = client.get(LIST).json()
    listed = next(r for r in body["data"] if r["id"] == row.id)
    assert listed["supplier"] == supplier.supplier_name, listed


def _spo_linked_row_naming_po(db, company_id, seeded, po_number):
    """A row whose ONLY link is a book-fed SPO allocation (no `po_line_id`) that names
    its source PO by number alone (`from_po_number`) and states a DIFFERENT supplier of
    its own - the "via SPO" row on the owner's 9 Oct screenshot."""
    line = _line_on_authored_order(db, company_id, seeded, qty="7", day=11)
    spo_supplier = Supplier(
        id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} SPO OWN SUPPLIER",
    )
    db.add(spo_supplier)
    db.flush()
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}",
        spo_line_number=1, product_id=line.product_id, allocated_quantity=7,
        quantity_received=0, line_status="open", supplier_id=spo_supplier.id,
        po_line_id=None, from_po_number=po_number,
    )
    db.add(allocation)
    db.flush()
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    row = _row(db, company_id, inquiry, so_line_id=line.id, qty="7", state="placed")
    db.add(OrderInquiryLink(
        id=_uid(), company_id=company_id, row_id=row.id,
        spo_allocation_id=allocation.id, document=allocation.spo_number,
        qty=Decimal("7"),
    ))
    db.commit()
    return row, spo_supplier


def test_supplier_is_the_source_pos_supplier_not_the_spos_on_a_via_spo_row(api):
    """OI-SUPPLIER-FROM-PO (owner, 9 Oct 2026: "the supplier always take from PO, not
    the SPO"). An SPO-only row whose allocation names its source PO by number
    (`from_po_number`, the "via SPO" PO cell) reads that PO's supplier under Supplier,
    never the allocation's own `supplier_id`, and the Supplier filter agrees."""
    client, db, company_id, seeded = api
    po = _purchase_order(db, company_id)
    row, spo_supplier = _spo_linked_row_naming_po(
        db, company_id, seeded, po["order"].po_number
    )
    po_supplier_id = po["order"].supplier_id
    po_supplier = db.get(Supplier, po_supplier_id)

    body = client.get(LIST, params={"limit": 200}).json()
    listed = next(r for r in body["data"] if r["id"] == row.id)
    assert listed["supplier"] == po_supplier.supplier_name, listed
    assert listed["supplier_id"] == po_supplier_id, listed

    by_po_supplier = client.get(
        LIST, params={"limit": 200, "supplier_id": po_supplier_id}
    ).json()
    assert row.id in {r["id"] for r in by_po_supplier["data"]}
    by_spo_supplier = client.get(
        LIST, params={"limit": 200, "supplier_id": spo_supplier.id}
    ).json()
    assert row.id not in {r["id"] for r in by_spo_supplier["data"]}


def test_supplier_keeps_the_spos_own_when_its_named_po_is_not_in_the_book(api):
    """The fallback the rule leaves standing: an SPO naming a PO number the CRM holds no
    purchase order for has no PO supplier to read, so the allocation's own supplier
    still answers (AC-FB-52) rather than "Not linked"."""
    client, db, company_id, seeded = api
    row, spo_supplier = _spo_linked_row_naming_po(
        db, company_id, seeded, f"ZZT-NOPO-{_uid()[:8]}"
    )

    body = client.get(LIST, params={"limit": 200}).json()
    listed = next(r for r in body["data"] if r["id"] == row.id)
    assert listed["supplier"] == spo_supplier.supplier_name, listed


# ------------------------------------------------------------------- location


def test_a_row_with_a_stamped_stock_location_prints_it_unchanged(api):
    """`stock_location` is stamped once at raise time - the donor an order-back row left
    oversold, or a confirmed allocation's warehouse - and LOCATION just reads it back."""
    client, db, _company_id, seeded = api
    seeded["authored_row"].stock_location = "BRW-BB"
    db.add(seeded["authored_row"])
    db.commit()

    body = client.get(LIST).json()
    row = next(r for r in body["data"] if r["id"] == seeded["authored_row"].id)

    assert row["location"] == "BRW-BB"


def test_a_row_with_no_stamped_location_falls_back_to_its_lines_own_warehouse(api):
    """A plain Buy row raised straight off the board has no confirmed allocation and no
    donor, so the column falls back to the fulfilment warehouse already on the line's own
    core sales order line - a real answer, just not one purchasing confirmed themselves."""
    client, db, company_id, seeded = api
    warehouse = Warehouse(
        id=_uid(),
        company_id=company_id,
        warehouse_code=f"ZZT{_uid()[:6]}",
        warehouse_name=f"{MARKER} fulfilment WH",
    )
    db.add(warehouse)
    db.flush()
    core_line = SalesOrderLine(
        id=_uid(),
        company_id=company_id,
        sales_order_id=seeded["core"].id,
        product_id=_product(db, f"ZZT-COREP-{_uid()[:6]}", f"{MARKER} core line").id,
        warehouse_id=warehouse.id,
        qty_ordered=Decimal("85"),
    )
    db.add(core_line)
    db.flush()
    adopted_line = (
        db.query(ProjectSalesOrderLine)
        .filter(ProjectSalesOrderLine.id == seeded["adopted_row"].so_line_id)
        .one()
    )
    adopted_line.core_sales_order_line_id = core_line.id
    seeded["adopted_row"].stock_location = None
    db.add_all([adopted_line, seeded["adopted_row"]])
    db.commit()

    body = client.get(LIST).json()
    row = next(r for r in body["data"] if r["id"] == seeded["adopted_row"].id)

    assert row["location"] == warehouse.warehouse_code


def test_a_row_with_neither_a_stamped_nor_a_line_location_prints_blank(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST).json()
    row = next(r for r in body["data"] if r["id"] == seeded["authored_row"].id)

    assert row["location"] is None


def test_sorting_by_location_is_accepted(api):
    client, _db, _company_id, _seeded = api

    response = client.get(LIST, params={"sort": "location"})

    assert response.status_code == 200, response.text


# ------------------------------------------------------------------- sorting


def test_an_unknown_sort_column_is_refused_rather_than_quietly_ignored(api):
    client, _db, _company_id, _seeded = api

    response = client.get(LIST, params={"sort": "whatever"})

    assert response.status_code == 422


def test_an_unknown_direction_is_refused(api):
    client, _db, _company_id, _seeded = api

    assert client.get(LIST, params={"dir": "sideways"}).status_code == 422


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_a_row_with_no_delivery_date_sorts_last_in_both_directions(api, direction):
    client, _db, _company_id, seeded = api

    body = client.get(
        LIST, params={"sort": "delivery_date", "dir": direction}
    ).json()

    assert body["data"][-1]["id"] == seeded["undated_row"].id


def test_every_advertised_sort_column_answers(api):
    client, _db, _company_id, _seeded = api
    from app.services.order_inquiry_worklist_service import SORTABLE_FIELDS

    for field in sorted(SORTABLE_FIELDS):
        response = client.get(LIST, params={"sort": field})
        assert response.status_code == 200, f"{field}: {response.text}"


def test_the_route_and_the_service_agree_on_the_sortable_set():
    """FastAPI cannot build a `Literal` from a runtime set, so this is what keeps them
    from drifting: a column the route accepts and the service refuses is a 500."""
    from typing import get_args

    from app.api.v1.projects.order_inquiries import WorklistSort
    from app.services.order_inquiry_worklist_service import SORTABLE_FIELDS

    assert set(get_args(WorklistSort)) == set(SORTABLE_FIELDS)


def test_the_order_is_total_so_paging_neither_repeats_nor_drops_a_row(api):
    client, _db, _company_id, _seeded = api

    first = client.get(LIST, params={"limit": 2, "page": 1, "sort": "state"}).json()
    second = client.get(LIST, params={"limit": 2, "page": 2, "sort": "state"}).json()

    seen = [row["id"] for row in first["data"]] + [row["id"] for row in second["data"]]
    assert len(seen) == len(set(seen)) == 3


# ------------------------------------------------ SPO / Agent / Instruction sort keys


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_sorting_by_the_three_worklist_columns_that_draw_a_sort_arrow_is_accepted(
    api, direction
):
    """AC-1: SPO, Agent and Instruction are sortable columns on the worklist (they draw
    a sort arrow in `orderInquiryWorklistColumns.tsx`), so `sort` must accept the same
    ids those columns are keyed by - `spo_number`, `agent_code`, `verb` - in both
    directions. One fixture per direction rather than per id x direction (review round
    1): the same six checks, a third of the setup cost."""
    client, _db, _company_id, _seeded = api

    for field in ("spo_number", "agent_code", "verb"):
        response = client.get(LIST, params={"sort": field, "dir": direction})
        assert response.status_code == 200, f"{field} {direction}: {response.text}"


def test_spo_sort_orders_by_the_rows_own_link_then_by_spo_ref_blanks_last(api):
    """AC-2: the SPO column prints the row's own first linked SPO number ahead of a bare
    `spo_ref`, so the sort reads in the same order - own link, earliest `linked_at`,
    then `spo_ref` for a row with no link at all, and a row with neither trails last in
    BOTH directions (the generic `.nulls_last()` every sort field already gets)."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)

    warehouse = Warehouse(
        id=_uid(),
        company_id=company_id,
        warehouse_code=f"ZZT{_uid()[:6]}",
        warehouse_name=f"{MARKER} SPO sort WH",
    )
    db.add(warehouse)
    db.flush()
    allocation = SPOAllocation(
        id=_uid(),
        company_id=company_id,
        spo_number="ZZT-SPO-SORT-0100",
        product_id=_product(db, f"ZZT-P-{_uid()[:6]}", f"{MARKER} spo sort product").id,
        warehouse_id=warehouse.id,
        allocated_quantity=Decimal("10"),
    )
    db.add(allocation)
    db.flush()

    linked_line = _line_on_authored_order(db, company_id, seeded, qty="10", day=11)
    linked_row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=linked_line.id,
        item_code=f"{MARKER}-SPOSORT-LINKED",
        qty="10",
        state="partly_linked",
        delivery_date=date(2026, 4, 11),
    )
    db.add(
        OrderInquiryLink(
            id=_uid(),
            company_id=company_id,
            row_id=linked_row.id,
            spo_allocation_id=allocation.id,
            document=allocation.spo_number,
            qty=Decimal("10"),
        )
    )

    ref_line = _line_on_authored_order(db, company_id, seeded, qty="10", day=12)
    ref_row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=ref_line.id,
        item_code=f"{MARKER}-SPOSORT-REF",
        qty="10",
        delivery_date=date(2026, 4, 12),
        spo_ref="ZZT-SPO-SORT-0200",
    )

    blank_line = _line_on_authored_order(db, company_id, seeded, qty="10", day=13)
    blank_row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=blank_line.id,
        item_code=f"{MARKER}-SPOSORT-BLANK",
        qty="10",
        delivery_date=date(2026, 4, 13),
    )
    db.commit()

    ascending = client.get(
        LIST, params={"sort": "spo_number", "dir": "asc", "query": "SPOSORT"}
    ).json()["data"]
    assert [row["id"] for row in ascending] == [
        linked_row.id,
        ref_row.id,
        blank_row.id,
    ]

    descending = client.get(
        LIST, params={"sort": "spo_number", "dir": "desc", "query": "SPOSORT"}
    ).json()["data"]
    assert [row["id"] for row in descending] == [
        ref_row.id,
        linked_row.id,
        blank_row.id,
    ]


def test_agent_code_sort_matches_agent_sort_and_is_not_secretly_item_code(api):
    """Kill test (review round 1): a `sort=agent_code` that quietly pointed at
    `item_code` would still return 200 and would still print SOMETHING, so the AC-1
    "not a 422" check alone cannot catch it. Two rows with DIFFERENT agents, sorted
    ascending, must print their agent codes non-decreasing - and `agent_code` is the
    SAME underlying expression (`SalesAgent.sales_agent`) `agent` already sorts by, so
    the two must return the identical row sequence."""
    client, db, company_id, seeded = api
    customer = seeded["customer"]

    def _row_with_agent(agent_code: str, suffix: str, day: int) -> OrderInquiryRow:
        core = SalesOrder(
            id=_uid(),
            company_id=company_id,
            so_number=f"ZZTSO{_uid()[:8]}",
            customer_id=customer.id,
            order_date=date(2026, 5, day),
        )
        agent = SalesAgent(
            id=_uid(),
            company_id=company_id,
            sales_agent=agent_code,
            person_label=f"{MARKER} {agent_code}",
        )
        db.add_all([core, agent])
        db.flush()
        core.sales_agent_id = agent.id
        db.add(core)
        project_order = ProjectSalesOrder(
            id=_uid(),
            company_id=company_id,
            project_id=None,
            so_id=core.id,
            provisional_ref=core.so_number,
            autocount_doc_no=core.so_number,
            status="adopted",
        )
        db.add(project_order)
        db.flush()
        product = _product(db, f"ZZT-AGSORT-{_uid()[:6]}", f"{MARKER} agent sort product")
        line = ProjectSalesOrderLine(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=project_order.id,
            line_no=1,
            product_id=product.id,
            description=f"{MARKER} agent sort",
            qty=Decimal("5"),
            uom="UNIT",
            unit_price=Decimal("10.00"),
            amount=Decimal("50.00"),
            delivery_date=date(2026, 5, day),
        )
        db.add(line)
        db.flush()
        inquiry = _inquiry_for(db, company_id, project_order)
        return _row(
            db,
            company_id,
            inquiry,
            so_line_id=line.id,
            item_code=f"{MARKER}-AGENTSORT-{suffix}",
            qty="5",
            delivery_date=date(2026, 5, day),
        )

    # `item_code` is deliberately the OPPOSITE order of `agent_code` here (row_a's
    # item_code sorts last, row_b's sorts first): a sort key that quietly pointed at
    # `item_code` would print `[row_b, row_a]`, not `[row_a, row_b]` - the two orders
    # can only agree below if the key actually reads the agent.
    row_a = _row_with_agent("ZZT-AGENT-A", "ZLAST", 1)
    row_b = _row_with_agent("ZZT-AGENT-B", "AFIRST", 2)
    db.commit()

    by_agent_code = client.get(
        LIST, params={"sort": "agent_code", "dir": "asc", "query": "AGENTSORT"}
    ).json()["data"]
    assert [row["id"] for row in by_agent_code] == [row_a.id, row_b.id]
    codes = [row["agent_code"] for row in by_agent_code if row["agent_code"] is not None]
    assert codes == sorted(codes)

    by_agent = client.get(
        LIST, params={"sort": "agent", "dir": "asc", "query": "AGENTSORT"}
    ).json()["data"]
    assert [row["id"] for row in by_agent] == [row["id"] for row in by_agent_code]


def test_verb_sort_is_non_decreasing_across_two_distinct_verbs(api):
    """Kill test (review round 1): the same "not a 422" gap as `agent_code` above, for
    `verb`. Two rows with different verbs (`DELAY` sorts before `ORDER`), sorted
    ascending, must print in that order. `item_code` is deliberately the OPPOSITE order
    (the `DELAY` row's item_code sorts last, the `ORDER` row's sorts first) - a sort key
    that quietly pointed at `item_code` would print the pair reversed."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)

    delay_line = _line_on_authored_order(db, company_id, seeded, qty="5", day=21)
    delay_row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=delay_line.id,
        item_code=f"{MARKER}-VERBSORT-ZLATE",
        qty="5",
        verb=IV_DELAY,
        delivery_date=date(2026, 4, 21),
    )
    order_line = _line_on_authored_order(db, company_id, seeded, qty="5", day=22)
    order_row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=order_line.id,
        item_code=f"{MARKER}-VERBSORT-AFIRST",
        qty="5",
        verb=IV_ORDER,
        delivery_date=date(2026, 4, 22),
    )
    db.commit()

    ascending = client.get(
        LIST, params={"sort": "verb", "dir": "asc", "query": "VERBSORT"}
    ).json()["data"]
    assert [row["id"] for row in ascending] == [delay_row.id, order_row.id]
    verbs = [row["verb"] for row in ascending]
    assert verbs == sorted(verbs)


# ------------------------------------------------------------------- summary


def test_the_summary_totals_the_visible_set_and_still_lists_every_month(api):
    client, _db, _company_id, _seeded = api

    everything = client.get(f"{LIST}/summary").json()
    assert everything["total_rows"] == 3
    assert everything["total_qty"] == "182"
    assert everything["by_state"] == {
        "raised": 2,
        # The two states the links table added: some of the quantity on a document, and
        # all of it. Declared on the schema (`OrderInquiryStateCounts`) rather than left
        # to grow off the rows, because `response_model` drops a key it has not been told
        # about and the strip would have gone quietly wrong.
        "partly_linked": 0,
        "placed": 0,
        "actioned": 1,
        "cancelled": 0,
        "total": 3,
    }
    assert [entry["label"] for entry in everything["by_month"]] == ["JAN 26", "MAR 26"]
    assert everything["by_month"][0]["rows"] == 1
    assert everything["by_month"][0]["qty"] == "85"

    january = client.get(f"{LIST}/summary", params={"delivery_month": "2026-01"}).json()
    assert january["total_rows"] == 1
    # The month strip is the control that changes month, so narrowing to one month must
    # not leave the user with one tab and no way back.
    assert [entry["month"] for entry in january["by_month"]] == ["2026-01", "2026-03"]


def test_the_summary_offers_the_suppliers_and_projects_actually_present(api):
    client, db, company_id, seeded = api
    placed = _placed_supply(db, company_id, seeded["adopted_row"])

    body = client.get(f"{LIST}/summary").json()

    assert [entry["id"] for entry in body["suppliers"]] == [placed["supplier"].id]
    # The facet's `id` is the Project column's own TEXT now, not a registered project's
    # uuid (owner ask, `PLAN-oi-project-label-from-so.md` section 5).
    assert [entry["id"] for entry in body["projects"]] == [seeded["project"].title]


# --------------------------------------------------------------------- agent


def test_a_row_names_the_core_orders_sales_agent_when_the_chain_resolves(api):
    """Read off the same core sales order the SO DATE / S/O NO columns already join to."""
    client, db, company_id, seeded = api
    agent = SalesAgent(
        id=_uid(),
        company_id=company_id,
        sales_agent=f"ZZT{_uid()[:6].upper()}",
        person_label=f"{MARKER} Sean",
    )
    db.add(agent)
    db.flush()
    seeded["core"].sales_agent_id = agent.id
    db.add(seeded["core"])
    db.commit()

    body = client.get(LIST).json()
    adopted = next(row for row in body["data"] if row["id"] == seeded["adopted_row"].id)

    assert adopted["agent_code"] == agent.sales_agent
    assert adopted["agent_label"] == agent.person_label


def test_a_row_has_no_agent_when_the_chain_does_not_resolve(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST).json()
    # The adopted row's own core order carries no agent, and the authored row reaches no
    # core order at all - both are stated absences, never a guess.
    adopted = next(row for row in body["data"] if row["id"] == seeded["adopted_row"].id)
    authored = next(row for row in body["data"] if row["id"] == seeded["authored_row"].id)

    assert adopted["agent_code"] is None
    assert authored["agent_code"] is None


def test_sorting_by_agent_is_accepted(api):
    client, _db, _company_id, _seeded = api

    response = client.get(LIST, params={"sort": "agent"})

    assert response.status_code == 200, response.text


# -------------------------------------------------------------------- export


def test_the_workbook_is_one_sheet_for_every_delivery_month_with_their_heading_row(api):
    """Owner, 16 Oct 2026: "our OI excel cannot split by month, this applies to all places
    where OI excel is printed". January, March and the undated row share ONE sheet."""
    client, _db, _company_id, _seeded = api

    response = client.get(f"{LIST}/export")

    assert response.status_code == 200, response.text
    assert "attachment;" in response.headers["content-disposition"]
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["ORDER INQUIRY"]
    sheet = book["ORDER INQUIRY"]
    assert sheet.cell(row=1, column=1).value == "ORDER INQUIRY"
    assert [cell.value for cell in sheet[2][:9]] == [
        "SO DATE",
        "S/O NO",
        "ITEM CODE",
        "QTY",
        "TOTAL QTY",
        "DELIVERY DATE",
        "PROJECT/CUSTOMER",
        "SUPPLIER",
        "PO NO ",
    ]
    assert sheet.cell(row=2, column=10).value == "LOCATION"
    assert sheet.max_row == 2 + 3
    delivery_dates = [
        sheet.cell(row=r, column=6).value for r in range(3, sheet.max_row + 1)
    ]
    assert {
        value.date() if hasattr(value, "date") else value for value in delivery_dates
    } == {date(2026, 1, 19), date(2026, 3, 2), None}


def test_the_workbooks_supplier_is_the_source_pos_on_a_via_spo_row(api):
    """OI-SUPPLIER-FROM-PO (owner, 9 Oct 2026: "be it system view, printouts"): the
    SUPPLIER column of the printout names the PO's supplier, never the SPO's own."""
    client, db, company_id, seeded = api
    po = _purchase_order(db, company_id)
    _row_, spo_supplier = _spo_linked_row_naming_po(
        db, company_id, seeded, po["order"].po_number
    )
    po_supplier = db.get(Supplier, po["order"].supplier_id)

    response = client.get(f"{LIST}/export")

    assert response.status_code == 200, response.text
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    suppliers = {
        sheet.cell(row=r, column=8).value
        for sheet in book.worksheets
        for r in range(3, sheet.max_row + 1)
    }
    assert po_supplier.supplier_name in suppliers
    assert spo_supplier.supplier_name not in suppliers


def test_the_workbook_honours_the_filters_the_screen_is_showing(api):
    client, _db, _company_id, _seeded = api

    response = client.get(f"{LIST}/export", params={"delivery_month": "2026-03"})

    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["ORDER INQUIRY"]
    assert book["ORDER INQUIRY"].max_row == 2 + 1


def test_total_qty_totals_the_supplier_and_item_run_not_the_item_across_suppliers(api):
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["adopted_row"].order_inquiry_id)
    abc = f"ZZT-ABC-{_uid()[:6]}"
    dfe = f"ZZT-DEF-{_uid()[:6]}"

    def raised(code: str, qty: str, day: int) -> OrderInquiryRow:
        return _row(
            db,
            company_id,
            inquiry,
            item_code=code,
            qty=qty,
            delivery_date=date(2026, 5, day),
        )

    # Supplier A: ABC 10 + ABC 4, DEF 5. Supplier B: ABC 3.
    a_abc_first, a_abc_second, a_def, b_abc = (
        raised(abc, "10", 4),
        raised(abc, "4", 5),
        raised(dfe, "5", 4),
        raised(abc, "3", 4),
    )
    supplier_a = _placed_supply(
        db, company_id, a_abc_first, supplier_name=f"{MARKER} A FACTORY"
    )["supplier"]
    _placed_supply(db, company_id, a_abc_second, supplier=supplier_a)
    _placed_supply(db, company_id, a_def, supplier=supplier_a)
    _placed_supply(db, company_id, b_abc, supplier_name=f"{MARKER} B FACTORY")

    response = client.get(f"{LIST}/export", params={"delivery_month": "2026-05"})

    book = openpyxl.load_workbook(io.BytesIO(response.content))
    sheet = book["ORDER INQUIRY"]
    body = [[cell.value for cell in row[:9]] for row in sheet.iter_rows(min_row=3)]
    assert [(row[7], row[2], row[3], row[4]) for row in body] == [
        (supplier_a.supplier_name, abc, 10.0, None),
        (supplier_a.supplier_name, abc, 4.0, 14.0),
        (supplier_a.supplier_name, dfe, 5.0, None),
        (f"{MARKER} B FACTORY", abc, 3.0, None),
    ]


def test_an_empty_export_is_still_a_workbook_a_person_can_open(api):
    client, _db, _company_id, _seeded = api

    response = client.get(f"{LIST}/export", params={"query": "zzt-nothing-matches"})

    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["ORDER INQUIRY"]
    assert book.worksheets[0].max_row == 2


# ---------------------------------------------------- taken from PO / remaining open


def _line_on_authored_order(db, company_id: str, seeded: dict, *, qty: str, day: int):
    """A fresh SO line on the seed's own authored order, so its rows are addressable
    through the existing project inquiry without disturbing the seed's own two rows."""
    line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=seeded["authored"].id,
        line_no=99,
        product_id=_product(db, f"ZZT-FLOW-{_uid()[:6]}", f"{MARKER} flow line").id,
        description=f"{MARKER} flow line",
        qty=Decimal(qty),
        uom="UNIT",
        unit_price=Decimal("10.00"),
        amount=Decimal("0"),
        delivery_date=date(2026, 4, day),
    )
    db.add(line)
    db.flush()
    return line


def test_taken_from_po_and_remaining_open_read_the_links_not_a_split(api):
    """One line, 19 owed, 5 of it on a purchase order (AC-I6/AC-I7).

    The cascade used to SPLIT the line into a placed 5 and a raised 14, and the pair was
    "sum the placed siblings" against "sum the raised siblings". A row keeps its full
    quantity now and carries links, so the pair is "sum the links" against "sum of
    `qty - linked`" - the same two numbers, off the table that actually records them, and
    on ONE instruction rather than two.
    """
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line = _line_on_authored_order(db, company_id, seeded, qty="19", day=1)
    po_line = _purchase_order(db, company_id)["line"]
    row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=f"{MARKER}-SPLIT",
        qty="19",
        state="partly_linked",
        delivery_date=date(2026, 4, 1),
    )
    db.add(
        OrderInquiryLink(
            id=_uid(),
            company_id=company_id,
            row_id=row.id,
            po_line_id=po_line.id,
            document="ZZT-PO-LINKED",
            qty=Decimal("5"),
        )
    )
    db.commit()

    body = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    by_id = {entry["id"]: entry for entry in body["data"]}

    assert by_id[row.id]["taken_from_po"] == "5"
    assert by_id[row.id]["remaining_open"] == "14"
    assert by_id[row.id]["linked_qty"] == "5"
    assert [link["qty"] for link in by_id[row.id]["links"]] == ["5"]


def test_a_redirected_placed_row_no_longer_counts_toward_taken_from_po(api):
    """The captain's ruling, 21 Aug 2026: a placed row a planning change REDIRECTED to
    replenish the shared pool is real placed quantity, just not this line's anymore - it
    must not count toward `taken_from_po` (or it would read as this line's need being
    covered by a PO that is actually bound for the pool). The redirected row's OWN cells
    still show what it now is: its `location` reads the pool, and its `note` says so."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line = _line_on_authored_order(db, company_id, seeded, qty="20", day=3)
    redirected = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=f"{MARKER}-REDIRECT",
        qty="12",
        state=INQUIRY_PLACED,
        delivery_date=date(2026, 4, 3),
        redirected_to_pool=True,
        stock_location="ZZT-BRW",
        note="Redirected to replenish ZZT-BRW (was ZZT-OWN) - planning change batch abcd1234.",
    )
    raised = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=f"{MARKER}-REDIRECT",
        qty="8",
        state=INQUIRY_RAISED,
        delivery_date=date(2026, 4, 3),
    )
    db.commit()

    body = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    by_id = {row["id"]: row for row in body["data"]}

    # Neither row counts the redirected 12 as taken - it does not serve this line anymore.
    assert by_id[redirected.id]["taken_from_po"] == "0"
    assert by_id[raised.id]["taken_from_po"] == "0"
    assert by_id[raised.id]["remaining_open"] == "8"
    # The redirected row's own cells name what actually happened to it.
    assert by_id[redirected.id]["location"] == "ZZT-BRW"
    assert "Redirected" in (by_id[redirected.id]["note"] or "")


def test_worklist_stage_totals_exclude_a_redirected_row(api):
    """AC-RL-16 (`PLAN-oi-replan-received-links.md` S3): a redirected row's quantity is
    not owed anywhere any more - the Buy / Purchased / Incoming cards (`_kinds`) must not
    move when one is added, on top of the taken_from_po / remaining_open exclusion the
    sibling test above already covers. The row's own `redirected_to_pool` also has to
    reach the wire - `response_model` silently drops a field it is not told about."""
    client, db, company_id, seeded = api
    before = client.get(f"{LIST}/summary").json()["kinds"]

    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line = _line_on_authored_order(db, company_id, seeded, qty="158", day=6)
    po_line = _purchase_order(db, company_id)["line"]
    redirected = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=f"{MARKER}-REDIRECT-KIND",
        qty="158",
        state=INQUIRY_PLACED,
        delivery_date=date(2026, 4, 6),
        redirected_to_pool=True,
        po_line_id=po_line.id,
    )
    db.commit()

    after = client.get(f"{LIST}/summary").json()["kinds"]
    body = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    wire_row = next(r for r in body["data"] if r["id"] == redirected.id)

    assert wire_row["redirected_to_pool"] is True
    assert wire_row["taken_from_po"] == "0"
    assert wire_row["remaining_open"] == "0"
    assert after == before, "a redirected row's quantity must not move any card"


def test_worklist_kind_buy_excludes_a_redirected_rows_unlinked_remainder(api):
    """AC-RL-16c (S1, code review 17 Sep): `_kinds`' own summary already excludes a
    redirected row (the sibling test above), but the LIST's `kind=buy` row filter
    (`order_inquiry_worklist_service.py` ~:892, `_UNLINKED_QTY > 0`) is a SEPARATE
    query with no `redirected_to_pool` exclusion of its own - a redirected row that
    is only PARTLY linked (its unlinked remainder still positive) still lists under
    `kind=buy`, so a click into the Buy card shows a row the card's own number has
    already excluded. The plan scenario: a redirected row of 182 (158 linked, 24
    still unlinked) beside a fresh row of 220 - the rows returned for `kind=buy`
    must sum to 220, never 244 (220 + the redirected row's own unlinked 24)."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    item_code = f"{MARKER}-BUYKIND"
    line = _line_on_authored_order(db, company_id, seeded, qty="182", day=8)
    po_line = _purchase_order(db, company_id)["line"]
    redirected = _row(
        db, company_id, inquiry, so_line_id=line.id, item_code=item_code,
        qty="182", state="partly_linked", delivery_date=date(2026, 4, 8),
        redirected_to_pool=True,
    )
    db.add(OrderInquiryLink(
        id=_uid(), company_id=company_id, row_id=redirected.id,
        po_line_id=po_line.id, document="ZZT-PO-BUYKIND", qty=Decimal("158"),
    ))
    _fresh = _row(
        db, company_id, inquiry, so_line_id=line.id, item_code=item_code,
        qty="220", state=INQUIRY_RAISED, delivery_date=date(2026, 4, 8),
    )
    db.commit()

    body = client.get(LIST, params={"kind": "buy", "delivery_month": "2026-04"}).json()
    matching = [r for r in body["data"] if r["item_code"] == item_code]
    total_unlinked = sum(
        (Decimal(r["qty"]) - Decimal(r["linked_qty"]) for r in matching), Decimal("0")
    )
    assert total_unlinked == Decimal("220"), matching


def test_link_dict_carries_received_qty_and_received(api):
    """AC-RL-17: `links_for_rows` states the receipt figure on every link, PO or SPO -
    `received_qty` always, `received` once the document is fully received - and
    `OrderInquiryLinkOut` ships both, through the worklist ROUTE (`response_model`
    silently drops a field it is not told about)."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line = _line_on_authored_order(db, company_id, seeded, qty="200", day=7)
    row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=f"{MARKER}-RECEIVED",
        qty="200",
        state="partly_linked",
        delivery_date=date(2026, 4, 7),
    )
    supplier = Supplier(
        id=_uid(),
        company_id=company_id,
        supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} received supplier",
    )
    warehouse = Warehouse(
        id=_uid(),
        company_id=company_id,
        warehouse_code=f"ZZT{_uid()[:6]}",
        warehouse_name=f"{MARKER} WH",
    )
    db.add_all([supplier, warehouse])
    db.flush()
    allocation = SPOAllocation(
        id=_uid(),
        company_id=company_id,
        spo_number=f"ZZT-SPO-RECV-{_uid()[:6]}",
        product_id=_product(db, f"ZZT-P-RECV-{_uid()[:6]}", f"{MARKER} recv product").id,
        warehouse_id=warehouse.id,
        allocated_quantity=Decimal("158"),
        quantity_received=Decimal("158"),
        receipt_status="fully_received",
        line_status="closed",
    )
    po = PurchaseOrder(
        id=_uid(),
        company_id=company_id,
        po_number=f"ZZT-PO-OPEN-{_uid()[:6]}",
        supplier_id=supplier.id,
    )
    db.add_all([allocation, po])
    db.flush()
    open_line = PurchaseOrderLine(
        id=_uid(),
        company_id=company_id,
        purchase_order_id=po.id,
        product_id=allocation.product_id,
        warehouse_id=warehouse.id,
        qty_ordered=Decimal("42"),
        qty_received=Decimal("0"),
        line_status="open",
    )
    db.add(open_line)
    db.flush()
    db.add_all(
        [
            OrderInquiryLink(
                id=_uid(),
                company_id=company_id,
                row_id=row.id,
                spo_allocation_id=allocation.id,
                document=allocation.spo_number,
                qty=Decimal("158"),
            ),
            OrderInquiryLink(
                id=_uid(),
                company_id=company_id,
                row_id=row.id,
                po_line_id=open_line.id,
                document=po.po_number,
                qty=Decimal("42"),
            ),
        ]
    )
    db.commit()

    body = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row.id)
    by_document = {link["document"]: link for link in wire_row["links"]}

    spo_link = by_document[allocation.spo_number]
    assert spo_link["received"] is True
    assert spo_link["received_qty"] == "158"

    po_link = by_document[po.po_number]
    assert po_link["received"] is False
    assert po_link["received_qty"] == "0"


# ---------------------------------------------------------------------------
# S1b: the reallocate / unlink suggestion on an open link
# (`PLAN-oi-replan-received-links.md` S1b, AC-RL-20 to AC-RL-24)
# ---------------------------------------------------------------------------


def _lead_time(db, company_id: str, product: Product, *, days: int) -> ProductSupplier:
    """The STATED source `ProjectSupplyService.lead_times` reads (`standard_lead_time_
    days`) - no `SupplierPerformance` row is seeded, so the MEASURED source never
    outranks it."""
    supplier = Supplier(
        id=_uid(),
        company_id=company_id,
        supplier_code=f"ZZT-{_uid()[:8]}",
        supplier_name=f"{MARKER} lead supplier",
    )
    db.add(supplier)
    db.flush()
    row = ProductSupplier(
        id=_uid(),
        company_id=company_id,
        product_id=product.id,
        supplier_id=supplier.id,
        standard_lead_time_days=days,
    )
    db.add(row)
    db.flush()
    return row


def _line_for(
    db, company_id: str, seeded: dict, *, product: Product, qty: str, delivery_date, line_no: int
) -> ProjectSalesOrderLine:
    """A fresh line on the seed's own AUTHORED order - the row this suggestion is
    ABOUT, named the way `_line_on_authored_order` is, but for an explicit product
    rather than a fresh one per call: a suggestion trigger needs several rows sharing
    ONE product."""
    line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=seeded["authored"].id,
        line_no=line_no,
        product_id=product.id,
        description=f"{MARKER} suggestion line",
        qty=Decimal(qty),
        uom="UNIT",
        unit_price=Decimal("10.00"),
        amount=Decimal("0"),
        delivery_date=delivery_date,
    )
    db.add(line)
    db.flush()
    return line


def _candidate_row(
    db, company_id: str, *, product: Product, qty: str, delivery_date, so_number: str
):
    """A row on its OWN sales order (the adopted shape `_seed` already uses) - the
    candidate a suggestion may name. Its own SO, its own inquiry, so `suggestion.
    inquiry_no` / `so_number` genuinely trace back to IT rather than being read off
    whatever inquiry the row under test happens to share."""
    core = SalesOrder(
        id=_uid(), company_id=company_id, so_number=so_number, order_date=date(2026, 1, 1)
    )
    db.add(core)
    db.flush()
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=None,
        so_id=core.id,
        provisional_ref=so_number,
        autocount_doc_no=so_number,
        status="adopted",
    )
    db.add(order)
    db.flush()
    line = ProjectSalesOrderLine(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=order.id,
        line_no=1,
        product_id=product.id,
        description=f"{MARKER} candidate",
        qty=Decimal(qty),
        uom="UNIT",
        unit_price=Decimal("10.00"),
        amount=Decimal("0"),
        delivery_date=delivery_date,
    )
    db.add(line)
    db.flush()
    inquiry = _inquiry_for(db, company_id, order)
    row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=product.product_code,
        qty=qty,
        state=INQUIRY_RAISED,
        delivery_date=delivery_date,
        ack_state=ACK_ACKNOWLEDGED,
    )
    return order, inquiry, row


def _early_link(
    db, company_id: str, seeded: dict, inquiry, *, product, qty, delivery_date, expected_date,
    line_no,
):
    """A row of `qty` wholly on ONE open purchase-order line whose `expected_date`
    the caller states - the shape every S1b scenario starts from."""
    line = _line_for(
        db, company_id, seeded, product=product, qty=qty, delivery_date=delivery_date,
        line_no=line_no,
    )
    po_line = _purchase_order(db, company_id, expected_date=expected_date)["line"]
    # `_purchase_order` mints its OWN product - this link has to be on the SAME one
    # the row (and the lead time) is seeded for. `_purchase_order`'s own `expected_date`
    # kwarg only stamps the ORDER header, never the LINE `links_for_rows` actually reads
    # (`PurchaseOrderLine.expected_date`), and its line is seeded `qty_received=15`
    # unconditionally - both restated here so this really is the OPEN line at the
    # caller's own `expected_date` every S1b scenario needs.
    po_line.product_id = product.id
    po_line.qty_ordered = Decimal(qty)
    po_line.qty_received = Decimal("0")
    po_line.expected_date = expected_date
    db.flush()
    row = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=product.product_code,
        qty=qty,
        state="partly_linked",
        delivery_date=delivery_date,
        po_line_id=po_line.id,
    )
    return row


def test_link_suggests_reallocate_to_every_sooner_open_row(api):
    """AC-RL-20 (17 Sep rulings): an open link that lands well inside the product's
    lead-time window suggests reallocating to EVERY OTHER linkable row of the same
    product with open need - never a row on the SAME SO line - ordered delivery date
    ascending then open need descending; the first candidate is the suggested
    target."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)

    # -- earliest date wins, and every qualifying row is listed
    product = _product(db, f"ZZT-SUGGEST-{_uid()[:6]}", f"{MARKER} suggest")
    _lead_time(db, company_id, product, days=60)
    row_x = _early_link(
        db, company_id, seeded, inquiry, product=product, qty="158",
        delivery_date=date(2027, 4, 1), expected_date=date(2026, 9, 1), line_no=201,
    )
    # W: on the SAME SO line as X - must never be offered, however early its own date.
    _row(
        db, company_id, inquiry, so_line_id=row_x.so_line_id, item_code=product.product_code,
        qty="20", state=INQUIRY_RAISED, delivery_date=date(2026, 10, 1),
        ack_state=ACK_ACKNOWLEDGED,
    )
    _order_y, inquiry_y, row_y = _candidate_row(
        db, company_id, product=product, qty="90", delivery_date=date(2026, 12, 1),
        so_number=f"ZZT-CANDY-{_uid()[:6]}",
    )
    _order_z, inquiry_z, row_z = _candidate_row(
        db, company_id, product=product, qty="300", delivery_date=date(2027, 1, 15),
        so_number=f"ZZT-CANDZ-{_uid()[:6]}",
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row_x.id)
    [link] = wire_row["links"]
    suggestion = link["suggestion"]

    assert suggestion["kind"] == "reallocate"
    assert [c["inquiry_no"] for c in suggestion["candidates"]] == [
        inquiry_y.inquiry_no, inquiry_z.inquiry_no,
    ], "Y (2026-12-01) must lead Z (2027-01-15) - earliest date first"

    first = suggestion["candidates"][0]
    assert first["item_code"] == product.product_code
    assert first["so_number"] == _order_y.autocount_doc_no
    assert first["delivery_date"] == "2026-12-01"
    assert first["open_qty"] == "90"

    second = suggestion["candidates"][1]
    assert second["item_code"] == product.product_code
    assert second["so_number"] == _order_z.autocount_doc_no
    assert second["delivery_date"] == "2027-01-15"
    assert second["open_qty"] == "300"

    # -- tie on date: the LARGER open need leads
    product2 = _product(db, f"ZZT-TIE-{_uid()[:6]}", f"{MARKER} tie")
    _lead_time(db, company_id, product2, days=60)
    row_x2 = _early_link(
        db, company_id, seeded, inquiry, product=product2, qty="50",
        delivery_date=date(2027, 4, 1), expected_date=date(2026, 9, 1), line_no=211,
    )
    _order_small, inquiry_small, _row_small = _candidate_row(
        db, company_id, product=product2, qty="40", delivery_date=date(2026, 12, 1),
        so_number=f"ZZT-TIESMALL-{_uid()[:6]}",
    )
    _order_big, inquiry_big, _row_big = _candidate_row(
        db, company_id, product=product2, qty="120", delivery_date=date(2026, 12, 1),
        so_number=f"ZZT-TIEBIG-{_uid()[:6]}",
    )
    db.commit()

    body2 = client.get(LIST, params={"limit": 200}).json()
    wire_row2 = next(r for r in body2["data"] if r["id"] == row_x2.id)
    [link2] = wire_row2["links"]
    suggestion2 = link2["suggestion"]

    assert suggestion2["kind"] == "reallocate"
    assert [c["inquiry_no"] for c in suggestion2["candidates"]] == [
        inquiry_big.inquiry_no, inquiry_small.inquiry_no,
    ], "same date on both - the larger open need (120) must lead the smaller (40)"
    assert [c["open_qty"] for c in suggestion2["candidates"]] == ["120", "40"]


def test_link_suggests_unlink_when_no_sooner_row(api):
    """AC-RL-21: the same early-link shape, with no candidate at all (a decoy of a
    DIFFERENT product is seeded to prove it is not offered) - `suggestion.kind` reads
    `unlink`."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)

    product = _product(db, f"ZZT-NOSOON-{_uid()[:6]}", f"{MARKER} no sooner")
    _lead_time(db, company_id, product, days=60)
    row_x = _early_link(
        db, company_id, seeded, inquiry, product=product, qty="80",
        delivery_date=date(2027, 4, 1), expected_date=date(2026, 9, 1), line_no=301,
    )
    other_product = _product(db, f"ZZT-OTHERP-{_uid()[:6]}", f"{MARKER} other product")
    _candidate_row(
        db, company_id, product=other_product, qty="10", delivery_date=date(2026, 11, 1),
        so_number=f"ZZT-DECOY-{_uid()[:6]}",
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row_x.id)
    [link] = wire_row["links"]

    # Unchanged shape (17 Sep rulings): `{"kind": "unlink"}` and nothing else.
    assert link["suggestion"] == {"kind": "unlink"}


def test_no_suggestion_inside_lead_time_or_received(api):
    """AC-RL-22: a link inside the lead-time window (not enough slack to redirect and
    still rebuy in time) states no suggestion, and neither does a fully received
    link - however early it landed - even with a real candidate waiting."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)

    # -- inside the window: only 30 days of slack against a 60-day lead time.
    product_a = _product(db, f"ZZT-INWIN-{_uid()[:6]}", f"{MARKER} in window")
    _lead_time(db, company_id, product_a, days=60)
    row_a = _early_link(
        db, company_id, seeded, inquiry, product=product_a, qty="40",
        delivery_date=date(2027, 4, 14), expected_date=date(2027, 3, 15), line_no=401,
    )
    _candidate_row(
        db, company_id, product=product_a, qty="15", delivery_date=date(2026, 10, 1),
        so_number=f"ZZT-INWINCAND-{_uid()[:6]}",
    )

    # -- fully received: goods are in, whatever the date says.
    product_b = _product(db, f"ZZT-RECV-{_uid()[:6]}", f"{MARKER} received")
    _lead_time(db, company_id, product_b, days=60)
    row_b = _early_link(
        db, company_id, seeded, inquiry, product=product_b, qty="25",
        delivery_date=date(2027, 4, 1), expected_date=date(2025, 1, 1), line_no=402,
    )
    po_line_b = db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row_b.id).one().po_line_id
    db.query(PurchaseOrderLine).filter(PurchaseOrderLine.id == po_line_b).update(
        {"qty_received": Decimal("25")}
    )
    _candidate_row(
        db, company_id, product=product_b, qty="30", delivery_date=date(2026, 10, 1),
        so_number=f"ZZT-RECVCAND-{_uid()[:6]}",
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_a = next(r for r in body["data"] if r["id"] == row_a.id)
    wire_b = next(r for r in body["data"] if r["id"] == row_b.id)
    [link_a] = wire_a["links"]
    [link_b] = wire_b["links"]

    assert link_a["suggestion"] is None
    assert link_b["received"] is True, "the fixture has to be genuinely received for this to mean anything"
    assert link_b["suggestion"] is None


def test_worklist_api_declares_link_suggestion(api):
    """AC-RL-23 (first half): `OrderInquiryLinkOut` declares `suggestion` - through the
    worklist ROUTE, so `response_model` is what is actually exercised."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)

    product = _product(db, f"ZZT-WIRE-{_uid()[:6]}", f"{MARKER} wire")
    _lead_time(db, company_id, product, days=60)
    row = _early_link(
        db, company_id, seeded, inquiry, product=product, qty="15",
        delivery_date=date(2027, 4, 1), expected_date=date(2026, 9, 1), line_no=501,
    )
    db.commit()

    body = client.get(LIST, params={"limit": 200}).json()
    wire_row = next(r for r in body["data"] if r["id"] == row.id)
    [link] = wire_row["links"]

    assert "suggestion" in link
    assert link["suggestion"]["kind"] == "unlink"


def test_suggestion_is_one_grouped_query_per_page(api):
    """AC-RL-23 (second half): the suggestion is computed with ONE grouped query per
    page, the same "five bulk maps, then a query-free `_serialize`" pattern the
    worklist already uses - so the SQL statement count for a page of 5 suggestion-
    bearing rows must not exceed a page of 1."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    connection = db.get_bind()

    def _seed_early_row(tag: str, line_no: int):
        product = _product(db, f"ZZT-{tag}-{_uid()[:6]}", f"{MARKER} {tag}")
        _lead_time(db, company_id, product, days=60)
        return _early_link(
            db, company_id, seeded, inquiry, product=product, qty="50",
            delivery_date=date(2026, 6, 1), expected_date=date(2025, 1, 1),
            line_no=line_no,
        )

    _seed_early_row("QCOUNT-ONE", 601)
    for i in range(5):
        _seed_early_row(f"QCOUNT-FIVE{i}", 610 + i)
    db.commit()

    def _query_count(query: str) -> tuple:
        # SELECT only: a SAVEPOINT / RELEASE the test's OWN transaction management
        # emits per request is not a query the route issued, and counting it would
        # measure the harness rather than the page's own read pattern.
        calls: list = []

        def _capture(conn, cursor, statement, *_a, **_kw):
            if statement.strip().upper().startswith("SELECT"):
                calls.append(statement)

        event.listen(connection, "before_cursor_execute", _capture)
        try:
            response = client.get(LIST, params={"query": query, "limit": 50})
        finally:
            event.remove(connection, "before_cursor_execute", _capture)
        assert response.status_code == 200, response.text
        return response, calls

    resp_one, calls_one = _query_count("ZZT-QCOUNT-ONE")
    resp_five, calls_five = _query_count("ZZT-QCOUNT-FIVE")

    assert len(resp_one.json()["data"]) == 1
    assert len(resp_five.json()["data"]) == 5
    assert len(calls_five) == len(calls_one), (
        "the suggestion lookup must be one grouped query per page, not one per row: "
        f"{len(calls_one)} statements for 1 row, {len(calls_five)} for 5"
    )


def test_an_unplaced_lines_row_reports_zero_taken_and_its_full_qty_as_remaining(api):
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line = _line_on_authored_order(db, company_id, seeded, qty="12", day=2)
    raised = _row(
        db,
        company_id,
        inquiry,
        so_line_id=line.id,
        item_code=f"{MARKER}-UNPLACED",
        qty="12",
        delivery_date=date(2026, 4, 2),
    )
    db.commit()

    body = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    row = next(r for r in body["data"] if r["id"] == raised.id)

    assert row["taken_from_po"] == "0"
    assert row["remaining_open"] == "12"


# ---------------------------------------------------------------- the po popup


def test_the_po_detail_route_answers_the_header_and_every_line(api):
    client, db, company_id, _seeded = api
    placed = _purchase_order(db, company_id)

    response = client.get(f"{BASE}/order-inquiries/po/{placed['order'].id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["po_number"] == placed["order"].po_number
    assert body["supplier_code"] == placed["supplier"].supplier_code
    assert body["supplier_name"] == placed["supplier"].supplier_name
    assert body["expected_date"] == "2026-05-01"
    assert body["status"] == "active"
    assert len(body["lines"]) == 1
    line = body["lines"][0]
    assert line["sku"] == placed["product"].product_code
    assert line["product_name"] == placed["product"].product_name
    assert line["qty_ordered"] == "40"
    assert line["qty_received"] == "15"
    assert line["remaining"] == "25"
    assert line["location"] == placed["warehouse"].warehouse_code


def test_the_po_detail_route_404s_on_a_po_from_another_company(api):
    client, db, _company_id, _seeded = api
    from app.models.base import company_scope
    from app.models.company import Company

    other_id = _uid()
    with company_scope(db, None):
        db.add(Company(id=other_id, name=f"{MARKER} Other PO Co", code=f"ZZ{_uid()[:6]}"))
        db.flush()
        placed = _purchase_order(db, other_id)

    response = client.get(f"{BASE}/order-inquiries/po/{placed['order'].id}")

    assert response.status_code == 404


def test_the_po_detail_route_404s_on_an_unknown_id(api):
    client, _db, _company_id, _seeded = api

    response = client.get(f"{BASE}/order-inquiries/po/{_uid()}")

    assert response.status_code == 404


def test_the_po_detail_route_denies_a_user_without_the_view_permission(stranger_api):
    client, db, company_id, _seeded = stranger_api
    placed = _purchase_order(db, company_id)

    response = client.get(f"{BASE}/order-inquiries/po/{placed['order'].id}")

    assert response.status_code == 403


# ---------------------------------------------------------- auth and company


def test_a_user_without_the_module_read_is_refused(stranger_api):
    client, _db, _company_id, _seeded = stranger_api

    assert client.get(LIST).status_code == 403
    assert client.get(f"{LIST}/summary").status_code == 403
    assert client.get(f"{LIST}/export").status_code == 403


def test_another_companys_rows_are_not_on_this_companys_list(api):
    client, db, _company_id, _seeded = api
    from app.models.base import company_scope

    other_id = _uid()
    # The seed itself has to run unscoped, and it has to hand the scope BACK: leaving the
    # session on the all-companies scope would make this test pass by making every other
    # company visible, which is the opposite of what it is checking.
    with company_scope(db, None):
        db.add(Company(id=other_id, name=f"{MARKER} Other", code=f"ZZ{_uid()[:6]}"))
        db.flush()
        order = ProjectSalesOrder(
            id=_uid(),
            company_id=other_id,
            project_id=None,
            provisional_ref=f"ZZT-OTHER-{_uid()[:8]}",
            autocount_doc_no="ZZT-OTHER-SO",
            status="adopted",
        )
        db.add(order)
        db.flush()
        inquiry = _inquiry_for(db, other_id, order)
        _row(db, other_id, inquiry, item_code="ZZT-OTHER-ITEM", qty="1")

    body = client.get(LIST).json()

    assert all(row["item_code"] != "ZZT-OTHER-ITEM" for row in body["data"])
    assert body["pagination"]["total"] == 3


# ------------------------------------------------------- bundled_host_changes


def _companion_rule(db, company_id, companion, hosts, *, supplier_id=None, ratio="1"):
    """One active companion rule, hosts in the order they must read back in
    (`PLAN-oi-bundled-row-host-change.md`: the (i) lists each host in RULE order).
    """
    from app.models.product_companion import ProductCompanionRule, ProductCompanionRuleHost

    rule = ProductCompanionRule(
        id=_uid(),
        company_id=company_id,
        companion_product_id=companion.id,
        supplier_id=supplier_id,
        ratio=Decimal(ratio),
        is_active=True,
    )
    db.add(rule)
    db.flush()
    for host in hosts:
        db.add(ProductCompanionRuleHost(rule_id=rule.id, host_product_id=host.id))
    db.flush()
    return rule


def test_a_bundled_row_lists_each_hosts_own_change_in_rule_order(api):
    """A bundled row's (i) reads each HOST's own change, not the companion's own row
    (the companion has none of its own: no sheet row, no PO, no Was). Host X carries a
    Was (`previous_qty` 182, `previous_delivery_date` 2026-06-01, `qty` 280 @
    2027-03-01); host Y has never changed. Both entries come back, in rule order,
    values exact."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    host_x = _product(db, f"ZZT-HOSTX-{_uid()[:6]}", f"{MARKER} host X")
    host_y = _product(db, f"ZZT-HOSTY-{_uid()[:6]}", f"{MARKER} host Y")
    companion = _product(db, f"ZZT-SC-{_uid()[:6]}", f"{MARKER} seat cover")
    _companion_rule(db, company_id, companion, [host_x, host_y])

    host_x_row = _row(
        db,
        company_id,
        inquiry,
        item_code=host_x.product_code,
        qty="280",
        delivery_date=date(2027, 3, 1),
        previous_qty=Decimal("182"),
        previous_delivery_date=date(2026, 6, 1),
    )
    host_y_row = _row(
        db,
        company_id,
        inquiry,
        item_code=host_y.product_code,
        qty="50",
        delivery_date=date(2026, 5, 1),
    )
    companion_row = _row(
        db,
        company_id,
        inquiry,
        item_code=companion.product_code,
        qty="2",
        bundled_qty=Decimal("2"),
        bundled_with_row_id=host_x_row.id,
    )
    db.commit()

    body = client.get(LIST, params={"limit": 100}).json()
    row = next(r for r in body["data"] if r["id"] == companion_row.id)

    assert row["bundled_host_changes"] == [
        {
            "item_code": host_x.product_code,
            "qty": "280",
            "delivery_date": "2027-03-01",
            "previous_qty": "182",
            "previous_delivery_date": "2026-06-01",
        },
        {
            "item_code": host_y.product_code,
            "qty": "50",
            "delivery_date": "2026-05-01",
            "previous_qty": None,
            "previous_delivery_date": None,
        },
    ], row["bundled_host_changes"]
    assert host_y_row.id  # host Y's row exists; only its VALUES are read, not its id


def test_a_non_bundled_row_carries_no_host_changes(api):
    """A row nobody's rule ever bundled reads `bundled_host_changes` null - never an
    empty list, never a key error."""
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"limit": 100}).json()
    row = next(r for r in body["data"] if r["id"] == seeded["authored_row"].id)

    assert row["bundled_host_changes"] is None


def test_a_host_with_no_row_at_all_still_gets_a_null_entry(api):
    """A rule's second host has never had a row raised for it at all - the entry for
    that host's item code still comes back, with every row field null (never a
    shortened list)."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    host_x = _product(db, f"ZZT-HOSTX-{_uid()[:6]}", f"{MARKER} host X")
    host_y = _product(db, f"ZZT-HOSTY-{_uid()[:6]}", f"{MARKER} host Y (never raised)")
    companion = _product(db, f"ZZT-SC-{_uid()[:6]}", f"{MARKER} seat cover")
    _companion_rule(db, company_id, companion, [host_x, host_y])

    host_x_row = _row(
        db,
        company_id,
        inquiry,
        item_code=host_x.product_code,
        qty="10",
        delivery_date=date(2026, 4, 1),
    )
    companion_row = _row(
        db,
        company_id,
        inquiry,
        item_code=companion.product_code,
        qty="1",
        bundled_qty=Decimal("1"),
        bundled_with_row_id=host_x_row.id,
    )
    db.commit()

    body = client.get(LIST, params={"limit": 100}).json()
    row = next(r for r in body["data"] if r["id"] == companion_row.id)

    assert row["bundled_host_changes"][1] == {
        "item_code": host_y.product_code,
        "qty": None,
        "delivery_date": None,
        "previous_qty": None,
        "previous_delivery_date": None,
    }, row["bundled_host_changes"]


def test_a_hosts_cancelled_and_redirected_rows_are_excluded(api):
    """A host's own CANCELLED row and a REDIRECTED (back to the pool) row are both
    excluded from `bundled_host_changes` - only a LIVE row of the host's own item code
    counts, so a superseded or pooled row never reports as that host's current change."""
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    host_x = _product(db, f"ZZT-HOSTX-{_uid()[:6]}", f"{MARKER} host X")
    host_y = _product(db, f"ZZT-HOSTY-{_uid()[:6]}", f"{MARKER} host Y (no live row)")
    companion = _product(db, f"ZZT-SC-{_uid()[:6]}", f"{MARKER} seat cover")
    _companion_rule(db, company_id, companion, [host_x, host_y])

    host_x_row = _row(
        db,
        company_id,
        inquiry,
        item_code=host_x.product_code,
        qty="10",
        delivery_date=date(2026, 4, 1),
    )
    # Host Y carries only a cancelled row and a redirected one - never a live row.
    _row(
        db,
        company_id,
        inquiry,
        item_code=host_y.product_code,
        qty="5",
        delivery_date=date(2026, 4, 5),
        state="cancelled",
    )
    _row(
        db,
        company_id,
        inquiry,
        item_code=host_y.product_code,
        qty="7",
        delivery_date=date(2026, 4, 7),
        redirected_to_pool=True,
    )
    companion_row = _row(
        db,
        company_id,
        inquiry,
        item_code=companion.product_code,
        qty="1",
        bundled_qty=Decimal("1"),
        bundled_with_row_id=host_x_row.id,
    )
    db.commit()

    body = client.get(LIST, params={"limit": 100}).json()
    row = next(r for r in body["data"] if r["id"] == companion_row.id)

    assert row["bundled_host_changes"][0]["item_code"] == host_x.product_code
    assert row["bundled_host_changes"][1] == {
        "item_code": host_y.product_code,
        "qty": None,
        "delivery_date": None,
        "previous_qty": None,
        "previous_delivery_date": None,
    }, row["bundled_host_changes"]


def test_a_hosts_delay_row_never_answers_for_its_own_live_order_row_regardless_of_sort(api):
    """BLOCKER (review round 1, 19 Sep 2026). A host carries its own live ORDER row
    (280 @ 2027-03-01, Was 182 @ 2026-06-01) AND a DELAY exception row on the SAME
    item code (7 @ 2028-01-01, no Was) - the DELAY row must never answer for the
    host's own change, and the answer must be IDENTICAL under the default sort and
    under a sort that ties the two rows on their own sort column (`item_code`, same
    on both) and so falls through to the id tie-break - deliberately id-ordered here
    (the DELAY row's own id sorts FIRST) so a page-position bug fails this test on
    every run rather than by an id coin flip.
    """
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    host_x = _product(db, f"ZZT-HOSTX-{_uid()[:6]}", f"{MARKER} host X")
    companion = _product(db, f"ZZT-SC-{_uid()[:6]}", f"{MARKER} seat cover")
    _companion_rule(db, company_id, companion, [host_x])

    # Explicit ids, deliberately in the WRONG order for a page-position bug to read
    # correctly by luck: the DELAY row's id sorts before the ORDER row's under the
    # `id.asc()` tie-break `list_rows` always appends, whatever column is sorted by.
    delay_row = OrderInquiryRow(
        id="00000000-0000-0000-0000-000000000001",
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        item_code=host_x.product_code,
        qty=Decimal("7"),
        delivery_date=date(2028, 1, 1),
        verb=IV_DELAY,
        state=INQUIRY_RAISED,
    )
    host_order_row = OrderInquiryRow(
        id="00000000-0000-0000-0000-000000000002",
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        item_code=host_x.product_code,
        qty=Decimal("280"),
        delivery_date=date(2027, 3, 1),
        previous_qty=Decimal("182"),
        previous_delivery_date=date(2026, 6, 1),
        verb=IV_ORDER,
        state=INQUIRY_RAISED,
    )
    db.add_all([delay_row, host_order_row])
    db.flush()
    companion_row = _row(
        db,
        company_id,
        inquiry,
        item_code=companion.product_code,
        qty="1",
        bundled_qty=Decimal("1"),
        bundled_with_row_id=host_order_row.id,
    )
    db.commit()

    expected = [
        {
            "item_code": host_x.product_code,
            "qty": "280",
            "delivery_date": "2027-03-01",
            "previous_qty": "182",
            "previous_delivery_date": "2026-06-01",
        }
    ]

    default_body = client.get(LIST, params={"limit": 100}).json()
    default_row = next(r for r in default_body["data"] if r["id"] == companion_row.id)
    assert default_row["bundled_host_changes"] == expected, default_row["bundled_host_changes"]

    # Ties the two host rows on the sort column itself (both carry `item_code ==
    # host_x.product_code`), so the page's own order for THIS pair falls straight
    # through to the id tie-break - exactly the shape the live report showed
    # (`sort=item_code&dir=desc`).
    sorted_body = client.get(
        LIST, params={"limit": 100, "sort": "item_code", "dir": "desc"}
    ).json()
    sorted_row = next(r for r in sorted_body["data"] if r["id"] == companion_row.id)
    assert sorted_row["bundled_host_changes"] == expected, sorted_row["bundled_host_changes"]


def test_a_hosts_own_oldest_live_order_row_wins_over_a_newer_one_regardless_of_sort(api):
    """Review round 1 follow up (19 Sep 2026). A host carries TWO live ORDER rows on
    the same item code - the OLDER one (by created_at) is the one whose figures answer
    for the host, never the newer one, and the answer is IDENTICAL under the default
    sort and under sort=item_code and dir=desc.

    The older row is constructed SECOND in code, and both rows carry explicit
    created_at values (created_at defaults to the transaction's own now(), tied across
    every row one test writes) and explicit ids, deliberately ordered so a mutant that
    picks the first candidate encountered in page order (candidates[0]) rather than the
    OLDEST one picks the newer row every run, under both sorts, rather than by an id
    or page order coin flip.
    """
    client, db, company_id, seeded = api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    host_x = _product(db, f"ZZT-HOSTX-{_uid()[:6]}", f"{MARKER} host X")
    companion = _product(db, f"ZZT-SC-{_uid()[:6]}", f"{MARKER} seat cover")
    _companion_rule(db, company_id, companion, [host_x])

    # Constructed FIRST in code, but the NEWER row by created_at - an earlier delivery
    # date puts it FIRST under the default sort, and its own id sorts first under the
    # item_code tie break too, so candidates[0] would pick this one under both sorts.
    newer_row = OrderInquiryRow(
        id="00000000-0000-0000-0000-000000000001",
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        item_code=host_x.product_code,
        qty=Decimal("150"),
        delivery_date=date(2026, 5, 1),
        verb=IV_ORDER,
        state=INQUIRY_RAISED,
        created_at=datetime(2026, 9, 10, 0, 0, 0),
    )
    # Constructed SECOND in code, but the OLDER row by created_at - the one that must
    # win.
    older_row = OrderInquiryRow(
        id="00000000-0000-0000-0000-000000000002",
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        item_code=host_x.product_code,
        qty=Decimal("300"),
        delivery_date=date(2027, 4, 1),
        verb=IV_ORDER,
        state=INQUIRY_RAISED,
        created_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db.add_all([newer_row, older_row])
    db.flush()
    companion_row = _row(
        db,
        company_id,
        inquiry,
        item_code=companion.product_code,
        qty="1",
        bundled_qty=Decimal("1"),
        bundled_with_row_id=older_row.id,
    )
    db.commit()

    expected = [
        {
            "item_code": host_x.product_code,
            "qty": "300",
            "delivery_date": "2027-04-01",
            "previous_qty": None,
            "previous_delivery_date": None,
        }
    ]

    default_body = client.get(LIST, params={"limit": 100}).json()
    default_row = next(r for r in default_body["data"] if r["id"] == companion_row.id)
    assert default_row["bundled_host_changes"] == expected, default_row["bundled_host_changes"]

    sorted_body = client.get(
        LIST, params={"limit": 100, "sort": "item_code", "dir": "desc"}
    ).json()
    sorted_row = next(r for r in sorted_body["data"] if r["id"] == companion_row.id)
    assert sorted_row["bundled_host_changes"] == expected, sorted_row["bundled_host_changes"]
