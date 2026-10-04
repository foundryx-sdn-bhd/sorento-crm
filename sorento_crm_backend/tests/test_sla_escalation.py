"""
Unit tests for tier-based SLA escalation: get_escalation_assignee_for_tier.
Uses mocks to avoid PostgreSQL-specific DB (ARRAY, UUID). Run with: pytest tests/test_sla_escalation.py -v
"""
import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock, patch

from app.services.sla_service import (
    ConversationSLATrackingService,
    _respond_contact_phone_lookup_candidates,
)


@pytest.fixture(autouse=True)
def _no_coverage_redirect():
    """These tests drive SLA assignment against fully-mocked DB sessions. The
    coverage-redirect inject points call ``resolve_assignee_with_coverage`` /
    ``coverage_note`` right before ``assigned_to_id`` is set; against a MagicMock
    session those issue an extra query whose ``.first()`` returns a truthy MagicMock,
    so the assignee would be "redirected" to a fake coverer. On a real DB with no
    coverage they are no-ops. Patch the SOURCE namespace (the inject points import it
    lazily) to reflect real-DB "no coverer" behaviour: passthrough assignee, empty note.
    """
    with patch(
        "app.services.coverage_subscription_service.resolve_assignee_with_coverage",
        side_effect=lambda db, assignee: (assignee, None),
    ), patch(
        "app.services.coverage_subscription_service.coverage_note",
        return_value="",
    ):
        yield


def _http_exception_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or "")
    return str(detail or "")


def test_entity_type_to_agent_code_mapping():
    """Verify source_entity_type maps to expected agent codes."""
    assert ConversationSLATrackingService.ENTITY_TYPE_TO_AGENT_CODE.get("complaint") == "complaint"
    assert ConversationSLATrackingService.ENTITY_TYPE_TO_AGENT_CODE.get("stock_inquiry") == "lead_time_enquiries"
    assert ConversationSLATrackingService.ENTITY_TYPE_TO_AGENT_CODE.get("purchase_request") == "purchase_request"
    # Unknown or None defaults to complaint in the method logic
    assert "complaint" in ConversationSLATrackingService.ENTITY_TYPE_TO_AGENT_CODE.values()


def test_get_escalation_assignee_for_tier_returns_assignee_when_configured():
    """When agent/team/assignee exist, returns assignee dict with respond_user_id."""
    mock_db = MagicMock()
    service = ConversationSLATrackingService(mock_db)
    expected = {"id": "u1", "email": "a@test.com", "name": "A", "respond_user_id": "971724"}

    with patch("app.services.user_service.AccessAgentService") as AccessAgentService:
        svc = AccessAgentService.return_value
        svc.get_agent_id_by_code.return_value = "agent1"
        svc.get_team_id_by_tier.return_value = "team1"
        svc.get_next_assignee.return_value = expected

        assignee = service.get_escalation_assignee_for_tier("complaint", 2, company_id="00000000-0000-0000-0000-000000000001")

        assert assignee == expected
        svc.get_agent_id_by_code.assert_called_once_with("complaint")
        svc.get_team_id_by_tier.assert_called_once_with(
            "agent1", 2, team_set_code=None, company_id="00000000-0000-0000-0000-000000000001"
        )
        # Both opt-in filters are always passed now, defaulting to "no filter".
        svc.get_next_assignee.assert_called_once_with(
            "agent1", "team1", None, brand_code=None
        )


def test_get_escalation_assignee_for_tier_defaults_none_to_complaint():
    """When source_entity_type is None, agent code defaults to complaint."""
    mock_db = MagicMock()
    service = ConversationSLATrackingService(mock_db)
    expected = {"id": "u1", "respond_user_id": "123"}

    with patch("app.services.user_service.AccessAgentService") as AccessAgentService:
        svc = AccessAgentService.return_value
        svc.get_agent_id_by_code.return_value = "agent1"
        svc.get_team_id_by_tier.return_value = "team1"
        svc.get_next_assignee.return_value = expected

        assignee = service.get_escalation_assignee_for_tier(None, 2, company_id="00000000-0000-0000-0000-000000000001")

        assert assignee == expected
        svc.get_agent_id_by_code.assert_called_once_with("complaint")
        svc.get_team_id_by_tier.assert_called_once_with(
            "agent1", 2, team_set_code=None, company_id="00000000-0000-0000-0000-000000000001"
        )


def test_get_escalation_assignee_for_tier_unknown_agent_raises():
    """When agent code does not exist, raises validation error."""
    mock_db = MagicMock()
    service = ConversationSLATrackingService(mock_db)

    with patch("app.services.user_service.AccessAgentService") as AccessAgentService:
        svc = AccessAgentService.return_value
        svc.get_agent_id_by_code.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            service.get_escalation_assignee_for_tier("stock_inquiry", 2, company_id="00000000-0000-0000-0000-000000000001")
        assert exc_info.value.status_code == 400
        msg = _http_exception_message(exc_info.value)
        assert "lead_time_enquiries" in msg


def test_get_escalation_assignee_for_tier_missing_tier_team_raises():
    """When agent exists but has no tier team, raises validation error."""
    mock_db = MagicMock()
    service = ConversationSLATrackingService(mock_db)

    with patch("app.services.user_service.AccessAgentService") as AccessAgentService:
        svc = AccessAgentService.return_value
        svc.get_agent_id_by_code.return_value = "agent1"
        svc.get_team_id_by_tier.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            service.get_escalation_assignee_for_tier("complaint", 3, company_id="00000000-0000-0000-0000-000000000001")
        assert exc_info.value.status_code == 400
        msg = _http_exception_message(exc_info.value)
        assert "tier 3" in msg or "3" in msg


def test_create_tracking_agent_code_with_explicit_assignee_skips_round_robin():
    """agent_code + assigned_to_id must not call get_escalation_assignee_for_tier (no double RR)."""
    from app.schemas.sla import ConversationSLATrackingCreate

    contact = MagicMock()
    contact.id = "contact-1"
    agent = MagicMock()
    agent.id = "agent-uuid-1"
    user = MagicMock()
    user.id = "user-explicit-uuid"
    user.respond_user_id = "273808"
    policy = MagicMock()
    tier = MagicMock()
    tier.response_hours = 24.0
    tier.resolution_hours = 24.0
    existing = MagicMock()
    existing.is_resolved = True
    existing.id = "tracking-existing"

    returns = iter([contact, agent, user, policy, tier, existing])

    def query_side_effect(_model):
        # The working-hours due-date calc (create_tracking) reads the work calendar
        # via the same session; return a benign mock for those models so it falls
        # back to calendar math without consuming the business query sequence.
        if getattr(_model, "__name__", "") in ("WorkCalendarConfig", "PublicHoliday"):
            return MagicMock()
        qm = MagicMock()
        qm.filter.return_value = qm
        qm.order_by.return_value = qm
        qm.first.return_value = next(returns)
        return qm

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect

    service = ConversationSLATrackingService(mock_db)
    # The backend now resolves the policy from (agent, team_set); with no binding
    # it falls back to the n8n-supplied policy_id (D8). Stub the resolver to None so
    # it doesn't consume the mocked query sequence - the fallback uses payload policy.
    with patch.object(
        service,
        "get_escalation_assignee_for_tier",
        side_effect=AssertionError("round-robin should not run when assignee is explicit"),
    ), patch.object(service, "_write_assign_event_log"), patch(
        "app.services.user_service.AccessAgentService.resolve_policy_id_for",
        return_value=None,
    ), patch(
        # Stamping the tracker's company reads the contact's company rows. Stub it for
        # the same reason resolve_policy_id_for is stubbed: it would otherwise consume
        # the fixed .first() sequence this test drives the session with.
        "app.services.company_routing_service.company_for_contact",
        return_value="00000000-0000-0000-0000-000000000001",
    ):
        payload = ConversationSLATrackingCreate(
            contact_phone_number="+60160000509",
            policy_id="policy-1",
            current_tier=1,
            agent_code="general_enquiries",
            assigned_to_id=str(user.id),
            team_set_code="purchasing_c",
        )
        service.create_tracking(payload)

    assert existing.assigned_to_id == user.id
    assert existing.assigned_to == "273808"
    assert existing.agent_id == str(agent.id)
    mock_db.commit.assert_called()


def test_respond_contact_phone_lookup_candidates_e164_and_local():
    c1 = _respond_contact_phone_lookup_candidates("+60160000509")
    assert "+60160000509" in c1
    assert "60160000509" in c1
    c2 = _respond_contact_phone_lookup_candidates("0160000509")
    assert "+60160000509" in c2
    c3 = _respond_contact_phone_lookup_candidates("+60 16-0000509")
    assert "+60160000509" in c3


def test_resolve_internal_respond_contact_id_falls_back_to_phone():
    mock_db = MagicMock()
    service = ConversationSLATrackingService(mock_db)
    contact = MagicMock()
    contact.id = "crm-contact-1"

    q_miss = MagicMock()
    q_miss.filter.return_value = q_miss
    q_miss.first.return_value = None

    q_phone = MagicMock()
    q_phone.filter.return_value = q_phone
    q_phone.first.return_value = contact

    mock_db.query.side_effect = [q_miss, q_miss, q_phone]

    assert service.resolve_internal_respond_contact_id("+60160000509") == "crm-contact-1"
    assert mock_db.query.call_count == 3


def test_get_escalation_assignee_for_tier_empty_team_raises():
    """When tier team has no members (get_next_assignee returns None), raises validation error."""
    mock_db = MagicMock()
    service = ConversationSLATrackingService(mock_db)

    with patch("app.services.user_service.AccessAgentService") as AccessAgentService:
        svc = AccessAgentService.return_value
        svc.get_agent_id_by_code.return_value = "agent1"
        svc.get_team_id_by_tier.return_value = "team1"
        svc.get_next_assignee.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            service.get_escalation_assignee_for_tier("purchase_request", 2, company_id="00000000-0000-0000-0000-000000000001")
        assert exc_info.value.status_code == 400
        msg = _http_exception_message(exc_info.value)
        assert "No assignee" in msg
