"""PV2 R5 slice G (UAC R5-8): a non-staff contact with NO customer links sees no other customer. Red tests.

Real DB (blank schema, rolled back), the real `turn.scope_for_resolver` and the real `DbResolver`; the parse is
hand-written. The contact is classified by `contact_asker.classify` (app/services/contact_asker.py:97): an unlinked
non-staff contact reads `unclassified`, a salesperson (a sales_agents row on the contact) reads `salesperson` and keeps
staff scope (V1-DOC-SCOPE-SALES).
"""
from __future__ import annotations

import uuid

import pytest

from app.models.access import RespondContact, RespondContactCustomer
from app.models.sales_agent import SalesAgent
from app.services import contact_asker
from app.services.chatbot.pv2 import clarify_copy, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from app.services.chatbot.turn.state import Profile
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, ask, entity, out

PINNED = {"brand": set(), "product_type": set(), "attachment_type": set(), "spec_attribute": set()}


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _contact(db):
    row = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6019{uuid.uuid4().int % 10**8:08d}",
                         respond_io_id=f"ZZT-g-{uuid.uuid4().hex[:8]}")
    db.add(row)
    db.flush()
    return row


def _link(db, contact, cust):
    db.add(RespondContactCustomer(id=str(uuid.uuid4()), contact_id=contact.id, customer_id=cust.id,
                                  company_id=DEFAULT_COMPANY_ID))
    db.flush()


def _salesperson(db, contact):
    db.add(SalesAgent(id=str(uuid.uuid4()), sales_agent=f"ZZTAGENT{uuid.uuid4().hex[:6]}".upper(),
                      contact_id=contact.id, company_id=DEFAULT_COMPANY_ID))
    db.flush()


def _resolver(db, contact, *, tier=None):
    scope = turn.scope_for_resolver(db, contact.respond_io_id, None, Profile(tier=tier))
    scope.pop("granted_reveals")
    scope.pop("staff")
    return DbResolver(db, pinned=PINNED, **scope), scope


def _say(resolver, phrase, domain="order"):
    parsed = out(ask(act="new_topic", domain=domain, entities=[entity("customer", phrase=phrase)]))
    from app.services.chatbot.pv2.state import new_state

    return apply_turn(new_state(NOW), parsed, resolver=resolver, now=NOW, can_offer_handover=False)


def _everything_said(st, acts):
    texts = []
    for a in acts:
        if isinstance(a, ClarifyAct):
            texts.append(clarify_copy.render(a.pending, "en"))
            texts += [o.label for o in a.pending.options]
            texts += [str(o.payload) for o in a.pending.options]
    texts += [r.label for refs in st.held.refs.values() for r in refs]
    return "\n".join(texts)


def _other_customers(db):
    S.customer(db, "300-Z001", "KOPI ZEBRA SDN BHD")
    S.customer(db, "300-Z002", "BATU ZEBRA ENTERPRISE")


# ------------------------------------------------------------------ 1 an unlinked contact finds nobody by name

@pytest.mark.parametrize("phrase", ["zebra", "kopi zeb"])
def test_g1_an_unlinked_contact_asking_by_partial_customer_name_gets_no_customer_name_back(db, phrase):
    _other_customers(db)
    c = _contact(db)
    assert contact_asker.classify(db, c.id) == "unclassified"
    resolver, _ = _resolver(db, c)
    st, acts = _say(resolver, phrase)
    said = _everything_said(st, acts)
    assert "KOPI ZEBRA" not in said.upper() and "BATU ZEBRA" not in said.upper(), said
    assert not any(isinstance(a, Lookup) for a in acts), "no document lookup on another customer"


def test_g1_an_unlinked_contact_typing_the_exact_name_still_gets_no_customer_held(db):
    _other_customers(db)
    c = _contact(db)
    resolver, _ = _resolver(db, c)
    st, acts = _say(resolver, "KOPI ZEBRA SDN BHD")
    assert not st.held.refs.get("customer"), st.held.refs
    assert not any(isinstance(a, Lookup) for a in acts)


# ------------------------------------------------------------------ 2 a pick cannot widen the scope

def test_g2_the_scope_pv2_hands_on_for_an_unlinked_contact_is_enforced_and_empty(db):
    _other_customers(db)
    c = _contact(db)
    _, scope = _resolver(db, c)
    cs = scope["customer_scope"] or {}
    assert cs.get("enforced") is True and list(cs.get("ids") or []) == [] and not cs.get("linked"), cs


def test_g2_guard_a_customer_entity_carrying_another_customers_key_resolves_to_nothing(db):  # guard: passes today
    other = S.customer(db, "300-Z003", "LAUT BIRU TRADING")
    c = _contact(db)
    resolver, _ = _resolver(db, c)
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", key=str(other.id))]))
    from app.services.chatbot.pv2.state import new_state

    st, acts = apply_turn(new_state(NOW), parsed, resolver=resolver, now=NOW, can_offer_handover=False)
    assert not st.held.refs.get("customer"), st.held.refs
    assert not any(isinstance(a, Lookup) for a in acts)
    assert "LAUT BIRU" not in _everything_said(st, acts).upper()


# ------------------------------------------------------------------ 3 account names in a confirm

def _accounts(db):
    return [S.customer(db, f"400-A00{n}", f"ACME HARDWARE SDN BHD [A/C {lvl}]") for n, lvl in ((1, "I"), (2, "II"), (3, "III"))]


def test_g3_an_unlinked_contacts_family_confirm_prints_no_ledger_account_name(db):
    _accounts(db)
    c = _contact(db)
    resolver, _ = _resolver(db, c)
    st, acts = _say(resolver, "acme hard")
    assert "ACME HARDWARE" not in _everything_said(st, acts).upper()


def test_g3_a_linked_dealers_family_confirm_prints_only_their_own_linked_accounts():  # guard: passes today
    with S.resolve_session() as db:
        a1, a2, a3 = _accounts(db)
        c = _contact(db)
        _link(db, c, a1)
        _link(db, c, a2)
        resolver, _ = _resolver(db, c)
        st, acts = _say(resolver, "acme hard")
        said = _everything_said(st, acts)
        # R6 C lead ruling: a family confirm prints the group name only (no account list); the account outside the
        # dealer's links is never printed, nor carried in what a pick would apply.
        assert "A/C III" not in said, said
        assert str(a3.id) not in said, said


# ------------------------------------------------------------------ 4 guards

def test_g4_a_salesperson_with_no_links_keeps_staff_scope_and_can_search_customers():  # guard: passes today
    with S.resolve_session() as db:
        _other_customers(db)
        c = _contact(db)
        _salesperson(db, c)
        assert contact_asker.classify(db, c.id) == "salesperson"
        resolver, scope = _resolver(db, c)
        assert not (scope["customer_scope"] or {}).get("enforced")
        st, acts = _say(resolver, "zebra")
        assert "KOPI ZEBRA" in _everything_said(st, acts).upper()


def test_g4_a_salesperson_asking_by_the_exact_name_holds_that_customer():  # guard: passes today
    with S.resolve_session() as db:
        _other_customers(db)
        c = _contact(db)
        _salesperson(db, c)
        resolver, _ = _resolver(db, c)
        st, _acts = _say(resolver, "KOPI ZEBRA SDN BHD")
        assert [r.label for r in st.held.refs["customer"]] == ["KOPI ZEBRA SDN BHD"]


def test_g4_office_staff_search_is_unchanged():  # guard: passes today
    with S.resolve_session() as db:
        _other_customers(db)
        c = _contact(db)
        resolver, scope = _resolver(db, c, tier="office")
        assert not (scope["customer_scope"] or {}).get("enforced")
        st, acts = _say(resolver, "zebra")
        assert "KOPI ZEBRA" in _everything_said(st, acts).upper()


def test_g4_a_linked_dealer_still_finds_their_own_customer_and_not_others():  # guard: passes today
    with S.resolve_session() as db:
        _other_customers(db)
        mine = S.customer(db, "300-M001", "TASIK EMAS TRADING SDN BHD")
        c = _contact(db)
        _link(db, c, mine)
        resolver, _ = _resolver(db, c)
        st, _acts = _say(resolver, "TASIK EMAS TRADING SDN BHD")
        assert [r.label for r in st.held.refs["customer"]] == ["TASIK EMAS TRADING SDN BHD"]
        st, acts = _say(resolver, "zebra")
        said = _everything_said(st, acts).upper()
        assert "KOPI ZEBRA" not in said and "BATU ZEBRA" not in said, said
