"""CLAUDE-CONNECTOR S2: the reused catalog tools (card sections 4.1 and 4.2).

Allow-list source: CHATBOT_READ_ONLY_TOOLS in sorento_crm_backend/app/services/chatbot/lanes/business/fetch.py.
The container image has no backend, so the 37 names are PINNED in conftest.REUSED_TOOLS (the card's table);
the drift test below also reads the live backend set when the backend is importable (it skips otherwise,
and CI's backend job runs it with the backend present). A pinned copy keeps the container tests free of
a backend dependency; the live read stops the pinned copy and the backend set drifting silently.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import CHAT_TOOLS, EXCLUDED_FROM_43, REMOVED_TOOLS, REUSED_TOOLS, WRITE_TOOLS, list_tools


def _reused():
    from sorento_analytics_mcp.tools import ANALYTICS_REUSED_TOOLS

    return ANALYTICS_REUSED_TOOLS


def test_allow_list_is_exactly_the_pinned_37():
    assert _reused() == REUSED_TOOLS
    assert isinstance(_reused(), frozenset)
    assert len(_reused()) == 37


def test_allow_list_has_no_write_tool_and_none_of_the_three_excluded():
    assert not (_reused() & WRITE_TOOLS)
    assert not (_reused() & EXCLUDED_FROM_43)


def test_every_allowed_tool_is_in_the_catalog_and_read_only():
    from sorento_crm_mcp.catalog import CATALOG
    from sorento_crm_mcp.module_loader import merged_catalog

    by_name = {t.name: t for t in merged_catalog(CATALOG)}
    for name in _reused():
        spec = by_name.get(name)
        assert spec is not None, f"{name} missing from the catalog"
        assert spec.method.upper() == "GET" or spec.read_only is True, f"{name} is not read-only"
        assert not spec.external, f"{name} is external (calls a third party with a server key)"
        assert not spec.internal, f"{name} is internal (admin only)"


def test_allow_list_is_a_subset_of_the_chatbot_read_only_set_live(monkeypatch):
    backend_dir = Path(__file__).resolve().parents[2] / "sorento_crm_backend"
    sys.path.insert(0, str(backend_dir))
    monkeypatch.chdir(backend_dir)  # the backend Settings read .env from the working directory
    try:
        from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS
    except Exception as exc:  # backend deps or env absent in the container CI image
        pytest.skip(f"backend not importable here: {exc!r}")
    finally:
        sys.path.remove(str(backend_dir))
    assert _reused() <= CHATBOT_READ_ONLY_TOOLS


def test_registered_tools_are_the_37_plus_the_five_chat_tools(client):
    names = {t["name"] for t in list_tools(client)}
    assert names == REUSED_TOOLS | CHAT_TOOLS
    assert not (names & WRITE_TOOLS)
    assert not (names & EXCLUDED_FROM_43)


def test_every_tool_is_annotated_read_only_and_non_destructive(client):
    tools = list_tools(client)
    assert len(tools) == 42
    for t in tools:
        ann = t.get("annotations") or {}
        assert ann.get("readOnlyHint") is True, t["name"]
        assert ann.get("destructiveHint") is False, t["name"]


def test_reused_tools_keep_the_n8n_parameters(client):
    """Compiled with the same _compile_tool: the brand list still takes its catalog query params."""
    tool = next(t for t in list_tools(client) if t["name"] == "crm_master_brands_list")
    props = set(tool["inputSchema"].get("properties", {}))
    assert {"page", "limit", "brand_ids", "product_ids"} <= props


def test_listing_tools_needs_a_valid_token(client, backend):
    from conftest import ACCEPT

    resp = client.post("/analytics/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=ACCEPT)
    assert resp.status_code == 401


def test_the_three_removed_tools_are_not_allowed_and_not_listed(client):
    """crm_low_stock_report writes (report run + WhatsApp send) on GET; the other two are chatbot-only (403 for staff)."""
    assert REMOVED_TOOLS == {"crm_low_stock_report", "crm_report_ask", "crm_sales_analysis"}
    assert not (_reused() & REMOVED_TOOLS)
    assert not ({t["name"] for t in list_tools(client)} & REMOVED_TOOLS)
