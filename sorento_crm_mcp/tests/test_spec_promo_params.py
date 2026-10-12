"""SPEC-PROMO-PARAMS: `spec_constraints` and `has_active_promotion` on the two area-lane tools.

Card: `documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` section 2.

RED today: neither ToolSpec declares either param, so FastMCP drops them (`extra="ignore"`) and
the backend never sees them. `spec_constraints` is a list of objects, so it must be typed as an
object param (`TOOL_OBJECT_QUERY_PARAMS`) or FastMCP's `pre_parse_json` turns the JSON string
into a list that a scalar union rejects.
"""
from __future__ import annotations

import contextlib
import inspect
import json

import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import TOOL_OBJECT_QUERY_PARAMS, _compile_tool, create_mcp_app
from sorento_crm_mcp.settings import Settings

TOOLS = ["crm_master_products_list", "crm_inventory_stock_balance_list"]

CONSTRAINTS = [
    {"key": "product_type", "op": "eq", "value": "floor_grating"},
    {"key": "dim_length", "op": "between", "value": [240, 260]},
]
EXPECTED_QUERY = json.dumps(CONSTRAINTS, separators=(",", ":"), sort_keys=True)


def _spec(name):
    matches = [s for s in CATALOG if s.name == name]
    assert len(matches) == 1, f"expected exactly one {name} entry in CATALOG"
    return matches[0]


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("param", ["spec_constraints", "has_active_promotion"])
def test_catalog_declares_param(tool, param):
    """Not in query_params -> never forwarded to the backend."""
    assert param in _spec(tool).query_params


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("param", ["spec_constraints", "has_active_promotion"])
def test_compiled_tool_signature_carries_param(tool, param):
    """A catalog tuple alone is not enough: the compiled signature must have it."""
    fn = getattr(_compile_tool(_spec(tool)), "fn", _compile_tool(_spec(tool)))
    assert param in inspect.signature(fn).parameters


@pytest.mark.parametrize("tool", TOOLS)
def test_spec_constraints_is_an_object_param(tool):
    """A list of objects survives pre_parse_json only as an object-typed param."""
    assert "spec_constraints" in TOOL_OBJECT_QUERY_PARAMS.get(tool, ())


@pytest.mark.parametrize("tool", TOOLS)
def test_description_tells_the_caller_about_both_params(tool):
    """Without the instruction the planner has the slot and never fills it."""
    description = _spec(tool).description
    assert "spec_constraints" in description
    assert "has_active_promotion" in description


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
        return json.dumps(
            {"data": [], "pagination": {"total": 0, "page": 1, "limit": 10}, "empty": True}
        )


@contextlib.contextmanager
def _request_context(client):
    token = request_ctx.set(
        RequestContext(
            request_id=1,
            meta=None,
            session=None,  # type: ignore[arg-type]
            lifespan_context={"client": client, "settings": _FakeSettings()},
        )
    )
    try:
        yield
    finally:
        request_ctx.reset(token)


async def _call(tool, arguments):
    app = create_mcp_app(Settings(CRM_BASE_URL="http://crm.local", EXTERNAL_API_KEY="k"))
    client = _CapturingClient()
    with _request_context(client):
        await app.call_tool(tool, arguments)
    return client.query


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOLS)
async def test_list_of_objects_reaches_the_backend_as_a_json_string(tool):
    """A native list of objects is accepted and sent as one compact JSON string."""
    query = await _call(tool, {"spec_constraints": CONSTRAINTS})
    assert query is not None and "spec_constraints" in query, query
    assert json.loads(query["spec_constraints"]) == CONSTRAINTS


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOLS)
async def test_json_string_survives_preparse_and_reaches_the_backend(tool):
    """FastMCP pre-parses a JSON string into a list before validation; it must still
    arrive at the backend as the JSON string its route parses."""
    query = await _call(tool, {"spec_constraints": EXPECTED_QUERY})
    assert query is not None and "spec_constraints" in query, query
    assert json.loads(query["spec_constraints"]) == CONSTRAINTS


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOLS)
async def test_has_active_promotion_reaches_the_backend_as_true(tool):
    """Booleans are stringified to lowercase for FastAPI's bool parser."""
    query = await _call(tool, {"has_active_promotion": True})
    assert query is not None and query.get("has_active_promotion") == "true", query
