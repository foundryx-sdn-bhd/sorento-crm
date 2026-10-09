"""CLAUDE-CONNECTOR S2: the auth gate, discovery documents and healthz (card sections 3 and 8.3)."""
from __future__ import annotations

import pytest

from conftest import ACCEPT, BE, INTERNAL_KEY, ME, PUBLIC_BASE, WWW_AUTHENTICATE

MCP_BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


# --------------------------------------------------------------------------- 401 gate


def test_no_token_is_401_with_exact_www_authenticate_and_no_backend_call(client, backend):
    resp = client.post("/analytics/mcp", json=MCP_BODY, headers=ACCEPT)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == WWW_AUTHENTICATE
    assert backend.requests == []


def test_get_without_token_is_also_401(client):
    resp = client.get("/analytics/mcp", headers={"Accept": "text/event-stream"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == WWW_AUTHENTICATE


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "token abc", "abc"])
def test_a_non_bearer_authorization_is_401_before_any_backend_call(client, backend, header):
    resp = client.post("/analytics/mcp", json=MCP_BODY, headers={**ACCEPT, "Authorization": header})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == WWW_AUTHENTICATE
    assert backend.requests == []


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_backend_me_non_200_becomes_401_with_the_header(client, backend, status):
    backend.on("GET", ME, status=status, json_body={"detail": "no"})
    resp = client.post("/analytics/mcp", json=MCP_BODY,
                       headers={**ACCEPT, "Authorization": "Bearer dead-token"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == WWW_AUTHENTICATE
    # The MCP SDK never ran: nothing but the /me probe went to the backend.
    assert [r.path for r in backend.requests] == [ME]


def test_gate_probe_sends_the_callers_bearer_and_the_internal_key(client, backend):
    resp = client.post("/analytics/mcp", json=MCP_BODY,
                       headers={**ACCEPT, "Authorization": "Bearer tok-A"})
    assert resp.status_code == 200, resp.text
    probe = backend.calls("GET", ME)[0]
    assert probe.headers["authorization"] == "Bearer tok-A"
    assert probe.headers["x-analytics-connector-key"] == INTERNAL_KEY
    assert "x-api-key" not in probe.headers


def test_the_gate_probes_on_every_request(client, backend):
    for _ in range(3):
        assert client.post("/analytics/mcp", json=MCP_BODY,
                           headers={**ACCEPT, "Authorization": "Bearer tok-A"}).status_code == 200
    assert len(backend.calls("GET", ME)) == 3


# --------------------------------------------------------------------------- RFC 9728

PROTECTED_RESOURCE = {
    "resource": f"{PUBLIC_BASE}/mcp",
    "authorization_servers": [PUBLIC_BASE],
    "scopes_supported": ["analytics.read"],
    "bearer_methods_supported": ["header"],
}


@pytest.mark.parametrize("path", [
    "/analytics/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/analytics/mcp",
])
def test_protected_resource_metadata_at_both_paths(client, backend, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == PROTECTED_RESOURCE
    assert backend.requests == []


# --------------------------------------------------------------------------- RFC 8414

AS_PATHS = [
    "/.well-known/oauth-authorization-server/analytics",
    "/analytics/.well-known/oauth-authorization-server",
    "/analytics/.well-known/openid-configuration",
]


@pytest.mark.parametrize("path", AS_PATHS)
def test_authorization_server_metadata_fields(client, backend, path):
    resp = client.get(path)
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["issuer"] == PUBLIC_BASE
    assert doc["authorization_endpoint"] == f"{PUBLIC_BASE}/oauth/authorize"
    assert doc["token_endpoint"] == f"{PUBLIC_BASE}/oauth/token"
    assert doc["registration_endpoint"] == f"{PUBLIC_BASE}/oauth/register"
    assert doc["response_types_supported"] == ["code"]
    assert doc["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert doc["token_endpoint_auth_methods_supported"] == ["none"]
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["scopes_supported"] == ["analytics.read"]
    assert backend.requests == []


@pytest.mark.parametrize("path", AS_PATHS)
def test_cimd_is_not_advertised(client, path):
    """CIMD would make the server fetch an arbitrary URL during /authorize (card section 3, step 1)."""
    doc = client.get(path).json()
    assert not doc.get("client_id_metadata_document_supported")


def test_the_three_as_documents_are_identical(client):
    docs = [client.get(p).json() for p in AS_PATHS]
    assert docs[0] == docs[1] == docs[2]


# --------------------------------------------------------------------------- healthz


def test_healthz_is_200_without_auth_and_without_a_backend_call(client, backend):
    resp = client.get("/analytics/healthz")
    assert resp.status_code == 200
    assert backend.requests == []
