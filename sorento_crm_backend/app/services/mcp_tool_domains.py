"""Tool -> chatbot domain mapping, as plain data, for services OUTSIDE the chatbot
package boundary (AC-002, `tests/chatbot/test_import_boundary.py`).

D15 first put this fact on the chatbot's own domain vocabulary (then
`contracts.DOMAIN_SPEC`, now the `chatbot_domains` table and its frozen seed)
and had `mcp_tool_registry_service.sync_catalog` invert it directly - which is core
importing the chatbot package, the one direction AC-002 forbids (D17, CI run on
bf8814585). This module is the fix: the mapping lives here, in `app/services/`, so
`sync_catalog` (and any other core service) can read it with no import across the
boundary.

**Nothing READS the stamped column today (8 Sep 2026).** Its one reader was the
chatbot's tool search, and that search is gone: the business lane picks the domain
row's own first tool outright (`turn.policy.Policy.domain(name).tools[0]`). Both the column and this mapping
stay because dropping them is a migration on a column production never populated, and
that is a follow-up with its own trigger (the next migration that touches
`mcp_tools`), not a reason to churn the table now.

`chatbot_domains.tools` (seeded from `turn/policy_rows.py`) stays the hand-authored,
richly-commented version the chatbot module itself reads (`CHATBOT_READ_ONLY_TOOLS`, the fetch lane's
per-tool tables) - moving those tuples here would strand the per-tool reasoning
comments they carry (e.g. why `crm_order_analytics` is excluded from `order`) next to
a flat dict that has no room for them. Chosen over deriving those tools from
this module because that is the smaller diff and loses nothing: this module's own
data is dumb by design, so nothing here needs the prose the seed already carries.
`tests/chatbot/test_domain_spec.py` is the guardrail that keeps the two from
drifting - one runtime fact, told twice, checked never to disagree.
"""
from __future__ import annotations

#: tool_name -> domain name (a `chatbot_domains.name`). Grouped by domain, in the same
#: order the seed declares them, so a diff against that dict's flattened form is
#: easy to eyeball. A tool in no domain is simply absent - it never enters a chatbot
#: pool, the same rule `mcp_tools.chatbot_domain` NULL encodes.
CHATBOT_TOOL_DOMAINS: dict[str, str] = {
    "crm_master_products_list": "master_products",
    "crm_master_brands_list": "master_products",
    "crm_master_product_categories_list": "master_products",
    "crm_master_units_of_measure_list": "master_products",
    "crm_master_product_attachments_list": "product_attachment",
    "crm_certificates_list": "product_attachment",
    "crm_marketing_promotions_list": "promotion",
    "crm_marketing_promotion_attachments_list": "promotion",
    "crm_marketing_promotion_products_list": "promotion",
    "crm_forms_management_forms_list": "forms",
    "crm_inventory_stock_balance_list": "inventory",
    "crm_inventory_warehouses_list": "inventory",
    # Same domain as the stock tools it sits beside (PLAN-low-stock-report S6, AC-62);
    # the low stock INTENT is what picks it, not the domain.
    "crm_low_stock_report": "inventory",
    "crm_order_management_orders_list": "order",
    "crm_order_management_orders_by_product_list": "order",
    "crm_outstanding_report": "order",
    "crm_master_customers_list": "order",
    # PLAN-prompt-dynamic-30sep D9 (owner, 30 Sep 2026): the sales figures are the `sales`
    # domain's (migration `pdyn_0001_status_words_sales`). The `order` seed row still lists
    # them as allow-list members; this map names the domain they answer FROM.
    "crm_sales_report": "sales",
    "crm_top_selling_report": "sales",
    "crm_sales_analysis": "sales",
    "crm_incoming_stock_list": "incoming",
    "crm_incoming_stock_by_product": "incoming",
    "crm_incoming_stock_shipments": "incoming",
    "crm_portal_link_get": "portal_link",
    "crm_resource_attachments_list": "resource_attachment",
    "crm_resource_attachments_catalogue": "resource_attachment",
    "crm_resource_attachments_current_stock_list": "resource_attachment",
    "crm_procurement_spo_allocations_last_receipt_list": "spo_allocation",
    "crm_procurement_po_placed_list": "purchase_order",
    "crm_procurement_po_last_cost_list": "purchase_cost",
}


def domain_of(tool_name: str) -> str | None:
    """The chatbot domain `tool_name` answers FROM, or `None` if it answers from none."""
    return CHATBOT_TOOL_DOMAINS.get(tool_name)
