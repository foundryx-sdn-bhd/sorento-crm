"""GET-route permission gates on the user-management surface.

Context: `documentation/plans/security/PLAN-user-management-read-gates.md` +
`documentation/plans/security/user-management-read-gates-acceptance-criteria.md`.
Thirteen GETs across `teams.py` / `access_agents.py` / `contact_access_types.py`
were `Depends(get_current_user)` only - any authenticated session, regardless of
role, could read the contact directory, staff rosters, the agent-to-contact
access matrix and per-field ACL rows. They now require
`user_management.teams.view` or `user_management.access_agents.view` via
`Depends(require_permission(...))`.

A follow-up change resolved the plan's deferred Q1, Q2 and Q3 the same way,
taking the gated total to 24: the eight `contacts` GETs now require the newly
registered `user_management.contacts.view`, the two cross-module reference
catalogs (`contact-access-types/`, `market-segments/`) require the equally new,
deliberately low-privilege `user_management.reference_data.view`, and the full
settings blob requires `user_management.settings.view`. The two new slugs are
registered AND granted by migration `359_um_contacts_reference_perms` - a slug
with no grant path would 403 the feature it was meant to protect. That grant is
exercised against the migration itself in
tests/test_migration_359_um_contacts_reference_perms.py.

A third pass added the two `system_logs` reads (`GET /system-logs/` and
`GET /system-logs/users/{user_id}`) on the already-registered
`user_management.logs.view`, taking the total to 26. An independent review found
them - the structural sweep below could not, because its scope was a hardcoded
set of seven module names and `system_logs.py` is an eighth. That scope is now
the whole `app.api.v1.user_management` package, matched on the module path
prefix, so the next file to arrive is in scope with nothing to remember.

Structure:

* Work item 3 (UAC1.1-1.3, 2.1-2.2, 3.1-3.2, 6a.2, 6c.2) - one 403 + one 200 per
  gated route, table-driven. 25 of the 26 gated routes live in that table; the
  26th, `GET /settings/`, needs a seeded settings singleton and is covered
  alongside its `/app-config` sibling in tests/test_settings_app_config_gate.py.
* UAC2.3 - the field-access 403 fires before any field-access row is read.
* Work item 4 (UAC4.1-4.3) - structural coverage: every GET in the
  user-management package carries a permission dependency or sits in a commented
  exception allowlist, so a route added tomorrow without a gate fails this test.
* Work item 5 (UAC3.3, 5.1, 5.2) - the documented exceptions stay exactly as
  documented: the quick-access read is self-scoped, and the contacts/companies
  read is gated in the handler body rather than by dependency. The active-only
  contact-access-types catalog was a third exception until Q3 was decided; its
  test now pins the reference-data gate that replaced it, including the negative
  that matters most (UAC6c.4): holding `access_agents.view` does NOT open the
  reference catalogs, so the low-privilege slug cannot be quietly collapsed back
  into the admin one.

Dependency-override + `allow`-set monkeypatch pattern copied from
tests/test_user_respond_users_permission.py (PR #168).
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
from app.models.access import (
    AccessAgent,
    ContactAccessType,
    MarketSegment,
    RespondContact,
    Team,
    TeamMember,
)
from app.models.base import set_company_scope
from app.models.user import SystemLog, User, UserQuickAccess, UserStatus
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import AccessAgentService
from tests._pg_fixture import blank_session, unique_code

TEAMS_VIEW = "user_management.teams.view"
ACCESS_AGENTS_VIEW = "user_management.access_agents.view"
CONTACTS_VIEW = "user_management.contacts.view"
REFERENCE_DATA_VIEW = "user_management.reference_data.view"
LOGS_VIEW = "user_management.logs.view"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


def _install_overrides(db, caller: dict):
    """Wire get_db / get_current_user / the company-scope resolver onto the SAME
    test session, for a raw (no bearer token) TestClient call.

    ``apply_company_scope`` is a router-level dependency on the whole /api/v1/*
    mount (app/main.py) that re-derives the principal from the request's own
    Authorization header - it does NOT read the `get_current_user` override, so
    without this a tokenless TestClient request resolves UNSET (fail-closed) and
    every CompanyScopedMixin table (e.g. `teams`) silently reads back zero rows,
    404-ing a route that a real gated + authenticated caller would see fine. This
    pins it open to "all companies" so seeded rows are visible, matching what the
    permission gate under test actually cares about.
    """

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        set_company_scope(_db, None)
        return None

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[apply_company_scope] = _override_scope


def _clear_overrides():
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(apply_company_scope, None)


@pytest.fixture
def api(db, monkeypatch):
    """A caller whose permission set is controlled by the returned `allow` set."""
    from app.services.user_service import UserPermissionService

    allow: set[str] = set()
    caller = {"id": str(uuid.uuid4()), "email": "read-gates-caller@zzt.test"}

    _install_overrides(db, caller)
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in allow,
    )
    client = TestClient(app)
    try:
        yield client, allow, caller
    finally:
        _clear_overrides()


# --------------------------------------------------------------------- seeds


def _seed_team(db) -> str:
    team = Team(id=str(uuid.uuid4()), name=unique_code("team"))
    db.add(team)
    db.flush()
    return str(team.id)


def _seed_team_with_member(db) -> tuple[str, str]:
    team = Team(id=str(uuid.uuid4()), name=unique_code("team"))
    db.add(team)
    db.flush()
    user = User(
        id=unique_code("user"),
        email=f"{unique_code('member')}@zzt.test",
        name="ZZT Member",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    member = TeamMember(id=str(uuid.uuid4()), team_id=team.id, user_id=user.id, sort_order=0)
    db.add(member)
    db.flush()
    return str(team.id), str(user.id)


def _seed_access_agent(db) -> str:
    agent = AccessAgent(id=str(uuid.uuid4()), code=unique_code("agent"), name="ZZT Agent", is_active=True)
    db.add(agent)
    db.flush()
    return str(agent.id)


def _seed_contact_access_type(db) -> str:
    code = unique_code("cat").lower().replace("-", "_")
    row = ContactAccessType(code=code, name="ZZT Access Type", is_active=True)
    db.add(row)
    db.flush()
    return code


def _seed_contact(db) -> str:
    """A respond contact of our own. `phone_number` is UNIQUE NOT NULL on
    Postgres, so it carries the marker rather than a borrowed real number."""
    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=unique_code("phone"),
        name="ZZT Contact",
    )
    db.add(contact)
    db.flush()
    return str(contact.id)


def _seed_market_segment(db) -> str:
    code = unique_code("seg").lower().replace("-", "_")[:50]
    row = MarketSegment(
        id=str(uuid.uuid4()),
        code=code,
        name="ZZT Segment",
        is_active=True,
        sort_order=0,
    )
    db.add(row)
    db.flush()
    return code


def _seed_system_log(db) -> tuple[str, str]:
    """One log row of our own, owned by a user of our own.

    `system_logs.user_id` is a NOT NULL FK to `users.id`, and CI's database holds
    no users at all, so the owner is seeded here rather than borrowed.
    """
    user = User(
        id=unique_code("user-log"),
        email=f"{unique_code('log')}@zzt.test",
        name="ZZT Log Owner",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    log = SystemLog(
        id=str(uuid.uuid4()),
        user_id=user.id,
        event="zzt.read_gates.probe",
        description="ZZT system log",
    )
    db.add(log)
    db.flush()
    return str(user.id), str(log.id)


# --------------------------------------------------------------- work item 3


def _team_route_specs(db):
    team_id = _seed_team(db)
    team_id_with_member, member_user_id = _seed_team_with_member(db)
    return [
        ("GET /teams/",
            "/api/v1/user-management/teams/",
            TEAMS_VIEW,
            lambda r: isinstance(r.json(), list)),
        ("GET /teams/{id}",
            f"/api/v1/user-management/teams/{team_id}",
            TEAMS_VIEW,
            lambda r: r.json()["id"] == team_id),
        ("GET /teams/{id}/members",
            f"/api/v1/user-management/teams/{team_id_with_member}/members",
            TEAMS_VIEW,
            lambda r: {m["user_id"] for m in r.json()} == {member_user_id}),
        ("GET /teams/{id}/members/{uid}/market-segments",
            f"/api/v1/user-management/teams/{team_id_with_member}/members/{member_user_id}/market-segments",
            TEAMS_VIEW,
            lambda r: r.json()["codes"] == []),
    ]


def _access_agent_route_specs(db):
    agent_id = _seed_access_agent(db)
    return [
        ("GET /access-agents/",
            "/api/v1/user-management/access-agents/",
            ACCESS_AGENTS_VIEW,
            lambda r: "data" in r.json() and "pagination" in r.json()),
        ("GET /access-agents/contact-access",
            "/api/v1/user-management/access-agents/contact-access",
            ACCESS_AGENTS_VIEW,
            lambda r: "data" in r.json() and "pagination" in r.json()),
        ("GET /access-agents/{id}",
            f"/api/v1/user-management/access-agents/{agent_id}",
            ACCESS_AGENTS_VIEW,
            lambda r: r.json()["id"] == agent_id),
        ("GET /access-agents/{id}/teams",
            f"/api/v1/user-management/access-agents/{agent_id}/teams",
            ACCESS_AGENTS_VIEW,
            lambda r: r.json() == {"assignments": []}),
        ("GET /access-agents/{id}/field-access",
            f"/api/v1/user-management/access-agents/{agent_id}/field-access",
            ACCESS_AGENTS_VIEW,
            lambda r: "fields" in r.json() and "overrides" in r.json()),
        ("GET /access-agents/{id}/contact-access",
            f"/api/v1/user-management/access-agents/{agent_id}/contact-access",
            ACCESS_AGENTS_VIEW,
            lambda r: r.json() == []),
    ]


def _contact_access_type_route_specs(db):
    code = _seed_contact_access_type(db)
    return [
        ("GET /contact-access-types/all",
            "/api/v1/user-management/contact-access-types/all",
            ACCESS_AGENTS_VIEW,
            lambda r: code in {row["code"] for row in r.json()}),
        ("GET /contact-access-types/{code}",
            f"/api/v1/user-management/contact-access-types/{code}",
            ACCESS_AGENTS_VIEW,
            lambda r: r.json()["code"] == code),
    ]


def _contacts_route_specs(db):
    """UAC6a.2 - the eight contacts GETs, all on `user_management.contacts.view`.

    Every 200 check asserts a SHAPE, or a row this test seeded itself; none
    asserts a count or an identity off whatever the database happened to hold,
    because CI's database holds nothing.
    """
    contact_id = _seed_contact(db)
    base = "/api/v1/user-management/contacts"
    return [
        ("GET /contacts/",
            f"{base}/",
            CONTACTS_VIEW,
            lambda r: contact_id in {row["id"] for row in r.json()["data"]}),
        ("GET /contacts/{id}",
            f"{base}/{contact_id}",
            CONTACTS_VIEW,
            lambda r: r.json()["id"] == contact_id),
        ("GET /contacts/cs-routing/candidates",
            f"{base}/cs-routing/candidates",
            CONTACTS_VIEW,
            lambda r: isinstance(r.json()["candidates"], list)),
        # `use_case` is a REQUIRED query param - without it the route 422s before
        # the gate is proven either way, so the 403 case needs it too.
        ("GET /contacts/cs-routing/fields",
            f"{base}/cs-routing/fields?use_case=purchase_request",
            CONTACTS_VIEW,
            lambda r: isinstance(r.json()["fields"], list)),
        ("GET /contacts/{id}/cs-routing",
            f"{base}/{contact_id}/cs-routing",
            CONTACTS_VIEW,
            lambda r: r.json()["pins"] == []),
        ("GET /contacts/{id}/market-segments",
            f"{base}/{contact_id}/market-segments",
            CONTACTS_VIEW,
            lambda r: r.json()["codes"] == []),
        ("GET /contacts/{id}/attachment-types",
            f"{base}/{contact_id}/attachment-types",
            CONTACTS_VIEW,
            lambda r: {"data", "granted_ids"} <= set(r.json())),
        ("GET /contacts/{id}/access-agents",
            f"{base}/{contact_id}/access-agents",
            CONTACTS_VIEW,
            lambda r: "data" in r.json() and "pagination" in r.json()),
    ]


def _reference_data_route_specs(db):
    """The two cross-module reference catalogs. Gated on `reference_data.view` by UAC6c.2
    until the owner opened them to every signed-in user (1 Oct 2026, NS-SHARED-LOOKUPS);
    no longer in `_all_route_specs`, kept for the open-read test at the bottom."""
    access_type_code = _seed_contact_access_type(db)
    segment_code = _seed_market_segment(db)
    return [
        ("GET /contact-access-types/",
            "/api/v1/user-management/contact-access-types/",
            REFERENCE_DATA_VIEW,
            lambda r: access_type_code in {row["code"] for row in r.json()}),
        ("GET /market-segments/",
            "/api/v1/user-management/market-segments/",
            REFERENCE_DATA_VIEW,
            lambda r: segment_code in {row["code"] for row in r.json()}),
    ]


def _system_log_route_specs(db):
    """The two system-logs reads, on `user_management.logs.view`.

    Missed by the original pass and found by widening the structural sweep below
    to the whole package - they live in an eighth module the old hardcoded scope
    could not see. The slug is not new: it is already registered and is already
    the `permission:` on the `/user-management/logs` menu entry.
    """
    log_user_id, log_id = _seed_system_log(db)
    base = "/api/v1/user-management/system-logs"
    return [
        ("GET /system-logs/",
            f"{base}/",
            LOGS_VIEW,
            lambda r: log_id in {row["id"] for row in r.json()["data"]}),
        ("GET /system-logs/users/{user_id}",
            f"{base}/users/{log_user_id}",
            LOGS_VIEW,
            lambda r: {row["id"] for row in r.json()["data"]} == {log_id}),
    ]


def _all_route_specs(db):
    return (
        _team_route_specs(db)
        + _access_agent_route_specs(db)
        + _contact_access_type_route_specs(db)
        + _contacts_route_specs(db)
        + _system_log_route_specs(db)
    )


class TestPerRouteGates:
    """UAC1.1-1.3, 2.1-2.2, 3.1-3.2, 6a.2, 6c.2: one 403 + one 200 per gated route."""

    @pytest.fixture(autouse=True)
    def _specs(self, db):
        self.specs = _all_route_specs(db)
        # 25 routes are gated by this PR series; 24 are in this table (the
        # access-agents prev/next endpoint went with S3's page-scoped pager).
        # `GET /settings/` is the twenty-fifth and lives in
        # tests/test_settings_app_config_gate.py, where the settings singleton is
        # seeded with non-null sensitive values so its 200 body and its
        # /app-config sibling can be asserted together. (The structural sweep
        # below covers 42 gated routes in total - the other 17 are the 12
        # users/roles/permissions reads that were already gated before this work,
        # the 3 onboarding reads, the teams member-brands read from the
        # brand-aware escalation PR (#197) and the contact media-access read from
        # the chatbot media PR, all of which have their own tests.)
        # 22 since the two reference catalogs opened to every signed-in user
        # (NS-SHARED-LOOKUPS, owner ruling 1 Oct 2026).
        assert len(self.specs) == 22, "one entry per gated route except GET /settings/"

    def test_denied_without_permission(self, api):
        client, allow, _caller = api
        for label, url, slug, _check in self.specs:
            resp = client.get(url)
            assert resp.status_code == 403, f"{label}: expected 403, got {resp.status_code} ({resp.text})"
            assert slug in resp.json()["detail"], f"{label}: slug {slug!r} not named in detail {resp.json()!r}"

    def test_allowed_with_permission(self, api):
        client, allow, _caller = api
        for label, url, slug, check in self.specs:
            allow.clear()
            allow.add(slug)
            resp = client.get(url)
            assert resp.status_code == 200, f"{label}: expected 200, got {resp.status_code} ({resp.text})"
            assert check(resp), f"{label}: response shape unexpected: {resp.json()!r}"


def test_field_access_403_fires_before_any_field_access_row_is_read(api, monkeypatch, db):
    """UAC2.3 - a denied caller learns nothing about which fields exist.

    A sentinel that appends instead of raising: the route wraps its body in a
    try/except, so a raise inside the monkeypatched service method would be
    swallowed and read as an unrelated 500, not prove the point. Recording,
    then asserting the recording list stayed empty AFTER the response check,
    proves the permission dependency ran (and denied) before the handler body
  - and therefore before `AccessAgentService.list_field_access` - ever ran.
    """
    client, allow, _caller = api
    calls: list[tuple[str, str | None]] = []

    def _sentinel(self, agent_id, contact_id=None):
        calls.append((agent_id, contact_id))
        return {"agent_code": "should-not-be-reached", "fields": [], "overrides": []}

    monkeypatch.setattr(AccessAgentService, "list_field_access", _sentinel)

    agent_id = _seed_access_agent(db)
    resp = client.get(f"/api/v1/user-management/access-agents/{agent_id}/field-access")

    assert resp.status_code == 403
    assert ACCESS_AGENTS_VIEW in resp.json()["detail"]
    assert calls == [], "field-access rows were read despite the caller lacking the permission"


# --------------------------------------------------------------- work item 4

# Scope of the structural coverage sweep: EVERY module in the
# `app.api.v1.user_management` package, matched on the module path prefix.
#
# It used to be a hardcoded set of the seven files the plan's audit table walked,
# and that set is precisely why the two `system_logs` GETs were missed - they
# live in an eighth module, so the test whose whole job is to catch an ungated
# GET could not see them. A prefix match means a router file added tomorrow is in
# scope the moment it is mounted, with nothing to remember to update.
_IN_SCOPE_MODULE_PREFIX = "app.api.v1.user_management."

_PLAN_PATH = "documentation/plans/security/PLAN-user-management-read-gates.md"

# Every GET in the package that is deliberately NOT behind a permission
# dependency, keyed on the exact ROUTE PATH (not endpoint function name -
# `get_contact_access_agents` names two different handlers: the gated one at
# `/access-agents/{agent_id}/contact-access` and this deferred one at
# `/contacts/{contact_id}/access-agents`; a name-keyed map would conflate them).
#
# Q1 (the eight `contacts` GETs), Q2 (`GET /settings/`) and Q3 (the two reference
# catalogs) have since been decided - gate them - so their thirteen entries are
# gone from here and into the gated set below, on `user_management.contacts.view`,
# `user_management.settings.view` and `user_management.reference_data.view`
# respectively.
#
# Widening the sweep to the whole package brought in four more genuinely ungated
# GETs, all self-scoped reads of the caller's OWN row - listed below with the
# filter that makes each self-scoped, because "self-scoped" is a claim about the
# handler body that has to be re-checked whenever the body changes.
_EXCEPTION_ALLOWLIST: dict[str, str] = {
    # --- Already gated, but in the handler body (`_require_superadmin`) rather
    # than a dependency - UAC5.2 asserts this still bites.
    "/api/v1/user-management/contacts/{contact_id}/companies": (
        "gated in-body via _require_superadmin, not a dependency - see UAC5.2"
    ),
    # --- Q2 is RESOLVED, and this is the half of the resolution that stays
    # ungated. The full blob at `/settings/` - n8n webhook URLs, SMTP config,
    # default-approver identities, every role id - is now gated on
    # `user_management.settings.view`. This six-field projection (currency,
    # currency_format and the four default-approver id/email fields) is what kept
    # the three non-admin consumers working: `useCurrencyFormat`,
    # `use-excel-accept` and `PurchaseRequestDetail` run under procurement roles
    # holding no `user_management.*` slug at all, so they read the projection
    # instead. Its `response_model` is what makes "nothing sensitive is in here"
    # enforceable rather than aspirational, and
    # tests/test_settings_app_config_gate.py seeds every sensitive column non-null
    # before asserting the six keys, so the suppression is proven, not assumed.
    # See UAC6b.2 / UAC6b.3 / UAC6d.2 and Q2 in _PLAN_PATH.
    "/api/v1/user-management/settings/app-config": (
        "narrow six-field projection, auth-only by design - see the route docstring"
    ),
    # --- Shared lookups, open to every signed-in user by owner ruling 1 Oct 2026
    # (never-stuck L10, documentation/plans/never-stuck/PLAN-ns-shared-lookups.md).
    # Each returns picker fields only; the writes on both catalogs need
    # `user_management.reference_data.manage` (tests/test_ns_shared_lookups.py).
    "/api/v1/user-management/users/lookup": (
        "people picker: active users, id + name only, name-only search - NS-SHARED-LOOKUPS"
    ),
    "/api/v1/user-management/contact-access-types/": (
        "catalog read (code, name, description, sort order) - NS-SHARED-LOOKUPS"
    ),
    "/api/v1/user-management/market-segments/": (
        "catalog read, no personal data - NS-SHARED-LOOKUPS"
    ),
    # --- Self-scoped: filters on `user_id == current_user["id"]`, discloses
    # nothing about anyone else, and fires on every page load for every user
    # from the app shell (a gate would 403 users with no pin/unpin grant).
    "/api/v1/user-management/quick-access/": "self-scoped - see UAC5.1",
    # --- Self-scoped: the caller's own profile, read via `current_user["id"]`.
    "/api/v1/user-management/users/me": (
        "self-scoped - returns the caller's own profile (current_user['id'])"
    ),
    # --- Self-scoped: the caller's own effective permission slugs. The frontend
    # RBAC layer reads it on every session, and it discloses only what the caller
    # can already discover by clicking around.
    "/api/v1/user-management/users/me/permissions": (
        "self-scoped - the caller's own permission slugs (current_user['id'])"
    ),
    # --- Self-scoped: filters `ImpersonationSession.admin_user_id == real_user['id']`
    # and `ended_at IS NULL`, so it can only ever return the caller's own active
    # session. `get_real_user` rather than `get_current_user`, so an impersonated
    # session cannot read it as somebody else either.
    "/api/v1/user-management/impersonation/current": (
        "self-scoped - filters admin_user_id == real_user['id']"
    ),
    # --- Self-scoped, same shape:
    # `ContactImpersonationSession.admin_user_id == real_user['id']` + `ended_at IS NULL`.
    "/api/v1/user-management/contact-impersonation/current": (
        "self-scoped - filters admin_user_id == real_user['id']"
    ),
}


def _mounted_get_routes():
    """The real flattened GET APIRoute objects for the whole user-management package.

    Mounted onto a throwaway app (rather than read off `app.main.app.routes`)
    for the same reasons as tests/test_external_permission_coverage.py: a
    half-initialised app.main under test ordering, and lazy _IncludedRouter
    wrappers on newer FastAPI. include_router() onto a fresh app forces the
    flatten deterministically either way.
    """
    probe = FastAPI()
    probe.include_router(user_management_router, prefix="/api/v1/user-management")
    return [
        r
        for r in probe.routes
        if hasattr(r, "path")
        and "GET" in getattr(r, "methods", set())
        and getattr(r.endpoint, "__module__", "").startswith(_IN_SCOPE_MODULE_PREFIX)
    ]


def _dependant_chain_names(route) -> list[str]:
    """Every dependency callable's qualname reachable from this route's Dependant
    tree - covers both `dependencies=[...]` entries and `Depends(...)` used as a
    parameter default (the shape `require_permission` is applied in), and any
    dependency nested under either.
    """
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
    names = _dependant_chain_names(route)
    return any("require_permission" in n or "require_any_permission" in n for n in names)


class TestStructuralCoverage:
    def test_there_are_user_management_get_routes_to_check(self):
        # UAC4.2's whole point breaks if this is vacuously empty. 45 is the real
        # count now the sweep covers the whole package - 39 gated + 7 exceptions.
        # (It was 27 under the old seven-name scope: 13 gated + 13 exceptions
        # when written, then 24 gated + 3 exceptions once Q1, Q2 and Q3 were
        # decided, Q2 also adding /settings/app-config.)
        assert len(_mounted_get_routes()) >= 40

    def test_every_get_route_is_gated_or_explicitly_excepted(self):
        ungated_unexplained = []
        for route in _mounted_get_routes():
            if _is_gated(route):
                continue
            if route.path in _EXCEPTION_ALLOWLIST:
                continue
            ungated_unexplained.append(route.path)
        assert not ungated_unexplained, (
            "GET route(s) with neither a permission dependency nor an allowlist "
            f"entry - a new ungated route (UAC4.2): {ungated_unexplained}. Gate it "
            f"or add it to _EXCEPTION_ALLOWLIST with a reason; see {_PLAN_PATH}"
        )

    def test_gated_routes_match_the_set_mounted_in_the_package(self):
        """Every gated GET in the package, as an exact set rather than a count:
        a route that loses its gate fails here even if another route gains one.

        Five groups. The 26 this PR series is responsible for - the plan's audit
        table (13), the Q1/Q2/Q3 decisions (8 contacts GETs + the settings blob +
        2 reference catalogs) and the 2 system-logs reads - the 12 in
        `users.py` / `roles.py` / `permissions.py` that were already correctly
        gated before any of it, the 3 onboarding reads (the review queue, one
        request, and the access templates; the queue's prev/next endpoint went
        with S3's page-scoped pager), the member-level
        brands read on teams (`/teams/{team_id}/members/{user_id}/brands`), added
        by the brand-aware escalation routing PR (#197) alongside its
        market-segments sibling, and the per-contact media-access read
        (`/contacts/{contact_id}/media-access`) from the chatbot media PR. Those
        12 are new to this assertion only because the sweep now covers the whole
        package; nothing about them changed.

        Adding a gated GET to the package is expected to fail here once: name it
        below so the gate is stated rather than counted.
        """
        gated_paths = {r.path for r in _mounted_get_routes() if _is_gated(r)}
        # 46: the chatbot memory read (below) and the contact -> customers read
        # (`/contacts/{contact_id}/customers`, PLAN-contact-customers-29sep D2, under
        # contacts.view) each joined the package on the same day. 44 since the two
        # reference catalogs opened to every signed-in user (NS-SHARED-LOOKUPS). 45 with the
        # per-contact accessible brands read (`/contacts/{contact_id}/brands`,
        # CONTACT-BRAND-SCOPE, under contacts.view).
        assert len(gated_paths) == 45
        assert gated_paths == {
            "/api/v1/user-management/teams/",
            "/api/v1/user-management/teams/{team_id}",
            "/api/v1/user-management/teams/{team_id}/members",
            "/api/v1/user-management/teams/{team_id}/members/{user_id}/market-segments",
            "/api/v1/user-management/teams/{team_id}/members/{user_id}/brands",
            "/api/v1/user-management/access-agents/",
            "/api/v1/user-management/access-agents/contact-access",
            "/api/v1/user-management/access-agents/{agent_id}",
            "/api/v1/user-management/access-agents/{agent_id}/teams",
            "/api/v1/user-management/access-agents/{agent_id}/field-access",
            "/api/v1/user-management/access-agents/{agent_id}/contact-access",
            "/api/v1/user-management/contact-access-types/all",
            "/api/v1/user-management/contact-access-types/{code}",
            # --- Q1 decided: user_management.contacts.view
            "/api/v1/user-management/contacts/",
            "/api/v1/user-management/contacts/cs-routing/candidates",
            "/api/v1/user-management/contacts/cs-routing/fields",
            "/api/v1/user-management/contacts/{contact_id}",
            "/api/v1/user-management/contacts/{contact_id}/access-agents",
            "/api/v1/user-management/contacts/{contact_id}/attachment-types",
            "/api/v1/user-management/contacts/{contact_id}/cs-routing",
            "/api/v1/user-management/contacts/{contact_id}/customers",
            "/api/v1/user-management/contacts/{contact_id}/market-segments",
            "/api/v1/user-management/contacts/{contact_id}/brands",
            # --- The chatbot media PR, and deliberately NOT on the `contacts.view`
            # its path siblings above take: reading a contact's media gates is
            # reading who may send the bot images and voice notes and how much of
            # it, which is the same authority as changing it, so the gate is
            # `user_management.contacts.edit` (UAC S1-06, no new admin-only role).
            # Its 403/200 pair lives in tests/test_media_access_contact_route.py
            # alongside the PUT sibling it shares a service with.
            "/api/v1/user-management/contacts/{contact_id}/media-access",
            # --- Per-contact portal form override editor (PLAN-contact-portal-form-
            # override): the GET reads which portal forms a contact may see, which
            # is the same read as the contact record itself, so it takes
            # `user_management.contacts.view` like its path siblings above. Its
            # 403/200 pair and the `.edit` gate on the PUT sibling live in
            # tests/test_contact_portal_forms.py.
            "/api/v1/user-management/contacts/{contact_id}/portal-forms",
            # --- Chatbot memory lane A (PLAN-chatbot-memory-26sep.md S2): the contact's
            # memory read (facts, recent conversations, open orders), the same read as
            # the contact record, so `user_management.contacts.view`; its episodes need
            # `system.chat_history.view` as well (tests/chatbot/test_memory_security_fixes.py).
            "/api/v1/user-management/contacts/{contact_id}/chatbot/memory",
            # --- Q2 decided: user_management.settings.view (the narrow
            # /settings/app-config projection stays open - see the allowlist)
            "/api/v1/user-management/settings/",
            # The Chatbot settings screen's lane vocabulary (AC-809). Same slug as the
            # settings blob it is edited beside, and named here rather than counted.
            "/api/v1/user-management/settings/chatbot-lanes",
            # --- Q3's two reference catalogs left this set on 1 Oct 2026: the owner
            # opened them to every signed-in user (see the allowlist).
            # --- Found by widening this sweep to the whole package:
            # user_management.logs.view, the slug the /user-management/logs menu
            # entry already advertises.
            "/api/v1/user-management/system-logs/",
            "/api/v1/user-management/system-logs/users/{user_id}",
            # --- Gated long before this PR series, in modules the old
            # seven-name scope never looked at.
            "/api/v1/user-management/users/",
            "/api/v1/user-management/users/respond-users",
            "/api/v1/user-management/users/select",
            "/api/v1/user-management/users/{user_id}",
            "/api/v1/user-management/users/{user_id}/companies",
            "/api/v1/user-management/users/{user_id}/roles",
            "/api/v1/user-management/roles/",
            "/api/v1/user-management/roles/select",
            "/api/v1/user-management/roles/{role_id}",
            "/api/v1/user-management/permissions/",
            "/api/v1/user-management/permissions/select",
            "/api/v1/user-management/permissions/{permission_id}",
            # Onboarding review. Reads carry `.view`.
            "/api/v1/user-management/onboarding/requests",
            "/api/v1/user-management/onboarding/requests/{request_id}",
            "/api/v1/user-management/onboarding/templates",
        }

    def test_allowlist_paths_are_actually_mounted_and_ungated(self):
        # Guards the allowlist itself from rotting into a list of stale/typo'd
        # paths that would silently widen coverage by never matching anything.
        mounted = {r.path: r for r in _mounted_get_routes()}
        for path in _EXCEPTION_ALLOWLIST:
            assert path in mounted, f"allowlisted path {path!r} is not a mounted GET route"
            assert not _is_gated(mounted[path]), (
                f"allowlisted path {path!r} actually carries a permission dependency - "
                "remove it from the allowlist instead"
            )


# --------------------------------------------------------------- work item 5


def test_reference_catalogs_answer_any_signed_in_user(api, db):
    """UAC6c.4 is superseded. Q3 gated the two shared catalogs on the low-privilege
    `reference_data.view` so cross-module pickers would not need `access_agents.view`; the
    owner then ruled (1 Oct 2026, never-stuck L10) that they open to every signed-in user,
    because a role without that grant still saw empty access-level and segment pickers.
    The read carries catalog rows only; writes keep `reference_data.manage`."""
    client, allow, _caller = api
    specs = _reference_data_route_specs(db)

    allow.clear()
    for label, url, _slug, check in specs:
        resp = client.get(url)
        assert resp.status_code == 200, f"{label}: {resp.status_code} ({resp.text})"
        assert check(resp), label


def test_contacts_gets_deny_a_caller_holding_only_the_neighbouring_slugs(api, db):
    """The mirror of the test above, for Q1. `contacts.view` is its own slug: a
    caller holding `access_agents.view` + `teams.view` - the two slugs the
    contacts DETAIL screen's other panels read - still cannot read the contact
    directory. Names and phone numbers are the payload; adjacency to the screen
    is not entitlement to them."""
    client, allow, _caller = api
    contact_id = _seed_contact(db)

    allow.clear()
    allow.update({ACCESS_AGENTS_VIEW, TEAMS_VIEW})
    for url in (
        "/api/v1/user-management/contacts/",
        f"/api/v1/user-management/contacts/{contact_id}",
    ):
        resp = client.get(url)
        assert resp.status_code == 403, f"{url}: {resp.status_code} ({resp.text})"
        assert CONTACTS_VIEW in resp.json()["detail"]


def test_quick_access_is_self_scoped(db):
    """UAC5.1 - user A's pins are not visible to user B."""
    user_a = User(
        id=unique_code("user-a"),
        email=f"{unique_code('a')}@zzt.test",
        name="ZZT User A",
        status=UserStatus.ACTIVE.value,
    )
    user_b = User(
        id=unique_code("user-b"),
        email=f"{unique_code('b')}@zzt.test",
        name="ZZT User B",
        status=UserStatus.ACTIVE.value,
    )
    db.add_all([user_a, user_b])
    db.flush()
    db.add_all(
        [
            UserQuickAccess(id=str(uuid.uuid4()), user_id=user_a.id, path="/a/pin-1", sort_order=0),
            UserQuickAccess(id=str(uuid.uuid4()), user_id=user_b.id, path="/b/pin-1", sort_order=0),
        ]
    )
    db.flush()

    _install_overrides(db, {"id": user_a.id, "email": user_a.email})
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/user-management/quick-access/")
    finally:
        _clear_overrides()

    assert resp.status_code == 200
    paths = {row["path"] for row in resp.json()}
    assert paths == {"/a/pin-1"}


def test_contacts_companies_denies_non_superadmin(db):
    """UAC5.2 - the in-body `_require_superadmin` check still bites; a caller
    with no role grant at all (so no superadmin/admin role slug) is denied."""
    contact_id = unique_code("contact")
    caller = {"id": str(uuid.uuid4()), "email": "non-superadmin@zzt.test"}

    _install_overrides(db, caller)
    try:
        client = TestClient(app)
        resp = client.get(f"/api/v1/user-management/contacts/{contact_id}/companies")
    finally:
        _clear_overrides()

    assert resp.status_code == 403
    assert "Superadmin" in resp.text


def test_contacts_companies_was_not_grazed_by_the_contacts_view_gate(api, db):
    """UAC6a.5 - `/contacts/{id}/companies` sits between eight sibling GETs that
    all took `Depends(require_permission("user_management.contacts.view"))` in
    one pass of line-targeted edits. It must NOT have picked one up: its gate is
    the in-body `_require_superadmin`, which is strictly stronger, and a
    dependency in front of it would have quietly downgraded a superadmin-only
    read to a directory-read.

    Two halves, and both matter. A caller holding `contacts.view` is still 403'd
    (so no dependency short-circuited to allow), and the denial still comes from
    the superadmin check rather than from a permission dependency (so the reason
    is the one documented, not a coincidence).
    """
    client, allow, _caller = api
    contact_id = _seed_contact(db)
    url = f"/api/v1/user-management/contacts/{contact_id}/companies"

    allow.clear()
    allow.add(CONTACTS_VIEW)
    resp = client.get(url)

    assert resp.status_code == 403, f"{resp.status_code} ({resp.text})"
    assert "Superadmin" in resp.text
    assert CONTACTS_VIEW not in resp.text, (
        "the denial came from a permission dependency, not from _require_superadmin - "
        "this route was grazed by the contacts.view pass"
    )
