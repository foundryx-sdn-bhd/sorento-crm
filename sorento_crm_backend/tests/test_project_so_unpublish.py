"""Unpublish: an experimental way back to draft (captain, 19 Aug 2026).

A published or amended project SO can go back to draft while this is experimental. Refused,
409, when something has already ACTED on the published state - an active supply decision, a
published amendment, or an applied planning change - because each of those assumed the order
would stay published. Otherwise the order returns to `draft` (or the two-tier gate's
`blocked`/`ready`, the same recompute a build leaves an order in) with its publish stamp
cleared and its lines untouched.

Route-level, mirroring `test_project_so_document_save.py`'s fixtures: every test seeds its
own chain, nothing borrowed off the shared database, which is a copy of production and empty
in CI.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    AMENDMENT_PROPOSED,
    AMENDMENT_PUBLISHED,
    DECISION_ACTIVE,
    SO_STATUS_AMENDED,
    SO_STATUS_DRAFT,
    SO_STATUS_PUBLISHED,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOAmendment,
    SOSupplyDecision,
)
from app.models.projects import ProjectParty, ProjectPurchaseOrder
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-so-unpub"
BASE = "/api/v1/project-sales"
EDIT = "projects.projects.edit"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, code_hint: str, list_price: str = "100.00") -> Product:
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
        list_price=Decimal(list_price),
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
        user_id = _user(db, f"{MARKER} CONTACT AH")
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
    db,
    project,
    *,
    status: str = SO_STATUS_PUBLISHED,
    lines: list[tuple[Product, str, str, str]] | None = None,
) -> ProjectSalesOrder:
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        area_group="TOWER",
        provisional_ref=f"PSO-{_uid()[:8]}",
        status=status,
        grouping_origin="area",
        total_amount=Decimal("0"),
        published_at=datetime(2026, 8, 1, 9, 0, 0) if status != SO_STATUS_DRAFT else None,
        published_by=None,
    )
    db.add(order)
    db.flush()
    total = Decimal("0")
    for index, (product, qty, unit_price, uom) in enumerate(lines or [], start=1):
        amount = (Decimal(qty) * Decimal(unit_price)).quantize(Decimal("0.01"))
        total += amount
        db.add(
            ProjectSalesOrderLine(
                id=_uid(),
                company_id=project.company_id,
                project_sales_order_id=order.id,
                line_no=index,
                product_id=product.id,
                description=f"{MARKER} line {index}",
                qty=Decimal(qty),
                uom=uom,
                unit_price=Decimal(unit_price),
                amount=amount,
                delivery_date=date(2026, 7, 1),
                explosion_source="quotation",
            )
        )
    order.total_amount = total
    # COMMITTED, not merely flushed: the routes under test roll the session back on a
    # refusal, and `blank_session` maps a commit onto a savepoint inside the outer
    # transaction, so without this "the order is still published" would read a session the
    # rollback had emptied of the fixture too.
    db.commit()
    return order


def _refresh(db, order_id: str) -> ProjectSalesOrder:
    db.expire_all()
    return db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == order_id).first()


# ------------------------------------------------------------------- the happy path


def test_unpublishing_a_published_order_returns_it_to_draft(api):
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "10", "100.00", "UNIT")])

    resp = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in (SO_STATUS_DRAFT, "blocked", "ready")
    assert body["provisional_ref"] == order.provisional_ref
    after = _refresh(db, order.id)
    assert after.status != SO_STATUS_PUBLISHED
    assert after.published_at is None
    assert after.published_by is None
    # Lines are untouched.
    assert (
        db.query(ProjectSalesOrderLine)
        .filter(ProjectSalesOrderLine.project_sales_order_id == order.id)
        .count()
        == 1
    )


def test_unpublishing_an_amended_order_returns_it_to_draft(api):
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, status=SO_STATUS_AMENDED, lines=[(product, "1", "1.00", "UNIT")])

    resp = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")

    assert resp.status_code == 200, resp.text
    after = _refresh(db, order.id)
    assert after.status != SO_STATUS_AMENDED


# ------------------------------------------------------------------- the refusals


def test_a_draft_order_cannot_be_unpublished(api):
    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, status=SO_STATUS_DRAFT, lines=[(product, "1", "1.00", "UNIT")])

    resp = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "so_not_published"


def test_an_active_supply_decision_blocks_unpublish(api):
    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    db.add(
        SOSupplyDecision(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            revision_no=1,
            state=DECISION_ACTIVE,
            line_snapshots={},
        )
    )
    db.commit()

    resp = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "so_unpublish_supply_active"
    after = _refresh(db, order.id)
    assert after.status == SO_STATUS_PUBLISHED


def test_a_published_amendment_blocks_unpublish(api):
    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(
        db, project, status=SO_STATUS_AMENDED, lines=[(product, "1", "1.00", "UNIT")]
    )
    db.add(
        SOAmendment(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            status=AMENDMENT_PUBLISHED,
        )
    )
    db.commit()

    resp = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "so_unpublish_amendment_published"


def test_a_proposed_amendment_does_not_block_unpublish(api):
    """Only a PUBLISHED amendment already acted on the order; a proposal is still just a
    proposal."""
    client, db, company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    db.add(
        SOAmendment(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            status=AMENDMENT_PROPOSED,
        )
    )
    db.commit()

    resp = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")

    assert resp.status_code == 200, resp.text


def test_an_applied_planning_change_row_blocks_unpublish(api):
    from app.models.planning_change import (
        PLANNING_CHANGE_KIND_DELAYED,
        PLANNING_CHANGE_STATE_APPLIED,
        PlanningChangeBatch,
        PlanningChangeRow,
    )

    client, db, company_id, user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])
    batch = PlanningChangeBatch(id=_uid(), company_id=company_id, created_by=user_id)
    db.add(batch)
    db.flush()
    db.add(
        PlanningChangeRow(
            id=_uid(),
            company_id=company_id,
            batch_id=batch.id,
            project_sales_order_id=order.id,
            kind=PLANNING_CHANGE_KIND_DELAYED,
            facts_json={},
            why=f"{MARKER} why",
            applied_state=PLANNING_CHANGE_STATE_APPLIED,
        )
    )
    db.commit()

    resp = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "so_unpublish_planning_change_applied"


# ------------------------------------------------------------------- auth denial


def test_a_reader_cannot_unpublish_a_sales_order(api):
    from app.services.user_service import UserPermissionService

    client, db, _company_id, _user_id, project = api
    product = _product(db, "WC")
    order = _order(db, project, lines=[(product, "1", "1.00", "UNIT")])

    granted = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug != EDIT
    try:
        refused = client.post(f"{BASE}/sales-orders/{order.id}/unpublish")
    finally:
        UserPermissionService.check_user_has_permission = granted

    assert refused.status_code == 403, refused.text
    after = _refresh(db, order.id)
    assert after.status == SO_STATUS_PUBLISHED
