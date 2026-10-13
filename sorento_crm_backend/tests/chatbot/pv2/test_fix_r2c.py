"""PV2-FIX r2 batch C (dev run r2-1): products after an empty document kind; the not-found
line names the document number."""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import answer as answer_mod
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import ClarifyAct

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_doc_numbers import _order
from tests.chatbot.pv2.test_fix_round2 import _Fake


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ent(kind, phrase=None, doc_number=None) -> Entity:
    return Entity.model_construct(kind=kind, key=None, phrase=phrase, op="set", refers_to_held=False,
                                  doc_number=doc_number, quantity=None)


def test_product_code_under_an_empty_purchase_order_kind_resolves_to_the_product(db):
    S.product(db, "SRT373-8")
    kinds = ["customer_order", "purchase_order", "purchase_request"]
    res = DbResolver(db, pinned={}, reference_kinds=kinds).resolve_entities(
        [_ent("purchase_order", "SRT373-8")], held=Held())[0]
    assert res.status == "exact"
    assert res.refs[0].kind == "product" and res.refs[0].key == "SRT373-8"


def test_named_document_kind_never_lands_on_another_document_kind(db):
    _order(db, "PS202609-0469")
    res = DbResolver(db, pinned={}, reference_kinds=["customer_order"]).resolve_entities(
        [_ent("purchase_order", None, "PS202609-0469")], held=Held())[0]
    assert "delivery_order" not in {r.kind for r in res.refs}
    assert all(o.payload.get("kind") == "product" for o in res.options)
    assert res.status in ("none", "exact", "ambiguous")
    assert not (res.status == "exact" and not res.refs)


def test_not_found_document_number_is_named_in_the_question():
    ent = {"kind": "purchase_order", "key": None, "phrase": None, "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": "PS202609-0469"}
    base = _base_ask().model_dump()
    base.update(domain="order", entities=[ent])
    _, actions = apply_turn(mk_state(), out(Ask.model_validate(base)), resolver=_Fake({}))
    act = actions[0]
    assert isinstance(act, ClarifyAct) and act.not_found is True
    text = answer_mod.reply_for(act, "en")
    assert "PS202609-0469" in text, text
    assert "Which one do you mean?" not in text
