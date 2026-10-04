"""AC-I5: POST .../conversation-sla-tracking/integration/escalate takes an
optional `tracking_id` that wins over contact+policy resolution.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-I5

`GET /integration/due-escalations` already returns one item PER ROW, but the
escalate body was contact-scoped: the server re-resolved "the" open tracking for
the contact (a most-recent-open pick). Under multi-open intervention tickets the
scheduler could therefore escalate a DIFFERENT sibling than the one that
breached - the breaching ticket keeps sitting at tier 1 while an unrelated,
in-SLA sibling gets bumped and its assignee notified.

Contact+policy resolution stays as the back-compat path for any caller still on
the old contract.

Run:
    venv/bin/pytest tests/test_conversation_escalate_by_tracking_id.py -q
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
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE = "+60100000069"
URL = "/api/v1/sla-management/conversation-sla-tracking/integration/escalate"


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
    for tier_level in (1, 2, 3):
        db.add(
            SLAPolicyTier(
                id=str(uuid.uuid4()),
                policy_id=policy_id,
                tier_level=tier_level,
                tier_name=f"Tier {tier_level}",
                response_hours=4,
                resolution_hours=24,
            )
        )
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            phone_number=PHONE,
            name="Hanif Latif",
            respond_io_id="10025906",
            session_vars={},
        )
    )
    tier1_user = str(uuid.uuid4())
    tier2_user = str(uuid.uuid4())
    db.add(User(id=tier1_user, email="t1@test.com", name="Tier One", respond_user_id="900401"))
    db.add(User(id=tier2_user, email="t2@test.com", name="Tier Two", respond_user_id="900402"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="CS_AGENT", name="CS Agent"))
    tier1_team = str(uuid.uuid4())
    tier2_team = str(uuid.uuid4())
    db.add(Team(id=tier1_team, name="Customer Service - Tier 1"))
    db.add(Team(id=tier2_team, name="Customer Service - Tier 2"))
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=tier1_team, user_id=tier1_user))
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=tier2_team, user_id=tier2_user))
    for tier, team in ((1, tier1_team), (2, tier2_team)):
        db.add(
            AgentTeam(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                code="cs_general",
                team_id=team,
                tier=tier,
                policy_id=policy_id,
            )
        )
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "tier1_user": tier1_user,
        "tier2_user": tier2_user,
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
            assigned_to_id=seed["tier1_user"],
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


def _order_by_creation(db, older, newer):
    """Pin created_at apart.

    Both rows are inserted inside one test transaction, so the ``now()`` server
    default gives them the SAME timestamp and the "most recent open" ordering
    that the contact-scoped path relies on becomes arbitrary. Without this the
    test can pass by luck and prove nothing.
    """
    base = datetime.now(timezone.utc)
    older.created_at = base - timedelta(hours=3)
    newer.created_at = base - timedelta(hours=1)
    db.commit()


def _fresh(db, tracking_id):
    db.expire_all()
    return (
        db.query(ConversationSLATracking)
        .filter(ConversationSLATracking.id == tracking_id)
        .one()
    )


def test_tracking_id_escalates_that_exact_ticket_not_the_most_recent(client, db):
    """The breaching ticket is the OLDER one; the contact-scoped path would pick
    the newer sibling."""
    seed = _seed(db)
    breaching = _create_ticket(db, seed, source_message_id="wamid.esc-1")
    newer = _create_ticket(db, seed, source_message_id="wamid.esc-2")
    _order_by_creation(db, breaching, newer)
    # Make the older one genuinely overdue so the scenario is the scheduler's.
    breaching.due_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    resp = client.post(
        URL,
        json={
            "respond_contact_id": "10025906",
            "policy_id": seed["policy_id"],
            "tracking_id": str(breaching.id),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["escalated"] is True
    assert body["tracking_id"] == str(breaching.id)
    assert body["from_tier"] == 1 and body["to_tier"] == 2

    assert _fresh(db, breaching.id).current_tier == 2
    assert _fresh(db, newer.id).current_tier == 1, (
        "the sibling that did not breach must stay where it was"
    )


def test_tracking_id_wins_over_a_contradicting_policy_id(client, db):
    seed = _seed(db)
    target = _create_ticket(db, seed, source_message_id="wamid.esc-3")
    newer = _create_ticket(db, seed, source_message_id="wamid.esc-4")
    _order_by_creation(db, target, newer)
    other_policy = str(uuid.uuid4())
    db.add(SLAPolicy(id=other_policy, code="WAREHOUSE", name="Warehouse"))
    db.commit()

    resp = client.post(
        URL,
        json={
            "respond_contact_id": "10025906",
            "policy_id": other_policy,
            "tracking_id": str(target.id),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tracking_id"] == str(target.id)
    assert _fresh(db, target.id).current_tier == 2


def test_contact_and_policy_resolution_still_works_without_tracking_id(client, db):
    """Back-compat: the old contract keeps its documented most-recent-open pick."""
    seed = _seed(db)
    older = _create_ticket(db, seed, source_message_id="wamid.esc-5")
    newer = _create_ticket(db, seed, source_message_id="wamid.esc-6")
    _order_by_creation(db, older, newer)

    resp = client.post(
        URL, json={"respond_contact_id": "10025906", "policy_id": seed["policy_id"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tracking_id"] == str(newer.id)


def test_unknown_tracking_id_is_a_404(client, db):
    seed = _seed(db)
    _create_ticket(db, seed, source_message_id="wamid.esc-7")

    resp = client.post(
        URL,
        json={
            "respond_contact_id": "10025906",
            "policy_id": seed["policy_id"],
            "tracking_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404, resp.text


def test_tracking_id_for_another_contact_is_rejected(client, db):
    """A mis-paired body must never silently escalate someone else's ticket."""
    seed = _seed(db)
    mine = _create_ticket(db, seed, source_message_id="wamid.esc-8")
    other_contact = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=other_contact,
            phone_number="+60100000070",
            name="Other",
            respond_io_id="10025907",
            session_vars={},
        )
    )
    db.commit()

    resp = client.post(
        URL,
        json={
            "respond_contact_id": "10025907",
            "policy_id": seed["policy_id"],
            "tracking_id": str(mine.id),
        },
    )
    assert resp.status_code in (400, 404), resp.text
    assert _fresh(db, mine.id).current_tier == 1


def test_resolved_ticket_addressed_by_tracking_id_is_refused(client, db):
    from app.schemas.sla import ConversationSLATrackingUpdate

    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.esc-9")
    ConversationSLATrackingService(db).update_tracking(
        str(ticket.id), ConversationSLATrackingUpdate(is_resolved=True)
    )

    resp = client.post(
        URL,
        json={
            "respond_contact_id": "10025906",
            "policy_id": seed["policy_id"],
            "tracking_id": str(ticket.id),
        },
    )
    assert resp.status_code == 400, resp.text
