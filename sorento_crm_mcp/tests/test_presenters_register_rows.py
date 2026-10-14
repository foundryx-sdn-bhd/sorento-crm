"""PV2 redesign R1: the MCP presenters carry the facts the answer register needs but the reply does not print.

* `crm_order_management_orders_list`: each order item carries an UNPRINTED top-level `lines`
  [{"product_code": str, "qty": number}] (UAC R1-2), so the register never parses the "Products" text.
* `crm_sales_report`: a drill (`group_by` + `rows`) also hands back `group_by` and `rows` [{"name": str}] as
  unprinted keys, so the delivered by-product / by-customer breakdown can be recorded (UAC R1-6).

Run with `PYTHONPATH=$PWD` from `sorento_crm_mcp/`.
"""
from __future__ import annotations

import json
from pathlib import Path

from sorento_crm_mcp.presenters import present_response

ORDERS = "crm_order_management_orders_list"
_FIXTURES = Path(__file__).parent / "fixtures" / "sales_report"


def env(tool, data):
    return json.loads(present_response(tool, json.dumps(data)))


def _orders(lines):
    return env(ORDERS, {"data": [{"order_number": "202606-1622", "debtor_name": "HANLIM",
                                  "order_date": "2026-06-10", "warehouse": "BRW", "lines": lines}]})


def test_an_order_item_carries_lines_with_code_and_numeric_qty():
    out = _orders([{"quantity": 3, "product": {"product_code": "SRTWB7109"}},
                   {"quantity": "2.5000", "product": {"product_code": "SRTWT7203"}}])
    assert out["items"][0]["lines"] == [{"product_code": "SRTWB7109", "qty": 3},
                                        {"product_code": "SRTWT7203", "qty": 2.5}]


def test_lines_are_never_a_printed_field():
    out = _orders([{"quantity": 3, "product": {"product_code": "SRTWB7109"}}])
    assert "lines" not in {f["label"] for f in out["items"][0]["fields"]}
    assert "lines" not in {f.get("key") for f in out["items"][0]["fields"]}


def test_the_printed_products_field_is_unchanged():
    out = _orders([{"quantity": 3, "product": {"product_code": "SRTWB7109"}}])
    fields = {f["label"]: f["value"] for f in out["items"][0]["fields"]}
    assert fields["Products"] == "SRTWB7109 (3)"


def test_a_line_without_a_code_is_not_recorded():
    out = _orders([{"quantity": 3, "product": {}}, {"quantity": 1, "product": {"product_code": "A"}}])
    assert out["items"][0].get("lines") == [{"product_code": "A", "qty": 1}]


def test_an_order_with_no_lines_has_an_empty_lines_key_or_none():
    out = _orders([])
    assert out["items"][0].get("lines", []) == []


def test_a_delivered_drill_carries_group_by_and_rows_unprinted():
    body = json.loads((_FIXTURES / "sales-report-drill-product.json").read_text())
    out = env("crm_sales_report", body)
    assert out["group_by"] == "product"
    assert [r["name"] for r in out["rows"]] == [r["name"] for r in body["rows"]]
    assert "rows" not in out["response"]


def test_a_delivered_total_has_no_rows():
    body = json.loads((_FIXTURES / "sales-report-month.json").read_text())
    out = env("crm_sales_report", body)
    assert out.get("rows", []) == []
