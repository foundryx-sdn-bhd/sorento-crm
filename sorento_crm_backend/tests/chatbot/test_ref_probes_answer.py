"""REF-PROBES turn level: the kind of a typed reference number comes from the data
(AC-8, AC-9, AC-14, AC-18).

Harness: one real `engine.run_turn` with the REAL resolver over rows seeded on the blank
schema (`real_resolver=True`); only the parser, access check and the MCP network edge are
doubled. Staff = `chatbot_profile.tier == "office"`.
"""
from __future__ import annotations

import uuid
from typing import Any

from tests.chatbot.test_customer_scope_lane import ORDERS, _calls, _link_customers, _other_customer
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_po_ref_ask import _make_staff, _seed_po_so
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, SO_NOT_ENABLED, _seed_so

SO_NO = "SO2610-0127"
DO_NO = "202610-0838"
FOREIGN = "ZZT FOREIGN TRADING"
OWN = "ZZT OWN A"
DO_LIST = {
    "has_result": True,
    "response": f"*Orders*\n1. {DO_NO} - {OWN} - Delivered",
    "items": [{"order_number": DO_NO}],
}


def _seed_do(session_factory, number: str, customer_id: str | None) -> None:
    from app.models.order import Order
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = session_factory()
    db.add(Order(id=str(uuid.uuid4()), order_number=number, customer_id=customer_id, company_id=DEFAULT_COMPANY_ID))
    db.commit()


def _ask(raw: str, hint: str, **over: Any) -> dict[str, Any]:
    return _parser_output(
        domain_hint="order",
        intent_hint="check_order",
        order_status=None,
        entities=[{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
        **over,
    )


def _turn(session_factory, monkeypatch, raw: str, hint: str, *, attributes=(OUTSTANDING_KEY,)):
    result, captured = _run_turn(
        session_factory,
        monkeypatch,
        qf=_ask(raw, hint),
        text_body=f"status of {raw}",
        msg_id=f"ZZT-refprobe-{uuid.uuid4().hex[:10]}",
        attributes=list(attributes),
        real_resolver=True,
        mcp_response=DO_LIST,
    )
    return ((result.reply or {}).get("text") or ""), captured


def test_staff_bare_so_hit_answers_sales_order(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _make_staff(session_factory)
    _seed_so(session_factory, SO_NO, customer_id=None, lines=[(4, 0)])
    reply, _ = _turn(session_factory, monkeypatch, SO_NO, "order")
    assert "Sales order" in reply, reply
    assert SO_NO in reply, reply


def test_do_hit_header_names_kind():
    from app.services.chatbot.tail import scope_block

    qf = {
        "entities": [{"raw": DO_NO, "hint": "order", "confident": True}],
        "date_filter_start": None,
        "date_filter_end": None,
    }
    gate_json = {"compatible_entities": [{"entity_type": "customer_order", "code": DO_NO, "title": DO_NO}]}
    header = scope_block.search_scope_header(domain="order", qf=qf, gate_json=gate_json, resolver_json={})
    assert header is not None
    assert f"Delivery order: {DO_NO}" in header.splitlines(), header


def test_named_so_miss_answers_do(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _make_staff(session_factory)
    _seed_do(session_factory, DO_NO, None)
    reply, _ = _turn(session_factory, monkeypatch, DO_NO, "sales_order")
    assert reply.startswith(f"No sales order {DO_NO}."), reply


def test_foreign_so_hit_is_not_a_kind_option(session_factory, monkeypatch):
    """AC-8: a dealer's own DO and another customer's SO share a number; the SO is dropped
    before the picker, so the DO is answered and the SO is never offered or named."""
    _seed_contact(session_factory, variables={})
    own = _link_customers(session_factory, OWN)[0]
    foreign = _other_customer(session_factory, FOREIGN)
    _seed_do(session_factory, DO_NO, own)
    _seed_so(session_factory, DO_NO, customer_id=foreign, debtor_name=FOREIGN, lines=[(1, 0)])
    reply, captured = _turn(session_factory, monkeypatch, DO_NO, "order")
    assert "(sales order)" not in reply and "Which one" not in reply, reply
    assert FOREIGN not in reply, reply
    assert _calls(captured, ORDERS), "the DO was answered through the orders list"
    # Round 2b note: this is a real collision (same number, own DO + foreign SO). It may pass
    # while the AND-mode resolver never probes SOs; it must keep passing once it does.


def test_foreign_po_ref_hit_is_silent_miss(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN)
    foreign = _other_customer(session_factory, FOREIGN)
    _seed_po_so(session_factory, "SOZZT200001", customer_id=foreign, po_ref="4500999", po_ref_key="4500999", debtor_name=FOREIGN)
    reply, _ = _turn(session_factory, monkeypatch, "4500999", "customer_po_ref")
    assert FOREIGN not in reply and "SOZZT200001" not in reply, reply
    assert "could not find" in reply.lower(), reply


def test_dealer_without_so_grant_keeps_not_enabled_line(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN)
    _seed_so(session_factory, SO_NO, customer_id=None, lines=[(4, 0)])
    reply, _ = _turn(session_factory, monkeypatch, SO_NO, "order", attributes=())
    assert reply.strip() == SO_NOT_ENABLED, reply


PLACED = "purchase_orders.placed"
PICKER = "Which one do you mean?"


def _seed_po(session_factory, number: str) -> None:
    from app.models.procurement import PurchaseOrder
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = session_factory()
    db.add(PurchaseOrder(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, po_number=number, status="active"))
    db.commit()


def test_named_po_on_do_po_collision_answers_po(session_factory, monkeypatch):
    """Round 2b A: a DO and our PO share a number; the PO word names the PO, so the PO card
    is answered and the DO hit does not hide it (AND mode probes only the old kinds)."""
    number = "PO-2026/04-0011"
    _seed_contact(session_factory, variables={})
    _make_staff(session_factory)
    _seed_do(session_factory, number, None)
    _seed_po(session_factory, number)
    reply, _ = _turn(session_factory, monkeypatch, number, "purchase_order", attributes=(OUTSTANDING_KEY, PLACED))
    assert not reply.startswith("No purchase order"), reply
    assert "Purchase order" in reply and number in reply, reply


def test_bare_do_so_collision_asks_picker(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _make_staff(session_factory)
    _seed_do(session_factory, SO_NO, None)
    _seed_so(session_factory, SO_NO, customer_id=None, lines=[(1, 0)])
    reply, _ = _turn(session_factory, monkeypatch, SO_NO, "order")
    assert PICKER in reply, reply
    assert "(delivery order)" in reply and "(sales order)" in reply, reply


def test_dealer_own_null_customer_so_with_linked_debtor_code_is_answered(session_factory, monkeypatch):
    """Round 2b D: the SO has no customer_id but its debtor code is a linked customer's code
    in the same company (so_status scope rule), so it is the dealer's own."""
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN, codes={OWN: "ZZT-D001"})
    _seed_so(session_factory, SO_NO, customer_id=None, debtor_code="ZZT-D001", debtor_name=OWN, lines=[(4, 0)])
    reply, _ = _turn(session_factory, monkeypatch, SO_NO, "order")
    assert f"*{SO_NO}*" in reply or SO_NO in reply, reply
    assert "could not find" not in reply.lower(), reply


def test_dealer_own_null_customer_so_via_sales_order_hint(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN, codes={OWN: "ZZT-D001"})
    _seed_so(session_factory, SO_NO, customer_id=None, debtor_code="ZZT-D001", debtor_name=OWN, lines=[(4, 0)])
    reply, _ = _turn(session_factory, monkeypatch, SO_NO, "sales_order")
    assert SO_NO in reply and "could not find" not in reply.lower(), reply
