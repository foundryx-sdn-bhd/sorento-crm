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


# --------------------------------------------------------------------------- #
# Crew ruling (b), 4 Oct: `prompt_cache_key` per prompt version. (Moving the date line
# into the user block was measured and NOT shipped: it changed parses, see the plan.)
# --------------------------------------------------------------------------- #


class TestParserSendsPromptCacheKey:
    def test_one_key_per_prompt_version(self, monkeypatch):
        seen: list[dict] = []

        def fake_chat(provider, key, model, messages, **kwargs):
            seen.append({"messages": messages, **kwargs})
            raise RuntimeError("stop here")

        monkeypatch.setattr(llm_call, "chat", fake_chat)
        for version in (56, None):
            config = parser_mod.ParserConfig(
                system_prompt="S", prompt_version=version, provider="openai", model="m", api_key="k"
            )
            with pytest.raises(parser_mod.ParserError):
                parser_mod.parse(config, "Current message: hi")
        assert seen[0]["prompt_cache_key"] == "chatbot_semantic_parser:v56"
        assert seen[1]["prompt_cache_key"] == "chatbot_semantic_parser:vfallback"
        # The prompt itself is untouched: system first, the user block as given.
        assert seen[0]["messages"] == [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "Current message: hi"},
        ]


class TestPromptCacheKey:
    def _client(self):
        import types

        from openai.types.chat import ChatCompletion

        calls: list[dict] = []
        completion = ChatCompletion.model_validate(
            {
                "id": "x", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
        create = lambda **kw: calls.append(kw) or completion  # noqa: E731
        return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))), calls

    def test_openai_sends_it_and_the_cassette_key_ignores_it(self, monkeypatch, cassette_dir):
        client, calls = self._client()
        provider = llm_provider.OpenAIProvider("sk-test-cachekey", default_model="m")
        monkeypatch.setattr(provider, "_client", lambda **_kw: client)
        monkeypatch.setenv("LLM_CASSETTE_MODE", "record")
        provider.chat(MESSAGES, prompt_cache_key="chatbot_semantic_parser:v56")
        assert calls[0]["prompt_cache_key"] == "chatbot_semantic_parser:v56"
        monkeypatch.setenv("LLM_CASSETTE_MODE", "replay")
        replayed = llm_provider.OpenAIProvider("sk-test-cachekey", default_model="m")
        monkeypatch.setattr(replayed, "_client", lambda **_kw: (_ for _ in ()).throw(AssertionError("live")))
        replayed.chat(MESSAGES, prompt_cache_key="chatbot_semantic_parser:v57")

    def test_unset_sends_nothing(self, monkeypatch):
        client, calls = self._client()
        provider = llm_provider.OpenAIProvider("sk-test-cachekey", default_model="m")
        monkeypatch.setattr(provider, "_client", lambda **_kw: client)
        provider.chat(MESSAGES)
        assert "prompt_cache_key" not in calls[0]


class TestPinnedDate:
    def test_pinned_only_in_a_cassette_mode(self, monkeypatch):
        from app.services.chatbot import engine as engine_mod

        monkeypatch.setenv("LLM_CASSETTE_DATE", "Friday, 02 October 2026")
        monkeypatch.delenv("LLM_CASSETTE_MODE", raising=False)
        assert engine_mod._current_date_directive() != "Friday, 02 October 2026"
        for mode in ("record", "replay"):
            monkeypatch.setenv("LLM_CASSETTE_MODE", mode)
            assert engine_mod._current_date_directive() == "Friday, 02 October 2026"
