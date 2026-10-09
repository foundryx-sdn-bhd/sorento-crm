"""PHOTO-SEND-CAP round 2, review B1: link mode through `run_tail` (n8n's `/complete` path).

No scenario under the current engine still delegates a turn, so the row is put at
`delegated` by hand, the way `test_escalation_control_complete.py` does.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.config import settings as app_settings
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from tests.chatbot.test_complete_turn import _fragments, seeded, stub_parser  # noqa: F401 - fixtures
from tests.chatbot.test_engine import CONTACT_ID, _envelope
from tests.chatbot.test_file_mode_turns import BASE_URL, _links_on, _replied_facts


def _delegated_turn(session_factory) -> str:
    head = engine_mod.run_turn(_envelope(is_test=False), session_factory=session_factory)
    db = session_factory()
    db.query(ChatbotTurn).filter(ChatbotTurn.id == head.turn_id).update(
        {"status": "delegated", "stage": "routed", "response": None}
    )
    db.commit()
    db.close()
    return head.turn_id


def _answer_with_a_file() -> dict[str, Any]:
    return {
        "outcome_fragment": {
            "central-exchange": {
                "response": "Here is your sales analysis.",
                "attachments": [{"url": "s3://zzt/sales.xlsx", "filename": "Sales.xlsx"}],
            }
        }
    }


def test_run_tail_sends_one_text_with_the_link(seeded, stub_parser, session_factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app_settings, "frontend_base_url", BASE_URL)
    turn_id = _delegated_turn(session_factory)
    _links_on(session_factory)

    result = engine_mod.complete_turn(turn_id, _fragments(answer=_answer_with_a_file()), session_factory=session_factory)

    # On this path n8n sends from `reply`: `attachments_src` is what `send-attachments` reads.
    assert result.reply["attachments_src"] is None
    assert not [a for a in result.actions if a.get("kind") == "send_attachments"]
    assert f"*File:* Sales.xlsx\n*Link:* {BASE_URL}/f/" in result.reply["text"]
    facts = _replied_facts(session_factory, turn_id)
    assert facts.get("file_mode") == "links"
    assert facts.get("files") == 1


def test_run_tail_in_attachment_mode_is_unchanged(seeded, stub_parser, session_factory, monkeypatch):  # noqa: F811
    turn_id = _delegated_turn(session_factory)
    result = engine_mod.complete_turn(turn_id, _fragments(answer=_answer_with_a_file()), session_factory=session_factory)
    assert result.reply["attachments_src"]["attachments"] == [{"url": "s3://zzt/sales.xlsx", "filename": "Sales.xlsx"}]
    assert "/f/" not in result.reply["text"]
    assert _replied_facts(session_factory, turn_id).get("file_mode") == "attachments"
