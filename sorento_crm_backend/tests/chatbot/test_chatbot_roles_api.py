"""ACCESS-MODEL S4: the admin API (AC-AM-1, 2, 3, 4, 17 registry).

Red-first: `app/api/v1/system/chatbot_roles.py` is not mounted yet, so every call 404s (and the
fixtures that import `app/models/chatbot_access.py` raise ImportError).

Routes (prefix `/api/v1/system/chatbot`):
  GET  /access/registry
  GET|POST /roles           GET|PATCH|DELETE /roles/{role_id}      PUT /roles/{role_id}/grants
  GET|PUT /contacts/{contact_id}/access     (contact_id = respond_contacts.id)

Permissions: reads `user_management.access_agents.view`, role writes
`user_management.reference_data.manage`; contact access GET `user_management.contacts.view`,
PUT `user_management.contacts.edit`. Response fields are asserted by name because
`response_model` silently drops undeclared fields.
"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.services.user_service import UserPermissionService

from tests.chatbot._access_seed import (
    give_role,
    make_contact,
    make_domain,
    make_field,
    make_role,
    rid,
)
from tests.chatbot.test_turns_admin_api import db  # noqa: F401 - reuses the blank-schema fixture

BASE = "/api/v1/system/chatbot"
ACCESS_VIEW = "user_management.access_agents.view"
ROLE_MANAGE = "user_management.reference_data.manage"
CONTACT_VIEW = "user_management.contacts.view"
CONTACT_EDIT = "user_management.contacts.edit"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Roles Tester"}


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({ACCESS_VIEW, ROLE_MANAGE, CONTACT_VIEW, CONTACT_EDIT})
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture()
def client(db):  # noqa: F811
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


def _registry(db):
    make_domain(db, "zzt_stock")
    make_domain(db, "zzt_orders", access_section="reports")
    make_field(db, "zzt_stock", "zzt.stock.sellable", label="Sellable")
    make_field(db, "zzt_orders", "zzt.orders.outstanding", kind="report", label="Outstanding")


def _create(client, name: str = "ZZT Project sales", **extra) -> dict:
    resp = client.post(f"{BASE}/roles", json={"name": name, "description": "d", "sees_all_customers": False, **extra})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


ROLE_KEYS = {"id", "code", "name", "description", "sees_all_customers", "domains", "fields", "contact_count"}


class TestRegistry:
    def test_lists_domains_with_fields_and_escalation(self, client, db):
        from sqlalchemy import text

        _registry(db)
        db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), 'zzt_agent', 'Z', true, false, false)"
            )
        )
        db.commit()
        from app.models.chatbot_policy import ChatbotDomain

        row = db.query(ChatbotDomain).filter(ChatbotDomain.name == "zzt_stock").one()
        row.escalation_agent_code = "zzt_agent"
        row.escalation_team_code = "warehouse"
        db.commit()

        resp = client.get(f"{BASE}/access/registry")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"domains"}
        by_name = {d["name"]: d for d in body["domains"]}
        stock = by_name["zzt_stock"]
        assert {"name", "label", "supported", "access_section", "escalation_agent_code", "escalation_team_code", "fields"} <= set(stock)
        assert stock["escalation_agent_code"] == "zzt_agent"
        assert stock["escalation_team_code"] == "warehouse"
        assert stock["fields"] == [{"key": "zzt.stock.sellable", "label": "Sellable", "kind": "field"}]
        assert by_name["zzt_orders"]["fields"][0]["kind"] == "report"
        assert by_name["zzt_orders"]["access_section"] == "reports"
        assert stock["access_section"] is None

    def test_requires_view_permission(self, client, db):
        _GRANTS.discard(ACCESS_VIEW)
        assert client.get(f"{BASE}/access/registry").status_code == 403


class TestRolesCrud:
    def test_create_returns_the_documented_fields(self, client, db):
        body = _create(client, "ZZT Project sales", sees_all_customers=True)
        assert ROLE_KEYS <= set(body)
        assert body["name"] == "ZZT Project sales"
        assert body["sees_all_customers"] is True
        assert body["domains"] == [] and body["fields"] == []
        assert body["contact_count"] == 0
        assert body["code"], "a code is minted at create"

    def test_list_and_get_carry_the_same_fields(self, client, db):
        created = _create(client)
        listed = client.get(f"{BASE}/roles")
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"] if isinstance(listed.json(), dict) else listed.json()
        mine = [r for r in items if r["id"] == created["id"]]
        assert len(mine) == 1
        assert ROLE_KEYS <= set(mine[0])
        one = client.get(f"{BASE}/roles/{created['id']}")
        assert one.status_code == 200, one.text
        assert ROLE_KEYS <= set(one.json())

    def test_patch_renames_and_flips_the_flag_but_keeps_the_code(self, client, db):
        created = _create(client)
        resp = client.patch(
            f"{BASE}/roles/{created['id']}",
            json={"name": "ZZT Renamed", "description": "new", "sees_all_customers": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "ZZT Renamed"
        assert body["description"] == "new"
        assert body["sees_all_customers"] is True
        assert body["code"] == created["code"], "code is immutable"
        assert client.get(f"{BASE}/roles/{created['id']}").json()["name"] == "ZZT Renamed"

    def test_unknown_role_is_404(self, client, db):
        created = _create(client)  # the route exists: a bare 404 would also be "route not mounted"
        assert client.get(f"{BASE}/roles/{created['id']}").status_code == 200
        assert client.get(f"{BASE}/roles/{uuid.uuid4()}").status_code == 404

    def test_delete_of_a_held_role_is_409_naming_the_contact_count(self, client, db):
        """AC-AM-3."""
        created = _create(client)
        for _ in range(3):
            pk, _rio = make_contact(db)
            give_role(db, pk, created["id"])
        resp = client.delete(f"{BASE}/roles/{created['id']}")
        assert resp.status_code == 409, resp.text
        assert re.search(r"\b3\b", resp.text), resp.text
        assert client.get(f"{BASE}/roles/{created['id']}").status_code == 200
        assert client.get(f"{BASE}/roles/{created['id']}").json()["contact_count"] == 3

    def test_delete_of_an_unheld_role_is_204_and_takes_its_ticks(self, client, db):
        from app.models.chatbot_access import ChatbotRoleDomain, ChatbotRoleField

        _registry(db)
        created = _create(client)
        put = client.put(
            f"{BASE}/roles/{created['id']}/grants",
            json={"domains": ["zzt_stock"], "fields": ["zzt.stock.sellable"]},
        )
        assert put.status_code == 200, put.text
        resp = client.delete(f"{BASE}/roles/{created['id']}")
        assert resp.status_code == 204, resp.text
        assert client.get(f"{BASE}/roles/{created['id']}").status_code == 404
        assert db.query(ChatbotRoleDomain).filter(ChatbotRoleDomain.role_id == created["id"]).count() == 0
        assert db.query(ChatbotRoleField).filter(ChatbotRoleField.role_id == created["id"]).count() == 0

    def test_writes_require_the_manage_slug(self, client, db):
        created = _create(client)
        _GRANTS.discard(ROLE_MANAGE)
        assert client.post(f"{BASE}/roles", json={"name": "x", "description": "", "sees_all_customers": False}).status_code == 403
        assert client.patch(f"{BASE}/roles/{created['id']}", json={"name": "y"}).status_code == 403
        assert client.delete(f"{BASE}/roles/{created['id']}").status_code == 403
        assert client.put(f"{BASE}/roles/{created['id']}/grants", json={"domains": [], "fields": []}).status_code == 403
        # reads are untouched
        assert client.get(f"{BASE}/roles").status_code == 200

    def test_reads_require_the_view_slug(self, client, db):
        created = _create(client)
        _GRANTS.discard(ACCESS_VIEW)
        assert client.get(f"{BASE}/roles").status_code == 403
        assert client.get(f"{BASE}/roles/{created['id']}").status_code == 403


class TestRoleGrants:
    def test_put_is_a_full_replace_and_get_reads_it_back(self, client, db):
        """AC-AM-2."""
        _registry(db)
        created = _create(client)
        first = client.put(
            f"{BASE}/roles/{created['id']}/grants",
            json={"domains": ["zzt_stock", "zzt_orders"], "fields": ["zzt.stock.sellable"]},
        )
        assert first.status_code == 200, first.text
        got = client.get(f"{BASE}/roles/{created['id']}").json()
        assert sorted(got["domains"]) == ["zzt_orders", "zzt_stock"]
        assert got["fields"] == ["zzt.stock.sellable"]

        second = client.put(
            f"{BASE}/roles/{created['id']}/grants",
            json={"domains": ["zzt_orders"], "fields": ["zzt.orders.outstanding"]},
        )
        assert second.status_code == 200, second.text
        got = client.get(f"{BASE}/roles/{created['id']}").json()
        assert got["domains"] == ["zzt_orders"]
        assert got["fields"] == ["zzt.orders.outstanding"]

    def test_unknown_domain_is_422_and_writes_nothing(self, client, db):
        _registry(db)
        created = _create(client)
        client.put(f"{BASE}/roles/{created['id']}/grants", json={"domains": ["zzt_stock"], "fields": []})
        resp = client.put(
            f"{BASE}/roles/{created['id']}/grants", json={"domains": ["zzt_stock", "zzt_nope"], "fields": []}
        )
        assert resp.status_code == 422, resp.text
        assert "zzt_nope" in resp.text
        assert client.get(f"{BASE}/roles/{created['id']}").json()["domains"] == ["zzt_stock"]

    def test_unknown_field_is_422_and_writes_nothing(self, client, db):
        _registry(db)
        created = _create(client)
        resp = client.put(
            f"{BASE}/roles/{created['id']}/grants",
            json={"domains": ["zzt_stock"], "fields": ["zzt.stock.sellable", "zzt.nope"]},
        )
        assert resp.status_code == 422, resp.text
        assert "zzt.nope" in resp.text
        got = client.get(f"{BASE}/roles/{created['id']}").json()
        assert got["domains"] == [] and got["fields"] == []

    def test_a_malformed_body_is_422(self, client, db):
        created = _create(client)
        assert client.put(f"{BASE}/roles/{created['id']}/grants", json={"domains": "zzt_stock"}).status_code == 422


class TestContactAccess:
    def test_get_returns_roles_overrides_and_effective(self, client, db):
        _registry(db)
        pk, _rio = make_contact(db)
        role_id = make_role(db, rid("r"), domains=["zzt_stock"], fields=["zzt.stock.sellable"], sees_all=True)
        give_role(db, pk, role_id)
        resp = client.get(f"{BASE}/contacts/{pk}/access")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) >= {"roles", "overrides", "effective"}
        assert [r["id"] for r in body["roles"]] == [role_id]
        assert {"id", "code", "name"} <= set(body["roles"][0])
        assert body["overrides"] == []
        assert set(body["effective"]) >= {"domains", "attributes", "sees_all_customers"}
        assert body["effective"]["domains"] == ["zzt_stock"]
        assert body["effective"]["attributes"] == ["zzt.stock.sellable"]
        assert body["effective"]["sees_all_customers"] is True

    def test_put_replaces_roles_and_overrides_and_effective_follows(self, client, db):
        _registry(db)
        pk, _rio = make_contact(db)
        role_a = make_role(db, rid("a"), domains=["zzt_stock", "zzt_orders"], fields=["zzt.stock.sellable"])
        role_b = make_role(db, rid("b"), domains=["zzt_orders"])
        first = client.put(
            f"{BASE}/contacts/{pk}/access",
            json={
                "role_ids": [role_a],
                "overrides": [{"domain_name": "zzt_orders", "field_key": None, "granted": False}],
            },
        )
        assert first.status_code == 200, first.text
        body = client.get(f"{BASE}/contacts/{pk}/access").json()
        assert [r["id"] for r in body["roles"]] == [role_a]
        assert body["overrides"] == [{"domain_name": "zzt_orders", "field_key": None, "granted": False}]
        assert body["effective"]["domains"] == ["zzt_stock"], "the remove override beats the role add"

        second = client.put(f"{BASE}/contacts/{pk}/access", json={"role_ids": [role_b], "overrides": []})
        assert second.status_code == 200, second.text
        body = client.get(f"{BASE}/contacts/{pk}/access").json()
        assert [r["id"] for r in body["roles"]] == [role_b]
        assert body["overrides"] == []
        assert body["effective"]["domains"] == ["zzt_orders"]

    def test_role_page_contact_count_follows_the_contact_write(self, client, db):
        """AC-AM-4: both screens write the same rows."""
        _registry(db)
        pk, _rio = make_contact(db)
        created = _create(client)
        client.put(f"{BASE}/contacts/{pk}/access", json={"role_ids": [created["id"]], "overrides": []})
        assert client.get(f"{BASE}/roles/{created['id']}").json()["contact_count"] == 1

    def test_unknown_contact_is_404(self, client, db):
        real, _rio = make_contact(db)
        assert client.get(f"{BASE}/contacts/{real}/access").status_code == 200
        assert client.get(f"{BASE}/contacts/ZZT-no-such/access").status_code == 404
        assert client.put(f"{BASE}/contacts/ZZT-no-such/access", json={"role_ids": [], "overrides": []}).status_code == 404

    def test_put_requires_edit_and_get_requires_view(self, client, db):
        pk, _rio = make_contact(db)
        _GRANTS.discard(CONTACT_EDIT)
        assert client.put(f"{BASE}/contacts/{pk}/access", json={"role_ids": [], "overrides": []}).status_code == 403
        assert client.get(f"{BASE}/contacts/{pk}/access").status_code == 200
        _GRANTS.discard(CONTACT_VIEW)
        assert client.get(f"{BASE}/contacts/{pk}/access").status_code == 403

    def test_put_with_a_malformed_body_is_422(self, client, db):
        pk, _rio = make_contact(db)
        assert client.put(f"{BASE}/contacts/{pk}/access", json={"role_ids": "x"}).status_code == 422
