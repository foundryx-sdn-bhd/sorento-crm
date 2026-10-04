"""LLM-TEST-COST, the chatbot half: the parser's `cached_tokens`, the cache-friendly
message order, and a cassette miss through `llm_call`. Lives under `tests/chatbot/`
because only this tree may import the chatbot package (`test_import_boundary.py`); the
provider-level cassette tests are `tests/test_llm_cassette.py`.
"""
from __future__ import annotations

import json
import os

import pytest

from app.services import llm_cassette, llm_provider
from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as parser_mod

MESSAGES = [
    {"role": "system", "content": "static system prompt"},
    {"role": "user", "content": "a fake customer message"},
]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


@pytest.fixture
def cassette_dir(monkeypatch, tmp_path):
    path = tmp_path / "cassettes"
    monkeypatch.setenv("LLM_CASSETTE_DIR", str(path))
    return path


class TestChatbotSeam:
    def test_a_miss_through_llm_call_is_raised_at_once_never_retried(self, monkeypatch, cassette_dir):
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        slept: list[float] = []
        monkeypatch.setattr(llm_call, "_sleep", slept.append)
        with pytest.raises(llm_cassette.CassetteMiss):
            llm_call.chat("openai", "", "gpt-fake", MESSAGES, json_schema=SCHEMA)
        assert slept == []


class TestParserUsage:
    def test_parser_usage_carries_cached_tokens(self, monkeypatch):
        emission = {key: None for key in parser_mod.DECLARED_KEYS}
        result = llm_provider.ChatResult(
            content=json.dumps(emission),
            prompt_tokens=33000,
            completion_tokens=200,
            total_tokens=33200,
            cached_tokens=32000,
        )
        monkeypatch.setattr(llm_call, "chat", lambda *a, **k: result)
        config = parser_mod.ParserConfig(
            system_prompt="s", prompt_version=1, provider="openai", model="m", api_key="k"
        )
        out = parser_mod.parse(config, "user block")
        assert out.usage["cached_tokens"] == 32000


# --------------------------------------------------------------------------- #
# Cache friendliness: the static part is a stable prefix
# --------------------------------------------------------------------------- #


class TestStablePrefix:
    def test_parser_messages_put_the_variable_block_last(self, monkeypatch):
        seen: list[dict] = []

        def fake_chat(provider, key, model, messages, **kwargs):
            seen.append({"messages": messages, **kwargs})
            raise RuntimeError("stop here")

        monkeypatch.setattr(llm_call, "chat", fake_chat)
        config = parser_mod.ParserConfig(
            system_prompt="S", prompt_version=1, provider="openai", model="m", api_key="k"
        )
        for block in ("first user block", "second user block"):
            with pytest.raises(parser_mod.ParserError):
                parser_mod.parse(config, block)
        first, second = seen
        assert first["messages"][:-1] == second["messages"][:-1]
        assert first["messages"][-1]["role"] == "user"
        assert first["messages"][-1] != second["messages"][-1]
        assert json.dumps(first["json_schema"], sort_keys=False) == json.dumps(
            second["json_schema"], sort_keys=False
        )

    def test_the_date_sits_at_the_tail_of_the_system_prompt(self):
        """Two days' prompts share at least 95% of their text as a common prefix, so a
        date change re-bills only the tail, not the whole 32k-token system prompt."""
        from app.services.ai_prompt_registry import _substitute
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        monday = _substitute(SEMANTIC_PARSER_PROMPT, {"current_date": "Monday, 05 October 2026"})
        tuesday = _substitute(SEMANTIC_PARSER_PROMPT, {"current_date": "Tuesday, 06 October 2026"})
        common = len(os.path.commonprefix([monday, tuesday]))
        assert common / len(monday) >= 0.95, common / len(monday)
