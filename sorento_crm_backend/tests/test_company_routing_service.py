"""S0 - resolve_routing_company against real Postgres (AC-A1..A5, AC-J1).

Every row is seeded by the test: CI's database is empty, so borrowing an existing
contact or company would pass here and fail there.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.access import RespondContact
from app.models.company import Company, RespondContactCompany
from app.models.respond_workspace import RespondWorkspace
from app.services.company_routing_service import (
    DEFAULT_COMPANY_ID,
    resolve_routing_company,
)
from tests._pg_fixture import blank_session, unique_code


SPACE_ID = "ZZT-364817"


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _mocha(session) -> Company:
    company = Company(
        id=str(uuid.uuid4()), name="ZZT Mocha", code=unique_code("MCH"), is_active=True
    )
    session.add(company)
    session.flush()
    return company


def _workspace(session) -> RespondWorkspace:
    ws = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=SPACE_ID,
        name="ZZT workspace",
        api_key_ciphertext="ZZT-not-a-real-key",
    )
    session.add(ws)
    session.flush()
    return ws


def _contact(session, *, phone: str, respond_io_id: str, workspace=None) -> RespondContact:
    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=phone,
        name="ZZT Contact",
        respond_io_id=respond_io_id,
        workspace_id=workspace.id if workspace is not None else None,
    )
    session.add(contact)
    session.flush()
    return contact


def _tag(session, contact: RespondContact, company: Company) -> None:
    session.add(
        RespondContactCompany(
            id=str(uuid.uuid4()),
            respond_contact_id=str(contact.id),
            company_id=str(company.id),
        )
    )
    session.flush()


# --------------------------------------------------------------------- contact


def test_contact_tagged_one_company_resolves_to_it(db):
    """AC-A1 step (b)."""
    mocha = _mocha(db)
    ws = _workspace(db)
    contact = _contact(db, phone="+60190000001", respond_io_id="ZZT-rio-1", workspace=ws)
    _tag(db, contact, mocha)

    got = resolve_routing_company(db, contact_id="ZZT-rio-1", space_id=SPACE_ID)

    assert got.company_id == str(mocha.id)
    assert got.company_code == mocha.code
    assert got.source == "contact"
    assert got.ambiguous is False


def test_phone_only_resolves_the_same_company_as_id_only(db):
    """AC-A2 - the guarantee that makes n8n's phone-only body work unchanged."""
    mocha = _mocha(db)
    ws = _workspace(db)
    contact = _contact(db, phone="+60190000002", respond_io_id="ZZT-rio-2", workspace=ws)
    _tag(db, contact, mocha)

    by_id = resolve_routing_company(db, contact_id="ZZT-rio-2", space_id=SPACE_ID)
    by_phone = resolve_routing_company(db, phone="+60190000002")

    assert by_phone.company_id == by_id.company_id == str(mocha.id)
    assert by_phone.source == "contact"


def test_phone_matches_without_plus_prefix(db):
    """The lookup reuses the integration candidate list, so 60... finds +60...."""
    mocha = _mocha(db)
    contact = _contact(db, phone="+60190000003", respond_io_id="ZZT-rio-3")
    _tag(db, contact, mocha)

    got = resolve_routing_company(db, phone="60190000003")

    assert got.company_id == str(mocha.id)
    assert got.source == "contact"


def test_internal_contact_id_also_resolves(db):
    """Internal callers pass respond_contacts.id, not the Respond.io id."""
    mocha = _mocha(db)
    contact = _contact(db, phone="+60190000004", respond_io_id="ZZT-rio-4")
    _tag(db, contact, mocha)

    got = resolve_routing_company(db, contact_id=str(contact.id))

    assert got.company_id == str(mocha.id)


# ------------------------------------------------------------------- fallbacks


def test_multi_company_contact_falls_to_default_and_flags(db):
    """AC-A3 - never an arbitrary pick."""
    mocha = _mocha(db)
    contact = _contact(db, phone="+60190000005", respond_io_id="ZZT-rio-5")
    _tag(db, contact, mocha)
    sorento = db.query(Company).filter(Company.id == DEFAULT_COMPANY_ID).one()
    _tag(db, contact, sorento)

    got = resolve_routing_company(db, phone="+60190000005")

    assert got.company_id == DEFAULT_COMPANY_ID
    assert got.source == "default"
    assert got.ambiguous is True


def test_untagged_contact_falls_to_default(db):
    """AC-A4 - the safety net that keeps a shared channel routable."""
    _contact(db, phone="+60190000006", respond_io_id="ZZT-rio-6")

    got = resolve_routing_company(db, phone="+60190000006")

    assert got.company_id == DEFAULT_COMPANY_ID
    assert got.company_code == "SRT"
    assert got.source == "default"
    assert got.ambiguous is False


def test_unknown_contact_falls_to_default(db):
    got = resolve_routing_company(db, phone="+60199999999", contact_id="ZZT-nope")

    assert got.company_id == DEFAULT_COMPANY_ID
    assert got.source == "default"


def test_no_identity_at_all_falls_to_default(db):
    got = resolve_routing_company(db)

    assert got.company_id == DEFAULT_COMPANY_ID
    assert got.source == "default"


# ----------------------------------------------------------------- body override


def test_body_company_code_wins(db):
    """AC-A1 step (a) / D3 - the override beats the contact's own tag."""
    mocha = _mocha(db)
    other = Company(
        id=str(uuid.uuid4()), name="ZZT Other", code=unique_code("OTH"), is_active=True
    )
    db.add(other)
    db.flush()
    contact = _contact(db, phone="+60190000007", respond_io_id="ZZT-rio-7")
    _tag(db, contact, mocha)

    got = resolve_routing_company(
        db, company_code=other.code, phone="+60190000007"
    )

    assert got.company_id == str(other.id)
    assert got.company_code == other.code
    assert got.source == "body"


def test_body_company_code_is_case_insensitive(db):
    mocha = _mocha(db)

    got = resolve_routing_company(db, company_code=mocha.code.lower())

    assert got.company_id == str(mocha.id)
    assert got.source == "body"


def test_unknown_company_code_falls_through_to_the_contact(db):
    """An unknown code is not a hit, so resolution continues down AC-A1's order.

    Deliberately NOT "unknown code means default": the contact is a better signal
    than a typo, and ignoring it would misroute a correctly-tagged Mocha contact.
    """
    mocha = _mocha(db)
    contact = _contact(db, phone="+60190000008", respond_io_id="ZZT-rio-8")
    _tag(db, contact, mocha)

    got = resolve_routing_company(
        db, company_code="ZZT-NO-SUCH-COMPANY", phone="+60190000008"
    )

    assert got.company_id == str(mocha.id)
    assert got.source == "contact"


def test_unknown_company_code_with_no_contact_falls_to_default(db):
    """AC-A4 - with no other signal, a bad code lands on the default, never 4xx."""
    got = resolve_routing_company(db, company_code="ZZT-NO-SUCH-COMPANY")

    assert got.company_id == DEFAULT_COMPANY_ID
    assert got.source == "default"


# ------------------------------------------------------------------ resilience


def test_resolution_never_raises(db):
    """AC-J1 - a broken session degrades to the default instead of 500ing."""

    class Boom:
        def query(self, *a, **k):
            raise RuntimeError("database on fire")

    got = resolve_routing_company(Boom(), phone="+60190000009")

    assert got.company_id == DEFAULT_COMPANY_ID
    assert got.source == "default"
    assert got.company_code is None


# -------------------------------------------------------------- space scoping


def test_space_id_mismatch_still_resolves_via_phone(db):
    """Phone is the authoritative fallback, so a wrong space_id does not strand n8n."""
    mocha = _mocha(db)
    ws = _workspace(db)
    contact = _contact(db, phone="+60190000010", respond_io_id="ZZT-rio-10", workspace=ws)
    _tag(db, contact, mocha)

    got = resolve_routing_company(
        db, contact_id="ZZT-rio-10", space_id="ZZT-wrong-space", phone="+60190000010"
    )

    assert got.company_id == str(mocha.id)
    assert got.source == "contact"
