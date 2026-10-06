"""Lenient inbound schema for POST /api/v1/external/chat-history/messages.

Lane CHAT-HISTORY-SCHEMA (owner, 6 Oct 2026, prod). n8n's redis-consume-queue-mongo
lane failed 422 on two shapes it has always been able to produce:

  1. quote-reply items carry ``reply_to_message_id`` as a NUMBER (Respond's
     ``messageId`` is epoch microseconds, and one consume-main variant forwards it
     unquoted) while ``message_id`` arrives as a string. Every quote-reply save
     failed, so the SLA pairing, the inbound push and the quote-reply state lookup
     all lost their incoming row.
  2. non-text incoming items (photo / voice / document) built the Redis item without
     a ``message`` key at all (``undefined`` is dropped by JSON.stringify).

The fix is at OUR boundary: ids coerce int -> str, ``message`` defaults to "". No
guessing - an absent message is stored as the empty string, never invented.

A 422 on this route is also recorded in ``api_call_log`` with the caller's
``X-Source`` and the offending field, so the next schema drift shows up in the
System > API call logs screen instead of only in n8n's execution log.

Run:
    SORENTO_ENV_FILE=.env.ci-tests venv/bin/pytest tests/test_chat_history_lenient_schema.py -q
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.chat_history import ChatHistory
from app.services.conversation_variables_service import get_referenced_result_set
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

INGEST_URL = "/api/v1/external/chat-history/messages"
RESPOND_IO_ID = "ZZT900000777"
PHONE = "+60100000777"
# Respond message ids ARE epoch microseconds. The outgoing one is what the user
# later quote-replies to; n8n forwards it as a bare JSON number.
OUTGOING_MESSAGE_ID = 1791283416935171
INCOMING_MESSAGE_ID = "1791283495000000"
RESULT_SET = [{"idx": 1, "uuid": "zzt-row-1", "label": "ZZT option one"}]


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


@pytest.fixture
def written(monkeypatch):
    """Rows the api_call_log middleware would have persisted, captured in place
    of its own database session (the same seam tests/test_api_call_log_middleware.py
    uses)."""
    import app.services.api_call_log_service as svc

    rows: list[dict] = []
    monkeypatch.setattr(svc, "write_call_log", lambda db, **fields: rows.append(fields))

    class _Session:
        def close(self):
            pass

    monkeypatch.setattr("app.database.SessionLocal", lambda: _Session())
    return rows


@pytest.fixture
def client(db, written):
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
        "phone_number": PHONE,
        "message": "how about SRTWC287",
        "sent_at": 1780751906900,
        "type": "incoming",
        "message_id": INCOMING_MESSAGE_ID,
        "turn_id": "exec-1",
    }
    payload.update(overrides)
    return payload


def _row(db, row_id: int) -> ChatHistory:
    return db.query(ChatHistory).filter(ChatHistory.id == row_id).one()


# ------------------------------------------------------------------ fix A: ids


def test_reply_to_message_id_as_int_is_stored_as_string_and_resolves_the_quote(client, db):
    """The prod failure: ``reply_to_message_id: 1791283416935171`` (a number) was a
    422 ``string_type``. It must save, land as the string form of that number, and
    the quote-reply lookup (string vs string on the column) must find the outgoing
    row it points at."""
    outgoing = client.post(
        INGEST_URL,
        json=_payload(
            type="outgoing",
            message="1. ZZT option one",
            message_id=str(OUTGOING_MESSAGE_ID),
            result=RESULT_SET,
            turn_id="exec-0",
        ),
    )
    assert outgoing.status_code == 201, outgoing.text

    incoming = client.post(
        INGEST_URL,
        json=_payload(
            message="1",
            reply_to_message_id=OUTGOING_MESSAGE_ID,  # int, exactly as n8n sends it
            reply_to_message="1. ZZT option one",
        ),
    )
    assert incoming.status_code == 201, incoming.text

    row = _row(db, incoming.json()["id"])
    assert row.reply_to_message_id == str(OUTGOING_MESSAGE_ID)
    assert isinstance(row.reply_to_message_id, str)

    # The lookup the chatbot makes with the stored id must hit the outgoing row.
    assert (
        get_referenced_result_set(
            db, respond_io_id=RESPOND_IO_ID, message_id=row.reply_to_message_id
        )
        == RESULT_SET
    )


def test_message_id_as_int_is_accepted_and_dedupes_against_its_string_form(client, db):
    """``message_id`` as a number saves, and is the SAME id as its string form:
    the second lane (which sends the string) resolves the row the first created."""
    first = client.post(INGEST_URL, json=_payload(message_id=OUTGOING_MESSAGE_ID))
    assert first.status_code == 201, first.text
    assert _row(db, first.json()["id"]).message_id == str(OUTGOING_MESSAGE_ID)

    second = client.post(INGEST_URL, json=_payload(message_id=str(OUTGOING_MESSAGE_ID)))
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["status"] == "duplicate"


def test_turn_id_as_int_is_accepted(client, db):
    """``$execution.id`` is numeric in n8n; a variant that forwards it unquoted
    must not lose the turn pairing to a 422."""
    r = client.post(INGEST_URL, json=_payload(turn_id=4242))
    assert r.status_code == 201, r.text
    assert _row(db, r.json()["id"]).turn_id == "4242"


def test_null_ids_stay_null(client, db):
    r = client.post(
        INGEST_URL, json=_payload(message_id=None, reply_to_message_id=None, turn_id=None)
    )
    assert r.status_code == 201, r.text
    row = _row(db, r.json()["id"])
    assert row.message_id is None
    assert row.reply_to_message_id is None
    assert row.turn_id is None


# ------------------------------------------------------------- fix B: message


def test_incoming_without_message_key_stores_an_empty_string(client, db):
    """A photo / voice / document item has no ``message`` key at all. The row
    must still land (SLA pairing, push, state lookup all need it) with "" as the
    text - never invented, never NULL (the column is NOT NULL)."""
    payload = _payload(state_trace={"v": 1, "before": None, "parser_raw": None,
                                    "parser_applied": None, "after": None})
    del payload["message"]

    r = client.post(INGEST_URL, json=payload)
    assert r.status_code == 201, r.text
    row = _row(db, r.json()["id"])
    assert row.message == ""
    assert row.type == "incoming"
    assert row.state_trace is not None and row.state_trace["after"] is None


def test_text_payload_is_unchanged(client, db):
    """The shape every working save already uses keeps working verbatim."""
    r = client.post(INGEST_URL, json=_payload())
    assert r.status_code == 201, r.text
    row = _row(db, r.json()["id"])
    assert row.message == "how about SRTWC287"
    assert row.message_id == INCOMING_MESSAGE_ID
    assert row.phone_number == PHONE
    assert row.turn_id == "exec-1"


def test_outgoing_with_null_turn_id_is_accepted(client, db):
    """A proactive send (campaign, SLA notice) has no turn; ``turn_id: null`` was
    always accepted and must stay so."""
    r = client.post(
        INGEST_URL,
        json=_payload(type="outgoing", message="Your order is on its way.", turn_id=None),
    )
    assert r.status_code == 201, r.text
    assert _row(db, r.json()["id"]).turn_id is None


# -------------------------------------------------------- fix C: 422 is logged


def test_a_422_on_this_route_is_recorded_with_source_and_offending_field(client, written):
    """The outgoing-null shape the live n8n scan found: ``phone_number: null``.
    The column is NOT NULL and both read schemas type it as ``str``, so it stays
    required - but the refusal must be visible in the API call log, naming the
    caller and the field, instead of only in n8n's execution log."""
    r = client.post(
        INGEST_URL,
        json=_payload(type="outgoing", phone_number=None, turn_id=None),
        headers={"X-Source": "n8n"},
    )
    assert r.status_code == 422

    assert len(written) == 1, "exactly one api_call_log row per request"
    row = written[0]
    assert row["endpoint"] == INGEST_URL
    assert row["status_code"] == 422
    assert row["outcome"] == "client_error"
    assert row["source"] == "n8n"
    assert row["error_message"], "a validation refusal must name what was wrong"
    assert "phone_number" in row["error_message"]


def test_a_422_log_names_every_offending_field(client, written):
    """Two drifts in one payload -> both fields named, so one log row is the
    whole diagnosis."""
    payload = _payload(sent_at="not-a-number", phone_number=None)

    r = client.post(INGEST_URL, json=payload, headers={"X-Source": "n8n"})
    assert r.status_code == 422

    message = written[0]["error_message"]
    assert "phone_number" in message
    assert "sent_at" in message


def test_a_successful_save_logs_no_error_message(client, written):
    r = client.post(INGEST_URL, json=_payload(), headers={"X-Source": "n8n"})
    assert r.status_code == 201
    assert written[0]["status_code"] == 201
    assert written[0]["error_message"] is None
