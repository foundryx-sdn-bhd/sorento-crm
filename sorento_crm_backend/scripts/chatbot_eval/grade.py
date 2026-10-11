"""Deterministic grader. No LLM here."""

from __future__ import annotations

import json
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


LAYERS = ("understood", "resolved", "tool", "reply")


def _eq(want: object, got: object) -> bool:
    if isinstance(want, str) and isinstance(got, str):
        return want.lower() == got.lower()
    return want == got


def _contains_all(wanted: list, blob: object) -> bool:
    text = json.dumps(blob, default=str).lower()
    return all(str(w).lower() in text for w in wanted)


def score_layers(expect: Expect, digest: dict | None, verdict: Verdict) -> dict:
    """pass | fail | na (label has no field) | no_trace (label has it, digest lacks the stage)."""
    digest = digest or {}
    layers = {"understood": "na", "resolved": "na", "tool": "na"}
    if expect.understood:
        parser = digest.get("parser") or {}
        if not parser:
            layers["understood"] = "no_trace"
        else:
            want = expect.understood
            ok = all(_eq(want[k], parser.get(k)) for k in ("domain", "intent") if k in want)
            ok = ok and _contains_all(want.get("entities") or [], parser.get("entities"))
            layers["understood"] = "pass" if ok else "fail"
    if expect.expected_resolved:
        resolved = digest.get("resolved") or []
        if not resolved:
            layers["resolved"] = "no_trace"
        else:
            ok = _contains_all(expect.expected_resolved, [r.get("entities") for r in resolved])
            layers["resolved"] = "pass" if ok else "fail"
    if expect.tool:
        tools = digest.get("tools") or []
        if not tools:
            layers["tool"] = "no_trace"
        else:
            name, params = expect.tool.get("name"), expect.tool.get("params") or {}

            def match(t: dict) -> bool:
                args = t.get("args") or {}
                return (name is None or _eq(name, t.get("tool"))) and all(
                    k in args and _eq(v, args[k]) for k, v in params.items()
                )

            layers["tool"] = "pass" if any(match(t) for t in tools) else "fail"
    layers["reply"] = "pass" if verdict.label in ("correct", "good_clarify") else "fail"
    return {k: layers[k] for k in LAYERS}
