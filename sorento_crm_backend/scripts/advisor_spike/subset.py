"""Tool subset for the advisor (CHAT-ADVISOR-SPIKE contract). Pure except for the ADVISOR_READ_ONLY import."""
from __future__ import annotations

from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS

ADVISOR_READ_ONLY: frozenset[str] = frozenset(CHATBOT_READ_ONLY_TOOLS) | {"crm_find"}

ADVISOR_COMPANIONS: dict[str, list[str]] = {
    "inventory": [
        "crm_incoming_stock_by_product",
        "crm_marketing_promotion_products_list",
        "crm_find",
    ],
    "master_products": [
        "crm_inventory_stock_balance_list",
        "crm_find",
        "crm_marketing_promotion_products_list",
    ],
    "promotion": ["crm_inventory_stock_balance_list"],
    "incoming": ["crm_inventory_stock_balance_list"],
    "order": ["crm_inventory_stock_balance_list"],
}


def select_tools(
    domains: list[str],
    *,
    domain_tools: dict[str, list[str]],
    read_only: frozenset[str],
    max_tools: int = 6,
) -> list[str]:
    known = [d for d in domains if d in domain_tools]
    ordered: list[str] = []
    for d in known:
        ordered.extend(domain_tools[d])
    for d in known:
        ordered.extend(ADVISOR_COMPANIONS.get(d, []))
    out: list[str] = []
    for name in ordered:
        if name in read_only and name not in out:
            out.append(name)
    return out[:max_tools]
