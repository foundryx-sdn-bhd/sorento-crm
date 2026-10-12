"""Provider structured-output forcing tests (M0 Semantic Parser).

The Semantic Parser calls ``provider.chat(..., json_schema=..., json_schema_name=...)``
and relies on a schema-VALID JSON object coming back as ``ChatResult.content``.
These tests stub the SDK clients (no network) and assert each provider forces
structured output the right way:

  * OpenAI  → ``response_format = {"type":"json_schema","json_schema":{name,schema,strict:True}}``.
  * Anthropic (no native json_schema) → a single forced tool via
    ``tool_choice = {"type":"tool","name":...}``; the model's ``tool_use.input``
    is surfaced as the JSON-dumped ``content`` so the caller parses it exactly
    like the OpenAI path.
"""
from __future__ import annotations

import json
import sys
import types
from typing import Any

from app.schemas.ai_semantic_parser import (
    PARSE_RESULT_JSON_SCHEMA,
    PARSE_RESULT_SCHEMA_NAME,
)
from app.services.llm_provider import AnthropicProvider, OpenAIProvider


# --------------------------------------------------------------------------- #
# OpenAI                                                                        #
# --------------------------------------------------------------------------- #
class _OAMsg:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = []


class _OAChoice:
    def __init__(self, msg: _OAMsg) -> None:
        self.message = msg


class _OAUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 4
        self.completion_tokens = 6
        self.total_tokens = 10


class _OACompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_OAChoice(_OAMsg(content))]
        self.usage = _OAUsage()


class _StubOpenAIClient:
    def __init__(self, content: str) -> None:
        self.last_kwargs: dict[str, Any] | None = None
        outer = self

        class _Completions:
            def create(inner, **kwargs):
                outer.last_kwargs = kwargs
                return _OACompletion(content)

        class _Chat:
            def __init__(inner) -> None:
                inner.completions = _Completions()

        self.chat = _Chat()


def test_openai_forces_json_schema_strict(monkeypatch):
    payload = json.dumps({"intent": "data_query"})
    stub = _StubOpenAIClient(payload)
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: stub)

    provider = OpenAIProvider("k", default_model="gpt-4o-mini")
    result = provider.chat(
        [{"role": "user", "content": "hi"}],
        json_schema=PARSE_RESULT_JSON_SCHEMA,
        json_schema_name=PARSE_RESULT_SCHEMA_NAME,
    )

    assert result.content == payload
    rf = stub.last_kwargs and stub.last_kwargs.get("response_format")
    assert rf is not None, "json_schema kwarg must set response_format"
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == PARSE_RESULT_SCHEMA_NAME
    assert rf["json_schema"]["schema"] is PARSE_RESULT_JSON_SCHEMA


def test_openai_json_schema_takes_precedence_over_response_format(monkeypatch):
    stub = _StubOpenAIClient("{}")
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: stub)
    provider = OpenAIProvider("k")
    provider.chat(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
        json_schema=PARSE_RESULT_JSON_SCHEMA,
        json_schema_name=PARSE_RESULT_SCHEMA_NAME,
    )
    rf = stub.last_kwargs["response_format"]
    assert rf["type"] == "json_schema"  # not the plain json_object


# --------------------------------------------------------------------------- #
# Anthropic                                                                     #
# --------------------------------------------------------------------------- #
class _AntToolUse:
    def __init__(self, input_: dict) -> None:
        self.type = "tool_use"
        self.id = "toolu_1"
        self.name = PARSE_RESULT_SCHEMA_NAME
        self.input = input_


class _AntUsage:
    def __init__(self) -> None:
        self.input_tokens = 5
        self.output_tokens = 9


class _AntResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content
        self.usage = _AntUsage()


class _StubAnthropicClient:
    def __init__(self, tool_input: dict) -> None:
        self.last_kwargs: dict[str, Any] | None = None
        outer = self

        class _Messages:
            def create(inner, **kwargs):
                outer.last_kwargs = kwargs
                return _AntResponse([_AntToolUse(tool_input)])

        self.messages = _Messages()


def test_anthropic_forces_tool_choice_and_returns_input_as_content(monkeypatch):
    tool_input = {"intent": "form_submit", "form_target": "complaint"}
    stub = _StubAnthropicClient(tool_input)
    fake_module = types.SimpleNamespace(Anthropic=lambda api_key=None: stub)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    provider = AnthropicProvider("k", default_model="claude-haiku-4-5")
    result = provider.chat(
        [
            {"role": "system", "content": "be precise"},
            {"role": "user", "content": "I want to file a complaint"},
        ],
        json_schema=PARSE_RESULT_JSON_SCHEMA,
        json_schema_name=PARSE_RESULT_SCHEMA_NAME,
    )

    # The tool_use.input is surfaced as the JSON-dumped content.
    assert json.loads(result.content) == tool_input

    kw = stub.last_kwargs
    assert kw is not None
    assert kw["tool_choice"] == {"type": "tool", "name": PARSE_RESULT_SCHEMA_NAME}
    assert len(kw["tools"]) == 1
    forced_tool = kw["tools"][0]
    assert forced_tool["name"] == PARSE_RESULT_SCHEMA_NAME
    assert forced_tool["input_schema"] is PARSE_RESULT_JSON_SCHEMA
    # System text is forwarded, stripped out of the messages list.
    assert kw["system"] == "be precise"
    assert all(m.get("role") != "system" for m in kw["messages"])
    # Token usage mapped from Anthropic's input/output tokens.
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 9
    assert result.total_tokens == 14
