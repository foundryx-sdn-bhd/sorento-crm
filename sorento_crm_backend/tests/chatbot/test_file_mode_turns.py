"""PHOTO-SEND-CAP round 2, review B1: link mode through a WHOLE turn, on both tails.

* The composer tail (`run_turn`, a business answer the CRM finishes itself): the turn
  hands back ONE `send_message` and no `send_attachments`, the text carries the short
  link, the replied trace stage records `file_mode: links` and the file count, and the
  tool runner's lane context carries the contact's mode.
* `run_tail` (n8n's `/complete` path): `test_file_mode_complete.py`.

Attachment mode on the same turn is `test_s6_s7_integration.py`'s AC-507 case, unchanged.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.config import settings as app_settings
from app.models.chatbot_file_link import ChatbotFileLink
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from tests.chatbot.test_engine import CONTACT_ID, _envelope, seeded, stub_access, stub_parser  # noqa: F401

BASE_URL = "https://crm.test"


def _links_on(session_factory) -> None:
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET chatbot_files_as_links = true WHERE respond_io_id = :c"),
        {"c": str(CONTACT_ID)},
    )
    db.commit()
    db.close()


def _replied_facts(session_factory, turn_id: str) -> dict[str, Any]:
    db = session_factory()
    row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).one()
    stages = row.trace.get("stages") if isinstance(row.trace, dict) else row.trace
    records = stages if isinstance(stages, list) else []
    db.close()
    replied = [r for r in records if isinstance(r, dict) and r.get("stage") == "replied"]
    assert replied, row.trace
    return replied[-1].get("facts") or {}


@pytest.mark.parametrize("is_test", [False, True], ids=["live", "console_dry_run"])
def test_the_composer_tail_sends_one_text_with_the_link(
    is_test, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch  # noqa: F811
):
    """`is_test=True` is the chatbot console's dry run (D14): its links must still open,
    so the row is read back from a fresh session (crew audit item 4)."""
    from tests.chatbot import test_s6_s7_integration as harness

    monkeypatch.setattr(app_settings, "frontend_base_url", BASE_URL)
    harness.TestSendActionsAttachmentsAndDuplicateAc507D15._prepare(
        session_factory, system_settings_row, stub_parser, stub_access, monkeypatch
    )
    lane_ctx: list[Any] = []
    original = engine_mod.business.run_fetch

    def _spy(payload, **kwargs):
        lane_ctx.append(payload)
        return original(payload, **kwargs)

    monkeypatch.setattr(engine_mod.business, "run_fetch", _spy)
    _links_on(session_factory)

    result = engine_mod.run_turn(_envelope(is_test=is_test), session_factory=session_factory)

    assert result.status == "done", result.error
    assert [a["kind"] for a in result.actions] == ["send_message"]
    assert result.reply["attachments_src"] is None
    assert f"{BASE_URL}/f/" in result.actions[0]["text"]
    assert "s3://zzt/spec.pdf" not in result.actions[0]["text"]
    facts = _replied_facts(session_factory, result.turn_id)
    assert facts.get("file_mode") == "links"
    assert facts.get("files") == 1
    assert lane_ctx and all((c.get("ctx") or {}).get("files_as_links") is True for c in lane_ctx)
    db = session_factory()
    assert db.query(ChatbotFileLink).filter(ChatbotFileLink.turn_id == result.turn_id).count() == 1
    db.close()
