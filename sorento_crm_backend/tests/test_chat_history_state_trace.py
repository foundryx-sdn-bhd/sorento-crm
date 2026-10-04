"""state_trace ingest persistence + admin read-path exposure.

Covers the CRM-side acceptance cases of the state-transition-monitor plan:

- UAC-CRM-1: a POSTed state_trace actually PERSISTS (not validated-then-dropped - 
  the blocker was that the raw INSERT has an explicit column list, so the pydantic
  field alone is insufficient). Asserting 201 is NOT enough; we read the row back.
- UAC-CRM-2: `after: null` round-trips as a present key with a null value, not `{}`
  and not absent. (The `jsonb_typeof` half is Postgres-only and is asserted in the
  migration verification script; here we assert the app-layer round trip.)
- UAC-CRM-3: outgoing rows keep state_trace NULL.
- UAC-CRM-7: the trace is excluded from the integration_logs request_payload copy.
- Read path: the thread (transcript) carries state_trace; the grid list omits it.

sqlite fixture (JSONB -> generic JSON affinity), matching test_chat_history_result_set.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.chat_history import ChatHistory
from app.models.integration import IntegrationLog
from app.services import chat_history_query as svc
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session

RESPOND_IO_ID = "900000008"

TRACE = {
    "v": 1,
    "before": {"entities": [{"raw": "tan trading", "entity_type": "customer"}]},
    "parser_raw": {"domain_hint": "orders", "entities": [{"raw": "SRTWC287"}]},
    "parser_applied": {
        "scope_exclusive_applied": True,
        "entity_op_applied": True,
        "domain_signal_source": "current_message",
        "entities": [{"raw": "SRTWC287", "entity_type": "product"}],
    },
    "after": {"entities": [{"raw": "SRTWC287", "entity_type": "product"}]},
}


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def client(db):
    def _user():
        return {"id": "system"}

    def _db():
        yield db

    app.dependency_overrides[get_external_api_user] = _user
    app.dependency_overrides[get_current_user_or_api_key] = _user
    app.dependency_overrides[get_db] = _db
    # /external now enforces per-endpoint permissions (added on this branch), and
    # this suite mocks the principal, so grant them explicitly. Enforcement
    # itself is covered by test_external_permission_guard.
    with external_permissions_granted():
        yield TestClient(app)
    app.dependency_overrides.clear()


def _payload(**overrides) -> dict:
    payload = {
        "channel": "whatsapp",
        "contact_id": RESPOND_IO_ID,
        "phone_number": "+60160000509",
        "message": "how about SRTWC287",
        "sent_at": 1780751906900,
        "type": "incoming",
        "turn_id": "exec-1",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------- ingest


def test_ingest_persists_state_trace(client, db):
    """UAC-CRM-1: the trace lands in the row, all four layers, not just a 201."""
    r = client.post("/api/v1/external/chat-history/messages", json=_payload(state_trace=TRACE))
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.state_trace is not None
    assert str(row.state_trace["v"]) == "1"
    assert set(row.state_trace) >= {"before", "parser_raw", "parser_applied", "after"}


def test_after_null_round_trips_as_present_null(client, db):
    """UAC-CRM-2: after=null survives as a present key with a null value, not {} nor absent."""
    trace = {**TRACE, "after": None}
    r = client.post("/api/v1/external/chat-history/messages", json=_payload(state_trace=trace))
    assert r.status_code == 201

    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert "after" in row.state_trace  # present, not dropped
    assert row.state_trace["after"] is None  # null, not {}


def test_outgoing_row_keeps_state_trace_null(client, db):
    """UAC-CRM-3: an outgoing row carrying no trace stores NULL."""
    r = client.post(
        "/api/v1/external/chat-history/messages",
        json=_payload(type="outgoing", message="reply"),
    )
    assert r.status_code == 201
    row = db.query(ChatHistory).filter(ChatHistory.id == r.json()["id"]).one()
    assert row.state_trace is None


def test_integration_log_excludes_state_trace(client, db):
    """UAC-CRM-7: the trace is on the row, but NOT duplicated into the integration log."""
    r = client.post("/api/v1/external/chat-history/messages", json=_payload(state_trace=TRACE))
    assert r.status_code == 201

    log = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.business_table == "chat_histories")
        .order_by(IntegrationLog.id.desc())
        .first()
    )
    assert log is not None
    assert "state_trace" not in (log.request_payload or "")
    # the rest of the payload is still logged
    assert "turn_id" in log.request_payload


# ---------------------------------------------------------------- read path


def _seed(db, **kw) -> ChatHistory:
    row = ChatHistory(
        channel="whatsapp",
        contact_id=RESPOND_IO_ID,
        phone_number="+60160000509",
        message="m",
        sent_at=datetime(2026, 7, 20, 12, 0, 0),
        type="incoming",
        **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_thread_carries_state_trace(db):
    """The transcript is the diagnosis surface - it exposes the trace."""
    _seed(db, turn_id="t1", state_trace=TRACE)
    rows = svc.get_thread(db, contact_id=RESPOND_IO_ID)
    assert len(rows) == 1
    assert rows[0].state_trace is not None
    assert str(rows[0].state_trace["v"]) == "1"


def test_grid_list_omits_state_trace(db):
    """The grid is a scan view: it must not haul the 3 KB trace per row."""
    _seed(db, turn_id="t1", state_trace=TRACE)
    rows, _total = svc.list_messages_page(
        db, date_from=datetime(2026, 7, 20), date_to=datetime(2026, 7, 21)
    )
    assert len(rows) == 1
    assert rows[0].state_trace is None
