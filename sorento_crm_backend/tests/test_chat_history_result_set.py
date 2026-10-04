"""chat_histories message_id/result columns + conversation-variables referenced_result_set.

- POST /external/chat-history/messages accepts optional message_id + result (back-compat without)
- POST /external/chat-history returns message_id + result per message
- GET /external/conversation-variables/{respond_io_id}?message_id=... injects
  session_vars.referenced_result_set (resolved result / null) response-only
- service get_referenced_result_set: match, no match, message without result

Run with:
    pytest tests/test_chat_history_result_set.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.access import RespondContact
from app.models.chat_history import ChatHistory
from app.services.conversation_variables_service import (
    get_referenced_result_set,
    get_referenced_state,
)
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

RESPOND_IO_ID = "900000008"
MESSAGE_ID = "1780751891000000"
RESULT_SET = [
    {
        "idx": 1,
        "uuid": None,
        "label": "Promotion: UPDATED SORENTO STOP VALVE PROMO_07052026 DEALER",
        "entity_type": None,
        "product": None,
        "attachment_type": None,
        "filename": None,
    }
]


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


@pytest.fixture
def client(db):
    def _user():
        return {"id": "system"}

    def _db():
        yield db

    app.dependency_overrides[get_external_api_user] = _user
    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_db] = _db
    # Authorization is deliberately out of scope here: this suite mocks the
    # database, so the real RBAC lookup cannot answer. Enforcement is covered
    # by test_external_permission_guard / _coverage.
    with external_permissions_granted():
        yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_contact(db) -> None:
    db.add(
        RespondContact(
            id=str(uuid.uuid4()),
            respond_io_id=RESPOND_IO_ID,
            phone_number="+60160000509",
            session_vars={"flow": "promo", "last_result_set": [{"idx": 9}]},
        )
    )
    db.commit()


def _ingest_payload(**overrides) -> dict:
    payload = {
        "channel": "whatsapp",
        "contact_id": RESPOND_IO_ID,
        "phone_number": "+60160000509",
        "message": "I have attached the file(s) below.",
        "sent_at": 1780751906900,
        "first_name": "Jayson",
        "last_name": None,
        "type": "outgoing",
        "message_id": MESSAGE_ID,
        "result": RESULT_SET,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------- ingest


def test_ingest_persists_message_id_and_result(client, db):
    r = client.post("/api/v1/external/chat-history/messages", json=_ingest_payload())
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.message_id == MESSAGE_ID
    stored = row.result
    if isinstance(stored, str):  # sqlite stores the json text param verbatim
        import json

        stored = json.loads(stored)
    assert stored == RESULT_SET


def test_ingest_without_message_id_and_result_back_compat(client, db):
    payload = _ingest_payload()
    payload.pop("message_id")
    payload.pop("result")
    r = client.post("/api/v1/external/chat-history/messages", json=payload)
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.message_id is None
    assert row.result is None


# ------------------------------------------------- reply-to (CRM-002)


def test_ingest_incoming_persists_reply_to(client, db):
    """Incoming quote-reply carries reply_to_message_id/message onto the row."""
    payload = _ingest_payload(
        type="incoming",
        message="All",
        message_id=None,
        result=None,
        reply_to_message_id=MESSAGE_ID,
        reply_to_message="menu text the user replied to",
    )
    r = client.post("/api/v1/external/chat-history/messages", json=payload)
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.reply_to_message_id == MESSAGE_ID
    assert row.reply_to_message == "menu text the user replied to"


def test_ingest_non_reply_stores_null_reply_to(client, db):
    """Normal (non-reply) incoming message defaults reply_to columns to NULL."""
    payload = _ingest_payload(type="incoming", message="hi", message_id=None, result=None)
    r = client.post("/api/v1/external/chat-history/messages", json=payload)
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.reply_to_message_id is None
    assert row.reply_to_message is None


def test_reply_to_references_outgoing_message_id(client, db):
    """reply_to_message_id equals the message_id of an existing outgoing row (AC3)."""
    client.post("/api/v1/external/chat-history/messages", json=_ingest_payload())  # outgoing MESSAGE_ID
    reply = _ingest_payload(
        type="incoming", message="5", message_id=None, result=None,
        reply_to_message_id=MESSAGE_ID, reply_to_message=None,
    )
    rid = client.post("/api/v1/external/chat-history/messages", json=reply).json()["id"]

    reply_row = db.query(ChatHistory).filter(ChatHistory.id == rid).one()
    outgoing = (
        db.query(ChatHistory)
        .filter(ChatHistory.contact_id == RESPOND_IO_ID, ChatHistory.type == "outgoing")
        .one()
    )
    assert reply_row.reply_to_message_id == outgoing.message_id


def test_messages_read_returns_reply_to(client):
    client.post(
        "/api/v1/external/chat-history/messages",
        json=_ingest_payload(
            type="incoming", message="All", message_id=None, result=None,
            reply_to_message_id=MESSAGE_ID, reply_to_message="quoted",
        ),
    )
    r = client.post(
        "/api/v1/external/chat-history",
        json={"channel": "whatsapp", "contact_id": RESPOND_IO_ID, "limit": 10, "order": "asc"},
    )
    assert r.status_code == 200
    msg = r.json()["messages"][0]
    assert msg["reply_to_message_id"] == MESSAGE_ID
    assert msg["reply_to_message"] == "quoted"


# ---------------------------------------------------------------- read


def test_messages_read_returns_message_id_and_result(client):
    client.post("/api/v1/external/chat-history/messages", json=_ingest_payload())
    r = client.post(
        "/api/v1/external/chat-history",
        json={"channel": "whatsapp", "contact_id": RESPOND_IO_ID, "limit": 10, "order": "asc"},
    )
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["message_id"] == MESSAGE_ID
    assert msgs[0]["result"] == RESULT_SET


# ------------------------------------------- conversation-variables GET


def test_conversation_variables_without_message_id_unchanged(client, db):
    _seed_contact(db)
    r = client.get(f"/api/v1/external/conversation-variables/{RESPOND_IO_ID}")
    assert r.status_code == 200
    sv = r.json()["session_vars"]
    assert sv["flow"] == "promo"
    assert "referenced_result_set" not in sv


def test_conversation_variables_injects_referenced_result_set(client, db):
    _seed_contact(db)
    client.post("/api/v1/external/chat-history/messages", json=_ingest_payload())

    r = client.get(
        f"/api/v1/external/conversation-variables/{RESPOND_IO_ID}",
        params={"message_id": MESSAGE_ID},
    )
    assert r.status_code == 200
    sv = r.json()["session_vars"]
    assert sv["referenced_result_set"] == RESULT_SET
    # existing keys untouched; persisted session_vars not mutated by GET
    assert sv["last_result_set"] == [{"idx": 9}]
    contact = db.query(RespondContact).filter(RespondContact.respond_io_id == RESPOND_IO_ID).one()
    db.refresh(contact)
    assert "referenced_result_set" not in (contact.session_vars or {})


def test_conversation_variables_unknown_message_id_yields_null(client, db):
    _seed_contact(db)
    r = client.get(
        f"/api/v1/external/conversation-variables/{RESPOND_IO_ID}",
        params={"message_id": "999999"},
    )
    assert r.status_code == 200
    assert r.json()["session_vars"]["referenced_result_set"] is None


# ---------------------------------------------------------------- service


def test_get_referenced_result_set_message_without_result(client, db):
    payload = _ingest_payload(result=None)
    client.post("/api/v1/external/chat-history/messages", json=payload)
    assert (
        get_referenced_result_set(db, respond_io_id=RESPOND_IO_ID, message_id=MESSAGE_ID)
        is None
    )


def test_get_referenced_result_set_scoped_to_contact(client, db):
    client.post("/api/v1/external/chat-history/messages", json=_ingest_payload())
    assert (
        get_referenced_result_set(db, respond_io_id="other-contact", message_id=MESSAGE_ID)
        is None
    )


# -------------------------------- referenced_state (quoted-turn pointer)
#
# A quote-reply names an OUTGOING message_id. That row's `turn_id` identifies the
# turn; the INCOMING row of the same turn carries `state_trace`, whose `after` is
# the state that turn wrote. The endpoint returns a 4-key projection of it, so the
# parser can rebase continuity onto the quoted turn instead of the latest one.

TURN_ID = "exec-4242"
AFTER_STATE = {
    "domain_hint": "promotion",
    "intent_hint": "check_promotion",
    "entities": [{"raw": "stop valve", "entity_type": "product", "dym_slot": 0}],
    "dym_offer": {
        "candidates": [
            {
                "code": "SRTWC287",
                "for_raw": "stop valve",
                "for_canonical": "SORENTO STOP VALVE",
                "entity_type": "product",
                "uuid": "11111111-1111-1111-1111-111111111111",
                "picked": False,
            }
        ],
        "ttl": 2,
    },
    # withheld by the projection
    "last_result_set": {"n": 4, "first": "Promotion: A"},
    "selection_context": "member_offer",
    "response": "Would you like me to escalate this?",
    "access_levels": ["dealer"],
    "routing": {"suggested_team": "cs"},
}


def _seed_quoted_turn(
    client,
    *,
    turn_id: str | None = TURN_ID,
    incoming_turn_id: str | None = TURN_ID,
    state_trace: dict | None = None,
    message_id: str = MESSAGE_ID,
    contact_id: str = RESPOND_IO_ID,
    sent_at: int = 1780751906900,
) -> None:
    """Seed the pair a quote-reply resolves through: the quoted OUTGOING row
    (carries `message_id`) and the turn's INCOMING row (carries `state_trace`)."""
    client.post(
        "/api/v1/external/chat-history/messages",
        json=_ingest_payload(
            contact_id=contact_id,
            type="outgoing",
            message_id=message_id,
            turn_id=turn_id,
            sent_at=sent_at,
        ),
    )
    payload = _ingest_payload(
        contact_id=contact_id,
        type="incoming",
        message="promo for stop valve",
        message_id=None,
        result=None,
        turn_id=incoming_turn_id,
        sent_at=sent_at - 1000,
    )
    if state_trace is not None:
        payload["state_trace"] = state_trace
    client.post("/api/v1/external/chat-history/messages", json=payload)


def test_conversation_variables_injects_referenced_state(client, db):
    _seed_contact(db)
    _seed_quoted_turn(client, state_trace={"v": 1, "after": AFTER_STATE})

    r = client.get(
        f"/api/v1/external/conversation-variables/{RESPOND_IO_ID}",
        params={"message_id": MESSAGE_ID},
    )
    assert r.status_code == 200
    rs = r.json()["session_vars"]["referenced_state"]
    assert rs["domain_hint"] == "promotion"
    assert rs["intent_hint"] == "check_promotion"
    assert rs["entities"] == AFTER_STATE["entities"]
    assert rs["dym_offer"] == AFTER_STATE["dym_offer"]


def test_referenced_state_absent_without_message_id(client, db):
    _seed_contact(db)
    r = client.get(f"/api/v1/external/conversation-variables/{RESPOND_IO_ID}")
    assert r.status_code == 200
    assert "referenced_state" not in r.json()["session_vars"]


def test_referenced_state_null_when_turn_id_null(client, db):
    """Proactive send / console row: no turn_id, so no turn to resolve."""
    _seed_contact(db)
    _seed_quoted_turn(
        client,
        turn_id=None,
        incoming_turn_id=None,
        state_trace={"v": 1, "after": AFTER_STATE},
    )
    r = client.get(
        f"/api/v1/external/conversation-variables/{RESPOND_IO_ID}",
        params={"message_id": MESSAGE_ID},
    )
    assert r.status_code == 200
    assert r.json()["session_vars"]["referenced_state"] is None


def test_referenced_state_null_when_state_trace_null(client, db):
    """Turn resolves, but predates the state_trace writer."""
    _seed_contact(db)
    _seed_quoted_turn(client, state_trace=None)
    assert (
        get_referenced_state(db, respond_io_id=RESPOND_IO_ID, message_id=MESSAGE_ID)
        is None
    )


def test_referenced_state_null_when_after_is_json_null(client, db):
    """`after: null` = the turn wrote no state. MISS, never `{}`.

    Returning an empty baseline would WIPE continuity in the parser rebase, which is
    strictly worse than the pre-pointer behaviour of falling back to recency.
    """
    _seed_contact(db)
    _seed_quoted_turn(client, state_trace={"v": 1, "before": {"x": 1}, "after": None})
    assert (
        get_referenced_state(db, respond_io_id=RESPOND_IO_ID, message_id=MESSAGE_ID)
        is None
    )


def test_referenced_state_scoped_to_contact(client, db):
    _seed_contact(db)
    _seed_quoted_turn(client, state_trace={"v": 1, "after": AFTER_STATE})
    assert (
        get_referenced_state(db, respond_io_id="other-contact", message_id=MESSAGE_ID)
        is None
    )


def test_referenced_state_picks_latest_on_legacy_duplicate_message_id(client, db):
    """Newest turn wins where `(contact_id, message_id)` duplicates still exist.

    The ingest no longer creates them: since the two-lane mirror went live it
    upserts on that pair (UAC AC-J5, migration 326), so a re-post of the same
    Respond message id resolves the one row. The uniqueness rule is scoped to
    rows created from the cutover onward, though, so LEGACY duplicates remain
    readable - and the reader's newest-wins tie-break still has to hold for
    them. Seeded directly, dated before the cutover, because the ingest would
    (correctly) refuse to produce this shape today.
    """
    _seed_contact(db)
    legacy_created_at = datetime(2026, 6, 1, 9, 0, 0)
    for turn, hint, sent_at in (
        ("exec-old", "orders", datetime(2026, 6, 1, 8, 0, 0)),
        ("exec-new", "promotion", datetime(2026, 6, 1, 8, 30, 0)),
    ):
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number="+60160000509",
                message="quoted outgoing message",
                sent_at=sent_at,
                type="outgoing",
                message_id=MESSAGE_ID,
                turn_id=turn,
                created_at=legacy_created_at,
            )
        )
        db.add(
            ChatHistory(
                channel="whatsapp",
                contact_id=RESPOND_IO_ID,
                phone_number="+60160000509",
                message="promo for stop valve",
                sent_at=sent_at,
                type="incoming",
                turn_id=turn,
                state_trace={"v": 1, "after": {**AFTER_STATE, "domain_hint": hint}},
                created_at=legacy_created_at,
            )
        )
    db.commit()

    rs = get_referenced_state(
        db, respond_io_id=RESPOND_IO_ID, message_id=MESSAGE_ID
    )
    assert rs is not None
    assert rs["domain_hint"] == "promotion"


def test_a_re_posted_message_id_no_longer_duplicates_the_row(client, db):
    """The other half of the same rule: today the second lane is a no-op."""
    _seed_contact(db)
    _seed_quoted_turn(
        client,
        turn_id="exec-old",
        incoming_turn_id="exec-old",
        state_trace={"v": 1, "after": {**AFTER_STATE, "domain_hint": "orders"}},
        sent_at=1780751000000,
    )
    _seed_quoted_turn(
        client,
        turn_id="exec-new",
        incoming_turn_id="exec-new",
        state_trace={"v": 1, "after": {**AFTER_STATE, "domain_hint": "promotion"}},
        sent_at=1780759000000,
    )

    outgoing = (
        db.query(ChatHistory)
        .filter(
            ChatHistory.contact_id == RESPOND_IO_ID,
            ChatHistory.message_id == MESSAGE_ID,
        )
        .all()
    )
    assert len(outgoing) == 1, "one Respond message id is one row (AC-J5)"
    assert outgoing[0].turn_id == "exec-old", "the first lane's turn stands"


def test_referenced_state_projection_withholds_internal_keys(client, db):
    """Contract boundary: exactly four keys, whatever else `after` carries.

    Without this, a future `after` key starts leaking silently - and two of the
    withheld keys are safety properties (`selection_context` -> wrong-member assign,
    `access_levels` -> stale re-grant), not tidiness.
    """
    _seed_contact(db)
    _seed_quoted_turn(
        client,
        state_trace={
            "v": 1,
            "before": {"entities": []},
            "parser_raw": {"domain_hint": "x"},
            "parser_applied": {"domain_hint": "y"},
            "after": AFTER_STATE,
        },
    )
    rs = get_referenced_state(db, respond_io_id=RESPOND_IO_ID, message_id=MESSAGE_ID)
    assert rs is not None
    assert set(rs) == {"domain_hint", "intent_hint", "entities", "dym_offer"}


def test_referenced_state_dym_offer_candidates_survive(client, db):
    """`trim()` never descends into `dym_offer`, so its candidates arrive intact."""
    _seed_contact(db)
    _seed_quoted_turn(client, state_trace={"v": 1, "after": AFTER_STATE})
    rs = get_referenced_state(db, respond_io_id=RESPOND_IO_ID, message_id=MESSAGE_ID)
    assert rs is not None
    cand = rs["dym_offer"]["candidates"][0]
    assert cand["code"] == "SRTWC287"
    assert cand["for_raw"] == "stop valve"
    assert cand["entity_type"] == "product"
    assert cand["uuid"] == "11111111-1111-1111-1111-111111111111"


def test_referenced_state_tolerates_non_dict_entities(client, db):
    """`trim()` collapses list-valued keys to `{n, first}`; entities must not 500."""
    _seed_contact(db)
    _seed_quoted_turn(
        client,
        state_trace={
            "v": 1,
            "after": {
                "domain_hint": "promotion",
                "intent_hint": None,
                "entities": {"n": 3, "first": "stop valve"},
                "dym_offer": [],
            },
        },
    )
    r = client.get(
        f"/api/v1/external/conversation-variables/{RESPOND_IO_ID}",
        params={"message_id": MESSAGE_ID},
    )
    assert r.status_code == 200
    rs = r.json()["session_vars"]["referenced_state"]
    assert rs["entities"] == []
    assert rs["dym_offer"] is None


def test_referenced_state_resolves_when_quoting_own_incoming_message(client, db):
    """Quoting your OWN earlier message: the anchor row is the incoming row itself,
    whose turn_id resolves to its own turn - the semantically right answer."""
    _seed_contact(db)
    client.post(
        "/api/v1/external/chat-history/messages",
        json=_ingest_payload(
            type="incoming",
            message="promo for stop valve",
            message_id=MESSAGE_ID,
            result=None,
            turn_id=TURN_ID,
            state_trace={"v": 1, "after": AFTER_STATE},
        ),
    )
    rs = get_referenced_state(db, respond_io_id=RESPOND_IO_ID, message_id=MESSAGE_ID)
    assert rs is not None
    assert rs["domain_hint"] == "promotion"


# ------------------------------------------- respond_ts derived at ingest
#
# Respond's message id IS the message's epoch-microsecond timestamp, so the SLA
# clock lands on the row at ingest instead of waiting on the resolver's HTTP call.


def test_ingest_derives_respond_ts_from_message_id(client, db):
    r = client.post("/api/v1/external/chat-history/messages", json=_ingest_payload())
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    # 1780751891000000us -> 2026-06-06 13:18:11 UTC
    assert row.respond_ts == datetime(2026, 6, 6, 13, 18, 11)


def test_ingest_without_message_id_leaves_respond_ts_null(client, db):
    payload = _ingest_payload()
    payload.pop("message_id")
    r = client.post("/api/v1/external/chat-history/messages", json=payload)
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.respond_ts is None


def test_ingest_with_non_timestamp_message_id_leaves_respond_ts_null(client, db):
    """A sequence-style id must not resolve to 1970 and fake a 56-year round trip."""
    r = client.post(
        "/api/v1/external/chat-history/messages",
        json=_ingest_payload(message_id="1234556"),
    )
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.respond_ts is None


def test_ingested_turn_yields_latency_in_the_admin_grid(client, db):
    """The whole chain: two ingests, one turn_id, a latency the grid can render.

    This is the failure the fix targets - turn_id was pairing correctly but every
    respond_ts was NULL, so the Latency column showed a dash on every row.
    """
    from app.services.chat_history_query import list_messages_page

    turn = "9399053"
    client.post("/api/v1/external/chat-history/messages", json=_ingest_payload(
        type="incoming", message="Check stock",
        message_id="1784602082000000",   # 02:48:02.000
        sent_at=1784602082000, result=None, turn_id=turn,
    ))
    client.post("/api/v1/external/chat-history/messages", json=_ingest_payload(
        type="outgoing", message="Here's what I've got so far",
        message_id="1784602125363985",   # 02:48:45.363985
        sent_at=1784602125363, result=None, turn_id=turn,
    ))

    rows, _ = list_messages_page(
        db,
        date_from=datetime(2026, 7, 21, 0, 0),
        date_to=datetime(2026, 7, 21, 23, 59),
    )
    outgoing = [r for r in rows if r.type == "outgoing"]
    assert len(outgoing) == 1
    assert outgoing[0].latency_seconds == pytest.approx(43.363985, abs=1e-5)
    # Latency belongs to the reply only - the inbound row must stay blank.
    assert all(r.latency_seconds is None for r in rows if r.type == "incoming")
