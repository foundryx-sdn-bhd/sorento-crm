"""Contact / resolved / resolved-by filters on the conversation SLA listing.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-M2 (the drawer's "View history" link and the widget's "Recently resolved"
            link both land on the SLA tracking listing PRE-FILTERED - server-side,
            through the same list query, never a client-side slice)
PLAN: documentation/plans/sla/PLAN-conversation-intervention-tickets.md (S4.5)

Run:
    venv/bin/pytest tests/test_conversation_sla_list_history_filters.py -q
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE_A = "+60100000004"
PHONE_B = "+60100000003"
RESPOND_IO_A = "10025601"
RESPOND_IO_B = "10025602"


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    # The AC-M3 close webhook is a resolve side effect, not what this file tests.
    from app.config import settings

    monkeypatch.setattr(settings, "n8n_close_convo_webhook_url", None, raising=False)
    monkeypatch.delenv("N8N_CLOSE_CONVO_WEBHOOK_URL", raising=False)

    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


def _seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="ZZT-HIST", name="ZZT History"))
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
    contact_a = str(uuid.uuid4())
    contact_b = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_a,
            phone_number=PHONE_A,
            name="ZZT Contact A",
            respond_io_id=RESPOND_IO_A,
            session_vars={},
        )
    )
    db.add(
        RespondContact(
            id=contact_b,
            phone_number=PHONE_B,
            name="ZZT Contact B",
            respond_io_id=RESPOND_IO_B,
            session_vars={},
        )
    )
    user_one = str(uuid.uuid4())
    user_two = str(uuid.uuid4())
    db.add(User(id=user_one, email="zzt-hist1@test.com", name="Agent One", respond_user_id="910001"))
    db.add(User(id=user_two, email="zzt-hist2@test.com", name="Agent Two", respond_user_id="910002"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_HIST_AGENT", name="ZZT History Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT History Team"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_hist_general",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_a": contact_a,
        "contact_b": contact_b,
        "user_one": user_one,
        "user_two": user_two,
        "agent_code": "ZZT_HIST_AGENT",
        "team_set_code": "zzt_hist_general",
    }


def _ticket(db, seed, *, phone, assignee, source_message_id):
    return ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            assigned_to_id=assignee,
            contact_phone_number=phone,
            source_message_id=source_message_id,
            source_message_text="Please connect me to a person.",
        )
    )


def _resolve(db, tracking_id):
    return ConversationSLATrackingService(db).update_tracking(
        str(tracking_id), ConversationSLATrackingUpdate(is_resolved=True)
    )


def _ids(result) -> set[str]:
    return {str(row["id"]) for row in result["data"]}


def test_the_contact_filter_returns_only_that_contacts_tickets(db):
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    a2 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a2")
    b1 = _ticket(db, seed, phone=PHONE_B, assignee=seed["user_two"], source_message_id="m-b1")

    result = ConversationSLATrackingService(db).list_tracking(contact=seed["contact_a"])

    assert _ids(result) == {str(a1.id), str(a2.id)}
    assert str(b1.id) not in _ids(result)


def test_the_contact_filter_accepts_the_respond_io_id_so_no_uuid_reaches_the_url(db):
    """The drawer holds the contact's respond_io_id, never the CRM UUID - the
    link it builds must work with what it holds."""
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    _ticket(db, seed, phone=PHONE_B, assignee=seed["user_two"], source_message_id="m-b1")

    result = ConversationSLATrackingService(db).list_tracking(contact=RESPOND_IO_A)

    assert _ids(result) == {str(a1.id)}


def test_the_contact_filter_accepts_a_phone_number(db):
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    _ticket(db, seed, phone=PHONE_B, assignee=seed["user_two"], source_message_id="m-b1")

    result = ConversationSLATrackingService(db).list_tracking(contact=PHONE_A)

    assert _ids(result) == {str(a1.id)}


def test_an_unknown_contact_returns_nothing_rather_than_everything(db):
    """A filter that cannot be honoured must not silently widen: showing every
    contact's tickets under a "this contact" link is the worse failure."""
    seed = _seed(db)
    _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")

    result = ConversationSLATrackingService(db).list_tracking(contact="+60000000000")

    assert result["data"] == []
    assert result["pagination"]["total"] == 0


def test_is_resolved_splits_open_from_resolved(db):
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    a2 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a2")
    _resolve(db, a1.id)

    service = ConversationSLATrackingService(db)
    resolved = service.list_tracking(contact=seed["contact_a"], is_resolved=True)
    still_open = service.list_tracking(contact=seed["contact_a"], is_resolved=False)
    both = service.list_tracking(contact=seed["contact_a"])

    assert _ids(resolved) == {str(a1.id)}
    assert _ids(still_open) == {str(a2.id)}
    assert _ids(both) == {str(a1.id), str(a2.id)}


def test_resolved_by_narrows_to_one_resolver(db):
    """AC-KA-10: resolve keeps assigned_to_id on a conversation ticket (owner
    ruling R5, 22 Sep 2026), so both resolved_by and assigned_to answer "mine"
    after the fact - here they agree because each ticket's resolver was also
    its assignee."""
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    b1 = _ticket(db, seed, phone=PHONE_B, assignee=seed["user_two"], source_message_id="m-b1")
    _resolve(db, a1.id)
    _resolve(db, b1.id)

    service = ConversationSLATrackingService(db)
    mine = service.list_tracking(is_resolved=True, resolved_by=seed["user_one"])

    assert _ids(mine) == {str(a1.id)}
    assert _ids(
        service.list_tracking(is_resolved=True, assigned_to=seed["user_one"])
    ) == {str(a1.id)}, "the assignee survives resolve - assigned_to must find it too"


def test_resolved_by_accepts_a_respond_user_id_or_email(db):
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    _resolve(db, a1.id)

    service = ConversationSLATrackingService(db)
    assert _ids(service.list_tracking(resolved_by="910001")) == {str(a1.id)}
    assert _ids(service.list_tracking(resolved_by="zzt-hist1@test.com")) == {str(a1.id)}


def test_an_unknown_resolver_returns_nothing(db):
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    _resolve(db, a1.id)

    result = ConversationSLATrackingService(db).list_tracking(resolved_by="__no_such_user__")

    assert result["data"] == []


def test_form_sla_rows_never_appear_under_a_contact_filter(db):
    """AC-F3: the two families share the table; the contact link is conversation
    scope only."""
    seed = _seed(db)
    a1 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a1")
    a2 = _ticket(db, seed, phone=PHONE_A, assignee=seed["user_one"], source_message_id="m-a2")
    db.query(type(a2)).filter(type(a2).id == a2.id).update(
        {"source_entity_type": "complaint", "source_entity_id": str(uuid.uuid4())}
    )
    db.commit()

    result = ConversationSLATrackingService(db).list_tracking(contact=seed["contact_a"])

    assert _ids(result) == {str(a1.id)}


def test_the_me_sentinel_expands_to_the_calling_user(db):
    from app.api.v1.sla.sla_tracking import _resolved_by_param

    assert _resolved_by_param("me", {"id": "user-1"}) == "user-1"
    assert _resolved_by_param("  ", {"id": "user-1"}) is None
    assert _resolved_by_param("910001", {"id": "user-1"}) == "910001"
    # An api-key principal with no acting user filters to nothing rather than
    # widening to everybody's resolutions.
    assert _resolved_by_param("me", {}) == "__no_such_user__"
