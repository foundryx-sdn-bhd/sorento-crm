"""Respond.io direct message webhook (lane CHAT-LOCAL-FIRST, UAC section C).

`POST /api/v1/public/respond/webhook` writes the same row the n8n ingest writes, through
the same writer, so the two feeds dedupe on (contact_id, message_id) (AC-WH1); a bad or
missing signature writes nothing (AC-WH2); media and sender land in their columns
(AC-WH3); a new row pokes the event bus, a duplicate does not (AC-WH4).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.config import settings
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.chat_history import ChatHistory
from app.services import conversation_event_bus as bus
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

WEBHOOK_URL = "/api/v1/public/respond/webhook"
INGEST_URL = "/api/v1/external/chat-history/messages"
SECRET = "zzt-webhook-secret"
CONTACT_ID = "ZZT100000001"
MESSAGE_ID = 1786751891000000


class FakeTransport:
    def __init__(self):
        self.published: list[dict] = []

    def publish(self, channel, payload):
        self.published.append(json.loads(payload))

    def subscribe(self, channel):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def transport():
    fake = FakeTransport()
    bus.set_transport(fake)
    try:
        yield fake
    finally:
        bus.set_transport(None)


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "respond_webhook_secret", SECRET)

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


def _event(**overrides) -> dict:
    body = {
        "event_type": "message.received",
        "event_id": "evt-1",
        "contact": {
            "id": CONTACT_ID,
            "firstName": "Zzt",
            "lastName": "Hook",
            "phone": "+60100000001",
        },
        "message": {
            "messageId": MESSAGE_ID,
            "contactId": CONTACT_ID,
            "channelId": 12,
            "traffic": "incoming",
            "timestamp": 1786751891000,
            "message": {"type": "text", "text": "Sure, I will check that for you."},
            "sender": {"source": "contact"},
            "status": [{"value": "sent", "timestamp": 1786751891000}],
        },
        "channel": {"id": 12, "name": "Sorento WA", "source": "whatsapp_cloud"},
    }
    body.update(overrides)
    return body


def _signed(body: dict, *, secret: str = SECRET, encoding: str = "hex") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    sig = digest.hex() if encoding == "hex" else base64.b64encode(digest).decode("ascii")
    return raw, {"x-respond-signature": sig, "content-type": "application/json"}


def _post(client, body: dict, headers: dict | None = None, raw: bytes | None = None):
    if raw is None:
        raw, signed = _signed(body)
        headers = {**signed, **(headers or {})}
    return client.post(WEBHOOK_URL, content=raw, headers=headers)


def _rows(db, message_id=MESSAGE_ID):
    return (
        db.query(ChatHistory)
        .filter(ChatHistory.contact_id == CONTACT_ID, ChatHistory.message_id == str(message_id))
        .all()
    )


# ---------------------------------------------------------------------------
# AC-WH2: auth
# ---------------------------------------------------------------------------


def test_no_signature_is_401_and_writes_nothing(client, db):
    raw = json.dumps(_event()).encode()
    r = client.post(WEBHOOK_URL, content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 401
    assert _rows(db) == []


def test_a_wrong_signature_is_401(client, db):
    raw, _ = _signed(_event(), secret="someone-else")
    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={"x-respond-signature": "00" * 32, "content-type": "application/json"},
    )
    assert r.status_code == 401
    assert _rows(db) == []


def test_a_signature_over_a_different_body_is_401(client, db):
    _, headers = _signed(_event())
    tampered = json.dumps(_event(message={**_event()["message"], "traffic": "outgoing"})).encode()
    r = client.post(WEBHOOK_URL, content=tampered, headers=headers)
    assert r.status_code == 401
    assert _rows(db) == []


def test_an_unconfigured_secret_is_503(client, db, monkeypatch):
    monkeypatch.setattr(settings, "respond_webhook_secret", None)
    r = _post(client, _event())
    assert r.status_code == 503
    assert _rows(db) == []


def test_a_base64_signature_is_accepted(client, db):
    raw, headers = _signed(_event(), encoding="base64")
    r = client.post(WEBHOOK_URL, content=raw, headers=headers)
    assert r.status_code == 200, r.text
    assert len(_rows(db)) == 1


def test_the_shared_secret_header_is_accepted_without_a_signature(client, db):
    raw = json.dumps(_event()).encode()
    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={"x-respond-webhook-secret": SECRET, "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert len(_rows(db)) == 1


def test_a_wrong_shared_secret_is_401(client, db):
    raw = json.dumps(_event()).encode()
    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={"x-respond-webhook-secret": "nope", "content-type": "application/json"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# RESPOND-MULTI-KEY: Respond.io makes one webhook per event type, each with its own
# auto-generated base64 signing key, so the setting holds several keys and any one passes.
# ---------------------------------------------------------------------------

KEY_ONE = "k9/Fq+2Zr8LmW1x/Pt7+eD4uYc0sHn6vJb3aTq5oRg8="
KEY_TWO = "Zx+7mP/2Qw9eLr4/Tn6+Ya1cVb8uKd3sHj5gFo0iEk4="


@pytest.mark.parametrize(
    "configured",
    [
        f"{KEY_ONE}\n{KEY_TWO}",
        f"{KEY_ONE},{KEY_TWO}",
        f"  {KEY_ONE} ,\n\n , {KEY_TWO}\r\n",
        f"{KEY_ONE} {KEY_TWO}",
    ],
)
def test_a_request_signed_with_the_second_key_passes(client, db, monkeypatch, configured):
    monkeypatch.setattr(settings, "respond_webhook_secret", configured)
    raw, headers = _signed(_event(), secret=KEY_TWO)
    r = client.post(WEBHOOK_URL, content=raw, headers=headers)
    assert r.status_code == 200, r.text
    assert len(_rows(db)) == 1


def test_a_request_signed_with_the_first_key_passes(client, db, monkeypatch):
    monkeypatch.setattr(settings, "respond_webhook_secret", f"{KEY_ONE}\n{KEY_TWO}")
    raw, headers = _signed(_event(), secret=KEY_ONE, encoding="base64")
    r = client.post(WEBHOOK_URL, content=raw, headers=headers)
    assert r.status_code == 200, r.text


def test_a_request_signed_with_an_unknown_key_is_401(client, db, monkeypatch):
    monkeypatch.setattr(settings, "respond_webhook_secret", f"{KEY_ONE}\n{KEY_TWO}")
    raw, headers = _signed(_event(), secret="Qm9ndXMva2V5K3dpdGgvc2xhc2hlcz09")
    r = client.post(WEBHOOK_URL, content=raw, headers=headers)
    assert r.status_code == 401
    assert _rows(db) == []


def test_the_whole_key_list_is_not_itself_a_key(client, db, monkeypatch):
    configured = f"{KEY_ONE}\n{KEY_TWO}"
    monkeypatch.setattr(settings, "respond_webhook_secret", configured)
    raw, headers = _signed(_event(), secret=configured)
    assert client.post(WEBHOOK_URL, content=raw, headers=headers).status_code == 401
    shared = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={"x-respond-webhook-secret": configured, "content-type": "application/json"},
    )
    assert shared.status_code == 401


def test_the_shared_secret_header_matches_any_configured_key(client, db, monkeypatch):
    monkeypatch.setattr(settings, "respond_webhook_secret", f"{KEY_ONE}, {KEY_TWO}")
    raw = json.dumps(_event()).encode()
    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={"x-respond-webhook-secret": KEY_TWO, "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("configured", [" ", ",", " ,\n, \t"])
def test_a_setting_with_only_separators_is_503(client, db, monkeypatch, configured):
    monkeypatch.setattr(settings, "respond_webhook_secret", configured)
    r = _post(client, _event())
    assert r.status_code == 503
    assert _rows(db) == []


# ---------------------------------------------------------------------------
# AC-WH1: the same row as n8n, deduped either way round
# ---------------------------------------------------------------------------


def test_a_signed_message_event_writes_the_row(client, db, transport):
    r = _post(client, _event())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    (row,) = _rows(db)
    assert row.channel == "whatsapp", "whatsapp_cloud normalises to the n8n channel name"
    assert row.type == "incoming"
    assert row.phone_number == "+60100000001"
    assert row.message == "Sure, I will check that for you."
    assert row.first_name == "Zzt" and row.last_name == "Hook"
    assert row.sender_source == "contact"
    assert row.respond_ts is not None


def test_the_webhook_then_n8n_is_one_row(client, db, transport):
    hook = _post(client, _event())
    n8n = client.post(
        INGEST_URL,
        json={
            "channel": "whatsapp",
            "contact_id": CONTACT_ID,
            "phone_number": "+60100000001",
            "message": "Sure, I will check that for you.",
            "sent_at": 1786751891000,
            "type": "incoming",
            "message_id": str(MESSAGE_ID),
            "turn_id": "exec-42",
        },
    )
    assert hook.status_code == 200 and n8n.status_code == 201
    assert n8n.json()["status"] == "duplicate"
    assert n8n.json()["id"] == hook.json()["id"]
    (row,) = _rows(db)
    assert row.turn_id == "exec-42", "the losing lane still fills what the winner lacked"


def test_n8n_then_the_webhook_is_one_row(client, db, transport):
    n8n = client.post(
        INGEST_URL,
        json={
            "channel": "whatsapp",
            "contact_id": CONTACT_ID,
            "phone_number": "+60100000001",
            "message": "Sure, I will check that for you.",
            "sent_at": 1786751891000,
            "type": "incoming",
            "message_id": str(MESSAGE_ID),
        },
    )
    hook = _post(client, _event())
    assert hook.status_code == 200
    assert hook.json() == {"id": n8n.json()["id"], "status": "duplicate"}
    assert len(_rows(db)) == 1


def test_a_replayed_webhook_is_a_duplicate_not_an_error(client, db, transport):
    first = _post(client, _event())
    second = _post(client, _event())
    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "status": "duplicate"}
    assert len(_rows(db)) == 1


# ---------------------------------------------------------------------------
# AC-WH3: media + sender
# ---------------------------------------------------------------------------


def test_an_attachment_event_stores_the_media_columns(client, db, transport):
    body = _event()
    body["event_type"] = "message.sent"
    body["message"]["traffic"] = "outgoing"
    body["message"]["messageId"] = MESSAGE_ID + 1_000_000
    body["message"]["message"] = {
        "type": "attachment",
        "attachment": {"type": "file", "url": "https://cdn.chatapi.net/q.pdf", "fileName": "Quote%2042.pdf"},
    }
    body["message"]["sender"] = {"source": "user", "userId": 9911}

    r = _post(client, body)
    assert r.status_code == 200, r.text
    (row,) = _rows(db, MESSAGE_ID + 1_000_000)
    assert row.type == "outgoing"
    assert row.media_url == "https://cdn.chatapi.net/q.pdf"
    assert row.media_type == "file"
    assert row.media_file_name == "Quote 42.pdf"
    assert row.message == "[file] Quote 42.pdf"
    assert row.sender_source == "user"
    assert row.sender_user_id == "9911"


def test_a_media_url_on_an_unknown_host_is_dropped_not_stored(client, db, transport):
    """Security review finding 7: the thread renders media_url into an image tag, so a
    forged host must not be stored. The type and file name still describe the message."""
    body = _event()
    body["message"]["message"] = {
        "type": "attachment",
        "attachment": {"type": "image", "url": "https://attacker.example/beacon.png", "fileName": "x.png"},
    }
    r = _post(client, body)
    assert r.status_code == 200, r.text
    (row,) = _rows(db)
    assert row.media_url is None
    assert row.media_type == "image"
    assert row.media_file_name == "x.png"


def test_an_otp_template_code_reaches_neither_the_row_nor_the_log(client, db, transport):
    """Security review finding 1 (reviewer B2, #1280, applied to this lane): the sign-in
    template's code is scrubbed before the row and before the integration log."""
    from app.models.integration import IntegrationLog
    from app.models.respond_template import RespondTemplateDefault

    db.add(
        RespondTemplateDefault(
            use_case="login_otp", template_name_snapshot="login_otp_v1", param_mapping={"1": "otp_code"}
        )
    )
    db.flush()
    body = _event()
    body["event_type"] = "message.sent"
    body["message"]["traffic"] = "outgoing"
    body["message"]["sender"] = {"source": "api"}
    body["message"]["message"] = {
        "type": "whatsapp_template",
        "text": "Your sign-in code is 483920",
        "template": {
            "name": "login_otp_v1",
            "components": [{"type": "body", "parameters": [{"type": "text", "text": "483920"}]}],
        },
    }

    r = _post(client, body)
    assert r.status_code == 200, r.text
    (row,) = _rows(db)
    assert "483920" not in (row.message or "")
    logs = db.query(IntegrationLog).filter(IntegrationLog.integration_channel == "respond_webhook").all()
    assert logs, "the delivery is logged"
    assert all("483920" not in (log.request_payload or "") for log in logs)


def test_a_signature_header_with_a_stray_byte_is_401_not_500(client, db):
    raw = json.dumps(_event()).encode()
    r = client.post(
        WEBHOOK_URL,
        content=raw,
        headers={b"x-respond-signature": b"caf\xe9", b"content-type": b"application/json"},
    )
    assert r.status_code == 401


def test_a_body_past_the_cap_is_413_before_any_signature_work(client, db):
    from app.api.v1.public.respond_webhook import MAX_BODY_BYTES

    body = _event()
    body["padding"] = "x" * (MAX_BODY_BYTES + 10)
    raw, headers = _signed(body)
    r = client.post(WEBHOOK_URL, content=raw, headers=headers)
    assert r.status_code == 413
    assert _rows(db) == []


def test_n8n_can_send_the_media_columns_too(client, db, transport):
    r = client.post(
        INGEST_URL,
        json={
            "channel": "whatsapp",
            "contact_id": CONTACT_ID,
            "phone_number": "+60100000001",
            "message": "[image] photo.jpg",
            "sent_at": 1786751891000,
            "type": "incoming",
            "message_id": str(MESSAGE_ID),
            "media_url": "https://cdn/photo.jpg",
            "media_type": "image",
            "media_file_name": "photo.jpg",
            "sender_source": "contact",
        },
    )
    assert r.status_code == 201, r.text
    (row,) = _rows(db)
    assert (row.media_url, row.media_type, row.media_file_name) == (
        "https://cdn/photo.jpg", "image", "photo.jpg",
    )


# ---------------------------------------------------------------------------
# AC-WH4 + the rest of the contract
# ---------------------------------------------------------------------------


def test_a_new_row_pokes_the_bus_once_and_a_duplicate_does_not(client, db, transport):
    _post(client, _event())
    _post(client, _event())
    assert [e["type"] for e in transport.published] == ["message"]
    assert transport.published[0]["contact_id"] == CONTACT_ID


def test_a_non_message_event_is_ignored_with_200(client, db, transport):
    r = _post(client, {"event_type": "contact.updated", "contact": {"id": CONTACT_ID}})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert db.query(ChatHistory).filter(ChatHistory.contact_id == CONTACT_ID).count() == 0
    assert transport.published == []


def test_a_stored_row_enters_the_reconcile_window(client, db, transport):
    """Kill test from review (AC-RC1): both feeds stamp the contact's activity."""
    from app.models.chat_thread_sync_state import ChatThreadSyncState

    _post(client, _event())
    state = db.query(ChatThreadSyncState).filter(ChatThreadSyncState.contact_id == CONTACT_ID).one()
    assert state.last_activity_at is not None
    assert state.newest_synced_message_id is None, "a webhook row never moves the Respond watermark"

    client.post(
        INGEST_URL,
        json={
            "channel": "whatsapp",
            "contact_id": CONTACT_ID,
            "phone_number": "+60100000001",
            "message": "later",
            "sent_at": 1786751991000,
            "type": "incoming",
            "message_id": str(MESSAGE_ID + 100_000_000),
        },
    )
    db.expire_all()
    state = db.query(ChatThreadSyncState).filter(ChatThreadSyncState.contact_id == CONTACT_ID).one()
    assert state.last_activity_at.timestamp() >= 1786751991


def test_the_clock_is_the_message_id_and_a_seconds_timestamp_is_not_1970(client, db, transport):
    """Review S8: `sent_at` comes from the message id like every other lane; a bare
    seconds `timestamp` on an id that is not a clock is scaled, not read as milliseconds."""
    body = _event()
    body["message"]["timestamp"] = 1786751000000  # ms, deliberately different from the id
    r = _post(client, body)
    assert r.status_code == 200
    (row,) = _rows(db)
    assert row.sent_at.year == 2026 and int(row.sent_at.timestamp()) == MESSAGE_ID // 1_000_000

    body = _event()
    body["message"]["messageId"] = "abc-not-a-clock"
    body["message"]["timestamp"] = 1786751891  # seconds
    r = _post(client, body)
    assert r.status_code == 200
    (row,) = _rows(db, "abc-not-a-clock")
    assert row.sent_at.year == 2026


def test_a_message_event_with_no_message_id_is_400(client, db):
    body = _event()
    del body["message"]["messageId"]
    r = _post(client, body)
    assert r.status_code == 400


def test_a_non_json_body_is_400(client, db):
    raw = b"not json"
    digest = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    r = client.post(WEBHOOK_URL, content=raw, headers={"x-respond-signature": digest})
    assert r.status_code == 400
