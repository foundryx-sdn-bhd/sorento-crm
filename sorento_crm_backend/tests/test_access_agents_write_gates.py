"""Permission gates on the access-agents WRITE routes.

`documentation/plans/security/PLAN-access-agents-write-gates.md`, UAC alongside it.

The read-gates lane put `user_management.access_agents.view` on the six GETs in
`app/api/v1/user_management/access_agents.py` and left the ten writes beside them
on `Depends(get_current_user)`, which proves a session exists and nothing about
role. Found by the ACCESS-MODEL security review (PR #1434).

The wrong caller these tests use is the STRONGEST wrong caller: one holding
`.view` and nothing else. It can open every access-agents screen, so a gate that
only kept out users with no `user_management.*` slug at all would still let it
rewrite the agent it is looking at. Every denial here is asserted against that
caller, and every allow against the one slug the route is meant to take.

Shape mirrors tests/test_user_management_read_gates.py: `allow` set, patched
`check_user_has_permission`, scratch schema, real round trips through the
service wherever the route does not reach Respond.io.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1.user_management import router as user_management_router
from app.database import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.access import AccessAgent, ContactAgentAccess, RespondContact
from app.models.base import set_company_scope
from app.rbac.permission_registry import PERMISSION_REGISTRY
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import AccessAgentService
from tests._pg_fixture import blank_session, unique_code

VIEW = "user_management.access_agents.view"
ADD = "user_management.access_agents.add"
EDIT = "user_management.access_agents.edit"
DELETE = "user_management.access_agents.delete"

BASE = "/api/v1/user-management/access-agents"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def api(db, monkeypatch):
    """A caller whose permission set is the returned `allow` set.

    `apply_company_scope` is pinned open for the same reason the read-gates
    suite pins it: it re-derives the principal from the request's own bearer
    header, not from the `get_current_user` override, and a tokenless
    TestClient call would otherwise resolve fail-closed and hide every
    company-scoped row.
    """
    from app.services.user_service import UserPermissionService

    allow: set[str] = set()
    caller = {"id": str(uuid.uuid4()), "email": "write-gates-caller@zzt.test"}

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        set_company_scope(_db, None)
        return None

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in allow,
    )
    # Never reach Respond.io from a test. Both routes that would are gated on
    # `.edit`; the stub returns a name so their 200 bodies are real.
    monkeypatch.setattr(
        AccessAgentService,
        "lookup_respond_contact_name",
        lambda self, identifier: "ZZT Looked Up",
    )
    client = TestClient(app)
    try:
        yield client, allow
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(apply_company_scope, None)


# --------------------------------------------------------------------- seeds


def _seed_agent(db) -> AccessAgent:
    agent = AccessAgent(
        id=str(uuid.uuid4()), code=unique_code("agent"), name="ZZT Agent", is_active=True
    )
    db.add(agent)
    db.flush()
    return agent


def _seed_grant(db, agent: AccessAgent) -> ContactAgentAccess:
    contact = RespondContact(
        id=str(uuid.uuid4()), phone_number=unique_code("phone"), name="ZZT Contact"
    )
    db.add(contact)
    db.flush()
    grant = ContactAgentAccess(
        id=str(uuid.uuid4()),
        respond_contact_id=contact.id,
        respond_contact_phone=contact.phone_number,
        respond_contact_name=contact.name,
        agent_id=agent.id,
        is_allowed=True,
    )
    db.add(grant)
    db.flush()
    return grant


def _agent_body() -> dict:
    return {"code": unique_code("new"), "name": "ZZT New Agent"}


def _grant_body(agent: AccessAgent) -> dict:
    return {
        "agent_id": agent.id,
        "respond_contact_phone": unique_code("phone"),
        "respond_contact_name": "ZZT Dealer",
    }


def _write_route_specs(db):
    """(label, method, url, json, required slug, ok status, check) per write route.

    Each spec seeds its own rows so the 403 and the 200 run against the same
    shape and neither leaks into the other.
    """
    agent = _seed_agent(db)
    grant = _seed_grant(db, agent)
    return [
        ("POST /access-agents/", "POST", f"{BASE}/", _agent_body(), ADD, 201,
            lambda r: r.json()["name"] == "ZZT New Agent"),
        ("PUT /access-agents/{id}", "PUT", f"{BASE}/{agent.id}",
            {"name": "ZZT Renamed"}, EDIT, 200,
            lambda r: r.json()["name"] == "ZZT Renamed"),
        ("DELETE /access-agents/{id}", "DELETE", f"{BASE}/{_seed_agent(db).id}", None, DELETE, 200,
            lambda r: "deleted" in r.json()["message"]),
        ("PUT /access-agents/{id}/teams", "PUT", f"{BASE}/{agent.id}/teams",
            {"assignments": []}, EDIT, 200,
            lambda r: r.json() == {"assignments": []}),
        ("PUT /access-agents/{id}/field-access", "PUT", f"{BASE}/{agent.id}/field-access",
            {"fields": []}, EDIT, 200,
            lambda r: "fields" in r.json()),
        ("POST /access-agents/{id}/contact-access", "POST", f"{BASE}/{agent.id}/contact-access",
            _grant_body(agent), EDIT, 201,
            lambda r: r.json()["agent_id"] == agent.id),
        ("PUT /access-agents/{id}/contact-access/{cid}", "PUT",
            f"{BASE}/{agent.id}/contact-access/{grant.id}",
            {"is_allowed": False}, EDIT, 200,
            lambda r: r.json()["is_allowed"] is False),
        ("DELETE /access-agents/{id}/contact-access/{cid}", "DELETE",
            f"{BASE}/{agent.id}/contact-access/{_seed_grant(db, agent).id}", None, EDIT, 200,
            lambda r: "deleted" in r.json()["message"]),
        ("POST /access-agents/contact-access/lookup", "POST", f"{BASE}/contact-access/lookup",
            {"identifier": "+60123456789"}, EDIT, 200,
            lambda r: r.json() == {"contact_name": "ZZT Looked Up"}),
        ("POST .../contact-access/{cid}/sync-contact-name", "POST",
            f"{BASE}/{agent.id}/contact-access/{grant.id}/sync-contact-name", None, EDIT, 200,
            lambda r: r.json()["respond_contact_name"] == "ZZT Looked Up"),
    ]


class TestPerRouteGates:
    """UAC1.1-1.4, 2.1, 3.1, 3.2: one 403 + one success per write route."""

    @pytest.fixture(autouse=True)
    def _specs(self, db):
        self.specs = _write_route_specs(db)
        assert len(self.specs) == 10, "one entry per write route in access_agents.py"

    def test_a_view_only_caller_is_denied_every_write(self, api):
        client, allow = api
        allow.add(VIEW)
        for label, method, url, body, slug, _ok, _check in self.specs:
            resp = client.request(method, url, json=body)
            assert resp.status_code == 403, (
                f"{label}: expected 403 for a .view-only caller, got {resp.status_code} ({resp.text})"
            )
            assert slug in resp.json()["detail"], (
                f"{label}: slug {slug!r} not named in detail {resp.json()!r}"
            )

    def test_the_right_slug_opens_each_write(self, api):
        client, allow = api
        for label, method, url, body, slug, ok, check in self.specs:
            allow.clear()
            allow.add(slug)
            resp = client.request(method, url, json=body)
            assert resp.status_code == ok, (
                f"{label}: expected {ok}, got {resp.status_code} ({resp.text})"
            )
            assert check(resp), f"{label}: response shape unexpected: {resp.json()!r}"

    def test_the_neighbouring_slug_does_not_open_a_write(self, api):
        """`.add` is not `.edit` is not `.delete`: a caller holding every OTHER
        access-agents slug is still refused. Catches a copy-paste that gates a
        route on the slug of the route above it."""
        client, allow = api
        for label, method, url, body, slug, _ok, _check in self.specs:
            allow.clear()
            allow.update({VIEW, ADD, EDIT, DELETE} - {slug})
            resp = client.request(method, url, json=body)
            assert resp.status_code == 403, (
                f"{label}: expected 403 without {slug!r}, got {resp.status_code} ({resp.text})"
            )


def test_delete_without_the_slug_leaves_the_row(api, db):
    """UAC1.4: the 403 is a refusal, not a late one - the agent survives."""
    client, allow = api
    allow.add(VIEW)
    agent = _seed_agent(db)

    resp = client.delete(f"{BASE}/{agent.id}")

    assert resp.status_code == 403
    assert db.query(AccessAgent).filter(AccessAgent.id == agent.id).one_or_none() is not None


def test_field_access_403_fires_before_any_row_is_written(api, monkeypatch, db):
    """UAC2.2: same property as the read gate. A recording sentinel rather than a
    raise, because the route body's try/except would turn a raise into a 500."""
    client, allow = api
    allow.add(VIEW)
    calls: list = []

    def _sentinel(self, agent_id, entries, actor=None):
        calls.append(agent_id)

    monkeypatch.setattr(AccessAgentService, "set_field_access", _sentinel)
    agent = _seed_agent(db)

    resp = client.put(
        f"{BASE}/{agent.id}/field-access",
        json={"fields": [{"resource": "incoming_stock", "field_key": "eta_delay_date", "is_allowed": True}]},
    )

    assert resp.status_code == 403
    assert EDIT in resp.json()["detail"]
    assert calls == [], "field-access rows were written despite the caller lacking the permission"


# ------------------------------------------------------------ structural


def _mounted_access_agent_routes():
    probe = FastAPI()
    probe.include_router(user_management_router, prefix="/api/v1/user-management")
    return [
        r
        for r in probe.routes
        if hasattr(r, "path")
        and r.path.startswith(BASE)
        and getattr(r.endpoint, "__module__", "") == "app.api.v1.user_management.access_agents"
    ]


def _dependant_chain_names(route) -> list[str]:
    names: list[str] = []
    seen: set[int] = set()

    def _walk(dependant):
        if id(dependant) in seen:
            return
        seen.add(id(dependant))
        call = getattr(dependant, "call", None)
        if call is not None:
            names.append(getattr(call, "__qualname__", "") or "")
        for sub in getattr(dependant, "dependencies", []) or []:
            _walk(sub)

    _walk(route.dependant)
    return names


def _is_gated(route) -> bool:
    return any("require_permission" in n for n in _dependant_chain_names(route))


class TestStructuralCoverage:
    def test_every_write_route_in_the_file_is_gated(self):
        """UAC4.1: a new ungated write fails here by name."""
        ungated = sorted(
            f"{sorted(r.methods)} {r.path}"
            for r in _mounted_access_agent_routes()
            if r.methods - {"GET", "HEAD", "OPTIONS"} and not _is_gated(r)
        )
        assert ungated == [], f"write route(s) without a permission dependency: {ungated}"

    def test_the_file_still_has_the_ten_writes_this_plan_gates(self):
        """The sweep above is vacuous if the routes moved out of the module."""
        writes = [
            r for r in _mounted_access_agent_routes() if r.methods - {"GET", "HEAD", "OPTIONS"}
        ]
        assert len(writes) == 10

    def test_the_six_reads_keep_the_view_gate(self):
        """UAC4.2 (REG): the fix must not graze the GETs beside the writes."""
        reads = [r for r in _mounted_access_agent_routes() if "GET" in r.methods]
        assert len(reads) == 6
        assert all(_is_gated(r) for r in reads)

    def test_the_three_write_slugs_are_already_registered(self):
        """UAC4.3 (REG): no migration - the slugs the routes name exist."""
        slugs = {p["slug"] for p in PERMISSION_REGISTRY}
        assert {ADD, EDIT, DELETE} <= slugs
