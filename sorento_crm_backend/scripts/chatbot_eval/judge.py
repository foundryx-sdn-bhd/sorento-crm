"""Optional LLM judge. Scores are ignored unless calibration agreement reaches CALIBRATION_MIN."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
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
    return verdict


def _parse(text: str) -> JudgeResult:
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1])
        return JudgeResult(passed=bool(data.get("passed")), note=str(data.get("note", "")))
    except ValueError:
        return JudgeResult(passed=False, note="unparseable judge output")


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
        return _parse(resp.json()["choices"][0]["message"]["content"])


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
        return _parse(resp.json()["content"][0]["text"])


def load_judge(name: str) -> Judge | None:
    if name == "openai":
        return OpenAIJudge()
    if name == "anthropic":
        return AnthropicJudge()
    return None
