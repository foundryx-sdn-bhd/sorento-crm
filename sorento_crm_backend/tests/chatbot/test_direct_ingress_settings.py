"""CHAT-SPEED direct ingress, the owner's controls (UAC AC-11, AC-12).

The switch is two `system_settings` columns on Settings > Chatbot, through the manual GET
and PUT builders in `app/api/v1/user_management/settings.py`. The sender target is two
`respond_workspaces` columns next to the retry ingress pair, written by the same narrow
`PUT /{id}/chatbot-retry` route (Settings slug), with the same outbound guard and a
write-only key.
"""
from __future__ import annotations

import socket
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.models.user import SystemSetting
from app.schemas.respond_workspace import RespondWorkspaceCreate
from app.services.respond_workspace_service import RespondWorkspaceService
from app.services.user_service import UserPermissionService
from app.utils.field_encryption import decrypt_secret
from tests._pg_fixture import TEST_PREFIX, blank_session

SENDER_URL = "https://automate-zzt.example.com/webhook/chatbot-sender"
SENDER_KEY = "ZZT-plain-sender-key-0002"
WORKSPACES_BASE = "/api/v1/system/respond-workspaces"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def api(db, monkeypatch):
    allow = {"user_management.settings.view", "user_management.settings.edit"}

    def _override_db():
        yield db

    actor = {"id": str(uuid.uuid4()), "email": "direct-ingress@zzt.test", "name": "ZZT"}
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    monkeypatch.setattr(UserPermissionService, "get_user_permission_slugs", lambda self, uid: set(allow))
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# AC-11: the switch
# --------------------------------------------------------------------------- #


def test_columns_default_off(db):
    row = SystemSetting(id=str(uuid.uuid4()), name=f"{TEST_PREFIX} Co")
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.chatbot_direct_ingress_mode == "off"
    assert row.chatbot_direct_ingress_contacts is None


def test_settings_roundtrip(api, db):
    db.add(SystemSetting(id=str(uuid.uuid4()), name=f"{TEST_PREFIX} Co"))
    db.commit()

    before = api.get("/api/v1/user-management/settings/").json()["settings"]
    assert before["chatbot_direct_ingress_mode"] == "off"
    assert before["chatbot_direct_ingress_contacts"] is None

    resp = api.put(
        "/api/v1/user-management/settings/general",
        json={
            "chatbot_direct_ingress_mode": "contacts",
            "chatbot_direct_ingress_contacts": "ZZT100000077\nZZT100000078",
        },
    )
    assert resp.status_code == 200, resp.text

    after = api.get("/api/v1/user-management/settings/").json()["settings"]
    assert after["chatbot_direct_ingress_mode"] == "contacts"
    assert after["chatbot_direct_ingress_contacts"] == "ZZT100000077\nZZT100000078"


@pytest.mark.parametrize("bad", ["on", "ALL ", "everyone", ""])
def test_settings_rejects_bad_mode(api, db, bad):
    db.add(SystemSetting(id=str(uuid.uuid4()), name=f"{TEST_PREFIX} Co"))
    db.commit()
    resp = api.put(
        "/api/v1/user-management/settings/general",
        json={"chatbot_direct_ingress_mode": bad},
    )
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# AC-12: the sender target on the workspace
# --------------------------------------------------------------------------- #


@pytest.fixture
def workspace(db):
    return RespondWorkspaceService(db).create(
        RespondWorkspaceCreate(space_id="ZZT-space-sender", api_key="k", is_default=True)
    )


def _resolve_public(host, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))]


def test_workspace_sender_fields(api, db, workspace, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_public)
    resp = api.put(
        f"{WORKSPACES_BASE}/{workspace.id}/chatbot-retry",
        json={"chatbot_sender_url": SENDER_URL, "chatbot_sender_key": SENDER_KEY},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chatbot_sender_url"] == SENDER_URL
    assert body["has_chatbot_sender_key"] is True
    assert SENDER_KEY not in resp.text

    db.refresh(workspace)
    assert workspace.chatbot_sender_key_ciphertext != SENDER_KEY
    assert decrypt_secret(workspace.chatbot_sender_key_ciphertext) == SENDER_KEY
    # The retry pair is untouched by a body that did not mention it.
    assert workspace.chatbot_retry_ingress_url is None


def test_workspace_sender_url_refused_when_unsafe(api, db, workspace):
    resp = api.put(
        f"{WORKSPACES_BASE}/{workspace.id}/chatbot-retry",
        json={"chatbot_sender_url": "http://127.0.0.1/webhook/zzt"},
    )
    assert resp.status_code == 422, resp.text
    db.refresh(workspace)
    assert workspace.chatbot_sender_url is None


def test_workspace_sender_blank_clears(api, db, workspace, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_public)
    api.put(
        f"{WORKSPACES_BASE}/{workspace.id}/chatbot-retry",
        json={"chatbot_sender_url": SENDER_URL, "chatbot_sender_key": SENDER_KEY},
    )
    resp = api.put(
        f"{WORKSPACES_BASE}/{workspace.id}/chatbot-retry",
        json={"chatbot_sender_url": "", "chatbot_sender_key": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["chatbot_sender_url"] is None
    assert resp.json()["has_chatbot_sender_key"] is False
