"""PV2-FIX U2 quantity (and U3 doc_number schema field). Pure, no DB, no LLM."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import PV2_JSON_SCHEMA, Entity
from app.services.chatbot.pv2.state import Lookup

from tests.chatbot.pv2.carry_fakes import FakeResolver, ask, held, mk_state, out

TODAY = date(2026, 10, 13)
BASE = {"kind": "product", "key": None, "phrase": "SRTWC286-SH-200", "op": "set", "refers_to_held": False}


def _ent(**over) -> Entity:
    return Entity.model_validate({**BASE, **over})


def _entity_schema() -> dict:
    return PV2_JSON_SCHEMA["properties"]["asks"]["items"]["properties"]["entities"]["items"]


def _exact(*codes):
    return {f"product|{c}": {"status": "exact", "refs": [{"kind": "product", "key": c, "label": c}]} for c in codes}


def _qty(ref):
    q = getattr(ref, "quantity", None)
    return getattr(q, "number", q)


# ------------------------------------------------------------------ schema


def test_entity_accepts_quantity_object_and_null_and_missing():
    e = _ent(quantity={"number": 5, "unit": "unit"})
    assert e.quantity.number == 5 and e.quantity.unit == "unit"
    assert _ent(quantity=None).quantity is None
    assert Entity.model_validate(BASE).quantity is None  # old fixtures without the key


def test_entity_quantity_unit_may_be_null():
    assert _ent(quantity={"number": 14, "unit": None}).quantity.unit is None


def test_entity_accepts_doc_number():
    assert _ent(doc_number="202609-2012").doc_number == "202609-2012"
    assert _ent(doc_number=None).doc_number is None
    assert Entity.model_validate(BASE).doc_number is None


def test_strict_schema_requires_quantity_and_doc_number():
    node = _entity_schema()
    assert "quantity" in node["required"]
    assert "doc_number" in node["required"]
    q = node["properties"]["quantity"]
    variants = q.get("anyOf") or [q]
    obj = next(v for v in variants if v.get("type") == "object")
    assert sorted(obj["required"]) == ["number", "unit"]
    assert obj["additionalProperties"] is False
    assert any(v.get("type") == "null" for v in variants)


# ------------------------------------------------------------------ apply


def _apply(state, parsed, codes):
    return apply_turn(state, parsed, resolver=FakeResolver({"entities": _exact(*codes)}))


def test_u2_1_quantity_rides_on_the_held_product():
    parsed = out(ask(act="new_topic", domain="inventory", entities=[_ent(quantity={"number": 5, "unit": "unit"})]))
    state, actions = _apply(mk_state(), parsed, ["SRTWC286-SH-200"])
    assert len(actions) == 1 and isinstance(actions[0], Lookup)
    ref = state.held.refs["product"][0]
    assert _qty(ref) == 5
    assert getattr(ref.quantity, "unit", None) == "unit"
    assert state.held.specs == []


def test_u2_2_three_products_each_quantity_two():
    codes = ["SRT2367", "SRT2565", "SRT93-SS"]
    ents = [_ent(phrase=c, quantity={"number": 2, "unit": "pc"}) for c in codes]
    state, _ = _apply(mk_state(), out(ask(act="new_topic", domain="inventory", entities=ents)), codes)
    refs = state.held.refs["product"]
    assert [r.key for r in refs] == codes
    assert [_qty(r) for r in refs] == [2, 2, 2]


def test_u2_4_stock_refinement_keeps_the_held_quantity():
    first = out(ask(act="new_topic", domain="product_attachment", entities=[_ent(quantity={"number": 5, "unit": "unit"})]))
    state, _ = _apply(mk_state(), first, ["SRTWC286-SH-200"])
    state, actions = _apply(state, out(ask(act="refines_held", domain="inventory")), ["SRTWC286-SH-200"])
    assert isinstance(actions[0], Lookup)
    assert _qty(state.held.refs["product"][0]) == 5


def test_u2_4_refinement_with_a_new_quantity_replaces_it():
    first = out(ask(act="new_topic", domain="inventory", entities=[_ent(phrase="CB301", quantity={"number": 20, "unit": None})]))
    state, _ = _apply(mk_state(), first, ["CB301"])
    held_ent = _ent(phrase=None, refers_to_held=True, quantity={"number": 14, "unit": None})
    state, actions = _apply(state, out(ask(act="refines_held", domain="inventory", entities=[held_ent])), ["CB301"])
    assert isinstance(actions[0], Lookup)
    assert [r.key for r in state.held.refs["product"]] == ["CB301"]
    assert _qty(state.held.refs["product"][0]) == 14


# ------------------------------------------------------------------ bridge


def _bridged(qty):
    e = _ent(quantity=qty) if qty is not None else Entity.model_validate(BASE)
    parsed = out(ask(act="new_topic", domain="inventory", entities=[e]))
    state, actions = _apply(mk_state(), parsed, ["SRTWC286-SH-200"])
    return bridge.to_v1_verdict(state, actions[0], today=TODAY)


def test_u2_1_bridge_puts_quantity_on_the_v1_entity_and_stays_an_emission():
    v = _bridged({"number": 5, "unit": "unit"})
    assert_emission(v)
    prod = [e for e in v["entities"] if e["hint"] == "product"]
    assert len(prod) == 1 and prod[0]["canonical_code"] == "SRTWC286-SH-200"
    assert prod[0]["quantity"] == 5


def test_u2_1_parity_with_the_v1_parser_verdict_for_5unit():
    # v1 parser for "check stock SRTWC286-SH-200 5unit": raw/canonical the code, hint product, quantity 5.
    expected = ("SRTWC286-SH-200", "product", 5)
    prod = next(e for e in _bridged({"number": 5, "unit": "unit"})["entities"] if e["hint"] == "product")
    assert (prod["canonical_code"], prod["hint"], prod["quantity"]) == expected
    assert prod["raw"] == "SRTWC286-SH-200"


def test_no_quantity_stays_none_on_the_v1_entity():
    prod = next(e for e in _bridged(None)["entities"] if e["hint"] == "product")
    assert prod["quantity"] is None


def test_focus_rows_still_build_with_a_quantity_on_the_ref():
    parsed = out(ask(act="new_topic", domain="inventory", entities=[_ent(quantity={"number": 5, "unit": "unit"})]))
    state, _ = _apply(mk_state(), parsed, ["SRTWC286-SH-200"])
    f = bridge.focus_from_v2(state.held)
    assert [r["canonical_code"] for r in f.products] == ["SRTWC286-SH-200"]
    assert f.products[0]["current_message"] is False
