"""Write-route permission gates on the Teams screen (TEAMS-PERM).

Team create/update/delete and member add/update/remove in
`app/api/v1/user_management/teams.py` were `Depends(get_current_user)` only, so any
signed-in session could change team membership - and membership decides who is
handed work (CS routing candidates and round robin both read `team_members`).

They now take the CRUD slugs `_crud("user_management", "teams", "Teams")` seeds
(app/rbac/permission_registry.py), the same way roles.py gates its writes: create
needs `.add`, update `.edit`, delete `.delete`, and every member write `.edit` (the
slug the member market-segment and brand PUTs on the same router already take).
Reads stay on `.view`, and holding `.view` alone opens no write.

Dependency-override + `allow`-set monkeypatch pattern copied from
tests/test_user_management_read_gates.py.
"""
from __future__ import annotations

import uuid
from typing import Callable

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1.user_management import teams as teams_module
from app.database import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.access import Team, TeamMember
from app.models.base import set_company_scope
from app.models.company import UserCompany
from app.models.user import User, UserStatus
from app.services.company_scope_resolver import apply_company_scope
from tests._pg_fixture import blank_session, unique_code

TEAMS_VIEW = "user_management.teams.view"
TEAMS_ADD = "user_management.teams.add"
TEAMS_EDIT = "user_management.teams.edit"
TEAMS_DELETE = "user_management.teams.delete"

BASE = "/api/v1/user-management/teams"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def api(db, monkeypatch):
    """A caller whose permission set is exactly the returned `allow` set."""
    from app.services.user_service import UserPermissionService

    allow: set[str] = set()
    caller = {"id": str(uuid.uuid4()), "email": "teams-write-caller@zzt.test"}

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
    try:
        yield TestClient(app), allow
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(apply_company_scope, None)


# --------------------------------------------------------------------- seeds


def _seed_user(db) -> str:
    user = User(
        id=unique_code("user"),
        email=f"{unique_code('member')}@zzt.test",
        name="ZZT Member",
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    return str(user.id)


def _seed_team(db, *, member_user_id: str | None = None) -> str:
    team = Team(id=str(uuid.uuid4()), name=unique_code("team"))
    db.add(team)
    db.flush()
    if member_user_id:
        db.add(
            TeamMember(
                id=str(uuid.uuid4()),
                team_id=team.id,
                user_id=member_user_id,
                sort_order=0,
                include_in_round_robin=True,
            )
        )
        db.flush()
    return str(team.id)


def _member(db, team_id: str, user_id: str) -> TeamMember | None:
    db.expire_all()
    return (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        .first()
    )


# --------------------------------------------------------------------- cases
#
# Each case seeds its own rows and returns
# (method, url, json body, slug that must open it, success status, wrote()),
# where wrote() reads the DB back and says whether the write took effect - so a
# 403 is proven to have stopped the write, not just to have been returned.

Case = tuple[str, str, dict | None, str, int, Callable[[], bool]]


def _create_team(db) -> Case:
    name = unique_code("team-new")

    def wrote() -> bool:
        db.expire_all()
        return db.query(Team).filter(Team.name == name).count() == 1

    return "POST", f"{BASE}/", {"name": name}, TEAMS_ADD, 201, wrote


def _update_team(db) -> Case:
    team_id = _seed_team(db)
    new_name = unique_code("team-renamed")

    def wrote() -> bool:
        db.expire_all()
        return db.query(Team.name).filter(Team.id == team_id).scalar() == new_name

    return "PUT", f"{BASE}/{team_id}", {"name": new_name}, TEAMS_EDIT, 200, wrote


def _delete_team(db) -> Case:
    team_id = _seed_team(db)

    def wrote() -> bool:
        db.expire_all()
        return db.query(Team).filter(Team.id == team_id).count() == 0

    return "DELETE", f"{BASE}/{team_id}", None, TEAMS_DELETE, 200, wrote


def _add_member(db) -> Case:
    team_id = _seed_team(db)
    user_id = _seed_user(db)
    # The team is stamped with the default company; a member needs that grant
    # (TeamService._guard_member_company_grant) or the add is a 400, not a 201.
    company_id = db.query(Team.company_id).filter(Team.id == team_id).scalar()
    if company_id:
        db.add(UserCompany(id=str(uuid.uuid4()), user_id=user_id, company_id=str(company_id)))
        db.flush()

    def wrote() -> bool:
        return _member(db, team_id, user_id) is not None

    return "POST", f"{BASE}/{team_id}/members", {"user_id": user_id}, TEAMS_EDIT, 201, wrote


def _update_member(db) -> Case:
    user_id = _seed_user(db)
    team_id = _seed_team(db, member_user_id=user_id)

    def wrote() -> bool:
        m = _member(db, team_id, user_id)
        return m is not None and m.include_in_round_robin is False

    return (
        "PATCH",
        f"{BASE}/{team_id}/members/{user_id}",
        {"include_in_round_robin": False},
        TEAMS_EDIT,
        200,
        wrote,
    )


def _remove_member(db) -> Case:
    user_id = _seed_user(db)
    team_id = _seed_team(db, member_user_id=user_id)

    def wrote() -> bool:
        return _member(db, team_id, user_id) is None

    return "DELETE", f"{BASE}/{team_id}/members/{user_id}", None, TEAMS_EDIT, 200, wrote


CASES = {
    "create team": _create_team,
    "update team": _update_team,
    "delete team": _delete_team,
    "add member": _add_member,
    "update member": _update_member,
    "remove member": _remove_member,
}


def _call(client: TestClient, method: str, url: str, body: dict | None):
    if body is None:
        return client.request(method, url)
    return client.request(method, url, json=body)


@pytest.mark.parametrize("label", list(CASES))
def test_write_is_403_without_any_teams_permission(api, db, label):
    client, allow = api
    method, url, body, slug, _ok, wrote = CASES[label](db)

    resp = _call(client, method, url, body)

    assert resp.status_code == 403, f"{label}: expected 403, got {resp.status_code} ({resp.text})"
    assert slug in resp.json()["detail"], f"{label}: {slug!r} not named in {resp.json()!r}"
    assert not wrote(), f"{label}: the write took effect despite the 403"


@pytest.mark.parametrize("label", list(CASES))
def test_write_is_403_with_teams_view_only(api, db, label):
    """The Teams screen opens on `.view`; that alone must not change anything."""
    client, allow = api
    allow.add(TEAMS_VIEW)
    method, url, body, slug, _ok, wrote = CASES[label](db)

    resp = _call(client, method, url, body)

    assert resp.status_code == 403, f"{label}: expected 403, got {resp.status_code} ({resp.text})"
    assert slug in resp.json()["detail"], f"{label}: {slug!r} not named in {resp.json()!r}"
    assert not wrote(), f"{label}: the write took effect despite the 403"


@pytest.mark.parametrize("label", list(CASES))
def test_write_succeeds_with_its_slug(api, db, label):
    client, allow = api
    method, url, body, slug, ok, wrote = CASES[label](db)
    allow.add(slug)

    resp = _call(client, method, url, body)

    assert resp.status_code == ok, f"{label}: expected {ok}, got {resp.status_code} ({resp.text})"
    assert wrote(), f"{label}: {resp.status_code} returned but the write did not take effect"


# ----------------------------------------------------------- structural sweep


def _gated(route) -> bool:
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
    return any("require_permission" in n for n in names)


def test_every_write_route_on_the_teams_router_is_permission_gated():
    """A write added to teams.py tomorrow without a permission dependency fails here."""
    probe = FastAPI()
    probe.include_router(teams_module.router, prefix="/teams")
    writes = [
        (sorted(r.methods - {"HEAD", "OPTIONS"}), r.path)
        for r in probe.routes
        if getattr(r, "methods", None) and r.methods - {"GET", "HEAD", "OPTIONS"}
    ]
    # 6 screen writes + the member market-segment and brand PUTs.
    assert len(writes) == 8, writes
    ungated = [
        (m, r.path)
        for r in probe.routes
        if getattr(r, "methods", None)
        for m in [sorted(r.methods - {"HEAD", "OPTIONS"})]
        if r.methods - {"GET", "HEAD", "OPTIONS"} and not _gated(r)
    ]
    assert not ungated, f"teams write route(s) with no permission dependency: {ungated}"
