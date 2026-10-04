"""AI assist: draft a reply into the ticket composer (UAC AC-L5, slice S4.4).

The draft NEVER sends. It is written into the assignee's input for them to edit,
so the only things worth pinning are: does the model see the thread, does the
route refuse the people it should, and does a model that is missing or broken
degrade into a message a human can act on rather than a 500.

The LLM client is stubbed throughout - no network, no key. The stub records the
exact messages it was handed, which is how the grounding assertions work.

Run:
    venv/bin/pytest tests/test_ticket_ai_draft.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.chat_history import ChatHistory
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

BASE = "/api/v1/sla-management/conversation-sla-tracking"
PHONE = "+60100000003"
RESPOND_IO_ID = "10025777"


class _DeadRespondClient:
    """Respond.io unreachable, so the thread read falls to the local lane.

    Deterministic on purpose: this suite is about what the model is TOLD, and a
    live Respond page would make the transcript depend on the network.
    """

    def list_messages(self, *a, **k):
        raise RuntimeError("respond down")

    def get_message(self, *a, **k):
        raise RuntimeError("respond down")

    # The thread read resolves the CONTACT's workspace rather than building a
    # default-workspace client, so the stub has to answer the same constructors
    # the real class does.
    @classmethod
    def for_identifier(cls, *_a, **_k):
        return cls()

    @classmethod
    def for_contact_id(cls, *_a, **_k):
        return cls()


class _ChatResult:
    def __init__(self, content):
        self.content = content
        self.prompt_tokens = 11
        self.completion_tokens = 7
        self.total_tokens = 18
        self.tool_calls = []
        self.raw = None


class _StubProvider:
    """Records every call so a test can read the prompt that was built."""

    def __init__(self, content="Thanks for waiting - your order ships tomorrow.", fail=False):
        self.content = content
        self.fail = fail
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self.fail:
            raise RuntimeError("provider exploded")
        return _ChatResult(self.content)


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.integration_service.RespondClient", _DeadRespondClient
    )
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _no_admins(monkeypatch):
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


_ACTOR: dict = {"id": None, "name": "Agent One"}


@pytest.fixture
def client(db):
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def provider(monkeypatch):
    stub = _StubProvider()
    monkeypatch.setattr(
        "app.services.conversation_reply_draft_service._resolve_provider",
        lambda db: (stub, "openai", "gpt-4o"),
    )
    return stub


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
    db.add(
        RespondContact(
            id=str(uuid.uuid4()),
            phone_number=PHONE,
            name="Aisyah Rahman",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    outsider_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="cs-ai@test.com", name="Agent One"))
    db.add(User(id=outsider_id, email="outsider-ai@test.com", name="Someone Else"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="CS_AI", name="CS AI"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="Customer Service - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="cs_ai_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    tracking = ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="CS_AI",
            team_set_code="cs_ai_set",
            policy_id=policy_id,
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id="wamid.ai-1",
            source_message_text="Where is my order?",
        )
    )
    _ACTOR["id"] = assignee_id
    _ACTOR["name"] = "Agent One"
    return tracking, assignee_id, outsider_id


def _seed_messages(db, count=3):
    base = datetime(2026, 8, 15, 9, 0, 0)
    for i in range(count):
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number=PHONE,
                message=f"customer line {i}",
                sent_at=base + timedelta(minutes=i * 2),
                type="incoming",
                message_id=str(1000 + i),
            )
        )
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number=PHONE,
                message=f"agent line {i}",
                sent_at=base + timedelta(minutes=i * 2 + 1),
                type="outgoing",
                message_id=str(2000 + i),
            )
        )
    db.commit()


def _prompt_text(provider) -> str:
    return "\n".join(str(m.get("content") or "") for m in provider.calls[-1]["messages"])


# ------------------------------------------------------------------ happy path


def test_draft_comes_back_for_the_assignee(client, db, provider):
    tracking, _a, _o = _seed(db)
    _seed_messages(db)

    resp = client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"] == "Thanks for waiting - your order ships tomorrow."
    assert body["model"] == "gpt-4o"
    assert body["grounded_on"] == 6


def test_the_prompt_carries_the_thread_tail(client, db, provider):
    tracking, _a, _o = _seed(db)
    _seed_messages(db)

    client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    prompt = _prompt_text(provider)
    for i in range(3):
        assert f"customer line {i}" in prompt
        assert f"agent line {i}" in prompt


def test_the_prompt_says_who_said_what(client, db, provider):
    tracking, _a, _o = _seed(db)
    _seed_messages(db, count=1)

    client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    prompt = _prompt_text(provider)
    assert "Customer: customer line 0" in prompt
    assert "Us: agent line 0" in prompt


def test_the_prompt_carries_the_enquiry_and_the_contact(client, db, provider):
    tracking, _a, _o = _seed(db)
    _seed_messages(db, count=1)

    client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    prompt = _prompt_text(provider)
    assert "Where is my order?" in prompt
    assert "Aisyah Rahman" in prompt


def test_only_the_tail_is_sent(client, db, provider):
    """A long thread is truncated to the visible window, newest kept."""
    tracking, _a, _o = _seed(db)
    _seed_messages(db, count=20)  # 40 rows

    body = client.post(f"{BASE}/{tracking.id}/ai-draft", json={"tail": 6}).json()

    assert body["grounded_on"] == 6
    prompt = _prompt_text(provider)
    assert "agent line 19" in prompt
    assert "customer line 0" not in prompt


def test_the_user_instruction_reaches_the_prompt(client, db, provider):
    tracking, _a, _o = _seed(db)
    _seed_messages(db, count=1)

    client.post(
        f"{BASE}/{tracking.id}/ai-draft",
        json={"instruction": "apologise and offer a Tuesday delivery"},
    )

    assert "apologise and offer a Tuesday delivery" in _prompt_text(provider)


def test_a_thread_with_no_messages_still_drafts_from_the_enquiry(client, db, provider):
    tracking, _a, _o = _seed(db)

    resp = client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["grounded_on"] == 0
    assert "Where is my order?" in _prompt_text(provider)


def test_the_draft_is_never_sent_to_the_contact(client, db, provider):
    """The stubbed Respond client raises on any messaging call, so a draft that
    leaked onto a send path fails here rather than reaching a customer."""
    tracking, _a, _o = _seed(db)
    _seed_messages(db, count=1)

    assert client.post(f"{BASE}/{tracking.id}/ai-draft", json={}).status_code == 200
    assert (
        db.query(ChatHistory).filter(ChatHistory.type == "outgoing").count() == 1
    ), "no new outgoing message may exist"


# ----------------------------------------------------------------- auth denial


def test_an_outsider_gets_a_404_not_a_403(client, db, provider):
    """Same rule as every sibling ticket route: no existence leak."""
    tracking, _assignee, outsider = _seed(db)
    _ACTOR["id"] = outsider

    resp = client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    assert resp.status_code == 404, resp.text
    assert provider.calls == [], "an outsider must not spend a model call"


def test_an_unknown_ticket_is_404(client, db, provider):
    _seed(db)
    assert client.post(f"{BASE}/{uuid.uuid4()}/ai-draft", json={}).status_code == 404


# ------------------------------------------------------------------- failures


def test_a_provider_failure_is_a_readable_error_not_a_500(client, db, monkeypatch):
    tracking, _a, _o = _seed(db)
    stub = _StubProvider(fail=True)
    monkeypatch.setattr(
        "app.services.conversation_reply_draft_service._resolve_provider",
        lambda db: (stub, "openai", "gpt-4o"),
    )

    resp = client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    assert resp.status_code == 503, resp.text
    # The AppException handler serializes the detail dict AS the body.
    assert "draft" in resp.json()["message"].lower()


def test_an_unconfigured_assistant_says_so(client, db, monkeypatch):
    tracking, _a, _o = _seed(db)
    monkeypatch.setattr(
        "app.services.conversation_reply_draft_service._resolve_provider",
        lambda db: (None, "openai", ""),
    )

    resp = client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    assert resp.status_code == 503, resp.text
    assert "not configured" in resp.json()["message"].lower()


def test_an_empty_model_answer_is_an_error_not_a_blank_draft(client, db, monkeypatch):
    tracking, _a, _o = _seed(db)
    stub = _StubProvider(content="   ")
    monkeypatch.setattr(
        "app.services.conversation_reply_draft_service._resolve_provider",
        lambda db: (stub, "openai", "gpt-4o"),
    )

    resp = client.post(f"{BASE}/{tracking.id}/ai-draft", json={})

    assert resp.status_code == 503, resp.text


# -------------------------------------------------------------- prompt registry


def test_the_system_prompt_comes_from_the_registry_key():
    from app.services.ai_prompt_registry import PROMPT_KEYS
    from app.services.conversation_reply_draft_service import AGENT_NAME

    assert AGENT_NAME in PROMPT_KEYS
    spec = PROMPT_KEYS[AGENT_NAME]
    assert spec.active is True
    assert spec.fallback().strip(), "the key must carry a hardcoded fallback body"


# -------------------------------------------------------------- transcript lines


def test_a_quick_reply_prompt_reads_its_title_not_a_placeholder():
    from app.services.conversation_reply_draft_service import _line_for

    line = _line_for(
        {"traffic": "outgoing", "message": {"type": "quick_reply", "title": "Escalate this?"}}
    )
    assert line == "Us: Escalate this?"
    assert _line_for({"traffic": "incoming", "message": {"type": "image"}}) == "Customer: [image]"
