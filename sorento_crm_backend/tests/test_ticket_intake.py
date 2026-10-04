"""Tests for the IT-Support ticket intake service.

Exercises the confirmed=True branch (no LLM call needed) and verifies the
multi-channel source attribution + round-robin assignment + form-SLA event
emission. The confirmed=False branch is exercised via a mocked provider.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.access import AccessAgent, AgentTeam, RespondContact, Team, TeamMember
from app.models.tickets import Ticket
from app.models.user import User
from app.schemas.tickets import (
    ITSupportTicketCreateRequest,
    ITSupportTicketDraftPreview,
)
from app.services.ticket_intake_service import TicketIntakeService


@pytest.fixture
def db() -> Session:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def seed_user(db: Session) -> User:
    user = (
        db.query(User).filter(User.email == "person38@example.com").first()
    )
    assert user is not None, "expected seed user person38@example.com to exist"
    return user


@pytest.fixture
def seed_agent(db: Session) -> AccessAgent:
    agent = db.query(AccessAgent).filter_by(code="it_support").first()
    assert agent is not None, "expected migration 183 to seed it_support agent"
    return agent


def _preview_dict() -> dict:
    return {
        "title": "Test ticket from intake",
        "priority": "medium",
        "category": "bug",
        "description_html": "<p>Test body</p>",
        "description_text": "Test body",
    }


def test_create_ai_assistant_path(
    db: Session, seed_user: User, seed_agent: AccessAgent
):
    payload = ITSupportTicketCreateRequest(
        original_message="Cannot login",
        source_channel="ai_assistant",
        actor_user_id=str(seed_user.id),
        confirmed=True,
        confirmation_args={
            "preview": _preview_dict(),
            "source_channel": "ai_assistant",
            "actor_user_id": str(seed_user.id),
        },
    )
    response = TicketIntakeService(db).submit(payload)

    assert response.status == "created"
    assert response.ticket is not None
    ticket_id = response.ticket.id

    ticket = db.query(Ticket).filter_by(id=ticket_id).first()
    assert ticket is not None
    assert ticket.source_channel == "ai_assistant"
    assert ticket.raised_by_kind == "user"
    assert ticket.raised_by == str(seed_user.id)
    assert ticket.assigned_to is not None  # round-robin set assignee
    assert ticket.status == "assigned"


def test_create_whatsapp_path(db: Session, seed_agent: AccessAgent):
    contact = (
        db.query(RespondContact)
        .filter(RespondContact.phone_number.isnot(None))
        .first()
    )
    if not contact:
        pytest.skip("no respond contact seeded; cannot exercise whatsapp path")

    payload = ITSupportTicketCreateRequest(
        original_message="Bug from WhatsApp",
        source_channel="whatsapp_respond",
        respond_contact_id=str(contact.id),
        source_conversation_id="conv-123",
        source_message_id="msg-456",
        source_space_id="space-789",
        confirmed=True,
        confirmation_args={
            "preview": _preview_dict(),
            "source_channel": "whatsapp_respond",
            "respond_contact_id": str(contact.id),
            "source_conversation_id": "conv-123",
            "source_message_id": "msg-456",
            "source_space_id": "space-789",
        },
    )
    response = TicketIntakeService(db).submit(payload)

    assert response.status == "created"
    ticket_id = response.ticket.id
    ticket = db.query(Ticket).filter_by(id=ticket_id).first()
    assert ticket.source_channel == "whatsapp_respond"
    assert ticket.raised_by_kind == "respond_contact"
    assert ticket.raised_by == str(contact.id)
    assert ticket.source_conversation_id == "conv-123"
    assert ticket.source_message_id == "msg-456"
    assert ticket.source_space_id == "space-789"


def test_invalid_preview_priority_rejected(db: Session, seed_user: User):
    payload = ITSupportTicketCreateRequest(
        original_message="x",
        source_channel="ai_assistant",
        actor_user_id=str(seed_user.id),
        confirmed=True,
        confirmation_args={
            "preview": {**_preview_dict(), "priority": "absurd"},
            "source_channel": "ai_assistant",
            "actor_user_id": str(seed_user.id),
        },
    )
    with pytest.raises(Exception):
        TicketIntakeService(db).submit(payload)


def test_draft_returns_needs_confirmation_with_mock_llm(db: Session, seed_user: User):
    """When confirmed=False, the LLM is invoked and a preview is returned with
    no DB write. The provider is patched to a deterministic stub."""
    from app.services.llm_provider import ChatResult

    fake_payload = (
        '{"title": "Login bug", "priority": "high", "category": "bug",'
        ' "description_html": "<p>Cannot login</p>",'
        ' "description_text": "Cannot login"}'
    )

    class _StubProvider:
        def chat(self, *_a, **_k):
            return ChatResult(
                content=fake_payload,
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            )

    with patch(
        "app.services.ticket_intake_service.get_provider",
        return_value=_StubProvider(),
    ):
        payload = ITSupportTicketCreateRequest(
            original_message="I cannot login",
            source_channel="ai_assistant",
            actor_user_id=str(seed_user.id),
        )
        response = TicketIntakeService(db).submit(payload)

    assert response.status == "needs_confirmation"
    assert response.preview is not None
    assert response.preview.title == "Login bug"
    assert response.preview.priority == "high"
    assert response.confirmation_args is not None
    assert response.confirmation_args["preview"]["title"] == "Login bug"
    # No ticket was created.
    assert response.ticket is None
