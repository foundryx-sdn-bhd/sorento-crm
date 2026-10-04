"""Composer message snippets: admin CRUD + the "/" picker (UAC AC-L4, S4.4).

ROUTE-layer tests for `/api/v1/sla-management/message-snippets`, covering the
happy path, permission denial on every verb, validation, the unique shortcut,
hard delete, and the picker's ticket-resolved bodies.

Run:
    venv/bin/pytest tests/test_message_snippets.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.message_snippet import MessageSnippet
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate
from app.services.sla_service import ConversationSLATrackingService
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

BASE = "/api/v1/sla-management/message-snippets"
PHONE = "+60100000004"


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


_ACTOR: dict = {"id": "actor-1", "name": "Agent One"}
_DENIED: set = set()


@pytest.fixture
def client(db, monkeypatch):
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key

    _DENIED.clear()
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug not in _DENIED,
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(_ACTOR)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(_ACTOR)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _deny(slug: str) -> None:
    _DENIED.add(slug)


def _add(db, **over) -> MessageSnippet:
    row = MessageSnippet(
        id=str(uuid.uuid4()),
        name=over.pop("name", "Stock check"),
        shortcut=over.pop("shortcut", None),
        body=over.pop("body", "We are checking stock now."),
        is_active=over.pop("is_active", True),
        **over,
    )
    db.add(row)
    db.commit()
    return row


# ----------------------------------------------------------------- list / get


def test_list_is_empty_before_anything_is_created(client):
    resp = client.get(BASE)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["empty"] is True
    assert body["pagination"]["total"] == 0


def test_list_returns_created_snippets(client, db):
    _add(db, name="Stock check", shortcut="stock")
    _add(db, name="Delivery ETA", shortcut="eta")

    body = client.get(BASE).json()

    assert [s["name"] for s in body["data"]] == ["Delivery ETA", "Stock check"]
    assert body["pagination"]["total"] == 2


def test_list_filters_by_query(client, db):
    _add(db, name="Stock check", shortcut="stock")
    _add(db, name="Delivery ETA", shortcut="eta")

    body = client.get(f"{BASE}?query=eta").json()

    assert [s["name"] for s in body["data"]] == ["Delivery ETA"]


def test_get_one(client, db):
    row = _add(db, name="Stock check")
    resp = client.get(f"{BASE}/{row.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Stock check"


def test_get_unknown_is_404(client):
    assert client.get(f"{BASE}/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- create/edit


def test_create(client, db):
    resp = client.post(
        BASE,
        json={
            "name": "Stock check",
            "shortcut": "stock",
            "body": "Hi $contact_name, we are checking stock.",
        },
    )

    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["name"] == "Stock check"
    assert payload["shortcut"] == "stock"
    assert payload["is_active"] is True
    assert db.query(MessageSnippet).count() == 1


def test_create_strips_a_leading_slash_from_the_shortcut(client):
    """The slash is composer syntax, not part of the keyword - storing it would
    make "//stock" the way to reach the snippet."""
    resp = client.post(BASE, json={"name": "Stock", "shortcut": "/stock", "body": "x"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["shortcut"] == "stock"


def test_create_with_a_blank_name_is_422(client):
    assert client.post(BASE, json={"name": "  ", "body": "x"}).status_code == 422


def test_create_with_a_blank_body_is_422(client):
    assert client.post(BASE, json={"name": "Stock", "body": "   "}).status_code == 422


def test_create_with_a_duplicate_shortcut_is_rejected(client, db):
    _add(db, name="Stock check", shortcut="stock")

    resp = client.post(BASE, json={"name": "Other", "shortcut": "STOCK", "body": "x"})

    assert resp.status_code == 409, resp.text


def test_update(client, db):
    row = _add(db, name="Stock check", shortcut="stock")

    resp = client.put(f"{BASE}/{row.id}", json={"name": "Stock status", "is_active": False})

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Stock status"
    assert resp.json()["is_active"] is False


def test_update_keeps_its_own_shortcut(client, db):
    """Saving a snippet without changing the shortcut must not clash with itself."""
    row = _add(db, name="Stock check", shortcut="stock")

    resp = client.put(f"{BASE}/{row.id}", json={"shortcut": "stock", "body": "new text"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["body"] == "new text"


def test_update_onto_another_shortcut_is_rejected(client, db):
    _add(db, name="Stock check", shortcut="stock")
    other = _add(db, name="Delivery ETA", shortcut="eta")

    resp = client.put(f"{BASE}/{other.id}", json={"shortcut": "stock"})

    assert resp.status_code == 409, resp.text


def test_update_unknown_is_404(client):
    assert client.put(f"{BASE}/{uuid.uuid4()}", json={"name": "x"}).status_code == 404


def test_the_database_refuses_a_duplicate_shortcut_on_its_own(db):
    """The case-insensitive uniqueness lived only in the migration, so a
    create_all schema (every test in this suite) had no index behind the
    service check - and the check alone races. Declared in __table_args__ the
    way ticket_comment.py does, the constraint is real everywhere."""
    from sqlalchemy.exc import IntegrityError

    _add(db, name="Stock check", shortcut="stock")

    db.add(
        MessageSnippet(
            id=str(uuid.uuid4()), name="Other", shortcut="STOCK", body="x"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_two_snippets_without_a_shortcut_are_fine(db):
    """The index is partial: a snippet is findable by name alone."""
    _add(db, name="One", shortcut=None)
    _add(db, name="Two", shortcut=None)

    assert db.query(MessageSnippet).count() == 2


def test_a_duplicate_that_beats_the_service_check_is_a_409_not_a_500(client, db):
    """The read-then-write check races. When the index catches what the check
    missed, the caller must still be told "that shortcut is taken", not handed
    an internal error."""
    from app.services import message_snippet_service as svc

    _add(db, name="Stock check", shortcut="stock")
    svc.MessageSnippetService._assert_shortcut_free = lambda *a, **k: None
    try:
        resp = client.post(BASE, json={"name": "Other", "shortcut": "STOCK", "body": "x"})
    finally:
        del svc.MessageSnippetService._assert_shortcut_free

    assert resp.status_code == 409, resp.text
    assert db.query(MessageSnippet).count() == 1


def test_an_update_onto_a_taken_shortcut_is_a_409_not_a_500(client, db):
    from app.services import message_snippet_service as svc

    _add(db, name="Stock check", shortcut="stock")
    other = _add(db, name="Delivery ETA", shortcut="eta")
    svc.MessageSnippetService._assert_shortcut_free = lambda *a, **k: None
    try:
        resp = client.put(f"{BASE}/{other.id}", json={"shortcut": "STOCK"})
    finally:
        del svc.MessageSnippetService._assert_shortcut_free

    assert resp.status_code == 409, resp.text


# --------------------------------------------------------------------- delete


def test_delete_is_a_hard_delete(client, db):
    row = _add(db, name="Stock check")

    resp = client.delete(f"{BASE}/{row.id}")

    assert resp.status_code == 200, resp.text
    assert db.query(MessageSnippet).count() == 0


def test_delete_unknown_is_404(client):
    assert client.delete(f"{BASE}/{uuid.uuid4()}").status_code == 404


# ----------------------------------------------------------------- auth denial


def test_list_without_the_view_permission_is_403(client):
    _deny("sla_management.message_snippets.view")
    assert client.get(BASE).status_code == 403


def test_create_without_the_add_permission_is_403(client):
    _deny("sla_management.message_snippets.add")
    assert client.post(BASE, json={"name": "x", "body": "y"}).status_code == 403


def test_update_without_the_edit_permission_is_403(client, db):
    row = _add(db, name="Stock check")
    _deny("sla_management.message_snippets.edit")
    assert client.put(f"{BASE}/{row.id}", json={"name": "y"}).status_code == 403


def test_delete_without_the_delete_permission_is_403(client, db):
    row = _add(db, name="Stock check")
    _deny("sla_management.message_snippets.delete")
    assert client.delete(f"{BASE}/{row.id}").status_code == 403


def test_the_composer_picker_needs_only_the_view_permission(client, db):
    """Everyone who works tickets inserts snippets; only admins manage them."""
    _add(db, name="Stock check", shortcut="stock")
    _deny("sla_management.message_snippets.add")
    _deny("sla_management.message_snippets.edit")

    resp = client.get(f"{BASE}/select")

    assert resp.status_code == 200, resp.text
    assert [s["name"] for s in resp.json()] == ["Stock check"]


# ---------------------------------------------------------- the "/" picker


def _seed_ticket(db):
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
            respond_io_id="10025599",
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(User(id=assignee_id, email="cs-snip@test.com", name="Agent One"))
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="CS_SNIP", name="CS Snip"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name="Customer Service - Tier 1"))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="cs_snip_set",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    tracking = ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code="CS_SNIP",
            team_set_code="cs_snip_set",
            policy_id=policy_id,
            assigned_to_id=assignee_id,
            contact_phone_number=PHONE,
            source_message_id="wamid.snip-1",
            source_message_text="Please help me.",
        )
    )
    return tracking, assignee_id


def test_picker_lists_active_snippets_only(client, db):
    _add(db, name="Stock check", shortcut="stock")
    _add(db, name="Retired wording", is_active=False)

    items = client.get(f"{BASE}/select").json()

    assert [s["name"] for s in items] == ["Stock check"]


def test_picker_filters_on_name_and_shortcut(client, db):
    _add(db, name="Stock check", shortcut="stk")
    _add(db, name="Delivery ETA", shortcut="eta")

    assert [s["name"] for s in client.get(f"{BASE}/select?query=stk").json()] == [
        "Stock check"
    ]
    assert [s["name"] for s in client.get(f"{BASE}/select?query=deliv").json()] == [
        "Delivery ETA"
    ]


def test_picker_resolves_variables_against_the_ticket(client, db):
    tracking, assignee_id = _seed_ticket(db)
    _add(
        db,
        name="Greeting",
        shortcut="hi",
        body="Hi $contact_name, $assignee_name here about $ticket_ref. Deposit $50.",
    )
    _ACTOR["id"] = assignee_id
    _ACTOR["name"] = "Agent One"

    items = client.get(f"{BASE}/select?tracking_id={tracking.id}").json()

    assert len(items) == 1
    resolved = items[0]["resolved_body"]
    assert resolved.startswith("Hi Aisyah Rahman, Agent One here about ENQ-")
    # Unknown token left literal (AC-L4).
    assert "Deposit $50." in resolved
    # The stored wording is untouched: an edit to the snippet still reaches
    # every future insert.
    assert items[0]["body"].startswith("Hi $contact_name")

    _ACTOR["id"] = "actor-1"
    _ACTOR["name"] = "Agent One"


def test_picker_without_a_ticket_falls_back_to_neutral_wording(client, db):
    _add(db, name="Greeting", body="Hi $contact_name")

    items = client.get(f"{BASE}/select").json()

    assert items[0]["resolved_body"] == "Hi there"


def test_picker_for_a_ticket_the_viewer_cannot_act_on_is_404(client, db):
    tracking, _assignee_id = _seed_ticket(db)
    _add(db, name="Greeting", body="Hi $contact_name")
    _ACTOR["id"] = str(uuid.uuid4())  # an outsider

    resp = client.get(f"{BASE}/select?tracking_id={tracking.id}")

    _ACTOR["id"] = "actor-1"
    assert resp.status_code == 404, resp.text
