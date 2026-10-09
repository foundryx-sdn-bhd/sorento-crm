"""CLAUDE-CONNECTOR r3: SS-signed calls (`Authorization: Connector <JWT>`, contract section 3).

The call token makes the request run AS the grant's user, with normal RBAC + company scope, no
`user_sessions` row and no impersonation. Error shape `{"error": {"code", "message"}}` (section 5).

Resolutions pinned here (contract is silent):
* a `req_hash` mismatch (same token on another path, query or body) answers 401 `invalid_signature`
  (no dedicated code in section 5); so do a wrong `typ`/`iss`/`connection_id` and a missing claim.
* the RBAC refusal of a reused route answers 403 with `error.code == "permission_required"`
  (translated for connector calls), so SS keeps the grant: a bare `{"detail": ...}` would leave SS
  unable to tell it from `connector_access_removed`.
* allowed non-GETs are exactly the manifest's POST routes: `/api/v1/connector/query` AND
  `/api/v1/lookup/resolve` (the reused `crm_lookup_resolve` tool is a read-only POST). Any other
  method/path pair answers 403 `route_not_allowed`.
* the exact route list lives at `app.services.analytics_connector.access.CONNECTOR_ROUTES`, a
  collection of `(METHOD, path_template)` (manifest test checks it both ways).
* `revoke_all_for_user` stamps `users.connector_revoked_at` (also with `except_session_id`).
"""
from __future__ import annotations

import uuid

import pytest

from app.models.user import User
from app.models.user_session import UserSession
from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import (
    BASE,
    BRANDS,
    BRANDS_VIEW,
    CHAT_VIEW,
    PRODUCT_TO_SS,
    USE,
    assert_error,
    call_token,
    client_for,
    epoch,
    error_code,
    make_admin_impersonating,
    make_user,
    revoke_permission,
    signed,
    tool_call_rows,
)

DATASETS = f"{BASE}/datasets"


@pytest.fixture()
def env(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            yield db, client


def _analyst(db, label="an", extra=()):
    return make_user(db, label, perms=(USE, CHAT_VIEW, BRANDS_VIEW, *extra))


# ------------------------------------------------------------------ happy path


def test_a_signed_call_reads_a_reused_route_as_the_user(env):
    db, client = env
    user = _analyst(db)
    resp = signed(client, "GET", BRANDS, user.id, tool="crm_master_brands_list")
    assert resp.status_code == 200, resp.text


def test_a_signed_call_reads_the_chat_datasets(env):
    db, client = env
    user = _analyst(db, "ds")
    resp = signed(client, "GET", DATASETS, user.id, tool="chat_list_datasets")
    assert resp.status_code == 200, resp.text
    assert "datasets" in resp.json()


def test_rbac_still_applies_to_reused_routes(env):
    db, client = env
    user = make_user(db, "nobrand", perms=(USE,))
    assert_error(signed(client, "GET", BRANDS, user.id, tool="crm_master_brands_list"), 403, "permission_required")


def test_a_call_creates_no_user_session_row(env):
    db, client = env
    user = _analyst(db, "nosess")
    assert signed(client, "GET", BRANDS, user.id).status_code == 200
    assert signed(client, "GET", DATASETS, user.id).status_code == 200
    db.expire_all()
    assert db.query(UserSession).filter(UserSession.user_id == user.id).count() == 0


# ------------------------------------------------------------------ token refusals (401)


def test_no_authorization_is_401(env):
    _db, client = env
    assert client.get(DATASETS).status_code == 401


def test_a_staff_bearer_scheme_carrying_a_call_jwt_is_not_accepted(env):
    """The JWT is only honoured under the `Connector` scheme; as a Bearer it is an unknown session."""
    db, client = env
    user = _analyst(db, "scheme")
    tok = call_token(user.id, "GET", BRANDS)
    assert client.get(BRANDS, headers={"Authorization": f"Bearer {tok}"}).status_code == 401


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"secret": "zzt-wrong-secret-0000000000000000000000000"}, "invalid_signature"),
        ({"secret": PRODUCT_TO_SS}, "invalid_signature"),  # the other direction's secret
        ({"aud": "sorento-connector-consent"}, "wrong_audience"),
        ({"aud": "foundryx-connector"}, "wrong_audience"),
        ({"typ": "connector_consent_request"}, "invalid_signature"),
        ({"iss": "someone-else"}, "invalid_signature"),
        ({"connection_id": "zzt-other-connection"}, "invalid_signature"),
        ({"drop": ("jti",)}, "invalid_signature"),
        ({"drop": ("req_hash",)}, "invalid_signature"),
        ({"drop": ("sub",)}, "invalid_signature"),
        ({"drop": ("consented_at",)}, "invalid_signature"),
    ],
)
def test_bad_tokens_are_refused(env, overrides, code):
    db, client = env
    user = _analyst(db, "bad")
    assert_error(signed(client, "GET", BRANDS, user.id, **overrides), 401, code)


def test_an_unsigned_alg_none_token_is_refused(env):
    import base64
    import json

    db, client = env
    user = _analyst(db, "none")
    good = call_token(user.id, "GET", BRANDS)
    from jose import jwt

    claims = jwt.get_unverified_claims(good)
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    forged = f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc(claims)}."
    resp = client.get(BRANDS, headers={"Authorization": f"Connector {forged}"})
    assert resp.status_code == 401, resp.text


def test_an_expired_token_is_token_expired(env):
    db, client = env
    user = _analyst(db, "exp")
    t = epoch()
    assert_error(signed(client, "GET", BRANDS, user.id, iat=t - 300, exp=t - 120), 401, "token_expired")


def test_a_replayed_jti_is_refused(env):
    db, client = env
    user = _analyst(db, "rpl")
    tok = call_token(user.id, "GET", BRANDS)
    assert signed(client, "GET", BRANDS, user.id, token=tok).status_code == 200
    assert_error(signed(client, "GET", BRANDS, user.id, token=tok), 401, "replayed")


@pytest.mark.parametrize(
    "bound,sent",
    [
        (("GET", BRANDS, "", b""), ("GET", f"{BASE}/datasets", None)),  # another path
        (("GET", BRANDS, "limit=5", b""), ("GET", f"{BRANDS}?limit=500", None)),  # another query
        (("GET", BRANDS, "", b""), ("GET", f"{BRANDS}?search=x", None)),  # a query added
        (("POST", f"{BASE}/query", "", b'{"dataset":"messages"}'),
         ("POST", f"{BASE}/query", {"dataset": "contacts"})),  # another body
    ],
)
def test_a_token_cannot_be_moved_to_another_route_query_or_body(env, bound, sent):
    db, client = env
    user = _analyst(db, "hash")
    m, p, q, b = bound
    tok = call_token(user.id, m, p, q, b)
    method, url, body = sent
    assert_error(signed(client, method, url, user.id, token=tok, json_body=body), 401, "invalid_signature")


def test_the_canonical_query_ignores_parameter_order(env):
    db, client = env
    user = _analyst(db, "order")
    tok = call_token(user.id, "GET", BRANDS, "page=1&limit=5")
    resp = signed(client, "GET", f"{BRANDS}?limit=5&page=1", user.id, token=tok)
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------ user refusals (403)


def test_an_unknown_user_is_refused(env):
    _db, client = env
    resp = signed(client, "GET", BRANDS, str(uuid.uuid4()))
    assert resp.status_code in (401, 403), resp.text
    assert error_code(resp) in {"invalid_signature", "connector_access_removed", "user_inactive"}


def test_an_inactive_user_is_user_inactive(env):
    db, client = env
    user = _analyst(db, "inact")
    user.status = "INACTIVE"
    db.commit()
    assert_error(signed(client, "GET", BRANDS, user.id), 403, "user_inactive")


def test_losing_the_connector_permission_is_connector_access_removed(env):
    db, client = env
    user = _analyst(db, "lost")
    assert signed(client, "GET", BRANDS, user.id).status_code == 200
    revoke_permission(db, user, USE)
    assert_error(signed(client, "GET", BRANDS, user.id), 403, "connector_access_removed")


def test_an_admin_without_the_connector_permission_is_refused(env):
    db, client = env
    admin = make_user(db, "admin-np", perms=(CHAT_VIEW,), role_slug="admin")
    assert_error(signed(client, "GET", BRANDS, admin.id), 403, "connector_access_removed")
    assert_error(signed(client, "GET", DATASETS, admin.id), 403, "connector_access_removed")


def test_a_consent_older_than_the_users_revocation_needs_reconsent(env):
    db, client = env
    user = _analyst(db, "recon")
    from app.services.user_session_service import revoke_all_for_user

    revoke_all_for_user(db, user.id)
    db.expire_all()
    revoked = db.get(User, user.id).connector_revoked_at
    assert revoked is not None
    revoked_epoch = int(revoked.replace(tzinfo=__import__("datetime").timezone.utc).timestamp())
    assert_error(signed(client, "GET", BRANDS, user.id, consented_at=revoked_epoch - 3600), 403,
                 "reconsent_required")
    resp = signed(client, "GET", BRANDS, user.id, consented_at=revoked_epoch + 5)
    assert resp.status_code == 200, resp.text


def test_sign_out_other_devices_also_stamps_connector_revoked_at(env):
    from app.services.user_session_service import mint_session, revoke_all_for_user

    db, client = env
    user = _analyst(db, "others")
    keep = mint_session(db, user.id)
    revoke_all_for_user(db, user.id, except_session_id=keep.id)
    db.expire_all()
    assert db.get(User, user.id).connector_revoked_at is not None


def test_a_user_never_revoked_has_no_stamp(env):
    db, _client = env
    user = _analyst(db, "fresh")
    db.expire_all()
    assert db.get(User, user.id).connector_revoked_at is None


# ------------------------------------------------------------------ chat permission


@pytest.mark.parametrize(
    "method,url,body",
    [
        ("GET", f"{BASE}/datasets", None),
        ("GET", f"{BASE}/datasets/messages", None),
        ("POST", f"{BASE}/query", {"dataset": "messages"}),
        ("GET", f"{BASE}/messages/search?text=x", None),
        ("GET", f"{BASE}/conversation?contact=x", None),
    ],
)
def test_chat_routes_need_chat_history_view(env, method, url, body):
    """Q2 (a): the connector permission alone does not open chat data."""
    db, client = env
    user = make_user(db, "nochat", perms=(USE, BRANDS_VIEW))
    assert_error(signed(client, method, url, user.id, json_body=body), 403, "chat_history_not_permitted")
    assert signed(client, "GET", BRANDS, user.id).status_code == 200


# ------------------------------------------------------------------ route guard


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/user-management/users/me",
        "/api/v1/user-management/users/me/permissions",
        "/api/v1/auth/sessions",
        "/api/v1/user-management/contacts/",
        "/api/v1/inventory/stock/balance/export",
        "/api/v1/resource-management/attachments/current-stock-list",
        "/api/v1/scm/low-stock-report",
        f"{BRANDS}select",
        f"{BRANDS}x",
    ],
)
def test_a_get_outside_the_manifest_routes_is_route_not_allowed(env, path):
    db, client = env
    user = _analyst(db, "out")
    assert_error(signed(client, "GET", path, user.id), 403, "route_not_allowed")


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/auth/logout"),
        ("POST", BRANDS),
        ("PUT", f"{BRANDS}3f2b8c1e-5a47-4d0e-9c1a-0b6d2f7e8a91"),
        ("DELETE", f"{BRANDS}3f2b8c1e-5a47-4d0e-9c1a-0b6d2f7e8a91"),
        ("POST", "/api/v1/user-management/contacts/"),
    ],
)
def test_non_get_methods_outside_the_read_only_posts_are_refused(env, method, path):
    db, client = env
    user = _analyst(db, "write", extra=("master_data.brands.create", "master_data.brands.delete"))
    resp = signed(client, method, path, user.id, json_body={"name": "zzt"} if method != "DELETE" else None)
    assert_error(resp, 403, "route_not_allowed")


def test_the_read_only_posts_pass_the_route_guard(env):
    db, client = env
    user = _analyst(db, "ropost")
    # these are the only non-GET routes in the manifest (chat_query, crm_lookup_resolve)
    for path, body in ((f"{BASE}/query", {"dataset": "messages", "limit": 1}),
                       ("/api/v1/lookup/resolve", {"entity": "brand", "query": "zzt"})):
        resp = signed(client, "POST", path, user.id, json_body=body)
        assert error_code(resp) != "route_not_allowed", (path, resp.text)
        assert resp.status_code != 401, (path, resp.text)


@pytest.mark.parametrize(
    "raw",
    [
        "/api/v1/master-data/brands/../../user-management/users/me",
        "/api/v1/master-data/brands/%2e%2e/%2e%2e/user-management/users/me",
        "/api/v1/master-data%2fbrands/",
        "//api/v1/master-data/brands/",
    ],
)
def test_dot_segments_and_encoded_separators_cannot_walk_out(env, raw):
    db, client = env
    user = _analyst(db, "walk")
    resp = signed(client, "GET", raw, user.id)
    assert resp.status_code in (401, 403, 404), resp.text
    assert resp.status_code != 200


def test_impersonation_header_is_ignored(env):
    db, client = env
    admin, target = make_admin_impersonating(db, "imph", admin_perms=(USE, CHAT_VIEW, BRANDS_VIEW))
    resp = signed(client, "GET", DATASETS, admin.id, tool="chat_list_datasets",
                  extra_headers={"X-Impersonate-User-Id": target.id})
    assert resp.status_code == 200, resp.text
    rows = tool_call_rows(db, admin.id)
    assert len(rows) == 1
    assert rows[0].real_user_id in (None, admin.id)
    assert tool_call_rows(db, target.id) == []


# ------------------------------------------------------------------ audit


def test_every_served_call_writes_one_tool_call_event(env):
    db, client = env
    user = _analyst(db, "aud")
    grant = f"grant-{uuid.uuid4().hex[:8]}"
    assert signed(client, "GET", BRANDS, user.id, tool="crm_master_brands_list", grant_id=grant).status_code == 200
    rows = tool_call_rows(db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "EVENT"
    assert row.actor_type == "user"
    assert row.auth_method == "connector"
    assert row.source == "connector"
    assert row.new_values["tool"] == "crm_master_brands_list"
    assert row.new_values["grant_id"] == grant
    assert row.new_values["status"] in ("ok", 200, "200")
    assert "ms" in row.new_values

    assert signed(client, "GET", DATASETS, user.id, tool="chat_list_datasets", grant_id=grant).status_code == 200
    rows = tool_call_rows(db, user.id)
    assert len(rows) == 2 and rows[1].new_values["tool"] == "chat_list_datasets"


def test_a_call_refused_by_rbac_is_still_audited_with_its_status(env):
    db, client = env
    user = make_user(db, "audrb", perms=(USE,))
    assert signed(client, "GET", BRANDS, user.id, tool="crm_master_brands_list").status_code == 403
    rows = tool_call_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].new_values["status"] not in ("ok", 200, "200")


# ------------------------------------------------------------------ staff sessions untouched


def test_staff_sessions_still_work_on_reused_routes(env):
    from tests.analytics_connector._helpers import staff_headers

    db, client = env
    user = make_user(db, "staff", perms=(BRANDS_VIEW,))
    assert client.get(BRANDS, headers=staff_headers(db, user)).status_code == 200
    assert tool_call_rows(db, user.id) == []
