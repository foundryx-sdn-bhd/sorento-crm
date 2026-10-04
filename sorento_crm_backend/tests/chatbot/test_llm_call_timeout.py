"""CHATBOT-QUEUE-FIX item 7: the parser LLM call has a hard time budget.

Prod 1 Oct, contact 900000081, turn a45f (ticket 3): stage `understood` (the parser call)
took 230,475 ms while every other turn's parse took 2.6-4.1 s. The call had no timeout at
all (the old `head/parser.py` comment said so), so the OpenAI SDK's 600 s default and its
two silent retries applied, and the contact's queue slot was held for almost four minutes.

Now: every attempt carries `ATTEMPT_TIMEOUT_SECONDS` with the SDK's retries off, the whole
call (backoff included) stops at `CALL_DEADLINE_SECONDS`, and a timeout is `TimedOut`, a
`RateLimited`, so the parser fails `understood` with `rate_limited=True` and the dealer
reads `RATE_LIMITED_REPLY` (that mapping is pinned by `test_chatbot_r6_rate_limit.py`).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services import llm_provider
from app.services.chatbot import llm_call
from app.services.chatbot.head import parser as parser_mod
from app.services.llm_provider import ChatResult


class APITimeoutError(Exception):
    """The OpenAI / Anthropic SDK's own class name for a request timeout."""


class RateLimitError(Exception):
    status_code = 429


class Provider:
    def __init__(self, errors: list[Exception] | None = None, content: str = "{}"):
        self.errors = list(errors or [])
        self.timeouts: list[Any] = []
        self.content = content

    def chat(self, messages, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        if self.errors:
            raise self.errors.pop(0)
        return ChatResult(content=self.content, prompt_tokens=1, completion_tokens=1, total_tokens=2)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(llm_call, "_monotonic", c)

    def _sleep(seconds: float) -> None:
        c.now += seconds

    monkeypatch.setattr(llm_call, "_sleep", _sleep)
    return c


def _install(monkeypatch, provider: Provider) -> None:
    monkeypatch.setattr(llm_provider, "get_provider", lambda *a, **k: provider)


def test_budget_is_well_under_the_n8n_turn_budget():
    from app.services.chatbot.send_order import N8N_CHAT_TURN_TIMEOUT_SECONDS

    assert llm_call.ATTEMPT_TIMEOUT_SECONDS <= llm_call.CALL_DEADLINE_SECONDS
    assert llm_call.CALL_DEADLINE_SECONDS <= N8N_CHAT_TURN_TIMEOUT_SECONDS / 2


def test_every_attempt_carries_a_timeout(monkeypatch, clock):
    provider = Provider()
    _install(monkeypatch, provider)
    llm_call.chat("openai", "k", "m", [])
    assert provider.timeouts == [llm_call.ATTEMPT_TIMEOUT_SECONDS]


def test_a_timeout_raises_timed_out_and_is_not_retried(monkeypatch, clock):
    provider = Provider(errors=[APITimeoutError("Request timed out.")])
    _install(monkeypatch, provider)
    with pytest.raises(llm_call.TimedOut) as caught:
        llm_call.chat("openai", "k", "m", [])
    assert isinstance(caught.value, llm_call.RateLimited), "callers send the busy reply"
    assert len(provider.timeouts) == 1


def test_gemini_style_wrapped_timeout_is_recognised(monkeypatch, clock):
    import httpx

    try:
        try:
            raise httpx.ReadTimeout("read timed out")
        except httpx.ReadTimeout as inner:
            raise RuntimeError(f"Gemini request failed: {inner}") from inner
    except RuntimeError as wrapped:
        error = wrapped
    _install(monkeypatch, Provider(errors=[error]))
    with pytest.raises(llm_call.TimedOut):
        llm_call.chat("gemini", "k", "m", [])


def test_rate_limit_backoff_never_outruns_the_deadline(monkeypatch, clock):
    """Each attempt eats its whole timeout before the 429: the waits plus attempts stop at
    the deadline instead of running all four attempts (4 x 20 s + 17 s of waits)."""
    started = clock.now

    class Slow429(Provider):
        def chat(self, messages, **kwargs):
            self.timeouts.append(kwargs.get("timeout"))
            clock.now += kwargs["timeout"]
            raise RateLimitError("Error code: 429 - slow down")

    provider = Slow429()
    _install(monkeypatch, provider)
    with pytest.raises(llm_call.RateLimited):
        llm_call.chat("openai", "k", "m", [])
    assert clock.now - started <= llm_call.CALL_DEADLINE_SECONDS
    assert all(t is not None and t <= llm_call.ATTEMPT_TIMEOUT_SECONDS for t in provider.timeouts)


def test_last_attempt_gets_only_the_time_left(monkeypatch, clock):
    class Eat(Provider):
        def chat(self, messages, **kwargs):
            self.timeouts.append(kwargs.get("timeout"))
            if len(self.timeouts) == 1:
                clock.now += 18.0
                raise RateLimitError("Error code: 429 - slow down")
            return ChatResult(content="ok", prompt_tokens=1, completion_tokens=1, total_tokens=2)

    provider = Eat()
    _install(monkeypatch, provider)
    llm_call.chat("openai", "k", "m", [])
    # 18 s spent + 2 s backoff = 20 s gone, 15 s left of the 35 s deadline.
    assert provider.timeouts[1] == pytest.approx(llm_call.CALL_DEADLINE_SECONDS - 20.0)


def test_parser_timeout_fails_understood_with_the_busy_flag(monkeypatch, clock):
    _install(monkeypatch, Provider(errors=[APITimeoutError("Request timed out.")]))
    config = parser_mod.ParserConfig(
        system_prompt="s", prompt_version=1, provider="openai", model="m", api_key="k"
    )
    with pytest.raises(parser_mod.ParserError) as caught:
        parser_mod.parse(config, "block")
    assert caught.value.rate_limited is True
    assert "within" in str(caught.value)


@pytest.mark.parametrize("cls", [llm_provider.OpenAIProvider, llm_provider.AnthropicProvider])
def test_sdk_providers_build_a_client_with_the_timeout_and_no_sdk_retries(monkeypatch, cls):
    built: list[dict] = []

    def _client(self, **overrides):
        built.append(overrides)
        raise RuntimeError("stop after construction")

    monkeypatch.setattr(cls, "_client", _client)
    with pytest.raises(RuntimeError):
        cls("k").chat([{"role": "user", "content": "x"}], timeout=7.5)
    assert built == [{"timeout": 7.5, "max_retries": 0}]

    built.clear()
    with pytest.raises(RuntimeError):
        cls("k").chat([{"role": "user", "content": "x"}])
    assert built == [{}], "callers that pass no timeout keep the SDK defaults"


def test_gemini_passes_the_timeout_to_its_request(monkeypatch):
    seen: list[Any] = []

    def _request(self, method, path, *, json_body=None, params=None, timeout=None):
        seen.append(timeout)
        raise RuntimeError("stop")

    monkeypatch.setattr(llm_provider.GeminiProvider, "_request", _request)
    with pytest.raises(RuntimeError):
        llm_provider.GeminiProvider("k").chat([{"role": "user", "content": "x"}], timeout=9.0)
    assert seen == [9.0]


class InternalServerError(Exception):
    status_code = 502


class OverloadedError(Exception):
    """Anthropic's 529."""

    status_code = 529


@pytest.mark.parametrize("error", [InternalServerError("bad gateway"), OverloadedError("overloaded")])
def test_a_transient_provider_error_is_retried(monkeypatch, clock, error):
    """The SDK's own retries are off, so a 5xx / 529 is retried here (review S2)."""
    provider = Provider(errors=[error], content="ok")
    _install(monkeypatch, provider)
    assert llm_call.chat("openai", "k", "m", []).content == "ok"
    assert len(provider.timeouts) == 2


def test_a_provider_that_stays_unavailable_ends_busy(monkeypatch, clock):
    provider = Provider(errors=[InternalServerError("bad gateway")] * 10)
    _install(monkeypatch, provider)
    with pytest.raises(llm_call.RateLimited):
        llm_call.chat("openai", "k", "m", [])


def test_a_callers_own_timeout_is_honoured_not_a_type_error(monkeypatch, clock):
    provider = Provider()
    _install(monkeypatch, provider)
    llm_call.chat("openai", "k", "m", [], timeout=5.0)
    assert provider.timeouts == [5.0]


def test_an_error_whose_text_says_timeout_is_not_a_timeout(monkeypatch, clock):
    provider = Provider(errors=[ValueError("invalid value for 'timeout' parameter")])
    _install(monkeypatch, provider)
    with pytest.raises(ValueError):
        llm_call.chat("openai", "k", "m", [])
