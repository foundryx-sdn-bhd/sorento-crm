"""Fix, 7 Sep 2026: `include_specs` / `include_sellable` / `include_pipeline` are opt-in
from the CALLER, not a default this shared MCP server injects for everybody.

`crm_inventory_stock_balance_list`, `crm_master_products_list` and
`crm_order_management_orders_list` are called through THIS ONE server by both n8n
(production business logic) and the CRM's own chatbot lane. `TOOL_DEFAULT_QUERY_PARAMS`
used to default `include_sellable` / `include_specs` ON here, which cannot tell those two
callers apart - so n8n started getting `open_so_qty` / `sellable` on every stock row and a
spec wall on every product row, with only the CRM's own renderer honouring
`restricted_fields`. n8n prints fields as given.

A call with the OLD parameter set - none of the three new params - must reach the backend
with a query dict that is EXACTLY what it would have been before this plan touched these
tools: the three keys simply absent, never present-and-false. That is the byte-identity
the owner asked to be proven, and the cheapest way to prove it is here, at the query this
server actually sends, rather than by diffing JSON against a captured fixture.

`documentation/plans/chatbot/PLAN-chatbot-growth-r1.md` Slice A;
`app/services/chatbot/lanes/business/fetch.py::entity_ids_transformer` is the CRM-side
half - it is what OPTS IN now, tested in the backend's own
`tests/chatbot/test_growth_fix_opt_in_envelope_fields.py`.
"""
from __future__ import annotations

import json

import pytest

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import TOOL_DEFAULT_QUERY_PARAMS, _compile_tool

_TOOLS = (
    "crm_inventory_stock_balance_list",
    "crm_master_products_list",
    "crm_order_management_orders_list",
)


def _spec(name):
    return next(spec for spec in CATALOG if spec.name == name)


class _FakeSettings:
    crm_base_url = "http://crm.local"
    external_api_key = "test-key"


class _CapturingClient:
    """Records the query the compiled tool actually sends to the backend."""

    def __init__(self):
        self.query = None

    async def get(self, path, path_params=None, query=None, tool_name=None):
        return await self.request("GET", path, path_params, query, None, tool_name)

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        self.query = dict(query or {})
        return json.dumps(
            {"data": [], "pagination": {"total": 0, "page": 1, "limit": 10}, "empty": True}
        )


class _FakeCtx:
    def __init__(self, client):
        self.request_context = type(
            "_RC",
            (),
            {"lifespan_context": {"client": client, "settings": _FakeSettings()}},
        )()


@pytest.mark.parametrize("name", _TOOLS)
def test_no_server_level_default_for_the_three_new_params(name):
    """The regression itself, guarded directly: NOTHING in this server's own defaults
    dict may inject `include_sellable` / `include_specs` / `include_pipeline` for any
    tool - a server-level default cannot tell the CRM's own turn apart from n8n's,
    which is exactly what made this a production incident waiting to happen."""
    defaults = TOOL_DEFAULT_QUERY_PARAMS.get(name, {})
    assert "include_sellable" not in defaults, name
    assert "include_specs" not in defaults, name
    assert "include_pipeline" not in defaults, name


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _TOOLS)
async def test_old_parameter_set_reaches_the_backend_with_none_of_the_new_keys(name):
    """A call with the parameter set every caller sent before this plan - no
    `include_sellable`, `include_specs` or `include_pipeline` - must still reach the
    backend with none of the three present. Byte-identical to `origin/main`'s query
    shape on the same call, because the keys are simply ABSENT, never sent false."""
    spec = _spec(name)
    fn = _compile_tool(spec)
    client = _CapturingClient()

    await fn(_FakeCtx(client), contact_id="900000008", space_id="364817")  # type: ignore[arg-type]

    assert client.query is not None, name
    for key in ("include_sellable", "include_specs", "include_pipeline"):
        assert key not in client.query, f"{name}: {key} leaked into a call that never asked for it"
