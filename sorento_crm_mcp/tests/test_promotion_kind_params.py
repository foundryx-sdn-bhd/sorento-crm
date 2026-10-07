"""PROMO-SUBJECT-FILTER AC-20: the promotions MCP tool exposes `promotion_types` and `brand_ids`."""
import inspect

import pytest

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import _compile_tool


def _spec():
    return next(s for s in CATALOG if s.name == "crm_marketing_promotions_list")


@pytest.mark.parametrize("param", ["promotion_types", "brand_ids"])
def test_catalog_forwards_the_param(param):
    """AC-20: forwarded as a query param."""
    assert param in _spec().query_params


@pytest.mark.parametrize("param", ["promotion_types", "brand_ids"])
def test_compiled_tool_accepts_the_param(param):
    """AC-20: the COMPILED tool signature carries it."""
    tool = _compile_tool(_spec())
    assert param in inspect.signature(getattr(tool, "fn", tool)).parameters
