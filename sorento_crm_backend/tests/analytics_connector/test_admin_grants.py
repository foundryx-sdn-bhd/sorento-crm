"""CLAUDE-CONNECTOR S3: the admin view of grants (card sections 3 step 7 and 6).

Contract pinned here (the frontend `claudeConnectorService.ts` consumes it):

    GET  /api/v1/analytics-connector/grants          DataGrid list, `analytics.connector.manage`
         query: page, limit, sort (user_name|created_at|last_used_at|calls_30d), dir, query, status
         body : {"data": [{id, user_id, user_name, created_at, last_used_at, calls_30d, status}],
                 "pagination": {total, page, limit}}      status: Active | Revoked | Expired
    GET  /api/v1/analytics-connector/grants/{id}     header fields + recent tool calls
         body : {id, user_id, user_name, client_name, created_at, last_used_at, expires_at, status,
                 recent_calls: [{time, tool, rows, ms, status}]}   newest first, [] when none
    Revoke = the existing deferred pending action (reversible window, 5 s):
         POST /api/v1/pending-actions {action_key: "analytics_connector_grant.revoke",
                                       entity_type: "analytics_connector_grant", entity_id: <grant id>}
         gated by `analytics.connector.manage`. On commit: grant.revoked_at set, the grant's
         current session revoked (connector /me -> 401), refresh -> invalid_grant, and an audit EVENT
         `analytics_connector.revoked` is written.

Windows are lapsed by moving `commit_at` into the past and reading `/pending-actions/current`
(the lazy commit), never by waiting.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.analytics_connector import AnalyticsConnectorGrant
from app.models.audit import AuditLog
from app.models.sla import SlaFormAction
from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import (
    BASE,
    INTERNAL,
    MANAGE,
    RESOURCE,
    USE,
    client_for,
    connector_headers,
    full_sign_in,
    make_user,
    now,
    staff_headers,
)

GRANTS = f"{BASE}/grants"
PENDING = "/api/v1/pending-actions"
ACTION = "analytics_connector_grant.revoke"
ENTITY = "analytics_connector_grant"


@pytest.fixture()
def world(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            admin = make_user(db, "adm", perms=(MANAGE, USE))
            member = make_user(db, "mem", perms=(USE,))
            yield db, client, admin, member


def _grant_for(db, user) -> AnalyticsConnectorGrant:
    return db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.user_id == user.id).one()


def _tool_call(db, grant, tool="chat_query", rows=3, ms=40, status="ok", when=None):
    """One tool-call audit row. audit_logs is append-only, so the time is set on INSERT."""
    db.add(
        AuditLog(
            entity_type=ENTITY,
            entity_id=str(grant.id),
            action="EVENT",
            event="analytics_connector.tool_call",
            changed_at=when or now(),
            new_values={"tool": tool, "args": {}, "status": status, "ms": ms, "rows": rows, "chars": 10},
        )
    )
    db.commit()


def _lapse(db, action_id: str) -> None:
    db.query(SlaFormAction).filter(SlaFormAction.id == action_id).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    db.commit()


def _park(client, db, admin, grant):
    resp = client.post(
        PENDING,
        json={"action_key": ACTION, "entity_type": ENTITY, "entity_id": str(grant.id), "payload": {}},
        headers=staff_headers(db, admin),
    )
    return resp


# ------------------------------------------------------------------ list


def test_list_requires_the_manage_permission(world):
    db, client, admin, member = world
    full_sign_in(client, db, member)
    assert client.get(GRANTS, headers=staff_headers(db, member)).status_code == 403
    assert client.get(GRANTS).status_code == 401


def test_a_connector_session_cannot_reach_the_admin_list_even_with_manage(world):
    db, client, admin, _member = world
    tok = full_sign_in(client, db, admin)
    resp = client.get(GRANTS, headers=connector_headers(tok["access_token"]))
    assert resp.status_code == 403


def test_list_rows_carry_user_dates_calls_and_status(world):
    db, client, admin, member = world
    full_sign_in(client, db, member)
    grant = _grant_for(db, member)
    grant.last_used_at = now()
    db.commit()
    _tool_call(db, grant)
    _tool_call(db, grant)
    _tool_call(db, grant, when=now() - timedelta(days=45))  # outside the 30 day count

    resp = client.get(GRANTS, params={"page": 1, "limit": 25}, headers=staff_headers(db, admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"] == {"total": 1, "page": 1, "limit": 25}
    row = body["data"][0]
    assert row["id"] == str(grant.id)
    assert row["user_name"] == member.name
    assert row["created_at"] and row["last_used_at"]
    assert row["calls_30d"] == 2
    assert row["status"] == "Active"


def test_list_status_revoked_and_expired(world):
    db, client, admin, member = world
    other = make_user(db, "oth", perms=(USE,))
    full_sign_in(client, db, member)
    full_sign_in(client, db, other)
    _grant_for(db, member).revoked_at = now()
    _grant_for(db, other).expires_at = now() - timedelta(days=1)
    db.commit()

    rows = client.get(GRANTS, headers=staff_headers(db, admin)).json()["data"]
    by_user = {r["user_name"]: r["status"] for r in rows}
    assert by_user == {member.name: "Revoked", other.name: "Expired"}

    only = client.get(GRANTS, params={"status": "Revoked"}, headers=staff_headers(db, admin)).json()
    assert [r["user_name"] for r in only["data"]] == [member.name]
    assert only["pagination"]["total"] == 1


def test_list_paginates_sorts_and_searches_by_user(world):
    db, client, admin, member = world
    people = [make_user(db, f"p{i}", perms=(USE,)) for i in range(3)]
    for p in people:
        full_sign_in(client, db, p)
    for i, p in enumerate(people):
        _grant_for(db, p).created_at = now() - timedelta(days=i)  # p0 newest
    db.commit()
    h = staff_headers(db, admin)

    page1 = client.get(GRANTS, params={"page": 1, "limit": 2, "sort": "created_at", "dir": "asc"}, headers=h).json()
    assert page1["pagination"] == {"total": 3, "page": 1, "limit": 2}
    assert [r["user_name"] for r in page1["data"]] == [people[2].name, people[1].name]
    page2 = client.get(GRANTS, params={"page": 2, "limit": 2, "sort": "created_at", "dir": "asc"}, headers=h).json()
    assert [r["user_name"] for r in page2["data"]] == [people[0].name]

    hit = client.get(GRANTS, params={"query": people[1].name}, headers=h).json()
    assert [r["user_name"] for r in hit["data"]] == [people[1].name]


def test_list_bad_limit_is_a_422(world):
    db, client, admin, _m = world
    resp = client.get(GRANTS, params={"limit": 0}, headers=staff_headers(db, admin))
    assert resp.status_code == 422


# ------------------------------------------------------------------ detail


def test_detail_requires_manage_and_404s_unknown(world):
    db, client, admin, member = world
    full_sign_in(client, db, member)
    grant = _grant_for(db, member)
    assert client.get(f"{GRANTS}/{grant.id}", headers=staff_headers(db, member)).status_code == 403
    assert client.get(f"{GRANTS}/does-not-exist", headers=staff_headers(db, admin)).status_code == 404


def test_detail_header_and_recent_calls_newest_first(world):
    db, client, admin, member = world
    full_sign_in(client, db, member)
    grant = _grant_for(db, member)
    _tool_call(db, grant, tool="chat_list_datasets", rows=6, ms=38, when=now() - timedelta(minutes=5))
    _tool_call(db, grant, tool="crm_low_stock_report", rows=0, ms=91, status="denied")

    resp = client.get(f"{GRANTS}/{grant.id}", headers=staff_headers(db, admin))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(grant.id)
    assert body["user_name"] == member.name
    assert body["client_name"] == "Claude"
    assert body["created_at"] and body["expires_at"]
    assert body["status"] == "Active"
    calls = body["recent_calls"]
    assert [c["tool"] for c in calls] == ["crm_low_stock_report", "chat_list_datasets"]
    assert set(calls[0]) >= {"time", "tool", "rows", "ms", "status"}
    assert calls[0]["status"] == "denied" and calls[0]["rows"] == 0 and calls[0]["ms"] == 91


def test_detail_with_no_calls_has_an_empty_list(world):
    db, client, admin, member = world
    full_sign_in(client, db, member)
    grant = _grant_for(db, member)
    body = client.get(f"{GRANTS}/{grant.id}", headers=staff_headers(db, admin)).json()
    assert body["recent_calls"] == []
    assert body["last_used_at"] is None


# ------------------------------------------------------------------ revoke (deferred)


def test_parking_a_revoke_needs_the_manage_permission(world):
    db, client, _admin, member = world
    full_sign_in(client, db, member)
    resp = _park(client, db, member, _grant_for(db, member))
    assert resp.status_code == 403


def test_parking_applies_nothing_and_uses_the_reversible_window(world):
    db, client, admin, member = world
    tok = full_sign_in(client, db, member)
    grant = _grant_for(db, member)
    resp = _park(client, db, admin, grant)
    assert resp.status_code == 202, resp.text
    assert resp.json()["window_seconds"] == 5
    db.refresh(grant)
    assert grant.revoked_at is None
    assert client.get(f"{BASE}/me", headers=connector_headers(tok["access_token"])).status_code == 200


def test_cancel_inside_the_window_keeps_the_grant_active(world):
    db, client, admin, member = world
    tok = full_sign_in(client, db, member)
    grant = _grant_for(db, member)
    parked = _park(client, db, admin, grant).json()
    cancel = client.post(f"{PENDING}/{parked['id']}/cancel", headers=staff_headers(db, admin))
    assert cancel.status_code == 200, cancel.text
    _lapse(db, parked["id"])
    client.get(f"{PENDING}/current", params={"entity_type": ENTITY, "entity_id": str(grant.id)},
               headers=staff_headers(db, admin))
    db.refresh(grant)
    assert grant.revoked_at is None
    assert client.get(f"{BASE}/me", headers=connector_headers(tok["access_token"])).status_code == 200


def test_committed_revoke_kills_the_session_the_refresh_and_audits(world):
    db, client, admin, member = world
    tok = full_sign_in(client, db, member)
    grant = _grant_for(db, member)
    parked = _park(client, db, admin, grant).json()
    _lapse(db, parked["id"])

    cur = client.get(f"{PENDING}/current", params={"entity_type": ENTITY, "entity_id": str(grant.id)},
                     headers=staff_headers(db, admin))
    assert cur.json()["last_outcome"]["status"] == "committed", cur.text

    db.refresh(grant)
    assert grant.revoked_at is not None
    assert client.get(f"{BASE}/me", headers=connector_headers(tok["access_token"])).status_code == 401
    refresh = client.post(
        f"{BASE}/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
              "client_id": tok["client_id"], "resource": RESOURCE},
        headers=INTERNAL,
    )
    assert refresh.status_code == 400 and refresh.json()["error"] == "invalid_grant"

    event = db.query(AuditLog).filter(
        AuditLog.event == "analytics_connector.revoked", AuditLog.entity_id == str(grant.id)
    ).first()
    assert event is not None

    detail = client.get(f"{GRANTS}/{grant.id}", headers=staff_headers(db, admin)).json()
    assert detail["status"] == "Revoked"
