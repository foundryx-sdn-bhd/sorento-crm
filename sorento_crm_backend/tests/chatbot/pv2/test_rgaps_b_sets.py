"""PV2 redesign R-GAPS-b, RG-5 (#1443): set codes. No model call.

DB level on a blank Postgres schema (the real `DbResolver` + `apply_turn`; `product_sets` comes from the models).
Sets answer in inventory only; a base code whose products belong to ACTIVE sets offers those sets as one pick.
"""
from __future__ import annotations

import uuid

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.models.product_set import ProductSet, ProductSetMember
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.schema import Quantity
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, ask, entity, out

PINNED = {"brand": set(), "product_type": {"Water Closet"}, "product_class": {"water_closet"},
          "attachment_type": set(), "spec_attribute": set(), "spec_value": set()}


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _set(db, code, members, *, active=True):
    row = ProductSet(id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID, set_code=code, name=f"Set {code}",
                     is_active=active)
    db.add(row)
    db.flush()
    for i, (product, qty) in enumerate(members):
        db.add(ProductSetMember(id=str(uuid.uuid4()), product_set_id=row.id, product_id=product.id, quantity=qty,
                                sort_order=i))
    db.flush()
    return row


def _products(db, *codes):
    cat = S.category(db, class_label="Water Closet")
    return [S.product(db, c, cat=cat) for c in codes]


def _turn(db, domain, phrase, *, quantity=None, can_offer=True):
    ent = entity("product", phrase=phrase)
    if quantity is not None:
        ent = ent.model_copy(update={"quantity": Quantity(number=quantity, unit=None)})
    parsed = out(ask(act="new_topic", domain=domain, entities=[ent]))
    return apply_turn(new_state(NOW), parsed, resolver=DbResolver(db, pinned=PINNED), now=NOW, can_offer_handover=can_offer)


def _codes(st):
    return sorted(r.key for r in st.held.refs.get("product", []))


# ------------------------------------------------------------------ a set code answers its member stock in inventory


def test_rg5_a_set_code_in_inventory_holds_the_member_products_and_asks_nothing(db):
    pedestal, cistern = _products(db, "SRTSETP-A", "SRTSETC-A")
    _set(db, "SRTSET1", [(pedestal, 1), (cistern, 1)])
    st, acts = _turn(db, "inventory", "SRTSET1")
    assert len(acts) == 1 and isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: a set code is never 'I need at least one filter'"
    assert _codes(st) == ["SRTSETC-A", "SRTSETP-A"], _codes(st)


def _member_quantities(st):
    return {r.key: (r.quantity.number if r.quantity else None) for r in st.held.refs["product"]}


def test_rg5_each_members_quantity_is_the_asked_quantity_times_its_set_quantity(db):
    a, b = _products(db, "SRTSETA-A", "SRTSETB-A")
    _set(db, "SRTSET2", [(a, 2), (b, 1)])
    st, acts = _turn(db, "inventory", "SRTSET2", quantity=5)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _member_quantities(st) == {"SRTSETA-A": 10, "SRTSETB-A": 5}


def test_rg5_with_no_asked_quantity_the_members_carry_no_quantity(db):
    a, b = _products(db, "SRTSETA-A", "SRTSETB-A")
    _set(db, "SRTSET2", [(a, 2), (b, 1)])
    st, acts = _turn(db, "inventory", "SRTSET2")
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _member_quantities(st) == {"SRTSETA-A": None, "SRTSETB-A": None}


# ------------------------------------------------------------------ a base code offers its active sets as one pick


@pytest.mark.parametrize("can_offer", [False, True], ids=["staff", "dealer"])
def test_rg5_a_base_code_whose_products_belong_to_active_sets_offers_the_sets_as_one_pick(db, can_offer):
    a, b = _products(db, "SRTWC8608-A", "SRTWC8608-B")
    _set(db, "SRTWC8608-SET1", [(a, 1), (b, 1)])
    _set(db, "SRTWC8608-SET2", [(a, 2)])
    st, acts = _turn(db, "inventory", "SRTWC8608", can_offer=can_offer)
    assert len(acts) == 1 and isinstance(acts[0], ClarifyAct), f"{type(acts[0]).__name__}: the active sets are offered"
    labels = " | ".join(f"{o.key} {o.label}" for o in acts[0].pending.options)
    assert "SRTWC8608-SET1" in labels and "SRTWC8608-SET2" in labels, labels


def test_rg5_an_inactive_set_is_not_offered_for_the_base_code(db):
    a, b = _products(db, "SRTWC8608-A", "SRTWC8608-B")
    _set(db, "SRTWC8608-SET1", [(a, 1), (b, 1)], active=True)
    _set(db, "SRTWC8608-OLD", [(a, 1)], active=False)
    st, acts = _turn(db, "inventory", "SRTWC8608")
    assert isinstance(acts[0], ClarifyAct)
    labels = " | ".join(f"{o.key} {o.label}" for o in acts[0].pending.options)
    assert "SRTWC8608-SET1" in labels and "SRTWC8608-OLD" not in labels, labels


# ------------------------------------------------------------------ guards: green today


def test_rg5_guard_an_inactive_set_typed_in_full_does_not_answer(db):
    pedestal, cistern = _products(db, "SRTSETP-A", "SRTSETC-A")
    _set(db, "SRTSETOLD", [(pedestal, 1), (cistern, 1)], active=False)
    st, acts = _turn(db, "inventory", "SRTSETOLD")
    assert _codes(st) == [], "an inactive set does not answer with its members"
    assert not isinstance(acts[0], Lookup) or not _codes(st)


@pytest.mark.parametrize("domain", ["order", "incoming", "master_products"])
def test_rg5_guard_other_domains_do_not_expand_a_set_into_its_members(db, domain):
    pedestal, cistern = _products(db, "SRTSETP-A", "SRTSETC-A")
    _set(db, "SRTSET1", [(pedestal, 1), (cistern, 1)])
    st, acts = _turn(db, domain, "SRTSET1")
    assert "SRTSETP-A" not in _codes(st) and "SRTSETC-A" not in _codes(st), _codes(st)
