"""Tests for the sponsor-subject lookup normalization and the approver-name fix.

Covers (Workstream 2 + 4 of PLAN-sponsorship-form-alignment):
- PurchaseRequestService._normalize_sponsor_subject resolves keyword synonyms
  to canonical option values and parks unmatched free text under 'others'.
- The binding write-validator accepts canonical values and rejects raw free text
  (so we know the resolver path is what keeps n8n intake from 422-ing).
- create_request normalizes a free-text sponsor_subject before persist.
- _resolve_approver_display_name returns the stored display name for an
  in-system approval (name in approved_by, no email) and "unknown" when empty,
  without leaking a UUID.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.schemas.lookup import LookupSetCreate, LookupOptionCreate, LookupKeywordIn
from app.services.lookup_set_service import LookupSetService
from app.services.lookup_option_service import LookupOptionService
from app.services.lookup_validator import validate_lookup_value, _cache_clear
from app.services.error_handler import AppException
from tests._pg_fixture import blank_session


SET_KEY = "procurement_sponsor_subject"


@pytest.fixture
def db_session():
    with blank_session() as session:
        _cache_clear()
        try:
            yield session
        finally:
            _cache_clear()


@pytest.fixture
def seeded_set(db_session):
    """Seed the procurement_sponsor_subject set + options + keyword synonyms + binding."""
    s = LookupSetService(db_session).create(
        LookupSetCreate(set_key=SET_KEY, name="Procurement - Sponsor Subject")
    )
    LookupOptionService(db_session).create(
        s.id,
        LookupOptionCreate(
            value="showroom",
            label="Showroom",
            keywords=[LookupKeywordIn(keyword="show room")],
        ),
    )
    LookupOptionService(db_session).create(
        s.id,
        LookupOptionCreate(
            value="mockup",
            label="Mockup",
            keywords=[
                LookupKeywordIn(keyword="mock up"),
                LookupKeywordIn(keyword="mock-up"),
                LookupKeywordIn(keyword="sample"),
                LookupKeywordIn(keyword="prototype"),
            ],
        ),
    )
    LookupOptionService(db_session).create(
        s.id, LookupOptionCreate(value="others", label="Others")
    )
    # Bind purchase_requests.sponsor_subject directly via the model (the service's
    # create() consults the eligibility registry + queries the real target table,
    # neither of which exist in this lightweight sqlite fixture).
    from app.models.lookup import LookupBinding

    db_session.add(
        LookupBinding(
            id=str(uuid.uuid4()),
            tenant_id=None,
            set_id=s.id,
            table_name="purchase_requests",
            column_name="sponsor_subject",
        )
    )
    db_session.commit()
    _cache_clear()
    return s


def _service(db_session):
    """Build a PurchaseRequestService without triggering its attachment dependency."""
    from app.services.procurement_service import PurchaseRequestService

    svc = PurchaseRequestService.__new__(PurchaseRequestService)
    svc.db = db_session
    return svc


# --- _normalize_sponsor_subject -------------------------------------------------


def test_normalize_exact_value(db_session, seeded_set):
    svc = _service(db_session)
    assert svc._normalize_sponsor_subject("sponsorship_form", "showroom") == (
        "showroom",
        None,
    )


def test_normalize_keyword_synonym_to_mockup(db_session, seeded_set):
    svc = _service(db_session)
    assert svc._normalize_sponsor_subject("sponsorship_form", "Mock Up") == (
        "mockup",
        None,
    )
    assert svc._normalize_sponsor_subject("sponsorship_form", "prototype") == (
        "mockup",
        None,
    )


def test_normalize_show_room_synonym(db_session, seeded_set):
    svc = _service(db_session)
    assert svc._normalize_sponsor_subject("sponsorship_form", "show room") == (
        "showroom",
        None,
    )


def test_normalize_unmatched_parks_in_other(db_session, seeded_set):
    svc = _service(db_session)
    assert svc._normalize_sponsor_subject(
        "sponsorship_form", "special VIP launch booth"
    ) == ("others", "special VIP launch booth")


def test_normalize_blank_is_none(db_session, seeded_set):
    svc = _service(db_session)
    assert svc._normalize_sponsor_subject("sponsorship_form", "   ") == (None, None)


def test_normalize_non_sponsorship_passthrough(db_session, seeded_set):
    svc = _service(db_session)
    # purchase_request never binds sponsor_subject; value passes untouched.
    assert svc._normalize_sponsor_subject("purchase_request", "anything") == (
        "anything",
        None,
    )


def test_normalize_no_set_falls_back_to_others(db_session):
    # No lookup set seeded → resolver raises → park raw under 'others'.
    svc = _service(db_session)
    assert svc._normalize_sponsor_subject("sponsorship_form", "freeform") == (
        "others",
        "freeform",
    )


# --- binding write-validator ----------------------------------------------------


def test_validator_accepts_canonical_value(db_session, seeded_set):
    # Canonical option value passes the strict binding validator.
    validate_lookup_value(
        db_session,
        table="purchase_requests",
        column="sponsor_subject",
        value="mockup",
    )


def test_validator_rejects_raw_free_text(db_session, seeded_set):
    with pytest.raises(AppException) as e:
        validate_lookup_value(
            db_session,
            table="purchase_requests",
            column="sponsor_subject",
            value="Mock Up",  # raw, not a canonical value
        )
    assert e.value.status_code == 422


# --- create_request normalizes free-text sponsor_subject ------------------------


def test_create_request_normalizes_free_text(db_session, seeded_set, monkeypatch):
    from app.schemas.procurement import PurchaseRequestHeaderCreate

    # Avoid touching the entity-attachment dependency / numbering / view token.
    svc = _service(db_session)
    monkeypatch.setattr(svc, "_build_respond_inbox_url", lambda *a, **k: None)
    monkeypatch.setattr(svc, "get_or_create_view_token", lambda *a, **k: None)

    data = PurchaseRequestHeaderCreate(
        request_type="sponsorship_form",
        request_number="SF-TEST-1",
        sponsor_subject="mock up",  # free text → should normalize to 'mockup'
        products=[],
    )
    header = svc.create_request(data)
    assert header.sponsor_subject == "mockup"
    assert header.sponsor_subject_other is None


def test_create_request_unmatched_parks_other(db_session, seeded_set, monkeypatch):
    from app.schemas.procurement import PurchaseRequestHeaderCreate

    svc = _service(db_session)
    monkeypatch.setattr(svc, "_build_respond_inbox_url", lambda *a, **k: None)
    monkeypatch.setattr(svc, "get_or_create_view_token", lambda *a, **k: None)

    data = PurchaseRequestHeaderCreate(
        request_type="sponsorship_form",
        request_number="SF-TEST-2",
        sponsor_subject="bespoke exhibition stand",
        products=[],
    )
    header = svc.create_request(data)
    assert header.sponsor_subject == "others"
    assert header.sponsor_subject_other == "bespoke exhibition stand"


# --- _resolve_approver_display_name (Workstream 4) ------------------------------


def test_approver_name_in_system_returns_stored_name(db_session):
    svc = _service(db_session)
    # In-system approve stores the display name straight into approved_by.
    header = SimpleNamespace(approved_by="CK Lee", approver_email=None)
    assert svc._resolve_approver_display_name(header) == "CK Lee"


def test_approver_resolves_user_id_to_name(db_session):
    from app.models.user import User

    user = User(id=str(uuid.uuid4()), email="a@b.com", name="Alice Approver")
    db_session.add(user)
    db_session.commit()
    svc = _service(db_session)
    header = SimpleNamespace(approved_by=user.id, approver_email=None)
    assert svc._resolve_approver_display_name(header) == "Alice Approver"


def test_approver_email_fallback(db_session):
    svc = _service(db_session)
    header = SimpleNamespace(approved_by=None, approver_email="ext@partner.com")
    assert svc._resolve_approver_display_name(header) == "ext@partner.com"


def test_approver_name_outranks_email(db_session):
    # When both a stored display name AND an approver_email are present (e.g. the
    # in-system approve route set approved_by=name while approver_email lingers),
    # the message must read the NAME so it matches the detail page's "Approved by".
    svc = _service(db_session)
    header = SimpleNamespace(
        approved_by="ABDUL BASER BIN RAMLI", approver_email="person6@example.com"
    )
    assert svc._resolve_approver_display_name(header) == "ABDUL BASER BIN RAMLI"


def test_approver_empty_returns_unknown(db_session):
    svc = _service(db_session)
    header = SimpleNamespace(approved_by=None, approver_email=None)
    assert svc._resolve_approver_display_name(header) == "unknown"


def test_approver_bare_uuid_not_leaked(db_session):
    svc = _service(db_session)
    # A stored UUID with no matching user + no email must NOT leak; → 'unknown'.
    header = SimpleNamespace(approved_by=str(uuid.uuid4()), approver_email=None)
    assert svc._resolve_approver_display_name(header) == "unknown"
