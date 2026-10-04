"""Mention email opt-in: notify_email_on_mention (email lane for AC-L1).

An @mention in an internal note used to be in-app only. It now also emails the
mentioned user, gated on the per-user ``notify_email_on_mention`` toggle - the
same per-event opt-in shape as ``notify_email_on_deadline_extended``. There is no
WhatsApp twin for this event.

Pinned here:

1. Flag ON (the column default): mentioning a user creates BOTH an in_app and an
   email NotificationDelivery, and the delivery dispatcher turns the email one
   into an EmailOutbox row addressed to that user's email address.
2. Flag OFF: in_app only. No email delivery row at all.
3. The flag round-trips through the API the account page uses
   (GET/PATCH /notifications/preferences/channels) and through get_me, whose
   manual dict builder silently drops any field it does not list (CLAUDE.md).

Postgres only, via ``tests/_pg_fixture.blank_session`` (never sqlite).

Run:
    venv/bin/pytest tests/test_ticket_comment_mention_email.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.email_outbox import EmailOutbox
from app.models.notification import Notification, NotificationDelivery
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from app.tasks.notification_tasks import _enqueue_email_for_delivery, _resolve_event_key
from tests._pg_fixture import blank_session

PHONE = "+60100000079"
BASE = "/api/v1/sla-management/conversation-sla-tracking"
CHANNELS = "/api/v1/notifications/preferences/channels"
ME = "/api/v1/user-management/users/me"


class _NoMessagesRespondClient:
    """A note is internal. Any send path reached from this suite is a failure."""

    def list_messages(self, identifier, limit=50, cursor=None):
        return {"items": []}

    def send_message(self, *a, **k):  # pragma: no cover - guarded, never called
        raise AssertionError("A comment must never send a message to the contact.")

    def create_comment(self, identifier, text):
        return {"id": f"respond-comment-{uuid.uuid4()}"}


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    # The delivery dispatcher is driven explicitly below, not through RQ.
    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.integration_service.RespondClient",
        lambda *a, **k: _NoMessagesRespondClient(),
    )
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _no_admins(monkeypatch):
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


@pytest.fixture(autouse=True)
def _no_respond_mirror(monkeypatch):
    monkeypatch.setattr(
        "app.services.ticket_comment_service.mirror_comment_to_respond",
        lambda *a, **k: False,
    )


_ACTOR: dict = {"id": None}


@pytest.fixture
def client(db):
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _act_as(user_id: str) -> None:
    _ACTOR["id"] = user_id
    _ACTOR["name"] = "Test Actor"


def _seed(db, *, email_pref: bool = True):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="NORMAL", name="Normal"))
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
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            phone_number=PHONE,
            name="Aisyah Rahman",
            respond_io_id="10025999",
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    mentioned_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="cs1@test.com", name="Agent One", respond_user_id="900001"))
    db.add(
        User(
            id=mentioned_id,
            email="lead@test.com",
            name="Team Lead",
            notify_email_on_mention=email_pref,
        )
    )
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="CS_AGENT", name="CS Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="Customer Service - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="cs_general",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    return {
        "policy_id": policy_id,
        "assignee_id": assignee_id,
        "mentioned_id": mentioned_id,
        "agent_code": "CS_AGENT",
        "team_set_code": "cs_general",
    }


def _create_ticket(db, seed):
    payload = ConversationSLATrackingCreate(
        agent_code=seed["agent_code"],
        team_set_code=seed["team_set_code"],
        policy_id=seed["policy_id"],
        assigned_to_id=seed["assignee_id"],
        contact_phone_number=PHONE,
        source_message_id=f"wamid.{uuid.uuid4()}",
        source_message_text="Yes, please connect me to a person.",
    )
    return ConversationSLATrackingService(db).create_tracking(payload)


def _mention(client, tracking_id, mentioned_id):
    return client.post(
        f"{BASE}/{tracking_id}/comments",
        json={
            "body": "@Team Lead can you take a look?",
            "mentioned_user_ids": [mentioned_id],
        },
    )


def _deliveries(db, user_id):
    note = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.type == "conversation_ticket_comment",
        )
        .first()
    )
    assert note is not None, "no mention notification was created"
    rows = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.notification_id == note.id)
        .all()
    )
    return note, rows


# --------------------------------------------------------------- flag ON


def test_mention_with_flag_on_emails_the_mentioned_user(client, db):
    """Default (flag on): in_app + email deliveries, and the dispatcher writes an
    EmailOutbox row addressed to the mentioned user."""
    seed = _seed(db, email_pref=True)
    tracking = _create_ticket(db, seed)
    _act_as(seed["assignee_id"])

    assert _mention(client, tracking.id, seed["mentioned_id"]).status_code == 201

    note, rows = _deliveries(db, seed["mentioned_id"])
    channels = {d.channel for d in rows}
    assert "in_app" in channels
    assert "email" in channels
    assert "whatsapp" not in channels  # email-only event, no WhatsApp twin

    email_delivery = next(d for d in rows if d.channel == "email")
    user = db.query(User).filter(User.id == seed["mentioned_id"]).first()
    event_key = _resolve_event_key(note)
    _enqueue_email_for_delivery(db, note, user, email_delivery, event_key)

    db.refresh(email_delivery)
    assert email_delivery.status == "queued", email_delivery.error_message
    outbox = (
        db.query(EmailOutbox)
        .filter(EmailOutbox.event_key == event_key)
        .all()
    )
    assert len(outbox) == 1
    assert outbox[0].recipient_email == "lead@test.com"
    assert "mentioned you in a note" in outbox[0].subject


# -------------------------------------------------------------- flag OFF


def test_mention_with_flag_off_is_in_app_only(client, db):
    seed = _seed(db, email_pref=False)
    tracking = _create_ticket(db, seed)
    _act_as(seed["assignee_id"])

    assert _mention(client, tracking.id, seed["mentioned_id"]).status_code == 201

    _note, rows = _deliveries(db, seed["mentioned_id"])
    channels = {d.channel for d in rows}
    assert channels == {"in_app"}


# ------------------------------------------------------------ round-trip


def test_flag_round_trips_through_the_account_page_api(client, db):
    """GET returns it, PATCH flips it - the two calls the account panel makes."""
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email="member@t.com", name="Member", status="ACTIVE"))
    db.commit()
    _act_as(uid)

    r = client.get(CHANNELS)
    assert r.status_code == 200, r.text
    assert r.json()["notify_email_on_mention"] is True  # column default

    r = client.patch(CHANNELS, json={"notify_email_on_mention": False})
    assert r.status_code == 200, r.text
    assert r.json()["notify_email_on_mention"] is False

    db.expire_all()
    assert db.query(User).filter(User.id == uid).first().notify_email_on_mention is False
    assert client.get(CHANNELS).json()["notify_email_on_mention"] is False


def test_get_me_carries_the_flag(client, db):
    """A NON-default value, so a pass proves the manual dict builder lists the
    field rather than the UserResponse default happening to match."""
    uid = str(uuid.uuid4())
    db.add(
        User(
            id=uid,
            email="member2@t.com",
            name="Member Two",
            status="ACTIVE",
            notify_email_on_mention=False,
        )
    )
    db.commit()
    _act_as(uid)

    r = client.get(ME)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "notify_email_on_mention" in body
    assert body["notify_email_on_mention"] is False


def test_every_user_dict_builder_lists_the_flag():
    """Three manual dict builders in users.py must all carry the column, or it
    never reaches the frontend on one of the read paths."""
    from pathlib import Path

    src = Path("app/api/v1/user_management/users.py").read_text()
    assert src.count('"notify_email_on_mention"') >= 3
