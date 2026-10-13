"""PARSER-GEMINI-LITE: the parser can run on Gemini, takes a per-label reasoning effort,
and its `understood` record says which model answered, how fast and at what cost.

Nothing here calls a provider: `httpx.request` (Gemini) and `llm_call.chat` (the parser
seam) are stubbed. Lives under `tests/chatbot/` because only this tree may import the
chatbot package (`test_import_boundary.py`).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.models.chatbot_turn import ChatbotTurn
from app.services import llm_provider
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as parser_mod
from tests.chatbot.test_engine import (  # noqa: F401
    _envelope,
    _parser_output,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_memory_trace_and_usage import CONTACT_ID, _seed_contact


# --------------------------------------------------------------------------- #
# 1. The real parser schema, through the real Gemini request builder
# --------------------------------------------------------------------------- #


class _Response:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _gemini_request_body(monkeypatch) -> dict:
    seen: list[dict] = []

    def transport(method, url, **kwargs):
        seen.append(kwargs["json"])
        return _Response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            }
        )

    monkeypatch.setattr("httpx.request", transport)
    llm_provider.GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}],
        model="gemini-3.5-flash-lite",
        json_schema=parser_mod.PARSE_OUTPUT_JSON_SCHEMA,
        json_schema_name=parser_mod.PARSE_OUTPUT_SCHEMA_NAME,
    )
    return seen[-1]


def _assert_gemini_well_formed(node: Any, path: str = "$") -> None:
    """What Google's Schema proto accepts: one string `type` per node, `items` only on
    an array, `properties` only on an object, no null member in an enum, and an `anyOf`
    node carries no `type` of its own."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            _assert_gemini_well_formed(item, f"{path}[{i}]")
        return
    if not isinstance(node, dict):
        return
    kind = node.get("type")
    assert kind is None or isinstance(kind, str), f"{path}: list type {kind!r}"
    assert kind != "null", f"{path}: bare null type"
    if "items" in node:
        assert kind == "array", f"{path}: items on a {kind!r}"
    if "properties" in node:
        assert kind == "object", f"{path}: properties on a {kind!r}"
    if isinstance(node.get("enum"), list):
        assert None not in node["enum"], path
    if "anyOf" in node:
        assert kind is None, f"{path}: anyOf beside a type"
    for key, value in node.items():
        if key == "properties":
            for name, prop in value.items():
                _assert_gemini_well_formed(prop, f"{path}.{name}")
        elif key in ("items", "anyOf"):
            _assert_gemini_well_formed(value, f"{path}.{key}")


def test_the_parser_schema_translates_into_a_schema_google_accepts(monkeypatch):
    """Red-first for the bug that failed every Gemini parser call: `profile_statements[]
    .value` is `["string","array"]` with `items`, and became a STRING carrying `items`."""
    schema = _gemini_request_body(monkeypatch)["generationConfig"]["responseSchema"]
    _assert_gemini_well_formed(schema)
    value = schema["properties"]["profile_statements"]["items"]["properties"]["value"]
    assert value == {
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]
    }


def test_the_other_multi_type_parser_fields_keep_every_type(monkeypatch):
    """`demand_qty` (number or string) used to be narrowed to number on Gemini."""
    schema = _gemini_request_body(monkeypatch)["generationConfig"]["responseSchema"]
    demand = schema["properties"]["demand_qty"]
    assert [branch["type"] for branch in demand["anyOf"]] == ["number", "string"]
    assert all(branch.get("nullable") is True for branch in demand["anyOf"])


# --------------------------------------------------------------------------- #
# 2. Reasoning effort, from the label to the call
# --------------------------------------------------------------------------- #


def _config(**overrides: Any) -> parser_mod.ParserConfig:
    base = dict(
        system_prompt="s", prompt_version=1, provider="openai", model="gpt-5.4-mini", api_key="k"
    )
    return parser_mod.ParserConfig(**{**base, **overrides})


def _emission_result(**overrides: Any) -> llm_provider.ChatResult:
    base = dict(
        content=json.dumps({key: None for key in parser_mod.DECLARED_KEYS}),
        prompt_tokens=1000,
        completion_tokens=80,
        total_tokens=1080,
        cached_tokens=512,
    )
    return llm_provider.ChatResult(**{**base, **overrides})


def _capture_chat(monkeypatch, result: llm_provider.ChatResult) -> list[dict]:
    seen: list[dict] = []

    def fake_chat(provider, key, model, messages, **kwargs):
        seen.append(kwargs)
        return result

    monkeypatch.setattr(llm_call, "chat", fake_chat)
    return seen


def test_the_config_defaults_to_no_reasoning_effort():
    assert _config().reasoning_effort is None


def test_parse_sends_no_reasoning_effort_when_the_label_sets_none(monkeypatch):
    seen = _capture_chat(monkeypatch, _emission_result())
    parser_mod.parse(_config(), "block")
    assert "reasoning_effort" not in seen[0]


def test_parse_sends_the_labels_reasoning_effort(monkeypatch):
    seen = _capture_chat(monkeypatch, _emission_result())
    parser_mod.parse(_config(reasoning_effort="low"), "block")
    assert seen[0]["reasoning_effort"] == "low"


def test_resolve_config_reads_the_labels_reasoning_effort(monkeypatch):
    class _Cfg:
        provider = "openai"
        model = "gpt-5.4-mini"
        api_key_ciphertext = "k"

    class _Service:
        def __init__(self, _db) -> None:
            pass

        def get(self):
            return _Cfg()

    import app.services.ai_assistant_service as assistant_service

    monkeypatch.setattr(parser_mod, "render", lambda *_a, **_k: ("prompt", 7))
    monkeypatch.setattr(parser_mod, "agent_model", lambda *_a, **_k: ("gemini", "gemini-3.5-flash-lite"))
    monkeypatch.setattr(parser_mod, "agent_reasoning_effort", lambda *_a, **_k: "minimal")
    monkeypatch.setattr(assistant_service, "AIAssistantConfigService", _Service)
    monkeypatch.setattr(llm_provider, "resolve_api_key", lambda *_a, **_k: "gk")

    config = parser_mod.resolve_config(object(), current_date="today")
    assert (config.provider, config.model, config.reasoning_effort) == (
        "gemini",
        "gemini-3.5-flash-lite",
        "minimal",
    )


# --------------------------------------------------------------------------- #
# 3. What the call cost, on `usage`
# --------------------------------------------------------------------------- #


def test_parse_usage_names_the_answering_model_latency_and_reasoning(monkeypatch):
    _capture_chat(
        monkeypatch,
        _emission_result(model="gpt-5.4-mini-2026-03-17", reasoning_tokens=48),
    )
    out = parser_mod.parse(_config(reasoning_effort="low"), "block")
    usage = out.usage
    assert usage["provider"] == "openai"
    assert usage["model"] == "gpt-5.4-mini"
    assert usage["model_answered"] == "gpt-5.4-mini-2026-03-17"
    assert usage["reasoning_tokens"] == 48
    assert usage["reasoning_effort"] == "low"
    assert isinstance(usage["latency_ms"], int) and usage["latency_ms"] >= 0


def test_a_failed_parse_still_carries_latency(monkeypatch):
    _capture_chat(monkeypatch, _emission_result(content="not json"))
    with pytest.raises(parser_mod.ParserError) as caught:
        parser_mod.parse(_config(), "block")
    assert isinstance(caught.value.usage["latency_ms"], int)


# --------------------------------------------------------------------------- #
# 4. The `understood` trace facts
# --------------------------------------------------------------------------- #


def test_understood_facts_carry_model_provider_latency_and_token_split(
    session_factory, stub_parser, stub_access, monkeypatch
):
    monkeypatch.delenv("LLM_CASSETTE_MODE", raising=False)
    _seed_contact(session_factory, str(CONTACT_ID))
    stub_access()
    usage = {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "model_answered": "gemini-3.5-flash-lite-001",
        "reasoning_effort": "minimal",
        "latency_ms": 3150,
        "prompt_tokens": 9000,
        "completion_tokens": 120,
        "total_tokens": 9120,
        "cached_tokens": 4096,
        "reasoning_tokens": 7,
    }
    stub_parser(parser_mod.ParsedOutput(_parser_output(), usage=usage))
    envelope = _envelope()
    envelope.message["message"]["messageId"] = "ZZT-gemini-lite-1"
    result = engine_mod.run_turn(envelope, session_factory=session_factory)
    row = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    facts = next(r for r in row.trace if r.get("stage") == "understood").get("facts") or {}

    assert facts.get("provider") == "gemini", facts
    assert facts.get("model") == "gemini-3.5-flash-lite", facts
    assert facts.get("model_answered") == "gemini-3.5-flash-lite-001", facts
    assert facts.get("reasoning_effort") == "minimal", facts
    assert facts.get("latency_ms") == 3150, facts
    assert facts.get("prompt_tokens") == 9000, facts
    assert facts.get("cached_tokens") == 4096, facts
    assert facts.get("completion_tokens") == 120, facts
    assert facts.get("reasoning_tokens") == 7, facts
