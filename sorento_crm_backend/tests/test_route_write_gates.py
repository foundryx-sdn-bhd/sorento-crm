"""ROUTE-PERM-GATES: three routers whose writes checked only "signed in".

Found by the security review of #1703 (SETTINGS-PERM-GATE). Every write below depended on
`get_current_user` (or `get_current_user_or_api_key`) alone, so any staff session, a
portal user's session, or an impersonated one could change them:

- `system/calendar.py`: the work calendar (which days count as working days, and the
  working hours) and the public holidays, which every SLA deadline is computed from.
  Gate: `system.work_calendar.{add,edit,delete}` (`app/rbac/permission_registry.py`,
  `_crud("system", "work_calendar", ...)`), the slug the integration read of the same
  calendar already carries (`external/permissions.py` `"work-calendar"`).
- `system/scheduled_tasks.py`: enabling, disabling or rescheduling a cron job, and running
  one now. There is no scheduled-tasks slug in the registry; the screen is
  `<RequireAccess superadmin>` (`scheduled-tasks/page.tsx`) and `superadminOnly` in the
  menu, i.e. the admin / superadmin role. The writes take that same role gate.
- `sla/sla_policies.py`: the six policy and tier writes. Gate:
  `sla_management.sla_policies.{add,edit,delete}` (`_crud("sla_management",
  "sla_policies", ...)`); its reads already carry `.view`. Tier writes take `.edit`: a
  tier is part of its policy (the create body carries the tiers, the tiers table sits on
  the policy's own page). The API-key path is kept: the slug is checked against the
  integration's act-as user, as the reads already do.

Admin is exercised through the REAL `check_user_has_permission` (its admin bypass) and
the real role lookup's answer pinned to `{"admin"}`, so the test fails if the bypass ever
stops reaching these routes. Every slug case pins `check_user_has_permission` against an
`allow` set, the pattern in tests/test_settings_write_gates.py.

Postgres `blank_session()` only, never sqlite.

Run: pytest tests/test_route_write_gates.py -v
"""
from __future__ import annotations

import uuid
from datetime import date, time

import pytest

from tests._pg_fixture import TEST_PREFIX, blank_session

CAL_ADD = "system.work_calendar.add"
CAL_EDIT = "system.work_calendar.edit"
CAL_DELETE = "system.work_calendar.delete"
SLA_ADD = "sla_management.sla_policies.add"
SLA_EDIT = "sla_management.sla_policies.edit"
SLA_DELETE = "sla_management.sla_policies.delete"
SLA_VIEW = "sla_management.sla_policies.view"

SYSTEM = "/api/v1/system"
SLA = "/api/v1/sla-management/sla-policies"

HOLIDAY_DATE = date(2031, 3, 4)
NEW_HOLIDAY_DATE = date(2031, 5, 6)
HOLIDAY_NAME = f"{TEST_PREFIX} Holiday"
POLICY_CODE = f"{TEST_PREFIX}-RPG-POLICY"
POLICY_NAME = f"{TEST_PREFIX} RPG Policy"
TIER_NAME = f"{TEST_PREFIX} RPG Tier 1"
TASK_KEY = f"{TEST_PREFIX.lower()}_rpg_task"


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def db():
    from app.models.base import set_company_scope

    with blank_session() as s:
        # Unscoped, as an API-key caller is: the SLA create then writes to the incumbent
        # company.
        set_company_scope(s, None)
        yield s


@pytest.fixture
def caller(db):
    from app.models.user import User

    user = User(
        id=str(uuid.uuid4()),
        email=f"{TEST_PREFIX}-rpg-caller-{uuid.uuid4().hex[:6]}@zzt.test".lower(),
        name=f"{TEST_PREFIX} RPG Caller",
        avatar_storage_provider="s3",
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def seeded(db):
    """One of each record the writes act on, so every write has something to change."""
    from app.models.calendar import PublicHoliday, WorkCalendarConfig
    from app.models.scheduled_task import ScheduledTask
    from app.models.sla import SLAPolicy, SLAPolicyTier
    from app.services.company_routing_service import DEFAULT_COMPANY_ID

    # The blank schema already carries the incumbent company (DEFAULT_COMPANY_ID).
    config = WorkCalendarConfig(
        config_key="default",
        saturday=False,
        work_day_start_time=time(9, 0),
        work_day_end_time=time(17, 0),
    )
    holiday = PublicHoliday(date=HOLIDAY_DATE, name=HOLIDAY_NAME)
    task = ScheduledTask(
        key=TASK_KEY,
        name=f"{TEST_PREFIX} RPG Task",
        enabled=True,
        interval_unit="hours",
        interval_value=6,
    )
    policy = SLAPolicy(code=POLICY_CODE, name=POLICY_NAME, company_id=DEFAULT_COMPANY_ID)
    db.add_all([config, holiday, task, policy])
    db.flush()
    tier = SLAPolicyTier(policy_id=policy.id, tier_level=1, tier_name=TIER_NAME, response_hours=1)
    db.add(tier)
    db.commit()
    return {
        "holiday_id": str(holiday.id),
        "task_id": str(task.id),
        "policy_id": str(policy.id),
        "tier_id": str(tier.id),
    }


@pytest.fixture
def run_now_calls(monkeypatch):
    """Run-now must never start a real job here: pin the service call the route makes."""
    from app.api.v1.system import scheduled_tasks as route

    calls: list[str] = []

    def _fake(db, task_id, requested_by_user_id=None):
        calls.append(task_id)
        return {"status": "success", "task_id": task_id}

    monkeypatch.setattr(route, "run_task_now", _fake)
    return calls


def _client(db, who):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: who
    # The module guards (and the SLA routes) resolve the caller through this one.
    app.dependency_overrides[get_current_user_or_api_key] = lambda: who
    # Keep the scope the `db` fixture set; the real resolver would read a request that
    # carries no company context.
    app.dependency_overrides[apply_company_scope] = lambda: None
    return TestClient(app)


@pytest.fixture
def client(db, caller):
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope

    try:
        yield _client(db, {"id": caller.id, "email": caller.email})
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_or_api_key, None)
        app.dependency_overrides.pop(apply_company_scope, None)


@pytest.fixture
def api_key_client(db, caller):
    """The same caller arriving as an integration's act-as user."""
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    from app.services.company_scope_resolver import apply_company_scope

    who = {"id": caller.id, "email": caller.email, "auth_method": "integration_api_key"}
    try:
        yield _client(db, who)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_or_api_key, None)
        app.dependency_overrides.pop(apply_company_scope, None)


@pytest.fixture
def allow(monkeypatch):
    from app.services.user_service import UserPermissionService

    granted: set[str] = set()
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in granted,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    return granted


@pytest.fixture
def as_admin(monkeypatch, caller):
    from app.services.user_service import UserPermissionService

    # Only the role lookup is pinned; `check_user_has_permission` itself is real, so
    # this goes through the same admin bypass production does.
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_role_slugs",
        lambda self, uid: {"admin"} if str(uid) == str(caller.id) else set(),
    )


def _send(client, method, path, body):
    if body is None:
        return getattr(client, method)(path)
    return getattr(client, method)(path, json=body)


# --------------------------------------------------------------------------- state readers


def _saturday(db):
    from app.models.calendar import WorkCalendarConfig

    db.expire_all()
    return db.query(WorkCalendarConfig).filter(WorkCalendarConfig.config_key == "default").one().saturday


def _holiday_dates(db):
    from app.models.calendar import PublicHoliday

    db.expire_all()
    return sorted(h.date for h in db.query(PublicHoliday).all())


def _holiday_name(db, holiday_id):
    from app.models.calendar import PublicHoliday

    db.expire_all()
    row = db.query(PublicHoliday).filter(PublicHoliday.id == holiday_id).first()
    return None if row is None else row.name


def _task_enabled(db, task_id):
    from app.models.scheduled_task import ScheduledTask

    db.expire_all()
    return db.query(ScheduledTask).filter(ScheduledTask.id == task_id).one().enabled


def _policy(db, policy_id):
    from app.models.sla import SLAPolicy

    db.expire_all()
    return db.query(SLAPolicy).filter(SLAPolicy.id == policy_id).first()


def _policy_codes(db):
    from app.models.sla import SLAPolicy

    db.expire_all()
    return sorted(p.code for p in db.query(SLAPolicy).all())


def _tiers(db, policy_id):
    from app.models.sla import SLAPolicyTier

    db.expire_all()
    return sorted(
        (t.tier_level, t.tier_name)
        for t in db.query(SLAPolicyTier).filter(SLAPolicyTier.policy_id == policy_id).all()
    )


# --------------------------------------------------------------------------- the writes
#
# Each case: (id, method, path template, body, slug, state reader, value before, value after).
# The path template is filled from `seeded`.

CALENDAR_WRITES = [
    (
        "work-calendar-config",
        "put",
        f"{SYSTEM}/work-calendar-config",
        {"saturday": True},
        CAL_EDIT,
        lambda db, ids: _saturday(db),
        False,
        True,
    ),
    (
        "public-holiday-create",
        "post",
        f"{SYSTEM}/public-holidays",
        {"date": NEW_HOLIDAY_DATE.isoformat(), "name": f"{TEST_PREFIX} New Holiday"},
        CAL_ADD,
        lambda db, ids: _holiday_dates(db),
        [HOLIDAY_DATE],
        [HOLIDAY_DATE, NEW_HOLIDAY_DATE],
    ),
    (
        "public-holiday-update",
        "put",
        f"{SYSTEM}/public-holidays/{{holiday_id}}",
        {"name": f"{TEST_PREFIX} Renamed"},
        CAL_EDIT,
        lambda db, ids: _holiday_name(db, ids["holiday_id"]),
        HOLIDAY_NAME,
        f"{TEST_PREFIX} Renamed",
    ),
    (
        "public-holiday-delete",
        "delete",
        f"{SYSTEM}/public-holidays/{{holiday_id}}",
        None,
        CAL_DELETE,
        lambda db, ids: _holiday_name(db, ids["holiday_id"]),
        HOLIDAY_NAME,
        None,
    ),
]

SLA_WRITES = [
    (
        "policy-create",
        "post",
        f"{SLA}/",
        {"code": f"{TEST_PREFIX}-RPG-NEW", "name": f"{TEST_PREFIX} RPG New"},
        SLA_ADD,
        lambda db, ids: _policy_codes(db),
        [POLICY_CODE],
        sorted([POLICY_CODE, f"{TEST_PREFIX}-RPG-NEW"]),
    ),
    (
        "policy-update",
        "put",
        f"{SLA}/{{policy_id}}",
        {"name": f"{TEST_PREFIX} RPG Renamed"},
        SLA_EDIT,
        lambda db, ids: _policy(db, ids["policy_id"]).name,
        POLICY_NAME,
        f"{TEST_PREFIX} RPG Renamed",
    ),
    (
        "policy-delete",
        "delete",
        f"{SLA}/{{policy_id}}",
        None,
        SLA_DELETE,
        lambda db, ids: _policy(db, ids["policy_id"]) is not None,
        True,
        False,
    ),
    (
        "tier-create",
        "post",
        f"{SLA}/{{policy_id}}/tiers",
        {"tier_level": 2, "tier_name": f"{TEST_PREFIX} RPG Tier 2", "response_hours": 2},
        SLA_EDIT,
        lambda db, ids: _tiers(db, ids["policy_id"]),
        [(1, TIER_NAME)],
        [(1, TIER_NAME), (2, f"{TEST_PREFIX} RPG Tier 2")],
    ),
    (
        "tier-update",
        "put",
        f"{SLA}/{{policy_id}}/tiers/{{tier_id}}",
        {"tier_name": f"{TEST_PREFIX} RPG Tier Renamed"},
        SLA_EDIT,
        lambda db, ids: _tiers(db, ids["policy_id"]),
        [(1, TIER_NAME)],
        [(1, f"{TEST_PREFIX} RPG Tier Renamed")],
    ),
    (
        "tier-delete",
        "delete",
        f"{SLA}/{{policy_id}}/tiers/{{tier_id}}",
        None,
        SLA_EDIT,
        lambda db, ids: _tiers(db, ids["policy_id"]),
        [(1, TIER_NAME)],
        [],
    ),
]

SLUG_WRITES = CALENDAR_WRITES + SLA_WRITES
SLUG_IDS = [w[0] for w in SLUG_WRITES]
SLUG_PARAMS = "case_id, method, path, body, slug, read, before, after"

# A role that may READ the screen but not write it. The SLA reads are `.view`; the
# calendar's read slug is `.view` too, held (on dev data) by roles that may not edit it.
READ_ONLY = {
    CAL_ADD: {"system.work_calendar.view"},
    CAL_EDIT: {"system.work_calendar.view"},
    CAL_DELETE: {"system.work_calendar.view"},
    SLA_ADD: {SLA_VIEW},
    SLA_EDIT: {SLA_VIEW},
    SLA_DELETE: {SLA_VIEW},
}
READ_ONLY_SLUGS = set().union(*READ_ONLY.values())


def test_every_gate_slug_is_in_the_permission_registry():
    """`allow` stubs the permission check, so a slug misspelt the same way in this file
    and in a route would pass every case below; the registry is what roles are granted."""
    from app.rbac.permission_registry import PERMISSION_REGISTRY

    registered = {p["slug"] for p in PERMISSION_REGISTRY}
    gate_slugs = {w[4] for w in SLUG_WRITES} | READ_ONLY_SLUGS
    assert gate_slugs - registered == set()


@pytest.mark.parametrize(SLUG_PARAMS, SLUG_WRITES, ids=SLUG_IDS)
def test_signed_in_without_the_slug_is_refused_and_nothing_changes(
    client, allow, db, seeded, case_id, method, path, body, slug, read, before, after
):
    assert allow == set(), "a signed-in caller holding no permission at all"
    assert read(db, seeded) == before

    resp = _send(client, method, path.format(**seeded), body)

    assert resp.status_code == 403, resp.text
    assert slug in resp.json()["detail"]
    assert read(db, seeded) == before


@pytest.mark.parametrize(SLUG_PARAMS, SLUG_WRITES, ids=SLUG_IDS)
def test_view_only_holder_is_refused(
    client, allow, db, seeded, case_id, method, path, body, slug, read, before, after
):
    allow.update(READ_ONLY[slug])

    resp = _send(client, method, path.format(**seeded), body)

    assert resp.status_code == 403, resp.text
    assert slug in resp.json()["detail"]
    assert read(db, seeded) == before


@pytest.mark.parametrize(SLUG_PARAMS, SLUG_WRITES, ids=SLUG_IDS)
def test_holder_of_the_slug_succeeds(
    client, allow, db, seeded, case_id, method, path, body, slug, read, before, after
):
    allow.add(slug)

    resp = _send(client, method, path.format(**seeded), body)

    assert resp.status_code in (200, 201, 204), resp.text
    assert read(db, seeded) == after


@pytest.mark.parametrize(SLUG_PARAMS, SLUG_WRITES, ids=SLUG_IDS)
def test_admin_still_succeeds_through_the_real_bypass(
    client, as_admin, db, seeded, case_id, method, path, body, slug, read, before, after
):
    resp = _send(client, method, path.format(**seeded), body)

    assert resp.status_code in (200, 201, 204), resp.text
    assert read(db, seeded) == after


@pytest.mark.parametrize(SLUG_PARAMS, SLA_WRITES, ids=[w[0] for w in SLA_WRITES])
def test_sla_writes_check_the_slug_on_the_api_key_path_too(
    api_key_client, allow, db, seeded, case_id, method, path, body, slug, read, before, after
):
    """The act-as user of an integration key is held to the same slug, and one that holds
    it keeps working (the integration roles hold all three on dev data)."""
    resp = _send(api_key_client, method, path.format(**seeded), body)
    assert resp.status_code == 403, resp.text
    assert read(db, seeded) == before

    allow.add(slug)
    resp = _send(api_key_client, method, path.format(**seeded), body)
    assert resp.status_code in (200, 201, 204), resp.text
    assert read(db, seeded) == after


# --------------------------------------------------------------------------- scheduled tasks


def test_scheduled_task_update_is_refused_to_a_non_admin(client, allow, db, seeded):
    # Holding every slug the calendar and SLA writes take is still not the admin role.
    allow.update({CAL_ADD, CAL_EDIT, CAL_DELETE, SLA_ADD, SLA_EDIT, SLA_DELETE})

    resp = client.patch(f"{SYSTEM}/scheduled-tasks/{seeded['task_id']}", json={"enabled": False})

    assert resp.status_code == 403, resp.text
    assert _task_enabled(db, seeded["task_id"]) is True


def test_scheduled_task_run_now_is_refused_to_a_non_admin(client, allow, db, seeded, run_now_calls):
    resp = client.post(f"{SYSTEM}/scheduled-tasks/{seeded['task_id']}/run-now")

    assert resp.status_code == 403, resp.text
    assert run_now_calls == [], "run-now started a job for a caller who is not an admin"


def test_scheduled_task_update_succeeds_for_admin(client, as_admin, db, seeded):
    resp = client.patch(f"{SYSTEM}/scheduled-tasks/{seeded['task_id']}", json={"enabled": False})

    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False
    assert _task_enabled(db, seeded["task_id"]) is False


def test_scheduled_task_run_now_succeeds_for_admin(client, as_admin, db, seeded, run_now_calls):
    resp = client.post(f"{SYSTEM}/scheduled-tasks/{seeded['task_id']}/run-now")

    assert resp.status_code == 200, resp.text
    assert run_now_calls == [seeded["task_id"]]


def test_calendar_reads_stay_open_to_a_signed_in_caller(client, allow, db, seeded):
    """Only the calendar writes move; the config and the holiday list stay readable."""
    assert allow == set()
    assert client.get(f"{SYSTEM}/work-calendar-config").status_code == 200
    resp = client.get(f"{SYSTEM}/public-holidays")
    assert resp.status_code == 200, resp.text
    assert [h["name"] for h in resp.json()["data"]] == [HOLIDAY_NAME]


def test_scheduled_task_reads_stay_open_to_a_signed_in_caller(client, allow, db, seeded):
    """Only the writes move; the list, the detail and the run log are unchanged."""
    assert client.get(f"{SYSTEM}/scheduled-tasks").status_code == 200
    assert client.get(f"{SYSTEM}/scheduled-tasks/{seeded['task_id']}").status_code == 200
    assert client.get(f"{SYSTEM}/scheduled-tasks/{seeded['task_id']}/runs").status_code == 200
