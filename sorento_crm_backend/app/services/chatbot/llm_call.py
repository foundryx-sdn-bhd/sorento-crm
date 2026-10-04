"""The ONE seam every model call in a chatbot turn goes through: the semantic parser
(`head/parser.parse`, and so its recall re-parse) and the clarifier
(`lanes/casual.call_clarifier`). Nothing else in `app/services/chatbot` calls a provider.

Owner console test of round 4 (26 Sep 2026, PR #1247, ruling 3): two turns failed with
"Error code: 429 - Rate limit reached for gpt-5.4-mini ... tokens per min (TPM): Limit
200000, Used 200000" and that raw text went to the dealer. A rate limit is a wait, not a
failure: it is retried here with a growing backoff, inside the turn, honouring the
server's own "try again in Ns" when it gives one. Only when every attempt is refused does
the caller get `RateLimited`, and the caller then says `RATE_LIMITED_REPLY`, never the
provider's text (the operator still sees that on the turn row and the trace).

A tokens-per-minute limit clears over tens of seconds, which is why these waits are long.

CHATBOT-QUEUE-FIX (prod 1 Oct, turn a45f): the parser call had NO timeout. The SDK default
is 600 s with two silent retries, and one call took 230 s while the contact's queue slot
waited on it. Every attempt now carries `ATTEMPT_TIMEOUT_SECONDS` with the SDK's own retries
off (this loop is the only retry), and the whole call, backoff included, stops at
`CALL_DEADLINE_SECONDS`. A timeout raises `TimedOut`, which IS a `RateLimited`: to the dealer
both mean "the model is busy, send that again", so every caller that already says
`RATE_LIMITED_REPLY` says it for a hang too, and the row keeps the real reason. With the SDK
retries off, a 5xx / 529 / dropped connection (`is_transient`) is retried here on the same
backoff as a 429, and ends the same way.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

RATE_LIMITED_REPLY = "Sorry, I am busy right now, please send that again in a minute."

#: The wait before each retry, in seconds: four attempts in all.
BACKOFF_SECONDS = (2.0, 5.0, 10.0)
#: No single wait is longer than this, whatever the server asks for.
MAX_WAIT_SECONDS = 15.0
#: Nor do the waits add up to more than this: a dealer is waiting on the reply.
MAX_TOTAL_WAIT_SECONDS = 30.0

#: One attempt's wall clock. A normal parse answers in 2.6-4.1 s (prod trace, 1 Oct).
ATTEMPT_TIMEOUT_SECONDS = 20.0
#: The whole call, every attempt and wait included. Well under n8n's 90 s turn budget
#: (`send_order.N8N_CHAT_TURN_TIMEOUT_SECONDS`).
CALL_DEADLINE_SECONDS = 35.0
#: An attempt with less than this left is not started: it could only time out.
MIN_ATTEMPT_SECONDS = 1.0

_RETRY_IN_RE = re.compile(r"try again in (\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE)

# The seams a test replaces, so no test sleeps or waits on a real clock.
_sleep = time.sleep
_monotonic = time.monotonic


class RateLimited(RuntimeError):
    """Every attempt was refused with a rate limit. `str()` is the provider's last text."""


class TimedOut(RateLimited):
    """The provider did not answer inside the call's time budget. A `RateLimited` on
    purpose: the dealer's reply is the same "busy, send that again" (see module doc)."""


def is_timeout(exc: BaseException) -> bool:
    """A request timeout, however the SDK spells it (`openai.APITimeoutError`,
    `anthropic.APITimeoutError`, `httpx.TimeoutException`, or Gemini's RuntimeError
    wrapping one). Walks the `from` chain because Gemini re-raises."""
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 5:
        if isinstance(current, TimeoutError) or "Timeout" in type(current).__name__:
            return True
        # `__cause__` only (an explicit `raise ... from`): an unrelated error raised while
        # a timeout was being handled is not a timeout.
        current = current.__cause__
        seen += 1
    return False


def is_rate_limited(exc: BaseException) -> bool:
    """A 429, however the provider's SDK spells it."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429 or type(exc).__name__ == "RateLimitError":
        return True
    text = str(exc).lower()
    return "error code: 429" in text or "rate limit reached" in text


def _quota_exhausted(exc: BaseException) -> bool:
    """A 429 that means the account is out of credit, which no wait will clear."""
    return "insufficient_quota" in str(exc)


def is_transient(exc: BaseException) -> bool:
    """A provider-side blip the SDK used to retry on its own before its retries were
    turned off: a 5xx, Anthropic's 529 "overloaded", or a dropped connection."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    return type(exc).__name__ in {"APIConnectionError", "InternalServerError", "OverloadedError"}


def _server_wait(exc: BaseException) -> float | None:
    match = _RETRY_IN_RE.search(str(exc))
    if not match:
        return None
    value = float(match.group(1))
    return value / 1000.0 if match.group(2).lower() == "ms" else value


def chat(provider: str, api_key: str, model: str, messages: list[dict], **kwargs: Any):
    """`get_provider(provider, api_key, model).chat(messages, model=model, **kwargs)`,
    with a rate limit waited out and a hard time budget. Raises `RateLimited` when a rate
    limit never clears, `TimedOut` when the budget runs out; any other error is raised as
    it came, on the first attempt."""
    from app.services.llm_provider import get_provider

    client = get_provider(provider, api_key, model)
    deadline = _monotonic() + CALL_DEADLINE_SECONDS
    waited = 0.0
    for attempt, backoff in enumerate((*BACKOFF_SECONDS, None), 1):
        remaining = deadline - _monotonic()
        if remaining < MIN_ATTEMPT_SECONDS:
            logger.error(
                "chatbot llm: %s/%s out of time before attempt %s", provider, model, attempt
            )
            raise TimedOut(f"no answer from {provider} within {CALL_DEADLINE_SECONDS:.0f}s")
        timeout = min(float(kwargs.get("timeout") or ATTEMPT_TIMEOUT_SECONDS), remaining)
        try:
            return client.chat(messages, model=model, **{**kwargs, "timeout": timeout})
        except Exception as exc:  # noqa: BLE001 - inspected, then re-raised
            if is_timeout(exc):
                # Not retried: a provider that hung for 20 s is not answering this turn,
                # and a second hang would spend the rest of the dealer's wait on it.
                logger.error(
                    "chatbot llm: %s/%s timed out after %.1fs on attempt %s",
                    provider,
                    model,
                    timeout,
                    attempt,
                )
                raise TimedOut(
                    f"no answer from {provider} within {timeout:.0f}s ({exc})"
                ) from exc
            transient = is_transient(exc)
            if not transient and not is_rate_limited(exc):
                raise
            wait = None
            if backoff is not None and not _quota_exhausted(exc):
                wait = min(max(backoff, _server_wait(exc) or 0.0), MAX_WAIT_SECONDS)
                if waited + wait > MAX_TOTAL_WAIT_SECONDS:
                    wait = None
                elif deadline - (_monotonic() + wait) < MIN_ATTEMPT_SECONDS:
                    wait = None
            reason = "unavailable" if transient else "rate limited"
            if wait is None:
                # A provider that stayed unavailable is told to the dealer the same way
                # as a rate limit that never cleared: busy, send that again.
                logger.warning("chatbot llm: %s on attempt %s, giving up", reason, attempt)
                raise RateLimited(str(exc)) from exc
            logger.info("chatbot llm: %s on attempt %s, waiting %.1fs", reason, attempt, wait)
            _sleep(wait)
            waited += wait
    raise AssertionError("unreachable")  # pragma: no cover - the loop always returns or raises
