"""Unit tests for the LLM provider abstraction.

We monkey-patch the SDK client classes so no network calls happen.
"""
from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from app.services.llm_provider import (
    DEFAULT_MODELS,
    AnthropicProvider,
    ChatResult,
    GeminiProvider,
    ImagePart,
    OpenAIProvider,
    _convert_messages_to_anthropic,
    _convert_messages_to_gemini,
    _convert_tools_to_anthropic,
    _convert_tools_to_gemini,
    _gemini_schema,
    _gemini_thinking_budget,
    _split_system_messages,
    default_model_for,
    get_provider,
    resolve_api_key,
    resolve_model,
)


# ---- Stubs --------------------------------------------------------------


class _StubFn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _StubToolCall:
    def __init__(self, id_: str, name: str, arguments: str) -> None:
        self.id = id_
        self.function = _StubFn(name, arguments)


class _StubMsg:
    def __init__(self, content: str | None = "", tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _StubChoice:
    def __init__(self, message: _StubMsg) -> None:
        self.message = message


class _StubUsage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _StubCompletion:
    def __init__(self, message: _StubMsg, usage: _StubUsage) -> None:
        self.choices = [_StubChoice(message)]
        self.usage = usage


class _StubOpenAIClient:
    """Minimal OpenAI client stub: client.chat.completions.create / client.embeddings.create."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.last_kwargs: dict[str, Any] | None = None

        class _Embeddings:
            def __init__(inner) -> None:
                inner.calls: list[Any] = []

            def create(inner, **kwargs):
                inner.calls.append(kwargs)
                obj = types.SimpleNamespace(
                    data=[types.SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
                )
                return obj

        class _Completions:
            def __init__(inner) -> None:
                inner.calls: list[Any] = []
                inner._next_message = _StubMsg(content="hello", tool_calls=[])
                inner._next_usage = _StubUsage(5, 7)

            def create(inner, **kwargs):
                inner.calls.append(kwargs)
                self.last_kwargs = kwargs
                return _StubCompletion(inner._next_message, inner._next_usage)

        class _Chat:
            def __init__(inner) -> None:
                inner.completions = _Completions()

        class _Models:
            def retrieve(inner, model, **_kwargs):
                return types.SimpleNamespace(id=model, object="model")

        self.chat = _Chat()
        self.embeddings = _Embeddings()
        self.models = _Models()


# Anthropic stubs ---------------------------------------------------------


class _AntTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _AntToolUseBlock:
    def __init__(self, id_: str, name: str, input_: dict) -> None:
        self.type = "tool_use"
        self.id = id_
        self.name = name
        self.input = input_


class _AntUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _AntResponse:
    def __init__(self, content: list[Any], usage: _AntUsage) -> None:
        self.content = content
        self.usage = usage


class _StubAnthropicClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.last_kwargs: dict[str, Any] | None = None

        class _Messages:
            def __init__(inner) -> None:
                inner._next = _AntResponse(
                    content=[_AntTextBlock("ok-from-anthropic")],
                    usage=_AntUsage(3, 4),
                )

            def create(inner, **kwargs):
                self.last_kwargs = kwargs
                return inner._next

        self.messages = _Messages()


# ---- OpenAIProvider tests ------------------------------------------------


def test_openai_provider_chat_returns_normalized_result(monkeypatch):
    stub = _StubOpenAIClient()
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: stub)
    provider = OpenAIProvider("k", default_model="gpt-4o-mini")
    result = provider.chat([{"role": "user", "content": "hi"}], temperature=0.0)
    assert isinstance(result, ChatResult)
    assert result.content == "hello"
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 7
    assert result.total_tokens == 12
    assert result.tool_calls == []
    assert stub.last_kwargs and stub.last_kwargs["model"] == "gpt-4o-mini"


def test_openai_provider_chat_normalizes_tool_calls(monkeypatch):
    stub = _StubOpenAIClient()
    stub.chat.completions._next_message = _StubMsg(
        content=None,
        tool_calls=[_StubToolCall("call_1", "do_thing", json.dumps({"x": 1}))],
    )
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: stub)
    provider = OpenAIProvider("k")
    result = provider.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "do_thing", "parameters": {}}}],
    )
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["id"] == "call_1"
    assert call["name"] == "do_thing"
    assert call["arguments"] == {"x": 1}
    assert stub.last_kwargs and stub.last_kwargs.get("tools")


def test_openai_provider_embed_returns_vector(monkeypatch):
    stub = _StubOpenAIClient()
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: stub)
    provider = OpenAIProvider("k")
    vec = provider.embed("hello")
    assert vec == [0.1, 0.2, 0.3]


def test_openai_test_connection_ok(monkeypatch):
    stub = _StubOpenAIClient()
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: stub)
    provider = OpenAIProvider("k")
    ok, message, latency = provider.test_connection()
    assert ok is True
    assert message == "OK"
    assert isinstance(latency, int) and latency >= 0


def test_openai_test_connection_failure(monkeypatch):
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    raise RuntimeError("nope")

        class models:
            @staticmethod
            def retrieve(*_args, **_kwargs):
                raise RuntimeError("nope")

        embeddings = None

    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: _Boom())
    provider = OpenAIProvider("k")
    ok, message, latency = provider.test_connection()
    assert ok is False
    assert "nope" in message
    assert isinstance(latency, int)


# ---- Test connection probe (#1315) ----------------------------------------
#
# A reasoning model spends its first output tokens thinking, so a one-token
# generate is answered with a 400 and a working key reads as broken. These
# stubs behave like the real providers: a generate with a tiny output ceiling
# fails the way OpenAI failed on gpt-5.4-mini, and the key/model errors come
# back in the provider's own words.

_OPENAI_400_OUTPUT_LIMIT = (
    "Error code: 400 - {'error': {'message': 'Could not finish the message because "
    "max_tokens or model output limit was reached. Please try again with higher "
    "max_tokens.', 'type': 'invalid_request_error', 'param': None, 'code': None}}"
)
_OPENAI_401 = (
    "Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-wrong. "
    "You can find your API key at https://platform.openai.com/account/api-keys.', "
    "'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}"
)
_OPENAI_404 = (
    "Error code: 404 - {'error': {'message': 'The model `gpt-5.4-mini` does not exist "
    "or you do not have access to it.', 'type': 'invalid_request_error', "
    "'param': None, 'code': 'model_not_found'}}"
)
_ANTHROPIC_401 = (
    "Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', "
    "'message': 'invalid x-api-key'}}"
)
_ANTHROPIC_404 = (
    "Error code: 404 - {'type': 'error', 'error': {'type': 'not_found_error', "
    "'message': 'model: claude-opus-9'}}"
)


class _RecordingOpenAI:
    """OpenAI stub that records every call and fails like the real API does."""

    def __init__(self, *, key_error: str | None = None, model_error: str | None = None):
        self.calls: list[tuple[str, Any]] = []
        outer = self

        class _Completions:
            def create(inner, **kwargs):
                outer.calls.append(("chat", kwargs))
                if key_error:
                    raise RuntimeError(key_error)
                if model_error:
                    raise RuntimeError(model_error)
                ceiling = kwargs.get("max_tokens", kwargs.get("max_completion_tokens"))
                if ceiling is not None and ceiling < 16:
                    raise RuntimeError(_OPENAI_400_OUTPUT_LIMIT)
                return _StubCompletion(_StubMsg(content="OK", tool_calls=[]), _StubUsage(5, 7))

        class _Models:
            def retrieve(inner, model, **kwargs):
                outer.calls.append(("models.retrieve", model))
                if key_error:
                    raise RuntimeError(key_error)
                if model_error:
                    raise RuntimeError(model_error)
                return types.SimpleNamespace(id=model, object="model")

            def list(inner, **kwargs):
                outer.calls.append(("models.list", kwargs))
                if key_error:
                    raise RuntimeError(key_error)
                return types.SimpleNamespace(data=[])

        self.chat = types.SimpleNamespace(completions=_Completions())
        self.models = _Models()


class _RecordingAnthropic:
    """Anthropic stub that records every call and fails like the real API does."""

    def __init__(self, *, key_error: str | None = None, model_error: str | None = None):
        self.calls: list[tuple[str, Any]] = []
        outer = self

        class _Messages:
            def create(inner, **kwargs):
                outer.calls.append(("messages", kwargs))
                if key_error:
                    raise RuntimeError(key_error)
                if model_error:
                    raise RuntimeError(model_error)
                if kwargs.get("max_tokens", 1024) < 16:
                    raise RuntimeError("max_tokens too small for a thinking model")
                return _AntResponse(content=[_AntTextBlock("OK")], usage=_AntUsage(3, 4))

        class _Models:
            def retrieve(inner, model_id, **kwargs):
                outer.calls.append(("models.retrieve", model_id))
                if key_error:
                    raise RuntimeError(key_error)
                if model_error:
                    raise RuntimeError(model_error)
                return types.SimpleNamespace(id=model_id, type="model")

            def list(inner, **kwargs):
                outer.calls.append(("models.list", kwargs))
                if key_error:
                    raise RuntimeError(key_error)
                return types.SimpleNamespace(data=[])

        self.messages = _Messages()
        self.models = _Models()


def _install_openai(monkeypatch, stub: _RecordingOpenAI) -> _RecordingOpenAI:
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None, **_kw: stub)
    return stub


def _install_anthropic(monkeypatch, stub: _RecordingAnthropic) -> _RecordingAnthropic:
    fake_module = types.SimpleNamespace(Anthropic=lambda api_key=None, **_kw: stub)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return stub


def _probe(provider) -> tuple[bool, str, int]:
    """Run the probe and pin the (ok, message, latency_ms) shape the endpoint reads."""
    result = provider.test_connection()
    assert isinstance(result, tuple) and len(result) == 3
    ok, message, latency = result
    assert isinstance(ok, bool)
    assert isinstance(message, str)
    assert isinstance(latency, int) and latency >= 0
    return result


def _no_one_token_generate(calls: list[tuple[str, Any]]) -> None:
    for kind, payload in calls:
        if kind in ("chat", "messages"):
            assert payload.get("max_tokens") != 1, payload
            assert payload.get("max_completion_tokens") != 1, payload


def test_openai_test_connection_valid_key_on_a_reasoning_model_reports_ok(monkeypatch):
    stub = _install_openai(monkeypatch, _RecordingOpenAI())
    provider = get_provider("openai", "sk-valid", "gpt-5.4-mini")
    ok, message, _ = _probe(provider)
    assert (ok, message) == (True, "OK")
    _no_one_token_generate(stub.calls)


def test_openai_test_connection_probes_the_selected_model(monkeypatch):
    stub = _install_openai(monkeypatch, _RecordingOpenAI())
    _probe(get_provider("openai", "sk-valid", "gpt-5.4-mini"))
    assert ("models.retrieve", "gpt-5.4-mini") in stub.calls
    assert not [c for c in stub.calls if c[0] == "chat"], "the probe must not generate"


def test_openai_test_connection_bad_key_reports_the_provider_401_text(monkeypatch):
    stub = _install_openai(monkeypatch, _RecordingOpenAI(key_error=_OPENAI_401))
    ok, message, _ = _probe(get_provider("openai", "sk-wrong", "gpt-5.4-mini"))
    assert ok is False
    assert message == _OPENAI_401
    _no_one_token_generate(stub.calls)


def test_openai_test_connection_unseen_model_reports_the_provider_text(monkeypatch):
    stub = _install_openai(monkeypatch, _RecordingOpenAI(model_error=_OPENAI_404))
    ok, message, _ = _probe(get_provider("openai", "sk-valid", "gpt-5.4-mini"))
    assert ok is False
    assert message == _OPENAI_404
    _no_one_token_generate(stub.calls)


def test_anthropic_test_connection_valid_key_reports_ok(monkeypatch):
    stub = _install_anthropic(monkeypatch, _RecordingAnthropic())
    ok, message, _ = _probe(get_provider("anthropic", "sk-ant-valid", "claude-sonnet-4-5"))
    assert (ok, message) == (True, "OK")
    _no_one_token_generate(stub.calls)


def test_anthropic_test_connection_probes_the_selected_model(monkeypatch):
    stub = _install_anthropic(monkeypatch, _RecordingAnthropic())
    _probe(get_provider("anthropic", "sk-ant-valid", "claude-sonnet-4-5"))
    assert ("models.retrieve", "claude-sonnet-4-5") in stub.calls
    assert not [c for c in stub.calls if c[0] == "messages"], "the probe must not generate"


def test_anthropic_test_connection_bad_key_reports_the_provider_401_text(monkeypatch):
    stub = _install_anthropic(monkeypatch, _RecordingAnthropic(key_error=_ANTHROPIC_401))
    ok, message, _ = _probe(get_provider("anthropic", "sk-ant-wrong", "claude-sonnet-4-5"))
    assert ok is False
    assert message == _ANTHROPIC_401
    _no_one_token_generate(stub.calls)


def test_anthropic_test_connection_unseen_model_reports_the_provider_text(monkeypatch):
    stub = _install_anthropic(monkeypatch, _RecordingAnthropic(model_error=_ANTHROPIC_404))
    ok, message, _ = _probe(get_provider("anthropic", "sk-ant-valid", "claude-opus-9"))
    assert ok is False
    assert message == _ANTHROPIC_404
    _no_one_token_generate(stub.calls)


# ---- AnthropicProvider tests --------------------------------------------


def test_anthropic_provider_chat_strips_system_and_returns_text(monkeypatch):
    stub = _StubAnthropicClient()
    fake_module = types.SimpleNamespace(Anthropic=lambda api_key=None: stub)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    provider = AnthropicProvider("k", default_model="claude-haiku-4-5")
    result = provider.chat(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
        temperature=0.0,
    )
    assert result.content == "ok-from-anthropic"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4
    assert result.total_tokens == 7
    assert stub.last_kwargs and stub.last_kwargs["system"] == "be terse"
    # system message should have been stripped from messages list
    assert all(m.get("role") != "system" for m in stub.last_kwargs["messages"])


def test_anthropic_chat_normalizes_tool_use_blocks(monkeypatch):
    stub = _StubAnthropicClient()
    stub.messages._next = _AntResponse(  # type: ignore[attr-defined]
        content=[
            _AntTextBlock("partial"),
            _AntToolUseBlock("toolu_1", "do_thing", {"x": 1}),
        ],
        usage=_AntUsage(2, 3),
    )
    fake_module = types.SimpleNamespace(Anthropic=lambda api_key=None: stub)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    provider = AnthropicProvider("k")
    result = provider.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "do_thing", "parameters": {}}}],
    )
    assert result.content == "partial"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["id"] == "toolu_1"
    assert call["name"] == "do_thing"
    assert call["arguments"] == {"x": 1}


def test_anthropic_embed_falls_back_to_openai(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "openai_api_key", "fake-key", raising=False)
    stub = _StubOpenAIClient()
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None: stub)
    provider = AnthropicProvider("k")
    vec = provider.embed("hi")
    assert vec == [0.1, 0.2, 0.3]


def test_anthropic_embed_raises_without_openai_key(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "openai_api_key", None, raising=False)
    provider = AnthropicProvider("k")
    with pytest.raises(RuntimeError):
        provider.embed("hi")


# ---- conversion helpers + factory ---------------------------------------


def test_convert_tools_to_anthropic_shape():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "do_thing",
                "description": "does",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    out = _convert_tools_to_anthropic(tools)
    assert out == [
        {
            "name": "do_thing",
            "description": "does",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_split_system_messages_concatenates():
    sys_text, rest = _split_system_messages(
        [
            {"role": "system", "content": "a"},
            {"role": "system", "content": "b"},
            {"role": "user", "content": "u"},
        ]
    )
    assert sys_text == "a\n\nb"
    assert rest == [{"role": "user", "content": "u"}]


def test_convert_messages_handles_tool_role():
    out = _convert_messages_to_anthropic(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tu_1",
                        "type": "function",
                        "function": {"name": "do_thing", "arguments": json.dumps({"x": 1})},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tu_1", "content": "{\"ok\": true}"},
        ]
    )
    assert out[0]["role"] == "assistant"
    assert any(b["type"] == "tool_use" for b in out[0]["content"])
    assert out[1]["role"] == "user"
    assert out[1]["content"][0]["type"] == "tool_result"
    assert out[1]["content"][0]["tool_use_id"] == "tu_1"


def test_get_provider_factory():
    p1 = get_provider("openai", "k")
    assert isinstance(p1, OpenAIProvider)
    p2 = get_provider("anthropic", "k")
    assert isinstance(p2, AnthropicProvider)
    p3 = get_provider("gemini", "k")
    assert isinstance(p3, GeminiProvider)
    assert p3.default_model == "gemini-2.5-flash"
    assert get_provider("gemini", "k", "gemini-2.5-pro").default_model == "gemini-2.5-pro"
    with pytest.raises(ValueError):
        get_provider("nope", "k")


def test_default_model_for_covers_every_registered_provider():
    """One table, so registering a provider cannot leave a caller handing it
    another vendor's model id."""
    assert set(DEFAULT_MODELS) == {"openai", "anthropic", "gemini"}
    assert default_model_for("openai") == "gpt-4o"
    assert default_model_for("anthropic") == "claude-sonnet-4-6"
    assert default_model_for("gemini") == "gemini-2.5-flash"
    # Case and padding come off a settings row, so they are normalized.
    assert default_model_for(" Gemini ") == "gemini-2.5-flash"


def test_get_provider_accepts_every_default_model():
    for name, expected in DEFAULT_MODELS.items():
        model = default_model_for(name)
        assert model == expected
        provider = get_provider(name, "k", model=model)
        assert provider.name == name
        assert provider.default_model == model


# ---- GeminiProvider ------------------------------------------------------
#
# Gemini goes over the REST API with `httpx`, not an SDK, so the seam stubbed
# here is `httpx.request` - the exact call `GeminiProvider._request` makes.


class _StubHTTPResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)

    def json(self):
        return self._payload


class _GeminiTransport:
    """Records every request and replays a canned response."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> _StubHTTPResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return _StubHTTPResponse(self.payload, self.status_code)

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]

    @property
    def body(self) -> dict[str, Any]:
        return self.calls[-1]["json"]


def _gemini_text_response(text: str = "ok-from-gemini") -> dict:
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": 11,
            "candidatesTokenCount": 5,
            "totalTokenCount": 16,
        },
    }


def _install(monkeypatch, transport: _GeminiTransport) -> _GeminiTransport:
    monkeypatch.setattr("httpx.request", transport)
    return transport


def test_gemini_chat_hoists_system_into_system_instruction(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    provider = GeminiProvider("k", default_model="gemini-2.5-flash")

    result = provider.chat(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
        temperature=0.0,
        max_tokens=64,
    )

    assert result.content == "ok-from-gemini"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 5
    assert result.total_tokens == 16
    assert result.tool_calls == []
    assert result.raw is t.payload

    assert t.last["method"] == "POST"
    assert t.last["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert t.last["headers"]["x-goog-api-key"] == "k"
    assert t.body["systemInstruction"] == {"parts": [{"text": "be terse"}]}
    assert t.body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert t.body["generationConfig"]["temperature"] == 0.0
    assert t.body["generationConfig"]["maxOutputTokens"] == 64


def test_gemini_chat_uses_the_per_call_model_over_the_default(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k", default_model="gemini-2.5-flash").chat(
        [{"role": "user", "content": "hi"}], model="gemini-2.5-pro"
    )
    assert t.last["url"].endswith("/models/gemini-2.5-pro:generateContent")


def test_gemini_flash_thinks_zero_and_keeps_the_callers_answer_budget(monkeypatch):
    """The media image lane's 2048 must all be answer tokens.

    Thinking is on by default on 2.5 and is spent out of `maxOutputTokens`, so
    an unset budget can burn the whole 2048 and return a candidate with no
    parts (`finishReason=MAX_TOKENS`).
    """
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k", default_model="gemini-2.5-flash").chat(
        [{"role": "user", "content": "read it"}], max_tokens=2048
    )
    generation = t.body["generationConfig"]
    assert generation["thinkingConfig"] == {"thinkingBudget": 0}
    assert generation["maxOutputTokens"] == 2048


def test_gemini_flash_lite_also_thinks_zero(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}],
        model="gemini-2.5-flash-lite",
        max_tokens=512,
    )
    assert t.body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert t.body["generationConfig"]["maxOutputTokens"] == 512


def test_gemini_pro_gets_a_small_budget_added_on_top_of_the_answer_budget(monkeypatch):
    """2.5 Pro cannot go below 128, so it thinks on a small fixed budget - and
    the caller's `max_tokens` is raised by it rather than shared with it."""
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}], model="gemini-2.5-pro", max_tokens=2048
    )
    generation = t.body["generationConfig"]
    assert generation["thinkingConfig"] == {"thinkingBudget": 1024}
    assert generation["maxOutputTokens"] == 2048 + 1024


def test_gemini_sends_the_thinking_config_even_without_a_max_tokens(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k", default_model="gemini-2.5-flash").chat(
        [{"role": "user", "content": "hi"}]
    )
    generation = t.body["generationConfig"]
    assert generation["thinkingConfig"] == {"thinkingBudget": 0}
    assert "maxOutputTokens" not in generation


def test_gemini_2_0_gets_no_thinking_config_and_an_untouched_answer_budget(monkeypatch):
    """Thinking budgets are a 2.5 feature: 2.0 rejects `thinkingConfig` with a
    400, and the media model box is free text, so an operator typing
    `gemini-2.0-flash` would fail every call."""
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [{"role": "user", "content": "read it"}],
        model="gemini-2.0-flash",
        max_tokens=2048,
    )
    generation = t.body["generationConfig"]
    assert "thinkingConfig" not in generation
    assert generation["maxOutputTokens"] == 2048


def test_gemini_thinking_budget_helper():
    assert _gemini_thinking_budget("gemini-2.5-flash") == 0
    assert _gemini_thinking_budget("gemini-2.5-flash-lite") == 0
    assert _gemini_thinking_budget("gemini-2.5-pro") == 1024
    # Newer families think too, and a Pro-family model rejects a zero budget.
    assert _gemini_thinking_budget("gemini-3-something") == 1024


def test_a_gemini_3_flash_never_gets_the_zero_budget_that_2_5_takes():
    """3.x rejects `thinkingBudget: 0` outright, so the floor there is 1.

    Measured 2026-08-22 against the live API with our own key:
    `gemini-3.5-flash-lite` answers on 1, 128 and on an omitted config, and
    returns `400 INVALID_ARGUMENT` (no field named) on 0. `gemini-2.5-flash`
    takes 0 as it always has. This is not a tuning preference: sending 0 fails
    EVERY call on a 3.x flash model, which is how a degraded media tier can be
    configured to a valid, listed, currently-offered model and still never work.
    """
    assert _gemini_thinking_budget("gemini-3.5-flash-lite") == 1
    assert _gemini_thinking_budget("gemini-3.1-flash-lite") == 1
    assert _gemini_thinking_budget("gemini-3.5-flash") == 1
    # Pro on 3.x already had a non-zero budget and keeps it.
    assert _gemini_thinking_budget("gemini-3.5-pro") == 1024
    # None means "this model has no thinking to budget", so the caller leaves
    # `thinkingConfig` off the request: 2.0 and older reject the field with a
    # 400, and the model box on the settings page is free text.
    assert _gemini_thinking_budget("gemini-2.0-flash") is None
    assert _gemini_thinking_budget("gemini-1.5-pro") is None
    assert _gemini_thinking_budget(None) is None


def test_gemini_counts_thinking_tokens_as_completion(monkeypatch):
    """`thoughtsTokenCount` is billed as output and IS inside the total, so
    leaving it out makes prompt + completion disagree with `totalTokenCount`."""
    _install(
        monkeypatch,
        _GeminiTransport(
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "ok"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 40,
                    "thoughtsTokenCount": 60,
                    "totalTokenCount": 200,
                },
            }
        ),
    )
    result = GeminiProvider("k").chat([{"role": "user", "content": "hi"}])
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 100
    assert result.total_tokens == 200
    assert result.prompt_tokens + result.completion_tokens == result.total_tokens


def test_gemini_chat_rejects_tools_together_with_json_mode(monkeypatch):
    """Gemini 400s on function declarations plus a JSON response mime type, so
    the contradiction is refused before the request is built."""

    def _explode(*_a, **_kw):
        raise AssertionError("no request should be made for a rejected combination")

    monkeypatch.setattr("httpx.request", _explode)
    tools = [
        {"type": "function", "function": {"name": "do_thing", "parameters": {}}}
    ]

    with pytest.raises(ValueError) as excinfo:
        GeminiProvider("k").chat(
            [{"role": "user", "content": "hi"}],
            tools=tools,
            response_format={"type": "json_object"},
        )
    assert "function calling" in str(excinfo.value)

    with pytest.raises(ValueError):
        GeminiProvider("k").chat(
            [{"role": "user", "content": "hi"}],
            tools=tools,
            json_schema={"type": "object", "properties": {}},
            json_schema_name="thing",
        )


def test_gemini_chat_appends_images_to_the_last_user_turn(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [
            {"role": "system", "content": "read it"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "noted"},
            {"role": "user", "content": "what does this say"},
        ],
        images=[
            ImagePart(mime="image/png", data_b64="AAAA"),
            ImagePart(mime="image/webp", data_b64="BBBB"),
        ],
    )

    contents = t.body["contents"]
    # Same placement rule as the OpenAI adapter: the LAST user turn, text kept.
    assert contents[-1]["role"] == "user"
    assert contents[-1]["parts"] == [
        {"text": "what does this say"},
        {"inlineData": {"mimeType": "image/png", "data": "AAAA"}},
        {"inlineData": {"mimeType": "image/webp", "data": "BBBB"}},
    ]
    assert contents[0]["parts"] == [{"text": "first"}]


def test_gemini_chat_creates_a_user_turn_for_images_when_there_is_none(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [{"role": "system", "content": "read it"}],
        images=[ImagePart(mime="image/jpeg", data_b64="CCCC")],
    )
    assert t.body["contents"] == [
        {
            "role": "user",
            "parts": [{"inlineData": {"mimeType": "image/jpeg", "data": "CCCC"}}],
        }
    ]


def test_gemini_chat_never_attaches_images_to_a_tool_result_turn(monkeypatch):
    """Gemini has no `tool` role, so a tool result comes back as `role: user`.

    Attaching the image to it would mix an `inlineData` part into a
    `functionResponse` turn; the real user turn above it is the target.
    """
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [
            {"role": "user", "content": "what does this say"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "stock_balance", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"on_hand": 12}'},
        ],
        images=[ImagePart(mime="image/png", data_b64="AAAA")],
    )

    contents = t.body["contents"]
    assert contents[0]["parts"] == [
        {"text": "what does this say"},
        {"inlineData": {"mimeType": "image/png", "data": "AAAA"}},
    ]
    # The tool result turn is left exactly as converted.
    assert contents[-1]["parts"] == [
        {"functionResponse": {"name": "stock_balance", "response": {"on_hand": 12}}}
    ]


def test_gemini_chat_adds_a_user_turn_when_only_tool_results_remain(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [{"role": "tool", "tool_call_id": "c1", "name": "do_thing", "content": "{}"}],
        images=[ImagePart(mime="image/jpeg", data_b64="CCCC")],
    )
    contents = t.body["contents"]
    assert len(contents) == 2
    assert "functionResponse" in contents[0]["parts"][0]
    assert contents[1] == {
        "role": "user",
        "parts": [{"inlineData": {"mimeType": "image/jpeg", "data": "CCCC"}}],
    }


def test_gemini_chat_declares_tools_as_function_declarations(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "do_thing",
                    "description": "does",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"x": {"type": "integer"}},
                        "required": ["x"],
                    },
                },
            },
            {
                "type": "function",
                "function": {"name": "other", "parameters": {"type": "object"}},
            },
        ],
    )

    # ONE tools entry holding every declaration, not one entry per tool.
    assert len(t.body["tools"]) == 1
    declarations = t.body["tools"][0]["functionDeclarations"]
    assert [d["name"] for d in declarations] == ["do_thing", "other"]
    # `additionalProperties` is not in Gemini's schema dialect and is a 400.
    assert "additionalProperties" not in declarations[0]["parameters"]
    assert declarations[0]["parameters"]["required"] == ["x"]


def test_gemini_chat_normalizes_function_calls_with_synthesized_ids(monkeypatch):
    _install(
        monkeypatch,
        _GeminiTransport(
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "let me look"},
                                {"functionCall": {"name": "do_thing", "args": {"x": 1}}},
                                {"functionCall": {"name": "do_thing", "args": {"x": 2}}},
                            ],
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
            }
        ),
    )

    result = GeminiProvider("k").chat([{"role": "user", "content": "hi"}])

    assert result.content == "let me look"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4
    # No totalTokenCount in the payload -> derived, never left at zero.
    assert result.total_tokens == 7
    assert [c["name"] for c in result.tool_calls] == ["do_thing", "do_thing"]
    assert [c["arguments"] for c in result.tool_calls] == [{"x": 1}, {"x": 2}]
    # Gemini returns no ids; the same tool called twice must still get two.
    ids = [c["id"] for c in result.tool_calls]
    assert len(set(ids)) == 2
    assert all(i for i in ids)


def test_gemini_chat_round_trips_an_assistant_tool_call_and_its_result(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [
            {"role": "user", "content": "stock for SRTKS6647?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "gemini_0_stock_balance",
                        "type": "function",
                        "function": {
                            "name": "stock_balance",
                            "arguments": json.dumps({"code": "SRTKS6647"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "gemini_0_stock_balance",
                "content": json.dumps({"on_hand": 12}),
            },
        ]
    )

    contents = t.body["contents"]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"] == [
        {"functionCall": {"name": "stock_balance", "args": {"code": "SRTKS6647"}}}
    ]
    # Gemini keys the response by NAME, resolved from the call it answers.
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"] == [
        {"functionResponse": {"name": "stock_balance", "response": {"on_hand": 12}}}
    ]


def test_gemini_json_object_response_format_sets_the_response_mime_type(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response('{"a":1}')))
    result = GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
    )
    assert result.content == '{"a":1}'
    assert t.body["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" not in t.body["generationConfig"]


def test_gemini_json_schema_sets_a_translated_response_schema(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response('{"intent":"x"}')))
    GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
        json_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {"type": "string"},
                "note": {"type": ["string", "null"]},
            },
            "required": ["intent", "note"],
        },
        json_schema_name="structured_result",
    )

    schema = t.body["generationConfig"]["responseSchema"]
    assert t.body["generationConfig"]["responseMimeType"] == "application/json"
    assert "additionalProperties" not in schema
    # OpenAI spells a nullable as ["string","null"]; Gemini wants a flag.
    assert schema["properties"]["note"] == {"type": "string", "nullable": True}
    assert schema["properties"]["intent"] == {"type": "string"}


def test_gemini_json_schema_drops_the_null_member_from_a_nullable_enum(monkeypatch):
    """The real schema every assistant turn sends. `_semantic_parse` calls
    `provider.chat(json_schema=PARSE_RESULT_JSON_SCHEMA)`, whose `form_target`,
    `entities.domain` and `entities.time_scope` each spell the null branch as a
    literal `None` inside `enum` (OpenAI strict mode's form). Gemini's
    `Schema.enum` is a proto `repeated string`, so a null member is a 400 and
    semantic routing dies silently into `fallback_parse` on every turn."""
    from app.schemas.ai_semantic_parser import PARSE_RESULT_JSON_SCHEMA

    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response('{"intent":"x"}')))
    GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
        json_schema=PARSE_RESULT_JSON_SCHEMA,
        json_schema_name="parse_result",
    )

    schema = t.body["generationConfig"]["responseSchema"]

    form_target = schema["properties"]["form_target"]
    assert None not in form_target["enum"]
    assert form_target["enum"] == [
        "complaint",
        "stock_inquiry",
        "purchase_request",
        "sponsorship_form",
    ]
    assert form_target["nullable"] is True
    assert form_target["type"] == "string"

    entities = schema["properties"]["entities"]["properties"]
    assert None not in entities["domain"]["enum"]
    assert entities["domain"]["nullable"] is True
    assert None not in entities["time_scope"]["enum"]
    assert entities["time_scope"]["nullable"] is True

    # A non-nullable enum is untouched, so the translation cannot be passing by
    # emptying every enum it sees.
    intent = schema["properties"]["intent"]
    assert "unknown" in intent["enum"]
    assert "nullable" not in intent

    def _no_null_enum(node):
        if isinstance(node, dict):
            if isinstance(node.get("enum"), list):
                assert None not in node["enum"], node
            for value in node.values():
                _no_null_enum(value)
        elif isinstance(node, list):
            for item in node:
                _no_null_enum(item)

    _no_null_enum(schema)


def test_gemini_chat_surfaces_the_provider_error_message(monkeypatch):
    _install(
        monkeypatch,
        _GeminiTransport(
            {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}},
            status_code=400,
        ),
    )
    with pytest.raises(RuntimeError) as excinfo:
        GeminiProvider("k").chat([{"role": "user", "content": "hi"}])
    assert "API key not valid" in str(excinfo.value)
    assert "400" in str(excinfo.value)


def test_gemini_chat_raises_when_the_prompt_was_blocked(monkeypatch):
    _install(
        monkeypatch,
        _GeminiTransport({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}),
    )
    with pytest.raises(RuntimeError) as excinfo:
        GeminiProvider("k").chat([{"role": "user", "content": "hi"}])
    assert "SAFETY" in str(excinfo.value)


def test_gemini_chat_raises_on_an_empty_candidate(monkeypatch):
    _install(
        monkeypatch,
        _GeminiTransport(
            {
                "candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}],
                "usageMetadata": {"promptTokenCount": 3},
            }
        ),
    )
    with pytest.raises(RuntimeError) as excinfo:
        GeminiProvider("k").chat([{"role": "user", "content": "hi"}])
    assert "MAX_TOKENS" in str(excinfo.value)


def test_gemini_chat_without_an_api_key_never_reaches_the_network(monkeypatch):
    def _explode(*_a, **_kw):
        raise AssertionError("no request should be made without a key")

    monkeypatch.setattr("httpx.request", _explode)
    with pytest.raises(RuntimeError) as excinfo:
        GeminiProvider("").chat([{"role": "user", "content": "hi"}])
    assert "Gemini API key" in str(excinfo.value)


def test_gemini_embed_returns_a_vector_sized_to_the_column(monkeypatch):
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "embedding_dimensions", 4, raising=False)
    t = _install(
        monkeypatch,
        _GeminiTransport({"embedding": {"values": [3.0, 0.0, 4.0, 0.0]}}),
    )

    vector = GeminiProvider("k").embed("hello")

    assert t.last["url"].endswith("/models/gemini-embedding-001:embedContent")
    assert t.body["outputDimensionality"] == 4
    assert t.body["content"] == {"parts": [{"text": "hello"}]}
    # Truncated below the native size, so re-normalized to unit length.
    assert vector == [0.6, 0.0, 0.8, 0.0]


def test_gemini_embed_raises_when_no_vector_came_back(monkeypatch):
    _install(monkeypatch, _GeminiTransport({"embedding": {}}))
    with pytest.raises(RuntimeError):
        GeminiProvider("k").embed("hello")


def test_gemini_test_connection_ok(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport({"models": [{"name": "models/x"}]}))
    ok, message, latency = GeminiProvider("k").test_connection()
    assert ok is True
    assert message == "OK"
    # Free and authenticated: a list, not a generate that a thinking model
    # would truncate to an empty candidate.
    assert t.last["method"] == "GET"
    assert t.last["url"].endswith("/models")


def test_gemini_test_connection_failure_carries_the_provider_message(monkeypatch):
    _install(
        monkeypatch,
        _GeminiTransport({"error": {"message": "API key not valid"}}, status_code=403),
    )
    ok, message, latency = GeminiProvider("k").test_connection()
    assert ok is False
    assert "API key not valid" in message


# ---- Gemini conversion helpers -------------------------------------------


def test_convert_tools_to_gemini_skips_unusable_entries():
    assert _convert_tools_to_gemini([{"type": "function", "function": {}}]) == []
    assert _convert_tools_to_gemini([]) == []


def test_convert_messages_to_gemini_maps_assistant_to_model():
    out = _convert_messages_to_gemini(
        [
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
    )
    assert [c["role"] for c in out] == ["user", "model"]


def test_convert_messages_to_gemini_wraps_a_non_object_tool_result():
    """Gemini's functionResponse.response must be an object; a bare string or
    list is a 400, so it is wrapped rather than sent through."""
    out = _convert_messages_to_gemini(
        [{"role": "tool", "tool_call_id": "c1", "name": "do_thing", "content": "plain text"}]
    )
    assert out[0]["parts"][0]["functionResponse"]["response"] == {"result": "plain text"}


def test_gemini_schema_drops_unsupported_keys_recursively():
    out = _gemini_schema(
        {
            "type": "object",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"raw": {"type": "string", "default": ""}},
                    },
                }
            },
        }
    )
    assert "$schema" not in out and "additionalProperties" not in out
    nested = out["properties"]["items"]["items"]
    assert "additionalProperties" not in nested
    assert nested["properties"]["raw"] == {"type": "string"}


@pytest.mark.parametrize("asked", ["gemini", "Gemini", " gemini "])
def test_resolve_api_key_normalizes_the_provider_name(asked, monkeypatch):
    """`get_provider` and `default_model_for` both lower/strip the name, so the
    key resolver must agree - otherwise a stored "Gemini" resolves an OpenAI key
    and hands it to a Gemini client."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "openai_api_key", "ZZT-openai-env-key", raising=False)
    monkeypatch.setattr(app_settings, "gemini_api_key", "", raising=False)
    cfg = types.SimpleNamespace(
        provider="Gemini",
        api_key_ciphertext="ZZT-generic-key",
        gemini_api_key_ciphertext="ZZT-gemini-column-key",
        anthropic_api_key_ciphertext="",
    )

    assert resolve_api_key(cfg, asked) == "ZZT-gemini-column-key"


def test_convert_tools_to_gemini_omits_parameters_for_a_parameterless_tool():
    """Gemini's REST API 400s an OBJECT schema with empty `properties`, so a
    tool that takes no arguments must carry no `parameters` at all - one such
    MCP tool would otherwise fail every agent-loop turn on the first call."""
    declarations = _convert_tools_to_gemini(
        [
            {"type": "function", "function": {"name": "no_args"}},
            {
                "type": "function",
                "function": {
                    "name": "empty_object",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "with_args",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                },
            },
        ]
    )[0]["functionDeclarations"]

    by_name = {d["name"]: d for d in declarations}
    assert "parameters" not in by_name["no_args"]
    assert "parameters" not in by_name["empty_object"]
    assert by_name["with_args"]["parameters"]["properties"] == {"q": {"type": "string"}}


def test_gemini_chat_sends_a_parameterless_tool_without_a_parameters_field(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "ping", "description": "p"}}],
    )
    declaration = t.body["tools"][0]["functionDeclarations"][0]
    assert declaration == {"name": "ping", "description": "p"}


def test_resolve_model_prefers_the_explicit_model():
    cfg = types.SimpleNamespace(provider="openai", model="gpt-4o")
    assert resolve_model(cfg, "gemini", "gemini-2.5-pro") == "gemini-2.5-pro"


@pytest.mark.parametrize("asked", ["openai", "OpenAI", " openai "])
def test_resolve_model_inherits_the_assistant_model_only_for_the_same_provider(asked):
    cfg = types.SimpleNamespace(provider="OpenAI ", model="gpt-4o")
    assert resolve_model(cfg, asked, None) == "gpt-4o"


def test_resolve_model_never_hands_one_vendors_model_to_another():
    """The assistant runs on gpt-4o; a lane pointed at Gemini with no model of its
    own must get Gemini's default, not `gpt-4o` posted to Google."""
    cfg = types.SimpleNamespace(provider="openai", model="gpt-4o")
    assert resolve_model(cfg, "gemini", None) == default_model_for("gemini")
    assert resolve_model(cfg, "gemini", "") == default_model_for("gemini")


def test_resolve_model_without_a_config_row_uses_the_provider_default():
    assert resolve_model(None, "anthropic", None) == default_model_for("anthropic")


# ---- PARSER-GEMINI-LITE: multi-type fields, reasoning effort, answering model ----


def test_gemini_schema_turns_a_multi_type_field_into_any_of():
    """`{"type": ["string","array"], "items": ...}` used to collapse to a STRING that
    still carried `items`, and Google rejects that with a 400 - every Gemini parser call
    failed on `profile_statements[].value`. Each concrete type becomes its own branch and
    only the array branch keeps `items`."""
    translated = _gemini_schema(
        {"type": ["string", "array"], "items": {"type": "string"}, "description": "d"}
    )
    assert translated == {
        "description": "d",
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
        ],
    }


def test_gemini_schema_carries_null_onto_every_any_of_branch():
    translated = _gemini_schema({"type": ["number", "string", "null"]})
    assert translated == {
        "anyOf": [
            {"type": "number", "nullable": True},
            {"type": "string", "nullable": True},
        ]
    }


def test_gemini_schema_keeps_a_single_type_with_null_as_a_nullable_type():
    """The common OpenAI-strict nullable is unchanged: no needless `anyOf`."""
    assert _gemini_schema(
        {"type": ["array", "null"], "maxItems": 3, "items": {"type": "string"}}
    ) == {"type": "array", "nullable": True, "maxItems": 3, "items": {"type": "string"}}


def test_openai_sends_reasoning_effort_only_when_asked(monkeypatch):
    stub = _StubOpenAIClient()
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None, **_k: stub)
    provider = OpenAIProvider("k", default_model="gpt-5.4-mini")

    provider.chat([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in stub.last_kwargs

    provider.chat([{"role": "user", "content": "hi"}], reasoning_effort="low")
    assert stub.last_kwargs["reasoning_effort"] == "low"


def test_openai_reports_the_answering_model_and_reasoning_tokens(monkeypatch):
    stub = _StubOpenAIClient()
    usage = _StubUsage(100, 40)
    usage.completion_tokens_details = types.SimpleNamespace(reasoning_tokens=32)
    stub.chat.completions._next_usage = usage
    original_create = stub.chat.completions.create

    def create(**kwargs):
        completion = original_create(**kwargs)
        completion.model = "gpt-5.4-mini-2026-03-17"
        return completion

    stub.chat.completions.create = create
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None, **_k: stub)
    result = OpenAIProvider("k", default_model="gpt-5.4-mini").chat(
        [{"role": "user", "content": "hi"}]
    )
    assert result.reasoning_tokens == 32
    assert result.model == "gpt-5.4-mini-2026-03-17"


def test_gemini_maps_reasoning_effort_onto_the_thinking_budget(monkeypatch):
    t = _install(monkeypatch, _GeminiTransport(_gemini_text_response()))
    provider = GeminiProvider("k")

    # Unset: the provider default this file already pins, unchanged.
    provider.chat([{"role": "user", "content": "hi"}], model="gemini-3.5-flash-lite", max_tokens=100)
    assert t.body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 1}
    assert t.body["generationConfig"]["maxOutputTokens"] == 101

    provider.chat(
        [{"role": "user", "content": "hi"}],
        model="gemini-3.5-flash-lite",
        max_tokens=100,
        reasoning_effort="low",
    )
    budget = t.body["generationConfig"]["thinkingConfig"]["thinkingBudget"]
    assert budget > 1
    assert t.body["generationConfig"]["maxOutputTokens"] == 100 + budget

    # "none" / "minimal" is the smallest budget the model takes (3.x refuses zero).
    provider.chat(
        [{"role": "user", "content": "hi"}],
        model="gemini-3.5-flash-lite",
        reasoning_effort="minimal",
    )
    assert t.body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 1}

    # A model with no thinking (2.0) still gets no thinkingConfig, whatever is asked.
    provider.chat(
        [{"role": "user", "content": "hi"}], model="gemini-2.0-flash", reasoning_effort="high"
    )
    assert "thinkingConfig" not in t.body["generationConfig"]


def test_gemini_reports_the_answering_model_and_reasoning_tokens(monkeypatch):
    payload = _gemini_text_response()
    payload["usageMetadata"]["thoughtsTokenCount"] = 9
    payload["modelVersion"] = "gemini-3.5-flash-lite-001"
    _install(monkeypatch, _GeminiTransport(payload))
    result = GeminiProvider("k").chat(
        [{"role": "user", "content": "hi"}], model="gemini-3.5-flash-lite"
    )
    assert result.reasoning_tokens == 9
    assert result.model == "gemini-3.5-flash-lite-001"


def test_anthropic_accepts_reasoning_effort_and_ignores_it(monkeypatch):
    stub = _StubAnthropicClient()
    monkeypatch.setattr(AnthropicProvider, "_client", lambda self, **_k: stub)
    AnthropicProvider("k").chat([{"role": "user", "content": "hi"}], reasoning_effort="low")
    assert "reasoning_effort" not in stub.last_kwargs


def test_a_refused_effort_value_is_not_remembered_against_the_model(monkeypatch):
    """`minimal` on a model that takes other efforts is a 400 `unsupported_value`. That is
    a fact about the VALUE, so the next call (another effort, or the same after the label
    is fixed) still sends one; only `Unsupported parameter` means the model takes none."""
    import app.services.llm_provider as llm

    class _Refusal(Exception):
        code = "unsupported_value"
        param = "reasoning_effort"

    stub = _StubOpenAIClient()
    original_create = stub.chat.completions.create
    refused = {"left": 1}

    def create(**kwargs):
        if kwargs.get("reasoning_effort") == "minimal" and refused["left"]:
            refused["left"] -= 1
            raise _Refusal("Unsupported value: 'reasoning_effort' does not support 'minimal' with this model.")
        return original_create(**kwargs)

    stub.chat.completions.create = create
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None, **_k: stub)
    monkeypatch.setattr(llm, "_OPENAI_REJECTED_PARAMS", {})
    provider = OpenAIProvider("k", default_model="gpt-effort-test")

    provider.chat([{"role": "user", "content": "hi"}], reasoning_effort="minimal")
    assert "reasoning_effort" not in stub.last_kwargs  # this call went without it

    provider.chat([{"role": "user", "content": "hi"}], reasoning_effort="low")
    assert stub.last_kwargs["reasoning_effort"] == "low"


def test_a_model_that_takes_no_effort_is_remembered_and_not_asked_again(monkeypatch):
    """gpt-4o answers `Unsupported parameter: 'reasoning_effort'`: a fact about the MODEL,
    so the 400 is paid once per process, not on every call (the carve-out above is for a
    refused VALUE only)."""
    import app.services.llm_provider as llm

    class _NoEffort(Exception):
        code = "unsupported_parameter"
        param = "reasoning_effort"

    stub = _StubOpenAIClient()
    original_create = stub.chat.completions.create
    refusals: list[dict] = []

    def create(**kwargs):
        if "reasoning_effort" in kwargs:
            refusals.append(kwargs)
            raise _NoEffort("Unsupported parameter: 'reasoning_effort' is not supported with this model.")
        return original_create(**kwargs)

    stub.chat.completions.create = create
    monkeypatch.setattr("openai.OpenAI", lambda api_key=None, **_k: stub)
    monkeypatch.setattr(llm, "_OPENAI_REJECTED_PARAMS", {})
    provider = OpenAIProvider("k", default_model="gpt-4o")

    provider.chat([{"role": "user", "content": "hi"}], reasoning_effort="low")
    provider.chat([{"role": "user", "content": "hi"}], reasoning_effort="low")
    assert len(refusals) == 1
    assert "reasoning_effort" not in stub.last_kwargs
