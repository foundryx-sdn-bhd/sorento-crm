"""SPEC-PROMO-PARAMS, owner ruling 12 Oct 2026: a size ask shows a few matches first, then
"reply to see more". The chatbot pages a spec-filtered answer off the route's `spec_page`
(`{page, limit, total}`), so the presenter hands it on. `pagination` itself stays dropped."""
import json

import pytest

from sorento_crm_mcp.presenters import present_response

TOOLS = ["crm_master_products_list", "crm_inventory_stock_balance_list", "crm_marketing_promotion_products_list"]


@pytest.mark.parametrize("tool", TOOLS)
def test_spec_page_is_handed_on(tool):
    """RED: `spec_page` is not a passthrough key, so the lane cannot tell there are more."""
    body = {"data": [], "pagination": {"total": 7, "page": 1, "limit": 5}, "spec_page": {"page": 1, "limit": 5, "total": 7}}
    out = json.loads(present_response(tool, json.dumps(body)))
    assert out.get("spec_page") == {"page": 1, "limit": 5, "total": 7}, out


def test_absent_when_the_route_sent_none():
    body = {"data": [], "pagination": {"total": 0, "page": 1, "limit": 5}}
    out = json.loads(present_response("crm_master_products_list", json.dumps(body)))
    assert "spec_page" not in out


def test_the_tool_descriptions_state_the_any_axis_ruling():
    """Reviewer S3 (owner ruling, 12 Oct 2026): a size on several axes matches any of them;
    no tool description still tells n8n to ask which axis, and each names `spec_page`."""
    from sorento_crm_mcp.catalog import CATALOG as TOOLS

    for name in ("crm_master_products_list", "crm_inventory_stock_balance_list", "crm_marketing_promotion_products_list"):
        text = next(t for t in TOOLS if t.name == name).description
        assert "spec_ambiguous" not in text, name
        assert "any of them" in text, name
    for name in ("crm_master_products_list", "crm_inventory_stock_balance_list"):
        assert "spec_page" in next(t for t in TOOLS if t.name == name).description, name


def test_a_paged_promotion_list_is_one_line_per_product():
    """Crew live check on bec7d329: each promotion line printed company, selling price, list
    price and dimensions on lines of their own. A spec-filtered (paged) promotion answer is one
    line per product: code, name when it differs, the promo price."""
    body = {
        "data": [
            {
                "company_name": "Sorento",
                "selling_price": 99,
                "product": {"product_code": "B1", "product_name": "Art basin", "list_price": 150, "length": 600},
                "promotion": {"id": "p1", "description": "Raya", "start_date": "2026-10-01", "end_date": "2026-10-31"},
            },
            {
                "selling_price": 80,
                "product": {"product_code": "B2", "product_name": "B2", "list_price": 120},
                "promotion": {"id": "p1", "description": "Raya", "start_date": "2026-10-01", "end_date": "2026-10-31"},
            },
        ],
        "pagination": {"total": 7, "page": 1, "limit": 5},
        "spec_page": {"page": 1, "limit": 5, "total": 7},
    }
    out = json.loads(present_response("crm_marketing_promotion_products_list", json.dumps(body)))
    items = out["groups"][0]["items"]
    assert [i["title"] for i in items] == ["B1 Art basin: MYR 99.00", "B2: MYR 80.00"], items
    assert all(i["fields"] == [] for i in items), items


def test_an_unpaged_promotion_list_keeps_its_fields():
    body = {"data": [{"selling_price": 99, "product": {"product_code": "B1", "list_price": 150}, "promotion": {"id": "p1"}}]}
    out = json.loads(present_response("crm_marketing_promotion_products_list", json.dumps(body)))
    assert any(f.get("label") == "List Price" for f in out["items"][0]["fields"]), out


def test_a_paged_promotion_line_names_its_group_and_a_name_beyond_the_code():
    """Crew data on 045a0fea: one product in two groups of one promotion printed as two identical
    lines, and a product whose name is its code printed no name. The line names its group, and
    the description stands in when the name only repeats the code."""
    promo = {"id": "p6", "description": "Office promo", "start_date": "2026-09-08", "end_date": "2026-12-08"}
    body = {
        "data": [
            {"selling_price": 650, "promotion_group_name": "LESS 40% + 5%", "promotion": promo,
             "product": {"product_code": "B1", "product_name": "B1", "description": "Art basin black"}},
            {"selling_price": 650, "promotion_group_name": ">=5 UNITS, EXTRA 10%", "promotion": promo,
             "product": {"product_code": "B1", "product_name": "B1", "description": "Art basin black"}},
        ],
        "pagination": {"total": 2, "page": 1, "limit": 5},
        "spec_page": {"page": 1, "limit": 5, "total": 2},
    }
    out = json.loads(present_response("crm_marketing_promotion_products_list", json.dumps(body)))
    titles = [i["title"] for i in out["groups"][0]["items"]]
    assert titles == [
        "B1 Art basin black: MYR 650.00 (LESS 40% + 5%)",
        "B1 Art basin black: MYR 650.00 (>=5 UNITS, EXTRA 10%)",
    ], titles
