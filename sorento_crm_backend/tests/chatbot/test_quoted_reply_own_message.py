"""QUOTED-REPLY round 5: the dealer quotes their OWN earlier message (AC-QR-21, AC-QR-22).

Resolver call shape chosen here: `resolve_quoted_turn(..., sender_source="contact")`. With it the
resolver reads `chatbot.turns` by `message_id == reply_to_id` only (no chat_histories hop, no text
route) and returns `via == "own"`.

Every id is synthetic; codes are catalogue codes.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.quoted import resolve_quoted_turn

from tests.chatbot._avail_mode_console import AvailConsole, Stock, _seed
from tests.chatbot._r9_engine_console import product, reply, stock
from tests.chatbot.test_engine import CONTACT_ID, _envelope, stub_access  # noqa: F401 - a pytest fixture

pytestmark = pytest.mark.usefixtures("stub_access")

C = "SRTWC286-SH-UF"
E2 = "SRTBF11841"
CONTACT = str(CONTACT_ID)
OWN_TEXT = f"{E2} have stock?"


def _stored(session_factory, *, message_id: str, reply_text: str = "How many?", contact: str = CONTACT,
            is_test: bool = False, focus: dict | None = None) -> ChatbotTurn:
    created = datetime.now(tz=timezone.utc) - timedelta(seconds=600)
    focus = focus or {"products": [{"raw": "X1", "canonical_code": "X1"}], "tasks": []}
    row = ChatbotTurn(
        id=str(uuid.uuid4()),
        contact_respond_id=contact,
        message_id=message_id,
        envelope={},
        is_test=is_test,
        status="done",
        response={"reply": {"text": reply_text}},
        trace=[{"kind": "state", "focus": focus, "open_question": None, "turn_no": 1}],
        created_at=created,
        finished_at=created + timedelta(seconds=4),
    )
    db = session_factory()
    db.add(row)
    db.commit()
    return row


def _resolve(session_factory, *, reply_to_id, quoted_text="whatever the dealer typed", is_test=False):
    db = session_factory()
    return resolve_quoted_turn(
        db,
        contact_respond_id=CONTACT,
        reply_to_id=reply_to_id,
        quoted_text=quoted_text,
        is_test=is_test,
        before=datetime.now(tz=timezone.utc),
        sender_source="contact",
    )


# --------------------------------------------------------------------------- #
# AC-QR-21: the resolver
# --------------------------------------------------------------------------- #


def test_ac_qr_21_own_message_resolves_by_message_id(session_factory):
    mid = f"ZZT-own-{uuid.uuid4().hex[:8]}"
    row = _stored(session_factory, message_id=mid)
    found = _resolve(session_factory, reply_to_id=mid)
    assert found is not None
    assert (found.via, found.turn_id) == ("own", row.id)
    assert found.focus == row.trace[0]["focus"]
    assert found.open_question is None


def test_ac_qr_21_another_contacts_turn_is_none(session_factory):
    mid = f"ZZT-own-{uuid.uuid4().hex[:8]}"
    _stored(session_factory, message_id=mid, contact="900000098")
    assert _resolve(session_factory, reply_to_id=mid) is None


def test_ac_qr_21_the_other_world_is_none(session_factory):
    mid = f"ZZT-own-{uuid.uuid4().hex[:8]}"
    _stored(session_factory, message_id=mid, is_test=True)
    assert _resolve(session_factory, reply_to_id=mid, is_test=False) is None


def test_ac_qr_21_no_such_turn_is_none(session_factory):
    assert _resolve(session_factory, reply_to_id=f"ZZT-none-{uuid.uuid4().hex[:8]}") is None


def test_ac_qr_21_an_own_quote_never_takes_the_text_route(session_factory):
    _stored(session_factory, message_id=f"ZZT-other-{uuid.uuid4().hex[:8]}", reply_text="Bot said this exact line")
    assert _resolve(session_factory, reply_to_id=f"ZZT-none-{uuid.uuid4().hex[:8]}",
                    quoted_text="Bot said this exact line") is None


# --------------------------------------------------------------------------- #
# AC-QR-22: through the engine
# --------------------------------------------------------------------------- #


def _console(session_factory, monkeypatch, stub_access, phone: str) -> AvailConsole:
    c = AvailConsole(
        session_factory, monkeypatch, stub_access, phone=phone,
        SRTBF11841=Stock(eta=date(2026, 11, 3)),
    )
    c.codes.update(_seed(session_factory, [E2]))
    c.uuid_of = {code: pid for pid, code in c.codes.items()}
    return c


def _say(c: AvailConsole, text: str, v: dict[str, Any], *, message_id: str,
         quote: tuple[str, str] | None = None) -> str:
    """One turn with a CONTROLLED user messageId; `quote` = (replyTo id, quoted text), own source."""
    c._turns += 1
    c._next = v
    inner: dict[str, Any] = {
        "messageId": message_id,
        "contactId": CONTACT_ID,
        "channelId": "whatsapp",
        "traffic": "incoming",
        "message": {"type": "text", "text": text},
    }
    if quote is not None:
        inner["replyTo"] = {"id": quote[0], "sender": {"source": "contact"}, "message": {"text": quote[1]}}
    out = engine_mod.run_turn(
        _envelope(
            is_test=True,
            previous_conversation_state=c.state,
            message={"event_type": "message.received", "contact": {"id": CONTACT_ID}, "message": inner},
        ),
        session_factory=c.session_factory,
    )
    assert out.error is None, out.error
    c.state = out.session_patch
    return (out.reply or {}).get("text") or ""


def _open_labels(c: AvailConsole) -> list[str]:
    tasks = ((c.state or {}).get("focus") or {}).get("tasks") or []
    return [s["label"] for t in tasks if t.get("status") == "open" for s in t.get("slots", [])]


def _quoted_facts(c: AvailConsole, text: str) -> dict[str, Any]:
    db = c.session_factory()
    for row in db.query(ChatbotTurn).filter(ChatbotTurn.contact_respond_id == CONTACT).all():
        body = (((row.envelope or {}).get("message") or {}).get("message") or {}).get("message") or {}
        if isinstance(body, dict) and body.get("text") == text:
            received = next(e for e in row.trace if e.get("stage") == "received")
            return received["facts"].get("quoted_turn")
    raise AssertionError(f"no turn for {text!r}")


def test_ac_qr_22_quoting_own_message_runs_on_that_turns_state(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000501")
    m1 = f"ZZT-own-{uuid.uuid4().hex[:8]}"
    assert _say(c, OWN_TEXT, stock(product(E2)), message_id=m1) == f"How many units of {E2}?"
    assert _say(c, f"check stock {C}", stock(product(C)), message_id=f"ZZT-own-{uuid.uuid4().hex[:8]}") \
        == f"How many units of {C}?"
    c.tool_calls.clear()

    out = _say(c, "and eta?", reply(entity_op="reuse", domain_hint="incoming", intent_hint="check_incoming"),
               message_id=f"ZZT-own-{uuid.uuid4().hex[:8]}", quote=(m1, OWN_TEXT))

    incoming = [args for name, args in c.tool_calls if name.startswith("crm_incoming_stock")]
    assert incoming, [name for name, _ in c.tool_calls]
    asked = {pid for args in incoming for pid in (args.get("product_ids") or [])}
    assert c.uuid_of[E2] in asked
    assert c.uuid_of[C] not in asked
    assert E2 in out and C not in out, out
    facts = _quoted_facts(c, "and eta?")
    assert facts is not None and facts["found"] is True and facts["via"] == "own"
    assert _open_labels(c) == [C] or C in _open_labels(c), "R4: the live open task stays open"


def test_ac_qr_22_own_quote_with_no_such_turn_runs_as_unquoted(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000502")
    assert _say(c, f"check stock {C}", stock(product(C)), message_id=f"ZZT-own-{uuid.uuid4().hex[:8]}") \
        == f"How many units of {C}?"
    saved = c.state
    verdict = reply(demand_qty=1, open_question_answer={"mode": "fill", "picked": [],
                                                         "items": [{"position": 1, "code": C, "qty": 1}],
                                                         "qty_for_all": None})
    plain = _say(c, "1 unit", verdict, message_id=f"ZZT-own-{uuid.uuid4().hex[:8]}")
    c.state = saved
    quoted = _say(c, "1 unit", verdict, message_id=f"ZZT-own-{uuid.uuid4().hex[:8]}",
                  quote=(f"ZZT-ghost-{uuid.uuid4().hex[:8]}", "a message nobody stored"))
    assert quoted == plain
    facts = _quoted_facts(c, "1 unit")
    assert facts is not None and facts["found"] is False
