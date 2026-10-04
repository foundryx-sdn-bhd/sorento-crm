"""ESCALATION-CONTROL: the contact's "Chatbot hands over to support teams" flag over the API.

Owner change, 30 Sep 2026: one per-contact flag, `respond_contacts.escalation_allowed`
(default true). `PUT .../contacts/{id}/chatbot` takes it (absent = leave alone, the rule
every switch on that card follows) and every contact read carries it; asserted here
because `contact_to_response_dict` is built by hand and `response_model` drops any field
it does not declare. Access types carry no escalation setting.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.services.user_service import UserPermissionService

from tests.chatbot.test_turns_admin_api import db  # noqa: F401 - reuses the blank-schema fixture

# Own harness (the shape of `test_stock_ask_contact_toggles.py`), so this module does
# not depend on another test module's fixtures.
BASE = "/api/v1/user-management/contacts"
_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Escalation Control Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({"user_management.contacts.view", "user_management.contacts.edit"})
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in _GRANTS
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def client(db):  # noqa: F811 - fixture shadow is the point
    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _seed_contact(db) -> str:  # noqa: F811
    contact_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (:id, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {
            "id": contact_id,
            "cid": f"ZZT-{uuid.uuid4().hex[:8]}",
            "phone": f"+6002{uuid.uuid4().hex[:7]}",
            "sv": json.dumps({}),
        },
    )
    db.commit()
    return contact_id


def test_a_new_contact_reads_allowed(db, client):
    contact_id = _seed_contact(db)
    body = client.get(f"{BASE}/{contact_id}").json()
    assert body["escalation_allowed"] is True
    assert "escalation_allowed_inherited" not in body


def test_put_unticks_and_ticks_the_flag_and_absent_leaves_it_alone(db, client):
    contact_id = _seed_contact(db)
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={"escalation_allowed": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["escalation_allowed"] is False

    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={"notify_salesman": True})
    assert resp.json()["escalation_allowed"] is False

    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={"escalation_allowed": True})
    assert resp.json()["escalation_allowed"] is True
    stored = db.execute(
        text("SELECT escalation_allowed FROM respond_contacts WHERE id = :c"), {"c": contact_id}
    ).scalar()
    assert stored is True


def test_writing_the_flag_needs_the_contact_edit_grant(db, client):
    """The flag rides the contact's own chatbot card, behind `contacts.edit`."""
    contact_id = _seed_contact(db)
    _GRANTS.discard("user_management.contacts.edit")
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={"escalation_allowed": False})
    assert resp.status_code == 403, resp.text


def test_access_types_carry_no_escalation_setting(db, client):
    from app.models.access import ContactAccessType

    assert not hasattr(ContactAccessType, "escalation_allowed")


def test_a_blocked_contact_reads_blocked(db, client):
    """Kill-matrix K13: the dict builder lists the field (the schema default alone would
    read every contact as allowed)."""
    contact_id = _seed_contact(db)
    db.execute(text("UPDATE respond_contacts SET escalation_allowed = false WHERE id = :c"), {"c": contact_id})
    db.commit()
    assert client.get(f"{BASE}/{contact_id}").json()["escalation_allowed"] is False
