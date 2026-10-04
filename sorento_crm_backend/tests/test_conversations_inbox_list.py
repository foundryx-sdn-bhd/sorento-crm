"""The Conversations inbox list: tabs, search, keyset paging, bounded cost.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-N1 (server-paginated, keyset on last-message time desc, tab + search,
     no per-row thread fetch, one list query per page)
     AC-N2 (read gate is `sla_management.conversations.view`, not assignment)

What is actually pinned here:

1. **The four tabs mean what AC-N1 says.** Mine = I hold an OPEN
   conversation-scope ticket; Unassigned = an open ticket with no assignee;
   Mentioned = a note whose `mentioned_user_ids` contains me, newest note
   first; All = any contact with any message.
2. **A page costs ONE query.** The whole point of the slice is 10 000+
   contacts, so a statement counter asserts the page is a single SELECT - no
   per-row thread fetch, no N+1 for the ticket counts.
3. **Paging has no gaps and no duplicates**, walked across many cursor
   boundaries over a 500-contact chain, including ties on the sort key (a
   pure `last_message_at` cursor loses rows the moment two contacts share a
   timestamp, which is exactly what a bulk ingest produces).

Run:
    venv/bin/pytest tests/test_conversations_inbox_list.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, text

from app.models.access import RespondContact
from app.models.chat_history import ChatHistory
from app.models.sla import SLAPolicy, SLAPolicyTier, ConversationSLATracking
from app.models.ticket_comment import ConversationTicketComment
from app.models.user import User
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

BASE = "/api/v1/sla-management/conversations"
VIEW = "sla_management.conversations.view"

_GRANTS: set[str] = set()
_ACTOR: dict = {"id": None, "name": "Test Actor"}


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
    _GRANTS.add(VIEW)
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in _GRANTS,
    )
    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, uid: set()
    )
    yield
    _GRANTS.clear()


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


# --------------------------------------------------------------------------- #
# Seeding                                                                      #
# --------------------------------------------------------------------------- #


def _user(db, label: str) -> str:
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"zzt-{label}-{uid[:8]}@test.com", name=f"ZZT {label}"))
    db.commit()
    return uid


def _policy(db) -> str:
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
    db.commit()
    return policy_id


def _contact(db, *, name: str, phone: str, respond_io_id: str) -> str:
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            phone_number=phone,
            name=name,
            respond_io_id=respond_io_id,
            session_vars={},
        )
    )
    db.commit()
    return contact_id


def _message(db, *, respond_io_id: str, phone: str, text_body: str, sent_at: datetime):
    db.add(
        ChatHistory(
            channel="whatsapp",
            contact_id=respond_io_id,
            phone_number=phone,
            message=text_body,
            sent_at=sent_at,
            type="incoming",
            message_id=str(int(sent_at.timestamp() * 1000)),
        )
    )
    db.commit()


def _ticket(
    db,
    *,
    policy_id: str,
    contact_pk: str,
    assigned_to_id: str | None,
    is_resolved: bool = False,
    source_entity_type: str | None = None,
) -> str:
    tid = str(uuid.uuid4())
    db.add(
        ConversationSLATracking(
            id=tid,
            policy_id=policy_id,
            respond_contact_id=contact_pk,
            assigned_to_id=assigned_to_id,
            is_resolved=is_resolved,
            source_entity_type=source_entity_type,
            source_entity_id=str(uuid.uuid4()) if source_entity_type else None,
            current_tier=1,
            initiated_at=datetime.utcnow(),
            due_at=datetime.utcnow() + timedelta(hours=4),
        )
    )
    db.commit()
    return tid


def _note(db, *, contact_pk: str, tracking_id: str | None, mentions: list[str], body: str,
          created_at: datetime | None = None) -> str:
    nid = str(uuid.uuid4())
    note = ConversationTicketComment(
        id=nid,
        tracking_id=tracking_id,
        respond_contact_id=contact_pk,
        author_name="ZZT Author",
        body=body,
        mentioned_user_ids=mentions,
        source="crm",
    )
    if created_at is not None:
        note.created_at = created_at
    db.add(note)
    db.commit()
    return nid


# --------------------------------------------------------------------------- #
# Tabs                                                                         #
# --------------------------------------------------------------------------- #


@pytest.fixture
def world(db):
    """Four contacts, one per tab-defining situation."""
    me = _user(db, "me")
    other = _user(db, "other")
    policy_id = _policy(db)
    base = datetime(2026, 8, 10, 8, 0, 0)

    mine = _contact(db, name="ZZT Mine", phone="+60110000001", respond_io_id="zzt-io-1")
    unassigned = _contact(
        db, name="ZZT Unassigned", phone="+60110000002", respond_io_id="zzt-io-2"
    )
    mentioned = _contact(
        db, name="ZZT Mentioned", phone="+60110000003", respond_io_id="zzt-io-3"
    )
    stranger = _contact(
        db, name="ZZT Stranger", phone="+60110000004", respond_io_id="zzt-io-4"
    )

    for idx, (rio, phone) in enumerate(
        [
            ("zzt-io-1", "+60110000001"),
            ("zzt-io-2", "+60110000002"),
            ("zzt-io-3", "+60110000003"),
            ("zzt-io-4", "+60110000004"),
        ]
    ):
        _message(
            db,
            respond_io_id=rio,
            phone=phone,
            text_body=f"hello from {rio}",
            sent_at=base + timedelta(minutes=idx),
        )

    _ticket(db, policy_id=policy_id, contact_pk=mine, assigned_to_id=me)
    _ticket(db, policy_id=policy_id, contact_pk=unassigned, assigned_to_id=None)
    other_ticket = _ticket(db, policy_id=policy_id, contact_pk=mentioned, assigned_to_id=other)
    _note(
        db,
        contact_pk=mentioned,
        tracking_id=other_ticket,
        mentions=[me],
        body="ping @me",
        created_at=datetime(2026, 8, 10, 9, 0, 0),
    )

    _act_as(me)
    return {
        "me": me,
        "other": other,
        "policy_id": policy_id,
        "mine": mine,
        "unassigned": unassigned,
        "mentioned": mentioned,
        "stranger": stranger,
    }


def _names(payload) -> list[str]:
    return [row["name"] for row in payload["items"]]


def test_mine_tab_lists_only_contacts_where_i_hold_an_open_ticket(client, world):
    body = client.get(BASE, params={"tab": "mine"})
    assert body.status_code == 200, body.text
    assert _names(body.json()) == ["ZZT Mine"]


def test_unassigned_tab_lists_open_tickets_with_no_assignee(client, world):
    body = client.get(BASE, params={"tab": "unassigned"})
    assert body.status_code == 200, body.text
    assert _names(body.json()) == ["ZZT Unassigned"]


def test_mentioned_tab_lists_contacts_whose_note_mentions_me(client, world):
    body = client.get(BASE, params={"tab": "mentioned"})
    assert body.status_code == 200, body.text
    assert _names(body.json()) == ["ZZT Mentioned"]


def test_all_tab_lists_every_contact_with_any_message_newest_first(client, world):
    body = client.get(BASE, params={"tab": "all"})
    assert body.status_code == 200, body.text
    # Seeded one minute apart ascending, so newest-first is the reverse.
    assert _names(body.json()) == [
        "ZZT Stranger",
        "ZZT Mentioned",
        "ZZT Unassigned",
        "ZZT Mine",
    ]


def test_a_resolved_ticket_does_not_keep_a_contact_on_mine(client, db, world):
    _ticket(
        db,
        policy_id=world["policy_id"],
        contact_pk=world["stranger"],
        assigned_to_id=world["me"],
        is_resolved=True,
    )
    assert _names(client.get(BASE, params={"tab": "mine"}).json()) == ["ZZT Mine"]


def test_a_form_sla_stage_is_not_a_conversation_ticket(client, db, world):
    """Form SLA rows share the table; only conversation-scope rows count here."""
    _ticket(
        db,
        policy_id=world["policy_id"],
        contact_pk=world["stranger"],
        assigned_to_id=world["me"],
        source_entity_type="complaint",
    )
    assert _names(client.get(BASE, params={"tab": "mine"}).json()) == ["ZZT Mine"]
    stranger_row = next(
        r
        for r in client.get(BASE, params={"tab": "all"}).json()["items"]
        if r["name"] == "ZZT Stranger"
    )
    assert stranger_row["open_ticket_count"] == 0


def test_mentioned_tab_orders_by_the_newest_mentioning_note(client, db, world):
    late = _contact(db, name="ZZT Late", phone="+60110000005", respond_io_id="zzt-io-5")
    _message(
        db,
        respond_io_id="zzt-io-5",
        phone="+60110000005",
        text_body="oldest message of all",
        sent_at=datetime(2026, 8, 1, 0, 0, 0),
    )
    _note(
        db,
        contact_pk=late,
        tracking_id=None,
        mentions=[world["me"]],
        body="latest ping",
        created_at=datetime(2026, 8, 14, 12, 0, 0),
    )
    # Its message is the OLDEST in the world, so ordering by note time is the
    # only way it can come first.
    assert _names(client.get(BASE, params={"tab": "mentioned"}).json()) == [
        "ZZT Late",
        "ZZT Mentioned",
    ]


# --------------------------------------------------------------------------- #
# Search                                                                       #
# --------------------------------------------------------------------------- #


def test_search_matches_contact_name_case_insensitively(client, world):
    body = client.get(BASE, params={"tab": "all", "q": "mentio"})
    assert _names(body.json()) == ["ZZT Mentioned"]


def test_search_matches_phone_fragment(client, world):
    body = client.get(BASE, params={"tab": "all", "q": "000002"})
    assert _names(body.json()) == ["ZZT Unassigned"]


def test_search_wildcards_are_literal_not_operators(client, world):
    assert client.get(BASE, params={"tab": "all", "q": "%"}).json()["items"] == []


# --------------------------------------------------------------------------- #
# Row shape                                                                    #
# --------------------------------------------------------------------------- #


def test_row_carries_the_snippet_counts_and_my_ticket_id(client, db, world):
    row = next(
        r
        for r in client.get(BASE, params={"tab": "all"}).json()["items"]
        if r["name"] == "ZZT Mine"
    )
    assert row["contact_ref"] == "zzt-io-1"
    assert row["respond_io_id"] == "zzt-io-1"
    assert row["phone"] == "+60110000001"
    assert row["last_message_snippet"] == "hello from zzt-io-1"
    assert row["last_message_at"].startswith("2026-08-10T08:00:00")
    assert row["last_message_direction"] == "incoming"
    assert row["open_ticket_count"] == 1
    assert row["my_open_ticket_count"] == 1
    assert row["my_open_ticket_id"] is not None


def test_my_open_ticket_id_is_withheld_when_i_hold_more_than_one(client, db, world):
    """Reply stamping is only unambiguous at exactly one open ticket."""
    _ticket(
        db, policy_id=world["policy_id"], contact_pk=world["mine"], assigned_to_id=world["me"]
    )
    row = next(
        r
        for r in client.get(BASE, params={"tab": "all"}).json()["items"]
        if r["name"] == "ZZT Mine"
    )
    assert row["my_open_ticket_count"] == 2
    assert row["my_open_ticket_id"] is None


# --------------------------------------------------------------------------- #
# Gate                                                                         #
# --------------------------------------------------------------------------- #


def test_without_the_view_permission_the_inbox_is_403(client, world):
    _GRANTS.clear()
    assert client.get(BASE, params={"tab": "all"}).status_code == 403


# --------------------------------------------------------------------------- #
# Scale: one query per page, no gaps, no duplicates                            #
# --------------------------------------------------------------------------- #


def _seed_many(db, n: int, *, tie_every: int = 7) -> None:
    """`n` contacts, each with one message. Every `tie_every`th contact shares
    its timestamp with the previous one, so the cursor is exercised against
    real ties on the sort key."""
    base = datetime(2026, 1, 1, 0, 0, 0)
    contacts = []
    messages = []
    stamp = base
    for i in range(n):
        if i % tie_every:
            stamp = stamp + timedelta(seconds=30)
        rio = f"zzt-bulk-{i:04d}"
        phone = f"+6019{i:07d}"
        contacts.append(
            {
                "id": str(uuid.uuid4()),
                "phone_number": phone,
                "name": f"ZZT Bulk {i:04d}",
                "respond_io_id": rio,
                "session_vars": {},
            }
        )
        messages.append(
            {
                "channel": "whatsapp",
                "contact_id": rio,
                "phone_number": phone,
                "message": f"bulk message {i:04d}",
                "sent_at": stamp,
                "type": "incoming",
                "message_id": str(1_700_000_000_000 + i),
            }
        )
    db.execute(insert(RespondContact), contacts)
    db.execute(insert(ChatHistory), messages)
    db.commit()


class _StatementCounter:
    def __init__(self, bind):
        self.bind = bind
        self.statements: list[str] = []

    def __enter__(self):
        event.listen(self.bind, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *exc):
        event.remove(self.bind, "before_cursor_execute", self._on)
        return False

    def _on(self, conn, cursor, statement, parameters, context, executemany):
        head = statement.strip().split(None, 1)[0].upper()
        if head in ("SELECT", "WITH"):
            self.statements.append(statement)


def test_a_page_is_one_bounded_query_and_paging_loses_nothing(db):
    """500 contacts, walked in pages of 30. Each page: exactly one SELECT."""
    from app.services import conversation_inbox_service as inbox

    _seed_many(db, 500)
    viewer = _user(db, "scale")

    seen: list[str] = []
    cursor = None
    pages = 0
    bind = db.get_bind()
    while True:
        with _StatementCounter(bind) as counted:
            page = inbox.list_conversations(
                db, viewer_user_id=viewer, tab="all", q=None, cursor=cursor, limit=30
            )
        assert len(counted.statements) == 1, (
            f"page {pages} issued {len(counted.statements)} queries; "
            "the inbox list must be a single query per page"
        )
        pages += 1
        seen.extend(row["contact_ref"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
        assert pages < 40, "pagination did not terminate"

    bulk = [ref for ref in seen if ref.startswith("zzt-bulk-")]
    assert len(bulk) == len(set(bulk)), "a contact was returned on two pages"
    assert set(bulk) == {f"zzt-bulk-{i:04d}" for i in range(500)}, "a contact was skipped"
    assert pages >= 17, "expected the 500 rows to span many cursor boundaries"


def test_the_page_is_ordered_newest_message_first_across_boundaries(db):
    from app.services import conversation_inbox_service as inbox

    _seed_many(db, 120)
    viewer = _user(db, "order")

    stamps: list[str] = []
    cursor = None
    while True:
        page = inbox.list_conversations(
            db, viewer_user_id=viewer, tab="all", q=None, cursor=cursor, limit=25
        )
        stamps.extend(
            row["last_message_at"] for row in page["items"] if row["contact_ref"].startswith("zzt-bulk-")
        )
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert stamps == sorted(stamps, reverse=True)


def test_limit_is_capped(client, world):
    body = client.get(BASE, params={"tab": "all", "limit": 5000})
    assert body.status_code == 422
