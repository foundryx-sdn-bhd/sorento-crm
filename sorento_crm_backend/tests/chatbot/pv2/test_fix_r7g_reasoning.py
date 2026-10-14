"""PV2-FIX r7g/r7h: the parser call passes the configured reasoning_effort through (the value now lives on
the prompt label, see test_pv2_label_reasoning.py); v1's parser call and every other provider call are
unchanged. No model is called."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as v1_parser
from app.services.chatbot.pv2 import call as pv2_call
from app.services.chatbot.pv2 import index as pv2_index
from tests.chatbot.pv2.conftest import minimal_output
from tests.chatbot.pv2.test_core_call import TEMPLATE, _Stub


def _config(reasoning_effort="low", **extra) -> pv2_call.V2Config:
    body = pv2_index.index_of(TEMPLATE)
    return pv2_call.V2Config(
        system_prompt=TEMPLATE, prompt_version=7, version_id="vid",
        index_sha=hashlib.sha1(body.encode()).hexdigest(), keys=pv2_index.parse_index_keys(TEMPLATE),
        provider="openai", model="gpt-x", api_key="k", reasoning_effort=reasoning_effort, **extra)


def test_parse_v2_sends_the_configured_reasoning_effort(monkeypatch):
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    pv2_call.parse_v2(_config(), history=[], memory_block="<memory>\n</memory>", text="stock?")
    assert stub.calls[0].get("reasoning_effort") == "low"


def test_none_means_reasoning_effort_is_not_sent(monkeypatch):
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    pv2_call.parse_v2(_config(reasoning_effort=None), history=[], memory_block="<memory>\n</memory>", text="x")
    assert "reasoning_effort" not in stub.calls[0]


def _provider_request(monkeypatch, **chat_kwargs):
    from app.services import llm_provider

    sent = {}

    def fake_create(client, kwargs):
        sent.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}", tool_calls=None))], usage=None)

    monkeypatch.setattr(llm_provider, "_create_chat_completion", fake_create)
    provider = llm_provider.get_provider("openai", "k", "gpt-x")
    provider.chat([{"role": "user", "content": "hi"}], model="gpt-x", **chat_kwargs)
    return sent


def test_the_provider_puts_reasoning_effort_on_the_outbound_request(monkeypatch):
    assert _provider_request(monkeypatch, reasoning_effort="low").get("reasoning_effort") == "low"


def test_the_provider_sends_no_reasoning_effort_when_none_is_given(monkeypatch):
    assert "reasoning_effort" not in _provider_request(monkeypatch)


def test_the_v1_parser_call_sends_no_reasoning_effort(monkeypatch):
    stub = _Stub({})
    monkeypatch.setattr(llm_call, "chat", stub)
    config = v1_parser.ParserConfig(system_prompt="s", prompt_version=1, provider="openai", model="gpt-x", api_key="k")
    try:
        v1_parser.parse(config, "user block")
    except Exception:  # noqa: BLE001 - the stub's empty emission fails validation; only the call is under test
        pass
    assert stub.calls and "reasoning_effort" not in stub.calls[0]


def test_the_reasoning_tokens_are_read_from_the_provider_result(monkeypatch):
    class _Reasoning(_Stub):
        def __call__(self, provider, api_key, model, messages, **kwargs):
            result = super().__call__(provider, api_key, model, messages, **kwargs)
            result.reasoning_tokens = 7
            return result

    monkeypatch.setattr(llm_call, "chat", _Reasoning(minimal_output()))
    res = pv2_call.parse_v2(_config(), history=[], memory_block="<memory>\n</memory>", text="stock?")
    assert res.usage["reasoning_tokens"] == 7
