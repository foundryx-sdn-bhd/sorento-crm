"""WA-CONCISE round 16 (red-first): AC-18 covers the generic "Order" scope row too.
Placeholder data only."""
from __future__ import annotations

import pytest

from app.services.chatbot.answer_bridge import apply_scope_block
from app.services.chatbot.turn.compose import Answer

BODY = "*Order Number:* SO-1\n*Status:* Delivered"


def _scope(entity_type: str, text: str = BODY) -> str:
    gate = {"compatible_entities": [{"entity_type": entity_type, "code": "SO-1", "title": "SO-1", "uuid": "o-1"}]}
    qf = {"entities": [{"raw": "SO-1", "hint": "order", "canonical_code": "SO-1"}]}
    return apply_scope_block(
        Answer(text=text), domain="order", qf=qf, gate_json=gate, resolver_json=None
    ).text


@pytest.mark.parametrize("entity_type", ["order", "order_number"])
def test_generic_order_that_prints_has_no_scope_header(entity_type):
    assert _scope(entity_type) == BODY


def test_guard_delivery_order_that_prints_still_has_no_header():
    assert _scope("customer_order") == BODY


@pytest.mark.parametrize(
    ("entity_type", "label"), [("order", "Order"), ("customer_order", "Delivery order")]
)
def test_guard_a_named_order_that_does_not_print_keeps_the_header(entity_type, label):
    body = "No orders found."

    assert _scope(entity_type, body) == (
        f"Customer: all customers\nProduct: all products\n{label}: SO-1\nDates: all dates\n\n{body}"
    )
