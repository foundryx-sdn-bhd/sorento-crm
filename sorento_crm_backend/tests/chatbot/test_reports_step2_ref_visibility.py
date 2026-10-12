"""REPORTS-STEP2 S3a widening must never add a reference kind the asker may not probe (security).

The several-ask widening adds `ALLOWED[domain]`; on main `ALLOWED["order"]` carries the visibility
gated reference kinds. They go through the same visible-kinds rule a typed reference hint does.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot.lanes.business.resolve_gate import (
    _allowed_entity_types,
    _widened_entity_types,
    resolve_entity_body,
)

GATED = {"purchase_order", "purchase_request", "sales_order", "customer_po_ref"}
REFERENCE = GATED | {"customer_order"}
DEALER = ["customer_order"]
SO_GRANTED = ["customer_order", "sales_order", "customer_po_ref"]
STAFF = ["customer_order", "sales_order", "customer_po_ref", "purchase_order", "purchase_request"]
ENTITIES = [{"raw": "CB88SS", "hint": "product", "canonical_code": "CB88SS", "current_message": True, "confident": True}]
TWO_ASKS = [{"domain": "inventory", "intent": "check_stock"}, {"domain": "order", "intent": "check_order"}]
ONE_ASK = [{"domain": "inventory", "intent": "check_stock"}]


def _po(asks: list[dict[str, Any]], entities=ENTITIES) -> dict[str, Any]:
    return {
        "message_type": "business_query", "intent_hint": "check_stock", "domain_hint": "inventory",
        "match_mode": "and", "access_levels": [], "entity_op": "replace_combine", "entities": list(entities),
        "asks": asks,
    }


def _ctx(kinds: list[str] | None, asks: list[dict[str, Any]], entities=ENTITIES) -> dict[str, Any]:
    ctx: dict[str, Any] = {"text": {"message": {"message": {"text": "x"}}}, "contact": {"id": "1"},
                           "parse": {"output": _po(asks, entities)}}
    if kinds is not None:
        ctx["reference_kinds"] = kinds
    return ctx


def _types(kinds, asks=TWO_ASKS) -> tuple[list[Any], list[Any], dict[str, Any]]:
    ctx = _ctx(kinds, asks)
    po = _po(asks)
    body = resolve_entity_body(ctx)
    return _allowed_entity_types(ctx, ENTITIES, po), _widened_entity_types(ctx, ENTITIES, po), body


def test_1_a_dealer_never_gets_a_gated_kind_from_the_widening() -> None:
    allowed, widened, body = _types(DEALER)
    for seen in (allowed, widened, body["allowed_entity_types"], body.get("widened_entity_types") or []):
        assert not set(seen) & GATED, seen


def test_2_an_so_granted_asker_may_get_sales_kinds_but_never_purchase_kinds() -> None:
    allowed, widened, body = _types(SO_GRANTED)
    assert {"sales_order", "customer_po_ref"} <= set(allowed), allowed
    for seen in (allowed, widened, body["allowed_entity_types"], body.get("widened_entity_types") or []):
        assert not set(seen) & {"purchase_order", "purchase_request"}, seen


def test_3_staff_with_the_placed_grant_gets_all_five() -> None:
    allowed, _widened, _body = _types(STAFF)
    assert REFERENCE <= set(allowed), allowed


def test_4_a_single_ask_is_unchanged() -> None:
    ctx = _ctx(DEALER, ONE_ASK)
    assert _allowed_entity_types(ctx, ENTITIES, _po(ONE_ASK)) == ["product"]
    assert _widened_entity_types(ctx, ENTITIES, _po(ONE_ASK)) == []


def test_5_the_customer_widening_still_happens() -> None:
    for kinds in (DEALER, SO_GRANTED, STAFF):
        allowed, widened, body = _types(kinds)
        assert "customer" in allowed and "customer" in widened, (kinds, allowed)
        assert "customer" in body["widened_entity_types"], body


def test_6_the_bare_order_hints_are_resolved_like_a_typed_reference_hint() -> None:
    allowed, widened, _body = _types(DEALER)
    assert "order" not in allowed and "order_number" not in allowed, allowed
    assert set(allowed) & REFERENCE == {"customer_order"}, allowed
    assert "order" not in widened, widened
