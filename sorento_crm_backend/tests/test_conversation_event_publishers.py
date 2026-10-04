"""Who pokes the stream, and who deliberately does not.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-K1 (inbound message -> the open drawer, within seconds)
     AC-K3 (ticket created / clocks changed -> the pending-tasks widget)
     AC-K4 (a duplicated ingest must not produce a second poke)
     AC-F3 (form-SLA rows share the table and must stay untouched)
PLAN: documentation/plans/sla/PLAN-conversation-intervention-tickets.md (S4.2)

The publishers are post-commit side effects on paths that already work, so the
question each test asks is narrow: did exactly the right event go out, keyed to
the people who need to refetch, and did nothing go out where nothing changed.

Run:
    venv/bin/pytest tests/test_conversation_event_publishers.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team, TeamMember
from app.models.sla import ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services import conversation_event_bus as bus
from app.services.sla_service import ConversationSLATrackingService
from tests._event_bus_fake import FakeEventTransport
from tests._pg_fixture import blank_session

PHONE = "+60100000071"
RESPOND_IO_ID = "10025990"
INGEST_URL = "/api/v1/external/chat-history/messages"


@pytest.fixture
def events():
    fake = FakeEventTransport()
    bus.set_transport(fake)
    try:
        yield fake
    finally:
        bus.set_transport(None)


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture
def client(db):
    from app.main import app
    from app.database import get_db as database_get_db
    from app.dependencies import get_db as dependencies_get_db, get_external_api_user
    from app.services.user_service import UserPermissionService

    app.dependency_overrides[database_get_db] = lambda: db
    app.dependency_overrides[dependencies_get_db] = lambda: db
    app.dependency_overrides[get_external_api_user] = lambda: {
        "id": "system",
        "auth_method": "api_key",
    }
    _orig = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        UserPermissionService.check_user_has_permission = _orig
        app.dependency_overrides.clear()


def _seed(db) -> dict:
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
            name="Live Thread Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    alice_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    db.add(User(id=alice_id, email="alice.k@test.com", name="Alice", respond_user_id="900401"))
    db.add(User(id=bob_id, email="bob.k@test.com", name="Bob", respond_user_id="900402"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="CS_AGENT_K", name="CS Agent K"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="Customer Service K - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="cs_general_k",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    for member in (alice_id, bob_id):
        db.add(TeamMember(id=str(uuid.uuid4()), team_id=team_id, user_id=member))
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "alice_id": alice_id,
        "bob_id": bob_id,
        "team_id": team_id,
        "agent_code": "CS_AGENT_K",
        "team_set_code": "cs_general_k",
    }


def _create_ticket(db, seed, *, source_message_id, assignee_id):
    return ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id=source_message_id,
            source_message_text="Please get a person to help me.",
        )
    )


def _of_type(events: FakeEventTransport, event_type: str) -> list[dict]:
    return [e for e in events.payloads() if e["type"] == event_type]


def _ingest_payload(**overrides) -> dict:
    payload = {
        "channel": "whatsapp",
        "contact_id": RESPOND_IO_ID,
        "phone_number": PHONE,
        "message": "Any update on my order?",
        "sent_at": 1780751906900,
        "type": "incoming",
        "message_id": "1780751906900000",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Chat ingest (AC-K1, AC-K4)                                                    #
# --------------------------------------------------------------------------- #


def test_an_ingested_message_pokes_the_contacts_thread(client, events):
    response = client.post(INGEST_URL, json=_ingest_payload())
    assert response.status_code == 201, response.text

    pokes = _of_type(events, bus.EVENT_MESSAGE)
    assert len(pokes) == 1
    assert pokes[0]["contact_id"] == RESPOND_IO_ID
    assert pokes[0]["user_id"] is None
    assert "Any update on my order?" not in str(pokes[0]), (
        "the poke must never carry the message itself"
    )


def test_a_deduped_message_pokes_nobody(client, events):
    """AC-K4 / AC-J5: the second mirror lane resolves the row that already
    exists, so every subscriber was already told about it. Poking again would
    be a wasted refetch on every open drawer for that contact."""
    client.post(INGEST_URL, json=_ingest_payload())
    events.published.clear()

    second = client.post(INGEST_URL, json=_ingest_payload())

    assert second.json()["status"] == "duplicate"
    assert _of_type(events, bus.EVENT_MESSAGE) == []


def test_an_ingest_failure_pokes_nobody(client, events):
    """A rejected payload wrote no row; the thread did not change."""
    response = client.post(INGEST_URL, json=_ingest_payload(sent_at=0))

    assert response.status_code == 400
    assert events.published == []


# --------------------------------------------------------------------------- #
# Ticket lifecycle (AC-K3)                                                      #
# --------------------------------------------------------------------------- #


def test_a_new_ticket_pokes_its_assignee(db, events):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.k-1", assignee_id=seed["alice_id"])

    created = _of_type(events, bus.EVENT_TICKET_CREATED)
    assert len(created) == 1
    assert created[0]["user_id"] == seed["alice_id"]
    assert created[0]["contact_id"] == RESPOND_IO_ID
    assert created[0]["entity_id"] == str(ticket.id)


def test_an_idempotent_recreate_pokes_nobody(db, events):
    """AC-A2: the retry is a no-op read of the ticket already open - nothing
    changed, so nothing needs refetching."""
    seed = _seed(db)
    _create_ticket(db, seed, source_message_id="wamid.k-2", assignee_id=seed["alice_id"])
    events.published.clear()

    _create_ticket(db, seed, source_message_id="wamid.k-2", assignee_id=seed["alice_id"])

    assert events.published == []


def test_resolving_a_ticket_pokes_the_assignee_and_the_thread(db, events):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.k-3", assignee_id=seed["alice_id"])
    events.published.clear()

    ConversationSLATrackingService(db).update_tracking(
        str(ticket.id),
        ConversationSLATrackingUpdate(is_resolved=True, resolved_by=seed["alice_id"]),
    )

    updated = _of_type(events, bus.EVENT_TICKET_UPDATED)
    assert len(updated) == 1
    assert updated[0]["user_id"] == seed["alice_id"]
    assert updated[0]["contact_id"] == RESPOND_IO_ID
    assert updated[0]["entity_id"] == str(ticket.id)


def test_stamping_the_first_response_pokes_the_ticket(db, events):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.k-4", assignee_id=seed["alice_id"])
    events.published.clear()

    ConversationSLATrackingService(db).mark_ticket_responded(
        ticket, responded_by_user_id=seed["alice_id"]
    )

    assert len(_of_type(events, bus.EVENT_TICKET_UPDATED)) == 1


def test_a_second_send_on_an_answered_ticket_pokes_nobody(db, events):
    """Only the FIRST reply stops the response clock; a later send changes no
    clock, so the widget has nothing to refetch."""
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.k-5", assignee_id=seed["alice_id"])
    service = ConversationSLATrackingService(db)
    service.mark_ticket_responded(ticket, responded_by_user_id=seed["alice_id"])
    events.published.clear()

    service.mark_ticket_responded(ticket, responded_by_user_id=seed["alice_id"])

    assert events.published == []


def test_a_reassign_pokes_both_the_old_and_the_new_owner(db, events):
    """Two worklists changed: one row left Alice's pending list and arrived in
    Bob's. Poking only the new owner leaves the old one showing a task they no
    longer hold until the slow poll catches up."""
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.k-6", assignee_id=seed["alice_id"])
    events.published.clear()

    ConversationSLATrackingService(db).reassign(
        str(ticket.id), seed["alice_id"], seed["bob_id"]
    )

    poked_users = {e["user_id"] for e in _of_type(events, bus.EVENT_TICKET_UPDATED)}
    assert poked_users == {seed["alice_id"], seed["bob_id"]}


def test_an_escalation_pokes_both_tiers(db, events):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.k-7", assignee_id=seed["alice_id"])
    events.published.clear()

    ConversationSLATrackingService(db).escalate_tracking(
        seed["contact_id"],
        seed["policy_id"],
        current_tier=2,
        escalation_reason="Response SLA breached.",
        assigned_to_id=seed["bob_id"],
        tracking_id=str(ticket.id),
    )

    poked_users = {e["user_id"] for e in _of_type(events, bus.EVENT_TICKET_UPDATED)}
    assert poked_users == {seed["alice_id"], seed["bob_id"]}


def test_extending_a_deadline_pokes_the_assignee(db, events):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.k-8", assignee_id=seed["alice_id"])
    events.published.clear()

    ConversationSLATrackingService(db).extend_tracking(
        str(ticket.id), seed["alice_id"], days=2, reason="Waiting on the supplier."
    )

    updated = _of_type(events, bus.EVENT_TICKET_UPDATED)
    assert len(updated) == 1
    assert updated[0]["user_id"] == seed["alice_id"]


# --------------------------------------------------------------------------- #
# Family isolation (AC-F3)                                                      #
# --------------------------------------------------------------------------- #


def test_a_form_sla_row_never_pokes_the_conversation_stream(db, events):
    """Form SLA shares this table and is discriminated only by
    source_entity_type (conversation_tracking_scope). Its stage rows belong to
    the form detail pages, not to the ticket drawer or the conversation
    worklist, so they must not reach this channel at all."""
    seed = _seed(db)
    form_row = ConversationSLATracking(
        id=str(uuid.uuid4()),
        respond_contact_id=seed["contact_id"],
        policy_id=seed["policy_id"],
        assigned_to_id=seed["alice_id"],
        current_tier=1,
        due_at=datetime.now(timezone.utc) + timedelta(hours=4),
        due_at_resolution=datetime.now(timezone.utc) + timedelta(hours=24),
        source_entity_type="complaint",
        source_entity_id=str(uuid.uuid4()),
    )
    db.add(form_row)
    db.commit()
    events.published.clear()

    ConversationSLATrackingService(db).update_tracking(
        str(form_row.id),
        ConversationSLATrackingUpdate(is_resolved=True, resolved_by=seed["alice_id"]),
    )

    assert events.published == []
