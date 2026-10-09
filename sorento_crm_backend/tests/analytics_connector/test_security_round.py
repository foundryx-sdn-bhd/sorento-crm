"""CLAUDE-CONNECTOR security round (crew audit of 9158d0a2c): red tests.

Names the coder must build to:

* ``analytics_connector_grants.refresh_issued_at`` (DateTime, set at every issue/rotation) and the
  token response field ``refresh_token_expires_in`` == 2592000 (30 days).
* a retired-refresh-token history on the grant, so ANY earlier token of the family (not only the
  previous one) revokes the grant; a replayed authorization code revokes the grant it produced.
* ``app.services.analytics_connector.oauth.purge_expired(db)`` and ``oauth.MAX_PENDING_CLIENTS`` (500).
  Rule pinned: ``register_client`` refuses when the clients WITHOUT a grant number >= MAX_PENDING_CLIENTS.
* ``app.services.analytics_connector.access.CONNECTOR_GET_PREFIXES`` (tuple of path prefixes); a GET by a
  connector session outside those prefixes answers 403 with detail ``connector_path_not_allowed``.
* ``revoke_all_for_user`` also revokes the user's connector grants.
* the ``bot_turns`` view masks OTP codes in ``reply_text`` and ``error``.
* ``POST /audit`` stores ``args`` truncated when its JSON is over ~4 KB.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.analytics_connector import AnalyticsConnectorGrant, AnalyticsOAuthClient, AnalyticsOAuthRequest
from app.models.audit import AuditLog
from app.services.otp_redaction import mask_otp_text
from tests._pg_fixture import blank_session, pg_session, unique_code
from tests.analytics_connector._helpers import (
    BASE,
    pkce_pair,
    start_authorize,
    staff_headers,
    INTERNAL,
    USE,
    client_for,
    connector_headers,
    full_sign_in,
    make_user,
    now,
    register,
)
from tests.analytics_connector.test_datasets import T0, _seed
from tests.analytics_connector.test_oauth_flow import _code_for, _exchange, _refresh


@pytest.fixture()
def env(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            yield db, client


def _grant(db, user):
    return db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.user_id == user.id).one()


def _me(client, token):
    return client.get(f"{BASE}/me", headers=connector_headers(token)).status_code


def _invalid_grant(resp):
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_grant", resp.text


# ------------------------------------------------------------------ 2/C: bot_turns text is OTP-masked


def test_bot_turn_reply_text_and_error_are_otp_masked():
    from app.services.analytics_connector.views import ensure_views

    with pg_session() as db:
        ensure_views(db)
        seed = _seed(db, unique_code("otpr", alpha=True))
        reply = "Your Sorento sign-in code is 482913"
        err = "send failed: your verification code is 777123"
        turn_id = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO chatbot.turns (id, contact_respond_id, envelope, status, is_test, response, error, created_at) "
                 "VALUES (:id, :c, '{}'::jsonb, 'done', false, CAST(:r AS jsonb), :e, :t)"),
            {"id": turn_id, "c": seed["contacts"]["a"]["respond_io_id"], "r": json.dumps({"reply": {"text": reply}}),
             "e": err, "t": T0 + timedelta(hours=1)},
        )
        db.flush()
        row = db.execute(
            text("SELECT reply_text, error FROM analytics_connector.bot_turns WHERE id = :i"), {"i": turn_id}
        ).one()
        assert row.reply_text == mask_otp_text(reply)
        assert "482913" not in row.reply_text
        assert row.error == mask_otp_text(err)
        assert "777123" not in row.error


# ------------------------------------------------------------------ 3a refresh lifetime 30 days


def test_refresh_token_response_says_it_lives_30_days(env):
    db, client = env
    user = make_user(db, "rf30", perms=(USE,))
    tok = full_sign_in(client, db, user)
    assert tok["refresh_token_expires_in"] == 2592000
    assert _refresh(client, tok["client_id"], tok["refresh_token"]).json()["refresh_token_expires_in"] == 2592000


def test_a_refresh_token_older_than_30_days_is_invalid_grant_though_the_grant_lives(env):
    db, client = env
    user = make_user(db, "rf31", perms=(USE,))
    tok = full_sign_in(client, db, user)
    grant = _grant(db, user)
    assert grant.expires_at - now() > timedelta(days=80)  # the grant (90 d) is alive
    grant.refresh_issued_at = now() - timedelta(days=31)
    db.commit()
    _invalid_grant(_refresh(client, tok["client_id"], tok["refresh_token"]))


def test_a_refresh_token_29_days_old_still_works_and_rotation_restarts_the_clock(env):
    db, client = env
    user = make_user(db, "rf29", perms=(USE,))
    tok = full_sign_in(client, db, user)
    grant = _grant(db, user)
    grant.refresh_issued_at = now() - timedelta(days=29)
    db.commit()
    resp = _refresh(client, tok["client_id"], tok["refresh_token"])
    assert resp.status_code == 200, resp.text
    db.refresh(grant)
    assert now() - grant.refresh_issued_at < timedelta(minutes=5)


# ------------------------------------------------------------------ 3b family revocation


def test_presenting_any_retired_refresh_token_revokes_the_whole_family(env):
    db, client = env
    user = make_user(db, "fam", perms=(USE,))
    r1 = full_sign_in(client, db, user)
    cid = r1["client_id"]
    r2 = _refresh(client, cid, r1["refresh_token"]).json()
    r3 = _refresh(client, cid, r2["refresh_token"]).json()
    # r1 is two rotations old: the old code only remembered r2.
    _invalid_grant(_refresh(client, cid, r1["refresh_token"]))
    grant = _grant(db, user)
    db.refresh(grant)
    assert grant.revoked_at is not None
    _invalid_grant(_refresh(client, cid, r3["refresh_token"]))
    assert _me(client, r3["access_token"]) == 401


# ------------------------------------------------------------------ 3c code replay


def test_replaying_a_used_code_revokes_the_grant_it_produced(env):
    db, client = env
    user = make_user(db, "rpl", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    first = _exchange(client, client_id, verifier, code)
    assert first.status_code == 200, first.text
    tok = first.json()
    assert _me(client, tok["access_token"]) == 200
    _invalid_grant(_exchange(client, client_id, verifier, code))
    assert _me(client, tok["access_token"]) == 401
    _invalid_grant(_refresh(client, client_id, tok["refresh_token"]))
    assert _grant(db, user).revoked_at is not None


def test_an_unknown_code_revokes_nothing(env):
    db, client = env
    user = make_user(db, "unk", perms=(USE,))
    tok = full_sign_in(client, db, user)
    _invalid_grant(_exchange(client, tok["client_id"], "v" * 43, "no-such-code"))
    assert _me(client, tok["access_token"]) == 200


# ------------------------------------------------------------------ 5 expired OAuth rows


def test_purge_expired_drops_dead_requests_and_unused_old_clients(env):
    from app.services.analytics_connector.oauth import purge_expired

    db, client = env
    user = make_user(db, "purge", perms=(USE,))
    signed = full_sign_in(client, db, user)  # this client has a grant
    old_unused, old_unused_2, fresh = register(client), register(client), register(client)
    for cid, age in ((signed["client_id"], 25), (old_unused, 25), (old_unused_2, 48), (fresh, 1)):
        db.query(AnalyticsOAuthClient).filter(AnalyticsOAuthClient.id == cid).update({"created_at": now() - timedelta(hours=age)})
    stale = AnalyticsOAuthRequest(id=str(uuid.uuid4()), client_id=fresh, redirect_uri="https://claude.ai/api/mcp/auth_callback",
                                  code_challenge="c", scope="analytics.read", resource="r", status="pending",
                                  expires_at=now() - timedelta(minutes=1))
    live = AnalyticsOAuthRequest(id=str(uuid.uuid4()), client_id=fresh, redirect_uri="https://claude.ai/api/mcp/auth_callback",
                                 code_challenge="c", scope="analytics.read", resource="r", status="pending",
                                 expires_at=now() + timedelta(minutes=5))
    db.add_all([stale, live])
    db.commit()
    purge_expired(db)
    db.expire_all()
    ids = {c.id for c in db.query(AnalyticsOAuthClient).all()}
    assert signed["client_id"] in ids and fresh in ids
    assert old_unused not in ids and old_unused_2 not in ids
    assert db.get(AnalyticsOAuthRequest, stale.id) is None
    assert db.get(AnalyticsOAuthRequest, live.id) is not None


def test_register_at_the_cap_evicts_the_oldest_grantless_client_instead_of_refusing(env, monkeypatch):
    from app.services.analytics_connector import oauth

    assert oauth.MAX_PENDING_CLIENTS == 500
    db, client = env
    user = make_user(db, "cap", perms=(USE,))
    signed = full_sign_in(client, db, user)  # a client WITH a grant is never evicted, never counts
    monkeypatch.setattr(oauth, "MAX_PENDING_CLIENTS", 3)
    ids = [register(client) for _ in range(3)]
    for age, cid in zip((30, 20, 10), ids):  # ids[0] is the oldest grant-less client
        db.query(AnalyticsOAuthClient).filter(AnalyticsOAuthClient.id == cid).update(
            {"created_at": now() - timedelta(minutes=age)})
    db.query(AnalyticsOAuthClient).filter(AnalyticsOAuthClient.id == signed["client_id"]).update(
        {"created_at": now() - timedelta(minutes=99)})  # older than all, but it has a grant
    db.commit()
    newest = register(client)  # asserts 201
    db.expire_all()
    present = {c.id for c in db.query(AnalyticsOAuthClient).all()}
    assert ids[0] not in present
    assert {ids[1], ids[2], newest, signed["client_id"]} <= present


def test_authorize_also_purges_expired_request_rows(env):
    db, client = env
    cid = register(client)
    stale = AnalyticsOAuthRequest(id=str(uuid.uuid4()), client_id=cid, redirect_uri="https://claude.ai/api/mcp/auth_callback",
                                  code_challenge="c", scope="analytics.read", resource="r", status="pending",
                                  expires_at=now() - timedelta(minutes=1))
    db.add(stale)
    db.commit()
    stale_id = stale.id
    _, challenge = pkce_pair()
    assert start_authorize(client, cid, challenge).status_code == 200
    db.expire_all()
    assert db.get(AnalyticsOAuthRequest, stale_id) is None


def _request_row(db, client_id):
    return db.query(AnalyticsOAuthRequest).filter(AnalyticsOAuthRequest.client_id == client_id).one()


def test_the_request_row_links_the_grant_as_soon_as_the_code_is_exchanged(env):
    db, client = env
    user = make_user(db, "lnk", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    assert _exchange(client, client_id, verifier, code).status_code == 200
    db.expire_all()
    row = _request_row(db, client_id)
    assert row.status == "used"
    assert row.grant_id == _grant(db, user).id


def test_purge_expired_keeps_a_used_request_row_that_has_a_grant_id(env):
    from app.services.analytics_connector.oauth import purge_expired

    db, client = env
    user = make_user(db, "keep", perms=(USE,))
    client_id, verifier, code = _code_for(client, db, user)
    assert _exchange(client, client_id, verifier, code).status_code == 200
    row = _request_row(db, client_id)
    row.expires_at = now() - timedelta(hours=1)
    row.code_expires_at = now() - timedelta(hours=1)
    db.commit()
    row_id = row.id
    purge_expired(db)
    db.expire_all()
    assert db.get(AnalyticsOAuthRequest, row_id) is not None
    _invalid_grant(_exchange(client, client_id, verifier, code))  # replay still revokes after a purge
    assert _grant(db, user).revoked_at is not None


# ------------------------------------------------------------------ A: connector path allowlist


@pytest.mark.parametrize("path", ["/api/v1/user-management/users/me", "/api/v1/user-management/contacts/",
                                  "/api/v1/user-management/users/me/permissions", "/api/v1/auth/sessions"])
def test_connector_get_outside_the_allowlist_is_403(env, path):
    db, client = env
    user = make_user(db, "pa", perms=(USE,))
    tok = full_sign_in(client, db, user)
    resp = client.get(path, headers=connector_headers(tok["access_token"]))
    assert resp.status_code == 403, resp.text
    assert "connector_path_not_allowed" in resp.text


def test_connector_get_on_a_reused_tool_route_and_its_own_routes_passes_the_path_guard(env):
    db, client = env
    user = make_user(db, "pb", perms=(USE,))
    tok = full_sign_in(client, db, user)
    for path in ("/api/v1/master-data/brands", f"{BASE}/me"):
        resp = client.get(path, headers=connector_headers(tok["access_token"]))
        assert "connector_path_not_allowed" not in resp.text, path
        assert resp.status_code not in (401, 405), (path, resp.text)


def test_a_dot_segment_cannot_walk_out_of_an_allowed_prefix(env):
    db, client = env
    user = make_user(db, "pc", perms=(USE,))
    tok = full_sign_in(client, db, user)
    resp = client.get("/api/v1/master-data/brands/../../users/me", headers=connector_headers(tok["access_token"]))
    assert resp.status_code in (403, 404), resp.text
    assert resp.status_code != 200


def test_every_reused_tool_route_is_in_the_exact_backend_route_list():
    """Keeps the container list and CONNECTOR_GET_ROUTES in sync: add a tool, add its path template."""
    repo = Path(__file__).resolve().parents[3]
    for extra in (repo / "sorento_crm_mcp", repo / "sorento_analytics_mcp"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    try:
        from sorento_analytics_mcp.tools import ANALYTICS_REUSED_TOOLS
        from sorento_crm_mcp.catalog import CATALOG
        from sorento_crm_mcp.module_loader import merged_catalog
    except Exception as exc:  # container deps absent in this env
        pytest.skip(f"container package not importable: {exc!r}")
    import re

    from app.services.analytics_connector.access import CONNECTOR_GET_ROUTES

    norm = lambda t: re.sub(r"\{[^}]*\}", "{}", t.rstrip("/"))  # noqa: E731
    routes = {norm(r) for r in CONNECTOR_GET_ROUTES}
    assert ANALYTICS_REUSED_TOOLS
    by_name = {t.name: t for t in merged_catalog(CATALOG)}
    for name in sorted(ANALYTICS_REUSED_TOOLS):
        spec = by_name[name]
        if spec.method.upper() != "GET":
            continue
        assert norm(spec.path) in routes, f"{name}: {spec.path}"
    assert any(r.startswith(BASE) for r in CONNECTOR_GET_ROUTES)


_UID = "3f2b8c1e-5a47-4d0e-9c1a-0b6d2f7e8a91"


@pytest.mark.parametrize("path", [
    f"/api/v1/resource-management/attachments/{_UID}/download",
    f"/api/v1/resource-management/attachments/{_UID}/preview-url",
    "/api/v1/resource-management/attachments/current-stock-list",
    "/api/v1/inventory/stock/balance/export",
    "/api/v1/master-data/product-attachments/brochure-images",
])
def test_connector_get_on_a_file_or_export_route_inside_an_old_prefix_is_403(env, path):
    db, client = env
    user = make_user(db, "rt", perms=(USE,))
    tok = full_sign_in(client, db, user)
    resp = client.get(path, headers=connector_headers(tok["access_token"]))
    assert resp.status_code == 403, resp.text
    assert "connector_path_not_allowed" in resp.text


def test_exact_routes_still_pass_the_guard_for_tool_routes_with_a_param(env):
    db, client = env
    user = make_user(db, "rt2", perms=(USE,))
    tok = full_sign_in(client, db, user)
    for path in ("/api/v1/master-data/brands", f"/api/v1/project-sales/projects/{_UID}"):
        resp = client.get(path, headers=connector_headers(tok["access_token"]))
        assert "connector_path_not_allowed" not in resp.text, path
        assert resp.status_code not in (401, 405), (path, resp.text)


@pytest.mark.parametrize("path,raw,expected", [
    ("/api/v1/master-data/brands/../../users/me", "", False),
    ("/api/v1/master-data/brands/x", "/api/v1/master-data/brands/%2e%2e/x", False),
    ("/api/v1/master-data/brands/x", "/api/v1/master-data/brands%2fx", False),
    ("//api/v1/master-data/brands", "", False),
    ("/api/v1/master-data/brands-secret", "", False),
    ("/api/v1/master-data/brands", "/api/v1/master-data/brands", True),
])
def test_connector_path_allowed_direct(path, raw, expected):
    from app.services.analytics_connector.access import connector_path_allowed

    assert connector_path_allowed(path, raw) is expected


# ------------------------------------------------------------------ B: sign out everywhere ends the grants


def test_revoke_all_for_user_also_revokes_connector_grants(env):
    from app.services.user_session_service import revoke_all_for_user

    db, client = env
    user = make_user(db, "sao", perms=(USE,))
    tok = full_sign_in(client, db, user)
    revoke_all_for_user(db, user.id)
    assert _me(client, tok["access_token"]) == 401
    _invalid_grant(_refresh(client, tok["client_id"], tok["refresh_token"]))
    assert _grant(db, user).revoked_at is not None


# ------------------------------------------------------------------ D: audit args are capped


def test_audit_args_over_4kb_are_stored_truncated_not_rejected(env):
    db, client = env
    user = make_user(db, "aud", perms=(USE,))
    tok = full_sign_in(client, db, user)
    big = {"q": "x" * 50_000, "other": ["y" * 1000] * 20}
    resp = client.post(f"{BASE}/audit", json={"tool": "crm_master_brands_list", "args": big, "status": "ok"},
                       headers=connector_headers(tok["access_token"]))
    assert resp.status_code == 204, resp.text
    row = db.query(AuditLog).filter(AuditLog.event == "analytics_connector.tool_call").one()
    stored = row.new_values["args"]
    assert len(json.dumps(stored)) <= 4500
    resp = client.post(f"{BASE}/audit", json={"tool": "t", "args": {"limit": "5"}, "status": "ok"},
                       headers=connector_headers(tok["access_token"]))
    assert resp.status_code == 204
    small = [r for r in db.query(AuditLog).filter(AuditLog.event == "analytics_connector.tool_call") if r.new_values["tool"] == "t"]
    assert small[0].new_values["args"] == {"limit": "5"}


# ------------------------------------------------------------------ single-session revoke ends the grant


def test_revoking_the_connector_session_by_the_owner_also_revokes_the_grant(env):
    db, client = env
    user = make_user(db, "one", perms=(USE,))
    tok = full_sign_in(client, db, user)
    grant = _grant(db, user)
    sid = grant.current_session_id
    resp = client.delete(f"/api/v1/auth/sessions/{sid}", headers=staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    db.refresh(grant)
    assert grant.revoked_at is not None
    _invalid_grant(_refresh(client, tok["client_id"], tok["refresh_token"]))
