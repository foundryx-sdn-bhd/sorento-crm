"""REF-PROBES S4: cross-kind reconciliation for reference numbers (AC-10, AC-12 to AC-14).
Pure: `apply_reconciliation` over hand-built `{raw: {kind: hits}}` maps.

Contract assumed: an unnamed kind is the hint "order"; every other reference hint
(delivery_order, sales_order, customer_po_ref, purchase_order, purchase_request) is a NAMED
kind. A named kind with an in-kind hit keeps only that kind. When the named kind and another
both hit by design (PO word: our PO + a customer's PO ref) the named kind is option 1.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot.turn.reconcile import apply_reconciliation


def _ent(raw: str, hint: str) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}


def test_unnamed_single_do_hit_rewrites():
    r = apply_reconciliation([_ent("202610-0838", "order")], {"202610-0838": {"customer_order": 1}})
    assert r.kind_pick_options is None
    assert [e["hint"] for e in r.entities] == ["customer_order"]


def test_unnamed_do_po_collision_asks_with_kind_words():
    number = "PO-2026/04-0011"
    r = apply_reconciliation([_ent(number, "order")], {number: {"customer_order": 1, "purchase_order": 1}})
    assert r.kind_pick_options is not None
    assert [o["label"] for o in r.kind_pick_options] == [
        "PO-2026/04-0011 (delivery order)",
        "PO-2026/04-0011 (purchase order)",
    ]


def test_named_kind_hit_beats_other_kind():
    r = apply_reconciliation(
        [_ent("202610-0838", "sales_order")],
        {"202610-0838": {"sales_order": 1, "customer_order": 1}},
    )
    assert r.kind_pick_options is None
    assert [e["hint"] for e in r.entities] == ["sales_order"]
    assert r.reconciled == []


def test_po_word_double_hit_asks():
    r = apply_reconciliation(
        [_ent("4500123", "purchase_order")],
        {"4500123": {"purchase_order": 1, "customer_po_ref": 1}},
    )
    assert r.kind_pick_options is not None and len(r.kind_pick_options) == 2
    assert r.kind_pick_options[0]["label"] == "4500123 (purchase order)"
    assert r.kind_pick_options[1]["label"] == "4500123 (PO ref)"


def test_named_kind_miss_rewrites_to_the_exact_other_kind():
    r = apply_reconciliation([_ent("X", "sales_order")], {"X": {"customer_order": 1}})
    assert r.kind_pick_options is None
    assert r.reconciled == [("X", "sales_order", "customer_order")]


def test_narrowed_miss_line():
    from app.services.chatbot.turn.reconcile import narrowed_miss_lines

    assert narrowed_miss_lines([("X", "sales_order", "customer_order")]) == ["No sales order X."]
    assert narrowed_miss_lines([("Y", "customer_po_ref", "sales_order")]) == ["No PO ref Y."]


def test_order_rewrite_is_no_narrowed_miss():
    from app.services.chatbot.turn.reconcile import narrowed_miss_lines

    assert narrowed_miss_lines([("X", "order", "customer_order")]) == []
