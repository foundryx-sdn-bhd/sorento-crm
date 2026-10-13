"""WA-CONCISE S3 (card v4): incoming and order rows, one fact per line. AC-16 to AC-18.

UAC: documentation/plans/chatbot/wa-concise-acceptance-criteria.md. Placeholder data only.
"""
from __future__ import annotations

from app.services.chatbot.answer_bridge import apply_scope_block
from app.services.chatbot.turn.compose import Answer
from tests.chatbot._wa_concise_helpers import (
    INCOMING_TOOL,
    ORDERS_TOOL,
    STOCK_TOOL,
    UNGRANTED,
    detailed_payload,
    detailed_row,
    incoming_payload,
    incoming_shipment,
    render,
)

ORDER_NO = "SMC202609-0055"
_DO_KEYS = [
    f"delivery_orders.{k}"
    for k in (
        "order_number", "customer", "order_date", "delivery_date", "status", "pickup_time",
        "transporter", "driver", "lorry_plate", "warehouse", "products",
    )
]
_DO_GRANTED = {"semantic_input": {}, "access": {"attributes": _DO_KEYS}}


def _order(number: str = ORDER_NO) -> dict:
    return {
        "order_number": number,
        "debtor_name": "CUSTOMER A (PROJECT-CASH)",
        "order_date": "08/09/2026",
        "actual_delivery_date": "09/09/2026",
        "order_status": "Picked Up / In Transit",
        "pickup_time": "19:46:00",
        "transporter": "GLORY MOTION",
        "driver_name": "DRIVER B",
        "lorry_plate": "PLATE-1",
        "warehouse": "BRW",
        "lines": [
            {"product_code": "SRTKS7547-NEW", "quantity": 1},
            {"product_code": "TPE-9201", "quantity": 1},
            {"product_code": "SRTKT71SS", "quantity": 1},
            {"product_code": "TRANSPORT", "quantity": 1},
        ],
        "updated_at": "2026-09-10T17:36:17",
    }


def _orders_text(*numbers: str) -> str:
    rows = [_order(n) for n in (numbers or (ORDER_NO,))]
    return render(ORDERS_TOOL, {"data": rows, "pagination": {"total": len(rows)}}, _DO_GRANTED)


_ORDER_BLOCK = (
    "*Customer:* CUSTOMER A (PROJECT-CASH)\n*Order Date:* 08/09/2026\n"
    "*Actual Delivery Date:* 09/09/2026\n*Status:* Picked Up / In Transit\n"
    "*Pickup Time:* 19:46:00\n*Transporter:* GLORY MOTION\n*Driver:* DRIVER B\n"
    "*Lorry Plate:* PLATE-1\n*Warehouse:* BRW\n"
    "*Products:* SRTKS7547-NEW (1), TPE-9201 (1), SRTKT71SS (1), TRANSPORT (1)"
)

# ---- AC-16 ----------------------------------------------------------------- #

_INC_A = incoming_shipment(
    "SRTWB1543", "IAAU1907074", "2026-09-09", 49, [("BRW", 35), ("BRW-BB", 4), ("BRW-SMC", 10)]
)
_INC_B = incoming_shipment("SRTWB1543", "IAAU1907075", "2026-09-20", 12, [("BRW", 12)])
_INC_A_LINES = (
    "*Product Code:* SRTWB1543\n*Container:* IAAU1907074\n*ETA:* 2026-09-09\n"
    "*Incoming Quantity:* 49\n*Warehouse Allocations:* BRW (35), BRW-BB (4), BRW-SMC (10)"
)


def test_ac16_single_incoming_block_is_unnumbered():
    text = render(INCOMING_TOOL, incoming_payload([_INC_A]), UNGRANTED)

    assert text == _INC_A_LINES, text


def test_ac16_guard_two_incoming_blocks_are_numbered_with_one_blank_line_between():
    """Green today: more than one block keeps `1. ` / `2. ` and a blank line between."""
    text = render(INCOMING_TOOL, incoming_payload([_INC_A, _INC_B]), UNGRANTED)

    assert text == (
        f"1. {_INC_A_LINES}\n\n"
        "2. *Product Code:* SRTWB1543\n*Container:* IAAU1907075\n*ETA:* 2026-09-20\n"
        "*Incoming Quantity:* 12\n*Warehouse Allocations:* BRW (12)"
    ), text


# ---- AC-17 ----------------------------------------------------------------- #


def test_ac17_single_order_whole_reply():
    text = _orders_text()

    assert text == f"*Order Number:* {ORDER_NO}\n{_ORDER_BLOCK}\n\n_Updated 10/09/2026 17:36_", text


def test_ac17_two_orders_are_numbered():
    text = _orders_text(ORDER_NO, "SMC202609-0056")

    assert text == (
        f"1. *Order Number:* {ORDER_NO}\n{_ORDER_BLOCK}\n\n"
        f"2. *Order Number:* SMC202609-0056\n{_ORDER_BLOCK}\n\n"
        "_Updated 10/09/2026 17:36_"
    ), text


def test_block_form_flag_prints_once_after_the_last_fact_line():
    """Block form: a flag line stays, once per block, after its last fact line - also when
    the block merges several rows."""
    first = detailed_row("Sorento", "P-ONE", "BRW", 5)
    second = detailed_row("Sorento", "P-ONE", "MWH", 6)
    first["is_discontinued"] = second["is_discontinued"] = True

    text = render(STOCK_TOOL, detailed_payload([first, second]), UNGRANTED)

    assert text == (
        "*Product Code:* P-ONE\n*Total:* 11\n*BRW:* 5\n*MWH:* 6\n"
        "⚠️  *(PRODUCT DISCONTINUED)*\n\n_Updated 11/09/2026 17:26_"
    ), text


# ---- AC-18 ----------------------------------------------------------------- #

_HEADER_BROAD = "Customer: all customers\nProduct: all products\nDates: all dates"
_HEADER_NAMED = (
    f"Customer: all customers\nProduct: all products\nDelivery order: {ORDER_NO}\nDates: all dates"
)
_NAMED_ORDER_GATE = {
    "compatible_entities": [
        {"entity_type": "customer_order", "code": ORDER_NO, "title": ORDER_NO, "uuid": "ord-1"}
    ]
}
_NAMED_ORDER_QF = {"entities": [{"raw": ORDER_NO, "hint": "order", "canonical_code": ORDER_NO}]}


def _scope(text: str, *, named: bool) -> str:
    out = apply_scope_block(
        Answer(text=text),
        domain="order",
        qf=_NAMED_ORDER_QF if named else {},
        gate_json=_NAMED_ORDER_GATE if named else None,
        resolver_json=None,
    )
    return out.text


def test_ac18_one_named_order_that_prints_has_no_scope_header():
    body = _orders_text()

    assert ORDER_NO in body
    assert _scope(body, named=True) == body


def test_ac18_guard_broad_search_keeps_the_scope_header():
    body = _orders_text(ORDER_NO, "SMC202609-0056")

    assert _scope(body, named=False) == f"{_HEADER_BROAD}\n\n{body}"


def test_ac18_guard_named_order_that_does_not_print_keeps_the_scope_header():
    """A named order whose number is not in the reply (the miss shape) keeps the header."""
    body = "No orders found."

    assert _scope(body, named=True) == f"{_HEADER_NAMED}\n\n{body}"
