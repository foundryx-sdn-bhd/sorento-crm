"""OpenAI parameter dialect: the first request a model sees is the one it accepts.

CHAT-SLOW-MISS (4 Oct 2026): every chatbot LLM call on gpt-5.4-mini went out twice -
the first with `max_tokens`, a 400 "Unsupported parameter: 'max_tokens'", then a retry
with `max_completion_tokens` - doubling the latency of every turn. The ceiling now goes
out as `max_completion_tokens`, which every OpenAI chat model accepts, and anything a
model still refuses is remembered for that model so the refusal is paid once per process.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from app.services import llm_provider
from app.services.llm_provider import OpenAIProvider


class _BadRequest(Exception):
    def __init__(self, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


def _completion() -> Any:
    msg = types.SimpleNamespace(content="ok", tool_calls=[])
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg)],
        usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class _Client:
    """`chat.completions.create` that refuses the parameters named in `rejects`."""

    def __init__(self, rejects: dict[str, str]) -> None:
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            def create(inner, **kwargs):
                outer.calls.append(dict(kwargs))
                for param, message in rejects.items():
                    if param in kwargs:
                        raise _BadRequest(message, param=param)
                return _completion()

        self.chat = types.SimpleNamespace(completions=_Completions())


_GPT5_MAX_TOKENS = (
    "Error code: 400 - {'error': {'message': \"Unsupported parameter: 'max_tokens' is not "
    "supported with this model. Use 'max_completion_tokens' instead.\", 'type': "
    "'invalid_request_error', 'param': 'max_tokens', 'code': 'unsupported_parameter'}}"
)
_TEMPERATURE = (
    "Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' does not "
    "support 0.0 with this model. Only the default (1) value is supported.\", 'param': "
    "'temperature', 'code': 'unsupported_value'}}"
)


@pytest.fixture(autouse=True)
def _fresh_dialect_memo():
    memo = getattr(llm_provider, "_OPENAI_REJECTED_PARAMS", {})
    memo.clear()
    yield
    memo.clear()


def _provider(monkeypatch, client: _Client, model: str) -> OpenAIProvider:
    provider = OpenAIProvider("k", default_model=model)
    monkeypatch.setattr(provider, "_client", lambda **_kw: client)
    return provider


def test_a_reasoning_model_gets_its_ceiling_on_the_first_request(monkeypatch):
    client = _Client({"max_tokens": _GPT5_MAX_TOKENS})
    provider = _provider(monkeypatch, client, "gpt-5.4-mini")

    result = provider.chat([{"role": "user", "content": "hi"}], max_tokens=512)

    assert result.content == "ok"
    assert len(client.calls) == 1, client.calls
    assert client.calls[0]["max_completion_tokens"] == 512
    assert "max_tokens" not in client.calls[0]


def test_an_older_model_gets_the_same_ceiling(monkeypatch):
    client = _Client({})
    provider = _provider(monkeypatch, client, "gpt-4o-mini")

    provider.chat([{"role": "user", "content": "hi"}], max_tokens=128, temperature=0.2)

    assert len(client.calls) == 1
    assert client.calls[0]["max_completion_tokens"] == 128
    assert client.calls[0]["temperature"] == 0.2


def test_no_ceiling_sends_neither_spelling(monkeypatch):
    client = _Client({})
    _provider(monkeypatch, client, "gpt-4o-mini").chat([{"role": "user", "content": "hi"}])

    assert "max_tokens" not in client.calls[0]
    assert "max_completion_tokens" not in client.calls[0]


def test_a_refused_parameter_is_paid_for_once_per_model(monkeypatch):
    client = _Client({"temperature": _TEMPERATURE})
    provider = _provider(monkeypatch, client, "o-reasoner")

    provider.chat([{"role": "user", "content": "one"}], temperature=0.0, max_tokens=64)
    provider.chat([{"role": "user", "content": "two"}], temperature=0.0, max_tokens=64)

    # Turn one: refused, then retried without it. Turn two: sent right the first time.
    assert len(client.calls) == 3, client.calls
    assert "temperature" not in client.calls[2]
    assert client.calls[2]["max_completion_tokens"] == 64


def test_what_one_model_refuses_is_still_sent_to_another(monkeypatch):
    client = _Client({})
    refusing = _Client({"temperature": _TEMPERATURE})
    _provider(monkeypatch, refusing, "o-reasoner").chat(
        [{"role": "user", "content": "x"}], temperature=0.0
    )

    _provider(monkeypatch, client, "gpt-4o-mini").chat(
        [{"role": "user", "content": "x"}], temperature=0.0
    )

    assert client.calls[0]["temperature"] == 0.0


def test_a_model_that_wants_the_old_spelling_still_gets_its_ceiling(monkeypatch):
    client = _Client({"max_completion_tokens": "Unsupported parameter: 'max_completion_tokens'"})
    provider = _provider(monkeypatch, client, "legacy-chat")

    provider.chat([{"role": "user", "content": "x"}], max_tokens=32)
    provider.chat([{"role": "user", "content": "y"}], max_tokens=32)

    assert client.calls[1]["max_tokens"] == 32
    assert "max_completion_tokens" not in client.calls[1]
    assert len(client.calls) == 3, client.calls
    assert client.calls[2]["max_tokens"] == 32


def test_a_genuine_error_is_raised_not_retried(monkeypatch):
    class _Boom(Exception):
        pass

    class _Client500:
        def __init__(self) -> None:
            self.calls = 0
            outer = self

            class _C:
                def create(inner, **_kw):
                    outer.calls += 1
                    raise _Boom("Error code: 500 - server error")

            self.chat = types.SimpleNamespace(completions=_C())

    client = _Client500()
    provider = OpenAIProvider("k", default_model="gpt-5.4-mini")
    monkeypatch.setattr(provider, "_client", lambda **_kw: client)

    with pytest.raises(_Boom):
        provider.chat([{"role": "user", "content": "x"}], max_tokens=8)
    assert client.calls == 1


def test_an_unrelated_400_naming_a_parameter_is_not_remembered(monkeypatch):
    """A context-length 400 carries `param: 'messages'`. It fails that one call, as it
    always did, and never strips `messages` from the model's later calls."""

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            outer = self

            class _C:
                def create(inner, **kwargs):
                    outer.calls.append(dict(kwargs))
                    if "messages" not in kwargs:
                        raise _BadRequest("Missing required parameter: 'messages'.", param="messages")
                    if len(kwargs["messages"]) > 1:
                        exc = _BadRequest(
                            "This model's maximum context length is 128000 tokens.",
                            param="messages",
                        )
                        exc.code = "context_length_exceeded"
                        raise exc
                    return _completion()

            self.chat = types.SimpleNamespace(completions=_C())

    client = _Client()
    provider = _provider(monkeypatch, client, "gpt-5.4-mini")

    with pytest.raises(_BadRequest):
        provider.chat([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    result = provider.chat([{"role": "user", "content": "short"}])

    assert result.content == "ok"
    assert "messages" in client.calls[-1]
