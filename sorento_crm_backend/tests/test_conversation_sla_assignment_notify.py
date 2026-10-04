"""Conversation-SLA create notifies the PRIMARY assignee (CRM owns it, not n8n).

Regression pin: conversation create used to delegate the assignee notification to
n8n/Respond, so the per-user assignment toggles in Settings never fired for a
conversation SLA. `ConversationSLATrackingService.create_tracking` now calls
`_notify_assignment_on_create` on the new-row AND overwrite-resolved paths (NOT the
idempotent already_active path), mirroring the form-SLA assignment matrix.

This pins:
  1. New-row notifies the primary assignee: one `conversation_sla` Notification keyed
     (source_entity_type="conversation_sla_tracking", source_entity_id=<tracking>,
     event_type="assigned:<occ>"), with in_app + email deliveries (email pref default).
  2. Idempotent-active does NOT re-notify (repeat inbound on the open conversation).
  3. Overwrite-resolved (fresh conversation reusing the row) with a DIFFERENT start
     time re-notifies - the occurrence-suffixed event_type dodges stale dedup on the
     reused tracking id.
  4. Per-user gate: notify_email_on_assignment=False => in_app only, NO email delivery.

Run:
    venv/bin/pytest tests/test_conversation_sla_assignment_notify.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact
from app.models.notification import (
    Notification,
    NotificationDelivery,
    NotificationSubscription,
)
from app.models.sla import (
    ConversationSLAEventLog,
    ConversationSLATracking,
    SLAPolicy,
    SLAPolicyTier,
)
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE = "+60123456789"
SOURCE = "conversation_sla_tracking"


@pytest.fixture
def db(monkeypatch):
    # No Redis in the unit harness; the notify's last step enqueues a delivery job.
    # create_with_channel_preferences swallows enqueue errors, but stub anyway so the
    # test never depends on a broker.
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)

    # agent_teams stays empty -> resolve_policy_id_for returns None -> policy_id
    # fallback, same as before; the blank schema just supplies every table.
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


def _seed(
    db,
    *,
    email_on_assignment: bool = True,
    whatsapp_on_assignment: bool = False,
    link_assignee_whatsapp: bool = False,
) -> dict:
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
            id=contact_id, phone_number=PHONE, name="Test Contact", session_vars={}
        )
    )
    db.add(AccessAgent(id=str(uuid.uuid4()), code="AG1", name="Agent Chain"))
    assignee_id = str(uuid.uuid4())
    # AC-G1 WhatsApp gating needs the ASSIGNEE (not the enquiry contact) to
    # resolve to a RespondContact via resolve_user_respond_io_id - a distinct
    # RespondContact row keyed by the assignee's own WhatsApp number.
    assignee_respond_contact_id = None
    if link_assignee_whatsapp:
        assignee_respond_contact_id = str(uuid.uuid4())
        db.add(
            RespondContact(
                id=assignee_respond_contact_id,
                phone_number="+60110000526",
                name="Agent One WhatsApp",
                respond_io_id="900001",
                session_vars={},
            )
        )
    db.add(
        User(
            id=assignee_id,
            email="agent1@test.com",
            name="Agent One",
            respond_user_id="900001",
            notify_email_on_assignment=email_on_assignment,
            notify_whatsapp_on_assignment=whatsapp_on_assignment,
            respond_contact_id=assignee_respond_contact_id,
        )
    )
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "assignee_id": assignee_id,
        "assignee_respond_contact_id": assignee_respond_contact_id,
    }


def _payload(seed, *, message_id=None, started_at=None) -> ConversationSLATrackingCreate:
    return ConversationSLATrackingCreate(
        policy_id=seed["policy_id"],  # fallback: no AgentTeam binding seeded
        current_tier=1,
        assigned_to_id=seed["assignee_id"],
        agent_code="AG1",
        team_set_code="TS1",
        contact_phone_number=PHONE,
        message_id=message_id,
        current_tier_started_at=started_at,
        initiated_at=started_at,
    )


def _assignee_notes(db, assignee_id) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(
            Notification.user_id == assignee_id,
            Notification.source_entity_type == SOURCE,
        )
        .order_by(Notification.event_type.asc())
        .all()
    )


def _deliveries(db, note_id) -> list[NotificationDelivery]:
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.notification_id == note_id)
        .all()
    )


# --------------------------------------------------------------------------
# 1. New-row notifies the primary assignee (in-app + email)
# --------------------------------------------------------------------------

def test_new_row_notifies_primary_assignee(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "frontend_base_url", "https://crm.example.com")
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    t0 = datetime(2026, 6, 1, 8, 0, 0)
    tracking = service.create_tracking(_payload(seed, message_id=111, started_at=t0))

    notes = _assignee_notes(db, seed["assignee_id"])
    assert len(notes) == 1, "primary assignee should get exactly one assignment note"
    note = notes[0]
    assert note.type == "conversation_sla"
    assert note.source_entity_id == str(tracking.id)
    assert note.event_type == f"assigned:{int(t0.timestamp())}"
    # Worded "to you" (assignee), deep-links to the dashboard with the ticket
    # targeted (UAC AC-G1) - the assignee answers from the "My Pending" drawer,
    # not the standalone SLA detail page or Respond.io.
    assert "assigned to you" in (note.body or "")
    assert f"/?ticket={tracking.id}" in (note.body or "")
    channels = {d.channel for d in _deliveries(db, note.id)}
    assert "in_app" in channels
    assert "email" in channels


# --------------------------------------------------------------------------
# 2. Idempotent-active does NOT re-notify
# --------------------------------------------------------------------------

def test_idempotent_active_does_not_renotify(db):
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    first = service.create_tracking(_payload(seed, message_id=111))
    assert len(_assignee_notes(db, seed["assignee_id"])) == 1

    # Repeat of the SAME trigger message on the still-open ticket -> idempotent
    # return, no notify. A DIFFERENT message_id is a new enquiry (AC-A1), not
    # this path any more.
    second = service.create_tracking(_payload(seed, message_id=111))
    assert str(second.id) == str(first.id)
    assert bool(getattr(second, "_already_active", False)) is True

    assert len(_assignee_notes(db, seed["assignee_id"])) == 1, (
        "idempotent-active path must not re-notify the assignee"
    )


# --------------------------------------------------------------------------
# 3. Overwrite-resolved (fresh conversation) re-notifies via occurrence key
# --------------------------------------------------------------------------

def test_overwrite_resolved_renotifies_new_occurrence(db):
    """Overwrite-in-place only happens on the identity-less legacy path now
    (AC-A1/AC-A2 gave every trigger message its own ticket) - no message_id on
    either call, so both hit the contact-level singleton fallback."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    t0 = datetime(2026, 6, 1, 8, 0, 0)
    first = service.create_tracking(_payload(seed, started_at=t0))
    assert len(_assignee_notes(db, seed["assignee_id"])) == 1

    first.is_resolved = True
    first.resolved_at = datetime(2026, 6, 1, 10, 0, 0)
    db.commit()

    t1 = datetime(2026, 6, 2, 8, 0, 0)  # different occurrence
    second = service.create_tracking(_payload(seed, started_at=t1))
    assert str(second.id) == str(first.id)
    assert bool(getattr(second, "_overwrote_resolved", False)) is True

    notes = _assignee_notes(db, seed["assignee_id"])
    assert len(notes) == 2, "overwrite-resolved must re-notify (new occurrence)"
    assert {n.event_type for n in notes} == {
        f"assigned:{int(t0.timestamp())}",
        f"assigned:{int(t1.timestamp())}",
    }


def test_same_occurrence_dedups_to_one(db):
    """Overwrite-resolved with the SAME start time = same occurrence key => one
    note. No message_id on either call - the identity-less legacy singleton
    path is the only one that still overwrites in place."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)

    t0 = datetime(2026, 6, 1, 8, 0, 0)
    first = service.create_tracking(_payload(seed, started_at=t0))
    first.is_resolved = True
    db.commit()
    service.create_tracking(_payload(seed, started_at=t0))

    notes = _assignee_notes(db, seed["assignee_id"])
    assert len(notes) == 1, "same-occurrence start-time must dedup to one note"


# --------------------------------------------------------------------------
# 4. Per-user email toggle gates the email channel
# --------------------------------------------------------------------------

def test_email_toggle_off_suppresses_email_delivery(db):
    seed = _seed(db, email_on_assignment=False)
    service = ConversationSLATrackingService(db)

    service.create_tracking(_payload(seed, message_id=111))

    notes = _assignee_notes(db, seed["assignee_id"])
    assert len(notes) == 1, "in-app note still created regardless of email toggle"
    channels = {d.channel for d in _deliveries(db, notes[0].id)}
    assert "in_app" in channels
    assert "email" not in channels, "email toggle off must suppress the email delivery"


# --------------------------------------------------------------------------
# 5. Per-user WhatsApp toggle gates the whatsapp channel (UAC AC-G1)
# --------------------------------------------------------------------------
# Mirrors the email-toggle pin above: create_with_channel_preferences gates
# send_whatsapp on BOTH the recipient's notify_whatsapp_on_assignment toggle
# AND resolve_user_respond_io_id(db, user) (an actually-linked RespondContact) -
# either one missing must suppress the whatsapp delivery row entirely.


def test_whatsapp_toggle_off_suppresses_whatsapp_attempt(db):
    """Toggle OFF (default) even WITH a linked RespondContact -> no whatsapp
    delivery is ever created; in-app/email are untouched."""
    seed = _seed(
        db, whatsapp_on_assignment=False, link_assignee_whatsapp=True
    )
    service = ConversationSLATrackingService(db)

    service.create_tracking(_payload(seed, message_id=111))

    notes = _assignee_notes(db, seed["assignee_id"])
    assert len(notes) == 1
    channels = {d.channel for d in _deliveries(db, notes[0].id)}
    assert "in_app" in channels
    assert "whatsapp" not in channels, (
        "whatsapp toggle off must suppress the whatsapp delivery even when a "
        "RespondContact is linked"
    )


def test_whatsapp_toggle_on_without_linked_contact_is_not_attempted(db):
    """Toggle ON but the assignee resolves to NO RespondContact (no
    respond_contact_id, no matching phone) -> still no whatsapp attempt; the
    gate is an AND of toggle + reachability, not the toggle alone."""
    seed = _seed(
        db, whatsapp_on_assignment=True, link_assignee_whatsapp=False
    )
    service = ConversationSLATrackingService(db)

    service.create_tracking(_payload(seed, message_id=111))

    notes = _assignee_notes(db, seed["assignee_id"])
    assert len(notes) == 1
    channels = {d.channel for d in _deliveries(db, notes[0].id)}
    assert "whatsapp" not in channels, (
        "no linked RespondContact must suppress the whatsapp delivery even "
        "with the toggle on"
    )


def test_whatsapp_toggle_on_with_linked_contact_is_attempted(db):
    """Toggle ON AND a resolvable RespondContact -> a pending whatsapp
    delivery row is created (the actual send is async via the notification
    worker; 'attempted' = the delivery row exists for the worker to pick up)."""
    seed = _seed(
        db, whatsapp_on_assignment=True, link_assignee_whatsapp=True
    )
    service = ConversationSLATrackingService(db)

    service.create_tracking(_payload(seed, message_id=111))

    notes = _assignee_notes(db, seed["assignee_id"])
    assert len(notes) == 1
    deliveries = {d.channel: d for d in _deliveries(db, notes[0].id)}
    assert "whatsapp" in deliveries, (
        "toggle on + linked RespondContact must attempt a whatsapp delivery"
    )
    assert deliveries["whatsapp"].status == "pending"
