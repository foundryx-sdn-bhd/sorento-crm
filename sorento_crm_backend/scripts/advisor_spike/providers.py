"""One interface over the two advisor models (and the judge).

complete(messages, tools, system) -> {text, tool_calls, usage, latency_ms, cost_usd, model}

Neutral message shapes (the advisor loop builds these; each provider converts):
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "results": [{"id", "name", "content": str}]}
tools: [{"name", "description", "input_schema"}]. usage: {input, cache_read, cache_write, output} (input = uncached).

Keys: anthropic from /Users/tehjayson/Documents/foundryx/crew/state/secrets/anthropic-eval.key (the dev key);
openai from OPENAI_API_KEY in the env file loaded by env.bootstrap(). Whether that OpenAI key is the dev or the
prod key cannot be detected here; the pv2 runner's convention is that the dev key ends in "bG4A", so a different
suffix only prints a warning. The spend ledger and --budget-usd in run.py are the real guard.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

ANTHROPIC_KEY_FILE = Path("/Users/tehjayson/Documents/foundryx/crew/state/secrets/anthropic-eval.key")
DEV_OPENAI_SUFFIX = "bG4A"

MODEL_IDS = {"sonnet": "claude-sonnet-5-5", "mini": "gpt-5.4-mini", "opus": "claude-opus-5-5"}
#: USD per million tokens. cache_write: 1.25x input for anthropic; openai has no write surcharge.
PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {"in": 2.0, "cache_read": 0.20, "cache_write": 2.5, "out": 10.0},
    "gpt-5.4-mini": {"in": 0.75, "cache_read": 0.075, "cache_write": 0.75, "out": 4.5},
    "claude-opus-5-5": {"in": 4.0, "cache_read": 0.20, "cache_write": 5.0, "out": 20.0},
}


def cost_usd(model: str, usage: dict[str, int]) -> float:
    p = PRICES[model]
    return (usage.get("input", 0) * p["in"] + usage.get("cache_read", 0) * p["cache_read"]
            + usage.get("cache_write", 0) * p["cache_write"] + usage.get("output", 0) * p["out"]) / 1e6


def _retry(fn: Any, tries: int = 4) -> Any:
    delay = 2.0
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if i == tries - 1 or status not in (408, 409, 429, 500, 502, 503, 529, None):
                raise
            if status is None and "timeout" not in type(exc).__name__.lower() and "connection" not in type(exc).__name__.lower():
                raise
            time.sleep(delay)
            delay *= 2


class AnthropicProvider:
    def __init__(self, model: str = "claude-sonnet-5-5", effort: str = "low", max_tokens: int = 1500,
                 key_file: Path = ANTHROPIC_KEY_FILE) -> None:
        import anthropic

        text = Path(key_file).read_text(encoding="utf-8").strip()
        key = text.split("=", 1)[1].strip().strip("\"'") if "ANTHROPIC_API_KEY" in text else text
        self.model, self.effort, self.max_tokens = model, effort, max_tokens
        self.client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=120)

    @staticmethod
    def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("text"):
                    blocks.append({"type": "text", "text": m["text"]})
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]} for r in m["results"]]})
        return out

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": self.model, "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": self._messages(messages),
            "output_config": {"effort": self.effort},
        }
        if tools:
            ts = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]
            ts[-1]["cache_control"] = {"type": "ephemeral"}
            kw["tools"] = ts
            kw["tool_choice"] = {"type": "auto"}
        t0 = time.monotonic()
        resp = _retry(lambda: self.client.messages.create(**kw))
        latency = int((time.monotonic() - t0) * 1000)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": dict(b.input)} for b in resp.content if b.type == "tool_use"]
        u = resp.usage
        usage = {"input": u.input_tokens or 0, "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0, "output": u.output_tokens or 0}
        return {"text": text, "tool_calls": calls, "usage": usage, "latency_ms": latency,
                "cost_usd": cost_usd(self.model, usage), "model": self.model, "stop_reason": resp.stop_reason}

    def complete_json(self, prompt: str, schema: dict[str, Any], system: str = "", max_tokens: int = 2000) -> dict[str, Any]:
        """Structured output (judge): returns {data, usage, latency_ms, cost_usd, model}."""
        kw: dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        }
        if system:
            kw["system"] = system
        t0 = time.monotonic()
        resp = _retry(lambda: self.client.messages.create(**kw))
        latency = int((time.monotonic() - t0) * 1000)
        text = "".join(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        usage = {"input": u.input_tokens or 0, "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0, "output": u.output_tokens or 0}
        return {"data": json.loads(text), "usage": usage, "latency_ms": latency,
                "cost_usd": cost_usd(self.model, usage), "model": self.model}


class OpenAIProvider:
    def __init__(self, model: str = "gpt-5.4-mini", max_tokens: int = 1500) -> None:
        import openai

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set (env.bootstrap() loads it from the env file)")
        if not key.endswith(DEV_OPENAI_SUFFIX):
            print(f"WARNING: OPENAI_API_KEY does not end in {DEV_OPENAI_SUFFIX!r}; it may not be the dev key")
        self.model, self.max_tokens = model, max_tokens
        self.client = openai.OpenAI(api_key=key, max_retries=0, timeout=120)

    @staticmethod
    def _messages(messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                          "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                                         for tc in m["tool_calls"]]
                out.append(msg)
            else:
                for r in m["results"]:
                    out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        return out

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> dict[str, Any]:
        kw: dict[str, Any] = {"model": self.model, "max_completion_tokens": self.max_tokens,
                              "messages": self._messages(messages, system)}
        if tools:
            kw["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                             "parameters": t["input_schema"]}} for t in tools]
            kw["tool_choice"] = "auto"
        t0 = time.monotonic()
        resp = _retry(lambda: self.client.chat.completions.create(**kw))
        latency = int((time.monotonic() - t0) * 1000)
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            calls.append({"id": tc.id, "name": tc.function.name, "args": args if isinstance(args, dict) else {}})
        u = resp.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        usage = {"input": max((u.prompt_tokens or 0) - cached, 0), "cache_read": cached, "cache_write": 0,
                 "output": u.completion_tokens or 0}
        return {"text": msg.content or "", "tool_calls": calls, "usage": usage, "latency_ms": latency,
                "cost_usd": cost_usd(self.model, usage), "model": self.model,
                "stop_reason": resp.choices[0].finish_reason}


class FakeProvider:
    """Scripted provider for smoke runs: each complete() pops the next scripted turn
    {"text", "tool_calls": [{"name", "args"}]}; usage is a fixed small figure at the given model's price."""

    def __init__(self, script: list[dict[str, Any]], model: str = "claude-sonnet-5-5") -> None:
        self.script, self.model, self.calls = list(script), model, []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> dict[str, Any]:
        self.calls.append({"messages": json.loads(json.dumps(messages, default=str)), "tools": [t["name"] for t in tools]})
        step = self.script.pop(0) if self.script else {"text": "(script exhausted)"}
        calls = [{"id": f"fake_{len(self.calls)}_{i}", "name": c["name"], "args": c.get("args", {})}
                 for i, c in enumerate(step.get("tool_calls") or [])]
        usage = {"input": 1000, "cache_read": 0, "cache_write": 0, "output": 100}
        return {"text": step.get("text", ""), "tool_calls": calls, "usage": usage, "latency_ms": 1,
                "cost_usd": cost_usd(self.model, usage), "model": self.model, "stop_reason": "fake"}


def make_provider(name: str) -> Any:
    if name == "sonnet":
        return AnthropicProvider(MODEL_IDS["sonnet"])
    if name == "mini":
        return OpenAIProvider(MODEL_IDS["mini"])
    if name == "opus":
        return AnthropicProvider(MODEL_IDS["opus"])
    raise ValueError(name)
