"""Batch-level findings: `GET/POST .../purchase-orders/{po_id}/schedule-findings...`.

A finding that names no PO line belongs to no drafted order (see `SODraftFinding`'s own
docstring), so it reads and clears at the (PO, schedule version) pair rather than through
any one sales order's routes.

Route-level, mirroring `test_project_so_reorder_lines.py`'s fixtures: every test seeds its
own chain, nothing borrowed off the shared database, which is a copy of production and empty
in CI.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    DeliveryScheduleCell,
    DeliveryScheduleVersion,
    ProjectDeliveryPhase,
    ProjectPOLine,
    ProjectPOVersion,
)
from app.models.projects import ProjectPurchaseOrder
from app.models.user import User
from app.services import project_seed_service

from ._pg_fixture import blank_session

MARKER = "zzt-so-schedfind"
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


def _po_and_schedule_with_batch_finding(db, project):
    """A PO with one line, and a schedule column for a DIFFERENT product entirely -- the
    shape `_spread_across_phases` reports as `schedule_over`, batch-level."""
    from app.services.project_so_draft_service import ProjectSODraftService

    ordered_product = _product(db, "ORDERED")
    stray_product = _product(db, "STRAY")
    po = ProjectPurchaseOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        po_source="trading_house",
        po_number=f"{MARKER}/{_uid()[:6]}",
        po_date=date(2026, 1, 1),
        status="approved",
    )
    db.add(po)
    db.flush()
    po_version = ProjectPOVersion(
        id=_uid(),
        company_id=project.company_id,
        purchase_order_id=po.id,
        version_no=1,
        extraction_state="done",
        confirmed_at=date.today(),
    )
    db.add(po_version)
    db.flush()
    db.add(
        ProjectPOLine(
            id=_uid(),
            company_id=project.company_id,
            po_version_id=po_version.id,
            line_no=1,
            stock_code_raw="ORDERED",
            description_raw=f"{MARKER} ordered",
            qty=Decimal("10"),
            uom_raw="UNIT",
            unit_price=Decimal("100.00"),
            amount=Decimal("1000.00"),
            is_cancelled=False,
            resolved_product_id=ordered_product.id,
            resolution_source="description",
        )
    )
    db.flush()
    from app.models.project_so import DeliverySchedule

    schedule = DeliverySchedule(
        id=_uid(), company_id=project.company_id, project_id=project.id, purchase_order_id=po.id,
        label=f"{MARKER} programme",
    )
    db.add(schedule)
    db.flush()
    version = DeliveryScheduleVersion(
        id=_uid(),
        company_id=project.company_id,
        delivery_schedule_id=schedule.id,
        version_no=1,
        po_version_id=po_version.id,
        extraction_state="done",
        schedule_date=date(2026, 1, 19),
    )
    db.add(version)
    db.flush()
    phase = ProjectDeliveryPhase(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        area_group="TOWER",
        sequence=1,
        label="Level 2",
        delivery_date=date(2026, 7, 1),
        source_version_id=version.id,
    )
    db.add(phase)
    db.flush()
    db.add_all(
        [
            DeliveryScheduleCell(
                id=_uid(), company_id=project.company_id, version_id=version.id,
                phase_id=phase.id, product_id=ordered_product.id,
                customer_code_raw="ORDERED", qty=Decimal("10"),
            ),
            DeliveryScheduleCell(
                id=_uid(), company_id=project.company_id, version_id=version.id,
                phase_id=phase.id, product_id=stray_product.id,
                customer_code_raw="STRAY", qty=Decimal("890"),
            ),
        ]
    )
    db.commit()

    ProjectSODraftService(db).build(po.id, version.id)
    db.commit()
    return po, version


# ------------------------------------------------------------------- the happy path


def test_listing_shows_the_batch_level_finding_with_no_trailing_zeros(api):
    client, db, _company_id, _user_id, project = api
    po, version = _po_and_schedule_with_batch_finding(db, project)

    resp = client.get(
        f"{BASE}/purchase-orders/{po.id}/schedule-findings",
        params={"schedule_version_id": version.id},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["code"] == "schedule_over"
    assert body[0]["line_id"] is None
    assert "890 of" in body[0]["detail"]
    assert "890.0000" not in body[0]["detail"]


def test_acknowledging_a_batch_level_finding_records_the_reason(api):
    client, db, _company_id, _user_id, project = api
    po, version = _po_and_schedule_with_batch_finding(db, project)
    listed = client.get(
        f"{BASE}/purchase-orders/{po.id}/schedule-findings",
        params={"schedule_version_id": version.id},
    ).json()
    finding_id = listed[0]["id"]

    resp = client.post(
        f"{BASE}/purchase-orders/{po.id}/schedule-findings/{finding_id}/acknowledge",
        json={"reason": "Confirmed the extra column is a duplicate, dropping it next revision."},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["acknowledged_reason"].startswith("Confirmed")
    assert body["acknowledged_at"] is not None


# ------------------------------------------------------------------- the refusals


def test_a_reader_cannot_acknowledge_a_batch_level_finding(api):
    from app.services.user_service import UserPermissionService

    client, db, _company_id, _user_id, project = api
    po, version = _po_and_schedule_with_batch_finding(db, project)
    listed = client.get(
        f"{BASE}/purchase-orders/{po.id}/schedule-findings",
        params={"schedule_version_id": version.id},
    ).json()
    finding_id = listed[0]["id"]

    granted = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug != EDIT
    try:
        resp = client.post(
            f"{BASE}/purchase-orders/{po.id}/schedule-findings/{finding_id}/acknowledge",
            json={"reason": "Should not go through."},
        )
    finally:
        UserPermissionService.check_user_has_permission = granted

    assert resp.status_code == 403, resp.text
