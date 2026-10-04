"""Drag-and-reorder the draft lines table: `PUT /sales-orders/{pso_id}/lines/order`.

A hand drag is persisted verbatim - the given `line_ids`, in that order, become `line_no`
1..N - because the read view groups lines by their PO line and set, which a drag cannot
honour (a dropped row would visually snap back to its own cluster), so dragging is only
offered against the flat view and this route trusts it completely.

Route-level, mirroring `test_project_so_unpublish.py`'s fixtures: every test seeds its own
chain, nothing borrowed off the shared database, which is a copy of production and empty in
CI.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    SO_STATUS_DRAFT,
    SO_STATUS_PUBLISHED,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-so-reorder"
BASE = "/api/v1/project-sales"
EDIT = "projects.projects.edit"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _product(db, code_hint: str) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{code_hint}-{_uid()[:6]}",
        product_name=f"{MARKER} {code_hint}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.commit()
    return row


def _client(db, user_id: str):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "superadmin"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    UserPermissionService.get_user_permission_slugs = lambda self, uid: [
        "projects.projects.view",
        "projects.projects.create",
        "projects.projects.edit",
        "projects.projects.delete",
        "projects.projects.manage",
    ]
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@pytest.fixture()
def api():
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _uid()
        db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=f"{MARKER} CONTACT AH"))
        db.flush()
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=user_id,
            developer_party_id=None,
            title=f"{MARKER} Tuju Residences",
        )
        db.commit()
        client, originals = _client(db, user_id)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, user_id, project
        finally:
            _restore(originals)


def _order(
    db, project, *, status: str = SO_STATUS_DRAFT, line_count: int = 3
) -> tuple[ProjectSalesOrder, list[str]]:
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        area_group="TOWER",
        provisional_ref=f"PSO-{_uid()[:8]}",
        status=status,
        grouping_origin="area",
        total_amount=Decimal("0"),
    )
    db.add(order)
    db.flush()
    line_ids: list[str] = []
    for index in range(1, line_count + 1):
        product = _product(db, f"P{index}")
        line = ProjectSalesOrderLine(
            id=_uid(),
            company_id=project.company_id,
            project_sales_order_id=order.id,
            line_no=index,
            product_id=product.id,
            description=f"{MARKER} line {index}",
            qty=Decimal("1"),
            uom="UNIT",
            unit_price=Decimal("100.00"),
            amount=Decimal("100.00"),
            delivery_date=date(2026, 7, 1),
            explosion_source="none",
        )
        db.add(line)
        line_ids.append(line.id)
    db.commit()
    return order, line_ids


def _line_order(db, pso_id: str) -> list[str]:
    db.expire_all()
    rows = (
        db.query(ProjectSalesOrderLine)
        .filter(ProjectSalesOrderLine.project_sales_order_id == pso_id)
        .order_by(ProjectSalesOrderLine.line_no.asc())
        .all()
    )
    return [row.id for row in rows]


# ------------------------------------------------------------------- the happy path


def test_a_drag_persists_the_given_order_verbatim(api):
    client, db, _company_id, _user_id, project = api
    order, line_ids = _order(db, project, line_count=3)
    dragged = [line_ids[2], line_ids[0], line_ids[1]]

    resp = client.put(f"{BASE}/sales-orders/{order.id}/lines/order", json={"line_ids": dragged})

    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] == 3
    assert _line_order(db, order.id) == dragged


def test_a_reorder_that_matches_the_current_order_changes_nothing(api):
    client, db, _company_id, _user_id, project = api
    order, line_ids = _order(db, project, line_count=2)

    resp = client.put(f"{BASE}/sales-orders/{order.id}/lines/order", json={"line_ids": line_ids})

    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] == 0


# ------------------------------------------------------------------- the refusals


def test_a_line_set_mismatch_is_refused(api):
    client, db, _company_id, _user_id, project = api
    order, line_ids = _order(db, project, line_count=2)
    missing_one = [line_ids[0]]

    resp = client.put(
        f"{BASE}/sales-orders/{order.id}/lines/order", json={"line_ids": missing_one}
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "so_lines_mismatch"
    # Untouched.
    assert _line_order(db, order.id) == line_ids


def test_a_foreign_line_id_is_refused(api):
    client, db, _company_id, _user_id, project = api
    order, line_ids = _order(db, project, line_count=2)
    forged = [line_ids[0], _uid()]

    resp = client.put(f"{BASE}/sales-orders/{order.id}/lines/order", json={"line_ids": forged})

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "so_lines_mismatch"


def test_a_published_order_refuses_reordering(api):
    client, db, _company_id, _user_id, project = api
    order, line_ids = _order(db, project, status=SO_STATUS_PUBLISHED, line_count=2)

    resp = client.put(
        f"{BASE}/sales-orders/{order.id}/lines/order",
        json={"line_ids": list(reversed(line_ids))},
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "so_not_draft"
    assert _line_order(db, order.id) == line_ids


# ------------------------------------------------------------------- auth denial


def test_a_reader_cannot_reorder_lines(api):
    from app.services.user_service import UserPermissionService

    client, db, _company_id, _user_id, project = api
    order, line_ids = _order(db, project, line_count=2)

    granted = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug != EDIT
    try:
        resp = client.put(
            f"{BASE}/sales-orders/{order.id}/lines/order",
            json={"line_ids": list(reversed(line_ids))},
        )
    finally:
        UserPermissionService.check_user_has_permission = granted

    assert resp.status_code == 403, resp.text
    assert _line_order(db, order.id) == line_ids
