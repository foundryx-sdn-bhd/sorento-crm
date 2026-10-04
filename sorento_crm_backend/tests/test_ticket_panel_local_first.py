"""The ticket panel's live read answers from chat_histories, not from Respond.io.

Lane CHAT-LOCAL-FIRST, slice R9 "ticket panel reads local first".

``GET /{tracking_id}/conversation`` (what ``TicketConversationPanel`` polls and
refetches after a send) used to call ``RespondClient.list_messages`` on every
request, so a webhook row or the row mirrored at send time never showed. It now
returns the local-first thread page for the ticket's contact.

AC-TP1  3 stored rows are returned, ``source == "local"``, no Respond read.
AC-TP2  a Respond.io webhook delivery is returned with no Respond read.
AC-TP3  a CRM send (``POST .../ticket/send``) is mirrored and returned with no Respond read.
AC-TP4  ``cursor=<oldest id>`` pages OLDER rows.

Run:
    venv/bin/pytest tests/test_ticket_panel_local_first.py -q
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.chat_history import ChatHistory
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60100000001"
RESPOND_IO_ID = "ZZT100000001"
BASE = "/api/v1/sla-management/conversation-sla-tracking"
WEBHOOK_URL = "/api/v1/public/respond/webhook"
SECRET = "zzt-webhook-secret"

# Respond message ids are epoch microseconds; the local lane only surfaces ids it can parse.
FIRST_ID = 1786751891000000

_ACTOR: dict = {"id": None}


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _roles(monkeypatch):
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


@pytest.fixture
def respond_reads(monkeypatch):
    """Records every Respond list_messages call; the test asserts it stayed empty."""
    from app.services.integration_service import RespondClient

    calls: list[tuple] = []

    def _recorder(self, *args, **kwargs):
        calls.append((args, kwargs))
        return {"items": []}

    monkeypatch.setattr(RespondClient, "list_messages", _recorder)
    return calls


@pytest.fixture
def client(db):
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="ZZT-NORMAL", name="ZZT Normal"))
    db.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy_id,
            tier_level=1,
            tier_name="Tier 1",
            response_hours=4,
            resolution_hours=24,
        )
    )
    db.add(
        RespondContact(
            id=str(uuid.uuid4()),
            phone_number=PHONE,
            name="Zzt Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="zzt-r9-agent@test.com", name="Zzt Agent"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_CS_AGENT", name="ZZT CS Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Customer Service - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_cs_general",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    tracking = ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="ZZT_CS_AGENT",
            team_set_code="zzt_cs_general",
            policy_id=policy_id,
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id="wamid.zzt-r9",
            source_message_text="Please connect me to a person.",
        )
    )
    _ACTOR["id"] = assignee_id
    _ACTOR["name"] = "Zzt Agent"
    return tracking


def _seed_messages(db, n: int, *, prefix: str = "zzt msg") -> list[str]:
    base = datetime.utcnow() - timedelta(hours=2)
    ids = []
    for i in range(n):
        mid = str(FIRST_ID + i * 1_000_000)
        ids.append(mid)
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number=PHONE,
                message=f"{prefix} {i}",
                sent_at=base + timedelta(minutes=i),
                type="incoming",
                message_id=mid,
            )
        )
    db.commit()
    return ids


def _ids(body: dict) -> list[str]:
    return [str(i["messageId"]) for i in body["items"]]


def test_tp1_ticket_panel_reads_stored_rows_without_respond(client, db, respond_reads):
    """AC-TP1: stored rows come back as a local page and Respond is never read."""
    tracking = _seed(db)
    stored = _seed_messages(db, 3)

    r = client.get(f"{BASE}/{tracking.id}/conversation")

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(stored) <= set(_ids(body))
    assert body.get("source") == "local"
    assert respond_reads == [], "list_messages called: the panel read went to Respond.io"


def _event(message_id: int, text_: str) -> dict:
    return {
        "event_type": "message.received",
        "event_id": "evt-zzt-r9",
        "contact": {
            "id": RESPOND_IO_ID,
            "firstName": "Zzt",
            "lastName": "Contact",
            "phone": PHONE,
        },
        "message": {
            "messageId": message_id,
            "contactId": RESPOND_IO_ID,
            "channelId": 12,
            "traffic": "incoming",
            "timestamp": message_id // 1000,
            "message": {"type": "text", "text": text_},
            "sender": {"source": "contact"},
            "status": [{"value": "sent", "timestamp": message_id // 1000}],
        },
        "channel": {"id": 12, "name": "Zzt WA", "source": "whatsapp_cloud"},
    }


def test_tp2_webhook_delivery_shows_in_ticket_panel_without_respond(
    client, db, respond_reads, monkeypatch
):
    """AC-TP2: a Respond.io webhook delivery is returned by the panel read, no Respond read."""
    from app.config import settings
    from tests._external_auth import external_permissions_granted

    monkeypatch.setattr(settings, "respond_webhook_secret", SECRET)
    tracking = _seed(db)
    new_id = int(time.time() * 1_000_000)
    raw = json.dumps(_event(new_id, "Zzt webhook hello")).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()

    with external_permissions_granted():
        hook = client.post(
            WEBHOOK_URL,
            content=raw,
            headers={"x-respond-signature": sig, "content-type": "application/json"},
        )
    assert hook.status_code == 200, hook.text

    r = client.get(f"{BASE}/{tracking.id}/conversation")

    assert r.status_code == 200, r.text
    assert str(new_id) in _ids(r.json())
    assert respond_reads == [], "list_messages called: the panel read went to Respond.io"


def test_tp3_ticket_send_is_mirrored_and_shown_without_respond(
    client, db, respond_reads, monkeypatch
):
    """AC-TP3: a CRM ticket send writes its chat_histories row and the panel read returns it."""
    from app.services import crm_chat_outbound_webhook, respond_chat_template_service

    sent_id = int(time.time() * 1_000_000)

    def _fake_send(*args, **kwargs):
        return {
            "sent_as": "message",
            "rendered_text": "Zzt reply from the CRM",
            "flattened": False,
            "window_state": {"open": True},
            "response": {"messageId": sent_id},
        }

    monkeypatch.setattr(respond_chat_template_service, "send_chat_message_for", _fake_send)
    monkeypatch.setattr(
        crm_chat_outbound_webhook, "notify_human_ticket_send", lambda *a, **k: True
    )
    tracking = _seed(db)

    sent = client.post(f"{BASE}/{tracking.id}/ticket/send", json={"text": "Zzt reply from the CRM"})
    assert sent.status_code == 200, sent.text
    mirrored = db.query(ChatHistory).filter(ChatHistory.message_id == str(sent_id)).all()
    assert len(mirrored) == 1, "mirror_outgoing_send did not write the row"

    r = client.get(f"{BASE}/{tracking.id}/conversation")

    assert r.status_code == 200, r.text
    body = r.json()
    assert str(sent_id) in _ids(body)
    assert "Zzt reply from the CRM" in [i["message"]["text"] for i in body["items"]]
    assert respond_reads == [], "list_messages called: the panel read went to Respond.io"


def test_tp4_cursor_pages_older_rows(client, db, respond_reads):
    """AC-TP4: cursor=<oldest returned id> returns the older page."""
    tracking = _seed(db)
    stored = _seed_messages(db, 6)

    first = client.get(f"{BASE}/{tracking.id}/conversation", params={"limit": 3})
    assert first.status_code == 200, first.text
    first_ids = _ids(first.json())
    assert sorted(first_ids) == sorted(stored[3:]), "first page is the newest 3"

    oldest = min(first_ids, key=int)
    second = client.get(
        f"{BASE}/{tracking.id}/conversation", params={"limit": 3, "cursor": oldest}
    )
    assert second.status_code == 200, second.text
    assert sorted(_ids(second.json())) == sorted(stored[:3]), "second page is the older 3"
    assert respond_reads == [], "list_messages called: the panel read went to Respond.io"
