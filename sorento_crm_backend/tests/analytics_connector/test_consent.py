"""CLAUDE-CONNECTOR r3: the consent hop (contract section 2, errors section 5).

`POST /api/v1/connector/consent/inspect|approve|deny {req}` with the staff session.

Resolutions pinned here (contract is silent):
* status codes: `bad_request`, `request_expired`, `request_used` answer 400;
  `connector_access_removed` and `impersonation_not_allowed` answer 403.
* inspect never consumes the `jti`; approve and deny do. inspect of a used request answers
  `request_used`, so the page can show the used variant.
* inspect, approve and deny echo `request_id` (the page form-POSTs it to SS and cannot read it from the
  opaque `req`). Approve: `{return_url, assertion, request_id}`; deny: `{return_url, request_id}`.
* inspect `expires_at` is ISO 8601 UTC of the request's `exp`.
* impersonation refuses inspect too (403 impersonation_not_allowed), so the page shows that variant first.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from jose import jwt

from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import (
    CONSENT,
    PRODUCT_TO_SS,
    SS_CALLBACK,
    USE,
    assert_error,
    client_for,
    consent_request,
    epoch,
    make_admin_impersonating,
    make_user,
    staff_headers,
)


@pytest.fixture()
def env(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            yield db, client


def _post(client, verb, req, headers):
    return client.post(f"{CONSENT}/{verb}", json={"req": req}, headers=headers)


# ------------------------------------------------------------------ inspect


def test_inspect_describes_the_request_for_a_permitted_user(env):
    db, client = env
    user = make_user(db, "insp", perms=(USE,))
    req, claims = consent_request()
    resp = _post(client, "inspect", req, staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_name"] == "Claude"
    assert body["redirect_host"] == "claude.ai"
    assert body["user_name"] == user.name
    assert body["allowed"] is True
    assert body["request_id"] == claims["request_id"]
    exp = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert int(exp.timestamp()) == claims["exp"]


def test_inspect_says_not_allowed_without_the_connector_permission(env):
    db, client = env
    user = make_user(db, "noperm")
    resp = _post(client, "inspect", consent_request()[0], staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["allowed"] is False


def test_inspect_says_not_allowed_for_an_admin_without_the_permission(env):
    """Q1 (a): no admin bypass for analytics.connector.use."""
    db, client = env
    admin = make_user(db, "adm", role_slug="admin")
    resp = _post(client, "inspect", consent_request()[0], staff_headers(db, admin))
    assert resp.status_code == 200, resp.text
    assert resp.json()["allowed"] is False


def test_inspect_needs_a_signed_in_user(env):
    _db, client = env
    resp = client.post(f"{CONSENT}/inspect", json={"req": consent_request()[0]})
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("verb", ["inspect", "approve", "deny"])
@pytest.mark.parametrize(
    "case",
    ["bad_signature", "wrong_aud", "wrong_typ", "wrong_iss", "wrong_connection", "foreign_return_url",
     "missing_request_id", "garbage", "product_secret"],
)
def test_a_malformed_or_foreign_request_is_bad_request(env, verb, case):
    db, client = env
    user = make_user(db, f"bad-{verb}", perms=(USE,))
    req = {
        "bad_signature": lambda: consent_request(secret="zzt-not-the-secret-at-all-000000000000")[0],
        "wrong_aud": lambda: consent_request(aud="sorento-connector-api")[0],
        "wrong_typ": lambda: consent_request(typ="connector_call")[0],
        "wrong_iss": lambda: consent_request(iss="someone-else")[0],
        "wrong_connection": lambda: consent_request(connection_id="zzt-other-connection")[0],
        "foreign_return_url": lambda: consent_request(return_url="https://evil.example.com/cb")[0],
        "missing_request_id": lambda: consent_request(drop=("request_id",))[0],
        "garbage": lambda: "not-a-jwt",
        # a token signed with the OTHER direction's secret must not verify (contract section 1)
        "product_secret": lambda: consent_request(secret=PRODUCT_TO_SS)[0],
    }[case]()
    assert_error(_post(client, verb, req, staff_headers(db, user)), 400, "bad_request")


def test_return_url_must_match_exactly_not_by_prefix(env):
    db, client = env
    user = make_user(db, "prefix", perms=(USE,))
    req, _ = consent_request(return_url=SS_CALLBACK + "/../../steal")
    assert_error(_post(client, "approve", req, staff_headers(db, user)), 400, "bad_request")


@pytest.mark.parametrize("verb", ["inspect", "approve", "deny"])
def test_an_expired_request_is_request_expired(env, verb):
    db, client = env
    user = make_user(db, f"exp-{verb}", perms=(USE,))
    t = epoch()
    req, _ = consent_request(iat=t - 700, exp=t - 100)
    assert_error(_post(client, verb, req, staff_headers(db, user)), 400, "request_expired")


def test_inspect_does_not_consume_the_request(env):
    db, client = env
    user = make_user(db, "twice", perms=(USE,))
    h = staff_headers(db, user)
    req, _ = consent_request()
    assert _post(client, "inspect", req, h).status_code == 200
    assert _post(client, "inspect", req, h).status_code == 200
    assert _post(client, "approve", req, h).status_code == 200


# ------------------------------------------------------------------ approve


def test_approve_returns_the_return_url_and_a_signed_assertion(env):
    db, client = env
    user = make_user(db, "ok", perms=(USE,))
    req, claims = consent_request()
    resp = _post(client, "approve", req, staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["return_url"] == SS_CALLBACK
    assert body["request_id"] == claims["request_id"]
    a = jwt.decode(body["assertion"], PRODUCT_TO_SS, algorithms=["HS256"], audience="foundryx-connector")
    assert set(a) == {"typ", "iss", "aud", "connection_id", "request_id", "sub", "name", "email", "iat", "exp", "jti"}
    assert a["typ"] == "connector_assertion"
    assert a["iss"] == "sorento"
    assert a["aud"] == "foundryx-connector"
    assert a["connection_id"] == claims["connection_id"]
    assert a["request_id"] == claims["request_id"]
    assert a["sub"] == user.id
    assert a["name"] == user.name and a["name"] != user.email
    assert a["email"] == user.email
    assert a["exp"] - a["iat"] == 120
    assert abs(a["iat"] - epoch()) < 30
    assert a["jti"] and a["jti"] != claims["jti"]


def test_the_assertion_is_not_signed_with_the_inbound_secret(env):
    """Two secrets so one leaked direction cannot forge the other (contract section 1)."""
    from jose import JWTError

    from tests.analytics_connector._helpers import SS_TO_PRODUCT

    db, client = env
    user = make_user(db, "dir", perms=(USE,))
    body = _post(client, "approve", consent_request()[0], staff_headers(db, user)).json()
    with pytest.raises(JWTError):
        jwt.decode(body["assertion"], SS_TO_PRODUCT, algorithms=["HS256"], audience="foundryx-connector")


def test_two_approvals_get_distinct_assertion_jtis(env):
    db, client = env
    user = make_user(db, "jti", perms=(USE,))
    h = staff_headers(db, user)
    a1 = jwt.get_unverified_claims(_post(client, "approve", consent_request()[0], h).json()["assertion"])
    a2 = jwt.get_unverified_claims(_post(client, "approve", consent_request()[0], h).json()["assertion"])
    assert a1["jti"] != a2["jti"]


def test_approve_twice_is_request_used(env):
    db, client = env
    user = make_user(db, "used", perms=(USE,))
    h = staff_headers(db, user)
    req, _ = consent_request()
    assert _post(client, "approve", req, h).status_code == 200
    assert_error(_post(client, "approve", req, h), 400, "request_used")
    assert_error(_post(client, "inspect", req, h), 400, "request_used")
    assert_error(_post(client, "deny", req, h), 400, "request_used")


def test_deny_then_approve_is_request_used(env):
    db, client = env
    user = make_user(db, "dthen", perms=(USE,))
    h = staff_headers(db, user)
    req, _ = consent_request()
    assert _post(client, "deny", req, h).status_code == 200
    assert_error(_post(client, "approve", req, h), 400, "request_used")


def test_a_used_jti_stays_used_for_another_user(env):
    db, client = env
    a = make_user(db, "ua", perms=(USE,))
    b = make_user(db, "ub", perms=(USE,))
    req, _ = consent_request()
    assert _post(client, "approve", req, staff_headers(db, a)).status_code == 200
    assert_error(_post(client, "approve", req, staff_headers(db, b)), 400, "request_used")


def test_approve_without_the_permission_is_connector_access_removed(env):
    db, client = env
    user = make_user(db, "deny-np")
    assert_error(_post(client, "approve", consent_request()[0], staff_headers(db, user)), 403,
                 "connector_access_removed")


def test_approve_by_an_admin_without_the_permission_is_refused(env):
    db, client = env
    admin = make_user(db, "adm2", role_slug="admin")
    assert_error(_post(client, "approve", consent_request()[0], staff_headers(db, admin)), 403,
                 "connector_access_removed")


def test_a_refused_approve_does_not_consume_the_request(env):
    """A user lacking the permission must not burn the request for the holder who opens it next."""
    db, client = env
    lacking = make_user(db, "burn", perms=())
    holder = make_user(db, "keep", perms=(USE,))
    req, _ = consent_request()
    assert _post(client, "approve", req, staff_headers(db, lacking)).status_code == 403
    assert _post(client, "approve", req, staff_headers(db, holder)).status_code == 200


def test_approve_needs_a_signed_in_user(env):
    _db, client = env
    assert client.post(f"{CONSENT}/approve", json={"req": consent_request()[0]}).status_code == 401


@pytest.mark.parametrize("verb", ["inspect", "approve"])
def test_impersonation_is_refused(env, verb):
    """An admin viewing as someone cannot consent on their behalf."""
    db, client = env
    admin, target = make_admin_impersonating(db, f"imp-{verb}")
    headers = {**staff_headers(db, admin), "X-Impersonate-User-Id": target.id}
    assert_error(_post(client, verb, consent_request()[0], headers), 403, "impersonation_not_allowed")


def test_approve_validates_the_body(env):
    db, client = env
    user = make_user(db, "val", perms=(USE,))
    resp = client.post(f"{CONSENT}/approve", json={}, headers=staff_headers(db, user))
    assert resp.status_code in (400, 422), resp.text


# ------------------------------------------------------------------ deny


def test_deny_returns_the_return_url_and_no_assertion(env):
    db, client = env
    user = make_user(db, "deny", perms=(USE,))
    req, claims = consent_request()
    resp = _post(client, "deny", req, staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["return_url"] == SS_CALLBACK
    assert body["request_id"] == claims["request_id"]
    assert "assertion" not in body


def test_deny_works_without_the_connector_permission(env):
    """The no-permission view offers Deny only, so Deny must not need the permission."""
    db, client = env
    user = make_user(db, "deny-nop")
    resp = _post(client, "deny", consent_request()[0], staff_headers(db, user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["return_url"] == SS_CALLBACK


def test_consent_is_refused_when_the_connection_is_not_configured(env, monkeypatch):
    """Fail closed: no secret or callback configured -> nothing verifies."""
    from tests.analytics_connector._helpers import pin_settings

    db, client = env
    user = make_user(db, "nocfg", perms=(USE,))
    req, _ = consent_request()
    pin_settings(monkeypatch, connector_ss_callback_url=None)
    resp = _post(client, "approve", req, staff_headers(db, user))
    assert resp.status_code >= 400 and resp.status_code != 500, resp.text
    assert "assertion" not in resp.text
