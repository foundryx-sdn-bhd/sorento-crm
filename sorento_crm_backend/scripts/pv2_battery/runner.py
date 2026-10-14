"""Run the battery through the real console turn in this process (UAC-6, UAC-7, UAC-8)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import checks, guard, judge, report, schema

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT_ROOT = Path("/Users/tehjayson/Documents/foundryx/crew/state/eval/pv2-battery")
DEFAULT_V1_VERSION = "f3dc8c35-4fc8-41b1-89eb-a6ef14e71cea"
V1_KEY = "chatbot_semantic_parser"
V2_KEY = "chatbot_semantic_parser_v2"
CALIBRATION_FILE = "calibration-latest.json"

_write_lock = threading.Lock()


def bootstrap_env() -> None:
    """The backend .env into os.environ with override, as `app.main._load_env_file` does."""
    from dotenv import load_dotenv

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    load_dotenv(BACKEND_DIR / ".env", override=True)


def ensure_worktree_mcp() -> Path:
    """Import THIS worktree's sorento_crm_mcp: the shared venv's editable install points at the primary
    checkout. Returns the resolved presenters file; GuardError if it still lands elsewhere."""
    import importlib

    root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if sys.path[:1] != [str(root)]:
        if str(root) in sys.path:
            sys.path.remove(str(root))
        sys.path.insert(0, str(root))
    for name in [n for n in sys.modules if n == "sorento_crm_mcp" or n.startswith("sorento_crm_mcp.")]:
        file = getattr(sys.modules[name], "__file__", None)
        if not file or root not in Path(file).resolve().parents:
            del sys.modules[name]
    importlib.invalidate_caches()
    mod = importlib.import_module("sorento_crm_mcp.presenters")
    path = Path(mod.__file__).resolve()
    if root not in path.parents:
        raise guard.GuardError(f"sorento_crm_mcp resolves to {path}, outside the worktree {root}")
    return path


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=BACKEND_DIR,
                                       text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def resolve_pin(db: Any, version_id: str) -> dict[str, Any]:
    from app.models.ai_prompt import AIPromptVersion

    row = db.query(AIPromptVersion).filter(AIPromptVersion.id == version_id).first()
    if row is None:
        raise RuntimeError(f"prompt version {version_id} not found")
    return {"id": str(row.id), "number": int(row.version), "name": row.name,
            "md5": hashlib.md5((row.template or "").encode("utf-8")).hexdigest()}


def resolve_pins(v2_override: str | None, v1_override: str | None) -> dict[str, dict[str, Any]]:
    from app.database import SessionLocal
    from app.models.ai_prompt import AIPromptLabel

    db = SessionLocal()
    try:
        v2_id = v2_override
        if not v2_id:
            label = (db.query(AIPromptLabel)
                     .filter(AIPromptLabel.name == V2_KEY, AIPromptLabel.label == "staging").first())
            if label is None:
                raise RuntimeError("no staging label for chatbot_semantic_parser_v2; pass --v2-version")
            v2_id = str(label.version_id)
        return {"v2": resolve_pin(db, v2_id), "v1": resolve_pin(db, v1_override or DEFAULT_V1_VERSION)}
    finally:
        db.close()


def install_envelope_patch(pins: dict[str, dict[str, Any]]) -> None:
    """Wrap `console_service._build_envelope`: no console_origin, both prompt keys pinned."""
    from app.services.chatbot import console_service

    real = getattr(console_service, "_battery_real_build", None) or console_service._build_envelope
    console_service._battery_real_build = real

    def build(*args: Any, **kwargs: Any) -> dict[str, Any]:
        envelope = guard.patch_envelope(real(*args, **kwargs))
        envelope["prompt_overrides"] = {V1_KEY: pins["v1"]["id"], V2_KEY: pins["v2"]["id"]}
        if "console_origin" in envelope or envelope.get("is_test") is not True:
            raise guard.GuardError("battery envelope must be is_test without console_origin")
        return envelope

    console_service._build_envelope = build


def _find(node: Any, key: str, depth: int = 4) -> Any:
    if depth < 0 or not isinstance(node, dict):
        return None
    if node.get(key) is not None:
        return node[key]
    for v in node.values():
        if isinstance(v, dict):
            got = _find(v, key, depth - 1)
            if got is not None:
                return got
    return None


def read_trace(turn_id: str | None) -> list[Any]:
    if not turn_id:
        return []
    from app.database import SessionLocal
    from app.models.chatbot_turn import ChatbotTurn

    db = SessionLocal()
    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        trace = row.trace if row is not None else None
        return trace if isinstance(trace, list) else []
    finally:
        db.close()


def pv2_event(trace: list[Any]) -> dict[str, Any] | None:
    for e in trace:
        if isinstance(e, dict) and (e.get("kind") == "pv2_turn" or e.get("stage") == "pv2_turn"):
            if _find(e, "parser_mode") == "v1" and _find(e, "parser_out") is None:
                continue
            return e
    return None


def _norm_version(candidates: list[Any], pin: dict[str, Any]) -> str | None:
    cands = [str(c) for c in candidates if c is not None and str(c) != ""]
    if not cands:
        return None
    accepted = {str(pin["id"]), str(pin["number"])}
    return str(pin["id"]) if any(c in accepted for c in cands) else cands[0]


def run_turn(res: Any, text: str, mode: str, pin: dict[str, Any], prev_reply: str | None,
             expect: dict[str, Any]) -> tuple[checks.TurnRecord, dict[str, Any]]:
    from scripts.chatbot_eval.trace import build_digest, parse_trace

    trace = read_trace(res.turn_id)
    event = pv2_event(trace)
    _, tool_errors, _ = parse_trace(trace)
    candidates = [res.prompt_version]
    if event is not None:
        candidates += [_find(event, "version"), _find(event, "version_id")]
    rec = checks.TurnRecord(
        text=text, reply=res.reply_text or "", actions=list(res.actions or []), mode=mode,
        pv2_event=event is not None, prompt_version=_norm_version(candidates, pin),
        expected_version=str(pin["id"]), tool_errors=tool_errors)
    digest = build_digest(trace, res.reply_text or "", res.branch_kind)
    parse = _find(event, "parser_out") if event is not None else None
    facts = {"resolved": digest["resolved"], "tools": digest["tools"], "branch_kind": res.branch_kind,
             "parser_out": parse}
    return rec, {"facts": facts, "parse": parse}


def run_conversation(conv: Any, mode: str, rep: int, contact_id: str, pins: dict[str, dict[str, Any]],
                     judge_client: Any, sink: Any) -> list[dict[str, Any]]:
    from app.database import SessionLocal
    from app.services.chatbot import console_service

    run_id = f"pv2bat-{conv.id}-{mode}-r{rep}-{uuid.uuid4().hex[:6]}"
    session_vars: dict | None = {}
    history: list[dict[str, str]] = []
    prev_reply: str | None = None
    rows: list[dict[str, Any]] = []
    guard.take_blocked()
    for n, turn in enumerate(conv.turns, 1):
        started = time.monotonic()
        row: dict[str, Any] = {"conversation": conv.id, "mode": mode, "rep": rep, "turn": n,
                               "text": turn.text, "reply": "", "files": {"count": 0, "names": []},
                               "quick_replies": [], "parse": None, "branch_kind": None, "turn_id": None,
                               "prompt_version": None, "checks": [], "judge": None, "ok": False,
                               "reasons": []}
        db = SessionLocal()
        try:
            res = console_service.run_console_turn(
                db, contact_respond_id=str(contact_id), text=turn.text, session_vars=session_vars,
                run_id=run_id)
        except guard.OutboundBlocked as exc:
            row["checks"] = [{"check": "outbound_blocked", "detail": str(exc)}]
            res = None
        except Exception as exc:  # noqa: BLE001 - one broken turn must not stop the run
            row["checks"] = [{"check": "turn_error", "detail": f"{type(exc).__name__}: {exc}"[:300]}]
            res = None
        finally:
            db.close()
        blocked = guard.take_blocked()
        if res is not None:
            if isinstance(res.session_vars, dict):
                session_vars = res.session_vars
            rec, extra = run_turn(res, turn.text, mode, pins[mode], prev_reply, turn.expect)
            fails = checks.run_checks(rec, turn.expect, prev_reply)
            row["checks"] = [{"check": f.check, "detail": f.detail} for f in fails]
            files = checks.sent_files(rec.actions)
            row.update(reply=rec.reply, quick_replies=res.quick_replies or [], parse=extra["parse"],
                       branch_kind=res.branch_kind, turn_id=res.turn_id, prompt_version=rec.prompt_version,
                       files={"count": len(files),
                              "names": [str(f.get("name") or f.get("label") or f.get("url") or "")
                                        for f in files]})
            if not fails and judge_client is not None:
                row["judge"] = judge.judge_turn(
                    judge_client, history=history, text=turn.text, reply=rec.reply, files=files,
                    expect=turn.expect, facts=extra["facts"], audience=conv.audience)
            history.append({"user": turn.text, "bot": rec.reply})
            prev_reply = rec.reply
        if blocked and not any(c["check"] == "outbound_blocked" for c in row["checks"]):
            row["checks"].append({"check": "outbound_blocked", "detail": ", ".join(blocked)})
        verdict = (row["judge"] or {}).get("verdict")
        row["ok"] = res is not None and not row["checks"] and verdict in (None, "pass")
        row["reasons"] = [f"{c['check']}: {c['detail']}" for c in row["checks"]]
        if verdict in ("fail", "judge_error"):
            row["reasons"].append(f"judge: {', '.join(row['judge']['classes']) or verdict}: "
                                  f"{row['judge']['reason']}")
        row["latency_ms"] = int((time.monotonic() - started) * 1000)
        rows.append(row)
        sink(row)
        if res is None:
            prev_reply = None
    return rows


def make_out_dir(name: str, out: str | None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(out) if out else DEFAULT_OUT_ROOT / f"{name}-{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def read_calibration_agreement() -> float | None:
    f = DEFAULT_OUT_ROOT / CALIBRATION_FILE
    try:
        return float(json.loads(f.read_text(encoding="utf-8"))["agreement"])
    except Exception:  # noqa: BLE001
        return None


def run(args: Any) -> Path:
    bootstrap_env()
    mcp_path = ensure_worktree_mcp()
    guard.install(os.environ)
    battery_dir = Path(args.battery_dir) if args.battery_dir else schema.BATTERY_DIR
    convs = schema.load_battery(battery_dir)
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        missing = wanted - {c.id for c in convs}
        if missing:
            raise SystemExit(f"unknown conversation ids: {sorted(missing)}")
        convs = [c for c in convs if c.id in wanted]
    if args.domain:
        convs = [c for c in convs if c.domain == args.domain]
    if not convs:
        raise SystemExit("no conversations selected")
    contacts = schema.load_contacts(battery_dir / "contacts.yaml")
    pins = resolve_pins(args.v2_version, args.v1_version)
    install_envelope_patch(pins)
    judge_client = None if args.no_judge else judge.AnthropicClient(judge.load_api_key(args.judge_key_file))
    out_dir = make_out_dir(args.name, args.out)
    modes = ["v2", "v1"] if args.mode == "both" else [args.mode]
    results_path = out_dir / "results.jsonl"
    all_rows: list[dict[str, Any]] = []

    def sink(row: dict[str, Any]) -> None:
        with _write_lock:
            all_rows.append(row)
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    print(f"run dir: {out_dir}", flush=True)
    for mode in modes:
        jobs = [(c, rep) for c in convs for rep in range(1, args.repeat + 1)]
        with guard.force_parser_mode(mode), ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
            futures = [pool.submit(run_conversation, c, mode, rep, contacts[c.audience], pins,
                                   judge_client, sink) for c, rep in jobs]
            for f in futures:
                f.result()
        print(f"mode {mode} done: {sum(1 for r in all_rows if r['mode'] == mode)} turns", flush=True)

    meta = {"v2": pins["v2"], "v1": pins["v1"], "git_sha": git_sha(), "mcp_path": str(mcp_path),
            "judge_agreement": read_calibration_agreement() if judge_client else None}
    results = report.build_results(convs, all_rows)
    (out_dir / "table.md").write_text(report.render_table(results, meta), encoding="utf-8")
    failing = [r for r in all_rows if not r["ok"]]
    (out_dir / "failures.md").write_text(report.render_failures(failing), encoding="utf-8")
    print(f"table: {out_dir / 'table.md'}", flush=True)
    blocked = [r for r in all_rows if any(c["check"] == "outbound_blocked" for c in r["checks"])]
    if blocked:
        print(f"WARNING: {len(blocked)} turns hit the outbound guard", flush=True)
    return out_dir


def calibrate(args: Any) -> float:
    bootstrap_env()
    rows = schema.load_calibration()
    client = judge.AnthropicClient(judge.load_api_key(args.judge_key_file))

    def one(r: dict[str, Any]) -> dict[str, Any]:
        return judge.judge_turn(client, history=r.get("history") or [], text=r["text"], reply=r["reply"],
                                files=r.get("files") or 0, expect=r["expect"],
                                facts=r.get("facts") or {}, audience=r.get("audience") or "staff")

    with ThreadPoolExecutor(max_workers=4) as pool:
        verdicts = list(pool.map(one, rows))
    labels = [r["label"] for r in rows]
    got = [v["verdict"] for v in verdicts]
    score = judge.agreement(labels, got)
    for r, v in zip(rows, verdicts):
        if v["verdict"] != r["label"]:
            print(f"DISAGREE {r['id']}: label {r['label']}, judge {v['verdict']} {v['classes']} {v['reason']}")
    mark = "" if score >= report.PROVISIONAL_BELOW else " (PROVISIONAL, below 0.9)"
    print(f"agreement {score:.2f} over {len(rows)} turns{mark}")
    DEFAULT_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (DEFAULT_OUT_ROOT / CALIBRATION_FILE).write_text(
        json.dumps({"agreement": score, "n": len(rows), "at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8")
    return score
