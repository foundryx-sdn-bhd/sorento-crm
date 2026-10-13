"""PV2-FIX U3: bare document numbers and loose form names resolve through v1's lookups.

Red tests from the UAC (U3.1 to U3.3). Blank Postgres schema, no LLM.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models.forms import Form
from app.models.order import Order, OrderStatus
from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import held as mk_held, mk_state
from app.services.chatbot.pv2.state import Lookup


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _entity(kind, phrase=None, doc_number=None) -> Entity:
    # model_construct: the resolver tests fail on resolver behaviour today, not on the
    # not-yet-existing schema field (the schema asserts live in test_fix_quantity.py).
    return Entity.model_construct(
        kind=kind, key=None, phrase=phrase, op="set", refers_to_held=False, doc_number=doc_number
    )


def _resolve(db, ent):
    return DbResolver(db, pinned={}).resolve_entities([ent], held=Held())[0]


def _order(db, number):
    status = OrderStatus(
        id=str(uuid.uuid4()), status_code=f"ST{uuid.uuid4().hex[:8]}", status_name="Delivered", sequence=1
    )
    db.add(status)
    db.flush()
    row = Order(
        id=str(uuid.uuid4()), order_number=number, order_date=date(2026, 9, 29), order_status_id=status.id,
        debtor_name="Test Debtor Sdn Bhd", is_cancelled=False, company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row


def _form(db, code, name, form_type="marketing"):
    row = Form(
        id=str(uuid.uuid4()), code=code, name=name, form_type=form_type, is_active=True,
        access_levels=["dealer", "end_user"],
    )
    db.add(row)
    db.flush()
    return row


# ------------------------------------------------------------------ DB resolver


def test_u3_1_delivery_order_number_resolves_exactly(db):
    _order(db, "202609-2012")
    _order(db, "PS202609-0469")
    # PV2-FIX r2: crew 14 Oct, verbatim document numbers (phrase null when only a kind word precedes it).
    res = _resolve(db, _entity("delivery_order", None, "202609-2012"))
    assert res.status == "exact"
    assert [r.key for r in res.refs] == ["202609-2012"]
    S.assert_clean_label(res.refs[0].label)


def test_u3_2_prefixed_delivery_order_number_resolves_exactly(db):
    _order(db, "202609-2012")
    _order(db, "PS202609-0469")
    res = _resolve(db, _entity("delivery_order", None, "PS202609-0469"))
    assert res.status == "exact"
    assert [r.key for r in res.refs] == ["PS202609-0469"]


def test_u3_2_unnamed_number_probes_the_reference_kinds(db):
    _order(db, "202610-0838")
    res = _resolve(db, _entity("order", "202610-0838", "202610-0838"))
    assert res.status == "exact"
    assert res.refs[0].key == "202610-0838"


def test_unknown_document_number_is_none(db):
    _order(db, "202609-2012")
    res = _resolve(db, _entity("delivery_order", "Delivery 209999-0001", "209999-0001"))
    assert res.status == "none"
    assert res.refs == []


def test_u3_3_loose_form_name_gives_form_candidates_never_none(db):
    _form(db, "DISPLAY_STANDEE_APP_MOCHA", "DISPLAY STANDEE APPLICATION FORM (MOCHA)")
    _form(db, "CONCEPT_RENOVATION_MOCHA", "CONCEPT RENOVATION FORM (MOCHA)")
    _form(db, "SORENTO_SPONSORSHIP", "SORENTO SPONSORSHIP REQUEST FORM")
    res = _resolve(db, _entity("form", "Mocha Application form"))
    assert res.status != "none", "v1's form search finds the MOCHA forms"
    labels = [o.label for o in res.options] + [r.label for r in res.refs]
    assert labels
    assert any("MOCHA" in label for label in labels)
    for label in labels:
        S.assert_clean_label(label)
        assert "_" not in label, f"form code shown instead of the name: {label!r}"
    assert not any("SPONSORSHIP" in label for label in labels)


# ------------------------------------------------------------------ bridge


def test_u3_1_held_delivery_order_reaches_v1_with_a_kind_v1_accepts():
    from app.services.chatbot.contracts import ENTITY_HINTS

    h = mk_held("delivery_status", {"delivery_order": ["202609-2012"]})
    v = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain=h.domain, held=h), today=date(2026, 10, 13))
    assert_emission(v)
    ents = [e for e in v["entities"] if e["canonical_code"] == "202609-2012"]
    assert len(ents) == 1
    assert ents[0]["hint"] in ENTITY_HINTS
    assert ents[0]["hint"] in {"delivery_order", "order"}
    assert ents[0]["raw"] == "202609-2012"
