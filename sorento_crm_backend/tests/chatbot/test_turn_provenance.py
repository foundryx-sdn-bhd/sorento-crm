"""EVAL-BENCH D1: every `understood` trace record carries the provenance of the parse.

AC1.1 success record, AC1.2 parser-failure record, AC1.3 `build_digest` keeps the keys.
Synthetic data only; the parser is stubbed at the same seam as tests/chatbot/test_engine.py.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from app.main import GIT_SHA
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from scripts.chatbot_eval.trace import build_digest
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures reused by name
    _envelope,
    _parser_output,
    _turn_row,
    seeded,
    stub_access,
)

PROMPT = "synthetic system prompt for provenance tests"
KEYS = ("prompt_sha256", "prompt_version", "model", "provider", "reasoning_effort", "code_sha")


@pytest.fixture()
def parser_stub(monkeypatch):
    def _install(*, error: Exception | None = None):
        from app.services.ai_assistant_service import MCPRuntimeClient

        monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: json.dumps({}))

        def fake_resolve_config(db, *, current_date, override_version_id=None):
            return parser_mod.ParserConfig(
                system_prompt=PROMPT, prompt_version=7, provider="openai", model="gpt-test", api_key="sk-test"
            )

        def fake_parse(config, user_block):
            if error is not None:
                raise error
            return _parser_output()

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(parser_mod, "parse", fake_parse)

    return _install


def _understood(session_factory, turn_id: str) -> dict[str, Any]:
    trace = _turn_row(session_factory, turn_id).trace
    return next(r for r in trace if r.get("stage") == "understood")


def test_ac1_1_success_record_carries_provenance(session_factory, seeded, parser_stub, stub_access):
    parser_stub()
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    facts = _understood(session_factory, result.turn_id)["facts"]
    assert facts.get("prompt_sha256") == hashlib.sha256(PROMPT.encode()).hexdigest()
    assert facts.get("prompt_version") == 7
    assert facts.get("model") == "gpt-test"
    assert facts.get("provider") == "openai"
    assert "reasoning_effort" in facts and facts["reasoning_effort"] is None
    assert facts.get("code_sha") == GIT_SHA


def test_ac1_2_parser_failure_record_carries_provenance(session_factory, seeded, parser_stub, stub_access):
    parser_stub(error=parser_mod.ParserError("boom"))
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    rec = _understood(session_factory, result.turn_id)
    assert rec["status"] == "failed"
    facts = rec["facts"]
    missing = [k for k in KEYS if k not in facts]
    assert not missing, f"failed-parse understood record lacks {missing}"
    assert facts["prompt_sha256"] == hashlib.sha256(PROMPT.encode()).hexdigest()
    assert facts["reasoning_effort"] is None
    assert facts["code_sha"] == GIT_SHA


def test_ac1_3_build_digest_keeps_provenance_keys():
    facts = {
        "domain": "master_products", "intent": "check_product", "entities": 1,
        "prompt_sha256": "a" * 64, "prompt_version": 7, "model": "gpt-test",
        "provider": "openai", "reasoning_effort": None, "code_sha": "abc1234",
    }
    parser = build_digest([{"stage": "understood", "facts": facts}], "reply", None)["parser"]
    assert parser["prompt_sha256"] == "a" * 64
    assert parser["model"] == "gpt-test"
    assert parser["provider"] == "openai"
    assert parser["code_sha"] == "abc1234"
    assert "reasoning_effort" in parser and parser["reasoning_effort"] is None
    assert parser["prompt_version"] == 7
