"""Golden set for the contact -> customer link (AC-D1, AC-D2).

Written BEFORE the implementation.

**Why this is a table and not a column.** The obvious move is
``respond_contacts.customer_id``. It is wrong twice over. ``customers`` is
company-scoped (Sorento and Mocha each have their own customer rows) while
``respond_contacts`` is not, so one column cannot say "this person is customer X
at Sorento and customer Y at Mocha" - and putting a company-scoped value on an
unscoped table smuggles one company's data into another's reads. A link row
carries its own ``company_id`` and the scope filter does the rest.

**Why resolution refuses to guess.** AC-D2 says a contact resolves to a customer
when one exists and does NOT invent one when it does not. The interesting case is
neither: a contact linked to TWO customers in the same company with no primary
marked. Picking the oldest, or the alphabetically-first, would be inventing an
answer that happens to look like data. Resolution returns nothing and the caller
asks a human.

Phone matching only ever PROPOSES. A proposal that writes itself is not a
proposal, and a mis-matched customer is a quote sent to the wrong company.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.models.base import company_scope
from tests._pg_fixture import pg_session, unique_code

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1",
    reason="SKIP_LIVE_DB_TESTS=1",
)

SORENTO = "00000000-0000-0000-0000-000000000001"


def _contact(db, phone: str | None = None):
    from app.models.access import RespondContact

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=phone or f"6012{unique_code('')[-7:]}",
        name=f"ZZT contact {unique_code('c')}",
    )
    db.add(contact)
    db.flush()
    return contact


def _customer(db, **overrides):
    from app.models.order import Customer

    code = unique_code("ZZTC")
    fields = dict(
        customer_code=code,
        customer_name=f"ZZT customer {code}",
        company_id=SORENTO,
    )
    fields.update(overrides)
    customer = Customer(**fields)
    db.add(customer)
    db.flush()
    return customer


# --- AC-D1: the link exists, and it is a real many-to-one per company ---------


def test_a_contact_links_to_a_customer():
    from app.services.contact_customer_service import link_customer, resolve_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        customer = _customer(db)

        link_customer(db, contact_id=contact.id, customer_id=customer.id)
        db.flush()

        assert resolve_customer(db, contact.id) == customer.id


def test_linking_twice_is_idempotent_not_a_duplicate():
    from app.services.contact_customer_service import link_customer, list_links

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        customer = _customer(db)

        link_customer(db, contact_id=contact.id, customer_id=customer.id)
        link_customer(db, contact_id=contact.id, customer_id=customer.id)
        db.flush()

        assert len(list_links(db, contact_id=contact.id)) == 1


def test_a_link_carries_the_company_of_its_customer():
    from app.services.contact_customer_service import link_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        customer = _customer(db)

        link = link_customer(db, contact_id=contact.id, customer_id=customer.id)
        db.flush()

        assert link.company_id == SORENTO


# --- AC-D2: resolution, and the refusal to invent ----------------------------


def test_an_unlinked_contact_resolves_to_nothing():
    from app.services.contact_customer_service import resolve_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        # Not "create a customer for them" - a contact who has never bought
        # anything is not a customer, and pretending otherwise fills the
        # customer list with phone numbers.
        assert resolve_customer(db, contact.id) is None


def test_two_links_with_no_primary_resolve_to_nothing():
    from app.services.contact_customer_service import link_customer, resolve_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        first = _customer(db)
        second = _customer(db)

        link_customer(db, contact_id=contact.id, customer_id=first.id)
        link_customer(db, contact_id=contact.id, customer_id=second.id)
        db.flush()

        # Ambiguous. Picking the oldest would be a guess wearing a data costume.
        assert resolve_customer(db, contact.id) is None


def test_a_primary_link_settles_an_ambiguous_contact():
    from app.services.contact_customer_service import link_customer, resolve_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        first = _customer(db)
        second = _customer(db)

        link_customer(db, contact_id=contact.id, customer_id=first.id)
        link_customer(db, contact_id=contact.id, customer_id=second.id, is_primary=True)
        db.flush()

        assert resolve_customer(db, contact.id) == second.id


def test_marking_a_new_primary_demotes_the_old_one():
    from app.services.contact_customer_service import link_customer, list_links, resolve_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        first = _customer(db)
        second = _customer(db)

        link_customer(db, contact_id=contact.id, customer_id=first.id, is_primary=True)
        link_customer(db, contact_id=contact.id, customer_id=second.id, is_primary=True)
        db.flush()

        primaries = [link for link in list_links(db, contact_id=contact.id) if link.is_primary]
        assert len(primaries) == 1
        assert resolve_customer(db, contact.id) == second.id


def test_unlinking_removes_the_link_and_not_the_customer():
    from app.models.order import Customer
    from app.services.contact_customer_service import link_customer, resolve_customer, unlink_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db)
        customer = _customer(db)
        link_customer(db, contact_id=contact.id, customer_id=customer.id)
        db.flush()

        unlink_customer(db, contact_id=contact.id, customer_id=customer.id)
        db.flush()

        assert resolve_customer(db, contact.id) is None
        assert db.query(Customer).filter(Customer.id == customer.id).first() is not None


# --- Proposals: matched, never applied ---------------------------------------


def test_a_matching_phone_number_is_proposed_not_linked():
    from app.services.contact_customer_service import propose_customers, resolve_customer

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db, phone="60123456789")
        customer = _customer(db, phone_number="0123456789")

        proposed = propose_customers(db, contact.id)

        # Same subscriber number, different national prefix - a human confirms.
        assert customer.id in [candidate.id for candidate in proposed]
        assert resolve_customer(db, contact.id) is None


def test_a_contact_with_no_phone_match_gets_no_proposal():
    from app.services.contact_customer_service import propose_customers

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        contact = _contact(db, phone="60111111111")
        _customer(db, phone_number="60999999999")

        assert propose_customers(db, contact.id) == []


def test_a_short_number_never_matches_by_suffix():
    from app.services.contact_customer_service import propose_customers

    with pg_session() as db, company_scope(db, frozenset({SORENTO})):
        # Suffix matching on a handful of digits would pair unrelated people.
        contact = _contact(db, phone="1234")
        _customer(db, phone_number="60120000540")

        assert propose_customers(db, contact.id) == []
