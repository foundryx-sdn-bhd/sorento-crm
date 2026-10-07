"""QUOTED-REPLY slice 2: the resolver `app.services.chatbot.quoted.resolve_quoted_turn`.

UAC: AC-QR-5, AC-QR-6, AC-QR-12 (nothing resolves), AC-QR-14 (contact and is_test scoping),
AC-QR-15 (state event first, memory event as the fallback).
Plan: documentation/plans/chatbot/PLAN-quoted-reply-07oct.md (rule R2, Design).

Red-first: the module does not exist, so its import sits INSIDE each test and every test
fails on its own ImportError instead of the whole file failing at collection.

Fake contact ids, synthetic uuids; the catalogue codes are public.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models.chat_history import ChatHistory
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot.turn import pending as turn_pending
from app.services.chatbot.turn.state import Focus, focus_to_wire
from app.services.chatbot.turn.task import Slot, Task
from tests._pg_fixture import blank_session

CONTACT = "900000031"
OTHER = "900000032"
QUESTION = "How many units of ELP3754?"
NOW = datetime.now(tz=timezone.utc)


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


def _focus_wire(code: str) -> dict[str, Any]:
    pid = str(uuid.uuid4())
    task = Task(
        kind="stock_qty",
        domain="inventory",
        status="open",
        opened_at_turn=1,
        touched_at_turn=1,
        slots=(Slot(key=pid, label=code, value=None),),
    )
    row = {"raw": code, "canonical_code": code, "uuid": pid, "hint": "product"}
    return focus_to_wire(Focus(products=[row], domains=["inventory"], tasks=(task,)))


def _pending_wire() -> dict[str, Any]:
    return turn_pending.to_wire(
        turn_pending.ask("product_pick", [{"position": 1, "code": "ELP3754"}], asked_at_turn=2)
    )


def _turn(
    db,
    *,
    contact=CONTACT,
    reply=QUESTION,
    is_test=False,
    age_s=60,
    trace: list[dict[str, Any]] | None = None,
    focus: dict[str, Any] | None = None,
    open_question: dict[str, Any] | None = None,
    turn_no=2,
) -> ChatbotTurn:
    created = NOW - timedelta(seconds=age_s)
    if trace is None:
        trace = [{"kind": "state", "focus": focus or _focus_wire("ELP3754"), "open_question": open_question, "turn_no": turn_no}]
    row = ChatbotTurn(
        id=str(uuid.uuid4()),
        contact_respond_id=contact,
        message_id=f"ZZT-{uuid.uuid4().hex[:10]}",
        envelope={},
        is_test=is_test,
        status="done",
        response={"reply": {"text": reply}},
        trace=trace,
        created_at=created,
        finished_at=created + timedelta(seconds=5),
    )
    db.add(row)
    db.commit()
    return row


def _outgoing(db, *, message_id: str, turn_id: str | None, contact=CONTACT) -> None:
    db.add(
        ChatHistory(
            channel="whatsapp",
            contact_id=contact,
            phone_number="+60160000531",
            message=QUESTION,
            sent_at=NOW.replace(tzinfo=None) - timedelta(seconds=55),
            type="outgoing",
            message_id=message_id,
            chatbot_turn_id=turn_id,
        )
    )
    db.commit()


def _resolve(db, *, reply_to_id="1780000000000001", quoted_text=QUESTION, contact=CONTACT, is_test=False, before=None):
    from app.services.chatbot.quoted import resolve_quoted_turn

    return resolve_quoted_turn(
        db,
        contact_respond_id=contact,
        reply_to_id=reply_to_id,
        quoted_text=quoted_text,
        is_test=is_test,
        before=before or NOW,
    )


def test_ac_qr_5_resolves_by_id_through_the_outgoing_row(db):
    pending = _pending_wire()
    turn = _turn(db, reply="text the dealer's client shows differently", open_question=pending, turn_no=4)
    _outgoing(db, message_id="1780000000000001", turn_id=turn.id)
    found = _resolve(db, quoted_text="not the stored reply at all")
    assert found is not None
    assert found.via == "id"
    assert found.turn_id == turn.id
    assert found.focus == turn.trace[0]["focus"]
    assert found.open_question == pending
    assert found.turn_no == 4


def test_ac_qr_5_id_route_beats_the_text_route(db):
    by_id = _turn(db, reply="first wording", age_s=300)
    _turn(db, reply=QUESTION, age_s=30)
    _outgoing(db, message_id="1780000000000001", turn_id=by_id.id)
    found = _resolve(db)
    assert found.via == "id"
    assert found.turn_id == by_id.id


def test_ac_qr_14_id_row_of_another_contact_is_never_used(db):
    theirs = _turn(db, contact=OTHER)
    _outgoing(db, message_id="1780000000000001", turn_id=theirs.id, contact=OTHER)
    assert _resolve(db, quoted_text="no stored reply says this") is None


def test_ac_qr_14_id_row_pointing_at_another_contacts_turn_is_never_used(db):
    theirs = _turn(db, contact=OTHER)
    _outgoing(db, message_id="1780000000000001", turn_id=theirs.id, contact=CONTACT)
    assert _resolve(db, quoted_text="no stored reply says this") is None


def test_ac_qr_6_text_fallback_when_there_is_no_row(db):
    turn = _turn(db)
    found = _resolve(db, quoted_text="  How many   units of\nELP3754?  ")
    assert found is not None
    assert (found.via, found.turn_id) == ("text", turn.id)


def test_ac_qr_6_text_fallback_when_the_row_is_unstamped(db):
    turn = _turn(db)
    _outgoing(db, message_id="1780000000000001", turn_id=None)
    found = _resolve(db)
    assert (found.via, found.turn_id) == ("text", turn.id)


def test_ac_qr_6_text_fallback_takes_the_latest_before_this_message(db):
    _turn(db, age_s=600)
    middle = _turn(db, age_s=300)
    _turn(db, age_s=10)  # created after `before`
    found = _resolve(db, before=NOW - timedelta(seconds=100))
    assert found.turn_id == middle.id


def test_ac_qr_6_text_fallback_never_reads_another_contact(db):
    _turn(db, contact=OTHER)
    assert _resolve(db) is None


def test_ac_qr_14_text_fallback_never_crosses_the_is_test_world(db):
    live = _turn(db, is_test=False)
    dry = _turn(db, is_test=True, age_s=30)
    assert _resolve(db, is_test=False).turn_id == live.id
    assert _resolve(db, is_test=True).turn_id == dry.id


def test_ac_qr_14_a_dry_run_message_never_resolves_a_live_turn(db):
    _turn(db, is_test=False)
    assert _resolve(db, is_test=True) is None


def test_ac_qr_12_nothing_matches_resolves_to_none(db):
    _turn(db, reply="a different sentence")
    assert _resolve(db, reply_to_id="1780000000000009") is None


def test_ac_qr_15_state_event_is_read_first_and_memory_only_when_absent(db):
    state_focus = _focus_wire("ELP3754")
    memory_focus = _focus_wire("SRTWC287-S-150")
    both = _turn(
        db,
        trace=[
            {"kind": "memory", "focus": {"after": memory_focus}, "open_question": {"after": None}},
            {"kind": "state", "focus": state_focus, "open_question": None, "turn_no": 3},
        ],
    )
    assert _resolve(db).focus == state_focus
    db.delete(both)
    db.commit()
    pending = _pending_wire()
    only_memory = _turn(
        db,
        trace=[{"kind": "memory", "focus": {"after": memory_focus}, "open_question": {"after": pending}}],
    )
    found = _resolve(db)
    assert found.turn_id == only_memory.id
    assert found.focus == memory_focus
    assert found.open_question == pending


def test_ac_qr_12_a_turn_with_neither_event_resolves_to_none(db):
    _turn(db, trace=[{"stage": "received", "facts": {}}])
    assert _resolve(db) is None


def test_ac_qr_2_link_outgoing_row_returns_an_explicit_turn_id_as_given(db):
    from app.services.chatbot.quoted import link_outgoing_row

    explicit = str(uuid.uuid4())
    got = link_outgoing_row(
        db,
        contact_id=CONTACT,
        turn_id=None,
        message="anything",
        sent_at=NOW.replace(tzinfo=None),
        explicit=explicit,
    )
    assert got == explicit
