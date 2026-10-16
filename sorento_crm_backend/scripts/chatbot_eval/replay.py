"""L1 replay: re-run recorded parses through THIS checkout's engine and diff the outcome.

usage (cwd = the backend dir of the checkout under test):
  python scripts/chatbot_eval/replay.py run --traces FILE... --out OUT.jsonl
  python scripts/chatbot_eval/replay.py diff BASE.jsonl HEAD.jsonl [--intended FILE]

Imports only the stdlib at module level and `app.*` inside functions, so one copy of this
file runs under each worktree's own venv. Input rows are recorded `chatbot.turns` rows plus
`case_id`. Output is ids and hashes only: no message or reply text.

The turn runs with `is_test=True` (the envelope's dry-run switch), the recorded parser output as `mock_reformulator_output`,
the recorded memory as `previous_conversation_state`, the MCP tool replaced by the recorded
envelopes, every LLM call blocked, and the database inside a transaction that is rolled back.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import sys
from typing import Any, Callable, Iterator

STAGES = ("grounded", "decision", "tools", "reply")
VOLATILE_KEYS = ("_diagnostics",)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _canon(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def _args_sha(args: Any) -> str:
    return _sha(_canon(args))


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


def _recorded_parse(row: dict[str, Any]) -> dict[str, Any] | None:
    ctx = ((row.get("response") or {}).get("ctx") or {})
    raw = (ctx.get("parse") or {}).get("_parser_raw")
    return raw if isinstance(raw, dict) and raw else None


def _recorded_memory(row: dict[str, Any]) -> dict[str, Any]:
    """The contact memory the recorded turn started from (`received` record), harness shape."""
    from app.services.chatbot import session_state

    for rec in row.get("trace") or []:
        if isinstance(rec, dict) and rec.get("stage") == "received":
            outer = ((rec.get("raw") or {}).get("session_vars") or {})
            sv = outer.get("session_vars") if isinstance(outer.get("session_vars"), dict) else {}
            if any(k in sv for k in session_state.FIVE_KEYS):
                return {k: sv.get(k) for k in session_state.FIVE_KEYS}
            variables = sv.get("variables")
            return variables if isinstance(variables, dict) and variables else {}
    return {}


def _recorded_tools(row: dict[str, Any]) -> dict[tuple[str, str], Any]:
    out: dict[tuple[str, str], Any] = {}
    for rec in row.get("trace") or []:
        if isinstance(rec, dict) and rec.get("kind") == "tool" and rec.get("name"):
            out.setdefault((rec["name"], _args_sha(rec.get("args"))), rec.get("envelope"))
    return out


@contextlib.contextmanager
def _rolled_back(session_factory: Callable[[], Any]) -> Iterator[Callable[[], Any]]:
    """A session factory whose every session shares one outer transaction (inner commits
    become savepoints), rolled back on exit so the database is never written."""
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    probe = session_factory()
    bind = probe.get_bind()
    autoflush = probe.autoflush
    probe.close()
    if isinstance(bind, Engine):
        conn = bind.connect()
        outer = conn.begin()
    else:
        conn = bind
        outer = conn.begin_nested()
    opened: list[Any] = []

    def factory() -> Any:
        s = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=autoflush)
        opened.append(s)
        return s

    try:
        yield factory
    finally:
        for s in opened:
            with contextlib.suppress(Exception):
                s.close()
        with contextlib.suppress(Exception):
            outer.rollback()
        if isinstance(bind, Engine):
            conn.close()


@contextlib.contextmanager
def _patched(recorded: dict[tuple[str, str], Any], misses: list[dict[str, Any]]) -> Iterator[None]:
    from unittest import mock

    from app.services import embedding_worker
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot import llm_call
    from app.services.chatbot.lanes import ideate

    def call_tool(self: Any, name: str, arguments: dict[str, Any]) -> str:
        key = (name, _args_sha(arguments))
        if key in recorded:
            return json.dumps(recorded[key] if recorded[key] is not None else {})
        misses.append({"tool": name, "args_sha": key[1]})
        return json.dumps({"ok": False, "error": "replay_miss", "items": []})

    def blocked(label: str) -> Callable[..., Any]:
        def fail(*a: Any, **k: Any) -> Any:
            misses.append({"llm_blocked": label})
            raise RuntimeError(f"llm_blocked: {label}")

        return fail

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(MCPRuntimeClient, "call_tool", call_tool))
        stack.enter_context(mock.patch.object(llm_call, "chat", blocked("llm_call.chat")))
        stack.enter_context(mock.patch.object(ideate, "call_ideation_tool", blocked("ideate")))
        stack.enter_context(mock.patch.object(embedding_worker, "_embed_text_chunks", blocked("embedding")))
        yield


def _entity_key(ents: list[Any]) -> list[list[Any]]:
    return sorted(
        [e.get("hint"), e.get("canonical_code"), _sha(str(e.get("raw") or "").lower())[:12]]
        for e in ents
        if isinstance(e, dict)
    )


def _entities(trace: list[dict[str, Any]]) -> list[list[Any]]:
    for rec in trace:
        if isinstance(rec, dict) and rec.get("kind") == "apply":
            return _entity_key((rec.get("verdict") or {}).get("entities") or [])
    return []


def _message_first_line(envelope: dict[str, Any]) -> str | None:
    m: Any = envelope
    for _ in range(6):
        if isinstance(m, dict) and isinstance(m.get("text"), str):
            return m["text"].split("\n", 1)[0]
        m = m.get("message") if isinstance(m, dict) else None
    return None


def _ground_one(row: dict[str, Any], parse: dict[str, Any], db: Any, vocab: Any) -> list[list[Any]]:
    """`grounding.ground()` alone, the way the engine calls it (held ranking words go back on after)."""
    from app.services.chatbot.head import grounding
    from app.services.chatbot.lanes.business import report_ask

    verdict, held = report_ask.hold_words(copy.deepcopy(parse))
    grounded, _notes = grounding.ground(db, verdict, vocab=vocab, message=_message_first_line(row.get("envelope") or {}))
    ents = grounded.get("entities") or []
    if held:
        ents = [*ents, *held]
    return _entity_key(ents)


def _decision(trace: list[dict[str, Any]], branch_kind: Any) -> list[Any]:
    lane = None
    plan: dict[str, Any] = {}
    for rec in trace:
        if not isinstance(rec, dict):
            continue
        if rec.get("stage") == "routed":
            lane = (rec.get("facts") or {}).get("lane")
        if rec.get("kind") == "apply":
            plan = rec.get("plan") or {}
    return [branch_kind, lane, plan.get("lane"), sorted(plan.get("fetch") or []), sorted(plan.get("denied") or [])]


def _tools(trace: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [rec["name"], _args_sha(rec.get("args"))]
        for rec in trace
        if isinstance(rec, dict) and rec.get("kind") == "tool" and rec.get("name")
    ]


def _replay_one(row: dict[str, Any], factory: Callable[[], Any], stage: str | None = None,
                vocab_box: list[Any] | None = None) -> dict[str, Any]:
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import engine
    from app.services.chatbot.contracts import Envelope

    out: dict[str, Any] = {"case_id": row.get("case_id"), "turn_id": str(row.get("id")), "skipped": None,
                           "misses": [], "stages": {}}
    parse = _recorded_parse(row)
    if parse is None:
        out["skipped"] = "no_parse"
        return out
    from app.services.chatbot.head import parser as parser_mod

    parse = copy.deepcopy(parse)
    # A recorded parse written before a key joined the schema lacks it; the bypass refuses
    # an incomplete emission, so the missing declared keys are set to None (never invented).
    for key in parser_mod.DECLARED_KEYS - parser_mod.TOLERATED_ABSENT - set(parse):
        parse[key] = None
    if stage == "grounding":
        from app.services.chatbot.head import grounding

        db = factory()
        try:
            if vocab_box is not None and not vocab_box:
                vocab_box.append(grounding.load_vocabulary(db))
            vocab = vocab_box[0] if vocab_box else None
            out["stages"] = {"grounded": _ground_one(row, parse, db, vocab)}
        except Exception as exc:  # noqa: BLE001 - the replay records, never raises
            out["misses"] = [{"error": type(exc).__name__}]
        finally:
            db.rollback()
            db.close()
        return out
    payload = copy.deepcopy(row.get("envelope") or {})
    payload.update(is_test=True, mock_reformulator_output=parse,
                   previous_conversation_state=_recorded_memory(row))
    envelope = Envelope(**payload)
    misses: list[dict[str, Any]] = []
    with _patched(_recorded_tools(row), misses):
        try:
            result = engine.run_turn(envelope, session_factory=factory)
        except Exception as exc:  # noqa: BLE001 - the replay records, never raises
            out["misses"] = misses + [{"error": type(exc).__name__}]
            return out
    db = factory()
    turn = db.query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first()
    trace = list(turn.trace or []) if turn is not None else []
    db.close()
    out["misses"] = misses
    out["stages"] = {
        "grounded": _entities(trace),
        "decision": _decision(trace, result.branch_kind),
        "tools": _tools(trace),
    }
    if not any("tool" in m for m in misses):
        text = (result.reply or {}).get("text") if isinstance(result.reply, dict) else None
        out["stages"]["reply"] = {"sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
                                  "branch_kind": result.branch_kind}
    return out


def replay_rows(rows: list[dict[str, Any]], *, session_factory: Callable[[], Any],
                stage: str | None = None) -> list[dict[str, Any]]:
    """`stage="grounding"` runs only `grounding.ground()` per row (vocabulary loaded once per call)
    and returns `stages.grounded` alone; None runs the whole turn."""
    results = []
    vocab_box: list[Any] = []
    for row in rows:
        with _rolled_back(session_factory) as factory:
            results.append(_replay_one(row, factory, stage, vocab_box))
    return results


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #


def _stage_value(row: dict[str, Any], stage: str) -> Any:
    stages = row.get("stages") or {}
    if stage == "tools":
        return [stages.get("tools"), row.get("misses") or []]
    return stages.get(stage)


def diff_rows(base: list[dict[str, Any]], head: list[dict[str, Any]],
              intended: set[tuple[str, str]] | None = None) -> dict[str, Any]:
    intended = intended or set()
    head_by = {(r["case_id"], r["turn_id"]): r for r in head}
    diffs: list[tuple[str, str, str]] = []
    stage_counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for b in base:
        key = (b["case_id"], b["turn_id"])
        h = head_by.get(key)
        if h is None:
            skipped["unmatched"] = skipped.get("unmatched", 0) + 1
            continue
        kind = b.get("skipped") or h.get("skipped")
        if kind:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        for stage in STAGES:
            if stage == "reply" and ("reply" not in (b.get("stages") or {}) or "reply" not in (h.get("stages") or {})):
                continue
            if _stage_value(b, stage) != _stage_value(h, stage):
                diffs.append((key[0], key[1], stage))
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
                break
    unintended = [d for d in diffs if (d[0], d[2]) not in intended]
    return {"diffs": diffs, "unintended": unintended, "counts": {"stage": stage_counts, "skipped": skipped}}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _read(path: str) -> list[dict[str, Any]]:
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _cmd_run(args: argparse.Namespace) -> int:
    import app

    if not os.path.abspath(app.__file__).startswith(os.getcwd() + os.sep):
        print(f"refused: app is imported from {app.__file__}, not from the cwd", file=sys.stderr)
        return 2
    from app.database import SessionLocal

    rows = [r for path in args.traces for r in _read(path)]
    results = replay_rows(rows, session_factory=SessionLocal, stage=args.stage)
    with open(args.out, "w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    intended: set[tuple[str, str]] = set()
    if args.intended:
        for line in open(args.intended):
            parts = line.split()
            if len(parts) == 2:
                intended.add((parts[0], parts[1]))
    res = diff_rows(_read(args.base), _read(args.head), intended)
    for cid, tid, stage in res["diffs"]:
        print(f"{cid} {tid} {stage}")
    print(f"stages {json.dumps(res['counts']['stage'], sort_keys=True)}")
    print(f"skipped {json.dumps(res['counts']['skipped'], sort_keys=True)}")
    print(f"unintended {len(res['unintended'])}")
    return 1 if res["unintended"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--traces", nargs="+", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--stage", choices=["grounding"], default=None)
    diff = sub.add_parser("diff")
    diff.add_argument("base")
    diff.add_argument("head")
    diff.add_argument("--intended")
    args = ap.parse_args(argv)
    return _cmd_run(args) if args.cmd == "run" else _cmd_diff(args)


if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    sys.exit(main())
