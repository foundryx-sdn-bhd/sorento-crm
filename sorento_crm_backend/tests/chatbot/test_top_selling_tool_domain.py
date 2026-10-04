"""AC-1941 (PLAN-chatbot-top-x-hot-selling-24sep, slice S3): the backend half of the
MCP tool's wiring.

`crm_top_selling_report` is claimed by the `order` domain (so it is a member of
`fetch.CHATBOT_READ_ONLY_TOOLS`, the egress allow-list), never as `tools[0]` (the pick
is S4's override in `run_fetch`); `mcp_tool_domains` maps it to `order`; the migration
that puts it on a live `chatbot_domains` row agrees with the frozen seed; and the in-app
assistant bootstrap does not carry it. The catalogue-equality half
(`test_tool_pool_is_read_only.py`) and the reveal-key half
(`test_field_reveal_keys_pinned_to_catalog.py`) are the existing guardrails.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL = "crm_top_selling_report"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def test_order_domain_claims_the_tool_but_not_first() -> None:
    from app.services.chatbot.turn.policy import default_policy

    tools = default_policy().domain("order").tools
    assert TOOL in tools, tools
    assert tools[0] != TOOL, tools


def test_tool_is_in_the_read_only_allow_list() -> None:
    from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS

    assert TOOL in CHATBOT_READ_ONLY_TOOLS


def test_mcp_tool_domain_is_sales() -> None:
    # R7 (PLAN-prompt-dynamic-30sep D9): the ranking answers from the `sales` domain.
    from app.services.mcp_tool_domains import CHATBOT_TOOL_DOMAINS

    assert CHATBOT_TOOL_DOMAINS.get(TOOL) == "sales"


def test_migration_appends_the_tool_to_the_order_row() -> None:
    spec = importlib.util.spec_from_file_location("_top_selling_tool", _VERSIONS / "chatbot_top_selling_tool.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TOOL == TOOL
    assert len(module.revision) <= 32


def test_bootstrap_replays_the_migration() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_env.py").read_text()
    assert "chatbot_top_selling_tool.py" in source


def test_not_enabled_for_the_in_app_assistant() -> None:
    import app.main as main_mod

    assert "top_selling" not in Path(main_mod.__file__).read_text()
