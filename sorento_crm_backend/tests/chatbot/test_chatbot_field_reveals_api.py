"""`GET /system/chatbot/field-reveal-keys` and `GET|PUT
.../contacts/{id}/field-reveals` (chatbot growth r1, Slice C1, AC-963, AC-964).

`field-reveal-keys` is served from the frozen `contact_field_reveal_service.
FIELD_REVEAL_KEYS` literal, not a live `mcp_tools` query (see that service module's
docstring), so these tests assert against the real literal's keys instead of seeding an
`McpTool` row.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS
from app.services.user_service import UserPermissionService

from tests.chatbot.test_turns_admin_api import db  # noqa: F401 - reuses the blank-schema fixture

# DO-ASK-SIMPLIFY (owner, 4 Oct 2026): every DO field reveal defaults ON for every contact.
DO_KEYS = sorted(key for key, _label in FIELD_REVEAL_KEYS if key.startswith("delivery_orders."))

BASE = "/api/v1/system/chatbot"
CONTACT_VIEW = "user_management.contacts.view"
CONTACT_EDIT = "user_management.contacts.edit"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Field Reveal Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.add(CONTACT_VIEW)
    _GRANTS.add(CONTACT_EDIT)
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def client(db):  # noqa: F811 - fixture shadow is the point
    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    _ACTOR["id"] = str(uuid.uuid4())
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _contact(db) -> str:
    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": f"ZZT-{uuid.uuid4().hex[:8]}", "phone": f"+6000{uuid.uuid4().hex[:7]}", "sv": json.dumps({})},
    )
    db.commit()
    return db.execute(text("SELECT id FROM respond_contacts ORDER BY created_at DESC LIMIT 1")).scalar()


class TestFieldRevealKeys:
    def test_lists_keys_with_labels(self, client, db):
        resp = client.get(f"{BASE}/field-reveal-keys")
        assert resp.status_code == 200, resp.text
        keys = {item["key"]: item["label"] for item in resp.json()["items"]}
        assert dict(FIELD_REVEAL_KEYS) == keys

    def test_requires_permission(self, client, db):
        _GRANTS.discard(CONTACT_VIEW)
        resp = client.get(f"{BASE}/field-reveal-keys")
        assert resp.status_code == 403


class TestContactFieldReveals:
    def test_get_defaults_to_the_do_keys(self, client, db):
        contact_id = _contact(db)
        resp = client.get(f"{BASE}/contacts/{contact_id}/field-reveals")
        assert resp.status_code == 200, resp.text
        assert resp.json()["granted"] == DO_KEYS

    def test_put_replaces_and_get_reflects_it(self, client, db):
        contact_id = _contact(db)

        put_resp = client.put(
            f"{BASE}/contacts/{contact_id}/field-reveals",
            json={"granted": ["inventory.sellable"]},
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["granted"] == ["inventory.sellable"]

        get_resp = client.get(f"{BASE}/contacts/{contact_id}/field-reveals")
        assert get_resp.json()["granted"] == ["inventory.sellable"]

        # Full replace: the previous key drops off, the new one lands.
        put_resp2 = client.put(
            f"{BASE}/contacts/{contact_id}/field-reveals",
            json={"granted": ["purchase_orders.supplier"]},
        )
        assert put_resp2.json()["granted"] == ["purchase_orders.supplier"]

    def test_unknown_contact_is_404(self, client, db):
        resp = client.get(f"{BASE}/contacts/ZZT-no-such-contact/field-reveals")
        assert resp.status_code == 404

    def test_put_rejects_an_unknown_key(self, client, db):
        contact_id = _contact(db)

        resp = client.put(
            f"{BASE}/contacts/{contact_id}/field-reveals",
            json={"granted": ["inventory.sellable", "not_a_real_key"]},
        )
        assert resp.status_code == 422, resp.text
        assert "not_a_real_key" in resp.text
        assert "inventory.sellable" in resp.text  # the allowed list, named

        # Nothing was written by the rejected call.
        get_resp = client.get(f"{BASE}/contacts/{contact_id}/field-reveals")
        assert get_resp.json()["granted"] == DO_KEYS

    def test_put_requires_edit_permission(self, client, db):
        contact_id = _contact(db)
        _GRANTS.discard(CONTACT_EDIT)
        resp = client.put(
            f"{BASE}/contacts/{contact_id}/field-reveals", json={"granted": []}
        )
        assert resp.status_code == 403


class TestUnknownHeldKeysAreKept:
    """Tester pass of #1431: the dev DB holds `delivery_orders.*` reveal rows seeded by
    another lane (#1433) that this build does not know. The contact screen sends the
    contact's held keys back on save, so rejecting every unknown key 422'd ANY reveal
    change on that contact (merge order). A key the contact already holds and this build
    does not know is kept untouched; a NEW unknown key is still a 422."""

    HELD = "delivery_orders.zzt_future_key"

    def _hold(self, db, contact_id: str) -> None:
        from app.models.access import ContactFieldReveal

        db.add(ContactFieldReveal(respond_contact_id=contact_id, field_key=self.HELD, granted=True))
        db.add(
            ContactFieldReveal(
                respond_contact_id=contact_id, field_key="inventory.sellable", granted=True
            )
        )
        db.commit()

    def test_save_echoing_a_held_unknown_key_succeeds_and_keeps_it(self, client, db):
        contact_id = _contact(db)
        self._hold(db, contact_id)

        resp = client.put(
            f"{BASE}/contacts/{contact_id}/field-reveals", json={"granted": [self.HELD]}
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["granted"] == [self.HELD]

    def test_save_omitting_a_held_unknown_key_leaves_it_untouched(self, client, db):
        contact_id = _contact(db)
        self._hold(db, contact_id)

        resp = client.put(f"{BASE}/contacts/{contact_id}/field-reveals", json={"granted": []})

        assert resp.status_code == 200, resp.text
        assert resp.json()["granted"] == [self.HELD]

    def test_a_new_unknown_key_is_still_rejected(self, client, db):
        contact_id = _contact(db)
        self._hold(db, contact_id)

        resp = client.put(
            f"{BASE}/contacts/{contact_id}/field-reveals",
            json={"granted": [self.HELD, "not_a_real_key"]},
        )

        assert resp.status_code == 422, resp.text
        assert "not_a_real_key" in resp.text
