"""PV2-FIX round 2: cross-type document fallback, list misses, status, portal_form.

Red tests from the coordinator's round-2 brief (dev eval run 1 losses). No LLM.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.schema import PV2_JSON_SCHEMA, Ask
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option, ResolvedRef

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import FakeResolution, ask, mk_state, out
from tests.chatbot.pv2.test_fix_doc_numbers import _order  # seeds an orders row

TODAY = date(2026, 10, 14)


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ent(kind, phrase=None, doc_number=None, **extra) -> Entity:
    return Entity.model_construct(
        kind=kind, key=None, phrase=phrase, op="set", refers_to_held=False,
        doc_number=doc_number, quantity=None, **extra,
    )


def _resolve(db, ent, **kw):
    return DbResolver(db, pinned={}, **kw).resolve_entities([ent], held=Held())[0]


# ------------------------------------------------------------------ R1 resolver


# PV2-FIX r2: crew 14 Oct, no cross-type document retry (replaces the round-2 fallback tests).
def test_r1_inbound_shipment_number_never_retries_as_a_delivery_order(db):
    _order(db, "202610-0838")
    res = _resolve(db, _ent("inbound_shipment", "202610-0838", None))
    assert res.status == "none"


def test_r1_purchase_order_kind_the_asker_cannot_see_is_none(db):
    _order(db, "PS202609-0469")
    res = _resolve(db, _ent("purchase_order", "PS202609-0469", None))
    assert res.status == "none"


def test_r1_unresolved_number_reruns_across_types_products_last(db):
    S.product(db, "MBF97503")
    S.product(db, "MMC97503")
    res = _resolve(db, _ent("order", "97503", "97503"))
    assert res.status == "ambiguous"
    assert {o.key for o in res.options} == {"MBF97503", "MMC97503"}
    assert all(o.payload.get("kind") == "product" for o in res.options)


def test_r1_order_kind_phrase_that_is_a_product_resolves_to_the_product(db):
    S.product(db, "SRTKT71SS")
    res = _resolve(db, _ent("order", "Srtkt71-ss"))
    assert res.status == "exact"
    assert res.refs[0].kind == "product" and res.refs[0].key == "SRTKT71SS"


def test_r1_did_you_mean_flag(db):
    S.product(db, "MKS9505-SS-HC")
    S.product(db, "SRTWF12C")
    S.product(db, "SRTWF12C-BL-NEW")
    near = _resolve(db, _ent("product", "MKS9505-HC-SS"))
    assert getattr(near, "did_you_mean", None) is True
    family = _resolve(db, _ent("product", "WF12C"))
    assert family.status == "ambiguous"
    assert getattr(family, "did_you_mean", None) is False


# ------------------------------------------------------------------ R2 apply


class _Fake:
    """kind|key-or-phrase -> (status, [(kind, key)], did_you_mean)."""

    def __init__(self, table):
        self.table = table

    def resolve_entities(self, ents, *, held):
        res = []
        for e in ents:
            status, refs, dym = self.table.get(f"{e.kind}|{e.key or e.phrase}", ("none", [], False))
            options = [Option(key=k, label=k, payload={"kind": kd, "key": k}) for kd, k in refs] if status == "ambiguous" else []
            r = FakeResolution(
                status=status, phrase=e.phrase,
                refs=[ResolvedRef(kind=kd, key=k, label=k, phrase=e.phrase) for kd, k in refs] if status == "exact" else [],
                options=options,
            )
            r.did_you_mean = dym
            res.append(r)
        return res

    def resolve_specs(self, specs, *, held):
        return []

    def resolve_attachment_type(self, key, *, held):
        return FakeResolution(status="none")


def _e(kind="product", phrase=None, key=None, refers=False, quantity=None):
    return {"kind": kind, "key": key, "phrase": phrase, "op": "set", "refers_to_held": refers,
            "quantity": quantity, "doc_number": None}


def _ask(act="new_topic", **kw) -> Ask:
    base = ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _run(table, entities, domain="incoming"):
    parsed = out(_ask(domain=domain, entities=entities))
    return apply_turn(mk_state(), parsed, resolver=_Fake(table))


def _three(b_status, dym):
    return _run(
        {"product|SRTA": ("exact", [("product", "SRTA")], False),
         "product|SRTB": (b_status, [("product", "SRTB1"), ("product", "SRTB2")] if b_status == "ambiguous" else [], dym),
         "product|SRTC": ("exact", [("product", "SRTC")], False)},
        [_e(phrase="SRTA"), _e(phrase="SRTB"), _e(phrase="SRTC")],
    )


def test_r2_a_missing_item_in_a_list_does_not_block_the_found_ones():
    state, actions = _three("none", False)
    assert len(actions) == 1 and isinstance(actions[0], Lookup)
    assert [r.key for r in state.held.refs["product"]] == ["SRTA", "SRTC"]
    assert [(m.kind, m.phrase) for m in actions[0].misses] == [("product", "SRTB")]


def test_r2_a_did_you_mean_item_in_a_list_is_a_miss_not_a_question():
    state, actions = _three("ambiguous", True)
    assert isinstance(actions[0], Lookup)
    assert [(m.kind, m.phrase) for m in actions[0].misses] == [("product", "SRTB")]


def test_r2_real_candidates_in_a_list_still_ask():
    _, actions = _three("ambiguous", False)
    assert isinstance(actions[0], ClarifyAct)


def test_r2_a_single_none_entity_is_still_not_found():
    _, actions = _run({}, [_e(phrase="SRTB")])
    assert isinstance(actions[0], ClarifyAct) and actions[0].not_found is True


def test_r2_doc_found_as_a_delivery_order_answers_in_the_order_domain():
    state, actions = _run({"inbound_shipment|202610-0838": ("exact", [("delivery_order", "202610-0838")], False)},
                          [_e(kind="inbound_shipment", phrase="202610-0838")], domain="incoming")
    assert isinstance(actions[0], Lookup)
    assert actions[0].held.domain == "order"


def test_r2_quantity_only_entity_attaches_to_the_named_product():
    state, actions = _run(
        {"product|SRTWT5807": ("exact", [("product", "SRTWT5807")], False)},
        [_e(phrase="SRTWT5807"), _e(phrase=None, quantity={"number": 2, "unit": "UNIT"})],
        domain="inventory",
    )
    assert isinstance(actions[0], Lookup)
    refs = state.held.refs["product"]
    assert [r.key for r in refs] == ["SRTWT5807"]
    assert refs[0].quantity.number == 2


# ------------------------------------------------------------------ R3 schema + bridge


def test_r3_ask_accepts_status_and_portal_form_and_defaults_to_none():
    base = ask().model_dump()
    assert Ask.model_validate({**base, "status": "outstanding", "portal_form": "complaint"}).status == "outstanding"
    old = Ask.model_validate(base)
    assert old.status is None and old.portal_form is None
    for bad in ({"status": "late"}, {"portal_form": "random"}):
        with pytest.raises(Exception):
            Ask.model_validate({**base, **bad})


def test_r3_strict_schema_requires_status_and_portal_form_on_the_ask():
    node = PV2_JSON_SCHEMA["properties"]["asks"]["items"]
    assert "status" in node["required"] and "portal_form" in node["required"]
    enum_of = lambda n: sorted(v for v in n.get("enum", []) if v is not None) or sorted(
        x for alt in n.get("anyOf", []) for x in alt.get("enum", []))
    assert enum_of(node["properties"]["status"]) == ["delivered", "outstanding"]
    assert enum_of(node["properties"]["portal_form"]) == [
        "complaint", "purchase_request", "sponsorship_form", "stock_inquiry"]


def _bridge(table, entities, domain, **kw):
    parsed = out(_ask(domain=domain, entities=entities, **kw))
    state, actions = apply_turn(mk_state(), parsed, resolver=_Fake(table))
    return bridge.to_v1_verdict(state, actions[0], today=TODAY)


def test_r3_status_reaches_the_v1_verdict():
    v = _bridge({"product|SRTA": ("exact", [("product", "SRTA")], False)}, [_e(phrase="SRTA")],
                "order", status="outstanding")
    assert_emission(v)
    assert v["status"] == "outstanding"


def test_r3_portal_form_reaches_the_v1_verdict():
    v = _bridge({}, [], "portal_link", portal_form="stock_inquiry")
    assert_emission(v)
    assert v["portal_form"] == "stock_inquiry"


def test_r3_misses_become_unresolved_v1_entities():
    v = _bridge({"product|SRTA": ("exact", [("product", "SRTA")], False)},
                [_e(phrase="SRTA"), _e(phrase="srtwc7014")], "incoming")
    assert_emission(v)
    miss = [e for e in v["entities"] if e["raw"] == "srtwc7014"]
    assert len(miss) == 1
    m = miss[0]
    assert (m["hint"], m["canonical_code"], m["confident"], m["current_message"]) == ("product", None, False, True)
