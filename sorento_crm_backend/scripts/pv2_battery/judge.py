"""Opus judge for one battery turn (UAC-5). Runs only when the deterministic checks pass."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

MODEL = "claude-opus-5-5"
API_URL = "https://api.anthropic.com/v1/messages"

CLASSES = ("wrong_subject", "lost_ask", "should_clarify", "needless_clarify", "invented_data", "wrong_language",
           "leak", "false_gap", "permission", "handover_wrong", "stuck", "tone", "other")

RUBRIC = """You grade ONE reply of a WhatsApp sales-support chatbot for a Malaysian trading company.
You see the conversation so far, the user's message, the bot's reply, the expectation for this turn and the facts
the bot looked up. Judge the reply against the expectation and these owner rules.

1. Clarify, never assume. When the ask is ambiguous (several products, several documents, unclear subject) the
   right reply asks ONE short question. Guessing a subject is wrong_subject or should_clarify. Asking when the
   context already answers it is needless_clarify.
2. Resolve then reuse. A short follow-up ("the second one", "how many?", "this product", a bare number, "yes")
   refers to what the conversation was just about. Dropping that subject is lost_ask or wrong_subject.
3. The bot states only data it holds. Any figure, date, status or product detail that is not in the facts is
   invented_data. Data shown in an EARLIER bot reply of this conversation counts as held (the facts cover only
   the current turn), so repeating or narrowing it is not invented_data.
   Greeting the customer by their own WhatsApp contact name is fine and is not invented_data. Saying it does not know or handing over is fine when the facts hold nothing.
4. No internal detail leaks: no UUID, no internal key or snake_case identifier, no template braces, no bare
   None / null. Product codes, document numbers and company names are normal and are not leaks.
5. False gap: never say a product has no photos or files when files were sent in this turn (file count above).
6. Reply in the language the customer used (English, Malay, Chinese, or the mix); a different language is
   wrong_language.
7. Permission: a customer without the grant gets a polite refusal or hand-over, never the restricted data.
   Giving it is permission. Refusing what the customer is allowed is also wrong.
8. Hand-over is only for what the bot cannot answer or what the expectation says; a needless or missing
   hand-over is handover_wrong.
9. Repeating the previous reply to a new message is stuck. Tone: short, polite, plain; no scolding, no padding.

The expectation `kind` is answer, ask, handover, refuse or any; `about` says what must be answered or asked.
The reply only needs to satisfy the expectation; extra correct helpfulness is fine.

Answer with ONE JSON object and nothing else:
{"verdict": "pass" | "fail", "classes": [<zero or more of the classes>], "reason": "<one short sentence>"}
Classes: """ + ", ".join(CLASSES) + ". A pass has an empty classes list."

FACTS_LIMIT = 12000


def _files_text(files: Any) -> str:
    if not isinstance(files, list):
        return str(files or 0)
    if not files:
        return "0"
    items = [f"{f.get('filename') or f.get('name') or f.get('url')} ({f.get('attachmentType') or 'no type'})"
             if isinstance(f, dict) else str(f) for f in files]
    return f"{len(files)}: " + "; ".join(items)


def build_judge_prompt(*, history: list[dict[str, str]], text: str, reply: str, files: Any,
                       expect: dict[str, Any], facts: Any, audience: str) -> str:
    convo = "\n".join(f"User: {h.get('user')}\nBot: {h.get('bot')}" for h in history) or "(first message)"
    facts_text = facts if isinstance(facts, str) else json.dumps(facts, ensure_ascii=False, default=str)
    if len(facts_text) > FACTS_LIMIT:
        facts_text = facts_text[:FACTS_LIMIT] + "...(truncated)"
    return (
        f"{RUBRIC}\n\n"
        f"AUDIENCE: {audience}\n\n"
        f"CONVERSATION SO FAR:\n{convo}\n\n"
        f"USER MESSAGE: {text}\n\n"
        f"BOT REPLY: {reply}\n"
        f"FILES SENT THIS TURN: {_files_text(files)}\n\n"
        f"EXPECTATION: {json.dumps(expect, ensure_ascii=False)}\n\n"
        f"FACTS THE BOT HAD: {facts_text}\n"
    )


def _error(reason: str) -> dict[str, Any]:
    return {"verdict": "judge_error", "classes": [], "reason": reason}


def parse_verdict(raw: str) -> dict[str, Any]:
    """Never returns a pass for anything it cannot read."""
    body = (raw or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", body, re.DOTALL)
    if fence:
        body = fence.group(1)
    data: Any = None
    try:
        data = json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(body[start:end + 1])
            except ValueError:
                data = None
    if not isinstance(data, dict) or data.get("verdict") not in ("pass", "fail"):
        return _error(f"unparseable judge output: {(raw or '')[:120]!r}")
    classes = data.get("classes") if isinstance(data.get("classes"), list) else []
    classes = [c if c in CLASSES else "other" for c in classes]
    return {"verdict": data["verdict"], "classes": classes, "reason": str(data.get("reason") or "")}


def judge_turn(client: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return parse_verdict(client.complete(build_judge_prompt(**kwargs)))
    except Exception as exc:  # noqa: BLE001 - a judge fault is a judge_error, never a pass
        return _error(f"judge call failed: {type(exc).__name__}: {exc}")


def agreement(labels: list[str], verdicts: list[str]) -> float:
    if not labels:
        return 0.0
    return sum(1 for a, b in zip(labels, verdicts) if a == b) / len(labels)


def load_api_key(key_file: str | None = None) -> str:
    if key_file:
        text = Path(key_file).read_text(encoding="utf-8").strip()
        m = re.search(r"ANTHROPIC_API_KEY\s*=\s*(\S+)", text)
        return (m.group(1) if m else text).strip("\"'")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no judge key: set ANTHROPIC_API_KEY or pass --judge-key-file")
    return key


class AnthropicClient:
    """`.complete(prompt) -> str` over the Messages API, with backoff on 429 and 5xx."""

    def __init__(self, api_key: str, model: str = MODEL, timeout: float = 90, retries: int = 5):
        self.api_key, self.model, self.timeout, self.retries = api_key, model, timeout, retries

    def complete(self, prompt: str) -> str:
        delay = 2.0
        for attempt in range(self.retries + 1):
            resp = httpx.post(
                API_URL,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json={"model": self.model, "max_tokens": 2000,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=self.timeout,
            )
            if resp.status_code in (429, 500, 502, 503, 504, 529) and attempt < self.retries:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            resp.raise_for_status()
            blocks = resp.json().get("content") or []
            text = "".join(str(b.get("text") or "") for b in blocks
                           if isinstance(b, dict) and b.get("type") == "text")
            if not text.strip():
                raise RuntimeError("judge response held no text block")
            return text
        raise RuntimeError("unreachable")
