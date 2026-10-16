"""SEC-MCP-AUTH (UAC SCOPE-1..6): a company-scoped tool call without a contact gets nothing.

The backend resolves an X-API-Key call with no `contact_id` + `space_id` to EVERY company
(`sorento_crm_backend/app/services/company_scope_resolver.py::_resolve_api_key_scope`). So a
scoped tool whose caller dropped the pair used to answer across the whole group. The MCP server
now refuses that call before any backend request. The staff AI assistant, which has no contact,
opts out explicitly with `X-MCP-Scope: staff`.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from sorento_crm_mcp import server
from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.module_loader import merged_catalog
from sorento_crm_mcp.scope import scope_refusal
from sorento_crm_mcp.server import _compile_tool, _execute_tool_request_with_body, create_http_app
from sorento_crm_mcp.settings import Settings

CONTACT = "900000008"
SPACE = "364817"
TOKEN = "x" * 40
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}",
}
BACKEND_ROWS = {"data": [{"id": "00000000-0000-0000-0000-0000000000b1", "name": "Row"}], "total": 1}

ALL_SPECS = list(merged_catalog(CATALOG))


def _is_scoped(spec) -> bool:
    declared = set(spec.query_params) | set(spec.body_params)
    return {"contact_id", "space_id"} <= declared


SCOPED = [s for s in ALL_SPECS if _is_scoped(s)]
UNSCOPED = [s for s in ALL_SPECS if not _is_scoped(s)]


def _spec(name):
    return next(s for s in ALL_SPECS if s.name == name)


class _Client:
    def __init__(self):
        self.calls: list[dict] = []

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        self.calls.append({"method": method, "path": path, "query": dict(query or {}), "body": body})
        return json.dumps(BACKEND_ROWS)

    async def get(self, path, path_params=None, query=None, tool_name=None):
        return await self.request("GET", path, path_params=path_params, query=query, tool_name=tool_name)


class _Request:
    def __init__(self, headers):
        self.headers = {k.lower(): v for k, v in headers.items()}


def _ctx(client, headers=None):
    settings = type("S", (), {"crm_base_url": "http://crm.local", "external_api_key": "k"})()
    rc = type(
        "RC",
        (),
        {
            "lifespan_context": {"client": client, "settings": settings},
            "request": _Request(headers or {}) if headers is not None else None,
        },
    )()
    return type("Ctx", (), {"request_context": rc})()


def _run(name, client, headers=None, **kwargs):
    impl = _compile_tool(_spec(name))
    return json.loads(asyncio.run(impl(_ctx(client, headers), **kwargs)))


@pytest.fixture(autouse=True)
def _no_escalation_network(monkeypatch):
    async def passthrough(_name, resp, **_kw):
        return resp

    monkeypatch.setattr(server, "attach_suggested_escalation", passthrough)


# --- SCOPE-6: the check reads the catalog -------------------------------------------------------


def test_the_catalog_has_scoped_tools():
    assert len(SCOPED) >= 30


@pytest.mark.parametrize("spec", SCOPED, ids=lambda s: s.name)
@pytest.mark.parametrize(
    "query",
    [{}, {"contact_id": CONTACT}, {"space_id": SPACE}, {"contact_id": "", "space_id": SPACE},
     {"contact_id": "  ", "space_id": "  "}],
)
def test_every_scoped_tool_refuses_without_both_ids(spec, query):
    refusal = scope_refusal(spec, query, None, staff=False)
    assert refusal is not None
    assert json.loads(refusal)["error"] == "scope_required"


@pytest.mark.parametrize("spec", SCOPED, ids=lambda s: s.name)
def test_every_scoped_tool_passes_with_both_ids(spec):
    assert scope_refusal(spec, {"contact_id": CONTACT, "space_id": SPACE}, None, staff=False) is None


@pytest.mark.parametrize("spec", UNSCOPED, ids=lambda s: s.name)
def test_global_tools_are_untouched(spec):
    assert scope_refusal(spec, {}, None, staff=False) is None


def test_body_scoped_tool_reads_the_body():
    spec = _spec("crm_lookup_resolve")
    assert scope_refusal(spec, {}, {"set_key": "x", "raw": "y"}, staff=False) is not None
    assert scope_refusal(spec, {}, {"contact_id": CONTACT, "space_id": SPACE}, staff=False) is None


def test_staff_opt_out_passes_an_unscoped_call():
    assert scope_refusal(_spec("crm_master_products_list"), {}, None, staff=True) is None


# --- SCOPE-1..4 through the compiled tool (the path every catalog call takes) --------------------


def test_unscoped_call_makes_no_backend_request():
    client = _Client()
    out = _run("crm_master_products_list", client)
    assert out["error"] == "scope_required"
    assert out["data"] == [] and out["total"] == 0
    assert client.calls == [], "a refused call must never reach the backend"


def test_blank_contact_is_refused_too():
    client = _Client()
    out = _run("crm_order_management_orders_list", client, contact_id="", space_id="")
    assert out["error"] == "scope_required"
    assert client.calls == []


def test_scoped_call_is_unchanged():
    """SCOPE-2: same result as today, i.e. the pre-change execution path's own answer."""
    client = _Client()
    out = _run("crm_order_management_orders_list", client, contact_id=CONTACT, space_id=SPACE)
    assert client.calls[0]["query"]["contact_id"] == CONTACT
    assert client.calls[0]["query"]["space_id"] == SPACE

    direct = _Client()
    today = asyncio.run(
        _execute_tool_request_with_body(
            _spec("crm_order_management_orders_list"),
            direct,
            {},
            {"contact_id": CONTACT, "space_id": SPACE},
            None,
        )
    )
    assert out == json.loads(today)


def test_global_tool_call_is_unchanged():
    client = _Client()
    _run("crm_forms_management_forms_list", client)
    assert len(client.calls) == 1


def test_staff_header_lets_the_assistant_through():
    client = _Client()
    _run("crm_master_products_list", client, headers={"X-MCP-Scope": "staff"})
    assert len(client.calls) == 1
    assert "contact_id" not in client.calls[0]["query"]


def test_any_other_scope_header_value_is_refused():
    client = _Client()
    out = _run("crm_master_products_list", client, headers={"X-MCP-Scope": "all"})
    assert out["error"] == "scope_required"
    assert client.calls == []


def test_lookup_resolve_without_contact_is_refused():
    client = _Client()
    out = _run("crm_lookup_resolve", client, set_key="product", raw="abc", locale="en")
    assert out["error"] == "scope_required"
    assert client.calls == []


# --- SCOPE-5: the products fan-out keeps the caller's scope -------------------------------------


def test_products_attachment_fanout_forwards_the_contact():
    client = _Client()
    _run(
        "crm_master_products_list",
        client,
        contact_id=CONTACT,
        space_id=SPACE,
        attachment_type_ids="6f1c2a4e-8b3d-4c5e-9f7a-1b2c3d4e5f60",
    )
    child = [c for c in client.calls if c["path"].endswith("/product-attachments")]
    assert child, "the fan-out should have run"
    assert child[0]["query"]["contact_id"] == CONTACT
    assert child[0]["query"]["space_id"] == SPACE


# --- end to end: the header reaches the tool through the real HTTP app --------------------------


def _call(client, name, arguments, extra_headers=None):
    payload = {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    resp = client.post("/mcp", json=payload, headers={**MCP_HEADERS, **(extra_headers or {})})
    assert resp.status_code == 200, resp.text
    text = resp.text
    if "text/event-stream" in resp.headers.get("content-type", ""):
        text = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")][-1]
    result = json.loads(text)["result"]
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def http_app(monkeypatch):
    calls: list[dict] = []

    async def fake_request(self, method, path, *, path_params=None, query=None, body=None, tool_name=None):
        calls.append({"path": path, "query": dict(query or {})})
        return json.dumps(BACKEND_ROWS)

    monkeypatch.setattr(server.CRMClient, "request", fake_request)
    settings = Settings(CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k", MCP_AUTH_TOKEN=TOKEN)
    with TestClient(create_http_app(settings)) as c:
        yield c, calls


def test_http_unscoped_call_is_refused(http_app):
    client, calls = http_app
    out = _call(client, "crm_master_brands_list", {})
    assert out["error"] == "scope_required"
    assert calls == []


def test_http_scoped_call_reaches_the_backend(http_app):
    client, calls = http_app
    _call(client, "crm_master_brands_list", {"contact_id": CONTACT, "space_id": SPACE})
    assert calls and calls[0]["query"]["contact_id"] == CONTACT


def test_http_staff_header_reaches_the_tool(http_app):
    client, calls = http_app
    _call(client, "crm_master_brands_list", {}, {"X-MCP-Scope": "staff"})
    assert len(calls) == 1


# --- crew conditions (16 Oct): the staff opt-out needs a valid bearer, and is logged -------------


NO_BEARER = {k: v for k, v in MCP_HEADERS.items() if k != "Authorization"}


def _http_app_in(monkeypatch, mode):
    calls: list[dict] = []

    async def fake_request(self, method, path, *, path_params=None, query=None, body=None, tool_name=None):
        calls.append({"path": path, "query": dict(query or {})})
        return json.dumps(BACKEND_ROWS)

    monkeypatch.setattr(server.CRMClient, "request", fake_request)
    settings = Settings(
        CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k", MCP_AUTH_TOKEN=TOKEN, MCP_AUTH_MODE=mode
    )
    return create_http_app(settings), calls


def _tools_call(name, arguments):
    return {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def test_staff_header_without_a_bearer_is_401(monkeypatch):
    app, calls = _http_app_in(monkeypatch, "enforce")
    with TestClient(app) as c:
        resp = c.post(
            "/mcp", json=_tools_call("crm_master_brands_list", {}), headers={**NO_BEARER, "X-MCP-Scope": "staff"}
        )
    assert resp.status_code == 401
    assert calls == []


def test_staff_header_with_a_wrong_bearer_is_401(monkeypatch):
    app, calls = _http_app_in(monkeypatch, "permit")
    with TestClient(app) as c:
        resp = c.post(
            "/mcp",
            json=_tools_call("crm_master_brands_list", {}),
            headers={**NO_BEARER, "Authorization": "Bearer nope", "X-MCP-Scope": "staff"},
        )
    assert resp.status_code == 401
    assert calls == []


def test_permit_mode_ignores_the_staff_header_without_a_bearer(monkeypatch):
    """Permit lets an unauthenticated call in, but never as staff: it still needs a contact."""
    app, calls = _http_app_in(monkeypatch, "permit")
    with TestClient(app) as c:
        resp = c.post(
            "/mcp", json=_tools_call("crm_master_brands_list", {}), headers={**NO_BEARER, "X-MCP-Scope": "staff"}
        )
    assert resp.status_code == 200, resp.text
    text = resp.text
    if "text/event-stream" in resp.headers.get("content-type", ""):
        text = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")][-1]
    out = json.loads(json.loads(text)["result"]["content"][0]["text"])
    assert out["error"] == "scope_required"
    assert calls == []


def test_bearer_without_the_staff_header_still_needs_a_contact(http_app):
    """A contact-facing caller (the chatbot lanes) holds the bearer but never sends the header."""
    client, calls = http_app
    out = _call(client, "crm_order_management_orders_list", {"contact_id": "", "space_id": ""})
    assert out["error"] == "scope_required"
    assert calls == []


def test_staff_scope_call_is_logged_with_tool_and_user(http_app, caplog):
    import logging

    client, _calls = http_app
    with caplog.at_level(logging.INFO, logger="sorento_crm_mcp.scope"):
        _call(
            client,
            "crm_master_brands_list",
            {},
            {"X-MCP-Scope": "staff", "X-MCP-Staff-User": "00000000-0000-0000-0000-0000000000d1"},
        )
    lines = [r.getMessage() for r in caplog.records if "staff-scope" in r.getMessage()]
    assert lines, "a staff opt-out must leave a log line"
    assert "tool=crm_master_brands_list" in lines[0]
    assert "staff_user=00000000-0000-0000-0000-0000000000d1" in lines[0]
    assert TOKEN not in caplog.text


def test_scoped_staff_call_with_a_contact_is_not_logged(http_app, caplog):
    import logging

    client, _calls = http_app
    with caplog.at_level(logging.INFO, logger="sorento_crm_mcp.scope"):
        _call(client, "crm_master_brands_list", {"contact_id": CONTACT, "space_id": SPACE}, {"X-MCP-Scope": "staff"})
    assert not [r for r in caplog.records if "staff-scope" in r.getMessage()]
