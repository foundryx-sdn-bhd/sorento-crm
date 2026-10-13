"""PV2-FIX r2: a non-staff contact's document lookup is scoped to its own linked customers.

Carrier pinned here: `DbResolver(..., reference_links=<list of customer ids> | None)`. None means no
restriction (staff); a list (possibly empty) means held to those customers, as v1's
`engine._reference_scope` does for any non-staff contact (the customer_scope dict carries
links only for ENFORCED dealers, so it cannot be the carrier for every non-staff contact).
"""
from __future__ import annotations

import uuid
from datetime import date
from datetime import date as _date

import pytest

from app.models.order import Order, OrderStatus
from app.services.chatbot.head.parser import assert_emission
from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.state import Lookup
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import held as mk_held, mk_state


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _seed(db):
    x = S.customer(db, "CUSTX", "Customer X Sdn Bhd")
    y = S.customer(db, "CUSTY", "Customer Y Sdn Bhd")
    status = OrderStatus(id=str(uuid.uuid4()), status_code=f"ST{uuid.uuid4().hex[:8]}", status_name="Delivered", sequence=1)
    db.add(status)
    db.flush()
    for number, cust in (("PS202609-0469", x), ("202609-0469", y)):
        db.add(Order(id=str(uuid.uuid4()), order_number=number, order_date=_date(2026, 9, 29),
                     order_status_id=status.id, customer_id=cust.id, debtor_name=cust.customer_name,
                     is_cancelled=False, company_id=DEFAULT_COMPANY_ID))
    db.flush()
    return x, y


def _ent(doc_number):
    return Entity.model_construct(kind="delivery_order", key=None, phrase=None, op="set",
                                  refers_to_held=False, doc_number=doc_number, quantity=None)


def _resolve(db, number, **kw):
    return DbResolver(db, pinned={}, **kw).resolve_entities([_ent(number)], held=Held())[0]


def _keys(res):
    return {r.key for r in res.refs} | {o.key for o in res.options}


def test_linked_non_staff_never_gets_another_customers_document_from_a_stripped_number(db):
    x, _ = _seed(db)
    res = _resolve(db, "202609-0469", reference_links=[x.id])
    assert "202609-0469" not in _keys(res), res
    assert res.status in ("none", "ambiguous")


def test_linked_non_staff_still_gets_its_own_document(db):
    x, _ = _seed(db)
    res = _resolve(db, "PS202609-0469", reference_links=[x.id])
    assert res.status == "exact" and [r.key for r in res.refs] == ["PS202609-0469"]


def test_unlinked_non_staff_gets_no_document(db):
    _seed(db)
    res = _resolve(db, "202609-0469", reference_links=[])
    assert res.status == "none" and not _keys(res)


def test_staff_without_a_links_restriction_is_unchanged(db):
    _seed(db)
    res = _resolve(db, "202609-0469")
    assert res.status == "exact" and [r.key for r in res.refs] == ["202609-0469"]


def test_the_v1_verdict_names_the_number_actually_found():
    h = mk_held("order", {"delivery_order": ["PS202609-0469"]})
    v = bridge.to_v1_verdict(mk_state(h), Lookup(ask_index=0, domain=h.domain, held=h), today=date(2026, 10, 14))
    assert_emission(v)
    ent = [e for e in v["entities"] if e["canonical_code"] == "PS202609-0469"]
    assert len(ent) == 1 and ent[0]["raw"] == "PS202609-0469"
