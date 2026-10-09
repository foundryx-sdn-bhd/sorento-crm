"""Optional LLM judge. Scores are ignored unless calibration agreement reaches CALIBRATION_MIN."""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import httpx

from .grade import Verdict

CALIBRATION_MIN = 0.9

_PROMPT = (
    "You grade a customer-service chatbot reply. Criterion: {note}\n"
    "Customer message: {question}\nBot reply: {reply}\n"
    'Answer with JSON only: {{"passed": true|false, "note": "<one short sentence>"}}'
)


@dataclass
class JudgeResult:
    passed: bool
    note: str = ""
    tokens: dict = field(default_factory=lambda: {"prompt": 0, "cached": 0, "output": 0})


class Judge(Protocol):
    def score(self, case: Any, outcome: Any) -> JudgeResult: ...


def calibrate(judge: Judge, items: list[tuple[Any, Any, bool]]) -> float:
    if not items:
        return 0.0
    agree = sum(1 for case, outcome, human in items if judge.score(case, outcome).passed == human)
    return agree / len(items)


def apply_judge(verdict: Verdict, result: JudgeResult, calibrated: bool) -> Verdict:
    if not calibrated:
        return replace(verdict, reasons=[*verdict.reasons, "judge_uncalibrated"])
    if not result.passed:
        return Verdict("wrong", [*verdict.reasons, f"judge:{result.note}"], False)
    return replace(verdict, needs_judge=False)


def _parse(text: str, tokens: dict | None = None) -> JudgeResult:
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1])
        res = JudgeResult(passed=bool(data.get("passed")), note=str(data.get("note", "")))
    except ValueError:
        res = JudgeResult(passed=False, note="unparseable judge output")
    if tokens:
        res.tokens = tokens
    return res


def _prompt(case: Any, outcome: Any) -> str:
    return _PROMPT.format(note=case.expect.judge_note, question=case.message.get("text", ""), reply=outcome.reply_text)


class OpenAIJudge:
    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float = 60):
        self.model = model or os.environ.get("EVAL_JUDGE_MODEL", "gpt-5.4-mini")
        self.api_key = api_key or os.environ["OPENAI_API_KEY"]
        self.timeout = timeout

    def score(self, case: Any, outcome: Any) -> JudgeResult:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": [{"role": "user", "content": _prompt(case, outcome)}]},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        use = body.get("usage") or {}
        cached = (use.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        tokens = {"prompt": use.get("prompt_tokens", 0), "cached": cached, "output": use.get("completion_tokens", 0)}
        return _parse(body["choices"][0]["message"]["content"], tokens)


class AnthropicJudge:
    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float = 60):
        self.model = model or os.environ["EVAL_JUDGE_MODEL"]
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.timeout = timeout

    def score(self, case: Any, outcome: Any) -> JudgeResult:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": self.model,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": _prompt(case, outcome)}],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        use = body.get("usage") or {}
        tokens = {"prompt": use.get("input_tokens", 0), "cached": 0, "output": use.get("output_tokens", 0)}
        return _parse(body["content"][0]["text"], tokens)


def load_judge(name: str) -> Judge | None:
    if name == "openai":
        return OpenAIJudge()
    if name == "anthropic":
        return AnthropicJudge()
    if ":" in name:
        module, _, cls = name.partition(":")
        return getattr(importlib.import_module(module), cls)()
    return None
