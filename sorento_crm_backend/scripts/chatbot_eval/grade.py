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


def _canon(token: str) -> str:
    token = token.replace(",", "")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token


def _has_number(reply: str, want: object) -> bool:
    want = _canon(str(want))
    for m in NUM_RE.finditer(reply):
        if m.start() > 0 and reply[m.start() - 1].isalnum():
            continue
        if _canon(m.group()) == want:
            return True
    return False


def _has_date(reply: str, iso: str) -> bool:
    y, m, d = iso.split("-")
    forms = {iso, f"{d}/{m}/{y}", f"{int(d)}/{int(m)}/{y}"}
    return any(re.search(rf"(?<![\d/-]){re.escape(f)}(?![\d/-])", reply) for f in forms)


def _must_ok(reply: str, item: dict) -> bool:
    low = reply.lower()
    for key, want in item.items():
        if key == "near":
            continue
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


def _vacuous(kind: str, expect: Expect) -> bool:
    return not expect.options if kind == "clarify" else not expect.must


def _passes(outcome: str, expect: Expect, reply: str) -> str | None:
    if outcome == "clarify":
        low = reply.lower()
        return "good_clarify" if all(o.lower() in low for o in expect.options) else None
    return "correct" if all(_must_ok(reply, m) for m in expect.must) else None


def grade(expect: Expect, outcome: Outcome, previous_bot_reply: str | None) -> Verdict:
    reply = outcome.reply_text or ""
    if outcome.tool_errors:
        return Verdict("error", ["tool_error"])
    raw = outcome.raw or {}
    if expect.outcome == "silent":
        if not reply.strip() or (raw.get("reply") is None and raw.get("delegate")):
            return Verdict("correct", [], bool(expect.judge_note))
        return Verdict("wrong", ["reply_when_silent_expected"])
    if raw.get("reply") is None and raw.get("delegate"):
        return Verdict("error", ["delegated"])
    if not reply.strip():
        return Verdict("error", ["blank_reply"])
    verdict = _rubric(expect, reply)
    if verdict.label == "wrong" and previous_bot_reply is not None and _norm(reply) == _norm(previous_bot_reply):
        return Verdict("stuck", ["same_as_previous_reply"])
    return verdict


def _rubric(expect: Expect, reply: str) -> Verdict:
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
    kinds = [expect.outcome, *expect.acceptable_alternates]
    # An alternate with no criteria would pass anything, so it only counts when no kind has criteria.
    has_criteria = any(not _vacuous(k, expect) for k in kinds)
    for i, kind in enumerate(kinds):
        if i > 0 and has_criteria and _vacuous(kind, expect):
            continue
        label = _passes(kind, expect, reply)
        if label:
            return Verdict(label, [], bool(expect.judge_note) or _vacuous(kind, expect))
    return Verdict("wrong", ["expectation_not_met"])
