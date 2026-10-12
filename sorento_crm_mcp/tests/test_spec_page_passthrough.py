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
