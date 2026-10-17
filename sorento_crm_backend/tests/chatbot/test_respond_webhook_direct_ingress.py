"""CHAT-SPEED direct ingress, the webhook half (UAC AC-1..AC-5).

`POST /api/v1/public/respond/webhook` keeps storing every message exactly as before. With
`system_settings.chatbot_direct_ingress_mode` on, a NEW, fresh, incoming WhatsApp message from
the contact is also put on the `chat` queue as a turn, so the CRM no longer waits for the n8n
producer and dispatcher (plan `documentation/plans/chatbot/PLAN-chat-speed-ingress.md`).

The hand-off is faked at `chat_turns._submit` (the API process's bounded direct-turn pool):
these tests grade WHETHER the webhook hands a turn over and with what, never the turn itself
(that is `tests/chatbot/test_direct_ingress.py`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.config import settings
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.chat_history import ChatHistory
from app.models.user import SystemSetting
from app.services import conversation_event_bus as bus
from app.services import queue_service
from tests._pg_fixture import TEST_PREFIX, blank_session

WEBHOOK_URL = "/api/v1/public/respond/webhook"
SECRET = "zzt-webhook-secret-not-real-0002"  # made up; keys under 24 chars are ignored
CONTACT_ID = "ZZT100000077"
OTHER_CONTACT_ID = "ZZT100000078"


class _NullTransport:
    def publish(self, channel, payload):
        pass

    def subscribe(self, channel):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _quiet_bus():
    bus.set_transport(_NullTransport())
    try:
        yield
    finally:
        bus.set_transport(None)


@pytest.fixture
def enqueued(monkeypatch):
    """Every turn handed to the direct-turn pool, as (func name, args, kwargs)."""
    from app.tasks import chat_turns

    calls: list[tuple[str, tuple, dict]] = []

    def _fake(func, *args, **kwargs):
        calls.append((getattr(func, "__name__", str(func)), args, kwargs))
        return True

    monkeypatch.setattr(chat_turns, "_submit", _fake)
    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    return calls


def _chat_jobs(calls):
    return list(calls)


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
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _mode(db, mode: str, contacts: str | None = None, *, sender: bool = True) -> None:
    from app.schemas.respond_workspace import RespondWorkspaceCreate
    from app.services.respond_workspace_service import RespondWorkspaceService

    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting(id=str(uuid.uuid4()), name=f"{TEST_PREFIX} Co")
        db.add(row)
    row.chatbot_direct_ingress_mode = mode
    row.chatbot_direct_ingress_contacts = contacts
    db.commit()
    if sender:
        workspace = RespondWorkspaceService(db).get_default() or RespondWorkspaceService(db).create(
            RespondWorkspaceCreate(space_id="ZZT-space-direct", api_key="k", is_default=True)
        )
        workspace.chatbot_sender_url = "https://automate-zzt.example.com/webhook/chatbot-sender"
        db.commit()


def _now_message_id(age_seconds: float = 1.0) -> int:
    """A respond.io message id IS its send time in epoch microseconds."""
    return int((time.time() - age_seconds) * 1_000_000)


def _event(*, contact_id: str = CONTACT_ID, message_id: int | None = None, **overrides) -> dict:
    mid = message_id if message_id is not None else _now_message_id()
    body = {
        "event_type": "message.received",
        "event_id": f"evt-{mid}",
        "contact": {
            "id": contact_id,
            "firstName": "Zzt",
            "lastName": "Direct",
            "phone": "+60100000077",
            "custom_fields": [{"name": "is_human_intervened", "value": None}],
        },
        "message": {
            "messageId": mid,
            "contactId": contact_id,
            "channelId": 12,
            "traffic": "incoming",
            "timestamp": mid // 1000,
            "message": {"type": "text", "text": "SRTWC8517 got stock?"},
            "sender": {"source": "contact"},
        },
        "channel": {"id": 12, "name": "Sorento WA", "source": "whatsapp_cloud"},
    }
    for key, value in overrides.items():
        body[key] = value
    return body


def _post(client, body: dict, *, secret: str = SECRET):
    raw = json.dumps(body).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return client.post(
        WEBHOOK_URL,
        content=raw,
        headers={"x-respond-signature": sig, "content-type": "application/json"},
    )


def _stored(db, message_id: int) -> int:
    return (
        db.query(ChatHistory)
        .filter(ChatHistory.message_id == str(message_id))
        .count()
    )


# --------------------------------------------------------------------------- #
# AC-1: off is today
# --------------------------------------------------------------------------- #


def test_off_mode_enqueues_nothing(client, db, enqueued):
    _mode(db, "off")
    body = _event()
    r = _post(client, body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert _stored(db, body["message"]["messageId"]) == 1
    assert _chat_jobs(enqueued) == []


def test_no_settings_row_reads_as_off(client, db, enqueued):
    body = _event()
    r = _post(client, body)
    assert r.status_code == 200, r.text
    assert _chat_jobs(enqueued) == []


# --------------------------------------------------------------------------- #
# AC-2 / AC-3: all, and listed contacts
# --------------------------------------------------------------------------- #


def test_all_mode_enqueues_one_job(client, db, enqueued):
    _mode(db, "all")
    body = _event()
    r = _post(client, body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"

    jobs = _chat_jobs(enqueued)
    assert len(jobs) == 1, enqueued
    name, args, kwargs = jobs[0]
    assert name == "run_direct_turn_job"
    assert kwargs == {}
    payload = args[0]
    # The respond.io body travels verbatim: it IS the envelope's `message`.
    assert payload["message"]["messageId"] == body["message"]["messageId"]
    assert payload["contact"]["id"] == CONTACT_ID


def test_contacts_mode_only_listed(client, db, enqueued):
    _mode(db, "contacts", f"  {CONTACT_ID}\nZZT-someone-else, ")
    listed = _event(contact_id=CONTACT_ID)
    unlisted = _event(contact_id=OTHER_CONTACT_ID, message_id=_now_message_id(2.0))

    assert _post(client, listed).status_code == 200
    assert _post(client, unlisted).status_code == 200

    jobs = _chat_jobs(enqueued)
    assert len(jobs) == 1, enqueued
    assert jobs[0][1][0]["contact"]["id"] == CONTACT_ID


def test_contacts_mode_with_an_empty_list_enqueues_nothing(client, db, enqueued):
    _mode(db, "contacts", "")
    assert _post(client, _event()).status_code == 200
    assert _chat_jobs(enqueued) == []


# --------------------------------------------------------------------------- #
# AC-4: what never enqueues
# --------------------------------------------------------------------------- #


def test_skip_duplicate_delivery(client, db, enqueued):
    _mode(db, "all")
    body = _event()
    assert _post(client, body).json()["status"] == "created"
    second = _post(client, body)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(_chat_jobs(enqueued)) == 1


def test_skip_outgoing_message(client, db, enqueued):
    _mode(db, "all")
    body = _event()
    body["event_type"] = "message.sent"
    body["message"]["traffic"] = "outgoing"
    body["message"]["sender"] = {"source": "api"}
    assert _post(client, body).status_code == 200
    assert _chat_jobs(enqueued) == []


def test_skip_non_whatsapp_channel(client, db, enqueued):
    _mode(db, "all")
    body = _event(channel={"id": 13, "name": "FB", "source": "facebook"})
    assert _post(client, body).status_code == 200
    assert _chat_jobs(enqueued) == []


def test_skip_stale_message(client, db, enqueued):
    """Q3 (a): a body older than 120 s by its own clock never starts a turn here; the n8n
    copy still answers it. Guards a replayed or very late signed delivery."""
    _mode(db, "all")
    body = _event(message_id=_now_message_id(age_seconds=121))
    assert _post(client, body).status_code == 200
    assert _chat_jobs(enqueued) == []


def test_skip_message_with_no_clock(client, db, enqueued):
    _mode(db, "all")
    body = _event()
    body["message"]["messageId"] = "not-a-clock-zzt"
    body["message"].pop("timestamp")
    assert _post(client, body).status_code == 200
    assert _chat_jobs(enqueued) == []


def test_fresh_message_just_inside_the_window_enqueues(client, db, enqueued):
    _mode(db, "all")
    body = _event(message_id=_now_message_id(age_seconds=100))
    assert _post(client, body).status_code == 200
    assert len(_chat_jobs(enqueued)) == 1


def test_skip_shared_secret_header_delivery(client, db, enqueued, monkeypatch):
    """Security review M1: the shared-secret header mode carries the key in every request,
    so its holder can write any body, fresh clock included. Only a body HMAC starts a turn;
    the n8n copy still answers a header-mode delivery."""
    _mode(db, "all")
    body = _event()
    r = client.post(
        WEBHOOK_URL,
        content=json.dumps(body).encode("utf-8"),
        headers={"x-respond-webhook-secret": SECRET, "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert _chat_jobs(enqueued) == []


def test_skip_bad_signature(client, db, enqueued):
    _mode(db, "all")
    r = _post(client, _event(), secret="zzt-not-the-configured-key-xx")
    assert r.status_code == 401
    assert _chat_jobs(enqueued) == []


def test_the_webhook_contact_block_needs_no_custom_fields(client, db, enqueued):
    """respond.io's signed webhook never carries `custom_fields` (crew, 8,434 rows,
    11-15 Oct): the takeover flag is read by the JOB through a contact lookup, so the
    webhook gate must not require it."""
    _mode(db, "all")
    body = _event()
    body["contact"].pop("custom_fields")
    assert _post(client, body).status_code == 200
    assert len(_chat_jobs(enqueued)) == 1


def test_skip_over_the_rate_limit(client, db, enqueued):
    """The producer's own gate: more than 30 messages from one contact inside a second get
    n8n's fixed rate-limit reply. The n8n copy still sends it; this path stays out."""
    from app.services.chatbot import direct_ingress

    _mode(db, "all")
    queue_service.redis_conn.set(direct_ingress.rate_key(CONTACT_ID), 30, ex=5)
    try:
        assert _post(client, _event()).status_code == 200
        assert _chat_jobs(enqueued) == []
    finally:
        queue_service.redis_conn.delete(direct_ingress.rate_key(CONTACT_ID))


def test_under_the_rate_limit_counts_and_enqueues(client, db, enqueued):
    from app.services.chatbot import direct_ingress

    _mode(db, "all")
    queue_service.redis_conn.delete(direct_ingress.rate_key(CONTACT_ID))
    try:
        assert _post(client, _event()).status_code == 200
        assert len(_chat_jobs(enqueued)) == 1
        assert int(queue_service.redis_conn.get(direct_ingress.rate_key(CONTACT_ID))) == 1
    finally:
        queue_service.redis_conn.delete(direct_ingress.rate_key(CONTACT_ID))


@pytest.mark.parametrize(
    "inner",
    [
        {"type": "attachment", "attachment": {"type": "image", "url": "https://x.zzt/a.jpg"}},
        {"type": "attachment", "attachment": {"type": "audio", "url": "https://x.zzt/a.ogg"}},
        {"type": "location", "latitude": 3.1, "longitude": 101.6},
    ],
)
def test_skip_non_text_message(client, db, enqueued, inner):
    """Media stays on the n8n path: its extraction job drains on the same single fast
    worker the turn job would hold, so the turn would wait on a job that cannot start."""
    _mode(db, "all")
    body = _event()
    body["message"]["message"] = inner
    assert _post(client, body).status_code == 200
    assert _chat_jobs(enqueued) == []


def test_skip_message_from_a_staff_member(client, db, enqueued):
    """An incoming-traffic row the sender block marks as a user or the API is not the
    customer talking (a defensive guard; respond.io marks customer messages `contact`)."""
    _mode(db, "all")
    body = _event()
    body["message"]["sender"] = {"source": "user", "userId": 5}
    assert _post(client, body).status_code == 200
    assert _chat_jobs(enqueued) == []


# --------------------------------------------------------------------------- #
# AC-5: the queue can fail; the webhook cannot
# --------------------------------------------------------------------------- #


def test_enqueue_failure_is_swallowed(client, db, monkeypatch):
    from app.tasks import chat_turns

    _mode(db, "all")

    def _boom(func, *args, **kwargs):
        raise RuntimeError("cannot schedule new futures after shutdown (zzt)")

    monkeypatch.setattr(chat_turns, "_submit", _boom)
    body = _event()
    r = _post(client, body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert _stored(db, body["message"]["messageId"]) == 1


def test_skip_when_no_sender_is_configured(client, db, enqueued):
    """Review S2: with no sender URL the direct turn would win and send nothing, and the n8n
    copy would come back a duplicate. So the message stays on the n8n path."""
    _mode(db, "all", sender=False)
    assert _post(client, _event()).status_code == 200
    assert _chat_jobs(enqueued) == []


def test_rate_limit_counter_expires(client, db, enqueued):
    """Review B2: the counter carries a TTL from its first increment, so it resets."""
    from app.services.chatbot import direct_ingress

    _mode(db, "all")
    key = direct_ingress.rate_key(CONTACT_ID)
    queue_service.redis_conn.delete(key)
    try:
        assert _post(client, _event()).status_code == 200
        ttl_ms = queue_service.redis_conn.pttl(key)
        assert 0 < ttl_ms <= 1000, ttl_ms
        time.sleep(1.2)
        assert queue_service.redis_conn.get(key) is None
    finally:
        queue_service.redis_conn.delete(key)


def test_a_full_pool_leaves_the_message_to_n8n(client, db, monkeypatch):
    """The API process runs at most `DIRECT_TURN_THREADS` direct turns at once; past that the
    n8n copy answers, so a burst degrades to today's path rather than queueing here."""
    from app.tasks import chat_turns

    _mode(db, "all")
    ran: list[str] = []
    monkeypatch.setattr(chat_turns, "run_direct_turn_job", lambda payload: ran.append("ran"))
    monkeypatch.setattr(chat_turns, "_in_flight", chat_turns.DIRECT_TURN_THREADS)
    try:
        assert _post(client, _event()).status_code == 200
        assert ran == []
    finally:
        monkeypatch.setattr(chat_turns, "_in_flight", 0)
