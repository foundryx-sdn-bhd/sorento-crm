"""Chat fix PR-A red test (AC-A11): a class-level product list states its total.

"Do you have any bathtub?" answers a page of products out of a larger set; the reply must say how many
there are ("84"), not only list the page. The list route reports it as `pagination.total`.
"""
import json

from sorento_crm_mcp.presenters import present_response


def _env(data):
    return json.loads(present_response("crm_master_products_list", json.dumps(data)))


def test_a_product_list_page_states_the_set_total():
    rows = [{"product_code": f"ZZT-B{i}", "product_name": f"ZZT Bathtub {i}"} for i in range(3)]
    out = _env({"data": rows, "pagination": {"total": 84, "page": 1, "limit": 3}})
    assert "84" in out["intro"], out["intro"]


def test_a_complete_list_has_no_invented_total():
    rows = [{"product_code": "ZZT-B1", "product_name": "ZZT Bathtub 1"}]
    out = _env({"data": rows})
    assert out["intro"] == "Here are the matching products.", out["intro"]
