"""Deterministic grader. No LLM here."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cases import Expect
from .engine import Outcome

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class Verdict:
    label: str
    reasons: list[str] = field(default_factory=list)
    needs_judge: bool = False


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _has_number(reply: str, want: str) -> bool:
    want = want.replace(",", "")
    return any(t.replace(",", "") == want for t in NUM_RE.findall(reply))


def _has_date(reply: str, iso: str) -> bool:
    y, m, d = iso.split("-")
    forms = {iso, f"{d}/{m}/{y}", f"{int(d)}/{int(m)}/{y}"}
    return any(re.search(rf"(?<![\d/-]){re.escape(f)}(?![\d/-])", reply) for f in forms)


def _must_ok(reply: str, item: dict) -> bool:
    low = reply.lower()
    for key, want in item.items():
        want = str(want)
        if key in ("code", "text"):
            ok = want.lower() in low
        elif key == "number":
            ok = _has_number(reply, want)
        elif key == "date":
            ok = _has_date(reply, want)
        else:
            ok = False
        if not ok:
            return False
    return True


def _passes(outcome: str, expect: Expect, reply: str) -> str | None:
    if outcome == "clarify":
        low = reply.lower()
        return "good_clarify" if all(o.lower() in low for o in expect.options) else None
    return "correct" if all(_must_ok(reply, m) for m in expect.must) else None


def grade(expect: Expect, outcome: Outcome, previous_bot_reply: str | None) -> Verdict:
    reply = outcome.reply_text or ""
    if outcome.tool_errors:
        return Verdict("error", ["tool_error"])
    if not reply.strip():
        return Verdict("error", ["blank_reply"])
    if previous_bot_reply is not None and _norm(reply) == _norm(previous_bot_reply):
        return Verdict("stuck", ["same_as_previous_reply"])
    low = reply.lower()
    for rule in expect.must_not:
        if rule == "uuid":
            hit = bool(UUID_RE.search(reply))
        elif rule.startswith("stale_subject:"):
            hit = rule.split(":", 1)[1].lower() in low
        else:
            hit = rule.lower() in low
        if hit:
            return Verdict("wrong", [f"must_not:{rule}"])
    for kind in [expect.outcome, *expect.acceptable_alternates]:
        label = _passes(kind, expect, reply)
        if label:
            return Verdict(label, [], bool(expect.judge_note))
    return Verdict("wrong", ["expectation_not_met"])
