"""P8a ROUTES: the two ways in, the management list, and the resolve (AC-N3..N7).

Route-level because the wiring is its own seam. `/sales-orders/ingest` and
`/sales-orders/{pso_id}` are the same shape to the router, so the divergence router has
to mount FIRST or an upload is read as a sales order id and 422s on a path parameter that
was never a uuid - a failure that looks like a bad request rather than a bad mount.

The grants are the point of the auth cases: reading a reconciliation is the project's view
grant, answering one is its edit grant, and a reader must be refused the resolve.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.order import Customer
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    RESOLUTION_ACCEPT_THEIRS,
    SO_STATUS_PUBLISHED,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.projects import ProjectParty, ProjectPurchaseOrder
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-div-route"
BASE = "/api/v1/project-sales"

READ_ONLY = ["projects.projects.view"]
EDITOR = ["projects.projects.view", "projects.projects.edit", "projects.projects.manage"]

MAR = date(2026, 3, 10)


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


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


def _seed_order(db, company_id: str, user_id: str):
    from app.services.project_service import register_project

    project = register_project(
        db,
        company_id=company_id,
        actor_user_id=user_id,
        developer_party_id=None,
        title=f"{MARKER} Tuju Residences {_uid()[:12]}",
    )
    customer = Customer(
        id=_uid(), customer_code=f"ZZTC{_uid()[:6]}", customer_name=f"{MARKER} SLG"
    )
    db.add(customer)
    db.flush()
    party = ProjectParty(
        id=_uid(),
        company_id=company_id,
        party_type="contractor",
        name=f"{MARKER} SLG",
        customer_id=customer.id,
    )
    db.add(party)
    db.flush()
    po = ProjectPurchaseOrder(
        id=_uid(),
        company_id=company_id,
        project_id=project.id,
        issuing_party_id=party.id,
        po_number=f"PO-778-{_uid()[:6]}",
        po_date=date(2026, 2, 1),
        term_days=60,
        status="approved",
    )
    db.add(po)
    db.flush()
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        project_id=project.id,
        purchase_order_id=po.id,
        area_group="TOWER",
        provisional_ref=f"ZZT-PSO-{_uid()[:8]}",
        status=SO_STATUS_PUBLISHED,
        published_at=datetime.utcnow(),
        grouping_origin="area",
        total_amount=Decimal("7500.00"),
    )
    db.add(order)
    db.flush()
    product = _product(db, f"CB{_uid()[:6]}")
    db.add(
        ProjectSalesOrderLine(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            line_no=1,
            product_id=product.id,
            description=f"{MARKER} grating",
            qty=Decimal("600"),
            uom="UNIT",
            unit_price=Decimal("12.50"),
            amount=Decimal("7500.00"),
            delivery_date=MAR,
        )
    )
    db.flush()
    return project, po, customer, order, product


def _payload(po, customer, product, *, qty="550", total="6875.00"):
    return {
        "doc_no": "SO397450",
        "customer_code": customer.customer_code,
        "customer_po_no": po.po_number,
        "area_group": "TOWER",
        "terms": "*Net 60 days",
        "total_amount": total,
        "lines": [
            {
                "line_no": 1,
                "product_code": product.product_code,
                "description": "grating",
                "qty": qty,
                "unit_price": "12.50",
                "uom": "UNIT",
                "delivery_date": MAR.isoformat(),
            }
        ],
    }


@pytest.fixture()
def seeded():
    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _user(db, f"{MARKER} CONTACT AH")
        yield db, company_id, user_id


# --------------------------------------------------------------------------- #
# Ingest                                                                       #
# --------------------------------------------------------------------------- #


def test_the_canonical_ingest_route_is_not_captured_as_a_sales_order_id(seeded):
    """`/sales-orders/ingest` mounts before `/sales-orders/{pso_id}`, or this 422s on a
    path parameter that was never meant to be a uuid."""
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        response = client.post(
            f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["outcome"] == "divergent"
        assert body["project_sales_order_id"] == order.id
        assert body["divergence_id"]
    finally:
        _restore(originals)


def test_an_unmatched_document_answers_rather_than_404s(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        payload = _payload(po, customer, product)
        payload["customer_po_no"] = "PO-NOT-OURS"

        response = client.post(f"{BASE}/sales-orders/ingest", json=payload)

        assert response.status_code == 200
        assert response.json()["outcome"] == "unmatched"
        assert response.json()["message"]
    finally:
        _restore(originals)


def test_a_reader_may_not_ingest(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, READ_ONLY)
    try:
        response = client.post(
            f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
        )

        assert response.status_code == 403
    finally:
        _restore(originals)


def test_an_uploaded_export_is_parsed_and_ingested(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    body = (
        f"Doc No.,SO397450\n"
        f"Debtor,{customer.customer_code}\n"
        f"Your Ref No.,{po.po_number}\n"
        f"Terms,*Net 60 days\n\n"
        f"***TOWER***\n"
        f"Item,Description,Qty,Delivery Date,UOM,U/Price\n"
        f"{product.product_code},grating,550,2026-03-10,UNIT,12.50\n\n"
        f"Total,,,,,6875.00\n"
    )
    client, originals = _client(db, user_id, EDITOR)
    try:
        response = client.post(
            f"{BASE}/sales-orders/ingest-file",
            files={"file": ("SO397450.csv", body.encode("utf-8"), "text/csv")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "divergent"
        assert response.json()["project_sales_order_id"] == order.id
    finally:
        _restore(originals)


def test_a_pdf_upload_is_refused_with_a_reason(seeded):
    db, company_id, user_id = seeded
    _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        response = client.post(
            f"{BASE}/sales-orders/ingest-file",
            files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
        )

        assert response.status_code == 422
        assert "csv" in response.json()["message"].lower()
    finally:
        _restore(originals)


# --------------------------------------------------------------------------- #
# The list and the detail                                                      #
# --------------------------------------------------------------------------- #


def test_the_management_list_carries_the_age(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        client.post(f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product))

        response = client.get(f"{BASE}/divergences", params={"status": "open"})

        assert response.status_code == 200, response.text
        rows = response.json()["data"]
        mine = [row for row in rows if row["project_sales_order_id"] == order.id]
        assert len(mine) == 1
        assert mine[0]["age_days"] == 0
        assert mine[0]["differing_count"] >= 1
    finally:
        _restore(originals)


def test_the_detail_returns_rows_that_agree_as_well(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        created = client.post(
            f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
        ).json()

        response = client.get(f"{BASE}/divergences/{created['divergence_id']}")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["compared_count"] == len(body["rows"])
        assert any(row["needs_answer"] for row in body["rows"])
        assert any(row["scope"] == "header" for row in body["rows"])
    finally:
        _restore(originals)


def test_a_reader_may_read_a_reconciliation(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    created = client.post(
        f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
    ).json()
    _restore(originals)

    client, originals = _client(db, user_id, READ_ONLY)
    try:
        response = client.get(f"{BASE}/divergences/{created['divergence_id']}")
        assert response.status_code == 200
    finally:
        _restore(originals)


# --------------------------------------------------------------------------- #
# Resolution                                                                   #
# --------------------------------------------------------------------------- #


def test_resolving_a_row_returns_the_refreshed_reconciliation(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        created = client.post(
            f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
        ).json()
        detail = client.get(f"{BASE}/divergences/{created['divergence_id']}").json()
        row = next(r for r in detail["rows"] if r["needs_answer"] and r["scope"] == "line")

        response = client.post(
            f"{BASE}/divergences/{created['divergence_id']}/rows/{row['id']}/resolve",
            json={"resolution": RESOLUTION_ACCEPT_THEIRS, "reason": f"{MARKER} theirs"},
        )

        assert response.status_code == 200, response.text
        answered = next(r for r in response.json()["rows"] if r["id"] == row["id"])
        assert answered["resolution"] == RESOLUTION_ACCEPT_THEIRS
        assert answered["resolved_by"] == user_id
    finally:
        _restore(originals)


def test_a_resolution_without_a_reason_is_a_validation_error(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        created = client.post(
            f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
        ).json()
        detail = client.get(f"{BASE}/divergences/{created['divergence_id']}").json()
        row = next(r for r in detail["rows"] if r["needs_answer"])

        response = client.post(
            f"{BASE}/divergences/{created['divergence_id']}/rows/{row['id']}/resolve",
            json={"resolution": RESOLUTION_ACCEPT_THEIRS, "reason": ""},
        )

        assert response.status_code == 422
    finally:
        _restore(originals)


def test_a_reader_may_not_resolve(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    created = client.post(
        f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
    ).json()
    detail = client.get(f"{BASE}/divergences/{created['divergence_id']}").json()
    row = next(r for r in detail["rows"] if r["needs_answer"])
    _restore(originals)

    client, originals = _client(db, user_id, READ_ONLY)
    try:
        response = client.post(
            f"{BASE}/divergences/{created['divergence_id']}/rows/{row['id']}/resolve",
            json={"resolution": RESOLUTION_ACCEPT_THEIRS, "reason": f"{MARKER} theirs"},
        )

        assert response.status_code == 403
    finally:
        _restore(originals)


def test_the_corrective_file_downloads_once_something_was_kept(seeded):
    db, company_id, user_id = seeded
    _, po, customer, order, product = _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        created = client.post(
            f"{BASE}/sales-orders/ingest", json=_payload(po, customer, product)
        ).json()
        detail = client.get(f"{BASE}/divergences/{created['divergence_id']}").json()
        row = next(r for r in detail["rows"] if r["needs_answer"] and r["scope"] == "line")
        client.post(
            f"{BASE}/divergences/{created['divergence_id']}/rows/{row['id']}/resolve",
            json={"resolution": "keep_ours", "reason": f"{MARKER} the PO says 600"},
        )

        response = client.get(
            f"{BASE}/divergences/{created['divergence_id']}/corrective-import-file"
        )

        assert response.status_code == 200, response.text
        assert "attachment" in response.headers["content-disposition"]
        assert "600" in response.text
    finally:
        _restore(originals)


def test_an_unknown_reconciliation_is_a_404(seeded):
    db, company_id, user_id = seeded
    _seed_order(db, company_id, user_id)
    client, originals = _client(db, user_id, EDITOR)
    try:
        response = client.get(f"{BASE}/divergences/{_uid()}")
        assert response.status_code == 404
    finally:
        _restore(originals)
