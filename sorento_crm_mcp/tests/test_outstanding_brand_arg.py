"""Phase 2 RED tests - issue #1262 (the Samantha case), GROUP C brand slice, slice 9
(finding F1a), UAC `chatbot-samantha-slices-26sep-acceptance-criteria.md` AC-S9-4 /
AC-S9-6. Round 3 section 6 step 4: `crm_outstanding_report` (and the orders list /
by-product tools that already take `product_ids`, ruling 8) take a `brand_ids`
argument and forward it to the backend route; the header names the brand.

Written before any catalog entry declares `brand_ids`. Follows
`tests/test_catalog_outstanding.py`'s own pattern (catalog assertions) and
`tests/test_catalog_compile.py`'s fake-context pattern (forwarding).
"""
from __future__ import annotations

import json

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.presenters import _outstanding_header_lines
from sorento_crm_mcp.server import _compile_tool


class _FakeSettings:
    crm_base_url = "http://crm.local"
    external_api_key = "test-key"


class _FakeRC:
    def __init__(self, client):
        self.lifespan_context = {"client": client, "settings": _FakeSettings()}


class _FakeCtx:
    def __init__(self, client):
        self.request_context = _FakeRC(client)


class _RecordingClient:
    """`_compile_tool`'s generated body calls `client.request(method, path, ...)`
    (`server.py::_execute_tool_request`), never `client.get` directly - the same
    fake shape `test_catalog_compile.py::_FakeClient` uses. `is_active: true` keeps
    any promotion-activity precheck happy for tools this file does not exercise."""

    def __init__(self):
        self.calls: list[dict] = []

    async def get(self, path, path_params=None, query=None, tool_name=None):
        self.calls.append({"path": path, "query": dict(query or {})})
        return '{"has_result": false, "items": [], "is_active": true}'

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        self.calls.append({"path": path, "query": dict(query or {}), "method": method})
        return '{"has_result": false, "items": [], "is_active": true}'


def test_catalog_exposes_brand_ids_on_outstanding_report():
    spec = next(s for s in CATALOG if s.name == "crm_outstanding_report")
    assert "brand_ids" in spec.query_params, (
        f"crm_outstanding_report must expose brand_ids: {spec.query_params}"
    )


def test_catalog_exposes_brand_ids_on_order_list_tools():
    for name in (
        "crm_order_management_orders_list",
        "crm_order_management_orders_by_product_list",
    ):
        spec = next(s for s in CATALOG if s.name == name)
        assert "brand_ids" in spec.query_params, f"{name} must expose brand_ids: {spec.query_params}"


BRAND_UUID = "11111111-1111-4111-8111-111111111111"


async def _call_with_brand_ids(spec_name: str):
    spec = next(s for s in CATALOG if s.name == spec_name)
    fn = _compile_tool(spec)
    client = _RecordingClient()
    ctx = _FakeCtx(client)
    await fn(ctx, contact_id="900000008", space_id="364817", brand_ids=[BRAND_UUID])
    return client.calls


def test_mcp_outstanding_report_forwards_brand_ids():
    """`_compile_tool` has no `brand_ids` in its signature until the catalog declares
    it - calling with that kwarg raises a `TypeError` today, which is the RIGHT reason
    this is red (the argument genuinely does not exist yet)."""
    import asyncio

    calls = asyncio.run(
        _call_with_brand_ids("crm_outstanding_report")
    )
    assert calls, "no HTTP call was recorded"
    assert calls[0]["query"].get("brand_ids") == [BRAND_UUID], calls[0]


def test_mcp_orders_list_forwards_brand_ids():
    import asyncio

    calls = asyncio.run(
        _call_with_brand_ids("crm_order_management_orders_list")
    )
    assert calls, "no HTTP call was recorded"
    assert calls[0]["query"].get("brand_ids") == [BRAND_UUID], calls[0]


def test_header_names_the_brand_when_the_report_carries_one():
    report = {
        "product_code": None,
        "customer_name": None,
        "brand_name": "Sorento",
        "location_token": None,
        "warehouse_codes": [],
        "order_date_from": None,
        "order_date_to": None,
    }
    lines = _outstanding_header_lines(report)
    assert any(line == "Brand: Sorento" for line in lines), lines


def test_header_carries_no_brand_line_when_absent():
    """Guard: an ordinary (no-brand) report keeps today's fixed four lines, so this
    change is additive - no coder "helpfully" prints "Brand: all" on every reply."""
    report = {
        "product_code": "SRTWT7445",
        "customer_name": None,
        "location_token": None,
        "warehouse_codes": [],
        "order_date_from": None,
        "order_date_to": None,
    }
    lines = _outstanding_header_lines(report)
    assert not any(line.startswith("Brand:") for line in lines), lines
