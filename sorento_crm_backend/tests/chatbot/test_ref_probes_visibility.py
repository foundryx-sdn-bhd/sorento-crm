"""REF-PROBES S2: which reference kinds an asker may have probed (AC-7) and the parser hints
that name them (AC-12). Pure: no database.

Contract assumed from PLAN-ref-probes-11oct.md S2:
* `contracts.visible_reference_kinds(attributes, is_staff)` returns the set of resolver
  kinds; `attributes` is the same list of grant slugs `ctx["access"]["attributes"]` holds.
* `resolve_gate.resolve_entity_body(ctx)` reads `ctx["reference_kinds"]` (the engine puts
  the visible kinds there) and, when any entity carries a reference hint, sends them as
  `body["reference_kinds"]`. A body with only non-reference hints has no such key.
"""
from __future__ import annotations

from typing import Any

DO, SO, PO_REF, PO, PR = "customer_order", "sales_order", "customer_po_ref", "purchase_order", "purchase_request"
SO_GRANT = "sales_orders.outstanding"
PLACED = "purchase_orders.placed"


def _visible(attrs, *, staff: bool):
    from app.services.chatbot.contracts import visible_reference_kinds

    return set(visible_reference_kinds(list(attrs), staff))


def test_dealer_never_sees_po_pr_even_with_grant():
    assert _visible({SO_GRANT, PLACED}, staff=False) == {DO, SO, PO_REF}


def test_staff_without_placed_grant_has_no_po_pr():
    assert _visible({SO_GRANT}, staff=True) == {DO, SO, PO_REF}


def test_staff_with_both_grants_sees_five():
    assert _visible({SO_GRANT, PLACED}, staff=True) == {DO, SO, PO_REF, PO, PR}


def test_no_so_grant_drops_so_and_po_ref():
    assert _visible(set(), staff=False) == {DO}
    assert _visible({PLACED}, staff=True) == {DO, PO, PR}


def test_new_hints_in_contract():
    from app.services.chatbot.contracts import ENTITY_HINTS

    for hint in ("delivery_order", "sales_order", "customer_po_ref", "purchase_order", "purchase_request"):
        assert hint in ENTITY_HINTS, hint


def _ctx(entities: list[dict[str, Any]], kinds: list[str] | None) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "text": {"message": {"message": {"text": "x"}}},
        "contact": {"id": "1"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_order",
                "domain_hint": "order",
                "match_mode": "and",
                "access_levels": [],
                "entities": entities,
            }
        },
    }
    if kinds is not None:
        ctx["reference_kinds"] = kinds
    return ctx


def _ent(raw: str, hint: str) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}


def _body(entities, kinds):
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    return resolve_entity_body(_ctx(entities, kinds))


def test_order_hint_body_lists_visible_kinds():
    body = _body([_ent("202610-0838", "order")], [DO, SO, PO_REF])
    assert set(body.get("reference_kinds") or []) == {DO, SO, PO_REF}, body


def test_named_kind_body_still_lists_all_visible():
    body = _body([_ent("202610-0838", "sales_order")], [DO, SO, PO_REF, PO, PR])
    assert set(body.get("reference_kinds") or []) == {DO, SO, PO_REF, PO, PR}, body


def test_product_only_body_unchanged():
    body = _body([_ent("SRTWT7445", "product")], [DO, SO, PO_REF])
    assert "reference_kinds" not in body, body
