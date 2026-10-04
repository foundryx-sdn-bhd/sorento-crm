"""LLM-TEST-COST: the `understood` trace facts carry the prompt-cache hit and the cassette
mode, so an operator (and `scripts/chatbot_console_check.py`) can see both per turn.

Postgres only (`tests/chatbot/conftest.py::session_factory`, blank scratch schema).
"""
from __future__ import annotations

from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.test_engine import (  # noqa: F401
    _envelope,
    _parser_output,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_memory_trace_and_usage import CONTACT_ID, _seed_contact


def _understood_facts(session_factory, stub_parser, stub_access, message_id: str) -> dict:
    _seed_contact(session_factory, str(CONTACT_ID))
    stub_access()
    usage = {
        "provider": "openai",
        "model": "gpt-test",
        "prompt_tokens": 33000,
        "completion_tokens": 200,
        "total_tokens": 33200,
        "cached_tokens": 31744,
    }
    stub_parser(parser_mod.ParsedOutput(_parser_output(), usage=usage))
    envelope = _envelope()
    envelope.message["message"]["messageId"] = message_id
    result = engine_mod.run_turn(envelope, session_factory=session_factory)
    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    understood = next(r for r in row.trace if r.get("stage") == "understood")
    return understood.get("facts") or {}


def test_understood_facts_carry_cached_tokens(session_factory, stub_parser, stub_access, monkeypatch):
    monkeypatch.delenv("LLM_CASSETTE_MODE", raising=False)
    facts = _understood_facts(session_factory, stub_parser, stub_access, "ZZT-llmcost-1")
    assert facts.get("cached_tokens") == 31744, facts
    assert facts.get("llm_cassette") == "off", facts


def test_understood_facts_name_the_cassette_mode(session_factory, stub_parser, stub_access, monkeypatch):
    monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
    facts = _understood_facts(session_factory, stub_parser, stub_access, "ZZT-llmcost-2")
    assert facts.get("llm_cassette") == "replay", facts
