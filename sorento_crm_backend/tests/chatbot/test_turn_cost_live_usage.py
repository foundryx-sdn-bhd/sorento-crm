"""CHAT-TURN-COST (crew audit 13 Oct): live turns write their parser usage row, cached tokens included.

`turn_costs` is only as good as `ai_assistant_usage_logs`. Crew found no chatbot rows on dev
after 18 Sep. These tests run real `engine.run_turn` turns, with the provider faked below
`head/parser.parse` so the real parse, usage hand-off and write all run. They pin:

* every LIVE turn writes one `chatbot_parser` row carrying its turn id and the cache hit;
* a console / test turn (`dry_run`) writes none, by design (D14);
* a contact whose `respond_io_id` is shared by another row still gets its row (it was
  dropped by `MultipleResultsFound` before the fix).
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
)


@pytest.fixture()
def fake_provider(monkeypatch):
    from app.services.llm_provider import ChatResult

    class FakeProvider:
        def chat(self, messages, model=None, **kw):
            return ChatResult(content=json.dumps(_parser_output(asks=[], topic_reset=False)), prompt_tokens=38040, completion_tokens=435,
                              total_tokens=38475, tool_calls=[], raw=None, cached_tokens=37022)

    monkeypatch.setattr(
        parser_mod, "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=52, provider="openai", model="gpt-5.4-mini", api_key="sk-test"),
    )
    monkeypatch.setattr("app.services.llm_provider.get_provider", lambda *a, **k: FakeProvider())


def _envelope_for(message_id: str, **overrides):
    """`_envelope`'s turn with its own respond message id, so D15 does not dedupe it."""
    message = {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": message_id,
            "contactId": CONTACT_ID,
            "channelId": "whatsapp",
            "traffic": "incoming",
            "message": {"type": "text", "text": "price for SRTWC8517"},
        },
    }
    return _envelope(message=message, **overrides)


def _parser_rows(session_factory, turn_ids):
    db = session_factory()
    try:
        return db.execute(
            text("SELECT chatbot_turn_id, model, prompt_tokens, cached_tokens, completion_tokens, was_answered "
                 "FROM ai_assistant_usage_logs WHERE feature = 'chatbot_parser' AND chatbot_turn_id = ANY(:t)"),
            {"t": [str(t) for t in turn_ids]},
        ).all()
    finally:
        db.close()


def test_live_turns_each_write_a_parser_usage_row_with_cached_tokens(session_factory, seeded, stub_access, fake_provider):
    stub_access()
    turn_ids = []
    for i in range(5):
        result = engine_mod.run_turn(_envelope_for(f"ZZT-ctc-live-{i}-{uuid.uuid4().hex[:6]}"),
                                     session_factory=session_factory)
        turn_ids.append(result.turn_id)
    rows = _parser_rows(session_factory, turn_ids)
    assert sorted(r[0] for r in rows) == sorted(str(t) for t in turn_ids)
    # was_answered true: the SUCCESS path wrote these (`engine.py`, access stage), not a parse failure.
    assert {tuple(r[1:]) for r in rows} == {("gpt-5.4-mini", 38040, 37022, 435, True)}


def test_a_console_test_turn_writes_no_usage_row(session_factory, seeded, stub_access, fake_provider):
    stub_access()
    env = _envelope_for(f"ZZT-ctc-console-{uuid.uuid4().hex[:6]}", test_run_id="ZZT-ctc-console")
    assert env.dry_run is True
    result = engine_mod.run_turn(env, session_factory=session_factory)
    assert _parser_rows(session_factory, [result.turn_id]) == []


def test_a_live_turn_of_a_duplicated_contact_still_writes_its_row(session_factory, seeded, stub_access, fake_provider):
    db = session_factory()
    db.execute(
        text("INSERT INTO respond_contacts (id, phone_number, name, respond_io_id, created_at) "
             "VALUES (:i, :p, 'ZZT dup twin', :r, now())"),
        {"i": f"ZZT-twin-{uuid.uuid4().hex[:8]}", "p": f"+6010000{uuid.uuid4().int % 10000:04d}", "r": str(CONTACT_ID)},
    )
    db.commit()
    db.close()
    stub_access()
    result = engine_mod.run_turn(_envelope_for(f"ZZT-ctc-dup-{uuid.uuid4().hex[:6]}"), session_factory=session_factory)
    assert len(_parser_rows(session_factory, [result.turn_id])) == 1



def test_the_live_usage_probe_reports_a_row_for_every_parsed_turn(session_factory, seeded, stub_access, fake_provider):
    """`scripts/chatbot_live_usage_probe.py` is the hand-test's way to make fresh live turns on a
    copy (console turns are test turns and write no usage by design)."""
    from scripts.chatbot_live_usage_probe import run_probe

    stub_access()
    results = run_probe(session_factory, turns=4)
    assert len(results) == 4
    assert all(not r["is_test"] for r in results)
    parsed = [r for r in results if r["reached_parser"]]
    assert len(parsed) == 4, results
    for r in parsed:
        assert ("chatbot_parser", "gpt-5.4-mini", 38040, 37022, 435) in r["calls"], r
