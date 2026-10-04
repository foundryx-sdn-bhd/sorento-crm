"""Conversation SLA resolve keeps assignee, agent, team set code and message.

UAC: documentation/plans/sla/keep-assignee-on-resolve-22sep-acceptance-criteria.md
     AC-KA-1..7 (AC-KA-8..10 are pinned in existing suites, reworded there
     rather than duplicated here: test_conversation_multi_open_consumer_audit.py,
     test_ticket_thread_read_scope.py, test_conversation_sla_list_history_filters.py)
PLAN: documentation/plans/sla/PLAN-keep-assignee-on-resolve-22sep.md

Owner ruling R5 (22 Sep 2026): resolve of a CONVERSATION tracker keeps
`assigned_to`, `assigned_to_id`, `agent_id`, `team_set_code` and `message_id`
for audit, matching what form-SLA trackers already did. The worklists (My
Pending, My Team, the inbox Mine tab) already gate on `is_resolved` rather
than on the assignee, so keeping these fields must NOT resurrect a resolved
ticket anywhere - AC-KA-4/5/6 pin that as an explicit regression test, not
just an assertion by reading the code.

Run:
    venv/bin/pytest tests/test_conversation_sla_keep_assignee_on_resolve.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team, TeamMember
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.conversation_inbox_service import TAB_MINE, list_conversations
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60100000071"
RESPOND_IO_ID = "10025699"
BASE = "/api/v1/sla-management/conversation-sla-tracking"

_ACTOR: dict = {"id": None, "name": "Test Actor"}


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    # The AC-M3 close webhook and the Respond close are resolve side effects,
    # not what this file tests.
    from app.config import settings

    monkeypatch.setattr(settings, "n8n_close_convo_webhook_url", None, raising=False)
    monkeypatch.delenv("N8N_CLOSE_CONVO_WEBHOOK_URL", raising=False)

    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _permissions(monkeypatch):
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


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
    _ACTOR["name"] = "Test Actor"


def _seed(db):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="ZZT-KA", name="ZZT Keep Assignee"))
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
            name="ZZT KA Contact",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(
        User(
            id=assignee_id,
            email="zzt-ka1@test.com",
            name="Agent One",
            respond_user_id="900101",
        )
    )
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_KA_AGENT", name="ZZT KA Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="ZZT KA Team"))
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=team_id, user_id=assignee_id))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_ka_general",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "assignee_id": assignee_id,
        "agent_id": agent_id,
        "team_id": team_id,
        "agent_code": "ZZT_KA_AGENT",
        "team_set_code": "zzt_ka_general",
    }


def _ticket(db, seed, *, source_message_id="wamid.ka-1"):
    return ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            assigned_to_id=seed["assignee_id"],
            contact_phone_number=PHONE,
            message_id=778899,
            source_message_id=source_message_id,
            source_message_text="Please connect me to a person.",
        )
    )


def _assert_fields_kept(tracking, seed) -> None:
    assert str(tracking.assigned_to_id) == str(seed["assignee_id"])
    assert tracking.assigned_to is not None
    assert str(tracking.agent_id) == str(seed["agent_id"])
    assert tracking.team_set_code == seed["team_set_code"]
    assert tracking.message_id == 778899


def test_ac_ka_1_resolve_via_the_ui_route_keeps_all_four_fields(client, db):
    seed = _seed(db)
    tracking = _ticket(db, seed)
    _act_as(seed["assignee_id"])

    resp = client.post(f"{BASE}/{tracking.id}/resolve")
    assert resp.status_code == 200, resp.text

    db.expire_all()
    fresh = ConversationSLATrackingService(db).get_tracking(str(tracking.id))
    assert fresh.is_resolved is True
    _assert_fields_kept(fresh, seed)


def test_ac_ka_2_resolve_via_the_n8n_integration_route_keeps_all_four_fields(client, db):
    seed = _seed(db)
    tracking = _ticket(db, seed)

    resp = client.post(f"{BASE}/integration/{tracking.id}", json={"is_resolved": True})
    assert resp.status_code == 200, resp.text

    db.expire_all()
    fresh = ConversationSLATrackingService(db).get_tracking(str(tracking.id))
    assert fresh.is_resolved is True
    _assert_fields_kept(fresh, seed)


def test_ac_ka_3_detail_response_carries_the_four_fields_non_null(client, db):
    seed = _seed(db)
    tracking = _ticket(db, seed)
    _act_as(seed["assignee_id"])
    ConversationSLATrackingService(db).update_tracking(
        str(tracking.id), ConversationSLATrackingUpdate(is_resolved=True)
    )

    resp = client.get(f"{BASE}/{tracking.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["assigned_to_id"] == seed["assignee_id"]
    assert body["agent_code"] == seed["agent_code"]
    assert body["team_set_code"] == seed["team_set_code"]
    assert body["message_id"] == 778899


def test_ac_ka_4_list_my_pending_drops_the_tracker_once_resolved(db):
    seed = _seed(db)
    tracking = _ticket(db, seed)
    service = ConversationSLATrackingService(db)

    before = {row["id"] for row in service.list_my_pending(seed["assignee_id"])}
    assert str(tracking.id) in before

    service.update_tracking(str(tracking.id), ConversationSLATrackingUpdate(is_resolved=True))

    after = {row["id"] for row in service.list_my_pending(seed["assignee_id"])}
    assert str(tracking.id) not in after


def test_ac_ka_5_list_team_pending_drops_the_tracker_once_resolved(db):
    seed = _seed(db)
    tracking = _ticket(db, seed)
    service = ConversationSLATrackingService(db)

    viewer_id = str(uuid.uuid4())
    db.add(User(id=viewer_id, email="zzt-ka-viewer@test.com", name="Team Viewer"))
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=seed["team_id"], user_id=viewer_id))
    db.commit()

    before = service.list_team_pending(viewer_id)
    assert str(tracking.id) in {row["id"] for row in before["data"]}

    service.update_tracking(str(tracking.id), ConversationSLATrackingUpdate(is_resolved=True))

    after = service.list_team_pending(viewer_id)
    assert str(tracking.id) not in {row["id"] for row in after["data"]}


def test_ac_ka_6_inbox_mine_tab_excludes_the_tracker_once_resolved(db):
    seed = _seed(db)
    _ticket(db, seed)
    service = ConversationSLATrackingService(db)

    before = list_conversations(db, viewer_user_id=seed["assignee_id"], tab=TAB_MINE)
    assert any(
        item["respond_io_id"] == RESPOND_IO_ID for item in before["items"]
    ), "the contact must be on the Mine tab while the ticket is open"

    # Resolve the one open ticket for this contact.
    from app.models.sla import ConversationSLATracking

    open_tracking = (
        db.query(ConversationSLATracking)
        .filter(ConversationSLATracking.respond_contact_id == seed["contact_id"])
        .one()
    )
    service.update_tracking(
        str(open_tracking.id), ConversationSLATrackingUpdate(is_resolved=True)
    )

    after = list_conversations(db, viewer_user_id=seed["assignee_id"], tab=TAB_MINE)
    assert not any(
        item["respond_io_id"] == RESPOND_IO_ID for item in after["items"]
    ), "a resolved ticket must not keep the contact on the assignee's Mine tab"


def test_ac_ka_7_resolve_then_reopen_keeps_the_fields_and_restores_my_pending(db):
    seed = _seed(db)
    tracking = _ticket(db, seed)
    service = ConversationSLATrackingService(db)

    service.update_tracking(str(tracking.id), ConversationSLATrackingUpdate(is_resolved=True))
    service.update_tracking(str(tracking.id), ConversationSLATrackingUpdate(is_resolved=False))

    db.expire_all()
    fresh = service.get_tracking(str(tracking.id))
    assert fresh.is_resolved is False
    _assert_fields_kept(fresh, seed)

    pending_ids = {row["id"] for row in service.list_my_pending(seed["assignee_id"])}
    assert str(tracking.id) in pending_ids


def test_s2_get_existing_assignee_for_contact_phone_ignores_a_resolved_tracker(db):
    """Fix round 1, S2: resolve keeps assigned_to_id for audit now, so this
    lookup must exclude a resolved row outright rather than falling back to
    the last resolver as the contact's "existing assignee"."""
    seed = _seed(db)
    tracking = _ticket(db, seed)
    service = ConversationSLATrackingService(db)

    assert service.get_existing_assignee_for_contact_phone(PHONE)["id"] == seed["assignee_id"]

    service.update_tracking(str(tracking.id), ConversationSLATrackingUpdate(is_resolved=True))

    assert service.get_existing_assignee_for_contact_phone(PHONE) is None
