"""GET /api/v1/sla-management/conversation-sla-tracking/{tracking_id}/ticket -
ROUTE-layer tests (S2.7, AC-C1).

Complements tests/test_intervention_ticket_detail.py (service-level, 5 tests)
by exercising the actual FastAPI route through a TestClient with real
dependency-injection wiring (mirrors tests/test_form_handling_lock_routes.py):

 - happy path: assignee opens their own ticket -> 200 with the drawer contract
 - auth denial: no principal at all -> 401/403, route body never runs
 - out-of-scope viewer: an outsider (not assignee, not admin, no shared team)
    -> 404, never a 403 (no existence leak, matches the service-level pin)

Run:
    venv/bin/pytest tests/test_intervention_ticket_detail_route.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team, TeamMember
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

PHONE = "+60123456789"
BASE = "/api/v1/sla-management/conversation-sla-tracking"


class _NoMessagesRespondClient:
    """No Respond.io network calls - the window falls back to 'no incoming,
    closed' immediately (matches test_intervention_ticket_detail.py)."""

    api_key = "x"

    @classmethod
    def for_identifier(cls, db, identifier):
        return cls()

    def list_messages(self, identifier, limit=50, cursor=None):
        return {"items": []}


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.integration_service.RespondClient",
        _NoMessagesRespondClient,
    )
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _no_admins(monkeypatch):
    """No role tables are seeded here - pin every actor as a non-admin so the
    outsider-scope test exercises the team-membership branch, not an accidental
    admin bypass."""
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())


def _seed(db, *, respond_io_id="10025531"):
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
            id=contact_id,
            phone_number=PHONE,
            name="Aisyah Rahman",
            respond_io_id=respond_io_id,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    outsider_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="cs1@test.com", name="Agent One", respond_user_id="900001"))
    db.add(User(id=outsider_id, email="outsider@test.com", name="Someone Else"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="CS_AGENT", name="CS Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="Customer Service - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="cs_general",
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
        "outsider_id": outsider_id,
        "agent_code": "CS_AGENT",
        "team_set_code": "cs_general",
    }


def _create_ticket(db, seed, **over):
    service = ConversationSLATrackingService(db)
    payload = ConversationSLATrackingCreate(
        agent_code=seed["agent_code"],
        team_set_code=seed["team_set_code"],
        policy_id=seed["policy_id"],
        assigned_to_id=seed["assignee_id"],
        contact_phone_number=PHONE,
        source_message_id="wamid.msg-1",
        source_message_text="Yes, please connect me to a person.",
        **over,
    )
    return service.create_tracking(payload)


_ACTOR: dict = {"id": None}


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
def anon_client(db):
    """No auth override at all - the real get_current_user runs and rejects
    the request before the route body executes."""
    from app.main import app
    from app.database import get_db

    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _act_as(user_id: str) -> None:
    _ACTOR["id"] = user_id
    _ACTOR["name"] = "Test Actor"


def test_happy_path_assignee_gets_the_drawer_contract(client, db):
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    _act_as(seed["assignee_id"])

    resp = client.get(f"{BASE}/{tracking.id}/ticket")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(tracking.id)
    assert body["contact_name"] == "Aisyah Rahman"
    assert body["source_message_id"] == "wamid.msg-1"
    assert body["can_send"] is True
    assert body["can_resolve"] is True
    assert body["is_assignee"] is True
    assert body["send_capabilities"] == ["text", "attachment"]
    assert "window" in body
    assert "chat_template" in body


def test_no_principal_at_all_is_rejected(anon_client, db):
    seed = _seed(db)
    tracking = _create_ticket(db, seed)

    resp = anon_client.get(f"{BASE}/{tracking.id}/ticket")

    assert resp.status_code in (401, 403), resp.text


def test_outsider_viewer_gets_404_not_a_leak(client, db):
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    _act_as(seed["outsider_id"])

    resp = client.get(f"{BASE}/{tracking.id}/ticket")

    assert resp.status_code == 404, resp.text
    # Never a 403 - a 403 would confirm the ticket exists to someone with no
    # visibility into it (matches the service-level get_ticket_detail pin).
    assert resp.status_code != 403


def test_unknown_tracking_id_is_also_404(client, db):
    seed = _seed(db)
    _act_as(seed["assignee_id"])

    resp = client.get(f"{BASE}/{uuid.uuid4()}/ticket")

    assert resp.status_code == 404, resp.text


def test_conversation_thread_route_offloads_to_a_threadpool(client, db, monkeypatch):
    """GET .../{id}/conversation is `async def` over a synchronous service read
    (``fetch_respond_conversation_for_tracking`` -> the local-first thread page,
    which may do one live Respond read when the contact has no stored rows), so
    it must run via ``run_in_threadpool`` rather than inline on the event loop.
    Same offload as its two siblings."""
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    _act_as(seed["assignee_id"])

    calls = []

    async def _fake_run_in_threadpool(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr("starlette.concurrency.run_in_threadpool", _fake_run_in_threadpool)

    resp = client.get(f"{BASE}/{tracking.id}/conversation")

    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []
    assert calls, "GET .../conversation must offload via run_in_threadpool, not call it inline"


def test_ticket_detail_route_offloads_to_a_threadpool(client, db, monkeypatch):
    """FINDING 7 (code review): this route is `async def` but called the sync
    Respond HTTP path (get_window_state_for -> RespondClient.list_messages,
    15s timeout) INLINE, blocking the event loop for the duration of that
    call. It must run_in_threadpool, mirroring the sibling
    POST .../ticket/send route."""
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    _act_as(seed["assignee_id"])

    calls = []

    async def _fake_run_in_threadpool(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr("starlette.concurrency.run_in_threadpool", _fake_run_in_threadpool)

    resp = client.get(f"{BASE}/{tracking.id}/ticket")

    assert resp.status_code == 200, resp.text
    assert calls, "GET .../ticket must offload via run_in_threadpool, not call it inline"


# --------------------------------------------------------------------------- #
# Assignee-only resolve (My Team can now open the drawer via the row-click
# fix - a visible teammate must not also be able to CLOSE someone else's
# clock from it). Mirrors can_user_act_on_tracking's visibility rule: a
# teammate is added to the SAME team as the assignee via TeamMember (a
# separate table from AgentTeam, which only routes the ticket itself).
# --------------------------------------------------------------------------- #


def _add_teammate(db, seed) -> str:
    teammate_id = str(uuid.uuid4())
    db.add(User(id=teammate_id, email="teammate@test.com", name="Team Mate"))
    db.commit()
    # AgentTeam already points seed["team_id"]-equivalent at the assignee's
    # routing team; TeamMember is the separate visibility table both
    # can_user_act_on_tracking and _visible_team_ids read.
    team_id = db.query(AgentTeam).filter(
        AgentTeam.code == seed["team_set_code"]
    ).first().team_id
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=team_id, user_id=teammate_id))
    db.add(TeamMember(id=str(uuid.uuid4()), team_id=team_id, user_id=seed["assignee_id"]))
    db.commit()
    return teammate_id


def test_can_resolve_is_false_for_a_visible_non_assignee_teammate(client, db):
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    teammate_id = _add_teammate(db, seed)
    _act_as(teammate_id)

    resp = client.get(f"{BASE}/{tracking.id}/ticket")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Visible (My Team can open the drawer), but not resolvable - reading is
    # wider than resolving.
    assert body["can_resolve"] is False
    assert body["assignee_name"] == "Agent One"
    # Drives the drawer's Takeover affordance: a viewer with no team-visible
    # queue for the assignee gets None here rather than a wrong team.
    assert body["is_assignee"] is False


def test_resolve_route_blocks_a_visible_non_assignee_teammate(client, db, monkeypatch):
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    teammate_id = _add_teammate(db, seed)
    _act_as(teammate_id)

    resp = client.post(f"{BASE}/{tracking.id}/resolve")

    assert resp.status_code == 400, resp.text
    db.expire_all()
    assert (
        ConversationSLATrackingService(db).get_tracking(str(tracking.id)).is_resolved
        is False
    )


def test_resolve_route_allows_the_assignee(client, db, monkeypatch):
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    _act_as(seed["assignee_id"])

    resp = client.post(f"{BASE}/{tracking.id}/resolve")

    assert resp.status_code == 200, resp.text


def test_resolve_route_allows_an_admin_who_is_not_the_assignee(client, db, monkeypatch):
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )
    seed = _seed(db)
    tracking = _create_ticket(db, seed)
    teammate_id = _add_teammate(db, seed)
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_role_slugs",
        lambda self, uid: {"admin"} if str(uid) == teammate_id else set(),
    )
    _act_as(teammate_id)

    resp = client.post(f"{BASE}/{tracking.id}/resolve")

    assert resp.status_code == 200, resp.text
