"""DO-ASK-SIMPLIFY tester pass 1 (dd39cb4d, PR #1433), red first.

1. A dealer linked to several customers answers the period question ("this month"): the
   customer the ORIGINAL ask named carries into the answer. It used to widen to every link
   (`engine._customer_scope_gate`: "all of them for a message naming none").
   A DO-number ask is NOT narrowed by that carry (the number is the subject; it searches
   every own account), and its header prints no Customer line made of the forced links.
2. The header names the grouped customer ("HANLIM TRADING SDN BHD"), not the
   word the contact typed ("hanlim"), whenever every row carries a resolved name.
"""
from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import do_ask
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.tail import scope_block
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_dealer_eta_stock_routing import SORENTO, Console, _seed
from tests.chatbot.test_engine import CONTACT_ID, stub_access  # noqa: F401 - fixture

ORDERS_TOOL = "crm_order_management_orders_list"
_HANLIM = [
    "HANLIM TRADING SDN BHD [A/C II]",
    "HANLIM TRADING SDN BHD [A/C I]",
    "HANLIM TRADING SDN BHD [A/C III]",
]


# --- 1. the named customer carries into the period answer ----------------------------- #


def _link_second_customer(sf) -> str:
    from app.models.access import RespondContact, RespondContactCustomer
    from app.models.base import set_company_scope
    from app.models.order import Customer

    db = sf()
    set_company_scope(db, frozenset({SORENTO}))
    contact = db.query(RespondContact).filter(RespondContact.respond_io_id == str(CONTACT_ID)).one()
    other = Customer(
        id=str(uuid.uuid4()),
        customer_code=f"ZZT{uuid.uuid4().hex[:6]}",
        customer_name="ZZT Other Trading Sdn Bhd",
        company_id=SORENTO,
    )
    db.add(other)
    db.flush()
    db.add(RespondContactCustomer(contact_id=contact.id, customer_id=other.id, company_id=SORENTO, is_primary=False))
    db.commit()
    return other.id


def _dealer_customer_id(sf) -> str:
    from app.models.base import set_company_scope
    from app.models.order import Customer

    db = sf()
    set_company_scope(db, frozenset({SORENTO}))
    return db.query(Customer).filter(Customer.customer_name == "ZZT Dealer Sdn Bhd").one().id


@pytest.fixture
def dealer(session_factory, monkeypatch, stub_access):
    from app.main import app

    monkeypatch.setattr(do_ask, "today_myt", lambda: date(2026, 10, 2))
    _seed(session_factory, dealer=False, salesperson=True, shipments=False)
    other_id = _link_second_customer(session_factory)
    console = Console(session_factory, monkeypatch, stub_access)
    calls: list[tuple[str, dict[str, Any]]] = []
    inner = MCPRuntimeClient.call_tool

    def recording(self, name, args):
        calls.append((name, dict(args)))
        return inner(self, name, args)

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", recording)
    console.calls = calls
    console.own_id = _dealer_customer_id(session_factory)
    console.other_id = other_id
    try:
        yield console
    finally:
        app.dependency_overrides.clear()


def test_the_period_answer_keeps_the_customer_the_ask_named(dealer):
    dealer.say(
        "delivery to zzt dealer",
        verdict(domain_hint="order", intent_hint="check_order", entities=[entity("ZZT Dealer", hint="customer")]),
    )
    dealer.say(
        "this month",
        verdict(
            domain_hint="order",
            intent_hint="check_order",
            domain_in_message=False,
            entities=[],
            date_filter_start="2026-10-01",
            date_filter_end="2026-10-31",
        ),
    )
    orders = [args for name, args in dealer.calls if name == ORDERS_TOOL]
    assert len(orders) == 1, dealer.calls
    assert orders[0].get("customer_ids") == [dealer.own_id], orders[0]


# --- 1. the scope gate, unit level ------------------------------------------------------ #

_A, _B = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
_SCOPE = {"enforced": True, "ids": [_A, _B], "linked": [[_A, "ZZT A", "A1"], [_B, "ZZT B", "B1"]]}


def _focus(*uuids: str) -> Any:
    return SimpleNamespace(status=None, customers=[{"uuid": u, "hint": "customer"} for u in uuids])


def test_a_message_naming_no_customer_carries_the_focus_customer():
    _out, ids, refused, _line = engine_mod._customer_scope_gate(
        _SCOPE, {}, _focus(_A), {"entities": []}, ["order"]
    )
    assert (ids, refused) == ([_A], False)


def test_a_carried_customer_outside_the_links_is_not_carried():
    _out, ids, _refused, _line = engine_mod._customer_scope_gate(
        _SCOPE, {}, _focus("33333333-3333-4333-8333-333333333333"), {"entities": []}, ["order"]
    )
    assert ids == [_A, _B]


def test_my_means_every_link_even_with_a_carried_customer():
    _out, ids, _refused, _line = engine_mod._customer_scope_gate(
        _SCOPE, {"self_reference": True}, _focus(_A), {"entities": []}, ["order"]
    )
    assert ids == [_A, _B]


def test_an_order_number_searches_every_link_whatever_the_carry():
    _out, ids, _refused, _line = engine_mod._customer_scope_gate(
        _SCOPE, {}, _focus(_A), {"entities": [{"hint": "order", "raw": "202609-0916", "current_message": True}]}, ["order"]
    )
    assert ids == [_A, _B]


# --- 1b. the DO-number header ------------------------------------------------------------ #


def test_a_do_number_header_prints_no_customer_line_of_forced_links():
    gate = {
        "compatible_entities": [
            {"uuid": "o1", "entity_type": "order", "code": "202609-0916", "title": "202609-0916"},
            {"uuid": _A, "entity_type": "customer", "code": "A1", "display_name": "ZZT A", "scope": True},
            {"uuid": _B, "entity_type": "customer", "code": "B1", "display_name": "ZZT B", "scope": True},
        ]
    }
    header = scope_block.search_scope_header(domain="order", qf={"entities": []}, gate_json=gate, resolver_json={})
    assert "Order: 202609-0916" in header, header
    assert "Customer:" not in header, header


# --- 2. the grouped name, not the typed word -------------------------------------------- #


def test_the_header_prints_the_grouped_name_not_the_typed_word():
    rows = [
        {"uuid": f"0000000{i}-0000-4000-8000-000000000000", "entity_type": "customer", "code": "300-H", "display_name": n}
        for i, n in enumerate(_HANLIM)
    ]
    header = scope_block.search_scope_header(
        domain="order",
        qf={"entities": [{"raw": "hanlim", "hint": "customer"}]},
        gate_json={"compatible_entities": rows},
        resolver_json={"resolutions": [{"token": "hanlim", "matches": [{"entity_type": "customer"}]}]},
    )
    assert "Customer: HANLIM TRADING SDN BHD" in header, header


def test_carried_rows_print_the_grouped_name_not_the_typed_word():
    rows = [{"hint": "customer", "raw": "hanlim", "name": n} for n in _HANLIM]
    header = scope_block.search_scope_header(
        domain="order", qf={"entities": []}, gate_json={"compatible_entities": []}, resolver_json={}, focus_customers=rows
    )
    assert "Customer: HANLIM TRADING SDN BHD" in header, header


def test_rows_with_no_resolved_name_keep_the_typed_word():
    """Never a customer CODE: a row the DB has not named falls back to what was typed."""
    rows = [{"uuid": "u1", "entity_type": "customer", "code": "300-H001"}]
    header = scope_block.search_scope_header(
        domain="order",
        qf={"entities": [{"raw": "hanlim", "hint": "customer"}]},
        gate_json={"compatible_entities": rows},
        resolver_json={"resolutions": [{"token": "hanlim", "matches": [{"entity_type": "customer"}]}]},
    )
    assert "Customer: hanlim" in header and "300-H001" not in header, header


def test_a_do_number_miss_header_prints_no_customer_line_of_forced_links():
    """The same rule on the miss composer's own header (`answer.not_found_error_message`)."""
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    rows = [
        {"entity_type": "order", "code": "SO422056", "title": "SO422056"},
        {"uuid": _A, "entity_type": "customer", "code": "A1", "display_name": "ZZT A", "scope": True},
        {"uuid": _B, "entity_type": "customer", "code": "B1", "display_name": "ZZT B", "scope": True},
    ]
    out = not_found_error_message(
        {},
        parser={"domain_hint": "order", "entities": [{"hint": "order", "raw": "SO422056"}], "routing": {"suggested_team": "customer_service"}},
        resolved={"tokens": ["SO422056"], "unresolved_tokens": ["SO422056"], "resolutions": [], "intersection": [], "by_entity_type": {}},
        gate={"gate_passed": True, "compatible_entities": rows},
    )
    message = out.get("escalate_message") or ""
    assert "Order: SO422056" in message, message
    assert "Customer:" not in message, message
