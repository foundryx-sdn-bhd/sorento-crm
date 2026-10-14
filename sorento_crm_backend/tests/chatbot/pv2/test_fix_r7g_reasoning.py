"""PV2-FIX r7g (owner approved 14 Oct): the PV2 parser call sends reasoning_effort "low"; v1's parser call
and every other provider call are unchanged. No model is called."""
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


def _config(**extra) -> pv2_call.V2Config:
    body = pv2_index.index_of(TEMPLATE)
    return pv2_call.V2Config(
        system_prompt=TEMPLATE, prompt_version=7, version_id="vid",
        index_sha=hashlib.sha1(body.encode()).hexdigest(), keys=pv2_index.parse_index_keys(TEMPLATE),
        provider="openai", model="gpt-x", api_key="k", **extra)


def test_the_v2_config_defaults_to_low_reasoning_effort():
    assert _config().reasoning_effort == "low"


def test_parse_v2_sends_the_configured_reasoning_effort(monkeypatch):
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    pv2_call.parse_v2(_config(), history=[], memory_block="<memory>\n</memory>", text="stock?")
    assert stub.calls[0].get("reasoning_effort") == "low"


def test_the_module_default_is_low():
    assert pv2_call.PV2_REASONING_EFFORT == "low"


def _resolve(monkeypatch, env):
    """`resolve_v2_config` with the DB reads stubbed; only the reasoning setting is under test."""
    import app.services.ai_assistant_service as aas
    import app.services.ai_prompt_registry as apr
    import app.services.llm_provider as lp

    monkeypatch.setattr(pv2_call, "_pinned_text", lambda db, override, label: (TEMPLATE, 7, "vid"))
    monkeypatch.setattr(apr, "agent_model", lambda db, key, label: (None, None))
    monkeypatch.setattr(lp, "resolve_api_key", lambda config, provider: "k")
    monkeypatch.setattr(aas, "AIAssistantConfigService",
                        lambda db: SimpleNamespace(get=lambda: SimpleNamespace(provider="openai", model="gpt-x")))
    if env is None:
        monkeypatch.delenv("PV2_REASONING_EFFORT", raising=False)
    else:
        monkeypatch.setenv("PV2_REASONING_EFFORT", env)
    return pv2_call.resolve_v2_config(object())


def test_resolve_v2_config_defaults_to_low(monkeypatch):
    assert _resolve(monkeypatch, None).reasoning_effort == "low"


def test_the_env_var_overrides_the_default(monkeypatch):
    assert _resolve(monkeypatch, "medium").reasoning_effort == "medium"


@pytest.mark.parametrize("value", ["none", ""])
def test_none_or_empty_env_means_no_reasoning_effort(monkeypatch, value):
    assert _resolve(monkeypatch, value).reasoning_effort is None


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


@pytest.mark.parametrize("value", ["none", ""])
def test_env_none_means_no_reasoning_effort_in_the_outbound_request(monkeypatch, value):
    config = _resolve(monkeypatch, value)
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    pv2_call.parse_v2(config, history=[], memory_block="<memory>\n</memory>", text="x")
    assert "reasoning_effort" not in stub.calls[0]


def test_env_medium_reaches_the_outbound_request(monkeypatch):
    config = _resolve(monkeypatch, "medium")
    stub = _Stub(minimal_output())
    monkeypatch.setattr(llm_call, "chat", stub)
    pv2_call.parse_v2(config, history=[], memory_block="<memory>\n</memory>", text="x")
    assert stub.calls[0]["reasoning_effort"] == "medium"
