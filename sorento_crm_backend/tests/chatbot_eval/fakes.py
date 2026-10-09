"""Fake engine + fake case builders. All data is made up."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.chatbot_eval.engine import Outcome


def make_outcome(reply="ok", *, dry_run=True, tool_errors=None, tokens=None, state=None, latency_ms=10, branch_kind=None):
    return Outcome(
        reply_text=reply,
        branch_kind=branch_kind,
        dry_run=dry_run,
        tool_errors=list(tool_errors or []),
        tokens=tokens or {"prompt": 0, "cached": 0, "output": 0},
        latency_ms=latency_ms,
        state=state,
        raw={},
    )


class RecordingEngine:
    """Replies from a script keyed by the text sent; records every call."""

    def __init__(self, replies=None, default="Stock of TESTCODE-1 is 1,630 units", dry_run=True, tokens=None, **kw):
        self.replies = replies or {}
        self.default = default
        self.dry_run = dry_run
        self.tokens = tokens
        self.calls = []  # (text, state_in, run_id)
        self.n = 0

    def turn(self, case, text, state, run_id):
        self.calls.append((text, state, run_id))
        self.n += 1
        return make_outcome(
            self.replies.get(text, self.default),
            dry_run=self.dry_run,
            tokens=self.tokens,
            state={"n": self.n},
        )


class FakeEngine(RecordingEngine):
    """Importable by `--engine tests.chatbot_eval.fakes:FakeEngine`."""


def case_dict(cid="c1", split="dev", topic="stock", language="en", text="how many TESTCODE-1?", outcome="answer", **over):
    d = {
        "id": cid,
        "split": split,
        "topic_code": topic,
        "language": language,
        "contact": {"respond_id": "9001", "custom_fields": [{"name": "is_allowed_stock", "value": "true"}]},
        "history": [],
        "message": {"text": text},
        "variants": [],
        "expect": {"outcome": outcome, "must": [{"code": "TESTCODE-1"}]},
    }
    d.update(over)
    return d


def write_case(directory: Path, d: dict, name=None):
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / (name or f"{d.get('id', 'x')}.json")
    p.write_text(json.dumps(d))
    return p


class BudgetBurner(RecordingEngine):
    """Burns 1M prompt tokens per turn so any small --budget-usd trips."""

    def __init__(self, **kw):
        super().__init__(tokens={"prompt": 1_000_000, "cached": 0, "output": 0})
