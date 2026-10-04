"""chat_histories ingest is idempotent on the Respond messageId.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-J5 (a drawer send fires BOTH the direct webhook lane and Respond's own
            outgoing-message trigger; both lanes mirror the message through
            POST /api/v1/external/chat-history/messages, so the ingest must
            upsert on the message id instead of blind-inserting - the fix sits
            at OUR boundary, with no assumption about n8n's lane ordering)
PLAN: documentation/plans/sla/PLAN-conversation-intervention-tickets.md (S4.1)

Run:
    venv/bin/pytest tests/test_chat_history_ingest_idempotency.py -q
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.chat_history import ChatHistory
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

RESPOND_IO_ID = "900000008"
# Respond message ids ARE epoch microseconds; both lanes carry the same one.
MESSAGE_ID = "1780751891000000"
INGEST_URL = "/api/v1/external/chat-history/messages"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


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


def _payload(**overrides) -> dict:
    payload = {
        "channel": "whatsapp",
        "contact_id": RESPOND_IO_ID,
        "phone_number": "+60160000509",
        "message": "Sure, I will check that for you.",
        "sent_at": 1780751906900,
        "type": "outgoing",
        "message_id": MESSAGE_ID,
    }
    payload.update(overrides)
    return payload


def _rows(db, message_id=MESSAGE_ID):
    return (
        db.query(ChatHistory)
        .filter(
            ChatHistory.contact_id == RESPOND_IO_ID,
            ChatHistory.message_id == message_id,
        )
        .all()
    )


def test_the_same_message_id_twice_leaves_exactly_one_row(client, db):
    """The two mirror lanes race on the same outgoing message (AC-J5)."""
    first = client.post(INGEST_URL, json=_payload())
    second = client.post(INGEST_URL, json=_payload())

    assert first.status_code == 201
    assert second.status_code == 201, "a duplicate is a no-op, never an error"
    assert second.json()["id"] == first.json()["id"], "the second lane resolves the same row"
    assert second.json()["status"] == "duplicate"
    assert first.json()["status"] == "created"
    assert len(_rows(db)) == 1


def test_the_second_lane_fills_what_the_first_one_lacked(client, db):
    """Update-in-place, never overwrite: whichever lane carries the extra
    context (turn id, quoted message) contributes it without clobbering what
    already landed."""
    client.post(INGEST_URL, json=_payload(turn_id=None))
    client.post(
        INGEST_URL,
        json=_payload(
            message="a re-render of the same message",
            turn_id="exec-99",
            reply_to_message_id="1780751880000000",
        ),
    )

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.turn_id == "exec-99", "a NULL field is filled by the later lane"
    assert row.message == "Sure, I will check that for you.", (
        "the first lane's substance stands - a mirror is not an edit"
    )
    assert row.reply_to_message_id == "1780751880000000"


def test_different_message_ids_are_two_rows(client, db):
    client.post(INGEST_URL, json=_payload())
    client.post(INGEST_URL, json=_payload(message_id="1780751892000000", message="and one more"))

    assert len(_rows(db)) == 1
    assert len(_rows(db, "1780751892000000")) == 1


def test_a_missing_message_id_keeps_the_legacy_insert_behaviour(client, db):
    """Legacy callers omit the id entirely - there is nothing to dedupe on, so
    every post is its own row exactly as before."""
    first = client.post(INGEST_URL, json=_payload(message_id=None))
    second = client.post(INGEST_URL, json=_payload(message_id=None))

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["status"] == "created" and second.json()["status"] == "created"
    rows = (
        db.query(ChatHistory)
        .filter(
            ChatHistory.contact_id == RESPOND_IO_ID,
            ChatHistory.message_id.is_(None),
        )
        .all()
    )
    assert len(rows) == 2


def test_the_same_message_id_for_another_contact_is_its_own_row(client, db):
    client.post(INGEST_URL, json=_payload())
    client.post(INGEST_URL, json=_payload(contact_id="999888777"))

    assert len(_rows(db)) == 1
    other = (
        db.query(ChatHistory)
        .filter(
            ChatHistory.contact_id == "999888777",
            ChatHistory.message_id == MESSAGE_ID,
        )
        .all()
    )
    assert len(other) == 1
