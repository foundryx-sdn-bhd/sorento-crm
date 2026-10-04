"""ESCALATION-CONTROL, security review S1: n8n's `/complete` path hands the tail no APPLY
state, so `complete_turn` must read the contact's own profile and give it to `run_tail`, or
a blocked contact would be shown the team offer and the CS roster on that path.

No scenario under the current engine still delegates a turn (`test_complete_turn.py`'s
docstring), so the row is put at `delegated` by hand and `run_tail` is captured at its
boundary: this pins the wiring, not a reachable live turn (UNVERIFIED end to end).
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.services.chatbot import engine as engine_mod
from tests.chatbot.test_complete_turn import _fragments, seeded, stub_parser  # noqa: F401 - fixtures
from tests.chatbot.test_engine import CONTACT_ID, _envelope


def _delegated_turn(session_factory) -> str:
    head = engine_mod.run_turn(_envelope(is_test=False), session_factory=session_factory)
    from app.models.chatbot_turn import ChatbotTurn

    db = session_factory()
    db.query(ChatbotTurn).filter(ChatbotTurn.id == head.turn_id).update(
        {"status": "delegated", "stage": "routed", "response": None}
    )
    db.commit()
    return head.turn_id


@pytest.mark.parametrize("allowed", [True, False])
def test_complete_turn_hands_the_contacts_own_profile_to_the_tail(
    allowed, seeded, stub_parser, session_factory, monkeypatch
) -> None:
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET escalation_allowed = :a WHERE respond_io_id = :c"),
        {"a": allowed, "c": str(CONTACT_ID)},
    )
    db.commit()
    turn_id = _delegated_turn(session_factory)
    seen: dict[str, Any] = {}

    def fake_run_tail(db, **kwargs):
        seen.update(kwargs)
        return {"text": "ok", "quick_replies": None, "result_set": [], "attachments_src": None}, None

    monkeypatch.setattr(engine_mod, "run_tail", fake_run_tail)
    engine_mod.complete_turn(turn_id, _fragments(), session_factory=session_factory)
    assert seen, "complete_turn never reached the tail"
    assert getattr(seen.get("profile"), "escalation_allowed", None) is allowed, seen.get("profile")
