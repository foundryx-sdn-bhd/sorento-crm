"""AC-I4: POST /api/v1/external/conversation-sla-tracking/agent-replied.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-I4 (server owns the REVISED AC-E3 rule, always 200)
     AC-E3 REVISED 2026-08-13 (keys on the CONTACT first, the replier second)

What this replaces (a LIVE defect that predates the intervention-ticket work):
the n8n `respond-send-user` workflow resolves rows in raw SQL with predicates
`policy_id = <arbitrary first sla_policies row>` AND `is_responded = false` AND
`assigned_to = <replying user>` and NO CONTACT PREDICATE, then PUTs once per
returned row. One reply to one contact therefore stamps every unanswered ticket
that agent owns across ALL contacts. On the dev snapshot one assignee holds 5
open unanswered rows across 5 distinct contacts.

The rule, applied server-side in one place:
  1. contact has exactly ONE open unanswered ticket -> stamp it, whoever replied
     (`responded_by` records the actual replier);
  2. 2+ open unanswered -> stamp only if the replier owns exactly one of them,
     otherwise change nothing (`skipped_reason: "ambiguous"`);
  3. zero open unanswered -> `"no_open_ticket"`.

Run:
    venv/bin/pytest tests/test_conversation_agent_replied_endpoint.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.integration import IntegrationLog
from app.models.sla import ConversationSLAEventLog, ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE = "+60100000067"
OTHER_PHONE = "+60100000068"
URL = "/api/v1/external/conversation-sla-tracking/agent-replied"


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
            name="Zaki Omar",
            respond_io_id="10025904",
            session_vars={},
        )
    )
    other_contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=other_contact_id,
            phone_number=OTHER_PHONE,
            name="Second Contact",
            respond_io_id="10025905",
            session_vars={},
        )
    )
    alice_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    db.add(User(id=alice_id, email="alice@test.com", name="Alice", respond_user_id="900301"))
    db.add(User(id=bob_id, email="bob@test.com", name="Bob", respond_user_id="900302"))
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
        "other_contact_id": other_contact_id,
        "alice_id": alice_id,
        "bob_id": bob_id,
        "agent_code": "CS_AGENT",
        "team_set_code": "cs_general",
    }


def _create_ticket(db, seed, *, source_message_id, assignee_id, phone=PHONE):
    service = ConversationSLATrackingService(db)
    return service.create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            assigned_to_id=assignee_id,
            contact_phone_number=phone,
            source_message_id=source_message_id,
            source_message_text="Yes, please connect me to a person.",
        )
    )


@pytest.fixture
def client(db):
    from app.main import app
    from app.database import get_db as database_get_db
    from app.dependencies import get_db as dependencies_get_db, get_external_api_user
    from app.services.user_service import UserPermissionService

    def _api_user():
        return {"id": "system", "auth_method": "api_key"}

    app.dependency_overrides[database_get_db] = lambda: db
    app.dependency_overrides[dependencies_get_db] = lambda: db
    app.dependency_overrides[get_external_api_user] = _api_user
    _orig = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        UserPermissionService.check_user_has_permission = _orig
        app.dependency_overrides.clear()


def _fresh(db, tracking_id):
    db.expire_all()
    return (
        db.query(ConversationSLATracking)
        .filter(ConversationSLATracking.id == tracking_id)
        .one()
    )


def _agent_replied_logs(db):
    return (
        db.query(IntegrationLog)
        .filter(IntegrationLog.integration_channel == "sla_agent_replied")
        .all()
    )


# --------------------------------------------------------------------------- #
# Rule 1: exactly ONE open unanswered ticket -> stamp it, whoever replied       #
# --------------------------------------------------------------------------- #


def test_single_open_ticket_is_stamped_even_when_someone_else_replied(client, db):
    """The revision's whole point: the clock measures whether the CONTACT got a
    human response, not whether the assigned person typed it. A ticket raised on
    an already-assigned Respond conversation is owned by the CRM round-robin pick
    (AC-E6) while the Respond conversation stays with someone else."""
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.ar-1", assignee_id=seed["alice_id"])

    resp = client.post(
        URL, json={"contact_id": "10025904", "replied_by": "900302"}  # Bob replied
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] is True
    assert body["tracking_id"] == str(ticket.id)
    assert body["skipped_reason"] is None
    assert body["open_ticket_count"] == 1

    fresh = _fresh(db, ticket.id)
    assert fresh.is_responded is True
    assert str(fresh.responded_by) == seed["bob_id"], (
        "responded_by records the ACTUAL replier, not the ticket's assignee"
    )
    assert str(fresh.assigned_to_id) == seed["alice_id"], "ownership is not reassigned"


def test_single_open_ticket_writes_a_response_event_log(client, db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.ar-2", assignee_id=seed["alice_id"])

    client.post(URL, json={"contact_id": "10025904", "replied_by": "alice@test.com"})

    logs = (
        db.query(ConversationSLAEventLog)
        .filter(
            ConversationSLAEventLog.sla_tracking_id == str(ticket.id),
            ConversationSLAEventLog.event_type == "response",
        )
        .all()
    )
    assert len(logs) == 1


def test_already_responded_sibling_does_not_make_the_lone_unanswered_ambiguous(client, db):
    seed = _seed(db)
    answered = _create_ticket(db, seed, source_message_id="wamid.ar-3", assignee_id=seed["alice_id"])
    ConversationSLATrackingService(db).update_tracking(
        str(answered.id),
        ConversationSLATrackingUpdate(is_responded=True, responded_by=seed["alice_id"]),
    )
    unanswered = _create_ticket(
        db, seed, source_message_id="wamid.ar-4", assignee_id=seed["bob_id"]
    )

    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] is True
    assert body["tracking_id"] == str(unanswered.id)
    assert body["open_ticket_count"] == 1, "only UNANSWERED open tickets are candidates"


# --------------------------------------------------------------------------- #
# Rule 2: 2+ open unanswered -> narrow by replier                              #
# --------------------------------------------------------------------------- #


def test_two_open_unanswered_replier_owns_exactly_one_stamps_that_one(client, db):
    seed = _seed(db)
    alice_ticket = _create_ticket(
        db, seed, source_message_id="wamid.ar-5", assignee_id=seed["alice_id"]
    )
    bob_ticket = _create_ticket(
        db, seed, source_message_id="wamid.ar-6", assignee_id=seed["bob_id"]
    )

    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900302"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] is True
    assert body["tracking_id"] == str(bob_ticket.id)
    assert body["skipped_reason"] is None
    assert body["open_ticket_count"] == 2

    assert _fresh(db, bob_ticket.id).is_responded is True
    assert _fresh(db, alice_ticket.id).is_responded is False, "the sibling is untouched"


def test_two_open_unanswered_both_owned_by_the_replier_is_ambiguous(client, db):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.ar-7", assignee_id=seed["alice_id"])
    t2 = _create_ticket(db, seed, source_message_id="wamid.ar-8", assignee_id=seed["alice_id"])

    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] is False
    assert body["tracking_id"] is None
    assert body["skipped_reason"] == "ambiguous"
    assert body["open_ticket_count"] == 2

    assert _fresh(db, t1.id).is_responded is False
    assert _fresh(db, t2.id).is_responded is False


def test_two_open_unanswered_replier_owns_none_is_ambiguous(client, db):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.ar-9", assignee_id=seed["alice_id"])
    t2 = _create_ticket(db, seed, source_message_id="wamid.ar-10", assignee_id=seed["alice_id"])
    # Bob replied but owns neither: no basis to pick, so change nothing.
    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900302"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] is False
    assert body["skipped_reason"] == "ambiguous"

    assert _fresh(db, t1.id).is_responded is False
    assert _fresh(db, t2.id).is_responded is False


# --------------------------------------------------------------------------- #
# Rule 3: zero open unanswered                                                 #
# --------------------------------------------------------------------------- #


def test_no_open_ticket_is_200_with_a_reason(client, db):
    _seed(db)
    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "matched": False,
        "tracking_id": None,
        "skipped_reason": "no_open_ticket",
        "open_ticket_count": 0,
    }


def test_unknown_contact_is_200_no_open_ticket_not_404(client, db):
    _seed(db)
    resp = client.post(URL, json={"contact_id": "+60100000000", "replied_by": "900301"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped_reason"] == "no_open_ticket"
    assert resp.json()["open_ticket_count"] == 0


def test_unknown_replier_is_200_not_a_400(client, db):
    """n8n must never see a 4xx here: a Respond user with no CRM account is a
    real, recurring state and it is not a reason to lose the reply signal."""
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.ar-11", assignee_id=seed["alice_id"])

    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "999999"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched"] is True, (
        "one open unanswered ticket is stamped regardless of who replied"
    )
    fresh = _fresh(db, ticket.id)
    assert fresh.is_responded is True
    assert str(fresh.responded_by) == seed["alice_id"], (
        "an unresolvable replier falls back to the ticket's own assignee"
    )


def test_only_resolved_tickets_is_no_open_ticket(client, db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.ar-12", assignee_id=seed["alice_id"])
    ConversationSLATrackingService(db).update_tracking(
        str(ticket.id), ConversationSLATrackingUpdate(is_resolved=True)
    )

    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped_reason"] == "no_open_ticket"


# --------------------------------------------------------------------------- #
# Blast radius: the contact predicate the raw-SQL path never had                #
# --------------------------------------------------------------------------- #


def test_other_contacts_tickets_for_the_same_agent_are_never_touched(client, db):
    """The defect being retired: one reply stamped every unanswered ticket the
    agent owned across ALL contacts."""
    seed = _seed(db)
    mine = _create_ticket(db, seed, source_message_id="wamid.ar-13", assignee_id=seed["alice_id"])
    theirs = _create_ticket(
        db,
        seed,
        source_message_id="wamid.ar-14",
        assignee_id=seed["alice_id"],
        phone=OTHER_PHONE,
    )

    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tracking_id"] == str(mine.id)

    assert _fresh(db, mine.id).is_responded is True
    assert _fresh(db, theirs.id).is_responded is False, (
        "a reply to one contact must never stamp another contact's ticket"
    )


def test_form_sla_rows_are_never_candidates(client, db):
    from datetime import datetime, timezone

    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.ar-15", assignee_id=seed["alice_id"])
    form_row_id = str(uuid.uuid4())
    db.add(
        ConversationSLATracking(
            id=form_row_id,
            respond_contact_id=seed["contact_id"],
            policy_id=seed["policy_id"],
            assigned_to_id=seed["alice_id"],
            current_tier=1,
            due_at=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            is_responded=False,
            is_resolved=False,
            source_entity_type="complaint",
            source_entity_id=str(uuid.uuid4()),
        )
    )
    db.commit()

    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["open_ticket_count"] == 1, "the form-SLA row is not a candidate"
    assert resp.json()["tracking_id"] == str(ticket.id)
    assert _fresh(db, form_row_id).is_responded is False


# --------------------------------------------------------------------------- #
# Idempotency (AC-I3) and the integration_log trail                            #
# --------------------------------------------------------------------------- #


def test_replaying_the_same_reply_is_idempotent(client, db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.ar-16", assignee_id=seed["alice_id"])

    first = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert first.status_code == 200, first.text
    responded_at = _fresh(db, ticket.id).responded_at

    second = client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})
    assert second.status_code == 200, second.text
    assert second.json()["skipped_reason"] == "no_open_ticket", (
        "the ticket is answered now, so there is nothing unanswered left to stamp"
    )
    assert _fresh(db, ticket.id).responded_at == responded_at, "clocks untouched"


def test_every_outcome_writes_an_integration_log_including_the_skips(client, db):
    """This endpoint replaces an n8n raw-SQL path whose failures were visible as
    400s. That signal must not disappear when the call becomes an always-200."""
    seed = _seed(db)
    _create_ticket(db, seed, source_message_id="wamid.ar-17", assignee_id=seed["alice_id"])
    _create_ticket(db, seed, source_message_id="wamid.ar-18", assignee_id=seed["alice_id"])

    client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})  # ambiguous
    client.post(URL, json={"contact_id": "10025905", "replied_by": "900301"})  # no_open_ticket

    logs = _agent_replied_logs(db)
    assert len(logs) == 2, "a skipped outcome is still an outcome worth logging"
    statuses = sorted((log.status or "") for log in logs)
    assert statuses == ["skipped_ambiguous", "skipped_no_open_ticket"]


def test_matched_outcome_writes_a_success_integration_log(client, db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.ar-19", assignee_id=seed["alice_id"])

    client.post(URL, json={"contact_id": "10025904", "replied_by": "900301"})

    logs = _agent_replied_logs(db)
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert str(logs[0].business_id) == str(ticket.id)


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


def test_missing_contact_id_is_a_422(client, db):
    _seed(db)
    resp = client.post(URL, json={"replied_by": "900301"})
    assert resp.status_code == 422, resp.text


def test_blank_replied_by_is_a_422(client, db):
    _seed(db)
    resp = client.post(URL, json={"contact_id": "10025904", "replied_by": "   "})
    assert resp.status_code == 422, resp.text
