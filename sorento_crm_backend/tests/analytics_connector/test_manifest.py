"""CLAUDE-CONNECTOR r3: the tool manifest SS registers from (contract section 3, card 4.2/4.3).

`GET /api/v1/connector/manifest`, signed like any call (`sub` may be empty), returns
`{version, tools: [{name, title, description, input_schema, annotations, http: {method, path_template,
query_params, path_params}}]}`: 34 reused read tools + 5 chat tools.

Resolutions pinned here:
* `path_template` is the backend's REAL route path (trailing slash included where the route has one,
  e.g. `/api/v1/master-data/brands/`), so SS never hits a 307 redirect that would break `req_hash`.
* the guard's exact list is `app.services.analytics_connector.access.CONNECTOR_ROUTES`, a collection of
  `(METHOD, path_template)`; it equals the manifest routes plus `("GET", "/api/v1/connector/manifest")`.
* the backend image has no `sorento_crm_mcp`, so the manifest is built from a static table in the
  backend; this test cross-checks it against the repo's catalog when that package is importable.
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

import pytest

from tests._pg_fixture import blank_session
from tests.analytics_connector._helpers import (
    BASE,
    CHAT_TOOL_ROUTES,
    CHAT_VIEW,
    USE,
    assert_error,
    call_token,
    client_for,
    error_code,
    make_user,
    signed,
)

MANIFEST = f"{BASE}/manifest"

# Copied from sorento_analytics_mcp/tests/conftest.py, which is deleted with that package (r3).
REUSED_TOOLS = frozenset({
    "crm_master_products_list", "crm_master_brands_list", "crm_master_product_categories_list",
    "crm_master_units_of_measure_list", "crm_master_product_attachments_list", "crm_certificates_list",
    "crm_master_customers_list", "crm_lookup_resolve",
    "crm_inventory_stock_balance_list", "crm_inventory_warehouses_list",
    "crm_incoming_stock_by_product", "crm_incoming_stock_shipments", "crm_incoming_stock_list",
    "crm_order_management_orders_list", "crm_order_management_orders_by_product_list",
    "crm_outstanding_report", "crm_sales_report", "crm_top_selling_report",
    "crm_procurement_po_placed_list", "crm_procurement_spo_allocations_last_receipt_list",
    "crm_procurement_po_last_cost_list",
    "crm_marketing_promotions_list", "crm_marketing_promotion_products_list",
    "crm_marketing_promotion_attachments_list", "crm_resource_attachments_list",
    "crm_resource_attachments_catalogue",
    "crm_projects_list", "crm_project_detail", "crm_project_quotations_list", "crm_project_forecast",
    "crm_complaint_analytics", "crm_forms_management_forms_list",
    "crm_sla_conversation_tracking_dashboard", "crm_sla_conversation_event_logs_list",
})
CHAT_TOOLS = frozenset(CHAT_TOOL_ROUTES)
REMOVED_TOOLS = frozenset({
    "crm_low_stock_report", "crm_report_ask", "crm_sales_analysis",
    "crm_complaints_list", "crm_resource_attachments_current_stock_list", "crm_sla_conversation_tracking_list",
})
EXCLUDED_TOOLS = frozenset({"crm_portal_link_get", "user_guides_read", "crm_system_tool_capabilities_summary"})
WRITE_TOOLS = frozenset({
    "crm_order_cancel", "crm_purchase_request_approve", "crm_purchase_request_reject",
    "crm_complaint_close", "crm_it_support_ticket_create", "crm_ideation_turn",
})
SIGNED_URL_TOOLS = frozenset({"crm_certificates_list", "crm_resource_attachments_list", "crm_resource_attachments_catalogue"})
READ_ONLY_POSTS = {("POST", f"{BASE}/query"), ("POST", "/api/v1/lookup/resolve")}


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/"))


@pytest.fixture()
def env(monkeypatch):
    with blank_session() as db:
        with client_for(db, monkeypatch) as client:
            yield db, client


@pytest.fixture()
def manifest(env):
    _db, client = env
    resp = signed(client, "GET", MANIFEST, "", tool="")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tools(manifest) -> dict:
    return {t["name"]: t for t in manifest["tools"]}


def test_manifest_lists_exactly_the_34_reused_and_5_chat_tools(manifest):
    assert len(REUSED_TOOLS) == 34 and len(CHAT_TOOLS) == 5
    names = [t["name"] for t in manifest["tools"]]
    assert len(names) == len(set(names)) == 39
    assert set(names) == REUSED_TOOLS | CHAT_TOOLS


def test_manifest_has_a_version(manifest):
    assert isinstance(manifest["version"], str) and manifest["version"]


def test_removed_excluded_and_write_tools_are_absent(manifest):
    names = set(_tools(manifest))
    assert not names & REMOVED_TOOLS
    assert not names & EXCLUDED_TOOLS
    assert not names & WRITE_TOOLS


def test_every_tool_is_annotated_read_only_and_described(manifest):
    for t in manifest["tools"]:
        assert t["annotations"]["readOnlyHint"] is True, t["name"]
        assert t["annotations"]["destructiveHint"] is False, t["name"]
        assert t["title"] and t["description"], t["name"]
        assert isinstance(t["input_schema"], dict) and t["input_schema"].get("type") == "object", t["name"]


def test_every_tool_carries_its_http_binding(manifest):
    for t in manifest["tools"]:
        http = t["http"]
        assert http["method"] in ("GET", "POST"), t["name"]
        assert http["path_template"].startswith("/api/v1/"), t["name"]
        assert isinstance(http["query_params"], list) and isinstance(http["path_params"], list), t["name"]
        in_template = re.findall(r"\{([^}]+)\}", http["path_template"])
        assert sorted(in_template) == sorted(http["path_params"]), t["name"]
        props = set(t["input_schema"].get("properties", {}))
        assert set(http["path_params"]) <= props, t["name"]
        assert set(http["query_params"]) <= props, t["name"]


def test_only_the_two_read_only_posts_are_posts(manifest):
    posts = {(t["http"]["method"], t["http"]["path_template"]) for t in manifest["tools"] if t["http"]["method"] != "GET"}
    assert posts == READ_ONLY_POSTS
    by = _tools(manifest)
    assert by["chat_query"]["http"]["method"] == "POST"
    assert by["crm_lookup_resolve"]["http"]["method"] == "POST"


def test_chat_tools_bind_to_the_connector_routes(manifest):
    by = _tools(manifest)
    for name, (method, path) in CHAT_TOOL_ROUTES.items():
        assert by[name]["http"]["method"] == method, name
        assert _norm(by[name]["http"]["path_template"]) == _norm(path), name


def test_no_tool_offers_resolve_signed_urls(manifest):
    """No presigned URLs (a credential) ever leave through the connector."""
    for t in manifest["tools"]:
        assert "resolve_signed_urls" not in t["http"]["query_params"], t["name"]
        assert "resolve_signed_urls" not in t["input_schema"].get("properties", {}), t["name"]


def test_every_manifest_route_is_a_real_backend_route(manifest):
    from app.main import app

    real = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}
    for t in manifest["tools"]:
        key = (t["http"]["method"], t["http"]["path_template"])
        assert key in real, (t["name"], key)


def test_manifest_routes_and_the_guard_list_are_the_same_set(manifest):
    from app.services.analytics_connector.access import CONNECTOR_ROUTES

    from_manifest = {(t["http"]["method"], t["http"]["path_template"]) for t in manifest["tools"]}
    guard = {(m.upper(), p) for m, p in CONNECTOR_ROUTES}
    assert guard == from_manifest | {("GET", MANIFEST)}


def test_reused_tools_bind_to_the_catalog_routes():
    """The manifest's reused routes are the n8n catalog's routes for those names."""
    repo = Path(__file__).resolve().parents[3]
    if str(repo / "sorento_crm_mcp") not in sys.path:
        sys.path.insert(0, str(repo / "sorento_crm_mcp"))
    try:
        from sorento_crm_mcp.catalog import CATALOG
        from sorento_crm_mcp.module_loader import merged_catalog
    except Exception as exc:  # pragma: no cover - the package ships in this repo
        pytest.skip(f"sorento_crm_mcp not importable: {exc!r}")
    from app.services.analytics_connector.manifest import build_manifest

    by_catalog = {t.name: t for t in merged_catalog(CATALOG)}
    tools = {t["name"]: t for t in build_manifest()["tools"]}
    for name in REUSED_TOOLS:
        spec = by_catalog[name]
        assert spec.method.upper() == "GET" or spec.read_only, name
        assert tools[name]["http"]["method"] == spec.method.upper(), name
        assert _norm(tools[name]["http"]["path_template"]) == _norm(spec.path), name


def test_every_manifest_route_passes_the_route_guard(env, manifest):
    """Signed calls on each tool route are not refused by the guard (RBAC/422 may still answer)."""
    db, client = env
    user = make_user(db, "allroutes", perms=(USE, CHAT_VIEW))
    for t in manifest["tools"]:
        path = t["http"]["path_template"]
        for p in t["http"]["path_params"]:
            path = path.replace("{" + p + "}", "messages" if t["name"] == "chat_describe_dataset" else str(uuid.uuid4()))
        body = {"dataset": "messages", "limit": 1} if t["name"] == "chat_query" else (
            {"entity": "brand", "query": "zzt"} if t["http"]["method"] == "POST" else None)
        resp = signed(client, t["http"]["method"], path, user.id, tool=t["name"], json_body=body)
        assert resp.status_code != 401, (t["name"], resp.text)
        assert error_code(resp) != "route_not_allowed", (t["name"], resp.text)
        assert resp.status_code != 405, (t["name"], resp.text)


# ------------------------------------------------------------------ manifest auth


def test_manifest_needs_a_signed_call(env):
    _db, client = env
    assert client.get(MANIFEST).status_code == 401


def test_manifest_refuses_a_bad_signature(env):
    _db, client = env
    resp = signed(client, "GET", MANIFEST, "", secret="zzt-wrong-secret-000000000000000000000000")
    assert_error(resp, 401, "invalid_signature")


def test_manifest_token_is_bound_to_the_manifest_route(env):
    _db, client = env
    tok = call_token("", "GET", MANIFEST)
    resp = signed(client, "GET", f"{BASE}/datasets", "", token=tok)
    assert resp.status_code == 401, resp.text


def test_an_empty_sub_is_only_good_for_the_manifest(env):
    _db, client = env
    resp = signed(client, "GET", f"{BASE}/datasets", "")
    assert resp.status_code in (401, 403), resp.text
