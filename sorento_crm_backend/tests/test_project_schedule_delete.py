"""Deleting a delivery schedule and, separately, one of its versions (P6, follow-up).

*"Need to be able to delete delivery schedule"* - the captain's own words on the project
page's Delivery schedules tab. Both routes hard-delete: a schedule takes every version, its
cells and its stored document with it; a version takes its own cells and document, leaving
the schedule and the rest of its history untouched.

This is still the EXPERIMENTAL phase (captain's call): confirming a version does not, by
itself, block a delete - only a CONFIRMED version that is a live commitment does, built
into a published/amended sales order or named by a published amendment's delta. Everything
else is free to go, so a schedule read wrong is corrected by deleting it and uploading
again rather than living with a bad read forever.

Every test seeds its own chain. Nothing is borrowed off the shared database, which is a
copy of production and empty in CI.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.project_so import (
    AMENDMENT_PUBLISHED,
    AUTHORED_LIVE_STATUSES,
    SO_STATUS_PUBLISHED,
    DeliverySchedule,
    DeliveryScheduleCell,
    DeliveryScheduleVersion,
    ProjectDeliveryPhase,
    ProjectSalesOrder,
    SOAmendment,
)
from app.models.projects import ProjectParty, ProjectPurchaseOrder
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException
from app.services.project_schedule_service import ProjectScheduleService

from ._pg_fixture import blank_session

MARKER = "zzt-schedule-delete"
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


def _client(db, user_id: str):
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
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    UserPermissionService.get_user_permission_slugs = lambda self, uid: [
        "projects.projects.view",
        "projects.projects.edit",
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
        owner = _user(db, f"{MARKER} CONTACT AH")
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=owner,
            developer_party_id=None,
            title=f"{MARKER} Tuju Residences",
        )
        db.commit()
        client, originals = _client(db, owner)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, owner, project
        finally:
            _restore(originals)


def _po(db, project) -> ProjectPurchaseOrder:
    party = ProjectParty(
        id=_uid(),
        company_id=project.company_id,
        party_type="trading_house",
        name=f"{MARKER} Buimaco {_uid()[:6]}",
    )
    db.add(party)
    db.flush()
    row = ProjectPurchaseOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        po_source="trading_house",
        issuing_party_id=party.id,
        po_number=f"HQ/26/01/{_uid()[:4]}",
        po_date=date(2026, 1, 19),
        term_days=60,
        status="approved",
    )
    db.add(row)
    db.commit()
    return row


def _schedule(db, project, po) -> DeliverySchedule:
    row = DeliverySchedule(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        purchase_order_id=po.id,
        label=f"{MARKER} programme",
    )
    db.add(row)
    db.commit()
    return row


def _version(
    db,
    schedule,
    *,
    version_no: int,
    confirmed: bool = False,
    cells: int = 0,
) -> DeliveryScheduleVersion:
    row = DeliveryScheduleVersion(
        id=_uid(),
        company_id=schedule.company_id,
        delivery_schedule_id=schedule.id,
        version_no=version_no,
        revision_label=f"{MARKER} v{version_no}",
        extraction_state="done",
        confirmed_at=datetime(2026, 7, 1) if confirmed else None,
        reconciled_columns=1,
        total_columns=1,
    )
    db.add(row)
    db.flush()
    if cells:
        phase = ProjectDeliveryPhase(
            id=_uid(),
            company_id=schedule.company_id,
            project_id=schedule.project_id,
            area_group="TOWER",
            sequence=version_no,
            label=f"{MARKER} phase",
        )
        db.add(phase)
        db.flush()
        for index in range(cells):
            db.add(
                DeliveryScheduleCell(
                    id=_uid(),
                    company_id=schedule.company_id,
                    version_id=row.id,
                    phase_id=phase.id,
                    customer_code_raw=f"{MARKER}-{index}",
                    qty=Decimal("10"),
                )
            )
    db.commit()
    return row


def _order(db, project, *, po=None, schedule_version=None, status: str) -> ProjectSalesOrder:
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        purchase_order_id=po.id if po else None,
        schedule_version_id=schedule_version.id if schedule_version else None,
        provisional_ref=f"PSO-{_uid()[:8]}",
        status=status,
        total_amount=Decimal("0"),
    )
    db.add(order)
    db.commit()
    return order


# --------------------------------------------------------------- deleting a schedule


def test_deleting_a_schedule_removes_every_version_and_its_cells(api):
    client, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    first = _version(db, schedule, version_no=1, cells=2)
    second = _version(db, schedule, version_no=2, cells=1)
    schedule_id, first_id, second_id, po_id = schedule.id, first.id, second.id, po.id

    deleted = client.delete(f"{BASE}/delivery-schedules/{schedule_id}")

    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["success"] is True
    assert body["deleted"]["projects.delivery_schedules"] == 1
    assert body["deleted"]["projects.delivery_schedule_versions"] == 2
    assert body["deleted"]["projects.delivery_schedule_cells"] == 3

    db.expire_all()
    assert db.query(DeliverySchedule).filter(DeliverySchedule.id == schedule_id).first() is None
    assert (
        db.query(DeliveryScheduleVersion)
        .filter(DeliveryScheduleVersion.id.in_([first_id, second_id]))
        .count()
        == 0
    )
    assert (
        db.query(DeliveryScheduleCell)
        .filter(DeliveryScheduleCell.version_id.in_([first_id, second_id]))
        .count()
        == 0
    )
    # What made the delete possible: the PO the schedule was checked against.
    assert db.query(ProjectPurchaseOrder).filter(ProjectPurchaseOrder.id == po_id).first()


def test_a_schedule_with_a_version_built_into_a_published_order_cannot_be_deleted(api):
    client, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    version = _version(db, schedule, version_no=1, confirmed=True)
    order = _order(db, project, po=po, schedule_version=version, status=SO_STATUS_PUBLISHED)
    assert order.status in AUTHORED_LIVE_STATUSES

    refused = client.delete(f"{BASE}/delivery-schedules/{schedule.id}")

    assert refused.status_code == 409, refused.text
    assert order.provisional_ref in refused.text
    db.expire_all()
    assert db.query(DeliverySchedule).filter(DeliverySchedule.id == schedule.id).first()
    assert db.query(DeliveryScheduleVersion).filter(DeliveryScheduleVersion.id == version.id).first()


def test_a_version_named_by_a_published_amendment_cannot_be_deleted(api):
    client, db, company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    version = _version(db, schedule, version_no=1, confirmed=True)
    # A sibling version, so this exercises the LIVE-COMMITMENT refusal rather than the
    # separate "this is the only version" gate.
    _version(db, schedule, version_no=2)
    order = _order(db, project, po=po, status="draft")
    db.add(
        SOAmendment(
            id=_uid(),
            company_id=company_id,
            project_sales_order_id=order.id,
            status=AMENDMENT_PUBLISHED,
            from_version_kind="schedule",
            from_version_id=version.id,
            to_version_id=version.id,
        )
    )
    db.commit()

    refused = client.delete(f"{BASE}/delivery-schedule-versions/{version.id}")

    assert refused.status_code == 409, refused.text
    assert order.provisional_ref in refused.text


def test_a_confirmed_version_nobody_built_on_may_still_be_deleted(api):
    """Confirming does not itself block a delete - only a LIVE commitment does. This is
    still the experimental phase (captain's call)."""
    client, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    _version(db, schedule, version_no=1)
    confirmed = _version(db, schedule, version_no=2, confirmed=True, cells=1)
    schedule_id, confirmed_id = schedule.id, confirmed.id

    removed = client.delete(f"{BASE}/delivery-schedule-versions/{confirmed_id}")

    assert removed.status_code == 200, removed.text
    db.expire_all()
    assert (
        db.query(DeliveryScheduleVersion).filter(DeliveryScheduleVersion.id == confirmed_id).first()
        is None
    )
    # The schedule and its remaining version are untouched.
    assert db.query(DeliverySchedule).filter(DeliverySchedule.id == schedule_id).first()


def test_deleting_an_unknown_schedule_is_a_404(api):
    client, _db, _company_id, _owner, _project = api

    missing = client.delete(f"{BASE}/delivery-schedules/{_uid()}")

    assert missing.status_code == 404, missing.text


# ---------------------------------------------------------------- deleting one version


def test_deleting_one_version_leaves_the_schedule_and_its_sibling(api):
    client, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    older = _version(db, schedule, version_no=1, cells=2)
    newer = _version(db, schedule, version_no=2, cells=1)
    schedule_id, older_id, newer_id = schedule.id, older.id, newer.id

    removed = client.delete(f"{BASE}/delivery-schedule-versions/{older_id}")

    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["deleted"]["projects.delivery_schedule_versions"] == 1
    assert body["deleted"]["projects.delivery_schedule_cells"] == 2

    db.expire_all()
    assert db.query(DeliveryScheduleVersion).filter(DeliveryScheduleVersion.id == older_id).first() is None
    assert db.query(DeliveryScheduleCell).filter(DeliveryScheduleCell.version_id == older_id).count() == 0
    # The schedule, and the sibling version's own cells, are untouched.
    assert db.query(DeliverySchedule).filter(DeliverySchedule.id == schedule_id).first()
    survivor = db.query(DeliveryScheduleVersion).filter(DeliveryScheduleVersion.id == newer_id).first()
    assert survivor is not None
    assert db.query(DeliveryScheduleCell).filter(DeliveryScheduleCell.version_id == newer_id).count() == 1


def test_the_last_version_of_a_schedule_refuses_delete(api):
    client, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    only = _version(db, schedule, version_no=1)

    refused = client.delete(f"{BASE}/delivery-schedule-versions/{only.id}")

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "schedule_version_last"
    db.expire_all()
    assert db.query(DeliveryScheduleVersion).filter(DeliveryScheduleVersion.id == only.id).first()


def test_deleting_an_unknown_version_is_a_404(api):
    client, _db, _company_id, _owner, _project = api

    missing = client.delete(f"{BASE}/delivery-schedule-versions/{_uid()}")

    assert missing.status_code == 404, missing.text


# ------------------------------------------------------------------------ auth denial


def test_a_reader_without_the_edit_right_cannot_delete_a_schedule(api):
    """Rights live on the PROJECT: another salesperson's pursuit is not editable."""
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    client, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    _version(db, schedule, version_no=1)

    stranger = _user(db, f"{MARKER} Farah")
    db.commit()
    actor = {"id": stranger, "email": f"{stranger}@zzt.test", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)

    refused = client.delete(f"{BASE}/delivery-schedules/{schedule.id}")

    assert refused.status_code == 403, refused.text
    db.expire_all()
    assert db.query(DeliverySchedule).filter(DeliverySchedule.id == schedule.id).first()


def test_a_reader_without_the_edit_right_cannot_delete_a_version(api):
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    client, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    first = _version(db, schedule, version_no=1)
    _version(db, schedule, version_no=2)

    stranger = _user(db, f"{MARKER} Hafiz")
    db.commit()
    actor = {"id": stranger, "email": f"{stranger}@zzt.test", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)

    refused = client.delete(f"{BASE}/delivery-schedule-versions/{first.id}")

    assert refused.status_code == 403, refused.text
    db.expire_all()
    assert db.query(DeliveryScheduleVersion).filter(DeliveryScheduleVersion.id == first.id).first()


# ------------------------------------------------------------------------- unit-level


def test_delete_blockers_are_empty_for_an_unconfirmed_version(api):
    """The service rule directly: an unconfirmed version is never blocked, whatever
    points at it."""
    _client_unused, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    version = _version(db, schedule, version_no=1)
    _order(db, project, po=po, schedule_version=version, status=SO_STATUS_PUBLISHED)

    service = ProjectScheduleService(db)
    assert service._delete_blockers(version) == []


def test_service_delete_schedule_raises_app_exception_when_linked(api):
    _client_unused, db, _company_id, _owner, project = api
    po = _po(db, project)
    schedule = _schedule(db, project, po)
    version = _version(db, schedule, version_no=1, confirmed=True)
    _order(db, project, po=po, schedule_version=version, status=SO_STATUS_PUBLISHED)

    service = ProjectScheduleService(db)
    with pytest.raises(AppException) as excinfo:
        service.delete_schedule(schedule.id)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "schedule_has_live_commitments"
