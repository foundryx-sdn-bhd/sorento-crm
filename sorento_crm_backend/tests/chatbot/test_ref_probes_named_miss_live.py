"""REF-PROBES owner hand test 12 Oct, step 10: "sales order 202610-0107 delivery" from staff,
where 202610-0107 is a delivery order and no sales order. Expected (AC-14): the reply starts
"No sales order 202610-0107." and then answers the delivery order. Seen: the DO answer with
no lead line.

The parser output below is the rkind_0002 prompt's own emission for that message
(gpt-5.4-mini, temperature 0, parser-only replay): hint "sales_order", plus the keys the
earlier unit test left out (canonical_code, document ["SO"], requested_attributes
["delivery"]). Each variant adds one of them, so a red one names the key that loses the line.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_po_ref_ask import _make_staff
from tests.chatbot.test_ref_probes_answer import DO_LIST, _seed_do
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY

DO_NO = "202610-0107"

_ENTITY = {"raw": DO_NO, "hint": "sales_order", "canonical_code": None, "current_message": True, "confident": True}

VARIANTS: dict[str, dict[str, Any]] = {
    "bare": {},
    "canonical_code": {"entity": {"canonical_code": DO_NO}},
    "document_so": {"document": ["SO"]},
    "requested_delivery": {"requested_attributes": ["delivery"]},
    "live_shape": {
        "entity": {"canonical_code": DO_NO},
        "document": ["SO"],
        "requested_attributes": ["delivery"],
        "user_goal": f"trying to check the delivery status of sales order {DO_NO}",
        "routing": {"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
    },
}


def _qf(variant: dict[str, Any]) -> dict[str, Any]:
    over = dict(variant)
    entity = {**_ENTITY, **over.pop("entity", {})}
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[entity],
        **over,
    )


@pytest.mark.parametrize("name", list(VARIANTS))
def test_named_sales_order_on_a_delivery_order_leads_with_the_miss(session_factory, monkeypatch, name):
    _seed_contact(session_factory, variables={})
    _make_staff(session_factory)
    _seed_do(session_factory, DO_NO, None)
    result, _ = _run_turn(
        session_factory,
        monkeypatch,
        qf=_qf(VARIANTS[name]),
        text_body=f"sales order {DO_NO} delivery",
        msg_id=f"ZZT-step10-{uuid.uuid4().hex[:10]}",
        attributes=[OUTSTANDING_KEY],
        real_resolver=True,
        mcp_response=DO_LIST,
    )
    reply = (result.reply or {}).get("text") or ""
    assert reply.startswith(f"No sales order {DO_NO}."), reply


def test_named_sales_order_miss_after_an_open_kind_picker(session_factory, monkeypatch):
    """Hand test order: step 9 left a kind picker open ("<BOTH>" is a DO and our PO), then
    step 10 names a sales order that is a delivery order."""
    from tests.chatbot.test_ref_probes_answer import _seed_po

    both = "ZZT-BOTH-0011"
    _seed_contact(session_factory, variables={})
    _make_staff(session_factory)
    _seed_do(session_factory, DO_NO, None)
    _seed_do(session_factory, both, None)
    _seed_po(session_factory, both)
    attrs = [OUTSTANDING_KEY, "purchase_orders.placed"]
    first, _ = _run_turn(
        session_factory,
        monkeypatch,
        qf=_parser_output(
            domain_hint="order", intent_hint="check_order", order_status=None,
            entities=[{"raw": both, "hint": "order", "canonical_code": None, "current_message": True, "confident": True}],
        ),
        text_body=both,
        msg_id=f"ZZT-step9-{uuid.uuid4().hex[:10]}",
        attributes=attrs,
        real_resolver=True,
        mcp_response=DO_LIST,
    )
    assert "Which one do you mean?" in ((first.reply or {}).get("text") or ""), first.reply
    result, _ = _run_turn(
        session_factory,
        monkeypatch,
        qf=_qf(VARIANTS["live_shape"]),
        text_body=f"sales order {DO_NO} delivery",
        msg_id=f"ZZT-step10-{uuid.uuid4().hex[:10]}",
        attributes=attrs,
        real_resolver=True,
        mcp_response=DO_LIST,
    )
    reply = (result.reply or {}).get("text") or ""
    assert reply.startswith(f"No sales order {DO_NO}."), reply
