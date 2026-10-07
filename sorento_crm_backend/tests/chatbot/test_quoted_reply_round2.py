"""QUOTED-REPLY round 2: the live question survives every lane, and the resolver never fails a turn.

UAC: AC-QR-8a (non-answer lanes), AC-QR-14, AC-QR-15 (R6 on the memory fallback).
Fake contact ids, synthetic uuids, public catalogue codes.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: F401 - import first: app.dependencies alone is circular
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import quoted as quoted_mod
from app.services.chatbot.turn import pending as turn_pending
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import blank_session
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_quoted_reply_engine import (
    A,
    C,
    _console,
    _focus_wire,
    _live_c,
    _open_task_labels,
    _store_asked,
    _store_turn,
    ask_text,
    quoted_facts,
    say_quoted,
)

pytestmark = pytest.mark.usefixtures("stub_access")


@pytest.mark.parametrize("message_type", ["clarification", "casual", "out_of_scope", "other", "unknown"])
def test_ac_qr_8a_a_non_answer_lane_reply_to_a_quote_leaves_the_whole_live_focus(
    session_factory, monkeypatch, stub_access, message_type
):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000501")
    _store_asked(c, A, turn_no=1, age_s=900)
    _live_c(c)
    live_products = [p.get("canonical_code") for p in c.state["focus"]["products"]]
    assert live_products == [C]
    say_quoted(c, "ok thanks", verdict(message_type=message_type), quote=ask_text(A))
    assert _open_task_labels(c) == [C], "the live task is still C"
    assert [p.get("canonical_code") for p in c.state["focus"]["products"]] == [C], "never the quoted product"


def test_ac_qr_14_a_resolver_failure_never_fails_the_turn(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000502")
    _store_asked(c, A, turn_no=1, age_s=900)
    _live_c(c)

    def boom(*_a, **_k):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(quoted_mod, "resolve_quoted_turn", boom)
    say_quoted(c, "10 unit", verdict(domain_hint="inventory", intent_hint="check_stock"), quote=ask_text(A))
    facts = quoted_facts(c, "10 unit")
    assert facts["found"] is False and facts["via"] is None


def test_ac_qr_13_an_offer_resolved_through_the_memory_event_is_not_revived(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000503")
    offer_text = "Would you like me to bring in the warehouse team?"
    offer = turn_pending.to_wire(
        turn_pending.ask(
            "team_pick",
            [{"position": 1, "label": "Warehouse", "payload": {"team": "warehouse"}}],
            team="warehouse",
            asked_at_turn=1,
        )
    )
    memory = [
        {"kind": "memory", "focus": {"after": _focus_wire(c, A)}, "open_question": {"after": offer}},
    ]
    _store_turn(c, reply_text=offer_text, focus=None, trace=memory, age_s=900)
    _live_c(c)
    from tests.chatbot.test_quoted_reply_engine import reply, _ctx_lines

    say_quoted(c, "yes", reply(is_affirmative=True), quote=offer_text)
    lines = _ctx_lines(c.last_block)
    assert not any(line.startswith(("Pending:", "Open question options:")) for line in lines), lines


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
    with external_permissions_granted():
        yield TestClient(app)
    app.dependency_overrides.clear()


def _body(**extra):
    return {
        "channel": "whatsapp",
        "contact_id": "900000061",
        "phone_number": "+60160000561",
        "message": "hello",
        "sent_at": 1790000000000,
        "type": "outgoing",
        "message_id": f"17807{uuid.uuid4().int % 10**11:011d}",
        **extra,
    }


def test_ac_qr_4_a_non_uuid_chatbot_turn_id_is_a_422(client):
    response = client.post("/api/v1/external/chat-history/messages", json=_body(chatbot_turn_id="not-a-uuid"))
    assert response.status_code == 422


def test_ac_qr_4_a_uuid_chatbot_turn_id_is_stored(client, db):
    explicit = str(uuid.uuid4())
    body = _body(chatbot_turn_id=explicit)
    assert client.post("/api/v1/external/chat-history/messages", json=body).status_code == 201
    got = db.execute(
        text("SELECT chatbot_turn_id FROM chat_histories WHERE message_id = :m"), {"m": body["message_id"]}
    ).scalar_one()
    assert got == explicit
