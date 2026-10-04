"""The close-convo webhook must not answer n8n's own resolve (UAC AC-M3 hardening).

The loop, end to end:

    contact / agent closes the conversation in Respond
      -> n8n's respond-close-convo lane resolves the ticket via
         PUT /conversation-sla-tracking/{id} with the API-key principal
      -> update_tracking sees "resolved in this request" and fires the CRM's
         OWN respond-close-convo webhook back at n8n
      -> n8n sends the customer a SECOND closing message.

`_notify_close_convo_webhook_best_effort` was principal-agnostic, so the CRM
could not tell "a human pressed Resolve in the widget" from "n8n told us it
already closed this". The fix is at the source: the resolve carries its origin,
and the webhook fires only for a USER-origin resolve. An API-key resolve
originates from n8n, which already knows.

The RQ Respond-close job is deliberately UNCHANGED: it is the transport tidy-up
(mark the Respond conversation closed) and is idempotent, not a message to the
customer.

Run:
    venv/bin/pytest tests/test_close_convo_webhook_origin.py -q
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.integration import IntegrationLog
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60100000002"
RESPOND_IO_ID = "10025601"
BASE = "/api/v1/sla-management/conversation-sla-tracking"
CLOSE_WEBHOOK_URL = "https://n8n.test/webhook/respond-close-convo"

_ACTOR: dict = {"id": None, "name": "Test Actor", "auth_method": "jwt"}


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _no_webhook_http():
    with patch("app.services.crm_close_convo_webhook.threading"):
        yield


@pytest.fixture(autouse=True)
def _webhook_config(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "n8n_crm_webhook_secret", "zzt-secret", raising=False)
    monkeypatch.setenv("N8N_CRM_WEBHOOK_SECRET", "zzt-secret")
    monkeypatch.setattr(
        settings, "n8n_close_convo_webhook_url", CLOSE_WEBHOOK_URL, raising=False
    )
    monkeypatch.setenv("N8N_CLOSE_CONVO_WEBHOOK_URL", CLOSE_WEBHOOK_URL)


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


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


def _as_user(user_id: str) -> None:
    _ACTOR.update({"id": user_id, "auth_method": "jwt"})


def _as_api_key(user_id: str) -> None:
    """The n8n principal: an API key acting as a real user (act-as)."""
    _ACTOR.update({"id": user_id, "auth_method": "api_key"})


@pytest.fixture
def seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=f"ZZT-{uuid.uuid4().hex[:6]}", name="ZZT Close Origin"))
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
            name="ZZT Close Origin Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(
        User(
            id=assignee_id,
            email=f"zzt-co-{assignee_id[:8]}@test.com",
            name="ZZT Agent",
            respond_user_id="900011",
        )
    )
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_CO_AGENT", name="ZZT CO Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT CO Team - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_co_set",
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
        "agent_code": "ZZT_CO_AGENT",
        "team_set_code": "zzt_co_set",
    }


def _ticket(db, seed, *, source_message_id):
    return ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            assigned_to_id=seed["assignee_id"],
            contact_phone_number=PHONE,
            source_message_id=source_message_id,
            source_message_text="Please connect me to a person.",
        )
    )


def _close_logs(db):
    return (
        db.query(IntegrationLog)
        .filter(IntegrationLog.integration_channel == "n8n_crm_close_convo")
        .all()
    )


# --------------------------------------------------------------------------- #


def test_a_user_resolve_of_the_last_open_ticket_fires_the_close_webhook(client, db, seed):
    tracking = _ticket(db, seed, source_message_id="wamid.co-1")
    _as_user(seed["assignee_id"])

    got = client.post(f"{BASE}/{tracking.id}/resolve")
    assert got.status_code == 200, got.text
    assert len(_close_logs(db)) == 1


def test_an_api_key_resolve_does_not_fire_the_close_webhook(client, db, seed):
    """n8n resolving after a Respond-side close must not be answered with a
    close instruction - that is the loop."""
    tracking = _ticket(db, seed, source_message_id="wamid.co-2")
    _as_api_key(seed["assignee_id"])

    got = client.put(f"{BASE}/{tracking.id}", json={"is_resolved": True})
    assert got.status_code == 200, got.text
    db.expire_all()
    assert ConversationSLATrackingService(db).get_tracking(str(tracking.id)).is_resolved is True
    assert _close_logs(db) == [], "an API-key resolve must not fire the close webhook"


def test_a_user_principal_put_resolve_still_fires_the_close_webhook(client, db, seed):
    """The gate is the PRINCIPAL, not the route: a human resolving through PUT
    (an admin tool, a script with a real login) is still a CRM-origin resolve."""
    tracking = _ticket(db, seed, source_message_id="wamid.co-3")
    _as_user(seed["assignee_id"])

    got = client.put(f"{BASE}/{tracking.id}", json={"is_resolved": True})
    assert got.status_code == 200, got.text
    assert len(_close_logs(db)) == 1


def test_the_integration_route_never_fires_the_close_webhook(client, db, seed):
    """`/integration/{id}` has no auth dependency at all - it IS the n8n lane."""
    tracking = _ticket(db, seed, source_message_id="wamid.co-4")
    _as_api_key(seed["assignee_id"])

    got = client.put(f"{BASE}/integration/{tracking.id}", json={"is_resolved": True})
    assert got.status_code == 200, got.text
    assert _close_logs(db) == []


def test_a_user_resolve_with_an_open_sibling_still_fires_nothing(client, db, seed):
    """Unchanged AC-M3 gate: the conversation is not over while a sibling
    enquiry is open."""
    tracking = _ticket(db, seed, source_message_id="wamid.co-5")
    _ticket(db, seed, source_message_id="wamid.co-6")
    _as_user(seed["assignee_id"])

    got = client.post(f"{BASE}/{tracking.id}/resolve")
    assert got.status_code == 200, got.text
    assert _close_logs(db) == []


def test_the_respond_close_job_is_still_enqueued_on_an_api_key_resolve(client, db, seed, monkeypatch):
    """The transport tidy-up is deliberately unchanged: it marks the Respond
    conversation closed and is idempotent, it does not message the customer."""
    calls: list[tuple] = []
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: calls.append((a, k)))

    tracking = _ticket(db, seed, source_message_id="wamid.co-7")
    _as_api_key(seed["assignee_id"])
    got = client.put(f"{BASE}/{tracking.id}", json={"is_resolved": True})
    assert got.status_code == 200, got.text
    assert calls, "the Respond close job must still be enqueued"
    assert _close_logs(db) == []
