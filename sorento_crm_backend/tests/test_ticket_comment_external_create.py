"""n8n writes a comment INTO the CRM, and the CRM mirrors it to Respond.

POST /api/v1/external/chat-history/{contact_ref}/comments is the lane
`sub-add-comment-respond` calls instead of Respond's own comment endpoint, so a
bot / flow comment is saved in the CRM first (source of truth, renders in every
open ticket drawer for the contact) and only then pushed to the Respond inbox.

The n8n sub holds a Respond user id for its mention, not a CRM users.id: the
route maps `mentioned_respond_user_ids` onto CRM users where a mapping exists
(so the in-app mention notification fires) and still tags the raw Respond id
in the mirror text either way, so the Respond agent is notified exactly as
before.

Run:
    venv/bin/pytest tests/test_ticket_comment_external_create.py -q
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.access import RespondContact
from app.models.ticket_comment import ConversationTicketComment
from app.models.user import User
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

RESPOND_IO_ID = "900000008"
PHONE = "+60160000509"
AGENT_RESPOND_ID = "971724"
URL = f"/api/v1/external/chat-history/{RESPOND_IO_ID}/comments"


class _RecordingMirror:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, db, comment, *, identifier, respond_mentions=None, **_):
        from app.services.ticket_comment_service import build_mirror_text

        text_ = build_mirror_text(db, comment.body or "", list(comment.mentioned_user_ids or []))
        self.calls.append(
            {"identifier": identifier, "text": text_, "respond_mentions": list(respond_mentions or [])}
        )
        return True


@pytest.fixture
def mirror(monkeypatch):
    rec = _RecordingMirror()
    monkeypatch.setattr("app.services.ticket_comment_service.mirror_comment_to_respond", rec)
    return rec


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture
def actor(db) -> User:
    """The act-as principal behind the API key: a real users row."""
    u = User(
        id=str(uuid.uuid4()),
        email=f"n8n-{uuid.uuid4().hex[:6]}@zzt.test",
        name="n8n Integration",
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def client(db, actor):
    def _user():
        return {"id": actor.id, "auth_method": "api_key"}

    def _db():
        yield db

    app.dependency_overrides[get_external_api_user] = _user
    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_db] = _db
    with external_permissions_granted():
        yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_contact(db) -> str:
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            respond_io_id=RESPOND_IO_ID,
            phone_number=PHONE,
            name="Aisyah Rahman",
            session_vars={},
        )
    )
    db.commit()
    return contact_id


def _seed_mapped_agent(db) -> User:
    u = User(
        id=str(uuid.uuid4()),
        email=f"agent-{uuid.uuid4().hex[:6]}@zzt.test",
        name="Li Hua",
        respond_user_id=AGENT_RESPOND_ID,
    )
    db.add(u)
    db.commit()
    return u


def test_the_comment_is_saved_in_the_crm_then_mirrored(client, db, mirror, actor):
    contact_id = _seed_contact(db)

    resp = client.post(URL, json={"body": "Routed to the IT team.", "author_name": "Sorento Bot"})

    assert resp.status_code == 201, resp.text
    row = db.query(ConversationTicketComment).one()
    assert row.respond_contact_id == contact_id
    assert row.tracking_id is None, "contact-scoped, like an inbox note"
    assert row.source == "crm"
    assert row.body == "Routed to the IT team."
    assert row.author_id == actor.id
    assert row.author_name == "Sorento Bot", "the bot's name, not the act-as user's"
    assert resp.json()["author_name"] == "Sorento Bot"
    # Saved first, mirrored second: the mirror sees the committed row.
    assert [c["identifier"] for c in mirror.calls] == [RESPOND_IO_ID]


def test_a_mapped_respond_user_becomes_a_crm_mention_and_stays_tagged_in_respond(client, db, mirror):
    _seed_contact(db)
    agent = _seed_mapped_agent(db)

    resp = client.post(
        URL,
        json={"body": "Please follow up.", "mentioned_respond_user_ids": [AGENT_RESPOND_ID]},
    )

    assert resp.status_code == 201, resp.text
    row = db.query(ConversationTicketComment).one()
    assert list(row.mentioned_user_ids) == [agent.id], "mapped onto the CRM user"
    assert resp.json()["mentioned_names"] == ["Li Hua"]
    assert mirror.calls[0]["respond_mentions"] == [AGENT_RESPOND_ID]


def test_an_unmapped_respond_user_is_still_tagged_in_the_mirror_and_reported(client, db, mirror):
    _seed_contact(db)

    resp = client.post(
        URL, json={"body": "Please follow up.", "mentioned_respond_user_ids": ["555000"]}
    )

    assert resp.status_code == 201, resp.text
    row = db.query(ConversationTicketComment).one()
    assert list(row.mentioned_user_ids or []) == [], "nobody in the CRM to notify"
    assert mirror.calls[0]["respond_mentions"] == ["555000"], "Respond still tags the agent"
    assert resp.json()["unmapped_respond_user_ids"] == ["555000"]


def test_the_contact_resolves_by_phone_too(client, db, mirror):
    contact_id = _seed_contact(db)

    resp = client.post(f"/api/v1/external/chat-history/{PHONE}/comments", json={"body": "hi"})

    assert resp.status_code == 201, resp.text
    assert db.query(ConversationTicketComment).one().respond_contact_id == contact_id


def test_an_unknown_contact_is_a_404_and_nothing_is_saved(client, db, mirror):
    resp = client.post("/api/v1/external/chat-history/999999999/comments", json={"body": "hi"})

    assert resp.status_code == 404, resp.text
    assert db.query(ConversationTicketComment).count() == 0
    assert mirror.calls == []


def test_a_blank_body_is_refused(client, db, mirror):
    _seed_contact(db)

    resp = client.post(URL, json={"body": "   "})

    assert resp.status_code == 422, resp.text
    assert db.query(ConversationTicketComment).count() == 0


def test_without_the_api_key_the_route_denies(db):
    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    try:
        resp = TestClient(app).post(URL, json={"body": "hi"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code in (401, 403), resp.text
