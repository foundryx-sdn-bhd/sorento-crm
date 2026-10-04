"""Form-banner person links - shared wa.me phone resolver (UAC group PR).

Covers PR-1..PR-6 in documentation/plans/forms/form-banner-person-links-acceptance-criteria.md:
- resolve a users.id -> RespondContact.phone_number bare digits (PR-1)
- no phone-match fallback: an unlinked user has no wa.me phone (PR-2, superseded by
  identity S3 fix round 2, #1280)
- no linked contact / no phone-matchable number -> None (PR-3)
- respond_user_id path (complaint rejecter) -> User -> phone (PR-4)
- users.id path (stock-inquiry rejecter) -> User -> phone (PR-5)
- never raises on missing / garbage id -> None (PR-6)

Run: pytest tests/test_banner_person_phone_resolver.py -v
"""
from __future__ import annotations

import uuid

import pytest

from app.models.access import RespondContact
from app.models.user import User
from app.services.banner_person_service import (
    wa_phone_for_user_id,
    wa_phone_for_respond_user_id,
    name_and_wa_phone_for_user_id,
)
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _contact(db, phone, io_id="io-1", name="Field Tech"):
    cid = str(uuid.uuid4())
    db.add(RespondContact(id=cid, phone_number=phone, name=name, respond_io_id=io_id, session_vars={}))
    db.commit()
    return cid


def _user(db, *, contact_number=None, respond_contact_id=None, respond_user_id=None, email=None, name="U"):
    uid = str(uuid.uuid4())
    db.add(User(
        id=uid,
        email=email or f"u{uid[:8]}@test.com",
        name=name,
        contact_number=contact_number,
        respond_contact_id=respond_contact_id,
        respond_user_id=respond_user_id,
    ))
    db.commit()
    return uid


# ---- PR-1 ---------------------------------------------------------------
def test_explicit_link_returns_bare_digits(db):
    cid = _contact(db, "60123456789")
    uid = _user(db, respond_contact_id=cid)
    assert wa_phone_for_user_id(db, uid) == "60123456789"


# ---- PR-2 ---------------------------------------------------------------
def test_phone_match_without_link_returns_none(db):
    """PR-2's phone-match fallback is superseded by identity S3 fix round 2 (#1280,
    reviewer B1): a banner links only a user's linked contact, so a contact the owner
    unlinked is never shown (or re-linked) because its phone matches."""
    _contact(db, "60123456789")
    uid = _user(db, contact_number="60123456789")
    assert wa_phone_for_user_id(db, uid) is None
    assert db.query(User).get(uid).respond_contact_id is None


# ---- PR-3 ---------------------------------------------------------------
def test_no_contact_no_match_returns_none(db):
    uid = _user(db, contact_number="60999999999")  # no matching RespondContact
    assert wa_phone_for_user_id(db, uid) is None
    uid2 = _user(db, contact_number=None)
    assert wa_phone_for_user_id(db, uid2) is None


# ---- PR-4 ---------------------------------------------------------------
def test_respond_user_id_path(db):
    cid = _contact(db, "60110000526")
    _user(db, respond_contact_id=cid, respond_user_id="respond-abc")
    assert wa_phone_for_respond_user_id(db, "respond-abc") == "60110000526"


def test_respond_user_id_no_user_returns_none(db):
    assert wa_phone_for_respond_user_id(db, "nobody") is None


# ---- PR-5 ---------------------------------------------------------------
def test_users_id_path_direct(db):
    cid = _contact(db, "60120000539")
    uid = _user(db, respond_contact_id=cid)
    name, phone = name_and_wa_phone_for_user_id(db, uid)
    assert phone == "60120000539"
    assert name  # resolved display name, never a raw id


# ---- PR-6 ---------------------------------------------------------------
def test_never_raises_on_garbage(db):
    assert wa_phone_for_user_id(db, None) is None
    assert wa_phone_for_user_id(db, "") is None
    assert wa_phone_for_user_id(db, "not-a-real-id") is None
    assert wa_phone_for_respond_user_id(db, None) is None
    assert name_and_wa_phone_for_user_id(db, None) == (None, None)
