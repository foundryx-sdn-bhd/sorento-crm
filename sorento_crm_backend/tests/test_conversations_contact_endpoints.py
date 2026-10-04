"""Contact-keyed thread endpoints and the inbox reply (UAC AC-N2 / AC-N3).

The drawer's thread reads are ticket-keyed and assignee-or-manager scoped. That
conflates READ access with ACT access, which is the defect Section N exists to
fix: "after i reassign already, I can't see it anymore". These are the twins
keyed by CONTACT and gated by `sla_management.conversations.view`.

Pinned here:

1. **Same shapes.** The contact-keyed page / search return byte-identical
   payload shapes to the ticket-keyed ones - asserted by comparing the two
   responses for the same underlying contact, not by restating the shape.
2. **The gate really is the permission.** A user who could never act on the
   ticket reads the thread when they hold `.view`, and is refused when they do
   not - the exact inversion of the ticket-keyed rule.
3. **Notes render from both scopes** (AC-N3): the contact-scoped ones ingested
   from Respond AND the ticket-scoped ones written in a drawer, including a
   drawer note on a ticket assigned to someone else.
4. **Reply stamping.** Exactly one open ticket of my own -> the reply is that
   ticket's first response. Zero or several -> the message still goes, nothing
   is stamped, and the human-intervention signal still fires so the bot stands
   down either way.
5. **Quoted replies** carry `reply_to_message_id` / `reply_to_excerpt` exactly
   like the drawer send: audit-only, recorded on the response event log,
   NEVER sent to Respond (the ">" quote prefix is composed by the FE and the
   text goes verbatim). Accepted on both the JSON and the multipart lane.

Run:
    venv/bin/pytest tests/test_conversations_contact_endpoints.py -q
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
from app.models.sla import ConversationSLAEventLog, SLAPolicy, SLAPolicyTier
from app.models.ticket_comment import ConversationTicketComment
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60120000543"
RESPOND_IO_ID = "zzt-conv-io-1"
TICKET_BASE = "/api/v1/sla-management/conversation-sla-tracking"
INBOX_BASE = "/api/v1/sla-management/conversations"
VIEW = "sla_management.conversations.view"
REPLY = "sla_management.conversations.reply"

_GRANTS: set[str] = set()
_ADMINS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "Test Actor"}


class _DeadRespondClient:
    """Respond unreachable, so the thread reads answer from the local lane and
    the assertions stay about scope and shape, not about the network."""

    def list_messages(self, *a, **k):
        raise RuntimeError("Respond.io unreachable in tests")


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    _GRANTS.clear()
    _GRANTS.update({VIEW, REPLY})
    _ADMINS.clear()
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_role_slugs",
        lambda self, uid: {"admin"} if str(uid) in _ADMINS else set(),
    )
    yield
    _GRANTS.clear()
    _ADMINS.clear()


@pytest.fixture(autouse=True)
def _local_lane_only():
    with patch("app.services.integration_service.RespondClient") as client_cls:
        client_cls.return_value = _DeadRespondClient()
        client_cls.for_identifier.return_value = _DeadRespondClient()
        client_cls.for_contact_id.return_value = _DeadRespondClient()
        yield client_cls


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
            name="ZZT Inbox Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    colleague_id = str(uuid.uuid4())
    for uid, label in ((assignee_id, "assignee"), (colleague_id, "colleague")):
        db.add(User(id=uid, email=f"zzt-{label}-{uid[:8]}@test.com", name=f"ZZT {label}"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_CONV_AGENT", name="ZZT Conv Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Conv Team - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_conv_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()

    base = datetime(2026, 8, 12, 3, 0, 0)
    for i in range(3):
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number=PHONE,
                message=f"inbox hello {i}",
                sent_at=base + timedelta(minutes=i),
                type="incoming",
                message_id=str(9000 + i),
            )
        )
    db.commit()

    tracking = ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="ZZT_CONV_AGENT",
            team_set_code="zzt_conv_set",
            policy_id=policy_id,
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id="wamid.inbox-1",
            source_message_text="Please connect me to a person.",
        )
    )
    _act_as(assignee_id)
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "assignee_id": assignee_id,
        "colleague_id": colleague_id,
        "tracking_id": str(tracking.id),
    }


def _second_ticket(db, seed, *, assigned_to_id, source_message_id):
    return ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="ZZT_CONV_AGENT",
            team_set_code="zzt_conv_set",
            policy_id=seed["policy_id"],
            assigned_to_id=assigned_to_id,
            contact_phone_number=PHONE,
            source_message_id=source_message_id,
            source_message_text="A second enquiry.",
        )
    )


# --------------------------------------------------------------------------- #
# Shape parity with the ticket-keyed twins (AC-N3)                             #
# --------------------------------------------------------------------------- #


def test_the_contact_keyed_page_matches_the_ticket_keyed_page(client, seed):
    by_ticket = client.get(f"{TICKET_BASE}/{seed['tracking_id']}/conversation/page")
    by_contact = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/page")
    assert by_ticket.status_code == 200, by_ticket.text
    assert by_contact.status_code == 200, by_contact.text
    assert by_contact.json() == by_ticket.json()
    assert [i["messageId"] for i in by_contact.json()["items"]] == [9000, 9001, 9002]


def test_the_contact_keyed_search_matches_the_ticket_keyed_search(client, seed):
    params = {"q": "inbox hello 1"}
    by_ticket = client.get(
        f"{TICKET_BASE}/{seed['tracking_id']}/conversation/search", params=params
    )
    by_contact = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/search", params=params)
    assert by_contact.status_code == 200, by_contact.text
    assert by_contact.json() == by_ticket.json()
    assert [i["message_id"] for i in by_contact.json()["items"]] == ["9001"]


def test_a_contact_ref_may_be_a_phone_number(client, seed):
    by_phone = client.get(f"{INBOX_BASE}/{PHONE}/page")
    assert by_phone.status_code == 200, by_phone.text
    assert [i["messageId"] for i in by_phone.json()["items"]] == [9000, 9001, 9002]


def test_an_unknown_contact_ref_is_404(client, seed):
    assert client.get(f"{INBOX_BASE}/zzt-no-such-contact/page").status_code == 404


def test_only_one_cursor_may_be_passed(client, seed):
    got = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/page", params={"before": "1", "after": "2"})
    assert got.status_code == 422


# --------------------------------------------------------------------------- #
# The gate is the permission, not the assignment (AC-N2)                       #
# --------------------------------------------------------------------------- #


def test_a_colleague_who_cannot_act_on_the_ticket_can_still_read_the_thread(client, seed):
    """The whole point of Section N. The same user, same contact: 404 through
    the ticket-keyed route, 200 through the contact-keyed one."""
    _act_as(seed["colleague_id"])
    assert (
        client.get(f"{TICKET_BASE}/{seed['tracking_id']}/conversation/page").status_code == 404
    )
    assert client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/page").status_code == 200


def test_without_the_view_permission_every_contact_read_is_403(client, seed):
    _GRANTS.clear()
    for path in (
        f"{INBOX_BASE}/{RESPOND_IO_ID}/page",
        f"{INBOX_BASE}/{RESPOND_IO_ID}/search",
        f"{INBOX_BASE}/{RESPOND_IO_ID}/comments",
    ):
        assert client.get(path).status_code == 403, path


# --------------------------------------------------------------------------- #
# Notes by contact (AC-N3)                                                     #
# --------------------------------------------------------------------------- #


def test_contact_notes_include_both_contact_scoped_and_ticket_scoped_ones(client, db, seed):
    db.add(
        ConversationTicketComment(
            id=str(uuid.uuid4()),
            tracking_id=None,
            respond_contact_id=seed["contact_id"],
            author_name="Respond Author",
            body="ingested from the Respond inbox",
            mentioned_user_ids=[],
            source="respond",
            respond_comment_id=f"zzt-{uuid.uuid4().hex[:8]}",
            created_at=datetime(2026, 8, 12, 1, 0, 0),
        )
    )
    db.add(
        ConversationTicketComment(
            id=str(uuid.uuid4()),
            tracking_id=seed["tracking_id"],
            respond_contact_id=seed["contact_id"],
            author_name="Drawer Author",
            body="written in the drawer",
            mentioned_user_ids=[],
            source="crm",
            created_at=datetime(2026, 8, 12, 2, 0, 0),
        )
    )
    db.commit()

    _act_as(seed["colleague_id"])
    got = client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments")
    assert got.status_code == 200, got.text
    assert [row["body"] for row in got.json()] == [
        "ingested from the Respond inbox",
        "written in the drawer",
    ]


def test_a_note_on_another_contact_does_not_leak_in(client, db, seed):
    other_contact = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=other_contact,
            phone_number="+60120000544",
            name="ZZT Other",
            respond_io_id="zzt-conv-io-9",
            session_vars={},
        )
    )
    db.add(
        ConversationTicketComment(
            id=str(uuid.uuid4()),
            tracking_id=None,
            respond_contact_id=other_contact,
            author_name="Elsewhere",
            body="not for this thread",
            mentioned_user_ids=[],
            source="crm",
        )
    )
    db.commit()
    bodies = [row["body"] for row in client.get(f"{INBOX_BASE}/{RESPOND_IO_ID}/comments").json()]
    assert "not for this thread" not in bodies


# --------------------------------------------------------------------------- #
# Reply (AC-N2)                                                                #
# --------------------------------------------------------------------------- #


@pytest.fixture
def sent(monkeypatch):
    """Capture what the shared smart-send helper was asked to deliver."""
    import app.services.respond_chat_template_service as chat

    calls: list[dict] = []

    def fake_send(db, **kwargs):
        calls.append(kwargs)
        return {
            "sent_as": "text",
            "rendered_text": kwargs.get("text") or "",
            "flattened": False,
            "window_state": {"open": True, "last_incoming_at": None},
            "response": {"messageId": 424242},
        }

    monkeypatch.setattr(chat, "send_chat_message_for", fake_send)
    return calls


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


def test_a_reply_with_one_open_ticket_of_mine_stamps_that_ticket(client, db, seed, sent, signals):
    _act_as(seed["assignee_id"])
    got = client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/reply", json={"text": "on my way"})
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] == seed["tracking_id"]
    assert [c["text"] for c in sent] == ["on my way"]
    assert [c["business_table"] for c in sent] == ["conversation_sla_tracking"]

    tracking = ConversationSLATrackingService(db).get_tracking(seed["tracking_id"])
    assert tracking.is_responded is True
    assert str(tracking.responded_by) == seed["assignee_id"]
    assert [s["business_table"] for s in signals] == ["conversation_sla_tracking"]


def test_a_reply_with_no_ticket_of_mine_is_unstamped_but_still_sends_and_signals(
    client, db, seed, sent, signals
):
    _act_as(seed["colleague_id"])
    got = client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/reply", json={"text": "just helping out"})
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] is None
    # Outbox keyed on the contact, because no one ticket owns this send.
    assert [c["business_table"] for c in sent] == ["respond_contacts"]
    assert [c["business_id"] for c in sent] == [seed["contact_id"]]
    # The bot still has to stand down.
    assert [s["business_table"] for s in signals] == ["respond_contacts"]

    tracking = ConversationSLATrackingService(db).get_tracking(seed["tracking_id"])
    assert tracking.is_responded is False, "someone else's ticket must not be stamped"


def test_a_reply_with_two_open_tickets_of_mine_is_unstamped(client, db, seed, sent, signals):
    _second_ticket(db, seed, assigned_to_id=seed["assignee_id"], source_message_id="wamid.inbox-2")
    _act_as(seed["assignee_id"])
    got = client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/reply", json={"text": "which one?"})
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] is None
    assert [c["business_table"] for c in sent] == ["respond_contacts"]


def test_a_resolved_ticket_of_mine_does_not_capture_the_reply(client, db, seed, sent, signals):
    from app.schemas.sla import ConversationSLATrackingUpdate

    ConversationSLATrackingService(db).update_tracking(
        seed["tracking_id"],
        ConversationSLATrackingUpdate(is_resolved=True, resolved_by=seed["assignee_id"]),
    )
    _act_as(seed["assignee_id"])
    got = client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/reply", json={"text": "following up"})
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] is None


def test_an_empty_reply_is_refused(client, seed, sent):
    _act_as(seed["assignee_id"])
    assert client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/reply", json={"text": "  "}).status_code == 400
    assert sent == []


def test_reply_needs_the_reply_permission_not_just_view(client, seed, sent):
    _GRANTS.clear()
    _GRANTS.add(VIEW)
    _act_as(seed["assignee_id"])
    assert (
        client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/reply", json={"text": "nope"}).status_code == 403
    )
    assert sent == []


# --------------------------------------------------------------------------- #
# Reassign picker linkage (AC-N7)                                              #
# --------------------------------------------------------------------------- #


def test_the_reassign_picker_carries_respond_linkage_over_the_wire(client, db, seed):
    """The dialog badges (and filters on) who can carry a real Respond sender
    identity. Asserted at the ROUTE, not the service: a response_model that
    forgot the field would drop it silently between the two."""
    from app.models.access import Team, TeamMember

    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT Picker Team"))
    linked_id = str(uuid.uuid4())
    db.add(
        User(
            id=linked_id,
            email=f"zzt-linked-{linked_id[:8]}@test.com",
            name="ZZT Linked",
            respond_user_id="900999",
        )
    )
    for uid in (seed["assignee_id"], seed["colleague_id"], linked_id):
        db.add(TeamMember(id=str(uuid.uuid4()), team_id=team_id, user_id=uid))
    db.commit()

    _act_as(seed["assignee_id"])
    got = client.get(f"{TICKET_BASE}/visible-users")
    assert got.status_code == 200, got.text
    rows = {row["id"]: row for row in got.json()["data"]}
    assert rows[linked_id]["respond_linked"] is True
    assert rows[seed["colleague_id"]]["respond_linked"] is False


# --------------------------------------------------------------------------- #
# Quoted reply (AC-L6 parity with the drawer send)                             #
# --------------------------------------------------------------------------- #


def _response_log(db, tracking_id: str):
    return (
        db.query(ConversationSLAEventLog)
        .filter(
            ConversationSLAEventLog.sla_tracking_id == str(tracking_id),
            ConversationSLAEventLog.event_type == "response",
        )
        .one()
    )


def test_a_quoted_reply_records_the_quote_on_the_response_event_log(
    client, db, seed, sent, signals
):
    """Same audit trail the drawer's send writes - the quote is the only record
    of WHICH message this answered, because Respond has no reply-to parameter."""
    _act_as(seed["assignee_id"])
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/reply",
        json={
            "text": "> inbox hello 1\n\nYes, Tuesday works.",
            "reply_to_message_id": "9001",
            "reply_to_excerpt": "inbox hello 1",
        },
    )
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] == seed["tracking_id"]

    reason = _response_log(db, seed["tracking_id"]).reason or ""
    assert "reply_to_message_id=9001" in reason
    assert "quoted_reply=true" in reason


def test_the_quote_prefix_goes_verbatim_and_the_audit_fields_never_reach_respond(
    client, db, seed, sent, signals
):
    _act_as(seed["assignee_id"])
    client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/reply",
        json={
            "text": "> inbox hello 1\n\nYes, Tuesday works.",
            "reply_to_message_id": "9001",
            "reply_to_excerpt": "inbox hello 1",
        },
    )
    assert [c["text"] for c in sent] == ["> inbox hello 1\n\nYes, Tuesday works."]
    for call in sent:
        assert "reply_to_message_id" not in call
        assert "reply_to_excerpt" not in call


def test_a_plain_reply_records_no_quote(client, db, seed, sent, signals):
    _act_as(seed["assignee_id"])
    client.post(f"{INBOX_BASE}/{RESPOND_IO_ID}/reply", json={"text": "no quote here"})
    reason = _response_log(db, seed["tracking_id"]).reason or ""
    assert "reply_to_message_id" not in reason
    assert "quoted_reply" not in reason


def test_the_multipart_lane_reads_the_quote_fields_too(client, db, seed, sent, signals):
    """The attachment lane is multipart, so the two fields have to be parsed
    from the form as well as from JSON. The empty-filename part is what a
    browser sends for an untouched file input - the route skips it, so this
    stays a text send while still being a real multipart request."""
    _act_as(seed["assignee_id"])
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/reply",
        data={
            "text": "> inbox hello 0\n\nOn it.",
            "reply_to_message_id": "9000",
            "reply_to_excerpt": "inbox hello 0",
        },
        files=[("files", ("", b"", "application/octet-stream"))],
    )
    assert got.status_code == 200, got.text
    reason = _response_log(db, seed["tracking_id"]).reason or ""
    assert "reply_to_message_id=9000" in reason
    assert "quoted_reply=true" in reason
    assert [c["text"] for c in sent] == ["> inbox hello 0\n\nOn it."]


def test_an_unstamped_quoted_reply_still_sends_verbatim(client, db, seed, sent, signals):
    """No ticket of mine to write the audit onto, so the quote has nowhere to be
    recorded - but the message the contact receives is unchanged."""
    _act_as(seed["colleague_id"])
    got = client.post(
        f"{INBOX_BASE}/{RESPOND_IO_ID}/reply",
        json={
            "text": "> inbox hello 2\n\nHelping out.",
            "reply_to_message_id": "9002",
            "reply_to_excerpt": "inbox hello 2",
        },
    )
    assert got.status_code == 200, got.text
    assert got.json()["stamped_ticket_id"] is None
    assert [c["text"] for c in sent] == ["> inbox hello 2\n\nHelping out."]
