"""CHAT-TURN-COST: the chatbot module's usage-row writers that feed `turn_costs` / `llm_calls`.

Moved here from `tests/analytics_connector/test_turn_costs.py`: these tests drive
`app.services.chatbot.usage` and `llm_call` directly, and only `tests/chatbot/` may import the
chatbot package (`test_import_boundary.py`, AC-002). The connector-side tests stay where they were.

Runs on the bootstrapped database (`pg_session`); every row is ZZT-marked and rolled back.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests._pg_fixture import pg_session, unique_code

T0 = datetime(2026, 9, 2, 2, 0, 0, tzinfo=timezone.utc)  # 10:00 Malaysia


def test_parser_usage_row_carries_cached_tokens():
    from app.services.chatbot.usage import record_parser_usage

    with pg_session() as db:
        turn_id = str(uuid.uuid4())
        record_parser_usage(
            db, usage={"provider": "openai", "model": "gpt-5.4-mini", "prompt_tokens": 100, "completion_tokens": 5,
                       "total_tokens": 105, "cached_tokens": 64},
            response_time_ms=12, contact_respond_id="rio-none", answered=True, chatbot_turn_id=turn_id,
        )
        row = db.execute(text("SELECT feature, cached_tokens FROM ai_assistant_usage_logs WHERE chatbot_turn_id = :t"),
                         {"t": turn_id}).one()
        assert tuple(row) == ("chatbot_parser", 64)


def test_clarifier_usage_row_is_recorded_for_the_turn():
    from app.services.chatbot.usage import CLARIFIER_FEATURE, record_turn_llm_usage

    assert CLARIFIER_FEATURE == "chatbot_clarifier"
    with pg_session() as db:
        turn_id = str(uuid.uuid4())
        record_turn_llm_usage(
            db, feature=CLARIFIER_FEATURE,
            usage={"provider": "openai", "model": "gpt-5.4-mini", "prompt_tokens": 900, "completion_tokens": 40,
                   "total_tokens": 940, "cached_tokens": 0},
            response_time_ms=30, contact_respond_id="rio-none", answered=True, chatbot_turn_id=turn_id,
        )
        row = db.execute(text("SELECT feature, prompt_tokens, completion_tokens FROM ai_assistant_usage_logs "
                              "WHERE chatbot_turn_id = :t"), {"t": turn_id}).one()
        assert tuple(row) == ("chatbot_clarifier", 900, 40)


def test_llm_call_capture_collects_usage_of_calls_made_inside_it(monkeypatch):
    from app.services.chatbot import llm_call
    from app.services.llm_provider import ChatResult

    class Fake:
        def chat(self, messages, model=None, **kw):
            return ChatResult(content="{}", prompt_tokens=10, completion_tokens=2, total_tokens=12,
                              tool_calls=[], raw=None, cached_tokens=4)

    monkeypatch.setattr("app.services.llm_provider.get_provider", lambda *a, **k: Fake())
    llm_call.chat("openai", "k", "gpt-x", [])  # outside a capture: nothing collected, nothing breaks
    with llm_call.capture_usage() as calls:
        llm_call.chat("openai", "k", "gpt-x", [])
    assert calls == [{"provider": "openai", "model": "gpt-x", "prompt_tokens": 10, "completion_tokens": 2,
                      "total_tokens": 12, "cached_tokens": 4}]


@pytest.mark.parametrize("feature", ["chatbot_parser", "chatbot_clarifier"])
def test_a_contact_with_a_duplicate_respond_io_id_still_gets_its_usage_row(feature):
    """`respond_io_id` is not unique. A bare `.scalar()` raised MultipleResultsFound on such a
    contact and the telemetry `except` dropped the whole row, so its turns cost nothing in
    turn_costs (crew audit 13 Oct; same fix as PARSER-GEMINI-LITE #1624). The oldest row stands."""
    from app.services.chatbot.usage import record_turn_llm_usage

    with pg_session() as db:
        rid = f"rio-dup-{uuid.uuid4().hex[:10]}"
        older, newer = unique_code("rcold"), unique_code("rcnew")
        for cid, at in ((older, T0), (newer, T0 + timedelta(days=1))):
            db.execute(text("INSERT INTO respond_contacts (id, phone_number, name, respond_io_id, created_at) "
                            "VALUES (:i, :p, 'ZZT dup', :r, :t)"),
                       {"i": cid, "p": f"+6010000{uuid.uuid4().int % 10000:04d}", "r": rid, "t": at})
        turn_id = str(uuid.uuid4())
        record_turn_llm_usage(
            db, feature=feature,
            usage={"provider": "openai", "model": "gpt-5.4-mini", "prompt_tokens": 100, "completion_tokens": 5,
                   "total_tokens": 105, "cached_tokens": 64},
            response_time_ms=12, contact_respond_id=rid, answered=True, chatbot_turn_id=turn_id,
        )
        row = db.execute(text("SELECT contact_id, cached_tokens FROM ai_assistant_usage_logs WHERE chatbot_turn_id = :t"),
                         {"t": turn_id}).one()
        assert tuple(row) == (older, 64)
