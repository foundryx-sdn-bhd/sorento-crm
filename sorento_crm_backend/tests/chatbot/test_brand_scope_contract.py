"""Phase 2 RED test - AC-16: every tool has a brand-scope treatment.

`BRAND_SCOPE_TREATMENT` (`app.services.chatbot.contracts`) must name every tool of
`CHATBOT_READ_ONLY_TOOLS` and of the MCP `CATALOG` as `filtered` or `no_products`. A tool added
without a treatment turns this red. Pattern of `test_customer_scope_fetch.py`
`test_customer_scoped_tools_pinned_to_catalogue`.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS
from app.services.mcp_tool_capability_service import _load_catalog_specs

ALLOWED = {"filtered", "no_products"}

#: Tools that return product rows by construction: a `no_products` mark on one is a hole.
MUST_BE_FILTERED = (
    "crm_inventory_stock_balance_list",
    "crm_master_products_list",
    "crm_lookup_resolve",
    "crm_incoming_stock_by_product",
    "crm_incoming_stock_shipments",
    "crm_incoming_stock_list",
    "crm_order_management_orders_list",
    "crm_order_management_orders_by_product_list",
    "crm_outstanding_report",
    "crm_sales_report",
    "crm_top_selling_report",
    "crm_sales_analysis",
    "crm_low_stock_report",
    "crm_resource_attachments_current_stock_list",
    "crm_master_product_attachments_list",
)

#: Tools that never return a product.
NO_PRODUCTS = ("crm_portal_link_get", "user_guides_read", "crm_inventory_warehouses_list")


def _treatment() -> dict[str, str]:
    from app.services.chatbot.contracts import BRAND_SCOPE_TREATMENT

    return BRAND_SCOPE_TREATMENT


def untreated(names, treatment) -> list[str]:
    """The check itself, so a test can prove it goes red on a gap."""
    return sorted(n for n in names if treatment.get(n) not in ALLOWED)


def test_every_chatbot_read_only_tool_has_a_treatment() -> None:
    assert untreated(CHATBOT_READ_ONLY_TOOLS, _treatment()) == []


def test_every_mcp_catalogue_tool_has_a_treatment() -> None:
    names = {spec.name for spec in _load_catalog_specs()}
    assert names, "the MCP catalogue is empty: the pin is stale"
    assert untreated(names, _treatment()) == []


def test_treatment_names_no_tool_that_does_not_exist() -> None:
    """A typo in a key would leave the real tool untreated and the typo harmless-looking."""
    known = {spec.name for spec in _load_catalog_specs()} | set(CHATBOT_READ_ONLY_TOOLS)
    assert sorted(set(_treatment()) - known) == []


def test_the_check_goes_red_when_a_tool_lacks_a_treatment() -> None:
    """AC-16: the guard fails on a tool missing from the map and on an unknown value."""
    base = dict(_treatment())
    assert untreated(["crm_brand_new_tool"], base) == ["crm_brand_new_tool"]
    some = next(iter(base))
    assert untreated([some], {**base, some: "maybe"}) == [some]
    assert untreated([some], {k: v for k, v in base.items() if k != some}) == [some]


@pytest.mark.parametrize("tool", MUST_BE_FILTERED)
def test_product_returning_tools_are_filtered(tool) -> None:
    assert _treatment().get(tool) == "filtered", tool


@pytest.mark.parametrize("tool", NO_PRODUCTS)
def test_product_free_tools_are_marked_no_products(tool) -> None:
    assert _treatment().get(tool) == "no_products", tool
