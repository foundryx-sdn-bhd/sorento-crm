"""MCP-LOCKDOWN: the CRM's MCP client sends `Authorization: Bearer <MCP_AUTH_TOKEN>`.

`MCPRuntimeClient` is the only in-repo MCP caller (AI assistant, chatbot business lane fetch,
ideate lane all construct it at `settings.ai_assistant_mcp_url`), so the header is set there once
rather than at each of its call sites. No database: the transport is an httpx MockTransport.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.services import ai_assistant_service
from app.services.ai_assistant_service import MCPRuntimeClient


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
    monkeypatch.setattr(settings, "mcp_auth_token", "secret-token-for-the-mcp-server-0123")
    MCPRuntimeClient("http://mcp:8765/mcp").list_tools()
    assert seen[0].headers["authorization"] == "Bearer secret-token-for-the-mcp-server-0123"


def test_no_token_configured_sends_no_header(monkeypatch, seen):
    monkeypatch.setattr(settings, "mcp_auth_token", "")
    MCPRuntimeClient("http://mcp:8765/mcp").call_tool("crm_x", {})
    assert "authorization" not in seen[0].headers


def test_the_token_setting_defaults_blank():
    from app.config import Settings

    assert Settings.model_fields["mcp_auth_token"].default == ""
