"""QUOTED-REPLY slice 1: `chat_histories.chatbot_turn_id`, stamped at the OUTGOING ingest.

UAC: documentation/plans/chatbot/quoted-reply-acceptance-criteria.md AC-QR-1 .. AC-QR-4.
Plan: documentation/plans/chatbot/PLAN-quoted-reply-07oct.md (rule R1).

Red-first: none of the column, the payload key or the stamping exists yet. The column is
read with raw SQL so a missing column fails the test it belongs to, not collection.

Every contact id, message id and uuid below is synthetic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.chat_history import ChatHistory
from app.models.chatbot_turn import ChatbotTurn
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

CONTACT = "900000021"
OTHER_CONTACT = "900000022"
INGEST_URL = "/api/v1/external/chat-history/messages"
REPLY = "I read ELP3754 from that photo.\nHow many units of ELP3754?"


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


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _payload(**overrides) -> dict:
    payload = {
        "channel": "whatsapp",
        "contact_id": CONTACT,
        "phone_number": "+60160000521",
        "message": REPLY,
        "sent_at": _ms(datetime.now(tz=timezone.utc)),
        "type": "outgoing",
        "message_id": f"17807{uuid.uuid4().int % 10**11:011d}",
    }
    payload.update(overrides)
    return payload


def _turn(db, *, contact=CONTACT, message_id=None, reply=REPLY, is_test=False, finished_at=None) -> ChatbotTurn:
    finished_at = finished_at or datetime.now(tz=timezone.utc)
    row = ChatbotTurn(
        id=str(uuid.uuid4()),
        contact_respond_id=contact,
        message_id=message_id or f"ZZT-in-{uuid.uuid4().hex[:8]}",
        envelope={},
        is_test=is_test,
        status="done",
        response={"reply": {"text": reply}},
        trace=[],
        created_at=finished_at - timedelta(seconds=8),
        finished_at=finished_at,
    )
    db.add(row)
    db.commit()
    return row


def _stamp(db, message_id: str):
    return db.execute(
        text("SELECT chatbot_turn_id FROM chat_histories WHERE message_id = :m"), {"m": message_id}
    ).scalar_one()


def _post(client, **overrides) -> dict:
    body = _payload(**overrides)
    response = client.post(INGEST_URL, json=body)
    assert response.status_code == 201, response.text
    return body


def test_ac_qr_1_model_declares_chatbot_turn_id():
    assert hasattr(ChatHistory, "chatbot_turn_id"), "ChatHistory has no chatbot_turn_id column"
    assert ChatHistory.chatbot_turn_id.nullable is True


def test_ac_qr_1_column_exists_in_the_database(db):
    found = db.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'chat_histories' AND column_name = 'chatbot_turn_id'"
        )
    ).scalar()
    assert found == "YES"


def test_ac_qr_1_incoming_row_keeps_null(client, db):
    turn = _turn(db)
    body = _post(client, type="incoming", turn_id="exec-1", message_id=turn.message_id)
    assert _stamp(db, body["message_id"]) is None


def test_ac_qr_1_proactive_send_keeps_null(client, db):
    _turn(db)  # a live turn exists, but this send has neither a turn_id nor the reply text
    body = _post(client, message="A campaign notice, nothing to do with a turn.")
    assert _stamp(db, body["message_id"]) is None


def test_ac_qr_2_outgoing_stamped_through_the_n8n_turn_id(client, db):
    turn = _turn(db, reply="a reply the text step could never match")
    _post(client, type="incoming", turn_id="exec-77", message_id=turn.message_id, message="1 unit")
    out = _post(client, turn_id="exec-77")
    assert _stamp(db, out["message_id"]) == turn.id


def test_ac_qr_2_incoming_row_of_another_contact_is_not_followed(client, db):
    turn = _turn(db, contact=OTHER_CONTACT, reply="unrelated text")
    _post(client, type="incoming", turn_id="exec-78", contact_id=OTHER_CONTACT, message_id=turn.message_id)
    out = _post(client, turn_id="exec-78", message="a different text again")
    assert _stamp(db, out["message_id"]) is None


def test_ac_qr_3_outgoing_stamped_by_stored_reply_text_ignoring_whitespace(client, db):
    turn = _turn(db)
    out = _post(client, message="I read   ELP3754 from that photo.\n\n How many units of ELP3754?  ")
    assert _stamp(db, out["message_id"]) == turn.id


def test_ac_qr_3_latest_matching_turn_wins(client, db):
    now = datetime.now(tz=timezone.utc)
    _turn(db, finished_at=now - timedelta(seconds=60))
    newer = _turn(db, finished_at=now - timedelta(seconds=10))
    out = _post(client, sent_at=_ms(now))
    assert _stamp(db, out["message_id"]) == newer.id


def test_ac_qr_3_never_a_turn_of_another_contact(client, db):
    _turn(db, contact=OTHER_CONTACT)
    out = _post(client)
    assert _stamp(db, out["message_id"]) is None


def test_ac_qr_3_never_a_dry_run_turn(client, db):
    _turn(db, is_test=True)
    out = _post(client)
    assert _stamp(db, out["message_id"]) is None


def test_ac_qr_3_never_a_turn_outside_the_120_second_window(client, db):
    now = datetime.now(tz=timezone.utc)
    _turn(db, finished_at=now - timedelta(seconds=300))
    out = _post(client, sent_at=_ms(now))
    assert _stamp(db, out["message_id"]) is None


def test_ac_qr_3_never_a_turn_whose_reply_differs(client, db):
    _turn(db, reply="How many units of SRTWC287-S-150?")
    out = _post(client)
    assert _stamp(db, out["message_id"]) is None


def test_ac_qr_4_explicit_payload_value_wins(client, db):
    _turn(db)  # the text step would stamp this one
    explicit = str(uuid.uuid4())
    out = _post(client, chatbot_turn_id=explicit)
    assert _stamp(db, out["message_id"]) == explicit


def test_ac_qr_4_duplicate_mirror_fills_only_when_null(client, db):
    first = _post(client, message="A send the engine never answered.")
    assert _stamp(db, first["message_id"]) is None
    explicit = str(uuid.uuid4())
    client.post(INGEST_URL, json={**first, "chatbot_turn_id": explicit})
    assert _stamp(db, first["message_id"]) == explicit, "a NULL column is filled by the later lane"
    client.post(INGEST_URL, json={**first, "chatbot_turn_id": str(uuid.uuid4())})
    assert _stamp(db, first["message_id"]) == explicit, "a stamped column is never overwritten"
