"""Phase 2 RED tests - chat fix PR-B S5b (AC-B7): the `crm_sales_report` reply names a brand filter
(`brand_name` on the body, as the outstanding report's header does), also on an empty total."""
from sorento_crm_mcp.presenters import _sales_report

BASE = {
    "status": "ok", "message": None, "basis": "delivered", "customer_name": "ZZT DEALER", "customer_count": 1,
    "product_code": None, "product_codes": [], "channel": None, "channel_shown": False,
    "location_token": None, "warehouse_codes": [], "date_from": "2026-09-01", "date_to": "2026-09-30",
    "grain": "month", "total": {"qty": 0, "amount": 0.0}, "periods": [], "group_by": None, "rows": [],
    "more": 0, "options": [],
}


def test_b7b_an_empty_brand_filtered_total_names_the_brand():
    text = _sales_report({**BASE, "brand_name": "MOCHA"})
    assert "MOCHA" in text and "Brand" in text, text
    assert "No sales found" in text


def test_b7b_a_total_with_sales_names_the_brand_too():
    body = {**BASE, "brand_name": "MOCHA", "total": {"qty": 5, "amount": 50.0},
            "periods": [{"from": "2026-09-01", "to": "2026-09-30", "qty": 5, "amount": 50.0}]}
    assert "MOCHA" in _sales_report(body)


def test_b7b_near_miss_no_brand_filter_prints_no_brand_line():
    assert "Brand" not in _sales_report(BASE)
    assert "Brand" not in _sales_report({**BASE, "brand_name": None})
    assert "Brand" not in _sales_report({**BASE, "brand_name": ""})
