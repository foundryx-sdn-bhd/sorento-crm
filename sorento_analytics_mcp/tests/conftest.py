"""CLAUDE-CONNECTOR S2: shared set-up for the container tests.

Card: documentation/plans/analytics/CARD-claude-connector-09oct.md (sections 2, 3, 4, 5.1, 6, 8.3).

Seams these tests pin (the coder builds to them):

* ``sorento_analytics_mcp.app.create_app()`` takes no argument, reads its settings from the
  environment (``CRM_BASE_URL``, ``ANALYTICS_CONNECTOR_INTERNAL_KEY``,
  ``ANALYTICS_CONNECTOR_PUBLIC_BASE_URL``) and returns an ASGI app that serves ``/analytics/*``
  and the two exact ``/.well-known/...`` paths.
* ``sorento_analytics_mcp.tools.ANALYTICS_REUSED_TOOLS`` is the static frozenset of reused tool names.

The backend is faked with ``httpx.MockTransport`` (respx is not in the backend venv). Every
``httpx.AsyncClient`` the container creates is forced onto that transport, so ANY outgoing call,
including the escalation side call that makes its own client, lands in ``Backend.requests``.
Starlette's TestClient uses ``httpx.Client`` and is not affected.

Imports of the package are lazy (inside fixtures) so each test fails on its own.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl

import httpx
import pytest

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_REPO = _PROJECT.parent
# Use THIS checkout's sorento_crm_mcp, not whatever editable install the venv points at.
for _p in (str(_REPO / "sorento_crm_mcp"), str(_PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PUBLIC_BASE = "https://crm.example.com/analytics"
CRM_BASE = "http://backend:8000"
INTERNAL_KEY = "zzt-internal-key-for-tests-only"
SHARED_API_KEY = "ZZT-SHARED-EXTERNAL-API-KEY-MUST-NEVER-LEAVE"
BE = "/api/v1/analytics-connector"
ME = f"{BE}/me"
AUDIT = f"{BE}/audit"
ACCEPT = {"Accept": "application/json, text/event-stream"}

WWW_AUTHENTICATE = (
    f'Bearer resource_metadata="{PUBLIC_BASE}/.well-known/oauth-protected-resource", '
    'scope="analytics.read"'
)


@dataclass
class Rec:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes

    @property
    def json(self) -> Any:
        return json.loads(self.body or b"null")

    @property
    def form(self) -> dict[str, str]:
        return dict(parse_qsl(self.body.decode()))


@dataclass
class Backend:
    requests: list[Rec] = field(default_factory=list)
    routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = field(default_factory=dict)

    def on(self, method: str, path: str, *, status: int = 200, json_body: Any = None,
           content: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        def _resp(_req: httpx.Request) -> httpx.Response:
            if content is not None:
                return httpx.Response(status, content=content, headers=headers)
            if json_body is None:
                return httpx.Response(status, headers=headers)
            return httpx.Response(status, json=json_body, headers=headers)

        self.routes[(method.upper(), path)] = _resp

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(Rec(
            method=req.method,
            path=req.url.path,
            query=dict(parse_qsl(req.url.query.decode())),
            headers={k.lower(): v for k, v in req.headers.items()},
            body=req.content,
        ))
        route = self.routes.get((req.method, req.url.path))
        if route is None:
            return httpx.Response(404, json={"detail": f"unrouted {req.method} {req.url.path}"})
        return route(req)

    def calls(self, method: str | None = None, path: str | None = None) -> list[Rec]:
        return [r for r in self.requests if (method is None or r.method == method.upper())
                and (path is None or r.path == path)]


@pytest.fixture()
def backend(monkeypatch) -> Backend:
    be = Backend()
    be.on("GET", ME, json_body={"user_id": "11111111-1111-1111-1111-111111111111", "name": "ZZT User"})
    be.on("POST", AUDIT, status=204)
    original = httpx.AsyncClient.__init__

    def _init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(be.handle)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return be


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("CRM_BASE_URL", CRM_BASE)
    monkeypatch.setenv("ANALYTICS_CONNECTOR_INTERNAL_KEY", INTERNAL_KEY)
    monkeypatch.setenv("ANALYTICS_CONNECTOR_PUBLIC_BASE_URL", PUBLIC_BASE)
    # The container must run without the shared key: it is deliberately absent here.
    monkeypatch.delenv("EXTERNAL_API_KEY", raising=False)


@pytest.fixture()
def client(env, backend):
    from starlette.testclient import TestClient

    from sorento_analytics_mcp.app import create_app

    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def _sse_json(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


def rpc(client, method: str, params: dict | None = None, *, token: str = "tok-A", rid: int = 1):
    """One stateless JSON-RPC request to /analytics/mcp as ``token``. Returns the httpx response."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/analytics/mcp", json=body,
                       headers={**ACCEPT, "Authorization": f"Bearer {token}"})


def call_tool(client, name: str, arguments: dict | None = None, *, token: str = "tok-A") -> dict:
    """tools/call; returns {"text": <first text content>, "isError": bool}."""
    resp = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    assert resp.status_code == 200, resp.text
    result = _sse_json(resp.text)["result"]
    return {"text": result["content"][0]["text"], "isError": bool(result.get("isError"))}


def list_tools(client, *, token: str = "tok-A") -> list[dict]:
    resp = rpc(client, "tools/list", token=token)
    assert resp.status_code == 200, resp.text
    return _sse_json(resp.text)["result"]["tools"]


# The 40 reused tools, card section 4.2 (pinned here on purpose: the test is the contract,
# the code under test must not be able to widen it by editing its own list).
REUSED_40 = frozenset({
    "crm_master_products_list", "crm_master_brands_list", "crm_master_product_categories_list",
    "crm_master_units_of_measure_list", "crm_master_product_attachments_list", "crm_certificates_list",
    "crm_master_customers_list", "crm_lookup_resolve",
    "crm_inventory_stock_balance_list", "crm_inventory_warehouses_list", "crm_low_stock_report",
    "crm_incoming_stock_by_product", "crm_incoming_stock_shipments", "crm_incoming_stock_list",
    "crm_order_management_orders_list", "crm_order_management_orders_by_product_list",
    "crm_outstanding_report", "crm_sales_report", "crm_report_ask", "crm_top_selling_report",
    "crm_sales_analysis",
    "crm_procurement_po_placed_list", "crm_procurement_spo_allocations_last_receipt_list",
    "crm_procurement_po_last_cost_list",
    "crm_marketing_promotions_list", "crm_marketing_promotion_products_list",
    "crm_marketing_promotion_attachments_list", "crm_resource_attachments_list",
    "crm_resource_attachments_catalogue", "crm_resource_attachments_current_stock_list",
    "crm_projects_list", "crm_project_detail", "crm_project_quotations_list", "crm_project_forecast",
    "crm_complaints_list", "crm_complaint_analytics", "crm_forms_management_forms_list",
    "crm_sla_conversation_tracking_dashboard", "crm_sla_conversation_tracking_list",
    "crm_sla_conversation_event_logs_list",
})
EXCLUDED_FROM_43 = frozenset({"crm_portal_link_get", "user_guides_read", "crm_system_tool_capabilities_summary"})
WRITE_TOOLS = frozenset({
    "crm_order_cancel", "crm_purchase_request_approve", "crm_purchase_request_reject",
    "crm_complaint_close", "crm_it_support_ticket_create", "crm_ideation_turn",
})
CHAT_TOOLS = frozenset({
    "chat_list_datasets", "chat_describe_dataset", "chat_query",
    "chat_search_messages", "chat_get_conversation",
})
