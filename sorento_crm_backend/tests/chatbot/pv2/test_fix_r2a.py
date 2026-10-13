"""PV2-FIX r2 batch A: verbatim exact documents, numbered picks, pinned specs, portal not blocked."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.chatbot.pv2 import clarify_copy, history, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held, Spec
from app.services.chatbot.pv2.schema import Ask, IndexKeys
from app.services.chatbot.pv2.state import ClarifyAct, Lookup

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_doc_numbers import _order
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

NOW = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ent(kind, phrase=None, doc_number=None) -> Entity:
    return Entity.model_construct(
        kind=kind, key=None, phrase=phrase, op="set", refers_to_held=False, doc_number=doc_number, quantity=None
    )


def _res(db, ent, **kw):
    return DbResolver(db, pinned={}, **kw).resolve_entities([ent], held=Held())[0]


# ------------------------------------------------------------------ 1 verbatim exact documents


def test_verbatim_phrase_beats_a_stripped_doc_number(db):
    _order(db, "PS202609-0469")
    _order(db, "202609-0469")
    res = _res(db, _ent("delivery_order", "PS202609-0469", "202609-0469"))
    assert res.status == "exact"
    assert [r.key for r in res.refs] == ["PS202609-0469"]


def test_doc_number_alone_with_prefix_is_exact(db):
    _order(db, "PS202609-0469")
    _order(db, "202609-0469")
    res = _res(db, _ent("delivery_order", None, "PS202609-0469"))
    assert res.status == "exact" and [r.key for r in res.refs] == ["PS202609-0469"]


def test_differing_phrase_gets_no_stripped_retry(db):
    _order(db, "202609-2012")
    res = _res(db, _ent("delivery_order", "Delivery 202609-2012", "202609-2012"))
    assert res.status == "none"


def test_doc_number_alone_is_exact(db):
    _order(db, "202609-2012")
    res = _res(db, _ent("delivery_order", None, "202609-2012"))
    assert res.status == "exact" and [r.key for r in res.refs] == ["202609-2012"]


def test_no_cross_type_document_retry(db):
    _order(db, "202610-0838")
    _order(db, "PS202609-0469")
    assert _res(db, _ent("inbound_shipment", "202610-0838", None)).status == "none"
    assert _res(db, _ent("purchase_order", "PS202609-0469", None), reference_kinds=["customer_order"]).status == "none"


def test_unnamed_order_hint_is_one_exact_lookup_over_visible_kinds(db):
    _order(db, "202610-0838")
    res = _res(db, _ent("order", "202610-0838", None))
    assert res.status == "exact"
    assert res.refs[0].kind == "delivery_order" and res.refs[0].key == "202610-0838"


def test_unnamed_order_hint_still_offers_products_for_a_non_document(db):
    S.product(db, "MBF97503")
    S.product(db, "MMC97503")
    res = _res(db, _ent("order", "97503", None))
    assert res.status == "ambiguous"
    assert {o.key for o in res.options} == {"MBF97503", "MMC97503"}


def test_document_number_is_exact_only(db):
    _order(db, "202609-0469")
    assert _res(db, _ent("delivery_order", None, "202609-04")).status == "none"


# ------------------------------------------------------------------ 2 numbered picks

CODES = ["SRTWF12C", "SRTWF12C-BL", "SRTWF12C-BK", "SRTWF12C-WH", "SRTWF12C-GR", "SRTWF12C-GM", "SRTWF12C-GM-NEW"]


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _first_turn():
    table = {"product|WF12C": ("ambiguous", [("product", c) for c in CODES], False)}
    state, actions = apply_turn(mk_state(), out(_ask(domain="incoming", entities=[_e(phrase="WF12C")])),
                                resolver=_Fake(table))
    return state, actions


def test_pending_options_are_keyed_by_position():
    _, actions = _first_turn()
    act = actions[0]
    assert isinstance(act, ClarifyAct)
    opts = act.pending.options
    assert [o.key for o in opts] == [str(i) for i in range(1, len(CODES) + 1)]
    assert [o.label for o in opts] == CODES
    assert all(o.payload.get("kind") == "product" and o.payload.get("key") == c for o, c in zip(opts, CODES))


def test_picking_six_resolves_the_sixth_code_and_keeps_the_domain():
    state, _ = _first_turn()
    state, actions = apply_turn(state, out(_ask(act="answers_bot_question",
                                                answer={"option_key": "6", "free_value": None})),
                                resolver=_Fake({}))
    assert isinstance(actions[0], Lookup)
    assert [r.key for r in state.held.refs["product"]] == [CODES[5]]
    assert state.held.domain == "incoming"


def test_rendered_question_and_memory_number_the_same_order():
    state, actions = _first_turn()
    pending = actions[0].pending
    text = clarify_copy.render(pending, "en")
    for i, code in enumerate(CODES, start=1):
        assert f"{i}. {code}" in text.splitlines()
    from app.services.chatbot.pv2.history import MemoryProfile

    mem = history.render_memory(state.model_copy(update={"pending": pending}), MemoryProfile(audience="staff"), NOW)
    assert f"6=6 ({CODES[5]})" in mem


# ------------------------------------------------------------------ 3 pinned spec keys


def test_pinned_carries_spec_attributes_and_values():
    keys = IndexKeys(
        spec_attributes=frozenset({"dim_length"}),
        spec_values={"finish": frozenset({"black"})},
        attachment_types=frozenset({"certification"}),
    )
    pinned = turn._pinned(keys)
    assert pinned["spec_attribute"] == {"dim_length"}
    assert pinned["spec_value"] == {"finish.black"}
    assert pinned["attachment_type"] == {"certification"}


def test_resolver_built_from_pinned_resolves_an_attribute_spec(db):
    S.registry(db, "dim_length", "Length", "numeric", unit="mm")
    pinned = turn._pinned(IndexKeys(spec_attributes=frozenset({"dim_length"})))
    spec = Spec(attribute="dim_length", value_key=None, number=600, number_to=None, unit="mm",
                comparator="eq", op="set", phrase=None)
    res = DbResolver(db, pinned=pinned).resolve_specs([spec], held=Held())[0]
    assert res.status == "exact"
    assert res.specs[0].attribute == "dim_length"


# ------------------------------------------------------------------ 4 portal form not blocked


def test_portal_form_is_not_blocked_by_an_unresolved_form_entity():
    state, actions = apply_turn(
        mk_state(),
        out(_ask(domain="portal_link", portal_form="stock_inquiry", entities=[_e(kind="form", phrase="stock inquiry")])),
        resolver=_Fake({}),
    )
    assert len(actions) == 1 and isinstance(actions[0], Lookup)
    assert actions[0].domain == "portal_link"
