"""CLAUDE-CONNECTOR S1: the OAuth 2.1 authorization server (card section 3).

DCR with a redirect allowlist, authorize -> consent -> code, PKCE S256 token exchange,
rotating refresh with reuse detection, revoke. Postgres scratch schema, writes discarded.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.analytics_connector import AnalyticsConnectorGrant, AnalyticsOAuthRequest
from app.models.user_session import UserSession
from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import (
    BASE,
    CHAT_VIEW,
    CLAUDE_CALLBACK,
    INTERNAL,
    RESOURCE,
    USE,
    client_for,
    connector_headers,
    full_sign_in,
    make_user,
    now,
    parse_redirect,
    pkce_pair,
    register,
    staff_headers,
    start_authorize,
)


@pytest.fixture()
def env(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            yield db, client


# ------------------------------------------------------------------ registration


def test_register_requires_internal_key(env):
    _db, client = env
    resp = client.post(f"{BASE}/oauth/register", json={"redirect_uris": [CLAUDE_CALLBACK]})
    assert resp.status_code == 401


def test_register_accepts_the_claude_callback(env):
    _db, client = env
    resp = client.post(
        f"{BASE}/oauth/register",
        json={"redirect_uris": [CLAUDE_CALLBACK], "client_name": "Claude", "token_endpoint_auth_method": "none"},
        headers=INTERNAL,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["client_id"]
    assert body["redirect_uris"] == [CLAUDE_CALLBACK]
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body


@pytest.mark.parametrize(
    "uris",
    [
        ["https://evil.example.com/cb"],
        [CLAUDE_CALLBACK, "https://evil.example.com/cb"],
        ["http://localhost:3118/callback"],  # Q4 (a): Claude Code loopback not allowed in v1
        ["https://claude.ai/api/mcp/auth_callback/../x"],
        [],
    ],
)
def test_register_refuses_any_redirect_outside_the_allowlist(env, uris):
    _db, client = env
    resp = client.post(f"{BASE}/oauth/register", json={"redirect_uris": uris}, headers=INTERNAL)
    assert resp.status_code == 400
    assert resp.json()["error"] in {"invalid_redirect_uri", "invalid_client_metadata"}


def test_register_refuses_a_confidential_client(env):
    _db, client = env
    resp = client.post(
        f"{BASE}/oauth/register",
        json={"redirect_uris": [CLAUDE_CALLBACK], "token_endpoint_auth_method": "client_secret_basic"},
        headers=INTERNAL,
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


# ------------------------------------------------------------------ authorize


def test_authorize_creates_a_pending_request(env):
    db, client = env
    client_id = register(client)
    _v, challenge = pkce_pair()
    resp = start_authorize(client, client_id, challenge)
    assert resp.status_code == 200, resp.text
    row = db.get(AnalyticsOAuthRequest, resp.json()["request_id"])
    assert row is not None and row.status == "pending"
    assert row.expires_at > now() + timedelta(minutes=5)


@pytest.mark.parametrize(
    "override,error",
    [
        ({"client_id": "nope"}, "invalid_client"),
        ({"redirect_uri": "https://evil.example.com/cb"}, "invalid_redirect_uri"),
    ],
)
def test_authorize_never_redirects_for_a_bad_client_or_redirect(env, override, error):
    _db, client = env
    client_id = register(client)
    _v, challenge = pkce_pair()
    resp = start_authorize(client, client_id, challenge, **override)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == error
    assert not body.get("redirect_to")


@pytest.mark.parametrize(
    "override,error",
    [
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge": ""}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"resource": "https://other.example.com/mcp"}, "invalid_target"),
        ({"scope": "analytics.write"}, "invalid_scope"),
    ],
)
def test_authorize_bad_params_redirect_back_with_the_error(env, override, error):
    _db, client = env
    client_id = register(client)
    _v, challenge = pkce_pair()
    resp = start_authorize(client, client_id, challenge, **override)
    assert resp.status_code == 400
    q = parse_redirect(resp.json()["redirect_to"])
    assert q["error"] == error
    assert q["state"] == "st-123"


def test_consent_details_name_the_user_and_the_redirect_host(env):
    db, client = env
    user = make_user(db, "alice", perms=(USE,))
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    resp = client.get(f"{BASE}/authorize/{request_id}", headers=staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["redirect_host"] == "claude.ai"
    assert body["user_name"] == user.name
    assert body["allowed"] is True


def test_consent_details_say_not_allowed_without_the_permission(env):
    db, client = env
    user = make_user(db, "bob")
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    resp = client.get(f"{BASE}/authorize/{request_id}", headers=staff_headers(db, user))
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False


def test_approve_without_the_permission_is_refused(env):
    db, client = env
    user = make_user(db, "carol", perms=(CHAT_VIEW,))
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    resp = client.post(f"{BASE}/authorize/{request_id}/approve", headers=staff_headers(db, user))
    assert resp.status_code == 403


@pytest.mark.parametrize("role_slug", ["admin", "superadmin"])
def test_admins_do_not_get_the_connector_by_bypass(env, role_slug):
    """Q1 (a): explicit grant only, admins included."""
    db, client = env
    user = make_user(db, f"adm-{role_slug}", role_slug=None)
    from app.models.user import UserRole, UserRoleAssignment

    role = db.query(UserRole).filter(UserRole.slug == role_slug).first()
    if role is None:
        import uuid

        role = UserRole(id=str(uuid.uuid4()), slug=role_slug, name=role_slug, is_protected=False, is_default=False)
        db.add(role)
        db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.commit()
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    resp = client.post(f"{BASE}/authorize/{request_id}/approve", headers=staff_headers(db, user))
    assert resp.status_code == 403


def test_approve_while_impersonating_is_refused(env):
    """An admin viewing as someone cannot consent on their behalf."""
    import uuid

    from app.models.impersonation import ImpersonationSession
    from app.models.user import UserRole, UserRoleAssignment

    db, client = env
    admin = make_user(db, "imp-admin", perms=(USE,))
    target = make_user(db, "imp-target", perms=(USE,))
    role = UserRole(id=str(uuid.uuid4()), slug="admin", name="ZZT admin", is_protected=False, is_default=False)
    db.add(role)
    db.flush()
    db.add(UserRoleAssignment(user_id=admin.id, role_id=role.id))
    db.add(ImpersonationSession(id=str(uuid.uuid4()), admin_user_id=admin.id, target_user_id=target.id))
    db.commit()
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    headers = {**staff_headers(db, admin), "X-Impersonate-User-Id": target.id}
    resp = client.post(f"{BASE}/authorize/{request_id}/approve", headers=headers)
    assert resp.status_code == 403


def test_approve_returns_a_code_redirect_with_state_and_iss(env):
    db, client = env
    user = make_user(db, "dave", perms=(USE,))
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    resp = client.post(f"{BASE}/authorize/{request_id}/approve", headers=staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    q = parse_redirect(resp.json()["redirect_to"])
    assert q["code"] and q["state"] == "st-123"
    assert q["iss"].endswith("/analytics")
    row = db.get(AnalyticsOAuthRequest, request_id)
    assert row.code_hash and q["code"] not in (row.code_hash or "")


def test_approve_twice_is_refused(env):
    db, client = env
    user = make_user(db, "erin", perms=(USE,))
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    h = staff_headers(db, user)
    assert client.post(f"{BASE}/authorize/{request_id}/approve", headers=h).status_code == 200
    assert client.post(f"{BASE}/authorize/{request_id}/approve", headers=h).status_code in (404, 409, 410)


def test_deny_redirects_with_access_denied(env):
    db, client = env
    user = make_user(db, "frank", perms=(USE,))
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    resp = client.post(f"{BASE}/authorize/{request_id}/deny", headers=staff_headers(db, user))
    assert resp.status_code == 200
    q = parse_redirect(resp.json()["redirect_to"])
    assert q["error"] == "access_denied" and q["state"] == "st-123"


def test_expired_request_cannot_be_approved(env):
    db, client = env
    user = make_user(db, "gina", perms=(USE,))
    client_id = register(client)
    _v, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    row = db.get(AnalyticsOAuthRequest, request_id)
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    resp = client.post(f"{BASE}/authorize/{request_id}/approve", headers=staff_headers(db, user))
    assert resp.status_code in (404, 410)


# ------------------------------------------------------------------ token


def test_full_flow_issues_a_one_hour_connector_session(env):
    db, client = env
    user = make_user(db, "hana", perms=(USE,))
    tok = full_sign_in(client, db, user)
    assert tok["token_type"] == "Bearer"
    assert tok["expires_in"] == 3600
    assert tok["refresh_token"] and tok["access_token"] != tok["refresh_token"]
    session = db.query(UserSession).filter(UserSession.token == tok["access_token"]).one()
    assert session.auth_method == "claude_connector"
    assert session.rolling is False
    assert timedelta(minutes=59) < session.expires_at - now() <= timedelta(hours=1)
    grant = db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.user_id == user.id).one()
    assert grant.current_session_id == session.id
    assert tok["refresh_token"] not in (grant.refresh_token_hash or "")
    assert timedelta(days=89) < grant.expires_at - now() <= timedelta(days=90)


def _code_for(client, db, user):
    client_id = register(client)
    verifier, challenge = pkce_pair()
    request_id = start_authorize(client, client_id, challenge).json()["request_id"]
    approved = client.post(f"{BASE}/authorize/{request_id}/approve", headers=staff_headers(db, user))
    return client_id, verifier, parse_redirect(approved.json()["redirect_to"])["code"]


def _exchange(client, client_id, verifier, code, /, **over):
    data = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "client_id": client_id, "redirect_uri": CLAUDE_CALLBACK, "resource": RESOURCE}
    data.update(over)
    return client.post(f"{BASE}/oauth/token", data=data, headers=INTERNAL)


def test_token_requires_internal_key(env):
    db, client = env
    user = make_user(db, "ivan", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    resp = client.post(
        f"{BASE}/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
              "client_id": client_id, "redirect_uri": CLAUDE_CALLBACK},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "over",
    [
        {"code_verifier": "x" * 50},
        {"redirect_uri": "https://claude.ai/other"},
        {"code": "not-a-code"},
        {"resource": "https://other.example.com/mcp"},
    ],
)
def test_token_exchange_failures_are_invalid_grant(env, over):
    db, client = env
    user = make_user(db, "judy", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    resp = _exchange(client, client_id, verifier, code, **over)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_exchange_with_another_client_is_refused(env):
    db, client = env
    user = make_user(db, "kim", perms=(USE,))
    _cid, verifier, code = _code_for(client, db, user)
    other = register(client)
    resp = _exchange(client, other, verifier, code)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_code_is_single_use(env):
    db, client = env
    user = make_user(db, "lee", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    assert _exchange(client, client_id, verifier, code).status_code == 200
    again = _exchange(client, client_id, verifier, code)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_code_expires_after_a_minute(env):
    db, client = env
    user = make_user(db, "max", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    row = db.query(AnalyticsOAuthRequest).filter(AnalyticsOAuthRequest.status == "approved").one()
    row.code_expires_at = now() - timedelta(seconds=1)
    db.commit()
    resp = _exchange(client, client_id, verifier, code)
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_grant"


def test_token_response_is_not_cacheable(env):
    db, client = env
    user = make_user(db, "nia", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    resp = _exchange(client, client_id, verifier, code)
    assert resp.headers.get("cache-control") == "no-store"


def test_unsupported_grant_type(env):
    _db, client = env
    resp = client.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials"}, headers=INTERNAL)
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


# ------------------------------------------------------------------ refresh


def _refresh(client, client_id, refresh_token):
    return client.post(
        f"{BASE}/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
        headers=INTERNAL,
    )


def test_refresh_rotates_and_revokes_the_old_session(env):
    db, client = env
    user = make_user(db, "omar", perms=(USE,))
    tok = full_sign_in(client, db, user)
    resp = _refresh(client, tok["client_id"], tok["refresh_token"])
    assert resp.status_code == 200, resp.text
    new = resp.json()
    assert new["refresh_token"] != tok["refresh_token"]
    assert new["access_token"] != tok["access_token"]
    old = db.query(UserSession).filter(UserSession.token == tok["access_token"]).one()
    db.refresh(old)
    assert old.revoked_at is not None
    assert client.get(f"{BASE}/me", headers=connector_headers(tok["access_token"])).status_code == 401
    assert client.get(f"{BASE}/me", headers=connector_headers(new["access_token"])).status_code == 200


def test_refresh_token_reuse_revokes_the_whole_grant(env):
    db, client = env
    user = make_user(db, "pia", perms=(USE,))
    tok = full_sign_in(client, db, user)
    new = _refresh(client, tok["client_id"], tok["refresh_token"]).json()
    reuse = _refresh(client, tok["client_id"], tok["refresh_token"])
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
    grant = db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.user_id == user.id).one()
    db.refresh(grant)
    assert grant.revoked_at is not None
    assert client.get(f"{BASE}/me", headers=connector_headers(new["access_token"])).status_code == 401
    assert _refresh(client, tok["client_id"], new["refresh_token"]).status_code == 400


def test_refresh_after_grant_expiry_is_invalid_grant(env):
    db, client = env
    user = make_user(db, "quinn", perms=(USE,))
    tok = full_sign_in(client, db, user)
    grant = db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.user_id == user.id).one()
    grant.expires_at = now() - timedelta(seconds=1)
    db.commit()
    resp = _refresh(client, tok["client_id"], tok["refresh_token"])
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_grant"


def test_refresh_with_wrong_client_is_invalid_grant(env):
    db, client = env
    user = make_user(db, "rita", perms=(USE,))
    tok = full_sign_in(client, db, user)
    other = register(client)
    resp = _refresh(client, other, tok["refresh_token"])
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_grant"


def test_refresh_for_a_user_who_lost_the_permission_is_invalid_grant(env):
    from tests.analytics_connector._helpers import revoke_permission

    db, client = env
    user = make_user(db, "sam", perms=(USE,))
    tok = full_sign_in(client, db, user)
    revoke_permission(db, user, USE)
    resp = _refresh(client, tok["client_id"], tok["refresh_token"])
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_grant"


# ------------------------------------------------------------------ audit of the lifecycle


def test_grant_creation_is_audited(env):
    from app.models.audit import AuditLog

    db, client = env
    user = make_user(db, "tara", perms=(USE,))
    full_sign_in(client, db, user)
    events = {
        r.event
        for r in db.query(AuditLog).filter(AuditLog.entity_type == "analytics_connector_grant").all()
    }
    assert "analytics_connector.grant_created" in events
