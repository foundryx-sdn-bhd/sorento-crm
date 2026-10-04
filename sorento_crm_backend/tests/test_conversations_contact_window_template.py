"""Contact-keyed 24h window read + template send (UAC AC-N2/AC-N3, S4.9 gap 3).

The drawer gets its window state and its out-of-window chat-template preview
from `GET .../conversation-sla-tracking/{tracking_id}/ticket`, which is
ticket-keyed and assignee-scoped. The Conversations inbox has no ticket, so it
had to run with the window forced open and no "Send template" button - the
backend still smart-sent the template, but the operator could not see what the
contact would receive or fill the message slot in.

Pinned here:

1. **Same numbers, same shapes.** `GET /conversations/{ref}/window` answers with
   the SAME `window` + `chat_template` objects the drawer reads off the ticket
   detail - asserted by comparing the two responses for one contact, not by
   restating the shape (they share one service core).
2. **Read is the view permission**, send is the reply permission.
3. **The template send is the smart-send twin of the reply**: stamped onto the
   sender's single open ticket when there is exactly one (response clock +
   human-intervention signal, exactly like the drawer's template send), else
   unstamped against the contact. Either way the Respond outbox is written -
   on success AND on failure.

Run:
    venv/bin/pytest tests/test_conversations_contact_window_template.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.chat_history import ChatHistory
from app.models.integration import IntegrationLog
from app.models.respond_template import (
    RespondChannel,
    RespondMessageTemplate,
    RespondTemplateDefault,
)
from app.models.respond_workspace import RespondWorkspace
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60120000547"
RESPOND_IO_ID = "zzt-win-io-1"
TICKET_BASE = "/api/v1/sla-management/conversation-sla-tracking"
INBOX_BASE = "/api/v1/sla-management/conversations"
VIEW = "sla_management.conversations.view"
REPLY = "sla_management.conversations.reply"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "ZZT Sender"}


class _TemplateClient:
    """Respond accepts template sends and nothing else. `list_messages` raises
    so the window resolves from the local chat_histories lane, which is what
    makes the parity assertion deterministic."""

    def __init__(self) -> None:
        self.sends: list[dict] = []
        self.fail = False

    def list_messages(self, *a, **k):
        raise RuntimeError("Respond.io unreachable in tests")

    def send_template_message(self, identifier, **kwargs):
        self.sends.append({"identifier": identifier, **kwargs})
        if self.fail:
            raise RuntimeError("401 Unauthorized")
        return {"messageId": 515151}

    def send_message(self, *a, **k):  # pragma: no cover - guarded
        raise AssertionError("A template send must not fall back to a text send.")


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service
    import app.services.respond_messaging_service as messaging

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    # Module-level window cache is keyed on the identifier only, so a stale
    # entry from another suite would decide this one's window.
    messaging._window_cache.clear()
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session
    messaging._window_cache.clear()


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({VIEW, REPLY})
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    yield
    _GRANTS.clear()


@pytest.fixture
def respond():
    stub = _TemplateClient()
    with patch("app.services.integration_service.RespondClient") as client_cls:
        client_cls.return_value = stub
        client_cls.for_identifier.return_value = stub
        client_cls.for_contact_id.return_value = stub
        yield stub


@pytest.fixture
def signals(monkeypatch):
    """Capture the AC-J human-intervention signal on both lanes."""
    import app.services.crm_chat_outbound_webhook as hook

    fired: list[dict] = []

    def fake(db, **kwargs):
        fired.append(kwargs)
        return True

    monkeypatch.setattr(hook, "_notify_human_send", fake)
    return fired


@pytest.fixture
def client(db, respond):
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


def _act_as(user_id: str) -> None:
    _ACTOR["id"] = user_id


@pytest.fixture
def seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=f"ZZT-{uuid.uuid4().hex[:6]}", name="ZZT Policy"))
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
            name="ZZT Window Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    colleague_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email=f"zzt-a-{assignee_id[:8]}@test.com", name="ZZT Assignee"))
    db.add(User(id=colleague_id, email=f"zzt-c-{colleague_id[:8]}@test.com", name="ZZT Colleague"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_WIN_AGENT", name="ZZT Win Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Win Team - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_win_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )

    # A conversation_chat template default, so the preview is "configured".
    workspace_id = str(uuid.uuid4())
    db.add(
        RespondWorkspace(
            id=workspace_id,
            space_id="zzt-space",
            name="ZZT Workspace",
            api_key_ciphertext="zzt-not-a-real-key",
        )
    )
    channel_id = str(uuid.uuid4())
    db.add(
        RespondChannel(
            id=channel_id,
            workspace_id=workspace_id,
            respond_channel_id=987654,
            name="ZZT WhatsApp",
            source="whatsapp_business",
        )
    )
    template_id = str(uuid.uuid4())
    db.add(
        RespondMessageTemplate(
            id=template_id,
            channel_id=channel_id,
            respond_template_id=112233,
            name="zzt_conversation_chat",
            language_code="en",
            status="approved",
            components=[],
            body_text="Hi {{1}}, {{2}} here: {{3}}",
            param_count=3,
        )
    )
    db.add(
        RespondTemplateDefault(
            id=str(uuid.uuid4()),
            use_case="conversation_chat",
            template_id=template_id,
            template_name_snapshot="zzt_conversation_chat",
            param_mapping={"1": "contact_name", "2": "sender_name", "3": "message"},
        )
    )
    db.commit()

    # Old enough that the 24h window is shut: the out-of-window branch is the
    # one the inbox composer could not render before this endpoint.
    base = datetime.utcnow() - timedelta(days=4)
    for i in range(2):
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number=PHONE,
                message=f"window hello {i}",
                sent_at=base + timedelta(minutes=i),
                type="incoming",
                message_id=str(9200 + i),
            )
        )
    db.commit()

    tracking = ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="ZZT_WIN_AGENT",
            team_set_code="zzt_win_set",
            policy_id=policy_id,
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id="wamid.window-1",
            source_message_text="Please connect me to a person.",
        )
    )
    _act_as(assignee_id)
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "assignee_id": assignee_id,
        "colleague_id": colleague_id,
        "template_id": template_id,
        "tracking_id": str(tracking.id),
    }


def _second_ticket(db, seed, *, assigned_to_id, source_message_id):
    return ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="ZZT_WIN_AGENT",
            team_set_code="zzt_win_set",
            policy_id=seed["policy_id"],
            assigned_to_id=assigned_to_id,
            contact_phone_number=PHONE,
            source_message_id=source_message_id,
            source_message_text="A second enquiry.",
        )
    )


# --------------------------------------------------------------------------- #
# Window + template preview (read)                                             #
# --------------------------------------------------------------------------- #


def test_the_contact_window_matches_what_the_drawer_reads(client, seed):
    by_ticket = client.get(f"{TICKET_BASE}/{seed['tracking_id']}/ticket")
    by_contact = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/window")
    assert by_ticket.status_code == 200, by_ticket.text
    assert by_contact.status_code == 200, by_contact.text
    ticket = by_ticket.json()
    assert by_contact.json() == {
        "window": ticket["window"],
        "chat_template": ticket["chat_template"],
    }


def test_the_window_reports_the_closed_state_and_the_fillable_template(client, seed):
    got = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/window").json()
    assert got["window"] == {"open": False, "expires_at": None}
    preview = got["chat_template"]
    assert preview["configured"] is True
    assert preview["template_name"] == "zzt_conversation_chat"
    # Exactly one editable slot: the message the operator types.
    assert [k for k, v in preview["slots"].items() if v["editable"]] == ["3"]
    assert preview["slots"]["1"]["value"] == "ZZT Window Contact"
    assert preview["slots"]["2"]["value"] == "ZZT Sender"


def test_a_contact_ref_may_be_a_phone_number(client, seed):
    assert client.get(f"{INBOX_BASE}/{PHONE}/window").status_code == 200


def test_a_contact_with_no_respond_link_is_a_closed_window_and_no_template(client, db, seed):
    db.add(
        RespondContact(
            id=str(uuid.uuid4()),
            phone_number="+60120000548",
            name="ZZT Unlinked",
            respond_io_id=None,
            session_vars={},
        )
    )
    db.commit()
    got = client.get(f"{INBOX_BASE}/+60120000548/window")
    assert got.status_code == 200, got.text
    assert got.json() == {
        "window": {"open": False, "expires_at": None},
        "chat_template": {"configured": False, "reason": "no_contact"},
    }


def test_the_window_read_needs_the_view_permission(client, seed):
    _GRANTS.clear()
    assert client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/window").status_code == 403


def test_an_unknown_contact_ref_is_404(client, seed):
    assert client.get(f"{INBOX_BASE}/zzt-no-such-contact/window").status_code == 404


# --------------------------------------------------------------------------- #
# Template send                                                                #
# --------------------------------------------------------------------------- #


def _send(client, seed, **over):
    body = {"template_id": seed["template_id"], "params": {"1": "Aisyah", "2": "ZZT Sender", "3": "We reopen Monday."}}
    body.update(over)
    return client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/template-message", json=body)


def test_a_template_send_stamps_my_single_open_ticket(client, db, seed, respond, signals):
    _act_as(seed["assignee_id"])
    got = _send(client, seed)
    assert got.status_code == 200, got.text
    payload = got.json()
    assert payload["ok"] is True
    assert payload["queued"] is False, "the operator needs the real outcome, not a receipt"
    assert payload["template_name"] == "zzt_conversation_chat"
    assert payload["rendered_body"] == "Hi Aisyah, ZZT Sender here: We reopen Monday."
    assert payload["stamped_ticket_id"] == seed["tracking_id"]

    assert [s["identifier"] for s in respond.sends] == [RESPOND_IO_ID]

    tracking = ConversationSLATrackingService(db).get_tracking(seed["tracking_id"])
    assert tracking.is_responded is True
    assert str(tracking.responded_by) == seed["assignee_id"]

    log = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "conversation_sla_tracking")
        .one()
    )
    assert log.status == "success"
    assert [s["business_table"] for s in signals] == ["conversation_sla_tracking"]


def test_a_template_send_with_no_ticket_of_mine_is_unstamped_but_still_goes(
    client, db, seed, respond, signals
):
    _act_as(seed["colleague_id"])
    got = _send(client, seed)
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] is None
    assert len(respond.sends) == 1

    log = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "respond_contacts")
        .one()
    )
    assert str(log.business_id) == seed["contact_id"]
    assert [s["business_table"] for s in signals] == ["respond_contacts"]

    tracking = ConversationSLATrackingService(db).get_tracking(seed["tracking_id"])
    assert tracking.is_responded is False, "someone else's ticket must not be stamped"


def test_two_open_tickets_of_mine_is_unstamped(client, db, seed, respond, signals):
    _second_ticket(db, seed, assigned_to_id=seed["assignee_id"], source_message_id="wamid.window-2")
    _act_as(seed["assignee_id"])
    got = _send(client, seed)
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] is None


def test_the_template_send_needs_the_reply_permission(client, seed, respond):
    _GRANTS.clear()
    _GRANTS.add(VIEW)
    assert _send(client, seed).status_code == 403
    assert respond.sends == []


def test_an_unknown_template_is_404(client, seed, respond):
    assert _send(client, seed, template_id=str(uuid.uuid4())).status_code == 404
    assert respond.sends == []


def test_a_missing_parameter_is_refused_before_the_send(client, seed, respond):
    got = _send(client, seed, params={"1": "Aisyah", "2": "ZZT Sender"})
    assert got.status_code == 400, got.text
    assert respond.sends == []


def test_an_unknown_contact_ref_is_404_on_send(client, seed, respond):
    got = client.post(
        f"{INBOX_BASE}/zzt-no-such-contact/template-message",
        json={"template_id": seed["template_id"], "params": {"1": "a", "2": "b", "3": "c"}},
    )
    assert got.status_code == 404, got.text
    assert respond.sends == []


def test_a_refused_send_is_a_502_with_an_outbox_row_and_no_stamp(
    client, db, seed, respond, signals
):
    """Local dev runs with deliberately-wrong credentials: a 401'd send must
    still be readable from the Respond outbox, and must not claim a reply."""
    respond.fail = True
    _act_as(seed["assignee_id"])
    got = _send(client, seed)
    assert got.status_code == 502, got.text

    log = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "conversation_sla_tracking")
        .one()
    )
    assert log.status == "failed"
    tracking = ConversationSLATrackingService(db).get_tracking(seed["tracking_id"])
    assert tracking.is_responded is False
    assert signals == []
