"""Form handling-lock - notification producer + WhatsApp outbox (§12 P2-11/P2-12).

Two layers:

1. PRODUCER (``HandlingLockService._notify``): claim / take-over / release each
   push a notification whose ``data`` carries the right WhatsApp use-case
   (``sla_handling_claimed`` / ``_taken_over`` / ``_released``) plus
   ``whatsapp_context_vars.handler_name``, and is gated by the per-user
   ``notify_email_on_handling`` / ``notify_whatsapp_on_handling`` prefs. Recipient
   set excludes the actor (§6). Driven through the real service against sqlite; the
   producer's ``create_with_channel_preferences`` call is captured.

2. WORKER OUTBOX (``notification_tasks._send_whatsapp_for_notification``): a handling
   notification whose ``whatsapp_use_case`` is a handling case writes an
   ``integration_log`` outbox row on BOTH success AND failure (local runs use
   intentionally-wrong Respond creds - a 401'd send must still be logged). Mirrors
   tests/test_whatsapp_notification_outbox_log.py with a handling payload.

Run: venv/bin/pytest tests/test_form_handling_lock_notify_outbox.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.models.access import (
    AccessAgent,
    AgentTeam,
    Team,
    TeamMember,
)
from app.models.sla import (
    ConversationSLAEventLog,
    ConversationSLATracking,
    SLAPolicy,
    SLAPolicyTier,
)
from app.models.user import SystemSetting, User
from app.services.handling_lock_service import HandlingLockService
from app.tasks.notification_tasks import _send_whatsapp_for_notification
from tests._pg_fixture import blank_session


# --------------------------------------------------------------------------- #
# PRODUCER - blank Postgres schema (mirrors tests/test_form_handling_lock.py)  #
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="FORM", name="Form"))
    for lvl in (1, 2, 3):
        db.add(
            SLAPolicyTier(
                id=str(uuid.uuid4()),
                policy_id=policy_id,
                tier_level=lvl,
                tier_name=f"Tier {lvl}",
                response_hours=4,
                resolution_hours=24,
            )
        )
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="complaint", name="Complaint"))

    team1 = str(uuid.uuid4())
    team2 = str(uuid.uuid4())
    db.add(Team(id=team1, name="Tier 1"))
    db.add(Team(id=team2, name="Tier 2"))
    db.add(AgentTeam(id=str(uuid.uuid4()), agent_id=agent_id, code="cs", team_id=team1, tier=1))
    db.add(AgentTeam(id=str(uuid.uuid4()), agent_id=agent_id, code="cs", team_id=team2, tier=2))

    users = {}
    for key, email in (
        ("assignee", "assignee@t.com"),
        ("member_a", "a@t.com"),
        ("member_b", "b@t.com"),
        ("outsider", "out@t.com"),
    ):
        uid = str(uuid.uuid4())
        db.add(User(id=uid, email=email, name=key, status="ACTIVE"))
        users[key] = uid
    for uid in (users["assignee"], users["member_a"]):
        db.add(TeamMember(id=str(uuid.uuid4()), team_id=team1, user_id=uid))
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=team2, user_id=users["member_b"]))

    db.add(
        SystemSetting(
            id=str(uuid.uuid4()),
            name="Co",
            handling_lock_enabled_types="complaint",
        )
    )
    db.commit()
    return {"policy_id": policy_id, "agent_id": agent_id, "users": users}


# Captures every create_with_channel_preferences(...) call as (user_id, kwargs).
_CALLS: list[tuple] = []


@pytest.fixture(autouse=True)
def _capture(monkeypatch):
    from app.services.notification_service import NotificationService

    _CALLS.clear()

    def _rec(self, user_id, **kwargs):
        _CALLS.append((str(user_id), kwargs))
        return None

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", _rec)
    yield


def _tracker(db, seed, *, tier=2, handled_by=None):
    now = datetime.utcnow()
    t = ConversationSLATracking(
        id=str(uuid.uuid4()),
        policy_id=seed["policy_id"],
        current_tier=tier,
        initiated_at=now - timedelta(hours=5),
        current_tier_started_at=now - timedelta(hours=2),
        due_at=now + timedelta(hours=4),
        due_at_resolution=now + timedelta(hours=20),
        escalated_at=now - timedelta(hours=2),  # escalated → lock applies
        is_resolved=False,
        source_entity_type="complaint",
        source_entity_id=str(uuid.uuid4()),
        agent_id=seed["agent_id"],
        team_set_code="cs",
        assigned_to_id=seed["users"]["assignee"],
        handled_by_id=handled_by,
        handled_at=now if handled_by else None,
    )
    db.add(t)
    db.commit()
    return t


def _actor(seed, key):
    return {"id": seed["users"][key]}


def _use_cases():
    return {kw["data"]["whatsapp_use_case"] for _uid, kw in _CALLS}


def _handler_names():
    return {
        kw["data"]["whatsapp_context_vars"].get("handler_name")
        for _uid, kw in _CALLS
    }


# --------------------------------------------------------------------------- #
# P2-12 producer: use-case + handler_name + channel-pref attrs                 #
# --------------------------------------------------------------------------- #
def test_claim_producer_use_case_and_handler_name(db, seed):
    t = _tracker(db, seed, tier=2)
    HandlingLockService(db).claim_handling(t.id, _actor(seed, "member_a"))
    assert _CALLS, "claim must notify at least one non-actor recipient"
    assert _use_cases() == {"sla_handling_claimed"}
    assert _handler_names() == {"member_a"}
    # Channel gating routes through the per-user handling prefs (§6 matrix).
    for _uid, kw in _CALLS:
        assert kw["whatsapp_pref_attr"] == "notify_whatsapp_on_handling"
        assert kw["email_pref_attr"] == "notify_email_on_handling"
        assert kw["send_in_app"] is True  # in-app always fires for non-actor recipients
        # WhatsApp text mirrors the notification body (in-window raw text path).
        assert kw["data"]["whatsapp_text"]


def test_claim_producer_excludes_actor(db, seed):  # P2-11
    t = _tracker(db, seed, tier=2)
    HandlingLockService(db).claim_handling(t.id, _actor(seed, "member_a"))
    recipients = {uid for uid, _kw in _CALLS}
    assert seed["users"]["member_a"] not in recipients  # actor never notified
    assert seed["users"]["assignee"] in recipients
    assert seed["users"]["member_b"] in recipients
    assert seed["users"]["outsider"] not in recipients


def test_take_over_producer_use_case_notifies_displaced_only(db, seed):
    t = _tracker(db, seed, tier=2, handled_by=seed["users"]["member_a"])
    HandlingLockService(db).take_over_handling(
        t.id, _actor(seed, "member_b"), seed["users"]["member_a"]
    )
    assert _use_cases() == {"sla_handling_taken_over"}
    assert _handler_names() == {"member_b"}  # the new handler
    # Only the displaced holder is notified.
    assert {uid for uid, _kw in _CALLS} == {seed["users"]["member_a"]}


def test_release_producer_use_case_notifies_pool(db, seed):
    t = _tracker(db, seed, tier=2, handled_by=seed["users"]["member_a"])
    HandlingLockService(db).release_handling(t.id, _actor(seed, "member_a"))
    assert _use_cases() == {"sla_handling_released"}
    assert _handler_names() == {"member_a"}
    recipients = {uid for uid, _kw in _CALLS}
    assert seed["users"]["member_a"] not in recipients  # actor excluded
    assert seed["users"]["member_b"] in recipients  # eligible pool


# --------------------------------------------------------------------------- #
# P2-12 worker outbox: integration_log on success AND failure                  #
# --------------------------------------------------------------------------- #
def _wa_objs(use_case: str):
    db = MagicMock()
    notification = SimpleNamespace(
        id="632da7e3-a60a-4727-8a71-baa77c2970fd",
        data={
            "whatsapp_use_case": use_case,
            "whatsapp_text": "member_a is now handling complaint CMP-1.",
            "whatsapp_context_vars": {"handler_name": "member_a"},
        },
        event_type="form_sla",
        title="Handling started",
        body="member_a is now handling complaint CMP-1.",
        source_entity_type="form_sla_tracking",
        source_entity_id=str(uuid.uuid4()),
    )
    user = SimpleNamespace(id="u1")
    delivery = SimpleNamespace(status="pending", error_message=None, sent_at=None)
    contact = SimpleNamespace(id="contact-uuid", respond_io_id="900000104")
    return db, notification, user, delivery, contact


def _wa_patches(contact, send_side_effect=None, send_return=None):
    log_service = MagicMock()
    p_contact = patch(
        "app.services.respond_link_service.resolve_user_respond_contact",
        return_value=contact,
    )
    send = MagicMock(side_effect=send_side_effect, return_value=send_return)
    p_send = patch(
        "app.services.respond_messaging_service.send_text_or_template", send
    )
    p_log = patch(
        "app.services.integration_service.IntegrationLogService",
        return_value=log_service,
    )
    return p_contact, p_send, p_log, log_service


def test_handling_whatsapp_failure_writes_outbox_log():
    db, notification, user, delivery, contact = _wa_objs("sla_handling_claimed")
    resp = httpx.Response(
        401,
        json={"code": 401, "message": "Token not found"},
        request=httpx.Request("POST", "https://api.respond.io/x"),
    )
    err = httpx.HTTPStatusError("401", request=resp.request, response=resp)
    err.request_payload = {
        "message": {"type": "whatsapp_template", "use_case": "sla_handling_claimed"}
    }
    p_contact, p_send, p_log, log_service = _wa_patches(contact, send_side_effect=err)
    with p_contact, p_send, p_log:
        _send_whatsapp_for_notification(db, notification, user, delivery)

    assert log_service.create_integration_log.call_count == 1
    log_arg = log_service.create_integration_log.call_args.args[0]
    assert log_arg.integration_channel == "respond_io"
    assert log_arg.direction == "outbound"
    assert log_arg.status == "failed"
    assert log_arg.status_code == 401
    assert log_arg.external_reference == "900000104"
    # business_id is the notification UUID, never a composite source id.
    assert log_arg.business_id == notification.id
    assert delivery.status == "failed"


def test_handling_whatsapp_success_writes_outbox_log():
    db, notification, user, delivery, contact = _wa_objs("sla_handling_released")
    p_contact, p_send, p_log, log_service = _wa_patches(
        contact,
        send_return={
            "request_payload": {"message": {"type": "text", "text": "x"}},
            "response": {"messageId": 1},
            "sent_as": "text",
        },
    )
    with p_contact, p_send, p_log:
        _send_whatsapp_for_notification(db, notification, user, delivery)

    assert log_service.create_integration_log.call_count == 1
    log_arg = log_service.create_integration_log.call_args.args[0]
    assert log_arg.status == "success"
    assert log_arg.external_reference == "900000104"
    assert delivery.status == "sent"


def test_handling_whatsapp_send_receives_handling_use_case():
    """The worker forwards the notification's handling use-case to the send path so
    the out-of-window template resolves to the correct approved template."""
    db, notification, user, delivery, contact = _wa_objs("sla_handling_taken_over")
    p_contact, p_send, p_log, _log_service = _wa_patches(
        contact,
        send_return={"request_payload": {}, "response": {"messageId": 2}},
    )
    with p_contact, p_send as send, p_log:
        _send_whatsapp_for_notification(db, notification, user, delivery)

    assert send.call_count == 1
    assert send.call_args.kwargs["use_case"] == "sla_handling_taken_over"
    assert send.call_args.kwargs["context_vars"] == {"handler_name": "member_a"}
