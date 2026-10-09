"""CLAUDE-CONNECTOR S2: register / authorize / token forwarders (card section 3, steps 1, 2, 4, 5).

The container holds no OAuth state: it forwards to the backend routes pinned by
sorento_crm_backend/tests/analytics_connector/test_oauth_flow.py, with X-Analytics-Connector-Key.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from conftest import BE, INTERNAL_KEY

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "https://crm.example.com/analytics/mcp"
REGISTER = f"{BE}/oauth/register"
AUTHORIZE = f"{BE}/oauth/authorize"
TOKEN = f"{BE}/oauth/token"

AUTH_PARAMS = {
    "response_type": "code",
    "client_id": "client-1",
    "redirect_uri": CALLBACK,
    "code_challenge": "chal-abc",
    "code_challenge_method": "S256",
    "state": "st-123",
    "scope": "analytics.read",
    "resource": RESOURCE,
}


# --------------------------------------------------------------------------- register


def test_register_forwards_json_with_the_internal_key_and_passes_the_answer_through(client, backend):
    reply = {"client_id": "client-1", "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
    backend.on("POST", REGISTER, status=201, json_body=reply)
    payload = {"redirect_uris": [CALLBACK], "client_name": "Claude", "token_endpoint_auth_method": "none"}
    resp = client.post("/analytics/oauth/register", json=payload)
    assert resp.status_code == 201
    assert resp.json() == reply
    sent = backend.calls("POST", REGISTER)[0]
    assert sent.json == payload
    assert sent.headers["x-analytics-connector-key"] == INTERNAL_KEY
    assert "x-api-key" not in sent.headers


def test_register_passes_a_backend_400_through_unchanged(client, backend):
    backend.on("POST", REGISTER, status=400, json_body={"error": "invalid_redirect_uri"})
    resp = client.post("/analytics/oauth/register", json={"redirect_uris": ["https://evil.example.com/cb"]})
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_redirect_uri"}


def test_register_does_not_need_a_bearer(client, backend):
    backend.on("POST", REGISTER, status=201, json_body={"client_id": "c"})
    assert client.post("/analytics/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 201
    assert "authorization" not in backend.calls("POST", REGISTER)[0].headers


# --------------------------------------------------------------------------- token


def test_token_forwards_the_form_with_the_internal_key(client, backend):
    reply = {"access_token": "at", "token_type": "Bearer", "expires_in": 3600,
             "refresh_token": "rt", "scope": "analytics.read"}
    backend.on("POST", TOKEN, json_body=reply)
    form = {"grant_type": "authorization_code", "code": "c0de", "code_verifier": "v" * 50,
            "client_id": "client-1", "redirect_uri": CALLBACK, "resource": RESOURCE}
    resp = client.post("/analytics/oauth/token", data=form)
    assert resp.status_code == 200
    assert resp.json() == reply
    sent = backend.calls("POST", TOKEN)[0]
    assert sent.form == form
    assert sent.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert sent.headers["x-analytics-connector-key"] == INTERNAL_KEY
    assert "x-api-key" not in sent.headers


def test_token_response_is_not_cacheable(client, backend):
    backend.on("POST", TOKEN, json_body={"access_token": "at", "token_type": "Bearer"})
    resp = client.post("/analytics/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "rt",
                                                       "client_id": "client-1"})
    assert "no-store" in resp.headers["cache-control"]


def test_refresh_grant_is_forwarded_the_same_way(client, backend):
    backend.on("POST", TOKEN, json_body={"access_token": "at2", "token_type": "Bearer"})
    form = {"grant_type": "refresh_token", "refresh_token": "rt", "client_id": "client-1"}
    assert client.post("/analytics/oauth/token", data=form).status_code == 200
    assert backend.calls("POST", TOKEN)[0].form == form


def test_token_invalid_grant_status_and_body_pass_through(client, backend):
    backend.on("POST", TOKEN, status=400, json_body={"error": "invalid_grant"})
    resp = client.post("/analytics/oauth/token", data={"grant_type": "authorization_code", "code": "bad"})
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_grant"}


# --------------------------------------------------------------------------- authorize


def _is_connect_url(location: str, request_id: str) -> bool:
    parts = urlsplit(location)
    return (parts.path == "/connect/claude" and parts.query == f"request={request_id}"
            and parts.netloc in ("", "crm.example.com"))


def test_authorize_forwards_the_params_then_302s_to_the_consent_page(client, backend):
    backend.on("POST", AUTHORIZE, json_body={"request_id": "req-777"})
    resp = client.get("/analytics/oauth/authorize", params=AUTH_PARAMS)
    assert resp.status_code == 302
    assert _is_connect_url(resp.headers["location"], "req-777"), resp.headers["location"]
    sent = backend.calls("POST", AUTHORIZE)[0]
    assert sent.json == AUTH_PARAMS
    assert sent.headers["x-analytics-connector-key"] == INTERNAL_KEY
    assert "x-api-key" not in sent.headers


def test_authorize_needs_no_bearer_because_it_is_the_browser(client, backend):
    backend.on("POST", AUTHORIZE, json_body={"request_id": "req-1"})
    assert client.get("/analytics/oauth/authorize", params=AUTH_PARAMS).status_code == 302
    assert "authorization" not in backend.calls("POST", AUTHORIZE)[0].headers


@pytest.mark.parametrize("error,override", [
    ("invalid_client", {"client_id": "nope"}),
    ("invalid_redirect_uri", {"redirect_uri": "https://evil.example.com/cb"}),
])
def test_bad_client_or_redirect_is_an_error_page_and_never_a_redirect(client, backend, error, override):
    """No open redirect: the user-supplied redirect_uri must not become a Location."""
    backend.on("POST", AUTHORIZE, status=400, json_body={"error": error})
    resp = client.get("/analytics/oauth/authorize", params={**AUTH_PARAMS, **override})
    assert resp.status_code == 400
    assert "location" not in resp.headers
    assert resp.headers["content-type"].startswith("text/html")


def test_a_redirect_uri_the_backend_rejected_is_never_echoed_as_a_location(client, backend):
    backend.on("POST", AUTHORIZE, status=400, json_body={"error": "invalid_redirect_uri", "redirect_to": None})
    resp = client.get("/analytics/oauth/authorize",
                      params={**AUTH_PARAMS, "redirect_uri": "https://evil.example.com/cb"})
    assert resp.status_code == 400 and "location" not in resp.headers


def test_other_authorize_errors_redirect_back_to_the_redirect_the_backend_named(client, backend):
    back = f"{CALLBACK}?error=invalid_request&state=st-123"
    backend.on("POST", AUTHORIZE, status=400, json_body={"error": "invalid_request", "redirect_to": back})
    resp = client.get("/analytics/oauth/authorize", params={**AUTH_PARAMS, "code_challenge_method": "plain"})
    assert resp.status_code == 302
    assert resp.headers["location"] == back


def test_authorize_with_a_backend_failure_is_an_error_page_not_a_redirect(client, backend):
    backend.on("POST", AUTHORIZE, status=500, json_body={"detail": "boom"})
    resp = client.get("/analytics/oauth/authorize", params=AUTH_PARAMS)
    assert resp.status_code >= 400
    assert "location" not in resp.headers
