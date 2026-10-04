"""S6: sales teams with dated membership (UAC S6-1, S6-2, S6-3, S6-8, S6-14).

Plan: documentation/plans/sales/PLAN-sales-targets-opportunities-26sep.md, sections 3.7 and
3.8. UAC: sales-targets-opportunities-26sep-acceptance-criteria.md, S6.

Route level on purpose: the permission gate, the module mount at `/sales` and the company
scope are all part of what S6 promises, and a service-only test reaches none of them.

"Today" is pinned to 20 Oct 2026 through `team_service._today`, so the dated-membership golden
set (a move on 15 Oct) reads the same on any day this runs.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.models.company import Company
from app.models.sales_agent import SalesAgent
from app.models.base import company_scope

from ._pg_fixture import blank_session

BASE = "/api/v1/sales/teams"
TODAY = date(2026, 10, 20)

ALL = ["sales.teams.view", "sales.teams.add", "sales.teams.edit", "sales.teams.delete"]


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _agent(db, code: str, name: str, *, company_id=None, active=True) -> SalesAgent:
    row = SalesAgent(
        id=_uid(),
        sales_agent=f"ZZT{code}-{_uid()[:6]}".upper(),
        description=name,
        is_active=active,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


def _client(db, permissions):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    user_id = _uid()
    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    granted = list(permissions)
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug in granted
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(granted)
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@pytest.fixture
def world(monkeypatch):
    from app.services.sales import team_service

    monkeypatch.setattr(team_service, "_today", lambda: TODAY)
    with blank_session() as db:
        company_id = _sorento(db)
        with company_scope(db, frozenset({company_id})):
            yield db, company_id


@pytest.fixture
def api(world):
    db, company_id = world
    client, originals = _client(db, ALL)
    try:
        yield client, db, company_id
        # The leader rule is a DEFERRED constraint trigger, which fires at the real commit and
        # a savepoint release never reaches. Fire it here, so a write that leaves a team's
        # leader outside the team fails the test instead of the owner's next save (W1).
        db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    finally:
        _restore(originals)


def _codes(members):
    return sorted(m["code"] for m in members)


# --------------------------------------------------------------------------- #
# S6-1: create, list, read
# --------------------------------------------------------------------------- #

def test_s6_1_create_team_with_members_and_read_it_back(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")

    res = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id, mei.id]})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "North"
    assert body["is_active"] is True
    assert body["member_count"] == 2
    assert _codes(body["members"]) == sorted([ali.sales_agent, mei.sales_agent])

    detail = client.get(f"{BASE}/{body['id']}").json()
    ali_row = next(m for m in detail["members"] if m["sales_agent_id"] == ali.id)
    # Members come back as agent code and name, never as a bare id to print.
    assert ali_row["code"] == ali.sales_agent
    assert ali_row["name"] == "Ali Hassan"
    assert ali_row["label"] == f"{ali.sales_agent} - Ali Hassan"

    listed = client.get(BASE).json()
    row = next(t for t in listed["data"] if t["id"] == body["id"])
    assert row["member_count"] == 2
    assert sorted(m["label"] for m in row["members"]) == sorted(
        [f"{ali.sales_agent} - Ali Hassan", f"{mei.sales_agent} - Tan Mei Ling"]
    )


@pytest.mark.parametrize("name", ["", "   "])
def test_s6_1_blank_name_is_422(api, name):
    client, _, _ = api
    res = client.post(BASE, json={"name": name, "sales_agent_ids": []})
    assert res.status_code == 422, res.text


def test_s6_1_same_name_case_insensitive_is_409(api):
    client, _, _ = api
    assert client.post(BASE, json={"name": "North", "sales_agent_ids": []}).status_code == 201
    res = client.post(BASE, json={"name": "  nORTH ", "sales_agent_ids": []})
    assert res.status_code == 409, res.text


def test_s6_1_list_search_matches_the_name(api):
    client, _, _ = api
    client.post(BASE, json={"name": "ZZT North", "sales_agent_ids": []})
    client.post(BASE, json={"name": "ZZT South", "sales_agent_ids": []})
    names = [t["name"] for t in client.get(BASE, params={"query": "sou"}).json()["data"]]
    assert names == ["ZZT South"]


def test_s6_1_unknown_agent_is_422(api):
    client, _, _ = api
    res = client.post(BASE, json={"name": "North", "sales_agent_ids": [_uid()]})
    assert res.status_code == 422, res.text


# --------------------------------------------------------------------------- #
# S6-2 and S6-14: members, dated moves
# --------------------------------------------------------------------------- #

def _membership_rows(db, agent_id):
    from app.models.sales import SalesTeamMember

    return (
        db.query(SalesTeamMember)
        .filter(SalesTeamMember.sales_agent_id == agent_id)
        .order_by(SalesTeamMember.created_at, SalesTeamMember.valid_from.nullsfirst())
        .all()
    )


def test_s6_14_first_team_counts_from_the_beginning(api):
    """V1: an agent never in a team gets `valid_from` empty, so earlier orders count too."""
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    team = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id]}).json()

    rows = _membership_rows(db, ali.id)
    assert len(rows) == 1
    assert rows[0].sales_team_id == team["id"]
    assert rows[0].valid_from is None
    assert rows[0].valid_to is None


def test_s6_2_s6_14_move_closes_old_row_and_opens_new_one_and_names_the_old_team(api):
    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]}).json()
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()

    res = client.put(
        f"{BASE}/{south['id']}/members",
        json={"sales_agent_ids": [kim.id], "moves_on": "2026-10-15"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moved"] == [
        {
            "sales_agent_id": kim.id,
            "label": f"{kim.sales_agent} - Kim Tan",
            "from_team_id": north["id"],
            "from_team_name": "North",
        }
    ]

    rows = {r.sales_team_id: r for r in _membership_rows(db, kim.id)}
    assert rows[north["id"]].valid_from is None
    assert rows[north["id"]].valid_to == date(2026, 10, 14)
    assert rows[south["id"]].valid_from == date(2026, 10, 15)
    assert rows[south["id"]].valid_to is None


def test_s6_2_moves_on_defaults_to_today(api):
    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]}).json()
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": [kim.id]}).json()

    assert south["moved"][0]["from_team_name"] == "North"
    rows = {r.sales_team_id: r for r in _membership_rows(db, kim.id)}
    assert rows[north["id"]].valid_to == date(2026, 10, 19)
    assert rows[south["id"]].valid_from == TODAY


def test_s6_14_future_moves_on_is_422(api):
    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]})
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()

    res = client.put(
        f"{BASE}/{south['id']}/members",
        json={"sales_agent_ids": [kim.id], "moves_on": "2026-10-21"},
    )
    assert res.status_code == 422, res.text


def test_s6_14_left_out_agent_is_closed_today_and_the_row_stays(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id, mei.id]}).json()

    res = client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": [ali.id]})
    assert res.status_code == 200, res.text
    assert res.json()["moved"] == []

    rows = _membership_rows(db, mei.id)
    assert len(rows) == 1
    assert rows[0].valid_to == TODAY


def test_s6_2_an_agent_is_never_in_two_teams_on_the_same_day(api):
    """The open-membership index: two open rows for one agent in one company cannot exist."""
    from sqlalchemy.exc import IntegrityError

    from app.models.sales import SalesTeam, SalesTeamMember

    client, db, company_id = api
    kim = _agent(db, "KIM", "Kim Tan")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]}).json()
    south = SalesTeam(id=_uid(), company_id=company_id, name="ZZT South direct")
    db.add(south)
    db.flush()

    nested = db.begin_nested()
    db.add(
        SalesTeamMember(
            id=_uid(), company_id=company_id, sales_team_id=south.id, sales_agent_id=kim.id
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    nested.rollback()
    assert north["id"]


def test_s6_14_overlapping_move_is_422(api):
    """Moving again with a date before the current team's start would overlap the two."""
    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]})
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()
    west = client.post(BASE, json={"name": "West", "sales_agent_ids": []}).json()
    client.put(
        f"{BASE}/{south['id']}/members",
        json={"sales_agent_ids": [kim.id], "moves_on": "2026-10-15"},
    )

    res = client.put(
        f"{BASE}/{west['id']}/members",
        json={"sales_agent_ids": [kim.id], "moves_on": "2026-10-10"},
    )
    assert res.status_code == 422, res.text


def test_review_s1_moves_on_the_day_the_current_row_began_suggests_a_date_that_works(api):
    """Kim joined South on 15 Oct; a move to West on 15 Oct would leave South no day at all.

    The 422 says "on or after" and names 16 Oct, and retrying with that date is accepted.
    """
    import re

    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]})
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()
    west = client.post(BASE, json={"name": "West", "sales_agent_ids": []}).json()
    client.put(
        f"{BASE}/{south['id']}/members",
        json={"sales_agent_ids": [kim.id], "moves_on": "2026-10-15"},
    )

    res = client.put(
        f"{BASE}/{west['id']}/members",
        json={"sales_agent_ids": [kim.id], "moves_on": "2026-10-15"},
    )
    assert res.status_code == 422, res.text
    assert "on or after the Moves on date" in res.text
    suggested = re.search(r"pick (\d{4}-\d{2}-\d{2}) or later", res.text)
    assert suggested is not None, res.text
    assert suggested.group(1) == "2026-10-16"

    retry = client.put(
        f"{BASE}/{west['id']}/members",
        json={"sales_agent_ids": [kim.id], "moves_on": suggested.group(1)},
    )
    assert retry.status_code == 200, retry.text
    rows = {r.sales_team_id: r for r in _membership_rows(db, kim.id)}
    assert rows[south["id"]].valid_from == date(2026, 10, 15)
    assert rows[south["id"]].valid_to == date(2026, 10, 15)
    assert rows[west["id"]].valid_from == date(2026, 10, 16)


def test_s6_14_readding_the_same_day_reopens_the_row(api):
    """Removed by mistake and put straight back: one continuous membership, not a gap."""
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id]}).json()
    client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": []})
    client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": [ali.id]})

    rows = _membership_rows(db, ali.id)
    assert len(rows) == 1
    assert rows[0].valid_from is None
    assert rows[0].valid_to is None


def test_s6_14_golden_set_membership_on_the_date(api):
    """A in N, moved to S on 15 Oct: 10 Oct is N's, 15 Oct and later are S's.

    Read through the team page's `on` date, which is the same rule S1's team figure uses.
    """
    client, db, _ = api
    a = _agent(db, "A", "Agent A")
    b = _agent(db, "B", "Agent B")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [a.id, b.id]}).json()
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()
    client.put(
        f"{BASE}/{south['id']}/members",
        json={"sales_agent_ids": [a.id], "moves_on": "2026-10-15"},
    )

    def active(team_id, on):
        members = client.get(f"{BASE}/{team_id}", params={"on": on}).json()["members"]
        return {m["sales_agent_id"] for m in members if not m["left"]}

    assert active(north["id"], "2026-10-10") == {a.id, b.id}
    assert active(south["id"], "2026-10-10") == set()
    assert active(north["id"], "2026-10-15") == {b.id}
    assert active(south["id"], "2026-10-15") == {a.id}
    assert active(south["id"], "2026-10-20") == {a.id}
    # Long before anyone was placed: the first team counts from the beginning.
    assert active(north["id"], "2020-01-01") == {a.id, b.id}


def test_s6_15_left_member_shows_with_the_last_day_this_month(api):
    client, db, _ = api
    a = _agent(db, "A", "Agent A")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [a.id]}).json()
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()
    client.put(
        f"{BASE}/{south['id']}/members",
        json={"sales_agent_ids": [a.id], "moves_on": "2026-10-15"},
    )

    detail = client.get(f"{BASE}/{north['id']}").json()
    assert detail["member_count"] == 0
    left = [m for m in detail["members"] if m["left"]]
    assert [(m["sales_agent_id"], m["valid_to"]) for m in left] == [(a.id, "2026-10-14")]

    # Next month the line has gone: it explains THIS period's figure, not every one after.
    later = client.get(f"{BASE}/{north['id']}", params={"on": "2026-11-20"}).json()
    assert later["members"] == []


# --------------------------------------------------------------------------- #
# S6-3: rename, activate, delete
# --------------------------------------------------------------------------- #

def test_s6_3_patch_renames_and_deactivates(api):
    client, _, _ = api
    team = client.post(BASE, json={"name": "North", "sales_agent_ids": []}).json()

    res = client.patch(f"{BASE}/{team['id']}", json={"name": "North East", "is_active": False})
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "North East"
    assert res.json()["is_active"] is False


def test_s6_3_patch_rename_onto_another_team_is_409(api):
    client, _, _ = api
    client.post(BASE, json={"name": "North", "sales_agent_ids": []})
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()
    res = client.patch(f"{BASE}/{south['id']}", json={"name": "north"})
    assert res.status_code == 409, res.text


def test_s6_3_delete_removes_team_and_memberships_but_not_agents(api):
    from app.models.sales import SalesTeam, SalesTeamMember

    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    team = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id]}).json()

    res = client.delete(f"{BASE}/{team['id']}")
    assert res.status_code == 200, res.text
    db.expire_all()
    assert db.query(SalesTeam).filter(SalesTeam.id == team["id"]).count() == 0
    assert db.query(SalesTeamMember).filter(SalesTeamMember.sales_agent_id == ali.id).count() == 0
    assert db.query(SalesAgent).filter(SalesAgent.id == ali.id).count() == 1
    assert client.get(f"{BASE}/{team['id']}").status_code == 404


def test_s6_3_deferred_delete_is_registered_behind_the_delete_slug(world):
    """Delete is a parked action (D7): the record action runs the same service delete."""
    import app.services.record_actions  # noqa: F401
    from app.models.sales import SalesTeam
    from app.services.form_action_registry import get_action
    from app.services.sales import team_service

    db, company_id = world
    action = get_action("sales_team.delete")
    assert action is not None
    assert action.permission == "sales.teams.delete"
    assert action.entity_types == ("sales_team",)

    team = team_service.create_team(db, company_id=company_id, name="ZZT Doomed", sales_agent_ids=[])
    db.flush()
    action.execute(db, {"entity_id": team.id})
    db.flush()
    assert db.query(SalesTeam).filter(SalesTeam.id == team.id).count() == 0


# --------------------------------------------------------------------------- #
# agent options for the team modal's multi-select
# --------------------------------------------------------------------------- #

def test_agent_options_label_each_active_agent_with_their_team_now(api):
    client, db, _ = api
    sean = _agent(db, "SEAN", "CONTACT AE")
    raj = _agent(db, "RAJ", "Raj Kumar")
    gone = _agent(db, "OLD", "Retired", active=False)
    central = client.post(BASE, json={"name": "Central", "sales_agent_ids": [sean.id]}).json()

    data = client.get(f"{BASE}/agent-options").json()["data"]
    by_id = {o["id"]: o for o in data}
    assert by_id[sean.id]["team_id"] == central["id"]
    assert by_id[sean.id]["team_name"] == "Central"
    assert by_id[sean.id]["label"] == f"{sean.sales_agent} - CONTACT AE"
    assert by_id[raj.id]["team_id"] is None
    assert gone.id not in by_id


# --------------------------------------------------------------------------- #
# S6-8: permissions and company scope
# --------------------------------------------------------------------------- #

def _routes(team_id):
    return [
        ("get", BASE, None, "sales.teams.view"),
        ("get", f"{BASE}/agent-options", None, "sales.teams.view"),
        ("get", f"{BASE}/{team_id}", None, "sales.teams.view"),
        ("post", BASE, {"name": "ZZT Denied", "sales_agent_ids": []}, "sales.teams.add"),
        ("patch", f"{BASE}/{team_id}", {"name": "ZZT Renamed"}, "sales.teams.edit"),
        ("put", f"{BASE}/{team_id}/members", {"sales_agent_ids": []}, "sales.teams.edit"),
        ("delete", f"{BASE}/{team_id}", None, "sales.teams.delete"),
    ]


def test_s6_8_each_route_is_403_without_its_slug(world):
    from app.services.sales import team_service

    db, company_id = world
    team = team_service.create_team(db, company_id=company_id, name="ZZT Gate", sales_agent_ids=[])
    db.flush()

    for method, url, body, slug in _routes(team.id):
        others = [s for s in ALL if s != slug]
        client, originals = _client(db, others)
        try:
            kwargs = {"json": body} if body is not None else {}
            res = getattr(client, method)(url, **kwargs)
        finally:
            _restore(originals)
        assert res.status_code == 403, f"{method.upper()} {url} without {slug}: {res.status_code}"


def test_s6_8_another_companys_team_is_invisible(api):
    from app.models.sales import SalesTeam

    client, db, _ = api
    other = Company(id=_uid(), name="ZZT Other Co", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    with company_scope(db, None):
        foreign = SalesTeam(id=_uid(), company_id=other.id, name="ZZT Foreign")
        db.add(foreign)
        db.flush()

    listed = [t["id"] for t in client.get(BASE).json()["data"]]
    assert foreign.id not in listed
    assert client.get(f"{BASE}/{foreign.id}").status_code == 404
    assert client.delete(f"{BASE}/{foreign.id}").status_code == 404


# --------------------------------------------------------------------------- #
# Review round 1 (reviewer B1, N1, N2; security-reviewer test gaps)
# --------------------------------------------------------------------------- #

def test_review_b1_remove_then_place_elsewhere_the_same_day_is_a_move_today(api):
    """Removed from North today, placed in South today, removed from South today: no 500.

    Placing someone the same day they were removed counts as a move today, so North keeps
    everything up to yesterday and no row ever starts in the future.
    """
    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]}).json()
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()

    assert client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": []}).status_code == 200
    res = client.put(f"{BASE}/{south['id']}/members", json={"sales_agent_ids": [kim.id]})
    assert res.status_code == 200, res.text
    assert [m["sales_agent_id"] for m in res.json()["members"] if not m["left"]] == [kim.id]
    assert res.json()["moved"][0]["from_team_name"] == "North"

    rows = {r.sales_team_id: r for r in _membership_rows(db, kim.id)}
    assert rows[north["id"]].valid_to == date(2026, 10, 19)
    assert rows[south["id"]].valid_from == TODAY
    assert all(r.valid_from is None or r.valid_from <= TODAY for r in rows.values())

    res = client.put(f"{BASE}/{south['id']}/members", json={"sales_agent_ids": []})
    assert res.status_code == 200, res.text


def test_review_n2_a_mistaken_move_is_undone_the_same_day(api):
    """North to South today, then back to North today: one North row, reopened, as before."""
    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [kim.id]}).json()
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()
    client.put(f"{BASE}/{south['id']}/members", json={"sales_agent_ids": [kim.id]})

    res = client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": [kim.id]})
    assert res.status_code == 200, res.text
    rows = _membership_rows(db, kim.id)
    assert len(rows) == 1
    assert rows[0].sales_team_id == north["id"]
    assert rows[0].valid_from is None
    assert rows[0].valid_to is None


def test_review_n1_patch_saves_name_and_members_in_one_transaction(api):
    """The team page saves once: a refused rename leaves the members untouched too."""
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")
    client.post(BASE, json={"name": "South", "sales_agent_ids": []})
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id]}).json()

    res = client.patch(
        f"{BASE}/{north['id']}", json={"name": "south", "sales_agent_ids": [mei.id]}
    )
    assert res.status_code == 409, res.text
    db.expire_all()
    detail = client.get(f"{BASE}/{north['id']}").json()
    assert detail["name"] == "North"
    assert [m["sales_agent_id"] for m in detail["members"] if not m["left"]] == [ali.id]

    res = client.patch(
        f"{BASE}/{north['id']}", json={"name": "North East", "sales_agent_ids": [mei.id]}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "North East"
    assert [m["sales_agent_id"] for m in body["members"] if not m["left"]] == [mei.id]


def test_security_foreign_team_writes_are_404_and_the_parked_delete_misses(api):
    import app.services.record_actions  # noqa: F401
    from app.models.sales import SalesTeam
    from app.services.form_action_registry import get_action

    client, db, company_id = api
    other = Company(id=_uid(), name="ZZT Other Co 2", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    with company_scope(db, None):
        foreign = SalesTeam(id=_uid(), company_id=other.id, name="ZZT Foreign 2")
        db.add(foreign)
        # Committed (onto the fixture's savepoint): a 404 route rolls the session back, and
        # a merely flushed row would vanish with it and read as "deleted".
        db.commit()

    assert client.patch(f"{BASE}/{foreign.id}", json={"name": "Mine now"}).status_code == 404
    assert (
        client.put(f"{BASE}/{foreign.id}/members", json={"sales_agent_ids": []}).status_code
        == 404
    )
    with company_scope(db, frozenset({company_id})):
        get_action("sales_team.delete").execute(db, {"entity_id": foreign.id})
        db.flush()
    with company_scope(db, None):
        assert db.query(SalesTeam).filter(SalesTeam.id == foreign.id).count() == 1


def test_security_another_companys_agent_cannot_be_placed_or_listed(api):
    client, db, _ = api
    other = Company(id=_uid(), name="ZZT Other Co 3", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    theirs = _agent(db, "THEIRS", "Not Ours", company_id=other.id)

    assert (
        client.post(BASE, json={"name": "North", "sales_agent_ids": [theirs.id]}).status_code
        == 422
    )
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": []}).json()
    assert (
        client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": [theirs.id]}).status_code
        == 422
    )
    ids = {o["id"] for o in client.get(f"{BASE}/agent-options").json()["data"]}
    assert theirs.id not in ids


# --------------------------------------------------------------------------- #
# Fix lane round 2: team leader (W1), returning agent listed once (W2)
# Owner ruling 26 Sep ~13:25Z: "I need it to be able to set a sales leader, which is also a
# sales agent". Owner ruling 26 Sep ~13:05Z on N1: "hmm shouldn't list twice la".
# --------------------------------------------------------------------------- #

def _leader_rule_fires(db) -> None:
    """Fire the deferred leader trigger now, inside a savepoint the caller can roll back."""
    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    db.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def test_w1_create_team_with_a_leader_names_the_leader_on_the_page_and_the_list(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")

    res = client.post(
        BASE,
        json={"name": "North", "sales_agent_ids": [ali.id, mei.id], "leader_sales_agent_id": mei.id},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["leader_sales_agent_id"] == mei.id
    assert body["leader_label"] == f"{mei.sales_agent} - Tan Mei Ling"

    row = next(t for t in client.get(BASE).json()["data"] if t["id"] == body["id"])
    assert row["leader_sales_agent_id"] == mei.id
    assert client.get(f"{BASE}/{body['id']}").json()["leader_sales_agent_id"] == mei.id


def test_w1_a_team_has_no_leader_until_one_is_picked(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    body = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id]}).json()
    assert body["leader_sales_agent_id"] is None
    assert body["leader_label"] is None


def test_w1_a_leader_who_is_not_picked_is_added_as_a_member(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")

    body = client.post(
        BASE, json={"name": "North", "sales_agent_ids": [ali.id], "leader_sales_agent_id": mei.id}
    ).json()
    assert {m["sales_agent_id"] for m in body["members"] if not m["left"]} == {ali.id, mei.id}
    assert body["leader_sales_agent_id"] == mei.id
    # First team: from the beginning, like any agent Add agents places (V1).
    assert _membership_rows(db, mei.id)[0].valid_from is None


def test_w1_a_leader_from_another_team_moves_on_the_moves_on_date(api):
    client, db, _ = api
    kim = _agent(db, "KIM", "Kim Tan")
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": [kim.id]}).json()
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": []}).json()

    res = client.patch(
        f"{BASE}/{north['id']}",
        json={"leader_sales_agent_id": kim.id, "moves_on": "2026-10-15"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["leader_sales_agent_id"] == kim.id
    assert [m["from_team_name"] for m in body["moved"]] == ["South"]
    rows = _membership_rows(db, kim.id)
    assert [(r.sales_team_id, r.valid_from, r.valid_to) for r in rows] == [
        (south["id"], None, date(2026, 10, 14)),
        (north["id"], date(2026, 10, 15), None),
    ]


def test_w1_patch_changes_and_clears_the_leader_and_leaves_it_when_not_sent(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")
    north = client.post(
        BASE,
        json={"name": "North", "sales_agent_ids": [ali.id, mei.id], "leader_sales_agent_id": ali.id},
    ).json()

    kept = client.patch(f"{BASE}/{north['id']}", json={"name": "North East"}).json()
    assert kept["leader_sales_agent_id"] == ali.id

    changed = client.patch(f"{BASE}/{north['id']}", json={"leader_sales_agent_id": mei.id}).json()
    assert changed["leader_sales_agent_id"] == mei.id
    # Changing the leader moves nobody: both are still members.
    assert {m["sales_agent_id"] for m in changed["members"] if not m["left"]} == {ali.id, mei.id}

    cleared = client.patch(f"{BASE}/{north['id']}", json={"leader_sales_agent_id": None}).json()
    assert cleared["leader_sales_agent_id"] is None
    assert cleared["member_count"] == 2


def test_w1_removing_the_leader_from_the_team_clears_the_leader(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")
    north = client.post(
        BASE,
        json={"name": "North", "sales_agent_ids": [ali.id, mei.id], "leader_sales_agent_id": ali.id},
    ).json()

    body = client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": [mei.id]}).json()
    assert body["leader_sales_agent_id"] is None
    _leader_rule_fires(db)


def test_w1_the_leader_moving_to_another_team_clears_the_old_teams_leader(api):
    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    north = client.post(
        BASE, json={"name": "North", "sales_agent_ids": [ali.id], "leader_sales_agent_id": ali.id}
    ).json()
    south = client.post(BASE, json={"name": "South", "sales_agent_ids": []}).json()

    res = client.put(
        f"{BASE}/{south['id']}/members",
        json={"sales_agent_ids": [ali.id], "moves_on": "2026-10-15"},
    )
    assert res.status_code == 200, res.text
    db.expire_all()
    assert client.get(f"{BASE}/{north['id']}").json()["leader_sales_agent_id"] is None
    _leader_rule_fires(db)


def test_w1_the_leader_must_be_one_of_our_agents(api):
    client, db, _ = api
    other = Company(id=_uid(), name="ZZT Other Co 4", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    theirs = _agent(db, "THEIRS", "Not Ours", company_id=other.id)
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": []}).json()

    res = client.patch(f"{BASE}/{north['id']}", json={"leader_sales_agent_id": theirs.id})
    assert res.status_code == 422, res.text
    res = client.post(
        BASE, json={"name": "South", "sales_agent_ids": [], "leader_sales_agent_id": theirs.id}
    )
    assert res.status_code == 422, res.text


def test_w1_setting_the_leader_needs_the_edit_slug(world):
    from app.services.sales import team_service

    db, company_id = world
    ali = _agent(db, "ALI", "Ali Hassan")
    team = team_service.create_team(
        db, company_id=company_id, name="ZZT Lead Gate", sales_agent_ids=[ali.id]
    )
    db.flush()
    client, originals = _client(db, [s for s in ALL if s != "sales.teams.edit"])
    try:
        res = client.patch(f"{BASE}/{team.id}", json={"leader_sales_agent_id": ali.id})
    finally:
        _restore(originals)
    assert res.status_code == 403


def test_w1_the_database_refuses_a_leader_who_is_not_an_open_member(world):
    """`trg_sales_teams_leader_is_member`: the rule holds for any writer, not just the service."""
    from sqlalchemy.exc import IntegrityError

    from app.services.sales import team_service

    from app.models.sales import translated_schema

    db, company_id = world
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")
    team = team_service.create_team(
        db, company_id=company_id, name="ZZT Lead Rule", sales_agent_ids=[ali.id]
    )
    db.flush()
    # Raw SQL is not translated: name the test's scratch copy of the `sales` schema.
    sales = f'"{translated_schema(db.connection())}"'

    # A leader who was never a member.
    with pytest.raises(IntegrityError, match="leader"):
        with db.begin_nested():
            db.execute(
                text(f"UPDATE {sales}.teams SET leader_sales_agent_id = :a WHERE id = :t"),
                {"a": mei.id, "t": team.id},
            )
            _leader_rule_fires(db)

    # A leader whose membership is then closed behind the service's back.
    db.execute(
        text(f"UPDATE {sales}.teams SET leader_sales_agent_id = :a WHERE id = :t"),
        {"a": ali.id, "t": team.id},
    )
    _leader_rule_fires(db)
    with pytest.raises(IntegrityError, match="leader"):
        with db.begin_nested():
            db.execute(
                text(
                    f"UPDATE {sales}.team_members SET valid_to = current_date "
                    "WHERE sales_team_id = :t AND sales_agent_id = :a"
                ),
                {"a": ali.id, "t": team.id},
            )
            _leader_rule_fires(db)


def test_w2_an_agent_who_left_and_came_back_is_listed_once_as_active(api, monkeypatch):
    """N1 ruling: one line per agent; the earlier stint stays in the data, not on the page."""
    from app.services.sales import team_service

    client, db, _ = api
    ali = _agent(db, "ALI", "Ali Hassan")
    mei = _agent(db, "MEI", "Tan Mei Ling")
    north = client.post(BASE, json={"name": "North", "sales_agent_ids": [ali.id, mei.id]}).json()

    monkeypatch.setattr(team_service, "_today", lambda: date(2026, 10, 5))
    client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": [mei.id]})
    monkeypatch.setattr(team_service, "_today", lambda: TODAY)
    client.put(f"{BASE}/{north['id']}/members", json={"sales_agent_ids": [mei.id, ali.id]})

    # History keeps both stints.
    rows = _membership_rows(db, ali.id)
    assert [(r.valid_from, r.valid_to) for r in rows] == [
        (None, date(2026, 10, 5)),
        (TODAY, None),
    ]

    detail = client.get(f"{BASE}/{north['id']}").json()
    alis = [m for m in detail["members"] if m["sales_agent_id"] == ali.id]
    assert len(alis) == 1, alis
    assert alis[0]["left"] is False
    assert alis[0]["valid_from"] == TODAY.isoformat()
    assert detail["member_count"] == 2

    # Between the stints the page shows the one that had ended, still once.
    gap = client.get(f"{BASE}/{north['id']}", params={"on": "2026-10-10"}).json()
    alis = [m for m in gap["members"] if m["sales_agent_id"] == ali.id]
    assert [(m["left"], m["valid_to"]) for m in alis] == [(True, "2026-10-05")]
