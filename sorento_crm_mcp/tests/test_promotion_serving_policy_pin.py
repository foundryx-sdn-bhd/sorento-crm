"""The promo-serving tools are hard-pinned to the backend's per-type policy.

`serving_policy=true` goes out on every call and is deliberately NOT a tool
parameter: the agent must not be able to switch the policy off and re-introduce
an expired special that nobody will honour.
"""
import json

import pytest
from mcp.server.fastmcp.tools import Tool

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import (
    TOOL_DEFAULT_QUERY_PARAMS,
    TOOL_REQUIRED_NARROWING_FILTERS,
    _compile_tool,
)

PROMO_SERVING_TOOLS = (
    "crm_marketing_promotions_list",
    "crm_marketing_promotion_products_list",
    "crm_marketing_promotion_attachments_list",
)

_PROMO_UUID = "11111111-1111-4111-8111-111111111111"


def _spec(name):
    return next(spec for spec in CATALOG if spec.name == name)


class _FakeSettings:
    crm_base_url = "http://crm.local"
    external_api_key = "test-key"


class _CapturingClient:
    """Records the query the compiled tool actually sends to the CRM."""

    def __init__(self):
        self.query = None

    async def get(self, path, path_params=None, query=None, tool_name=None):
        return await self.request("GET", path, path_params, query, None, tool_name)

    async def request(self, method, path, path_params=None, query=None, body=None, tool_name=None):
        self.query = dict(query or {})
        return json.dumps(
            {
                "data": [],
                "pagination": {"total": 0, "page": 1, "limit": 10},
                "empty": True,
                "fallback_used": False,
                "is_active": True,
            }
        )


class _FakeCtx:
    def __init__(self, client):
        self.request_context = type(
            "_RC",
            (),
            {"lifespan_context": {"client": client, "settings": _FakeSettings()}},
        )()


def test_every_promo_serving_tool_pins_the_policy():
    for name in PROMO_SERVING_TOOLS:
        assert TOOL_DEFAULT_QUERY_PARAMS.get(name, {}).get("serving_policy") == "true", name


def test_the_policy_is_not_an_agent_facing_parameter():
    for name in PROMO_SERVING_TOOLS:
        assert "serving_policy" not in _spec(name).query_params, name


@pytest.mark.parametrize("name", PROMO_SERVING_TOOLS)
def test_the_registered_tool_schema_offers_no_way_to_switch_the_policy_off(name):
    """The schema handed to the agent is what bounds what it can ask for."""
    spec = _spec(name)
    tool = Tool.from_function(_compile_tool(spec), name=spec.name, description=spec.description)
    properties = tool.parameters.get("properties", {})
    assert properties, name
    assert "serving_policy" not in properties, name


@pytest.mark.asyncio
@pytest.mark.parametrize("name", PROMO_SERVING_TOOLS)
async def test_the_call_carries_the_policy_to_the_backend(name):
    """Pinned means sent: a plain call must reach the CRM with the policy on."""
    spec = _spec(name)
    fn = _compile_tool(spec)
    client = _CapturingClient()
    kwargs = {p: _PROMO_UUID for p in spec.path_params}
    needed = TOOL_REQUIRED_NARROWING_FILTERS.get(name)
    if needed:
        kwargs.setdefault(needed[0], _PROMO_UUID)
    kwargs.update(contact_id="900000008", space_id="364817")

    await fn(_FakeCtx(client), **kwargs)  # type: ignore[arg-type]

    assert client.query is not None, name
    assert client.query.get("serving_policy") == "true", name
