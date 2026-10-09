"""MCP-LOCKDOWN: every MCP request carries `Authorization: Bearer <MCP_AUTH_TOKEN>`.

The server used to accept anyone who could reach the port (FastMCP built with no auth,
published on the host's public IP through nginx :8765), and it exposes write tools
(crm_order_cancel, crm_purchase_request_approve, ...). `create_http_app` is the one ASGI app
`__main__` serves; these tests drive it in-process through Starlette's TestClient, which runs
the lifespan so FastMCP's session manager is live, exactly as uvicorn does.
"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from sorento_crm_mcp.server import create_http_app
from sorento_crm_mcp.settings import Settings

TOKEN = "t0k3n-for-tests-0123456789abcdef-0123"
LIST_TOOLS = {"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _settings(**overrides) -> Settings:
    values = {"CRM_BASE_URL": "http://crm.local", "EXTERNAL_API_KEY": "k", "MCP_AUTH_TOKEN": TOKEN}
    values.update(overrides)
    return Settings(**values)


def _tool_names(resp) -> list[str]:
    """The JSON-RPC result, whether FastMCP answered as JSON or as one SSE frame."""
    text = resp.text
    if "text/event-stream" in resp.headers.get("content-type", ""):
        text = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")][-1]
    return [t["name"] for t in json.loads(text)["result"]["tools"]]


@pytest.fixture
def client():
    with TestClient(create_http_app(_settings())) as c:
        yield c


def test_no_token_is_401(client):
    resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").lower().startswith("bearer")
    assert "crm_" not in resp.text, "a refused request must not leak the tool list"


def test_wrong_token_is_401(client):
    resp = client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_token_as_another_scheme_is_401(client):
    resp = client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": f"Basic {TOKEN}"})
    assert resp.status_code == 401


def test_token_with_a_suffix_is_401(client):
    resp = client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}x"})
    assert resp.status_code == 401


def test_right_token_lists_the_tools(client):
    resp = client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200, resp.text
    names = _tool_names(resp)
    assert "crm_ideation_turn" in names
    assert len(names) > 10


def test_scheme_is_case_insensitive(client):
    resp = client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": f"bearer {TOKEN}"})
    assert resp.status_code == 200


def test_other_paths_need_the_token_too(client):
    assert client.get("/mcp").status_code == 401
    assert client.get("/anything-else").status_code == 401


def test_healthz_needs_no_token(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


@pytest.mark.parametrize("token", [None, "", "   ", "short"])
def test_server_refuses_to_start_without_a_real_token(token):
    overrides = {"MCP_AUTH_TOKEN": token} if token is not None else {}
    settings = _settings(**overrides) if token is not None else Settings(
        CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k"
    )
    with pytest.raises(RuntimeError, match="MCP_AUTH_TOKEN"):
        create_http_app(settings)


def test_allowed_hosts_rejects_a_foreign_host_header():
    app = create_http_app(_settings(CRM_MCP_ALLOWED_HOSTS="mcp:8765, 127.0.0.1:*"))
    auth = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    with TestClient(app, base_url="http://evil.example") as c:
        assert c.post("/mcp", json=LIST_TOOLS, headers=auth).status_code == 421
    with TestClient(app, base_url="http://mcp:8765") as c:
        assert c.post("/mcp", json=LIST_TOOLS, headers=auth).status_code == 200
    with TestClient(app, base_url="http://127.0.0.1:8766") as c:
        assert c.post("/mcp", json=LIST_TOOLS, headers=auth).status_code == 200
