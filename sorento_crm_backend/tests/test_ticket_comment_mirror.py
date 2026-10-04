"""Best-effort mirror of a CRM comment to Respond.io (UAC AC-L2, slice S4.3).

Respond has no comment read-back API, so the CRM DB is the source of truth and
this mirror exists purely so staff still living in the Respond inbox see the
note. It must therefore:

 - POST the comment text to Respond's contact comment endpoint
 - render mentioned users that HAVE a Respond mapping as `{{@user.<id>}}` and
    leave unmapped ones as the plain "@Name" the author typed
 - write an `integration_log` outbox row on success AND on failure (repo rule)
 - never raise: the comment is already committed

Run:
    venv/bin/pytest tests/test_ticket_comment_mirror.py -q
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.access import RespondContact
from app.models.integration import IntegrationLog
from app.models.ticket_comment import ConversationTicketComment
from app.models.user import User
from app.services.ticket_comment_service import (
    build_mirror_text,
    mirror_comment_to_respond,
)
from tests._pg_fixture import blank_session

PHONE = "+60100000079"
RESPOND_IO_ID = "10025599"


@pytest.fixture
def db():
    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


class _RecordingClient:
    def __init__(self, *, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def create_comment(self, identifier: str, text: str) -> dict:
        self.calls.append((identifier, text))
        if self.fail:
            raise RuntimeError("401 Unauthorized")
        return {"id": "respond-comment-99"}


def _seed(db):
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            phone_number=PHONE,
            name="Aisyah Rahman",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    mapped_id = str(uuid.uuid4())
    unmapped_id = str(uuid.uuid4())
    db.add(User(id=mapped_id, email="mapped@test.com", name="Team Lead", respond_user_id="900123"))
    db.add(User(id=unmapped_id, email="unmapped@test.com", name="Warehouse Sam"))
    db.commit()
    return {"contact_id": contact_id, "mapped_id": mapped_id, "unmapped_id": unmapped_id}


def _comment(db, seed, body: str, mentioned: list[str]) -> ConversationTicketComment:
    row = ConversationTicketComment(
        id=str(uuid.uuid4()),
        tracking_id=None,
        respond_contact_id=seed["contact_id"],
        author_name="Agent One",
        body=body,
        mentioned_user_ids=mentioned,
        source="crm",
    )
    db.add(row)
    db.commit()
    return row


# --------------------------------------------------------------- mention text


def test_a_mapped_mention_becomes_a_respond_user_token(db):
    seed = _seed(db)

    rendered = build_mirror_text(
        db, "@Team Lead please check this", [seed["mapped_id"]]
    )

    assert rendered == "{{@user.900123}} please check this"


def test_an_unmapped_mention_stays_a_plain_name(db):
    seed = _seed(db)

    rendered = build_mirror_text(
        db, "@Warehouse Sam please check this", [seed["unmapped_id"]]
    )

    assert rendered == "@Warehouse Sam please check this"


def test_mixed_mentions_only_substitute_the_mapped_one(db):
    seed = _seed(db)

    rendered = build_mirror_text(
        db,
        "@Team Lead and @Warehouse Sam - both please",
        [seed["mapped_id"], seed["unmapped_id"]],
    )

    assert rendered == "{{@user.900123}} and @Warehouse Sam - both please"


def test_a_crm_uuid_in_respond_user_id_is_not_treated_as_a_mapping(db):
    """Same trap as resolve_sender_respond_user_id: some rows carry the CRM
    users.id in respond_user_id, which is not a Respond user id."""
    _seed(db)
    stray = str(uuid.uuid4())
    db.add(User(id=stray, email="stray@test.com", name="Stray One", respond_user_id=stray))
    db.commit()

    rendered = build_mirror_text(db, "@Stray One look", [stray])

    assert rendered == "@Stray One look"


# -------------------------------------------------------------------- mirror


def test_a_successful_mirror_writes_a_success_outbox_row(db):
    seed = _seed(db)
    comment = _comment(db, seed, "@Team Lead please check this", [seed["mapped_id"]])
    client = _RecordingClient()

    ok = mirror_comment_to_respond(db, comment, identifier=RESPOND_IO_ID, client=client)

    assert ok is True
    assert client.calls == [(RESPOND_IO_ID, "{{@user.900123}} please check this")]
    log = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "conversation_ticket_comments")
        .one()
    )
    assert log.status == "success"
    assert log.direction == "outbound"
    assert str(log.business_id) == str(comment.id)
    assert "comment" in (log.endpoint or "")
    assert db.query(ConversationTicketComment).one().respond_mirrored is True


def test_a_failed_mirror_logs_the_outbox_row_and_does_not_raise(db):
    seed = _seed(db)
    comment = _comment(db, seed, "internal note", [])
    client = _RecordingClient(fail=True)

    ok = mirror_comment_to_respond(db, comment, identifier=RESPOND_IO_ID, client=client)

    assert ok is False
    log = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "conversation_ticket_comments")
        .one()
    )
    assert log.status == "failed"
    assert "401" in (log.error_message or "")
    assert db.query(ConversationTicketComment).one().respond_mirrored is False


def test_a_contact_with_no_respond_identifier_is_skipped_without_a_log(db):
    seed = _seed(db)
    comment = _comment(db, seed, "internal note", [])
    client = _RecordingClient()

    ok = mirror_comment_to_respond(db, comment, identifier=None, client=client)

    assert ok is False
    assert client.calls == []
    assert db.query(IntegrationLog).count() == 0
