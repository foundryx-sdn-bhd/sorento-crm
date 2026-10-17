"""SEC-MCP-AUTH (UAC AUTH-1..6): every MCP request carries `Authorization: Bearer <MCP_AUTH_TOKEN>`.

The server used to accept anyone who could reach :8765 and acted with the shared CRM key, write
tools included. `create_http_app` is the one ASGI app `__main__` serves; these tests drive it
in-process through Starlette's TestClient, which runs the lifespan so FastMCP's session manager is
live, exactly as uvicorn does. Ported from PR #1548 (MCP-LOCKDOWN) plus the permit mode.
"""
from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

from sorento_crm_mcp.server import create_http_app
from sorento_crm_mcp.settings import Settings

TOKEN = "x" * 40  # fake fixture, built at runtime so it never looks like a real key
LIST_TOOLS = {"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
AUTH = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}


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


@pytest.fixture
def permit_client():
    with TestClient(create_http_app(_settings(MCP_AUTH_MODE="permit"))) as c:
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
    resp = client.post("/mcp", json=LIST_TOOLS, headers=AUTH)
    assert resp.status_code == 200, resp.text
    names = _tool_names(resp)
    assert "crm_master_products_list" in names
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


@pytest.mark.parametrize("token", [None, "", "   ", "short", "a" * 31, " " + "a" * 31 + " "])
@pytest.mark.parametrize("mode", ["enforce", "permit"])
def test_server_refuses_to_start_without_a_real_token(token, mode):
    if token is None:
        settings = Settings(CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k", MCP_AUTH_MODE=mode)
    else:
        settings = _settings(MCP_AUTH_TOKEN=token, MCP_AUTH_MODE=mode)
    with pytest.raises(RuntimeError, match="MCP_AUTH_TOKEN") as err:
        create_http_app(settings)
    if token and token.strip():
        assert token.strip() not in str(err.value), "the error text must not echo the token"


def test_an_unknown_mode_refuses_to_start():
    with pytest.raises(RuntimeError, match="MCP_AUTH_MODE"):
        create_http_app(_settings(MCP_AUTH_MODE="off"))


def test_a_32_char_token_is_enough():
    with TestClient(create_http_app(_settings(MCP_AUTH_TOKEN="b" * 32))) as c:
        resp = c.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": "Bearer " + "b" * 32})
    assert resp.status_code == 200


def test_websocket_is_refused(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/mcp") as ws:
            ws.receive_text()


def test_the_compare_is_constant_time():
    import inspect

    from sorento_crm_mcp import auth

    assert "hmac.compare_digest" in inspect.getsource(auth)


# --- permit mode (one release, so callers move over without an outage) -------------------------


def test_permit_lets_a_call_with_no_header_through_and_warns(permit_client, caplog):
    with caplog.at_level(logging.WARNING, logger="sorento_crm_mcp.auth"):
        resp = permit_client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS)
    assert resp.status_code == 200, resp.text
    assert any("permit mode" in r.getMessage() for r in caplog.records)


def test_permit_still_refuses_a_wrong_token(permit_client):
    resp = permit_client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_permit_right_token_passes_without_a_warning(permit_client, caplog):
    with caplog.at_level(logging.WARNING, logger="sorento_crm_mcp.auth"):
        resp = permit_client.post("/mcp", json=LIST_TOOLS, headers=AUTH)
    assert resp.status_code == 200
    assert not [r for r in caplog.records if "permit mode" in r.getMessage()]


def test_no_log_line_carries_the_token(client, permit_client, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/mcp", json=LIST_TOOLS, headers=AUTH)
        client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}x"})
        permit_client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS)
        permit_client.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": "Bearer nope"})
    assert TOKEN not in caplog.text
    assert "nope" not in caplog.text
