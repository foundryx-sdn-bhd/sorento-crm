"""CLAUDE-CONNECTOR S1: the connector-session guard (card section 3, step 6).

A `claude_connector` session is the signed-in user for ordinary RBAC, but only when it
arrives through the analytics container (internal key), only for reads, only while the
user still holds `analytics.connector.use`, and never through impersonation. Staff
sessions are untouched.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.user_session import UserSession
from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import (
    grant_permission,
    BASE,
    INTERNAL,
    USE,
    client_for,
    connector_headers,
    full_sign_in,
    make_user,
    revoke_permission,
    staff_headers,
)

ME_PERMS = "/api/v1/user-management/users/me/permissions"
ALLOWED_GET = "/api/v1/master-data/brands"  # a reused tool route: inside the connector path allowlist


@pytest.fixture()
def signed_in(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            user = make_user(db, "guard", perms=(USE,))
            tok = full_sign_in(client, db, user)
            yield db, client, user, tok["access_token"]


def test_me_answers_the_signed_in_user(signed_in):
    _db, client, user, token = signed_in
    resp = client.get(f"{BASE}/me", headers=connector_headers(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == user.id and body["name"] == user.name


def test_connector_token_without_internal_key_is_refused_everywhere(signed_in):
    """A stolen token replayed against the public /api/v1 is useless."""
    _db, client, _user, token = signed_in
    bare = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{BASE}/me", headers=bare).status_code == 401
    assert client.get(ME_PERMS, headers=bare).status_code == 401


def test_connector_token_with_a_wrong_internal_key_is_refused(signed_in):
    _db, client, _user, token = signed_in
    headers = {"Authorization": f"Bearer {token}", "X-Analytics-Connector-Key": "wrong"}
    assert client.get(ME_PERMS, headers=headers).status_code == 401


def test_connector_token_reads_ordinary_get_routes_as_the_user(signed_in):
    db, client, user, token = signed_in
    from app.models.user import UserRoleAssignment
    from app.services.user_service import invalidate_rbac_cache

    role_id = db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == user.id).first().role_id
    grant_permission(db, role_id, "master_data.brands.view")
    db.commit()
    invalidate_rbac_cache()
    resp = client.get(ALLOWED_GET, headers=connector_headers(token))
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/order-management/orders/00000000-0000-0000-0000-000000000000/cancel"),
        ("PUT", "/api/v1/user-management/users/me"),
        ("DELETE", "/api/v1/auth/sessions/x"),
        ("PATCH", "/api/v1/user-management/users/me"),
    ],
)
def test_connector_token_cannot_write(signed_in, method, path):
    _db, client, _user, token = signed_in
    resp = client.request(method, path, headers=connector_headers(token), json={})
    assert resp.status_code == 403, (method, path, resp.status_code, resp.text)
    assert "connector_read_only" in resp.text


def test_the_listed_read_post_is_allowed(signed_in):
    """`crm_lookup_resolve` is a POST that only reads; it must get past the guard."""
    _db, client, _user, token = signed_in
    resp = client.post("/api/v1/lookup/resolve", headers=connector_headers(token), json={"items": []})
    assert "connector_read_only" not in resp.text, resp.text


def test_losing_the_permission_fails_the_next_call(signed_in):
    db, client, user, token = signed_in
    revoke_permission(db, user, USE)
    resp = client.get(f"{BASE}/me", headers=connector_headers(token))
    assert resp.status_code == 401
    assert client.get(ALLOWED_GET, headers=connector_headers(token)).status_code == 401


def test_deactivated_user_fails_the_next_call(signed_in):
    db, client, user, token = signed_in
    user.status = "BLOCKED"
    db.commit()
    assert client.get(f"{BASE}/me", headers=connector_headers(token)).status_code == 401


def test_connector_session_ignores_impersonation(signed_in):
    from app.models.impersonation import ImpersonationSession
    from app.models.user import UserRole, UserRoleAssignment

    db, client, user, token = signed_in
    target = make_user(db, "imp-t")
    role = UserRole(id=str(uuid.uuid4()), slug="admin", name="ZZT admin", is_protected=False, is_default=False)
    db.add(role)
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.add(ImpersonationSession(id=str(uuid.uuid4()), admin_user_id=user.id, target_user_id=target.id))
    db.commit()
    headers = {**connector_headers(token), "X-Impersonate-User-Id": target.id}
    resp = client.get(f"{BASE}/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user_id"] == user.id


def test_staff_session_cannot_use_connector_routes(signed_in):
    db, client, user, _token = signed_in
    resp = client.get(f"{BASE}/me", headers={**staff_headers(db, user), **INTERNAL})
    assert resp.status_code == 403


def test_staff_session_is_untouched_by_the_guard(signed_in):
    db, client, user, _token = signed_in
    assert client.get(ME_PERMS, headers=staff_headers(db, user)).status_code == 200


def test_expired_connector_session_is_401(signed_in):
    from datetime import timedelta

    from tests.analytics_connector._helpers import now

    db, client, _user, token = signed_in
    row = db.query(UserSession).filter(UserSession.token == token).one()
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert client.get(f"{BASE}/me", headers=connector_headers(token)).status_code == 401


def test_connector_session_does_not_slide(signed_in):
    db, client, _user, token = signed_in
    row = db.query(UserSession).filter(UserSession.token == token).one()
    before = row.expires_at
    client.get(f"{BASE}/me", headers=connector_headers(token))
    db.refresh(row)
    assert row.expires_at == before


def test_me_touches_the_grant_last_used(signed_in):
    from app.models.analytics_connector import AnalyticsConnectorGrant

    db, client, user, token = signed_in
    grant = db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.user_id == user.id).one()
    grant.last_used_at = None
    db.commit()
    client.get(f"{BASE}/me", headers=connector_headers(token))
    db.refresh(grant)
    assert grant.last_used_at is not None
