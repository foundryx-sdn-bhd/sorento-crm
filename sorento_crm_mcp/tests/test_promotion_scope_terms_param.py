"""PROMO-SUBJECT-FILTER AC-4: the promotions MCP tool exposes `scope_terms`.

The chatbot sends the grounded class phrases here; a tool that does not declare the
param drops it (FastMCP `extra="ignore"`) and the backend filter is never reached.
"""
import inspect

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.server import _compile_tool


def _spec():
    return next(s for s in CATALOG if s.name == "crm_marketing_promotions_list")


def test_promotions_tool_forwards_scope_terms_as_a_query_param():
    """AC-4: catalog entry forwards `scope_terms` to the backend."""
    assert "scope_terms" in _spec().query_params


def test_compiled_promotions_tool_accepts_scope_terms():
    """AC-4: the COMPILED tool signature carries it (a catalog tuple alone is not enough)."""
    tool = _compile_tool(_spec())
    fn = getattr(tool, "fn", tool)
    assert "scope_terms" in inspect.signature(fn).parameters
