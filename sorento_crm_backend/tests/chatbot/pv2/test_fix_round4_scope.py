"""PV2-FIX round 4 (security review): reference-kind scoping, regions, exact documents."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.chatbot.contracts import visible_reference_kinds
from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.state import Held as StateHeld, Lookup, Miss

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import mk_state
from tests.chatbot.pv2.test_fix_doc_numbers import _order

SO = "sales_orders.outstanding"
PO = "purchase_orders.placed"


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ent(kind, phrase=None, doc_number=None) -> Entity:
    return Entity.model_construct(
        kind=kind, key=None, phrase=phrase, op="set", refers_to_held=False,
        doc_number=doc_number, quantity=None,
    )


# ------------------------------------------------------------------ 1 reference kinds


def _kinds(*args):
    from app.services.chatbot.pv2.turn import resolver_reference_kinds

    return resolver_reference_kinds(*args)


def test_contact_held_to_customer_links_keeps_so_and_po_ref_kinds():
    # PV2-FIX r6: crew 14 Oct, v1 parity: per-match scope (DbResolver._within_links) drops other
    # customers' matches, so the kinds themselves stay (engine._reference_kinds per_match_scope=True).
    assert _kinds([SO], False, True) == visible_reference_kinds([SO], False)


def test_unrestricted_contact_matches_visible_reference_kinds():
    assert _kinds([SO], False, False) == ["customer_order", "sales_order", "customer_po_ref"]
    assert _kinds([SO], False, False) == visible_reference_kinds([SO], False)


def test_no_grants_sees_delivery_orders_only():
    assert _kinds([], False, False) == ["customer_order"]


def test_staff_with_placed_and_so_grant_sees_purchase_kinds():
    kinds = _kinds([PO, SO], True, False)
    assert "purchase_order" in kinds and "purchase_request" in kinds and "sales_order" in kinds


# ------------------------------------------------------------------ 2 regions


def _spy(monkeypatch):
    calls: list[dict] = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(tokens=[], resolutions=[], elapsed_ms=0.0, truncated_tokens=[])

    monkeypatch.setattr("app.services.entity_resolver.resolve_references", fake)
    return calls


def test_regions_are_passed_to_every_v1_resolve_call(db, monkeypatch):
    calls = _spy(monkeypatch)
    r = DbResolver(db, pinned={}, regions=["CENTRAL"])
    r.resolve_entities([_ent("inbound_shipment", "TIIU1234567", "TIIU1234567")], held=Held())
    r.resolve_entities([_ent("product", "NOHITXYZ")], held=Held())
    assert calls, "no resolve_references call was made"
    for kw in calls:
        assert kw.get("regions") == ["CENTRAL"], kw


def test_no_regions_argument_does_not_crash(db, monkeypatch):
    _spy(monkeypatch)
    r = DbResolver(db, pinned={})
    r.resolve_entities([_ent("product", "NOHITXYZ")], held=Held())


# ------------------------------------------------------------------ 3 exact documents


def test_document_number_prefix_never_lists_candidates(db):
    _order(db, "202609-2012")
    _order(db, "202609-2013")
    res = DbResolver(db, pinned={}).resolve_entities(
        [_ent("delivery_order", "202609-201", "202609-201")], held=Held())[0]
    assert res.status == "none"
    assert res.options == [] and res.refs == []


def test_exact_document_number_still_exact(db):
    _order(db, "202609-2012")
    _order(db, "202609-2013")
    res = DbResolver(db, pinned={}).resolve_entities(
        [_ent("delivery_order", "202609-2012", "202609-2012")], held=Held())[0]
    assert res.status == "exact" and [r.key for r in res.refs] == ["202609-2012"]


# ------------------------------------------------------------------ 4 bridge


def test_miss_without_a_phrase_never_sends_the_literal_none():
    h = StateHeld(domain="inventory")
    action = Lookup(ask_index=0, domain="inventory", held=h, misses=[Miss(kind="product", phrase=None)])
    v = bridge.to_v1_verdict(mk_state(h), action, today=date(2026, 10, 14))
    assert_emission(v)
    assert all(e["raw"] != "None" for e in v["entities"]), v["entities"]
