"""PV2 fixture replay (CONTRACT-PV2-13oct.md section 7). No model call, ever.

A fixture file is JSONL, one object per TURN, cases in order:

    case, turn, now, state_in, text, index_sha, parser_out, source, expect

`parser_out` is a full ParserV2Output (recorded, converted or handwritten). Replay validates
it against `pv2.schema`, feeds it to `pv2.apply.apply_turn`, chains `state_out` into the next
turn of the same case (`state_in` is read on a case's FIRST turn only; null = a fresh state at
`now`), and asserts `expect`:

- `actions`: action class names in order (e.g. ["Lookup"], ["ClarifyAct"]).
- `held`: a SUBSET of `state_out.held` (JSON mode); only the keys given are compared.
- `options`: the labels of `state_out.pending.options`, in order ([] = no pending).
- `reply_must` / `reply_must_not`: substrings of the reply. `" · "` and anything shaped like
  a UUID are always forbidden. Reply checks need `pv2.answer.replay_reply(state, actions)`
  (CARRY); until it exists they are reported as skipped, never silently passed.

Optional per-turn `resolve`: canned resolver answers, keyed `"<kind>:<key or phrase>"`
(entities), `"attachment_type:<key>"`, `"spec:<attribute>:<number or value_key>"`; each value
is a `Resolution` / `SpecResolution` dict. A key not listed resolves EXACT to itself (label =
the key), which is what a handwritten fixture almost always means; a fixture that needs an
ambiguous or missing result says so explicitly.
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app.services.chatbot.pv2.schema import Entity, ParserV2Output, Spec

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_KEYS = ("case", "turn", "now", "state_in", "text", "index_sha", "parser_out", "source", "expect")
ALWAYS_FORBIDDEN = (" · ",)
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class FixtureError(AssertionError):
    pass


# --------------------------------------------------------------------------- loading


def load_turns(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name}:{n}: not JSON: {exc}") from exc
        missing = [k for k in FIXTURE_KEYS if k not in row]
        if missing:
            raise FixtureError(f"{path.name}:{n}: missing {missing}")
        row["_where"] = f"{path.name}:{n}"
        rows.append(row)
    return rows


def group_cases(rows: Iterable[dict]) -> dict[str, list[dict]]:
    cases: dict[str, list[dict]] = {}
    for row in rows:
        cases.setdefault(row["case"], []).append(row)
    for name, turns in cases.items():
        nums = [t["turn"] for t in turns]
        if nums != sorted(nums) or len(set(nums)) != len(nums):
            raise FixtureError(f"case {name}: turns out of order {nums}")
    return cases


def fixture_files(prefix: str | None = None) -> list[Path]:
    files = sorted(FIXTURE_DIR.glob("*.jsonl"))
    return [f for f in files if prefix is None or f.name.startswith(prefix)]


def all_cases(prefix: str | None = None) -> list[tuple[str, list[dict]]]:
    out = []
    for path in fixture_files(prefix):
        for name, turns in group_cases(load_turns(path)).items():
            out.append((f"{path.stem}::{name}", turns))
    return out


# --------------------------------------------------------------------------- CARRY seams


def _carry(module: str):
    try:
        return importlib.import_module(f"app.services.chatbot.pv2.{module}")
    except ModuleNotFoundError as exc:
        if exc.name == f"app.services.chatbot.pv2.{module}":
            return None
        raise


def carry_ready() -> bool:
    return _carry("apply") is not None and _carry("state") is not None


def fresh_state(now: datetime):
    state_mod = _carry("state")
    return state_mod.ConversationState.model_validate(
        {
            "v": 1,
            "session_started_at": now,
            "last_turn_at": now,
            "turn_no": 0,
            "held": {
                "domain": None, "refs": {}, "specs": [], "attachment_type": None,
                "period": None, "report": None, "idea_draft_id": None,
            },
            "pending": None,
            "topics": [],
            "last_rows": [],
        }
    )


# --------------------------------------------------------------------------- canned resolver


@dataclass
class CannedResolver:
    """The `Resolver` protocol over a fixture turn's `resolve` map (see module doc)."""

    canned: dict[str, dict] = field(default_factory=dict)

    def _resolution(self, key: str, kind: str, value: str | None, phrase: str | None):
        state_mod = _carry("state")
        resolve_mod = _carry("resolve")
        Resolution = getattr(resolve_mod, "Resolution", None) if resolve_mod else None
        Resolution = Resolution or getattr(state_mod, "Resolution")
        if key in self.canned:
            return Resolution.model_validate(self.canned[key])
        if value is None:
            return Resolution(status="none", refs=[], options=[], phrase=phrase, unrecognised=True)
        ref = state_mod.ResolvedRef(kind=kind, key=value, label=value, phrase=phrase)
        return Resolution(status="exact", refs=[ref], options=[], phrase=phrase, unrecognised=False)

    def resolve_entities(self, ents: list[Entity], *, held) -> list:
        out = []
        for e in ents:
            value = e.key or e.phrase
            out.append(self._resolution(f"{e.kind}:{value}", e.kind, value, e.phrase))
        return out

    def resolve_specs(self, specs: list[Spec], *, held) -> list:
        state_mod = _carry("state")
        resolve_mod = _carry("resolve")
        SpecResolution = getattr(resolve_mod, "SpecResolution", None) if resolve_mod else None
        SpecResolution = SpecResolution or getattr(state_mod, "SpecResolution")
        out = []
        for s in specs:
            value = s.value_key if s.value_key is not None else s.number
            key = f"spec:{s.attribute}:{value}"
            if key in self.canned:
                out.append(SpecResolution.model_validate(self.canned[key]))
                continue
            if s.attribute is None or value is None:
                out.append(SpecResolution.model_validate(
                    {"status": "none", "specs": [], "options": [], "phrase": s.phrase, "unrecognised": True}
                ))
                continue
            out.append(SpecResolution.model_validate({
                "status": "exact",
                "specs": [{
                    "attribute": s.attribute, "comparator": s.comparator, "value": value,
                    "value_to": s.number_to, "unit": s.unit,
                }],
                "options": [], "phrase": s.phrase, "unrecognised": False,
            }))
        return out

    def resolve_attachment_type(self, key: str | None, *, held):
        return self._resolution(f"attachment_type:{key}", "attachment_type", key, None)

    def resolve_report_spec(self, spec, *, held, profile=None):
        key = "report"
        if key in self.canned:
            resolve_mod = _carry("reports") or _carry("resolve")
            return resolve_mod.ReportResolution.model_validate(self.canned[key])
        return None


# --------------------------------------------------------------------------- run


@dataclass
class TurnResult:
    where: str
    state_out: Any
    actions: list
    reply: str | None
    failures: list[str]
    skipped: list[str]


def _call_apply(apply_turn, state, out, resolver, now):
    apply_mod = _carry("apply")
    policy = getattr(apply_mod, "DEFAULT_POLICY", None)
    if policy is None and hasattr(apply_mod, "ClarifyPolicy"):
        policy = apply_mod.ClarifyPolicy()
    kwargs: dict[str, Any] = {"resolver": resolver, "policy": policy}
    if "now" in inspect.signature(apply_turn).parameters:
        kwargs["now"] = now
    return apply_turn(state, out, **kwargs)


def _subset_mismatch(expected: Any, actual: Any, path: str = "held") -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {actual!r}"]
        out = []
        for k, v in expected.items():
            out += _subset_mismatch(v, actual.get(k), f"{path}.{k}")
        return out
    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return []


def check_reply(reply: str, expect: dict) -> list[str]:
    fails = []
    for s in expect.get("reply_must", []):
        if s not in reply:
            fails.append(f"reply missing {s!r}")
    for s in list(expect.get("reply_must_not", [])) + list(ALWAYS_FORBIDDEN):
        if s in reply:
            fails.append(f"reply contains forbidden {s!r}")
    if UUID_RE.search(reply):
        fails.append("reply leaks a UUID")
    return fails


def run_case(turns: list[dict]) -> list[TurnResult]:
    apply_mod = _carry("apply")
    state_mod = _carry("state")
    answer_mod = _carry("answer")
    replay_reply = getattr(answer_mod, "replay_reply", None) if answer_mod else None
    results: list[TurnResult] = []
    state = None
    for i, turn in enumerate(turns):
        now = datetime.fromisoformat(turn["now"])
        try:
            out = ParserV2Output.model_validate(turn["parser_out"])
        except Exception as exc:  # noqa: BLE001
            raise FixtureError(f"{turn['_where']}: parser_out is not a ParserV2Output: {exc}") from exc
        if i == 0:
            state = (
                state_mod.ConversationState.model_validate(turn["state_in"])
                if turn["state_in"] is not None
                else fresh_state(now)
            )
        resolver = CannedResolver(turn.get("resolve") or {})
        state_out, actions = _call_apply(apply_mod.apply_turn, state, out, resolver, now)
        expect = turn["expect"] or {}
        failures: list[str] = []
        skipped: list[str] = []
        if "actions" in expect:
            got = [type(a).__name__ for a in actions]
            if got != expect["actions"]:
                failures.append(f"actions: expected {expect['actions']}, got {got}")
        if "held" in expect:
            failures += _subset_mismatch(expect["held"], state_out.held.model_dump(mode="json"))
        if "options" in expect:
            got_labels = [o.label for o in (state_out.pending.options if state_out.pending else [])]
            if got_labels != expect["options"]:
                failures.append(f"options: expected {expect['options']}, got {got_labels}")
        reply = None
        if expect.get("reply_must") or expect.get("reply_must_not"):
            if replay_reply is None:
                skipped.append("reply checks: pv2.answer.replay_reply not available yet")
            else:
                reply = replay_reply(state_out, actions)
                failures += check_reply(reply, expect)
        results.append(TurnResult(turn["_where"], state_out, actions, reply, failures, skipped))
        state = state_out
    return results
