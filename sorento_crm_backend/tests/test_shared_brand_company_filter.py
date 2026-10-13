"""AC-E1 (PLAN-shared-brand-attachments.md S4, UAC group E):
`GET /attachments?company=shared|<id>` and `GET /attachments/drive?company=...`
narrow to exactly the requested company, folders included on the drive side.
Omitting the param keeps today's default (`company_id IS NULL OR company_id
IN (scope)`), unchanged.

Exercised at the service layer (`AttachmentService.list_attachments` /
`list_drive_contents`), which is what the route params are threaded into -
the route itself is a thin pass-through, already covered elsewhere for the
rest of its filters. Postgres only, own seeded `ZZT-` chain.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.company import Company
from app.models.resources import Attachment, AttachmentDirectory, AttachmentType
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope
from app.services.resources_service import AttachmentService

from tests._pg_fixture import blank_session, unique_code

MOCHA_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        session.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("MCH")[:20]))
        session.flush()
        # Both companies granted, S active - the common "granted both" reader.
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
        yield session


def _att_type(db) -> str:
    row = AttachmentType(
        id=str(uuid.uuid4()), type_name=unique_code("ZZT-Type")[:50],
        allowed_extensions="pdf",
    )
    db.add(row)
    db.flush()
    return row.id


def _attachment(db, *, company_id, type_id: str, directory_id=None) -> Attachment:
    row = Attachment(
        id=str(uuid.uuid4()),
        original_filename=f"{unique_code('ZZT-file')}.pdf",
        stored_filename=f"{unique_code('ZZT-file')}.pdf",
        file_path="https://cdn.test/zzt.pdf",
        attachment_type_id=type_id,
        is_deleted=False,
        company_id=company_id,
        directory_id=directory_id,
    )
    db.add(row)
    db.flush()
    return row


def _folder(db, *, company_id, name: str | None = None) -> AttachmentDirectory:
    row = AttachmentDirectory(
        id=str(uuid.uuid4()),
        name=name or unique_code("ZZT-Folder"),
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


# --- list_attachments --------------------------------------------------------


def test_company_shared_narrows_to_null_only(db):
    type_id = _att_type(db)
    sorento = _attachment(db, company_id=DEFAULT_COMPANY_ID, type_id=type_id)
    mocha = _attachment(db, company_id=MOCHA_ID, type_id=type_id)
    shared = _attachment(db, company_id=None, type_id=type_id)
    db.commit()

    service = AttachmentService(db)
    result = service.list_attachments(
        company="shared",
        attachment_ids=[sorento.id, mocha.id, shared.id],
    )

    ids = {row.id for row in result["data"]}
    assert ids == {shared.id}


def test_company_id_narrows_to_that_company_only_excludes_shared(db):
    type_id = _att_type(db)
    sorento = _attachment(db, company_id=DEFAULT_COMPANY_ID, type_id=type_id)
    mocha = _attachment(db, company_id=MOCHA_ID, type_id=type_id)
    shared = _attachment(db, company_id=None, type_id=type_id)
    db.commit()

    service = AttachmentService(db)
    result = service.list_attachments(
        company=DEFAULT_COMPANY_ID,
        attachment_ids=[sorento.id, mocha.id, shared.id],
    )

    ids = {row.id for row in result["data"]}
    assert ids == {sorento.id}, "a company filter must exclude shared (NULL) rows"


def test_no_company_param_keeps_default_shared_plus_scope(db):
    type_id = _att_type(db)
    sorento = _attachment(db, company_id=DEFAULT_COMPANY_ID, type_id=type_id)
    mocha = _attachment(db, company_id=MOCHA_ID, type_id=type_id)
    shared = _attachment(db, company_id=None, type_id=type_id)
    db.commit()

    service = AttachmentService(db)
    result = service.list_attachments(attachment_ids=[sorento.id, mocha.id, shared.id])

    ids = {row.id for row in result["data"]}
    assert ids == {sorento.id, mocha.id, shared.id}


# --- list_drive_contents (folders included) -----------------------------------


def test_drive_company_shared_narrows_folders_and_files(db):
    root_shared = _folder(db, company_id=None)
    root_owned = _folder(db, company_id=DEFAULT_COMPANY_ID)
    type_id = _att_type(db)
    _attachment(db, company_id=None, type_id=type_id, directory_id=root_shared.id)
    _attachment(db, company_id=DEFAULT_COMPANY_ID, type_id=type_id, directory_id=root_owned.id)
    db.commit()

    service = AttachmentService(db)
    result = service.list_drive_contents(company="shared", recursive=True)

    folder_ids = {row["id"] for row in result["items"] if row["kind"] == "folder"}
    assert root_shared.id in folder_ids
    assert root_owned.id not in folder_ids
    # Every file row present must belong to the shared folder's attachment.
    file_ids = {
        str(row["attachment"].id) for row in result["items"] if row["kind"] == "file"
    }
    shared_file_ids = {
        str(a.id)
        for a in db.query(Attachment).filter(Attachment.directory_id == root_shared.id)
    }
    assert file_ids == shared_file_ids


def test_drive_company_id_narrows_to_that_company_only(db):
    root_sorento = _folder(db, company_id=DEFAULT_COMPANY_ID)
    root_mocha = _folder(db, company_id=MOCHA_ID)
    root_shared = _folder(db, company_id=None)
    db.commit()

    service = AttachmentService(db)
    result = service.list_drive_contents(company=MOCHA_ID, recursive=True)

    folder_ids = {row["id"] for row in result["items"] if row["kind"] == "folder"}
    assert folder_ids == {root_mocha.id}
    assert root_sorento.id not in folder_ids
    assert root_shared.id not in folder_ids


def test_service_level_company_garbage_is_rejected(db):
    from app.services.error_handler import AppException

    service = AttachmentService(db)
    with pytest.raises(AppException) as exc_info:
        service.list_attachments(company="garbage")
    assert exc_info.value.status_code == 422


# --- S6 (reviewer fix round): the route itself 422s on a malformed `company`
# param, for both GET /attachments and GET /attachments/drive. ----------------


@pytest.fixture
def api(monkeypatch):
    from app.services.user_service import UserPermissionService

    # LIST-API-PERMS: the list and drive routes carry permission gates now; this principal
    # has no users row, so answer the role lookup as admin (the gates have their own tests).
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
    with blank_session() as session:
        session.add(Company(id=MOCHA_ID, name="Mocha", code=unique_code("MCH")[:20]))
        session.flush()

        def _override_get_db():
            yield session

        app.dependency_overrides[get_db] = _override_get_db
        principal = {"id": str(uuid.uuid4()), "email": "zzt-company-filter@test.com"}
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

        async def _override_scope():
            scope = frozenset({DEFAULT_COMPANY_ID})
            set_company_scope(session, scope)
            return scope

        app.dependency_overrides[apply_company_scope] = _override_scope

        yield session
        app.dependency_overrides.clear()


def test_s6_attachments_route_422s_on_garbage_company(api):
    with TestClient(app) as c:
        res = c.get("/api/v1/resource-management/attachments/?company=garbage")
    assert res.status_code == 422, res.text


def test_s6_drive_route_422s_on_garbage_company(api):
    with TestClient(app) as c:
        res = c.get("/api/v1/resource-management/attachments/drive?company=garbage")
    assert res.status_code == 422, res.text


def test_s6_attachments_route_accepts_shared(api):
    with TestClient(app) as c:
        res = c.get("/api/v1/resource-management/attachments/?company=shared")
    assert res.status_code == 200, res.text


def test_s6_attachments_route_accepts_a_uuid(api):
    with TestClient(app) as c:
        res = c.get(f"/api/v1/resource-management/attachments/?company={DEFAULT_COMPANY_ID}")
    assert res.status_code == 200, res.text


# --- R14 / AC-E3: the drive listing's Company column - folder rows, through
# response_model, not just files. ---------------------------------------------


def test_drive_folder_rows_carry_company_id_and_company_name(api):
    """`GET /attachments/drive` must stamp company_id/company_name on FOLDER
    rows the same way it already does for file rows - an owned folder reads
    its company's name, a shared folder reads null/null (the FE renders that
    as "Shared"). Asserted on the raw HTTP JSON body, so a field
    `response_model` silently drops would fail here, not just in
    `DriveFolderItem.model_dump()`.
    """
    db = api
    owned = _folder(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Owned Folder")
    shared = _folder(db, company_id=None, name="ZZT Shared Folder")
    db.commit()

    with TestClient(app) as c:
        res = c.get(
            "/api/v1/resource-management/attachments/drive",
            params={"recursive": True, "limit": 50},
        )

    assert res.status_code == 200, res.text
    rows = {
        row["id"]: row for row in res.json()["data"] if row.get("kind") == "folder"
    }
    assert rows[owned.id]["company_id"] == DEFAULT_COMPANY_ID
    assert rows[owned.id]["company_name"] == "Sorento"
    assert rows[shared.id]["company_id"] is None
    assert rows[shared.id]["company_name"] is None
