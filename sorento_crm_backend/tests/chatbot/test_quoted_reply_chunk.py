"""QUOTED-REPLY AC-QR-25: quoting one chunk of a split bot reply resolves to the turn that wrote it.

Text fallback rule: whitespace-squashed equality, or a substring when the quoted text has at
least 40 squashed characters; the newest matching turn wins. Every id is synthetic.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot.quoted import resolve_quoted_turn
from tests._pg_fixture import blank_session
from tests.chatbot._r9_engine_console import answer, product, reply
from tests.chatbot.test_engine import CONTACT_ID, stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_quoted_reply_engine import (  # noqa: F401
    A,
    B,
    _console,
    _focus_wire,
    _live_c,
    _store_turn,
    quoted_facts,
    say_quoted,
)

CONTACT = "900000041"
LONG = "\n".join(f"{i}. SRTWC{i:04d}-SH-UF  Sample product line number {i}" for i in range(1, 41))


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


def _row(db, reply_text: str, *, age_s: int) -> ChatbotTurn:
    created = datetime.now(tz=timezone.utc) - timedelta(seconds=age_s)
    row = ChatbotTurn(
        id=str(uuid.uuid4()),
        contact_respond_id=CONTACT,
        message_id=f"ZZT-ck-{uuid.uuid4().hex[:8]}",
        envelope={},
        is_test=False,
        status="done",
        response={"reply": {"text": reply_text}},
        trace=[{"kind": "state", "focus": {"products": [], "tasks": []}, "open_question": None, "turn_no": 1}],
        created_at=created,
        finished_at=created + timedelta(seconds=3),
    )
    db.add(row)
    db.commit()
    return row


def _resolve(db, quoted_text: str):
    return resolve_quoted_turn(
        db,
        contact_respond_id=CONTACT,
        reply_to_id=None,
        quoted_text=quoted_text,
        is_test=False,
        before=datetime.now(tz=timezone.utc),
    )


def test_ac_qr_25_the_second_chunk_resolves_by_text(db):
    row = _row(db, LONG, age_s=600)
    found = _resolve(db, LONG[700:1400])
    assert found is not None
    assert (found.via, found.turn_id) == ("text", row.id)


def test_ac_qr_25_a_short_fragment_that_is_only_a_substring_is_none(db):
    _row(db, "Thanks, ok, noted. We will check ok and come back to you shortly.", age_s=600)
    assert re.sub(r"\s+", "", "ok") and _resolve(db, "ok") is None


def test_ac_qr_25_a_short_fragment_equal_to_the_reply_resolves(db):
    text = "Routed to your PIC, they will reply shortly."
    row = _row(db, text, age_s=600)
    found = _resolve(db, text)
    assert found is not None and found.turn_id == row.id


def test_ac_qr_25_the_newest_of_two_matching_turns_wins(db):
    _row(db, LONG, age_s=900)
    newer = _row(db, LONG, age_s=300)
    found = _resolve(db, LONG[700:1400])
    assert found is not None and found.turn_id == newer.id


def test_ac_qr_25_engine_quote_of_a_middle_chunk_picks_in_that_turn(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60900000601")
    numbered = "\n".join(f"{i}. {A if i == 1 else B}  variant line number {i}" for i in range(1, 41))
    assert len(numbered) > 1100
    options = [{"position": 1, "code": A}, {"position": 2, "code": B}]
    from app.services.chatbot.turn import pending as turn_pending

    stored = _store_turn(
        c,
        reply_text=numbered,
        focus=_focus_wire(c, A),
        open_question=turn_pending.to_wire(turn_pending.ask("product_pick", options, asked_at_turn=1)),
        age_s=900,
    )
    _live_c(c)
    say_quoted(
        c, "1", reply(open_question_answer=answer("pick", picked=[1])), quote=numbered[500:1100]
    )
    facts = quoted_facts(c, "1")
    assert facts is not None
    assert (facts["found"], facts["via"], facts["turn_id"]) == (True, "text", stored.id)
