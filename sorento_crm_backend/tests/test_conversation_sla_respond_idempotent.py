"""AC-I3: setting is_responded on an ALREADY-responded ticket is idempotent.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-I3 (already responded -> 200 + `already_responded` marker, clocks untouched,
            NOT a 400)

Why this matters (production evidence): resolve has been idempotent for a while
(`_already_resolved` short-circuits and the route reports `already_resolved: true`),
but respond raised `handle_validation_error("Conversation is already responded.")`.
That asymmetry produced 53 refusals across 19 contacts on live data, one contact
hit 17 times. Under multi-open tickets the 400 is worse than noise: it aborts the
n8n Respond-app-reply fallback mid-loop, so the genuinely unanswered sibling never
gets stamped and breaches while a human is actively answering the contact.

Run:
    venv/bin/pytest tests/test_conversation_sla_respond_idempotent.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import ConversationSLAEventLog, ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE = "+60100000004"
BASE = "/api/v1/sla-management/conversation-sla-tracking"


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


def _seed(db):
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
            name="Nurul Hakim",
            respond_io_id="10025901",
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="idem1@test.com", name="Agent Idem", respond_user_id="900101"))
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
        "contact_id": contact_id,
        "assignee_id": assignee_id,
        "agent_code": "CS_AGENT",
        "team_set_code": "cs_general",
    }


def _create_ticket(db, seed, *, source_message_id):
    service = ConversationSLATrackingService(db)
    return service.create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            assigned_to_id=seed["assignee_id"],
            contact_phone_number=PHONE,
            source_message_id=source_message_id,
            source_message_text="Yes, please connect me to a person.",
        )
    )


@pytest.fixture
def client(db, monkeypatch):
    from app.main import app
    from app.database import get_db as database_get_db
    from app.dependencies import (
        get_current_user,
        get_current_user_or_api_key,
        get_db as dependencies_get_db,
    )
    from app.services.integration_service import IntegrationLogService

    def _system_user():
        return {"id": "system", "auth_method": "api_key"}

    # Same pre-existing choke point the sibling suites stub: create_integration_log
    # json.dumps()es the request payload with no default=str, so any payload holding
    # a real datetime 500s regardless of the behaviour under test.
    monkeypatch.setattr(
        IntegrationLogService, "create_integration_log", lambda self, *a, **k: None
    )

    app.dependency_overrides[database_get_db] = lambda: db
    app.dependency_overrides[dependencies_get_db] = lambda: db
    app.dependency_overrides[get_current_user_or_api_key] = _system_user
    app.dependency_overrides[get_current_user] = _system_user
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _response_event_logs(db, tracking_id):
    return (
        db.query(ConversationSLAEventLog)
        .filter(
            ConversationSLAEventLog.sla_tracking_id == str(tracking_id),
            ConversationSLAEventLog.event_type == "response",
        )
        .all()
    )


# --------------------------------------------------------------------------- #
# Service level                                                                #
# --------------------------------------------------------------------------- #


def test_service_second_respond_returns_marker_instead_of_raising(db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.idem-1")
    service = ConversationSLATrackingService(db)

    first = service.update_tracking(
        str(ticket.id),
        ConversationSLATrackingUpdate(is_responded=True, responded_by=seed["assignee_id"]),
    )
    assert first.is_responded is True
    assert getattr(first, "_already_responded", False) is False
    first_responded_at = first.responded_at
    first_response_time = first.response_time
    first_responded_by = str(first.responded_by)

    second = service.update_tracking(
        str(ticket.id),
        ConversationSLATrackingUpdate(is_responded=True, responded_by=seed["assignee_id"]),
    )

    assert getattr(second, "_already_responded", False) is True, (
        "AC-I3: the second respond must short-circuit with a marker, not raise 400"
    )
    db.expire_all()
    fresh = db.query(ConversationSLATracking).filter(ConversationSLATracking.id == ticket.id).one()
    assert fresh.responded_at == first_responded_at, "clocks must be untouched"
    assert fresh.response_time == first_response_time
    assert str(fresh.responded_by) == first_responded_by


def test_service_second_respond_still_applies_non_responded_fields(db):
    """Mirrors FINDING 5: only the responded-family fields are dropped."""
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.idem-2")
    service = ConversationSLATrackingService(db)
    service.update_tracking(
        str(ticket.id),
        ConversationSLATrackingUpdate(is_responded=True, responded_by=seed["assignee_id"]),
    )

    second = service.update_tracking(
        str(ticket.id),
        ConversationSLATrackingUpdate(
            is_responded=True,
            responded_by=seed["assignee_id"],
            escalation_reason="carried alongside a duplicate respond",
        ),
    )
    assert getattr(second, "_already_responded", False) is True

    db.expire_all()
    fresh = db.query(ConversationSLATracking).filter(ConversationSLATracking.id == ticket.id).one()
    assert fresh.escalation_reason == "carried alongside a duplicate respond"


def test_already_responded_does_not_block_resolving_in_the_same_call(db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.idem-3")
    service = ConversationSLATrackingService(db)
    service.update_tracking(
        str(ticket.id),
        ConversationSLATrackingUpdate(is_responded=True, responded_by=seed["assignee_id"]),
    )

    service.update_tracking(
        str(ticket.id),
        ConversationSLATrackingUpdate(
            is_responded=True, responded_by=seed["assignee_id"], is_resolved=True
        ),
    )

    db.expire_all()
    fresh = db.query(ConversationSLATracking).filter(ConversationSLATracking.id == ticket.id).one()
    assert fresh.is_resolved is True, (
        "the duplicate respond must not swallow the resolve riding in the same payload"
    )


# --------------------------------------------------------------------------- #
# Route level                                                                  #
# --------------------------------------------------------------------------- #


def test_put_route_reports_already_responded_with_200(client, db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.idem-4")

    first = client.put(
        f"{BASE}/{ticket.id}",
        json={"is_responded": True, "responded_by": seed["assignee_id"]},
    )
    assert first.status_code == 200, first.text
    assert first.json()["already_responded"] is False

    second = client.put(
        f"{BASE}/{ticket.id}",
        json={"is_responded": True, "responded_by": seed["assignee_id"]},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["already_responded"] is True
    assert body["updated_in_request"] is False, (
        "the whole payload was the duplicate responded-family fields - nothing applied"
    )
    assert body["responded_at"] == first.json()["responded_at"], "clocks untouched"


def test_integration_route_second_respond_is_200_and_writes_no_second_event_log(client, db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.idem-5")

    first = client.put(
        f"{BASE}/integration/{ticket.id}",
        json={"is_responded": True, "responded_at": "2026-08-13T03:00:00Z"},
    )
    assert first.status_code == 200, first.text
    assert len(_response_event_logs(db, ticket.id)) == 1
    db.expire_all()
    responded_at_after_first = (
        db.query(ConversationSLATracking)
        .filter(ConversationSLATracking.id == ticket.id)
        .one()
        .responded_at
    )

    second = client.put(
        f"{BASE}/integration/{ticket.id}",
        json={"is_responded": True, "responded_at": "2026-08-13T05:00:00Z"},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["already_responded"] is True
    assert body["updated"] is False

    assert len(_response_event_logs(db, ticket.id)) == 1, (
        "a skipped duplicate respond must not add a second 'response' event log"
    )
    db.expire_all()
    fresh = db.query(ConversationSLATracking).filter(ConversationSLATracking.id == ticket.id).one()
    assert fresh.responded_at == responded_at_after_first, (
        "the later responded_at must NOT overwrite the first response clock"
    )
