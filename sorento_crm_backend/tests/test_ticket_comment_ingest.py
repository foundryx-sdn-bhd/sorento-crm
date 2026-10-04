"""Ingest of comments made in Respond's own inbox (UAC AC-L3, slice S4.3).

POST /api/v1/external/chat-history/comments is the CRM half of the n8n
`comment.created` forward lane. Respond comments are CONTACT-scoped, not
ticket-scoped, so an ingested comment carries the contact only and renders in
every open ticket drawer for that contact.

Covered: contact resolution (by Respond contact id and by phone), dedupe on the
Respond comment id, unknown contact, validation, auth denial.

Run:
    venv/bin/pytest tests/test_ticket_comment_ingest.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.access import RespondContact
from app.models.ticket_comment import ConversationTicketComment
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

RESPOND_IO_ID = "900000008"
PHONE = "+60160000509"
URL = "/api/v1/external/chat-history/comments"


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


@pytest.fixture
def client(db):
    def _user():
        return {"id": "system"}

    def _db():
        yield db

    app.dependency_overrides[get_external_api_user] = _user
    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_db] = _db
    with external_permissions_granted():
        yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client(db):
    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_contact(db) -> str:
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            respond_io_id=RESPOND_IO_ID,
            phone_number=PHONE,
            name="Aisyah Rahman",
            session_vars={},
        )
    )
    db.commit()
    return contact_id


def _payload(**over) -> dict:
    body = {
        "contact_id": RESPOND_IO_ID,
        "comment_id": "cmt-1001",
        "text": "Called him, he is happy to wait.",
        "author_respond_user_id": "900123",
        "author_name": "Inbox Agent",
        "created_at": 1786758000000,
    }
    body.update(over)
    return body


def test_a_respond_comment_is_stored_against_the_contact(client, db):
    contact_id = _seed_contact(db)

    resp = client.post(URL, json=_payload())

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "created"
    row = db.query(ConversationTicketComment).one()
    assert row.respond_contact_id == contact_id
    assert row.tracking_id is None, "Respond comments are contact-scoped"
    assert row.body == "Called him, he is happy to wait."
    assert row.source == "respond"
    assert row.author_respond_user_id == "900123"
    assert row.author_name == "Inbox Agent"
    assert row.respond_comment_id == "cmt-1001"


def test_the_same_comment_id_twice_stores_one_row(client, db):
    _seed_contact(db)

    first = client.post(URL, json=_payload())
    second = client.post(URL, json=_payload(text="edited on the second lane"))

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "duplicate"
    assert first.json()["id"] == second.json()["id"]
    assert db.query(ConversationTicketComment).count() == 1
    assert db.query(ConversationTicketComment).one().body == "Called him, he is happy to wait."


def test_the_contact_can_be_resolved_by_phone_number(client, db):
    contact_id = _seed_contact(db)

    resp = client.post(URL, json=_payload(contact_id=None, phone_number=PHONE))

    assert resp.status_code == 201, resp.text
    assert db.query(ConversationTicketComment).one().respond_contact_id == contact_id


def test_an_unknown_contact_is_a_404(client, db):
    _seed_contact(db)

    resp = client.post(URL, json=_payload(contact_id="does-not-exist"))

    assert resp.status_code == 404, resp.text
    assert db.query(ConversationTicketComment).count() == 0


def test_no_contact_reference_at_all_is_a_validation_error(client, db):
    _seed_contact(db)

    resp = client.post(URL, json=_payload(contact_id=None))

    assert resp.status_code in (400, 422), resp.text
    assert db.query(ConversationTicketComment).count() == 0


def test_an_empty_comment_is_rejected(client, db):
    _seed_contact(db)

    assert client.post(URL, json=_payload(text="   ")).status_code == 422
    assert db.query(ConversationTicketComment).count() == 0


def test_no_api_key_is_rejected(anon_client, db):
    _seed_contact(db)

    resp = anon_client.post(URL, json=_payload())

    assert resp.status_code in (401, 403), resp.text
    assert db.query(ConversationTicketComment).count() == 0


# --------------------------------------------------------------------------- #
# Idempotency is the whole contract, so the key is mandatory                   #
# --------------------------------------------------------------------------- #


def test_a_payload_with_no_comment_id_is_refused(client, db):
    """`comment_id` used to be optional, and a payload without one inserted
    unconditionally - so an n8n retry of the same Respond comment produced a
    duplicate note in every open drawer for that contact, forever, with nothing
    able to tell them apart. A 201 that is not idempotent is a worse promise
    than a 400."""
    _seed_contact(db)

    resp = client.post(URL, json=_payload(comment_id=None))

    assert resp.status_code == 400, resp.text
    assert "comment_id" in resp.text
    assert db.query(ConversationTicketComment).count() == 0


def test_a_blank_comment_id_is_refused_too(client, db):
    _seed_contact(db)

    assert client.post(URL, json=_payload(comment_id="   ")).status_code == 400
    assert db.query(ConversationTicketComment).count() == 0


def test_a_duplicate_that_beats_the_read_check_is_still_a_duplicate(client, db):
    """The read-then-insert dedupe races: two lanes forwarding the same
    `comment.created` can both find nothing and both insert, and the second one
    then dies on the unique index as a 500 the forwarder retries forever. The
    insert is wrapped so the loser re-reads the winner's row and answers
    `duplicate`, which is what it truthfully is."""
    from app.services.ticket_comment_service import TicketCommentService

    _seed_contact(db)
    assert client.post(URL, json=_payload()).status_code == 201

    # Simulate losing the race: the existence probe sees nothing (as it would
    # have a microsecond before the winner committed), so the code path runs
    # straight into the unique index.
    original = TicketCommentService._existing_ingested_comment
    calls = {"n": 0}

    def _blind(self, respond_comment_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original(self, respond_comment_id)

    TicketCommentService._existing_ingested_comment = _blind
    try:
        second = client.post(URL, json=_payload(text="the losing lane"))
    finally:
        TicketCommentService._existing_ingested_comment = original

    assert second.status_code == 201, second.text
    assert second.json()["status"] == "duplicate"
    assert db.query(ConversationTicketComment).count() == 1
    assert db.query(ConversationTicketComment).one().body == (
        "Called him, he is happy to wait."
    ), "the winner's row stands; the loser does not overwrite it"
    assert calls["n"] == 2, "the loser re-selects after the IntegrityError"


# --------------------------------------------------------------------------- #
# The inbound integration_log the sibling message ingest already writes        #
# --------------------------------------------------------------------------- #


def _ingest_logs(db):
    from app.models.integration import IntegrationLog

    return (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "conversation_ticket_comments")
        .order_by(IntegrationLog.created_at.asc())
        .all()
    )


def test_an_ingested_comment_leaves_an_inbound_log_row(client, db):
    """The message ingest next door logs every call; the comment ingest logged
    none, so a comment that never appeared was undiagnosable from the CRM side."""
    import json

    _seed_contact(db)

    assert client.post(URL, json=_payload()).status_code == 201

    rows = _ingest_logs(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.direction == "inbound"
    assert row.integration_channel == "n8n"
    assert row.external_reference == RESPOND_IO_ID
    assert row.status == "success"
    assert row.status_code == 201
    assert "Called him" in (row.request_payload or "")
    headers = json.loads(row.request_headers or "{}")
    assert all(v == "***" for k, v in headers.items() if k.lower() == "x-api-key"), (
        "an API key must never be readable from a log row"
    )


def test_a_refused_comment_is_logged_too(client, db):
    """The failure is the case worth logging: "n8n says it forwarded it" versus
    "the CRM had never heard of that contact" is the whole diagnosis."""
    _seed_contact(db)

    assert client.post(URL, json=_payload(contact_id="does-not-exist")).status_code == 404

    rows = _ingest_logs(db)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].status_code == 404
    assert rows[0].error_message


def test_a_duplicate_is_logged_as_a_success(client, db):
    _seed_contact(db)
    client.post(URL, json=_payload())
    client.post(URL, json=_payload())

    rows = _ingest_logs(db)
    assert [r.status for r in rows] == ["success", "success"]
