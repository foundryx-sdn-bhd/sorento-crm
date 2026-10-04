"""Multi-open consumer audit (slice S2.3 of PLAN-conversation-intervention-tickets,
UAC AC-F1 / AC-F2 / AC-F3 / AC-E4).

S2a dropped the migration-180 "one open row per contact" singleton index: a
contact can now hold several open conversation-SLA tickets at once. This file
pins the explicit multi-row semantic every "the open tracking for this contact"
consumer got as part of that audit:

 - get_preferred_tracking_for_contact / get_tracking_by_contact_and_policy /
    get_open_tracking_by_contact / get_existing_assignee_for_contact_phone:
    documented MOST-RECENT-OPEN reductions (never a silent unordered .first()).
 - escalate_tracking(tracking_id=...): a real bug fix - escalating a specific
    ticket must target THAT row, never re-resolve by (contact, policy) and risk
    hitting a sibling.
 - sync_assignee_from_respond: retired to a deprecated no-op for conversation
    tickets (AC-F2) - CRM is the per-ticket assignee authority now.
 - resolve's Respond-close side effect: gated so a sibling ticket staying open
    is never orphaned by another ticket's resolve (AC-C3).
 - conversation_tracking_scope family separation stays intact under multi-open
    (AC-F3).

Run: pytest tests/test_conversation_multi_open_consumer_audit.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


PHONE = "+60170000542"


def _seed(db) -> dict:
    """Policy + tiers + contact + user + agent/team, mirroring
    test_conversation_sla_idempotent_create._seed so create_tracking works."""
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="AUDIT", name="Audit Policy"))
    for lvl, (resp_h, res_h) in {1: (4, 24), 2: (4, 24), 3: (4, 24)}.items():
        db.add(
            SLAPolicyTier(
                id=str(uuid.uuid4()),
                policy_id=policy_id,
                tier_level=lvl,
                tier_name=f"Tier {lvl}",
                response_hours=resp_h,
                resolution_hours=res_h,
            )
        )
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id, phone_number=PHONE, name="Audit Contact", session_vars={}
        )
    )
    user_id = str(uuid.uuid4())
    db.add(User(id=user_id, email="audit-agent1@test.com", name="Audit Agent", respond_user_id="900101"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="AUDITAGENT", name="Audit Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="Audit Team"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="audit_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "agent_code": "AUDITAGENT",
        "team_set_code": "audit_set",
    }


def _payload(seed, *, message_id) -> ConversationSLATrackingCreate:
    return ConversationSLATrackingCreate(
        agent_code=seed["agent_code"],
        team_set_code=seed["team_set_code"],
        policy_id=seed["policy_id"],
        current_tier=1,
        assigned_to_id=seed["user_id"],
        contact_phone_number=PHONE,
        message_id=message_id,
    )


def _form_row(db, seed, *, is_resolved=False, src="complaint") -> ConversationSLATracking:
    row = ConversationSLATracking(
        id=str(uuid.uuid4()),
        policy_id=seed["policy_id"],
        current_tier=1,
        assigned_to_id=seed["user_id"],
        due_at=datetime(2026, 6, 1, 12, 0, 0),
        is_resolved=is_resolved,
        respond_contact_id=seed["contact_id"],
        source_entity_type=src,
        source_entity_id=str(uuid.uuid4()),
    )
    db.add(row)
    db.commit()
    return row


def _two_open_tickets(db, seed) -> tuple[ConversationSLATracking, ConversationSLATracking]:
    """T1 created first, T2 created strictly later (distinct source_message_id via
    distinct message_id), both open, same contact and policy."""
    service = ConversationSLATrackingService(db)
    t1 = service.create_tracking(_payload(seed, message_id=1001))
    t1_id = str(t1.id)
    # Force a distinguishable created_at ordering (DB clock resolution could tie
    # two inserts in the same millisecond otherwise).
    db.execute(
        text("UPDATE conversation_sla_tracking SET created_at = :ts WHERE id = :id"),
        {"ts": datetime(2026, 1, 1, 8, 0, 0), "id": t1_id},
    )
    db.commit()
    t2 = service.create_tracking(_payload(seed, message_id=1002))
    t2_id = str(t2.id)
    db.execute(
        text("UPDATE conversation_sla_tracking SET created_at = :ts WHERE id = :id"),
        {"ts": datetime(2026, 1, 1, 9, 0, 0), "id": t2_id},
    )
    db.commit()
    return (
        db.query(ConversationSLATracking).get(t1_id),
        db.query(ConversationSLATracking).get(t2_id),
    )


# ---------------------------------------------------------------------------
# AC-F1: MOST-RECENT-OPEN readers (documented, not a silent .first())
# ---------------------------------------------------------------------------


def test_get_preferred_tracking_for_contact_picks_most_recently_created_open(db):
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)
    service = ConversationSLATrackingService(db)

    contact = db.query(RespondContact).get(seed["contact_id"])
    found = service.get_preferred_tracking_for_contact(contact)

    assert str(found.id) == str(t2.id)  # newer of the two open tickets
    assert str(found.id) != str(t1.id)

    # Wrapper used by the external GET-by-contact endpoint and next-assignee's
    # "is_already_assigned" signal - same reduction, exercised via a different path.
    assert str(service.get_tracking_by_contact_phone(PHONE).id) == str(t2.id)


def test_get_tracking_by_contact_and_policy_picks_most_recently_created_open(db):
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)
    service = ConversationSLATrackingService(db)

    found = service.get_tracking_by_contact_and_policy(seed["contact_id"], seed["policy_id"])

    assert str(found.id) == str(t2.id)
    assert str(found.id) != str(t1.id)


def test_get_open_tracking_by_contact_picks_most_recently_created_open(db):
    """Documents the accepted interim limitation of n8n's contact-keyed
    POST /integration/escalate: with 2 open tickets, only the newer one is
    reachable through this contact-only resolution path."""
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)
    service = ConversationSLATrackingService(db)

    found = service.get_open_tracking_by_contact(seed["contact_id"])

    assert str(found.id) == str(t2.id)
    assert str(found.id) != str(t1.id)


def test_get_existing_assignee_for_contact_phone_picks_most_recent_tickets_assignee(db):
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)
    other_user_id = str(uuid.uuid4())
    db.add(User(id=other_user_id, email="audit-agent2@test.com", name="Second Agent", respond_user_id="900102"))
    db.commit()
    t2.assigned_to_id = other_user_id
    db.commit()

    service = ConversationSLATrackingService(db)
    info = service.get_existing_assignee_for_contact_phone(PHONE)

    assert info is not None
    assert info["id"] == other_user_id  # T2's (newer ticket's) assignee, not T1's


# ---------------------------------------------------------------------------
# AC-F1: escalate_tracking(tracking_id=...) targets the exact row (bug fix)
# ---------------------------------------------------------------------------


def test_escalate_tracking_by_tracking_id_targets_exact_ticket_not_sibling(db):
    """The UI escalate route (POST /{tracking_id}/escalate) is keyed by a URL
    tracking_id. Before this fix, escalate_tracking re-resolved by
    (respond_contact_id, policy_id) internally and could silently escalate
    whichever sibling ticket was most-recently created instead of the one the
    caller asked for."""
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)  # t2 is "most recent" - the wrong target
    service = ConversationSLATrackingService(db)

    escalated = service.escalate_tracking(
        respond_contact_id=seed["contact_id"],
        policy_id=seed["policy_id"],
        current_tier=2,
        tracking_id=str(t1.id),
    )

    assert str(escalated.id) == str(t1.id)
    db.refresh(t1)
    db.refresh(t2)
    assert t1.current_tier == 2
    assert t1.escalated_at is not None
    assert t2.current_tier == 1  # sibling untouched
    assert t2.escalated_at is None


def test_escalate_tracking_without_tracking_id_keeps_legacy_contact_policy_fallback(db):
    """Back-compat: callers that never pass tracking_id keep the pre-fix
    (respond_contact_id, policy_id) resolution - documented as MOST-RECENT-OPEN."""
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)
    service = ConversationSLATrackingService(db)

    escalated = service.escalate_tracking(
        respond_contact_id=seed["contact_id"],
        policy_id=seed["policy_id"],
        current_tier=2,
    )

    assert str(escalated.id) == str(t2.id)  # most-recently-created open ticket
    db.refresh(t1)
    assert t1.current_tier == 1  # untouched


# ---------------------------------------------------------------------------
# AC-F2: sync_assignee_from_respond retired for conversation tickets
# ---------------------------------------------------------------------------


def test_sync_assignee_from_respond_is_deprecated_noop_for_conversation_ticket(db):
    seed = _seed(db)
    t1, _t2 = _two_open_tickets(db, seed)
    service = ConversationSLATrackingService(db)

    with patch("app.services.integration_service.RespondClient") as mock_client_cls:
        result = service.sync_assignee_from_respond(str(t1.id))

    assert result["updated"] is False
    assert result["deprecated"] is True
    mock_client_cls.assert_not_called()  # no Respond.io HTTP call at all
    db.refresh(t1)
    assert t1.assigned_to_id == seed["user_id"]  # untouched


def test_sync_assignee_from_respond_still_works_for_form_sla_row(db):
    """Form-SLA rows are outside this deprecation - AC-F3 isolation."""
    seed = _seed(db)
    form = _form_row(db, seed, is_resolved=False)
    service = ConversationSLATrackingService(db)

    with patch("app.services.integration_service.RespondClient") as mock_client_cls:
        mock_client_cls.return_value.get_contact_by_phone.return_value = {"assignee": None}
        result = service.sync_assignee_from_respond(str(form.id))

    assert result.get("deprecated") is not True
    mock_client_cls.return_value.get_contact_by_phone.assert_called_once()


# ---------------------------------------------------------------------------
# AC-C3 / AC-F1: resolve's Respond-close side effect is sibling-aware
# ---------------------------------------------------------------------------


def _enqueued(enqueue, job) -> list:
    """The enqueue calls that queued THIS job. A resolve fires more than one
    post-commit job onto the same queue, so a total call count says nothing
    about the one under test."""
    return [call for call in enqueue.call_args_list if call.args and call.args[0] is job]


def test_resolve_skips_respond_close_when_sibling_ticket_still_open(db):
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)
    service = ConversationSLATrackingService(db)

    with patch("app.services.queue_service.enqueue_job") as enqueue:
        service.update_tracking(str(t1.id), ConversationSLATrackingUpdate(is_resolved=True))

    from app.tasks.respond_io_tasks import (
        close_respond_conversation,
        send_ticket_resolved_message,
    )

    # T2 still open - the Respond conversation must stay open.
    assert _enqueued(enqueue, close_respond_conversation) == []
    # PLAN-ticket-resolved-closing-message: the closing message is per TICKET, so
    # T1 still gets exactly one while its sibling stays open.
    closing = _enqueued(enqueue, send_ticket_resolved_message)
    assert len(closing) == 1
    assert closing[0].args[1] == str(t1.id)
    db.refresh(t1)
    db.refresh(t2)
    assert t1.is_resolved is True
    assert t2.is_resolved is False


def test_resolve_still_closes_respond_when_last_open_ticket_for_contact(db):
    """Preserves the pre-multi-open behaviour byte-identically for the common
    single-ticket case."""
    seed = _seed(db)
    service = ConversationSLATrackingService(db)
    t1 = service.create_tracking(_payload(seed, message_id=2001))

    with patch("app.services.queue_service.enqueue_job") as enqueue:
        service.update_tracking(str(t1.id), ConversationSLATrackingUpdate(is_resolved=True))

    from app.tasks.respond_io_tasks import (
        close_respond_conversation,
        send_ticket_resolved_message,
    )

    close_calls = _enqueued(enqueue, close_respond_conversation)
    assert len(close_calls) == 1
    assert close_calls[0].args[1] == str(t1.id)
    # PLAN-ticket-resolved-closing-message: one closing message for the ticket,
    # alongside (not instead of) the Respond close.
    closing = _enqueued(enqueue, send_ticket_resolved_message)
    assert len(closing) == 1
    assert closing[0].args[1] == str(t1.id)


# ---------------------------------------------------------------------------
# AC-F3: form-SLA / conversation-SLA family separation unaffected by multi-open
# ---------------------------------------------------------------------------


def test_conversation_scope_readers_never_surface_form_rows_alongside_open_tickets(db):
    seed = _seed(db)
    t1, t2 = _two_open_tickets(db, seed)
    form = _form_row(db, seed, is_resolved=False)
    service = ConversationSLATrackingService(db)

    # Contact-keyed conversation readers must never return the form row, even
    # though it shares respond_contact_id and sits alongside 2 open conversation
    # tickets.
    preferred = service.get_preferred_tracking_for_contact(
        db.query(RespondContact).get(seed["contact_id"])
    )
    assert str(preferred.id) in {str(t1.id), str(t2.id)}
    assert str(preferred.id) != str(form.id)

    open_by_contact = service.get_open_tracking_by_contact(seed["contact_id"])
    assert str(open_by_contact.id) != str(form.id)

    # The full conversation worklist (list_tracking, scope="conversation") counts
    # both open tickets and excludes the form row.
    listed = service.list_tracking(scope="conversation", limit=100)
    listed_ids = {row["id"] for row in listed["data"]}
    assert str(t1.id) in listed_ids
    assert str(t2.id) in listed_ids
    assert str(form.id) not in listed_ids


def test_form_row_resolve_is_never_gated_by_conversation_siblings(db):
    """AC-F3: the AC-C3 sibling-open guard on resolve is conversation-scope only -
    a form row resolving must never consult (or be blocked by) open conversation
    tickets for the same contact."""
    seed = _seed(db)
    _t1, _t2 = _two_open_tickets(db, seed)  # 2 open conversation siblings
    form = _form_row(db, seed, is_resolved=False)
    service = ConversationSLATrackingService(db)

    with patch("app.services.queue_service.enqueue_job") as enqueue:
        service.update_tracking(str(form.id), ConversationSLATrackingUpdate(is_resolved=True))

    enqueue.assert_not_called()  # form rows never trigger the Respond-close side effect
    db.refresh(form)
    assert form.is_resolved is True
    # AC-KA-8: both the form path and the conversation path keep assignee/agent/
    # team fields (audit trail) on resolve (owner ruling R5, 22 Sep 2026). This
    # only re-confirms the form side; the two paths stay otherwise separate
    # (the sibling-open guard above is conversation-scope only).
    assert form.assigned_to_id == seed["user_id"]
