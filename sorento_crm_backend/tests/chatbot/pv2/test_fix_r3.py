"""PV2-FIX round 3 (crew live check, dev DB): staff signal, numbered replies, pick quantity,
customer ids, quoted text, report brand refine, v1's not-found wording. Red tests."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import answer as answer_mod
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.schema import Ask, Filter, Quantity, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, HandOver, Lookup, Option, ResolvedRef

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import ask as _base_ask, held as mk_held, mk_state, option, out, pending
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e
from tests.chatbot.pv2.test_core_engine_hook import fake_turn  # noqa: F401 - fixture
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401 - fixtures

TODAY = date(2026, 10, 15)
SO = "sales_orders.outstanding"


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


# ------------------------------------------------------------------ A staff signal


def _patch_contact(monkeypatch, *, tier, office, scope=None):
    import app.services.chatbot.head.access as access
    import app.services.chatbot.lanes.business.services as bsvc
    import app.services.contact_customer_scope as ccs
    import app.services.eta_policy as eta
    import app.services.field_access as fa

    monkeypatch.setattr(access, "_granted_field_reveal_keys", lambda *a, **k: [SO])
    monkeypatch.setattr(bsvc, "customer_scope", lambda *a, **k: scope)
    monkeypatch.setattr(fa, "resolve_contact_with_null_workspace_fallback", lambda *a, **k: "internal-1")
    monkeypatch.setattr(ccs, "is_office_staff", lambda db, contact_id: office)
    monkeypatch.setattr(eta, "resolve_request_contact", lambda *a, **k: object())
    monkeypatch.setattr(eta, "rules_for_contact", lambda *a, **k: SimpleNamespace(regions=["CENTRAL"]))
    return SimpleNamespace(tier=tier)


def test_is_staff_contact_tier_office_or_an_office_access_type(monkeypatch):
    from app.services.chatbot.pv2.turn import is_staff_contact

    profile = _patch_contact(monkeypatch, tier="office", office=False)
    assert is_staff_contact(object(), "c1", "s1", profile) is True
    profile = _patch_contact(monkeypatch, tier=None, office=True)
    assert is_staff_contact(object(), "c1", "s1", profile) is True
    profile = _patch_contact(monkeypatch, tier=None, office=False)
    assert is_staff_contact(object(), "c1", "s1", profile) is False


def test_an_office_access_contact_without_a_tier_is_unrestricted(monkeypatch):
    from app.services.chatbot.pv2.turn import scope_for_resolver

    profile = _patch_contact(monkeypatch, tier=None, office=True, scope=None)
    r = scope_for_resolver(object(), "c1", "s1", profile)
    assert r["reference_links"] is None
    assert "sales_order" in r["reference_kinds"]


def test_a_contact_with_no_tier_and_no_office_access_is_held_to_its_links(monkeypatch):
    from app.services.chatbot.pv2.turn import scope_for_resolver

    profile = _patch_contact(monkeypatch, tier=None, office=False, scope=None)
    assert scope_for_resolver(object(), "c1", "s1", profile)["reference_links"] == []


# ------------------------------------------------------------------ B numbered replies

CODES = [f"SRTWF12C-V{n}" for n in range(1, 9)]


def _asked_state():
    table = {"product|WF12C": ("ambiguous", [("product", c) for c in CODES], False)}
    state, _ = apply_turn(mk_state(), out(_ask(domain="incoming", entities=[_e(phrase="WF12C")])),
                          resolver=_Fake(table))
    return state


def _answer(state, **kw):
    return apply_turn(state, out(_ask(act="answers_bot_question", **kw)), resolver=_Fake({}))


def test_a_free_value_number_picks_that_option():
    state, actions = _answer(_asked_state(), answer={"option_key": None, "free_value": "6"})
    assert isinstance(actions[0], Lookup)
    assert [r.key for r in state.held.refs["product"]] == [CODES[5]]


def test_a_row_pick_number_picks_that_option():
    state, actions = _answer(_asked_state(), row_pick=6)
    assert isinstance(actions[0], Lookup)
    assert [r.key for r in state.held.refs["product"]] == [CODES[5]]


def _spec_pending():
    def opt(attr):
        return option(attr, attr, {"kind": "spec", "attribute": attr, "comparator": "eq", "value": 600, "unit": "mm"})

    return mk_state(mk_held("product_attachment", {"product": ["SRTWC286"]}),
                    pending_=pending("clarify_spec_attribute", [opt("dim_length"), opt("dim_width")]))


def test_a_number_picks_a_code_keyed_option_by_position():
    state, actions = _answer(_spec_pending(), answer={"option_key": None, "free_value": "2"})
    assert isinstance(actions[0], Lookup)
    assert [s.attribute for s in state.held.specs] == ["dim_width"]


def test_an_out_of_range_number_is_a_miss():
    _, actions = _answer(_asked_state(), answer={"option_key": None, "free_value": "9"})
    assert isinstance(actions[0], ClarifyAct) and actions[0].reask is True


def test_a_v1_open_question_free_value_still_passes_through():
    state = mk_state(mk_held("inventory", {"product": ["SRTWC286"]}),
                     pending_=pending("v1_open_question", [option("1", "A"), option("2", "B")]))
    _, actions = _answer(state, answer={"option_key": None, "free_value": "2"})
    assert isinstance(actions[0], Lookup) and actions[0].pending_kind == "v1_open_question"


# ------------------------------------------------------------------ C quantity across a v1 pick


def test_a_held_quantity_rides_a_v1_pick_as_qty_for_all_and_not_on_the_entity():
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    h.refs["product"][0] = ResolvedRef(kind="product", key="SRTWC286", label="SRTWC286",
                                       quantity=Quantity(number=5, unit=None))
    state = mk_state(h, pending_=pending("v1_open_question", [option("1", "A"), option("2", "B")]))
    state, actions = _answer(state, answer={"option_key": "1", "free_value": None})
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    assert_emission(v)
    assert v["open_question_answer"]["qty_for_all"] == 5
    assert [e["quantity"] for e in v["entities"] if e["hint"] == "product"] == [None]


# ------------------------------------------------------------------ D customer ids


def test_customer_refs_and_options_are_keyed_by_id_not_code(db):
    a = S.customer(db, "DUP1", "Acme Trading Sdn Bhd")
    b = S.customer(db, "DUP1X", "Acme Hardware Sdn Bhd")
    one = DbResolver(db, pinned={}).resolve_entities(
        [Entity.model_construct(kind="customer", key=None, phrase="Acme Trading Sdn Bhd", op="set",
                                refers_to_held=False, doc_number=None, quantity=None)], held=Held())[0]
    assert one.status == "exact" and [r.key for r in one.refs] == [str(a.id)]
    many = DbResolver(db, pinned={}).resolve_entities(
        [Entity.model_construct(kind="customer", key=None, phrase="Acme", op="set",
                                refers_to_held=False, doc_number=None, quantity=None)], held=Held())[0]
    assert many.status == "ambiguous"
    assert {o.key for o in many.options} == {str(a.id), str(b.id)}
    for o in many.options:
        S.assert_clean_label(o.label)


def test_a_held_customer_reaches_v1_in_its_carried_pick_shape():
    uid = "3f2b8c1e-5a4d-4e6f-9a7b-1c2d3e4f5a6b"
    h = mk_held("sales")
    h.refs["customer"] = [ResolvedRef(kind="customer", key=uid, label="Acme Trading Sdn Bhd")]
    v = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain="sales", held=h), today=TODAY)
    assert_emission(v)
    ent = [e for e in v["entities"] if e["hint"] == "customer"]
    assert len(ent) == 1
    e = ent[0]
    assert e["raw"] == "Acme Trading Sdn Bhd" and e["canonical_code"] is None
    assert e.get("uuid") == uid
    assert e["current_message"] is False and e["confident"] is True


# ------------------------------------------------------------------ E quoted reply


def test_pv2_gets_only_the_typed_line_of_a_quoted_reply():
    from app.services.chatbot import engine as engine_mod
    from tests.chatbot.test_engine import _envelope

    envelope = _envelope()
    envelope.message["message"]["message"]["text"] = "5件"
    envelope.message["message"]["replyTo"] = {"message": {"text": "Which quantity do you need?"}}
    assert engine_mod._pv2_text(envelope, "5件\nreply to: Which quantity do you need?\n") == "5件"


def test_the_engine_hook_hands_pv2_only_the_typed_line(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    from tests.chatbot.pv2.test_core_engine_hook import FakeResult, _set_contact_mode
    from app.services.chatbot import engine as engine_mod
    from tests.chatbot.test_engine import _envelope

    calls = fake_turn(FakeResult(direct_reply="ok", branch_kind="low_signal"))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    envelope = _envelope()
    envelope.message["message"]["message"]["text"] = "5件"
    envelope.message["message"]["replyTo"] = {"message": {"text": "Which quantity do you need?"}}
    engine_mod.run_turn(envelope, session_factory=session_factory)
    assert calls and calls[0].text == "5件", calls[0].text if calls else None


# ------------------------------------------------------------------ F report brand refine


def _sales_report(**kw):
    base = dict(dataset="sales", measures=["amount"], group_by=["customer"], top_n_per_group=None, filters=[],
                period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
                top_n=None, basis=None)
    base.update(kw)
    return ReportSpec.model_validate(base)


def _brand_ent(op):
    return {"kind": "brand", "key": "MOCHA", "phrase": "mocha", "op": op, "refers_to_held": False,
            "quantity": None, "doc_number": None}


def test_a_brand_entity_on_a_held_sales_report_becomes_a_brand_filter():
    h = mk_held("sales")
    h.report = _sales_report()
    table = {"brand|MOCHA": ("exact", [("brand", "MOCHA")], False)}
    state, actions = apply_turn(mk_state(h), out(_ask(act="refines_held", domain="sales", entities=[_brand_ent("set")])),
                                resolver=_Fake(table))
    filters = [(f.dimension, f.op, list(f.keys)) for f in state.held.report.filters]
    assert ("brand", "in", ["MOCHA"]) in filters, filters


def test_clearing_the_brand_removes_the_brand_filter():
    h = mk_held("sales", {"brand": ["MOCHA"]})
    h.report = _sales_report(filters=[Filter(dimension="brand", op="in", keys=["MOCHA"], values=[])])
    table = {"brand|MOCHA": ("exact", [("brand", "MOCHA")], False)}
    state, _ = apply_turn(mk_state(h), out(_ask(act="refines_held", domain="sales", entities=[_brand_ent("clear")])),
                          resolver=_Fake(table))
    assert not [f for f in state.held.report.filters if f.dimension == "brand"]


# ------------------------------------------------------------------ G v1's not-found wording


def _not_found(kind, *, offer, domain="order"):
    ent = {"kind": kind, "key": None, "phrase": None, "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": "202610-0151"}
    state, actions = apply_turn(mk_state(), out(_ask(domain=domain, entities=[ent])),
                                resolver=_Fake({}), can_offer_handover=offer)
    return state, actions[0]


def test_a_non_staff_not_found_document_offers_customer_service_in_v1s_words():
    state, act = _not_found("order", offer=True)
    text = answer_mod.reply_for(act, "en") if isinstance(act, ClarifyAct) else ""
    assert text == ('Couldn\'t find: "202610-0151" (order). '
                    "Would you like me to escalate to customer service team?"), text
    state, actions = apply_turn(state, out(_ask(act="answers_bot_question",
                                                answer={"option_key": "yes", "free_value": None})),
                                resolver=_Fake({}))
    assert isinstance(actions[0], HandOver) and actions[0].team_key == "customer_service"


def test_a_staff_not_found_document_has_no_offer():
    _, act = _not_found("order", offer=False)
    assert answer_mod.reply_for(act, "en") == 'Couldn\'t find: "202610-0151" (order).'


def test_a_delivery_order_kind_reads_delivery_order():
    _, act = _not_found("delivery_order", offer=False)
    assert answer_mod.reply_for(act, "en") == 'Couldn\'t find: "202610-0151" (delivery order).'
