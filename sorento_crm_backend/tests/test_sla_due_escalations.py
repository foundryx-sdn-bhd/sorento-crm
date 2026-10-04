"""
Tests for the scheduled-escalation work-list endpoint and backend-owned escalation logging:

- GET /integration/due-escalations (n8n runner work-list, replaces raw SQL nodes)
- escalate_tracking: default auto-escalation reason when escalation_reason omitted
- escalate_tracking: new tier assignee applied BEFORE the escalation event log is written
- /integration/escalate route passes the resolved assignee into the service and exposes
  escalated_at / escalation_reason in the response

Uses mocks to avoid PostgreSQL-specific DB. Run with: pytest tests/test_sla_due_escalations.py -v
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.sla_service import ConversationSLATrackingService


@pytest.fixture
def client():
    from app.database import get_db as database_get_db
    from app.dependencies import (
        get_db as dependencies_get_db,
        get_current_user_or_api_key,
        get_current_user,
    )
    from app.main import app

    def _user():
        return {"id": "system"}

    def _db():
        yield MagicMock()

    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[database_get_db] = _db
    app.dependency_overrides[dependencies_get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


ASSIGNEE = {"id": "user-2", "email": "b@test.com", "name": "Tier2 B", "respond_user_id": "888"}


def _tracking_mock(current_tier: int = 1) -> MagicMock:
    tracking = MagicMock()
    tracking.id = "tracking-1"
    tracking.is_resolved = False
    tracking.current_tier = current_tier
    tracking.agent_id = "agent-a"
    tracking.team_set_code = "set_a"
    tracking.source_entity_type = "complaint"
    tracking.message_id = "msg-1"
    tracking.assigned_to_id = "user-1"
    tracking.assigned_to = "777"
    tracking.assigned_user = None
    tracking.respond_contact_id = "contact-1"
    tracking.policy_id = "policy-1"
    tracking.is_responded = False
    tracking.initiated_at = datetime.now(timezone.utc) - timedelta(hours=8)
    tracking.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    tracking.due_at_resolution = datetime.now(timezone.utc) + timedelta(hours=24)
    tracking.escalated_at = None
    tracking.escalation_reason = None
    return tracking


# ---------------------------------------------------------------------------
# Service: list_due_escalations
# ---------------------------------------------------------------------------

def _query_chain(rows):
    qm = MagicMock()
    qm.outerjoin.return_value = qm
    qm.filter.return_value = qm
    qm.order_by.return_value = qm
    qm.all.return_value = rows
    return qm


def test_list_due_escalations_maps_tracking_and_contact_fields():
    tracking = _tracking_mock(current_tier=1)
    tracking.due_at = datetime(2026, 6, 6, 2, 0, 0)  # naive UTC, as stored
    contact = MagicMock()
    contact.phone_number = "+60160000509"
    contact.respond_io_id = "rio-99"
    contact.name = "Ali"

    mock_db = MagicMock()
    mock_db.query.return_value = _query_chain([(tracking, contact)])
    service = ConversationSLATrackingService(mock_db)

    items = service.list_due_escalations()

    assert len(items) == 1
    item = items[0]
    assert item["tracking_id"] == "tracking-1"
    assert item["respond_contact_id"] == "contact-1"
    assert item["policy_id"] == "policy-1"
    assert item["current_tier"] == 1
    assert item["phone_number"] == "+60160000509"
    assert item["respond_io_id"] == "rio-99"
    assert item["assigned_to_id"] == "user-1"
    assert item["assigned_to_respond_user_id"] == "777"
    assert item["team_set_code"] == "set_a"
    assert item["message_id"] == "msg-1"
    assert item["breach_type"] == "response"
    assert item["is_responded"] is False
    # naive-UTC column serialized as aware-UTC ISO
    assert item["due_at"] == "2026-06-06T02:00:00+00:00"


def test_list_due_escalations_responded_row_marked_resolution_breach():
    tracking = _tracking_mock(current_tier=1)
    tracking.is_responded = True
    mock_db = MagicMock()
    mock_db.query.return_value = _query_chain([(tracking, None)])
    service = ConversationSLATrackingService(mock_db)

    items = service.list_due_escalations()

    assert items[0]["breach_type"] == "resolution"
    assert items[0]["is_responded"] is True


def test_list_due_escalations_handles_missing_contact():
    tracking = _tracking_mock()
    mock_db = MagicMock()
    mock_db.query.return_value = _query_chain([(tracking, None)])
    service = ConversationSLATrackingService(mock_db)

    items = service.list_due_escalations()

    assert items[0]["phone_number"] is None
    assert items[0]["respond_io_id"] is None


# ---------------------------------------------------------------------------
# Service: escalate_tracking - default reason + assignee-before-log ordering
# ---------------------------------------------------------------------------

def _run_escalate(service, tracking, **kwargs):
    tier = MagicMock()
    tier.response_hours = 4
    tier.resolution_hours = 24
    tier_chain = MagicMock()
    tier_chain.filter.return_value = tier_chain
    tier_chain.first.return_value = tier
    service.db.query.return_value = tier_chain

    captured = {}

    def _capture_event_log(event_data):
        # Snapshot the tracking's assignee at log-write time to pin ordering.
        captured["payload"] = event_data
        captured["tracking_assigned_to_id_at_log_time"] = tracking.assigned_to_id
        return MagicMock()

    with patch.object(service, "get_tracking_by_contact_and_policy", return_value=tracking), \
         patch.object(service, "create_event_log", side_effect=_capture_event_log):
        service.escalate_tracking(
            respond_contact_id="contact-1",
            policy_id="policy-1",
            current_tier=2,
            **kwargs,
        )
    return captured


def test_escalate_tracking_defaults_reason_when_omitted():
    tracking = _tracking_mock(current_tier=1)
    service = ConversationSLATrackingService(MagicMock())

    captured = _run_escalate(service, tracking)

    expected = "Auto-escalation: tier 1 response due time breached"
    assert captured["payload"].reason == expected
    assert tracking.escalation_reason == expected


def test_escalate_tracking_keeps_explicit_reason():
    tracking = _tracking_mock(current_tier=1)
    service = ConversationSLATrackingService(MagicMock())

    captured = _run_escalate(service, tracking, escalation_reason="no response by executive")

    assert captured["payload"].reason == "no response by executive"
    assert tracking.escalation_reason == "no response by executive"


def test_escalate_tracking_logs_new_assignee_not_previous():
    tracking = _tracking_mock(current_tier=1)  # previous assignee: user-1
    service = ConversationSLATrackingService(MagicMock())

    captured = _run_escalate(
        service,
        tracking,
        assigned_to_id="user-2",
        assigned_to_respond_user_id="888",
    )

    assert captured["tracking_assigned_to_id_at_log_time"] == "user-2"
    assert captured["payload"].assigned_to_id == "user-2"
    assert tracking.assigned_to == "888"


def test_escalate_tracking_without_assignee_keeps_existing():
    tracking = _tracking_mock(current_tier=1)
    service = ConversationSLATrackingService(MagicMock())

    captured = _run_escalate(service, tracking)

    assert captured["payload"].assigned_to_id == "user-1"
    assert tracking.assigned_to == "777"


# ---------------------------------------------------------------------------
# Service: list_due_escalations - split-clock breach filter (real SQL via sqlite)
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_db():
    from tests._pg_fixture import blank_session

    with blank_session() as s:
        yield s


def _insert_row(
    db,
    *,
    contact_id,
    policy_id,
    tier=1,
    is_resolved=False,
    is_responded=False,
    due_at=None,
    due_at_resolution=None,
    source_entity_type=None,
):
    import uuid as _uuid

    from app.models.sla import ConversationSLATracking

    row = ConversationSLATracking(
        id=str(_uuid.uuid4()),
        policy_id=policy_id,
        current_tier=tier,
        respond_contact_id=contact_id,
        is_resolved=is_resolved,
        is_responded=is_responded,
        due_at=due_at,
        due_at_resolution=due_at_resolution,
        source_entity_type=source_entity_type,
        source_entity_id=str(_uuid.uuid4()) if source_entity_type else None,
    )
    db.add(row)
    db.commit()
    return row


def test_split_clock_breach_filter(sqlite_db):
    import uuid as _uuid

    from app.models.access import RespondContact
    from app.models.sla import SLAPolicy

    db = sqlite_db
    policy_id = str(_uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="NORMAL", name="Normal"))
    contact_id = str(_uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id, phone_number="+60123456789", name="C", session_vars={}
        )
    )
    db.commit()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    past = now - timedelta(hours=1)
    future = now + timedelta(hours=4)
    common = {"contact_id": contact_id, "policy_id": policy_id}

    # included: response breach (not responded, due_at past)
    r_response = _insert_row(db, **common, due_at=past, due_at_resolution=future)
    # excluded: responded, response deadline past but resolution still in the future
    _insert_row(
        db, **common, is_responded=True, due_at=past, due_at_resolution=future
    )
    # included: resolution breach (responded, due_at_resolution past)
    r_resolution = _insert_row(
        db, **common, is_responded=True, due_at=past, due_at_resolution=past
    )
    # excluded: resolved
    _insert_row(db, **common, is_resolved=True, due_at=past, due_at_resolution=past)
    # excluded: tier 3 (nowhere to escalate)
    _insert_row(db, **common, tier=3, due_at=past, due_at_resolution=past)
    # excluded: form-SLA row, even though breached
    _insert_row(
        db, **common, due_at=past, due_at_resolution=past, source_entity_type="complaint"
    )
    # excluded: not yet due
    _insert_row(db, **common, due_at=future, due_at_resolution=future)

    from app.services.sla_service import ConversationSLATrackingService as Svc

    items = Svc(db).list_due_escalations()

    by_id = {i["tracking_id"]: i for i in items}
    assert set(by_id) == {str(r_response.id), str(r_resolution.id)}
    assert by_id[str(r_response.id)]["breach_type"] == "response"
    assert by_id[str(r_resolution.id)]["breach_type"] == "resolution"
    assert by_id[str(r_response.id)]["phone_number"] == "+60123456789"


# ---------------------------------------------------------------------------
# Route: GET /integration/due-escalations
# ---------------------------------------------------------------------------

@patch("app.api.v1.sla.sla_tracking.ConversationSLATrackingService")
def test_due_escalations_route_returns_work_list(mock_service_cls, client):
    svc = mock_service_cls.return_value
    svc.list_due_escalations.return_value = [
        {
            "tracking_id": "tracking-1",
            "respond_contact_id": "contact-1",
            "policy_id": "policy-1",
            "current_tier": 1,
            "due_at": "2026-06-06T02:00:00+00:00",
            "message_id": "msg-1",
            "source_entity_type": "complaint",
            "team_set_code": "set_a",
            "assigned_to_id": "user-1",
            "assigned_to_respond_user_id": "777",
            "phone_number": "+60160000509",
            "respond_io_id": "rio-99",
            "contact_name": "Ali",
        }
    ]

    r = client.get(
        "/api/v1/sla-management/conversation-sla-tracking/integration/due-escalations"
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "success"
    assert data["count"] == 1
    assert data["items"][0]["respond_contact_id"] == "contact-1"
    assert data["items"][0]["phone_number"] == "+60160000509"


@patch("app.api.v1.sla.sla_tracking.ConversationSLATrackingService")
def test_due_escalations_route_empty(mock_service_cls, client):
    svc = mock_service_cls.return_value
    svc.list_due_escalations.return_value = []

    r = client.get(
        "/api/v1/sla-management/conversation-sla-tracking/integration/due-escalations"
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "success", "count": 0, "items": []}


# ---------------------------------------------------------------------------
# Route: /integration/escalate - optional reason, assignee pass-through, escalated_at
# ---------------------------------------------------------------------------

@patch("app.api.v1.sla.sla_tracking.IntegrationLogService")
@patch("app.api.v1.sla.sla_tracking.ConversationSLATrackingService")
def test_escalate_without_reason_passes_assignee_and_returns_escalated_at(
    mock_service_cls, _mock_log, client
):
    svc = mock_service_cls.return_value
    before = _tracking_mock(current_tier=1)
    after = _tracking_mock(current_tier=2)
    after.escalated_at = datetime(2026, 6, 6, 3, 0, 0, tzinfo=timezone.utc)
    after.escalation_reason = "Auto-escalation: tier 1 response due time breached"
    svc.resolve_internal_respond_contact_id.return_value = "contact-1"
    # get_open_tracking_by_contact is the route's PRIMARY resolver (S2b); an
    # unconfigured auto-mock is truthy (and its auto-mocked .is_resolved is
    # truthy too), so it must return None to fall through to the
    # get_tracking_by_contact_and_policy mock this test actually configures.
    svc.get_open_tracking_by_contact.return_value = None
    svc.get_tracking_by_contact_and_policy.return_value = before
    svc.get_escalation_assignee_for_tier.return_value = ASSIGNEE
    svc.escalate_tracking.return_value = after

    # Explicit null must behave like omitting the field (validators don't run on the
    # default, so the validator itself must accept None) - was a 422 before the fix.
    r = client.post(
        "/api/v1/sla-management/conversation-sla-tracking/integration/escalate",
        json={
            "respond_contact_id": "contact-1",
            "policy_id": "policy-1",
            "escalation_reason": None,
        },
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["escalated"] is True
    assert data["escalated_at"] == "2026-06-06T03:00:00+00:00"
    assert data["escalation_reason"] == "Auto-escalation: tier 1 response due time breached"
    kwargs = svc.escalate_tracking.call_args.kwargs
    assert kwargs["escalation_reason"] is None  # default applied in the service
    assert kwargs["assigned_to_id"] == "user-2"
    assert kwargs["assigned_to_respond_user_id"] == "888"
