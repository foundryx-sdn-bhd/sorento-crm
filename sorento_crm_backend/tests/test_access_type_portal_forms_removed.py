"""Contact access type create/update ignore ``portal_form_types`` (AC-R4, D1
removal): a payload carrying it is accepted (no 422), never persisted, and
the response carries no such key.

Today (pre-D1) the field is still live on ``ContactAccessTypeCreate`` /
``Update`` (``app/schemas/user.py``) and IS persisted - so every "absent"
assertion below fails against current code.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user, get_db
from app.main import app
from app.models.access import ContactAccessType
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/user-management/contact-access-types/"


@pytest.fixture(autouse=True)
def _clear_overrides(monkeypatch):
    # The writes are gated on `user_management.reference_data.manage` since NS-SHARED-LOOKUPS
    # (they took only sign-in before); this caller is the catalog admin holding it.
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug == "user_management.reference_data.manage",
    )
    yield
    app.dependency_overrides.clear()


def _client(db) -> TestClient:
    def _db_override():
        yield db

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_user] = lambda: {"id": "zzt-user"}
    return TestClient(app)


def test_create_ignores_portal_form_types_and_response_has_no_such_key():
    with blank_session() as db:
        client = _client(db)
        code = unique_code("at").lower()[:50]

        response = client.post(
            BASE,
            json={
                "code": code,
                "name": "ZZT access type",
                "portal_form_types": ["complaint"],
            },
        )

        assert response.status_code == 201, response.text
        assert "portal_form_types" not in response.json()

        row = db.query(ContactAccessType).filter(ContactAccessType.code == code).first()
        assert row is not None
        assert getattr(row, "portal_form_types", []) in ([], None)


def test_update_ignores_portal_form_types_and_response_has_no_such_key():
    with blank_session() as db:
        client = _client(db)
        code = unique_code("at").lower()[:50]
        create = client.post(BASE, json={"code": code, "name": "ZZT access type"})
        assert create.status_code == 201, create.text

        response = client.put(
            f"{BASE}{code}",
            json={"portal_form_types": ["complaint"]},
        )

        assert response.status_code == 200, response.text
        assert "portal_form_types" not in response.json()

        row = db.query(ContactAccessType).filter(ContactAccessType.code == code).first()
        assert row is not None
        assert getattr(row, "portal_form_types", []) in ([], None)
