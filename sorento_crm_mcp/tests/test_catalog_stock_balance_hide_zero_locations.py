"""SET-STOCK-RENDER: the stock balance tool forwards `hide_zero_locations` to the backend
route as a query param when given, and sends nothing when it is not."""
from __future__ import annotations

import json

import pytest

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import _compile_tool

TOOL = "crm_inventory_stock_balance_list"


def spec():
    matches = [s for s in CATALOG if s.name == TOOL]
    assert len(matches) == 1
    return matches[0]


class _FakeSettings:
    crm_base_url = "http://crm.local"
    external_api_key = "test-key"


class _CapturingClient:
    def __init__(self):
        self.query = None

    async def get(self, path, path_params=None, query=None, tool_name=None):
        return await self.request("GET", path, path_params, query, None, tool_name)

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        self.query = dict(query or {})
        return json.dumps({"data": [], "pagination": {"total": 0, "page": 1, "limit": 10}, "empty": True})


class _FakeCtx:
    def __init__(self, client):
        self.request_context = type(
            "_RC", (), {"lifespan_context": {"client": client, "settings": _FakeSettings()}}
        )()


def test_catalog_declares_hide_zero_locations_param():
    assert "hide_zero_locations" in spec().query_params


@pytest.mark.asyncio
async def test_hide_zero_locations_true_reaches_the_backend_query():
    client = _CapturingClient()
    await _compile_tool(spec())(
        _FakeCtx(client), contact_id="1", space_id="s", hide_zero_locations=True  # type: ignore[arg-type]
    )
    assert client.query is not None
    assert str(client.query["hide_zero_locations"]).lower() == "true", client.query


@pytest.mark.asyncio
async def test_hide_zero_locations_is_not_sent_when_not_given():
    client = _CapturingClient()
    await _compile_tool(spec())(_FakeCtx(client), contact_id="1", space_id="s")  # type: ignore[arg-type]
    assert client.query is not None
    assert "hide_zero_locations" not in client.query, client.query
