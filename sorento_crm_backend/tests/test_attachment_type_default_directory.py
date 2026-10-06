"""attachment_types.default_directory_id (R4, AC-B3, AC-B5, AC-B4).

`PLAN-scm-purchasing-consolidation-6sep.md` section 2 / UAC group B. One preference, one
column - the Upload packing list CTA needs somewhere to file the workbook it just read, and
the generic Create Attachment dialog pre-selects the same folder when a type carrying one
is picked.

Postgres only, `tests/_pg_fixture.py::blank_session` (a scratch schema built from the
CURRENT models via `create_all`, so the new column is already there without needing the
shared dev DB re-migrated - see backend `CLAUDE.md`).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

# MUST be the first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.resources import AttachmentDirectory, AttachmentType
from tests._pg_fixture import blank_session, unique_code


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


@pytest.fixture
def client(db, monkeypatch):
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.user_service import UserPermissionService

    def _override_get_db():
        yield db

    def _override_current_user():
        return {"id": "773b536d-c675-5a29-b44c-37f956462ba0"}

    # LIST-API-PERMS: the routes carry a permission gate now; this principal has no users
    # row, so answer the role lookup as admin (the gate has tests/test_list_api_perms.py).
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_current_user_or_api_key] = _override_current_user
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


_URL = "/api/v1/resource-management/attachment-types/"


def _directory(db, name: str | None = None) -> AttachmentDirectory:
    row = AttachmentDirectory(
        id=str(uuid.uuid4()),
        name=name or unique_code("Folder"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# AC-B3 / AC-B5 - round trips through create, update and the list serializer
# --------------------------------------------------------------------------- #


def test_default_directory_round_trips_through_create_update_list(db, client):
    folder = _directory(db)

    created = client.post(
        _URL,
        json={
            "type_name": unique_code("Packing List")[:50],
            "allowed_extensions": "xlsx,xls",
            "default_directory_id": folder.id,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["default_directory_id"] == folder.id, (
        "default_directory_id must be on the CREATE response body"
    )
    type_id = body["id"]

    other_folder = _directory(db)
    updated = client.put(
        f"{_URL}{type_id}",
        json={"default_directory_id": other_folder.id},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["default_directory_id"] == other_folder.id, (
        "default_directory_id must be on the UPDATE response body"
    )

    single = client.get(f"{_URL}{type_id}")
    assert single.status_code == 200, single.text
    assert single.json()["default_directory_id"] == other_folder.id

    listing = client.get(_URL, params={"limit": 200})
    assert listing.status_code == 200, listing.text
    row = next(r for r in listing.json()["data"] if r["id"] == type_id)
    assert row["default_directory_id"] == other_folder.id, (
        "default_directory_id must be on the LIST response body"
    )


# --------------------------------------------------------------------------- #
# AC-B4 - a type with no default folder behaves as today
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# `code` (Upload CTA reversal, 7 Sep) - list/read responses carry the stable
# machine key so the FE can resolve "the Packing List type" without matching
# on the editable `type_name` label.
# --------------------------------------------------------------------------- #


def test_code_is_on_the_list_and_read_response(db, client):
    att_type = AttachmentType(
        id=str(uuid.uuid4()),
        type_name=unique_code("Packing List"),
        allowed_extensions="xlsx,xls",
        code="packing_list",
    )
    db.add(att_type)
    db.commit()

    single = client.get(f"{_URL}{att_type.id}")
    assert single.status_code == 200, single.text
    assert single.json()["code"] == "packing_list"

    listing = client.get(_URL, params={"limit": 200})
    assert listing.status_code == 200, listing.text
    row = next(r for r in listing.json()["data"] if r["id"] == att_type.id)
    assert row["code"] == "packing_list"


def test_no_default_directory_reads_back_null(client):
    created = client.post(
        _URL,
        json={
            "type_name": unique_code("No Default")[:50],
            "allowed_extensions": "pdf",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["default_directory_id"] is None


# --------------------------------------------------------------------------- #
# FK behaviour - deleting the folder clears the type's default, never the type
# --------------------------------------------------------------------------- #


def test_directory_delete_sets_the_default_to_null(db):
    folder = _directory(db)
    att_type = AttachmentType(
        id=str(uuid.uuid4()),
        type_name=unique_code("Bound Type"),
        allowed_extensions="pdf",
        default_directory_id=folder.id,
    )
    db.add(att_type)
    db.commit()

    db.delete(folder)
    db.commit()

    db.refresh(att_type)
    assert att_type.default_directory_id is None
