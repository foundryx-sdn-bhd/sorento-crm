"""Real-shape envelopes for the answer-register tests (PV2 redesign R1): MCP raw body -> the real
presenter (`present_response`) -> the real `fetch.output_structurer` -> the real
`turn_runtime.envelope_of`. No field name is invented here; only the MCP raw bodies are written out, with
the keys the presenters read (copied from sorento_crm_mcp/tests)."""
from __future__ import annotations

import json
from typing import Any

from sorento_crm_mcp.presenters import present_response

from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.turn.plan import FetchSpec

#: Everything a staff contact may see on the orders / PO / stock lists.
ALL_REVEALS = [
    "delivery_orders.order_number", "delivery_orders.customer", "delivery_orders.branch",
    "delivery_orders.order_date", "delivery_orders.delivery_date", "delivery_orders.status",
    "delivery_orders.pickup_time", "delivery_orders.transporter", "delivery_orders.driver",
    "delivery_orders.lorry_plate", "delivery_orders.warehouse", "delivery_orders.products",
    "purchase_orders.supplier", "inventory.sellable",
]

ORDERS_TOOL = "crm_order_management_orders_list"
ORDERS_RAW = {
    "data": [
        {"order_number": "202609-2012", "debtor_name": "DELUXE HOME CENTRE SDN BHD",
         "order_date": "2026-09-10", "actual_delivery_date": "2026-09-11", "order_status": "Delivered",
         "warehouse": "BRW",
         "lines": [{"quantity": 5, "product": {"product_code": "SRTWB1096"}},
                   {"quantity": 2, "product": {"product_code": "SRTWT7203"}}]},
        {"order_number": "202609-2013", "debtor_name": "HANLIM TRADING SDN BHD",
         "order_date": "2026-09-12", "order_status": "New Order", "warehouse": "BRW",
         "lines": [{"quantity": 1, "product": {"product_code": "SRTW5000"}}]},
    ],
}

PO_TOOL = "crm_procurement_po_placed_list"
PO_RAW = {
    "data": [
        {"po_number": "PO-1001", "product_code": "SRTWC8517", "outstanding_qty": 50,
         "expected_date": "2026-07-01", "supplier": "Acme Supplies"},
        {"po_number": "PO-1002", "product_code": "SRTWC286", "outstanding_qty": 20,
         "expected_date": "2026-07-09", "supplier": "Beta Supplies"},
    ],
}

STOCK_TOOL = "crm_inventory_stock_balance_list"
STOCK_RAW = {
    "data": [],
    "pagination": {"total": 2, "page": 1, "limit": 50},
    "empty": False,
    "stock_visibility": {"mode": "compact", "warehouse_codes": ["BRW", "BRW-BB"], "source": "contact"},
    "stock_summary": [
        {"product_id": "11111111-1111-4111-8111-111111111111", "product_code": "SRTBF11201-NEW",
         "product_name": "SRTBF11201-NEW", "total_on_hand": 700,
         "locations": [{"warehouse_code": "BRW", "quantity_on_hand": 500},
                       {"warehouse_code": "BRW-BB", "quantity_on_hand": 200}],
         "flags": {"discontinued": False}},
        {"product_id": "22222222-2222-4222-8222-222222222222", "product_code": "SRTWB7109",
         "product_name": "SRTWB7109", "total_on_hand": 3,
         "locations": [{"warehouse_code": "BRW", "quantity_on_hand": 3}], "flags": {"discontinued": False}},
    ],
    "last_updated_at": "2026-08-24T18:00:00",
}

INCOMING_TOOL = "crm_incoming_stock_list"
INCOMING_RAW = {
    "data": [{
        "shipment_number": "OOLKSF6417", "shipping_container_number": "UETU5190029",
        "estimated_arrival_date": "2026-01-24",
        "lines": [
            {"product_code": "SRTWC286-SH-NEW", "product_name": "A", "remaining_incoming_quantity": 100,
             "warehouse_allocations": [{"warehouse_code": "BRW", "allocated_quantity": 100}]},
            {"product_code": "SRTWC286-SH-NEW-P", "product_name": "B", "remaining_incoming_quantity": 34,
             "warehouse_allocations": []},
        ],
    }],
}

PRODUCTS_TOOL = "crm_master_products_list"
PRODUCTS_RAW = {"data": [{"product_code": "SRTWC8517"}, {"product_code": "SRTWC8518"}]}

ATTACH_TOOL = "crm_resource_attachments_list"

REPORT_ASK_ENVELOPE = {
    "result_type": "report_ask",
    "response": ("Top 2 customers by delivered sales\nPeriod: 2026-09-01 to 2026-09-30\n"
                 "1. ABC SDN BHD RM 10.00, 2 pcs\n2. XYZ RM 5.00, 1 pc\nTotal RM 15.00, 3 pcs"),
    "has_result": True,
    "drill_rows": {"group_by": "customer",
                   "rows": [{"name": "ABC SDN BHD", "keys": ["c1"]}, {"name": "XYZ", "keys": ["c2", "c3"]}]},
}

SALES_DRILL_RAW = {
    "status": "ok", "message": None, "basis": "delivered", "customer_name": "ACME HARDWARE SDN BHD [A/C I]",
    "customer_count": 1, "product_code": None, "product_codes": [], "channel": None, "channel_shown": False,
    "location_token": None, "warehouse_codes": [], "date_from": "2026-07-01", "date_to": "2026-09-30",
    "grain": "month", "total": {"qty": 685, "amount": 58700.0}, "periods": [], "group_by": "product",
    "rows": [{"rank": 1, "name": "PRD-A101", "qty": 12, "amount": 5200.0, "date": None, "customer_name": None},
             {"rank": 2, "name": "PRD-A102", "qty": 12, "amount": 4800.0, "date": None, "customer_name": None}],
}


def _ctx(tool: str, access: list[str] | None, semantic_input: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"tool": tool, "semantic_input": semantic_input or {}, "entities": [],
            "access": {"attributes": list(access if access is not None else ALL_REVEALS)}}


def structured(tool: str, raw: dict[str, Any], *, access: list[str] | None = None,
               semantic_input: dict[str, Any] | None = None) -> dict[str, Any]:
    """What `fetch.output_structurer` returns for the MCP body, rendered by the real presenter."""
    rendered = json.loads(present_response(tool, json.dumps(raw)))
    return fetch.output_structurer(rendered, _ctx(tool, access, semantic_input))


def envelope(tool: str, raw: dict[str, Any], *, domain: str = "order", access: list[str] | None = None,
             semantic_input: dict[str, Any] | None = None, **fragment: Any) -> dict[str, Any]:
    """The composer envelope (`turn_runtime.envelope_of`) for that fetch: what the engine's answer stage
    holds for the register writer."""
    out = structured(tool, raw, access=access, semantic_input=semantic_input)
    spec = FetchSpec(domain=domain, entities=[], filters={}, date_window=None)
    return turn_runtime.envelope_of({"fetch": out, **fragment}, spec, [])
