"""Contact-keyed note CREATE from the Conversations inbox (UAC AC-N3, S4.9 gap 1).

The inbox thread is not looking at one ticket, so the note it writes cannot be
ticket-keyed. Before this endpoint the inbox could only offer Note when the
viewer happened to hold exactly one open ticket for the contact, and posted it
through the drawer's route - which made an internal annotation depend on an
assignment that has nothing to do with it.

What is pinned here:

1. **A view-holder may annotate.** A note is internal staff context, so the
   gate is ``sla_management.conversations.view`` - the same permission that
   lets them read the thread - never ticket assignment.
2. **The row is CONTACT-scoped**: ``tracking_id`` NULL, ``respond_contact_id``
   set. That is exactly the shape a Respond-ingested note has, so it renders
   in the contact list AND in every open ticket drawer for that contact
   without a second rule.
3. **Same side effects as the drawer's note** (``create_comment``): the
   mention notification, and the best-effort Respond mirror with its
   ``integration_log`` outbox row on success AND on failure.
4. **A note never reaches the contact** - no Respond SEND path is touched.

Run:
    venv/bin/pytest tests/test_conversations_contact_note_create.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.chat_history import ChatHistory
from app.models.integration import IntegrationLog
from app.models.notification import Notification, NotificationDelivery
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.ticket_comment import ConversationTicketComment
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60120000545"
RESPOND_IO_ID = "zzt-note-io-1"
TICKET_BASE = "/api/v1/sla-management/conversation-sla-tracking"
INBOX_BASE = "/api/v1/sla-management/conversations"
VIEW = "sla_management.conversations.view"
REPLY = "sla_management.conversations.reply"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "Test Actor"}


class _MirrorClient:
    """Respond accepts the comment mirror and refuses everything else: a note
    that leaked onto a SEND path fails the suite instead of messaging a real
    contact."""

    def __init__(self, *, fail: bool = False):
        self.comments: list[tuple[str, str]] = []
        self.fail = fail

    def create_comment(self, identifier: str, body: str) -> dict:
        self.comments.append((identifier, body))
        if self.fail:
            raise RuntimeError("401 Unauthorized")
        return {"id": "respond-comment-77"}

    def send_message(self, *a, **k):  # pragma: no cover - guarded, never called
        raise AssertionError("A note must never send a message to the contact.")

    def list_messages(self, *a, **k):
        return {"items": []}


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({VIEW, REPLY})
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture
def respond():
    """The Respond client every lane resolves to. Flip ``respond.fail`` inside a
    test to exercise the failure outbox."""
    stub = _MirrorClient()
    with patch("app.services.integration_service.RespondClient") as client_cls:
        client_cls.return_value = stub
        client_cls.for_identifier.return_value = stub
        client_cls.for_contact_id.return_value = stub
        yield stub


@pytest.fixture
def client(db, respond):
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


def _act_as(user_id: str) -> None:
    _ACTOR["id"] = user_id


@pytest.fixture
def seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=f"ZZT-{uuid.uuid4().hex[:6]}", name="ZZT Policy"))
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
            name="ZZT Note Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    colleague_id = str(uuid.uuid4())
    lead_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email=f"zzt-a-{assignee_id[:8]}@test.com", name="ZZT Assignee"))
    db.add(User(id=colleague_id, email=f"zzt-c-{colleague_id[:8]}@test.com", name="ZZT Colleague"))
    db.add(
        User(
            id=lead_id,
            email=f"zzt-l-{lead_id[:8]}@test.com",
            name="ZZT Team Lead",
            respond_user_id="900321",
        )
    )
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_NOTE_AGENT", name="ZZT Note Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Note Team - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_note_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()

    base = datetime(2026, 8, 12, 3, 0, 0)
    for i in range(2):
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number=PHONE,
                message=f"note hello {i}",
                sent_at=base + timedelta(minutes=i),
                type="incoming",
                message_id=str(9100 + i),
            )
        )
    db.commit()

    tracking = ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="ZZT_NOTE_AGENT",
            team_set_code="zzt_note_set",
            policy_id=policy_id,
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id="wamid.note-1",
            source_message_text="Please connect me to a person.",
        )
    )
    _act_as(colleague_id)
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "assignee_id": assignee_id,
        "colleague_id": colleague_id,
        "lead_id": lead_id,
        "tracking_id": str(tracking.id),
    }


# --------------------------------------------------------------------------- #
# Happy path + scope                                                           #
# --------------------------------------------------------------------------- #


def test_a_view_holder_with_no_ticket_can_write_a_contact_note(client, seed):
    """The colleague holds no ticket for this contact at all - under the old
    ticket-keyed route they could not annotate the thread they can read."""
    created = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/comments",
        json={"body": "Called them, they will send the receipt tonight."},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["body"] == "Called them, they will send the receipt tonight."
    assert payload["tracking_id"] is None, "a contact note belongs to no one ticket"
    assert payload["author_name"] == "ZZT Colleague"
    assert payload["source"] == "crm"
    assert payload["created_at"]

    listed = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments")
    assert [row["body"] for row in listed.json()] == [
        "Called them, they will send the receipt tonight."
    ]


def test_the_row_is_contact_scoped(client, db, seed):
    client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments", json={"body": "scoped"})
    row = db.query(ConversationTicketComment).one()
    assert row.tracking_id is None
    assert str(row.respond_contact_id) == seed["contact_id"]
    assert row.source == "crm"


def test_a_contact_note_renders_in_the_ticket_drawer_too(client, seed):
    """Same shape as a Respond-ingested note, so the drawer's existing
    contact-scope branch picks it up with no new rule."""
    client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments", json={"body": "seen from the drawer"})
    _act_as(seed["assignee_id"])
    listed = client.get(f"{TICKET_BASE}/{seed['tracking_id']}/comments")
    assert listed.status_code == 200, listed.text
    assert [row["body"] for row in listed.json()] == ["seen from the drawer"]


def test_a_note_on_this_contact_does_not_leak_into_another_contacts_thread(client, db, seed):
    other = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=other,
            phone_number="+60120000546",
            name="ZZT Other",
            respond_io_id="zzt-note-io-9",
            session_vars={},
        )
    )
    db.commit()
    client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments", json={"body": "not for the other one"})
    bodies = [row["body"] for row in client.get(f"{INBOX_BASE}/zzt-note-io-9/comments").json()]
    assert bodies == []


def test_a_contact_ref_may_be_a_phone_number(client, seed):
    created = client.post(f"{INBOX_BASE}/{PHONE}/comments", json={"body": "by phone"})
    assert created.status_code == 201, created.text


# --------------------------------------------------------------------------- #
# Auth + validation                                                            #
# --------------------------------------------------------------------------- #


def test_without_the_view_permission_the_note_is_refused(client, db, seed):
    _GRANTS.clear()
    got = client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments", json={"body": "nope"})
    assert got.status_code == 403, got.text
    assert db.query(ConversationTicketComment).count() == 0


def test_an_unknown_contact_ref_is_404(client, seed):
    got = client.post(f"{INBOX_BASE}/zzt-no-such-contact/comments", json={"body": "hello"})
    assert got.status_code == 404, got.text


def test_an_empty_note_is_refused(client, db, seed):
    got = client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments", json={"body": "   "})
    assert got.status_code == 422, got.text
    assert db.query(ConversationTicketComment).count() == 0


def test_an_unknown_mentioned_user_is_refused(client, db, seed):
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/comments",
        json={"body": "@Ghost hello", "mentioned_user_ids": [str(uuid.uuid4())]},
    )
    # 400 VALIDATION_ERROR (the app's own flow): the id is well formed, it just
    # names nobody. Same answer the ticket-keyed route gives.
    assert got.status_code == 400, got.text
    assert db.query(ConversationTicketComment).count() == 0


# --------------------------------------------------------------------------- #
# Side effects: mention notify, Respond mirror + outbox                        #
# --------------------------------------------------------------------------- #


def test_a_mentioned_colleague_is_notified_with_a_link_to_the_inbox(client, db, seed):
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/comments",
        json={"body": "@ZZT Team Lead can you take this?", "mentioned_user_ids": [seed["lead_id"]]},
    )
    assert got.status_code == 201, got.text
    assert got.json()["mentioned_names"] == ["ZZT Team Lead"]

    note = (
        db.query(Notification)
        .filter(
            Notification.user_id == seed["lead_id"],
            Notification.type == "conversation_ticket_comment",
        )
        .one()
    )
    # No ticket owns this note, so the deep link is the inbox on this contact.
    assert "/sla-management/conversations" in (note.body or "")
    assert RESPOND_IO_ID in (note.body or "")
    assert note.source_entity_type == "respond_contacts"
    assert str(note.source_entity_id) == seed["contact_id"]
    channels = {
        d.channel
        for d in db.query(NotificationDelivery)
        .filter(NotificationDelivery.notification_id == note.id)
        .all()
    }
    assert "in_app" in channels
    # Email joined the lane with notify_email_on_mention (default on); the seeded
    # user leaves it at the default. WhatsApp has no twin for this event.
    assert "email" in channels
    assert "whatsapp" not in channels


def test_mentioning_yourself_notifies_nobody(client, db, seed):
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/comments",
        json={"body": "@ZZT Colleague noting this", "mentioned_user_ids": [seed["colleague_id"]]},
    )
    assert got.status_code == 201, got.text
    assert (
        db.query(Notification)
        .filter(Notification.type == "conversation_ticket_comment")
        .count()
        == 0
    )


def test_a_failing_notification_never_fails_the_note(client, db, seed, monkeypatch):
    """Post-commit side effects are best-effort: the note is already saved."""

    def _boom(*a, **k):
        raise RuntimeError("notification backend down")

    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.create_with_channel_preferences",
        _boom,
    )
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/comments",
        json={"body": "@ZZT Team Lead ping", "mentioned_user_ids": [seed["lead_id"]]},
    )
    assert got.status_code == 201, got.text
    assert db.query(ConversationTicketComment).count() == 1


def test_the_mirror_reaches_respond_and_writes_an_outbox_row(client, db, seed, respond):
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/comments",
        json={"body": "@ZZT Team Lead please look", "mentioned_user_ids": [seed["lead_id"]]},
    )
    assert got.status_code == 201, got.text
    # Mentioned user carries a real Respond mapping, so the mirror tokenises it.
    assert respond.comments == [(RESPOND_IO_ID, "{{@user.900321}} please look")]

    logs = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "conversation_ticket_comments")
        .all()
    )
    assert len(logs) == 1
    assert str(logs[0].business_id) == got.json()["id"]
    assert db.query(ConversationTicketComment).one().respond_mirrored is True


def test_a_failed_mirror_still_writes_an_outbox_row_and_keeps_the_note(client, db, seed, respond):
    """Repo rule: every Respond call leaves an integration_log on success AND
    failure. Local dev runs with deliberately-wrong credentials."""
    respond.fail = True
    got = client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments", json={"body": "mirror will 401"})
    assert got.status_code == 201, got.text
    logs = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "conversation_ticket_comments")
        .all()
    )
    assert len(logs) == 1
    assert db.query(ConversationTicketComment).one().respond_mirrored is False


def test_a_note_never_reaches_the_contact(client, seed):
    """The stub raises on send_message, so a note that leaked onto a send path
    fails here."""
    assert (
        client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments", json={"body": "internal only"}).status_code
        == 201
    )
