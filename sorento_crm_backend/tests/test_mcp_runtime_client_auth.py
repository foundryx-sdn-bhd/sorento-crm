"""SEC-MCP-AUTH (UAC AUTH-7, SCOPE-4): the CRM's MCP client sends the bearer token, and only the
staff AI assistant marks its calls `X-MCP-Scope: staff`.

`MCPRuntimeClient` is the only in-repo MCP caller (AI assistant, chatbot business lane, ideate
lane), so the token is set there once. No database: the transport is an httpx MockTransport.
"""
from __future__ import annotations

import inspect
import json
import re

import httpx
import pytest

from app.config import settings
from app.services import ai_assistant_service
from app.services.ai_assistant_service import MCPRuntimeClient

TOKEN = "x" * 40  # fake fixture, built at runtime so it never looks like a real key


@pytest.fixture
def seen(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}})

    real_client = httpx.Client

    def client_with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ai_assistant_service.httpx, "Client", client_with_mock)
    return requests


def test_sends_the_bearer_token(monkeypatch, seen):
    monkeypatch.setattr(settings, "mcp_auth_token", TOKEN)
    MCPRuntimeClient("http://mcp:8765/mcp").list_tools()
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_no_token_configured_sends_no_header(monkeypatch, seen):
    monkeypatch.setattr(settings, "mcp_auth_token", "")
    MCPRuntimeClient("http://mcp:8765/mcp").call_tool("crm_x", {})
    assert "authorization" not in seen[0].headers


def test_the_token_setting_defaults_blank():
    from app.config import Settings

    assert Settings.model_fields["mcp_auth_token"].default == ""


def test_a_token_with_a_trailing_newline_is_stripped(monkeypatch, seen):
    monkeypatch.setattr(settings, "mcp_auth_token", TOKEN + "\n")
    MCPRuntimeClient("http://mcp:8765/mcp").list_tools()
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_default_client_sends_no_staff_marker(seen):
    MCPRuntimeClient("http://mcp:8765/mcp").call_tool("crm_x", {})
    assert "x-mcp-scope" not in seen[0].headers


def test_staff_client_sends_the_staff_marker(seen):
    MCPRuntimeClient("http://mcp:8765/mcp", staff=True).call_tool("crm_x", {})
    assert seen[0].headers["x-mcp-scope"] == "staff"


def _constructions(module) -> list[str]:
    src = inspect.getsource(module)
    return re.findall(r"MCPRuntimeClient\((.*?)\)\n", src, flags=re.S)


def test_every_ai_assistant_client_is_staff():
    calls = _constructions(ai_assistant_service)
    assert len(calls) >= 5
    for args in calls:
        assert "staff=True" in args, args


def test_no_chatbot_lane_client_is_staff():
    from app.services.chatbot.lanes import ideate
    from app.services.chatbot.lanes.business import services

    for module in (ideate, services):
        calls = _constructions(module)
        assert calls, module.__name__
        for args in calls:
            assert "staff" not in args, (module.__name__, args)
