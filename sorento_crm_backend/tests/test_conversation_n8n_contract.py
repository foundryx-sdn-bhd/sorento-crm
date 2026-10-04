"""The n8n-facing contract surface for conversation intervention tickets.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-I1 (`in_working_hours` is present on EVERY create response)
     AC-I2 (open-count is always 200)

These are shape tests, deliberately separate from the behaviour suites. Both
endpoints feed n8n branch conditions, and n8n's strict type validation coerces
an ABSENT key to `false` and routes on silently:

 - drop `in_working_hours` from a create response and every in-hours contact is
    told "we are outside working hours", with nothing red anywhere - no error, no
    failed integration_log, no alert. The n8n side fails loudly on a missing key
    via a sentinel; this file is the CRM-side half of that contract.
 - answer open-count with a 404 (or omit `open_count`) and the "conversation
    closed and resolved" message goes out to a contact whose enquiry is open.

So the assertion is on the KEY and its TYPE, not only on the value: a null
`in_working_hours` is the same defect as a missing one once n8n coerces it.

Run:
    venv/bin/pytest tests/test_conversation_n8n_contract.py -q
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE = "+60100000072"
CREATE_URL = "/api/v1/sla-management/conversation-sla-tracking/integration"
OPEN_COUNT_URL = "/api/v1/external/conversation-sla-tracking/open-count"


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
            name="Iman Yusof",
            respond_io_id="10025908",
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="contract@test.com", name="Agent C", respond_user_id="900501"))
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


def _create_body(seed, *, source_message_id):
    return {
        "agent_code": seed["agent_code"],
        "team_set_code": seed["team_set_code"],
        "policy_id": seed["policy_id"],
        "assigned_to_id": seed["assignee_id"],
        "contact_phone_number": PHONE,
        "source_message_id": source_message_id,
        "source_message_text": "Yes, please connect me to a person.",
    }


@pytest.fixture
def client(db, monkeypatch):
    from app.main import app
    from app.database import get_db as database_get_db
    from app.dependencies import (
        get_current_user,
        get_current_user_or_api_key,
        get_db as dependencies_get_db,
        get_external_api_user,
    )
    from app.services.integration_service import IntegrationLogService
    from app.services.user_service import UserPermissionService

    def _system_user():
        return {"id": "system", "auth_method": "api_key"}

    monkeypatch.setattr(
        IntegrationLogService, "create_integration_log", lambda self, *a, **k: None
    )
    app.dependency_overrides[database_get_db] = lambda: db
    app.dependency_overrides[dependencies_get_db] = lambda: db
    app.dependency_overrides[get_current_user_or_api_key] = _system_user
    app.dependency_overrides[get_current_user] = _system_user
    app.dependency_overrides[get_external_api_user] = _system_user
    _orig = UserPermissionService.check_user_has_permission
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: True
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        UserPermissionService.check_user_has_permission = _orig
        app.dependency_overrides.clear()


def _assert_in_working_hours_contract(body):
    assert "in_working_hours" in body, (
        "AC-I1: n8n coerces an absent key to false and routes on silently - "
        "in-hours contacts would be told the office is closed, with nothing red"
    )
    assert isinstance(body["in_working_hours"], bool), (
        f"must be a real bool, got {body['in_working_hours']!r} - "
        "null coerces to false exactly like a missing key"
    )


# --------------------------------------------------------------------------- #
# AC-I1 - in_working_hours on every create response                            #
# --------------------------------------------------------------------------- #


def test_fresh_insert_carries_in_working_hours(client, db):
    seed = _seed(db)
    resp = client.post(CREATE_URL, json=_create_body(seed, source_message_id="wamid.ct-1"))
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["already_active"] is False
    _assert_in_working_hours_contract(body)


def test_idempotent_retry_carries_in_working_hours(client, db):
    """The retry path returns an EXISTING row, so the flag cannot come from a
    column - it is a per-request marker and has to be stamped here too."""
    seed = _seed(db)
    payload = _create_body(seed, source_message_id="wamid.ct-2")
    first = client.post(CREATE_URL, json=payload)
    assert first.status_code in (200, 201), first.text

    retry = client.post(CREATE_URL, json=payload)
    assert retry.status_code in (200, 201), retry.text
    body = retry.json()
    assert body["already_active"] is True
    assert body["tracking_id"] == first.json()["tracking_id"]
    _assert_in_working_hours_contract(body)


def test_out_of_hours_create_carries_in_working_hours_false(client, db, monkeypatch):
    """Pinned against a stubbed calendar rather than the wall clock: the suite
    must give the same answer at 03:00 as at 15:00."""
    from app.services.calendar_service import CalendarService

    monkeypatch.setattr(
        CalendarService,
        "next_working_window_open",
        lambda self, start_dt: start_dt + timedelta(hours=9),
    )

    seed = _seed(db)
    resp = client.post(CREATE_URL, json=_create_body(seed, source_message_id="wamid.ct-3"))
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    _assert_in_working_hours_contract(body)
    assert body["in_working_hours"] is False


def test_in_hours_create_reports_true(client, db, monkeypatch):
    from app.services.calendar_service import CalendarService

    monkeypatch.setattr(
        CalendarService, "next_working_window_open", lambda self, start_dt: start_dt
    )

    seed = _seed(db)
    resp = client.post(CREATE_URL, json=_create_body(seed, source_message_id="wamid.ct-4"))
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    _assert_in_working_hours_contract(body)
    assert body["in_working_hours"] is True


def test_out_of_hours_retry_also_reports_false(client, db, monkeypatch):
    """AC-A4 + AC-A2 together: n8n picks its auto-reply copy off the RETRY
    response as readily as the first one."""
    from app.services.calendar_service import CalendarService

    monkeypatch.setattr(
        CalendarService,
        "next_working_window_open",
        lambda self, start_dt: start_dt + timedelta(hours=9),
    )

    seed = _seed(db)
    payload = _create_body(seed, source_message_id="wamid.ct-5")
    client.post(CREATE_URL, json=payload)
    retry = client.post(CREATE_URL, json=payload)
    assert retry.status_code in (200, 201), retry.text
    body = retry.json()
    assert body["already_active"] is True
    _assert_in_working_hours_contract(body)
    assert body["in_working_hours"] is False


def test_create_response_keeps_the_other_n8n_read_fields(client, db):
    """AC-A2: the retry keeps the clock/assignee fields n8n reads, so the old
    flow keeps working through the deploy."""
    seed = _seed(db)
    payload = _create_body(seed, source_message_id="wamid.ct-6")
    client.post(CREATE_URL, json=payload)
    body = client.post(CREATE_URL, json=payload).json()

    for key in ("initiated_at", "due_at", "due_at_resolution", "assigned_to", "tracking_id"):
        assert key in body, f"n8n reads {key} on every create response"


# --------------------------------------------------------------------------- #
# AC-I2 - open-count shape                                                     #
# --------------------------------------------------------------------------- #


def _assert_open_count_contract(resp):
    assert resp.status_code == 200, (
        f"AC-I2: open-count is ALWAYS 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert set(body) == {"contact_id", "open_count"}, body
    assert isinstance(body["open_count"], int)
    assert body["contact_id"] is None or isinstance(body["contact_id"], str)
    return body


def test_open_count_shape_is_stable_across_every_state(client, db):
    seed = _seed(db)

    unknown = client.get(OPEN_COUNT_URL, params={"phone_number": "+60100000000"})
    assert _assert_open_count_contract(unknown)["open_count"] == 0

    no_tickets = client.get(OPEN_COUNT_URL, params={"contact_id": "10025908"})
    assert _assert_open_count_contract(no_tickets) == {
        "contact_id": seed["contact_id"],
        "open_count": 0,
    }

    service = ConversationSLATrackingService(db)
    ticket = service.create_tracking(
        ConversationSLATrackingCreate(**_create_body(seed, source_message_id="wamid.ct-7"))
    )
    with_open = client.get(OPEN_COUNT_URL, params={"contact_id": "10025908"})
    assert _assert_open_count_contract(with_open)["open_count"] == 1

    service.update_tracking(str(ticket.id), ConversationSLATrackingUpdate(is_resolved=True))
    all_resolved = client.get(OPEN_COUNT_URL, params={"contact_id": "10025908"})
    assert _assert_open_count_contract(all_resolved)["open_count"] == 0
