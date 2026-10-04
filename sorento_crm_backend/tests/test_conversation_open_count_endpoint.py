"""AC-I2: GET /api/v1/external/conversation-sla-tracking/open-count.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-I2 (always 200, never 404; open conversation-scope rows only)

n8n gates the customer-facing "conversation closed and resolved" WhatsApp message
on this count. Two failure modes are unacceptable there and both are pinned below:
 - a 404-as-data (the sibling GET-by-contact 404s on an unknown contact / no
    tracking), which n8n would have to interpret, and which reads identically to
    a routing typo;
 - a sort-order-dependent read (the sibling returns the "preferred" single row),
    which cannot answer "are ANY still open" for a contact holding several.
Either one tells a contact their still-open enquiry is resolved.

Run:
    venv/bin/pytest tests/test_conversation_open_count_endpoint.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE = "+60100000003"
URL = "/api/v1/external/conversation-sla-tracking/open-count"


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
            name="Farah Idris",
            respond_io_id="10025902",
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="count1@test.com", name="Agent Count", respond_user_id="900201"))
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
def client(db):
    from app.main import app
    from app.database import get_db as database_get_db
    from app.dependencies import get_db as dependencies_get_db, get_external_api_user
    from app.api.v1.external.permissions import require_external_permission

    def _api_user():
        return {"id": "system", "auth_method": "api_key"}

    app.dependency_overrides[database_get_db] = lambda: db
    app.dependency_overrides[dependencies_get_db] = lambda: db
    app.dependency_overrides[get_external_api_user] = _api_user
    # The prefix-level RBAC guard is a closure per mount; override the principal
    # it resolves and stub the permission service so this suite tests the
    # endpoint contract, not the grant sweep (covered by the external-permission
    # completeness suite).
    from app.services.user_service import UserPermissionService

    _orig = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        UserPermissionService.check_user_has_permission = _orig
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Always 200 - the whole point of the endpoint                                 #
# --------------------------------------------------------------------------- #


def test_unknown_contact_is_200_zero_not_404(client, db):
    _seed(db)
    resp = client.get(URL, params={"phone_number": "+60100000000"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"contact_id": None, "open_count": 0}


def test_known_contact_with_no_tickets_is_200_zero(client, db):
    seed = _seed(db)
    resp = client.get(URL, params={"contact_id": "10025902"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"contact_id": seed["contact_id"], "open_count": 0}


def test_all_tickets_resolved_is_200_zero(client, db):
    seed = _seed(db)
    ticket = _create_ticket(db, seed, source_message_id="wamid.count-1")
    ConversationSLATrackingService(db).update_tracking(
        str(ticket.id), ConversationSLATrackingUpdate(is_resolved=True)
    )

    resp = client.get(URL, params={"contact_id": "10025902"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"contact_id": seed["contact_id"], "open_count": 0}


def test_counts_every_open_sibling_not_just_the_preferred_one(client, db):
    seed = _seed(db)
    _create_ticket(db, seed, source_message_id="wamid.count-2")
    _create_ticket(db, seed, source_message_id="wamid.count-3")
    _create_ticket(db, seed, source_message_id="wamid.count-4")

    resp = client.get(URL, params={"phone_number": PHONE})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"contact_id": seed["contact_id"], "open_count": 3}


def test_resolving_one_sibling_leaves_the_rest_counted(client, db):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.count-5")
    _create_ticket(db, seed, source_message_id="wamid.count-6")
    ConversationSLATrackingService(db).update_tracking(
        str(t1.id), ConversationSLATrackingUpdate(is_resolved=True)
    )

    resp = client.get(URL, params={"phone_number": PHONE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["open_count"] == 1, (
        "n8n must not tell the contact their remaining enquiry is resolved"
    )


def test_form_sla_rows_are_excluded_from_the_count(client, db):
    """AC-F3: form-SLA stage rows share the table and must never be counted."""
    seed = _seed(db)
    _create_ticket(db, seed, source_message_id="wamid.count-7")
    db.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            respond_contact_id=seed["contact_id"],
            policy_id=seed["policy_id"],
            current_tier=1,
            due_at=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            is_responded=False,
            is_resolved=False,
            source_entity_type="complaint",
            source_entity_id=str(uuid.uuid4()),
        )
    )
    db.commit()

    resp = client.get(URL, params={"phone_number": PHONE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["open_count"] == 1, (
        "conversation_tracking_scope must keep the form-SLA family out of the count"
    )


# --------------------------------------------------------------------------- #
# Identifier resolution mirrors the sibling GET                                #
# --------------------------------------------------------------------------- #


def test_resolves_by_crm_respond_contacts_id(client, db):
    seed = _seed(db)
    _create_ticket(db, seed, source_message_id="wamid.count-8")

    resp = client.get(URL, params={"contact_id": seed["contact_id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"contact_id": seed["contact_id"], "open_count": 1}


def test_contact_phone_alias_is_accepted(client, db):
    seed = _seed(db)
    _create_ticket(db, seed, source_message_id="wamid.count-9")

    resp = client.get(URL, params={"contact_phone": PHONE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["open_count"] == 1


def test_no_identifier_is_a_400(client, db):
    _seed(db)
    resp = client.get(URL)
    assert resp.status_code == 400, resp.text


def test_conflicting_identifiers_are_a_400(client, db):
    seed = _seed(db)
    db.add(
        RespondContact(
            id=str(uuid.uuid4()),
            phone_number="+60100000073",
            name="Other Contact",
            respond_io_id="10025903",
            session_vars={},
        )
    )
    db.commit()

    resp = client.get(URL, params={"contact_id": "10025903", "phone_number": PHONE})
    assert resp.status_code == 400, resp.text
    assert seed["contact_id"] not in resp.text
