"""PHOTO-PICKER (owner, 5 Oct 2026): a pick off this conversation's own roster is not a
carry from memory, so it does not open with "Carrying on from <day>: ...".

The owner asked "photo srtwc286", the photo picker listed ten codes, and "1" picked
SRTWC286-SH. The reply opened "Carrying on from Mon 5 Oct: incoming stock for
SRTWC286-SH." - the subject of an EARLIER, closed conversation about incoming, printed
over a photo answer. `engine._carried_line` read the picked code as carried from memory
because the customer never typed "SRTWC286-SH" themselves (the roster did), and then found
the newest closed conversation that named it.

The case is the owner's own shape. The frame is seeded on a blank Postgres schema.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.models.conversation_frame import ConversationFrame
from app.services.chatbot import engine as engine_mod

CID = "ZZT-photo-picker-carry"


def _seed_incoming_frame(session_factory) -> None:
    """An earlier conversation the same contact closed, about incoming for the code."""
    db = session_factory()
    when = datetime.now() - timedelta(hours=2)
    db.add(
        ConversationFrame(
            contact_id=CID,
            contact_respond_id=CID,
            space_id="0",
            channel="whatsapp",
            domain="incoming",
            status="closed",
            close_reason="topic_switch",
            summary="Asked incoming for SRTWC286-SH.",
            entities={"product": ["SRTWC286-SH"]},
            turn_ids=[f"ZZT-photo-picker-frame-{uuid.uuid4().hex[:6]}"],
            started_at=when,
            opened_at=when,
            last_activity_at=when,
            closed_at=when,
        )
    )
    db.commit()


def _memory_intake() -> dict:
    # The customer's own words this conversation: the photo ask, never the picked code.
    return {
        "effective_level": "episodes",
        "earlier_messages": [{"created_at": "Mon 5 Oct 10:00", "text": "photo srtwc286"}],
        "profile_facts": None,
    }


def _picked_verdict() -> dict:
    # The parser's echo of the picked option (`entity_op: reuse`): a carried entity.
    return {
        "domain_hint": "product_attachment",
        "entities": [
            {"raw": "SRTWC286-SH", "hint": "product", "canonical_code": "SRTWC286-SH", "current_message": False}
        ],
    }


def test_a_pick_off_the_live_roster_opens_with_no_carried_line(session_factory) -> None:
    _seed_incoming_frame(session_factory)
    line = engine_mod._carried_line(
        session_factory(),
        verdict=_picked_verdict(),
        memory_intake=_memory_intake(),
        contact_respond_id=CID,
        dry_run=False,
        decision={"kind": "answer", "why": "position"},
    )
    assert line is None, f"a pick off this conversation's roster printed a carried line: {line!r}"


def test_a_real_carry_from_memory_still_names_what_it_carried(session_factory) -> None:
    """The guard: the same carried code on a turn that answered no open question (a new
    conversation picking up yesterday's subject) keeps AC-MEM083's line."""
    _seed_incoming_frame(session_factory)
    line = engine_mod._carried_line(
        session_factory(),
        verdict=_picked_verdict(),
        memory_intake={**_memory_intake(), "earlier_messages": []},
        contact_respond_id=CID,
        dry_run=False,
        decision={"kind": "carry", "why": "no_subject_named"},
    )
    assert line is not None and line.startswith("Carrying on from "), line
    assert "SRTWC286-SH" in line
