"""Red tests for scripts.advisor_spike.subset (CHAT-ADVISOR-SPIKE contract). Pure, no DB."""
from scripts.advisor_spike import subset
from scripts.advisor_spike.subset import (
    ADVISOR_COMPANIONS,
    ADVISOR_READ_ONLY,
    select_tools,
)

DOMAIN_TOOLS = {
    "inventory": [
        "crm_inventory_stock_balance_list",
        "crm_inventory_warehouses_list",
        "crm_low_stock_report",
    ],
    "promotion": [
        "crm_marketing_promotions_list",
        "crm_marketing_promotion_attachments_list",
        "crm_marketing_promotion_products_list",
    ],
    "master_products": [
        "crm_master_products_list",
        "crm_master_brands_list",
        "crm_master_product_categories_list",
        "crm_master_units_of_measure_list",
    ],
    "incoming": [
        "crm_incoming_stock_list",
        "crm_incoming_stock_by_product",
        "crm_incoming_stock_shipments",
    ],
}
ALL = frozenset(t for v in DOMAIN_TOOLS.values() for t in v) | {
    "crm_find",
    "crm_inventory_stock_balance_list",
}


def sel(domains, tools=None, ro=ALL, **kw):
    return select_tools(
        domains, domain_tools=tools or DOMAIN_TOOLS, read_only=ro, **kw
    )


def test_constants_exist():
    assert isinstance(ADVISOR_COMPANIONS, dict)
    assert ADVISOR_COMPANIONS["inventory"] == [
        "crm_incoming_stock_by_product",
        "crm_marketing_promotion_products_list",
        "crm_find",
    ]
    assert ADVISOR_COMPANIONS["master_products"] == [
        "crm_inventory_stock_balance_list",
        "crm_find",
        "crm_marketing_promotion_products_list",
    ]
    assert ADVISOR_COMPANIONS["promotion"] == ["crm_inventory_stock_balance_list"]
    assert ADVISOR_COMPANIONS["incoming"] == ["crm_inventory_stock_balance_list"]
    assert ADVISOR_COMPANIONS["order"] == ["crm_inventory_stock_balance_list"]


def test_advisor_read_only_contents():
    assert "crm_find" in ADVISOR_READ_ONLY
    assert "crm_order_cancel" not in ADVISOR_READ_ONLY
    assert isinstance(ADVISOR_READ_ONLY, frozenset)


def test_primary_then_companions_order():
    out = sel(["inventory"], max_tools=20)
    assert out == [
        "crm_inventory_stock_balance_list",
        "crm_inventory_warehouses_list",
        "crm_low_stock_report",
        "crm_incoming_stock_by_product",
        "crm_marketing_promotion_products_list",
        "crm_find",
    ]


def test_dedup_keeps_first_occurrence():
    out = sel(["promotion", "inventory"], max_tools=50)
    assert len(out) == len(set(out))
    assert out.index("crm_marketing_promotion_products_list") == 2
    assert out.index("crm_inventory_stock_balance_list") == 3  # first as inventory primary


def test_multi_domain_primaries_before_companions():
    out = sel(["inventory", "incoming"], max_tools=50)
    assert out[:6] == [
        "crm_inventory_stock_balance_list",
        "crm_inventory_warehouses_list",
        "crm_low_stock_report",
        "crm_incoming_stock_list",
        "crm_incoming_stock_by_product",
        "crm_incoming_stock_shipments",
    ]
    assert out[6:] == ["crm_marketing_promotion_products_list", "crm_find"]


def test_domain_order_respected():
    a = sel(["incoming", "inventory"], max_tools=50)
    assert a[0] == "crm_incoming_stock_list"


def test_read_only_filter_drops_write_tools():
    tools = {"order": ["crm_order_list", "crm_order_cancel"]}
    out = sel(["order"], tools=tools, ro=frozenset({"crm_order_list"}), max_tools=20)
    assert "crm_order_cancel" not in out
    assert out == ["crm_order_list"]


def test_read_only_filter_with_real_constant_drops_cancel():
    tools = {"order": ["crm_order_cancel", "crm_order_list"]}
    out = select_tools(["order"], domain_tools=tools, read_only=ADVISOR_READ_ONLY)
    assert "crm_order_cancel" not in out


def test_companion_not_in_read_only_dropped():
    ro = ALL - {"crm_find"}
    assert "crm_find" not in sel(["inventory"], ro=ro, max_tools=20)


def test_max_tools_cap_default_six():
    out = sel(["master_products", "inventory"])
    assert len(out) == 6
    assert out == sel(["master_products", "inventory"], max_tools=50)[:6]


def test_max_tools_custom():
    assert len(sel(["inventory"], max_tools=2)) == 2


def test_unknown_domain_empty():
    assert sel(["nope"]) == []


def test_empty_domains_empty():
    assert sel([]) == []


def test_all_dropped_empty():
    assert sel(["inventory"], ro=frozenset()) == []


def test_unknown_mixed_with_known():
    out = sel(["nope", "incoming"], max_tools=50)
    assert out[0] == "crm_incoming_stock_list"


def test_domain_with_only_companions_survived_is_not_empty():
    # primaries all dropped but a companion remains: contract says "all dropped -> []"
    # for the domain; companion for incoming is stock_balance_list which IS read-only here.
    ro = frozenset({"crm_inventory_stock_balance_list"})
    out = sel(["incoming"], ro=ro)
    assert out in ([], ["crm_inventory_stock_balance_list"])


def test_module_exposes_select_tools():
    assert callable(subset.select_tools)
