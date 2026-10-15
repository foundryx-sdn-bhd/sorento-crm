"""Owner walkthrough through the REAL v1 pipeline (console_service.run_console_turn, in process, dry run).

python -m scripts.advisor_spike.walkthrough --scenarios PATH --contacts staff,dealer --out DIR --budget-usd N
       [--prompt-version-id UUID] [--ids W01,W02] [--dry-run]

Scenario file (YAML list): {id, turns: [user text, ...]}. Each scenario x contact is a fresh conversation (empty
session_vars, threaded through its own turns only; the contact's stored state is never read). The resolver's and
the spec fallback's model calls stay ON (real pipeline) and are recorded when llm_call.chat sees them.
One JSON object per turn is appended to <out>/walkthrough.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

CONTACTS = {"staff": "487555417", "dealer": "437264483"}
PARSER_KEY = "chatbot_semantic_parser"
PRICES = {"gpt-5.4-mini": (0.75, 0.075, 4.5)}  # in, cached in, out ($/M)
EST_TURN_USD, EST_TURN_SECONDS = 0.008, 7
SUMMARY_FIELDS = 4


# ---- isolation: every prior-turn read is limited to the current scenario's own console run_id ----------------
_ISOLATION: dict[str, Any] = {"run_id": None, "scoping": 0, "installed": False, "user_block": None}


def install_isolation() -> None:
    """Limit every prior-turn read of the chatbot path to the current run (`test_run_id` in the stored envelope).

    The readers (`turn_runtime.previous_reply_text`, `turn_runtime.recent_exchanges`, and the engine's
    `_memory_intake`, which reads the contact's earlier turns and closed conversation frames) all query
    `chatbot.turns` / `conversation_frames` by contact. Each is wrapped so that, while it runs, a session-level
    ORM criterion restricts ChatbotTurn to rows whose envelope carries this run's id and hides every
    ConversationFrame. Nothing else is touched (the borrowed inbound envelope and by-id reads run outside the
    wrappers). Also records the parser's user block for the JSONL row."""
    if _ISOLATION["installed"]:
        return
    from sqlalchemy import event, false
    from sqlalchemy.orm import Session, with_loader_criteria

    from app.models.chatbot_turn import ChatbotTurn
    from app.models.conversation_frame import ConversationFrame
    from app.services.chatbot import engine, turn_runtime
    from app.services.chatbot.head import parser

    @event.listens_for(Session, "do_orm_execute")
    def _scope(state: Any) -> None:
        if not _ISOLATION["scoping"] or not state.is_select:
            return
        run_id = _ISOLATION["run_id"]
        state.statement = state.statement.options(
            with_loader_criteria(ChatbotTurn, lambda cls: cls.envelope["test_run_id"].astext == run_id,
                                 include_aliases=True),
            with_loader_criteria(ConversationFrame, lambda cls: false(), include_aliases=True),
        )

    def scoped(fn: Any) -> Any:
        def wrapper(*a: Any, **k: Any) -> Any:
            _ISOLATION["scoping"] += 1
            try:
                return fn(*a, **k)
            finally:
                _ISOLATION["scoping"] -= 1
        wrapper.__name__ = getattr(fn, "__name__", "scoped")
        return wrapper

    turn_runtime.previous_reply_text = scoped(turn_runtime.previous_reply_text)
    turn_runtime.recent_exchanges = scoped(turn_runtime.recent_exchanges)
    engine._memory_intake = scoped(engine._memory_intake)

    real_parse = parser.parse

    def recording_parse(config: Any, user_block: str) -> Any:
        _ISOLATION["user_block"] = user_block
        return real_parse(config, user_block)

    parser.parse = recording_parse
    _ISOLATION["installed"] = True


def history_seen() -> dict[str, Any]:
    """What the parser's user block held of earlier turns on the turn just run."""
    block = _ISOLATION.get("user_block") or ""
    lines = block.splitlines()
    out: dict[str, Any] = {"recent_exchanges": None, "previous_response": None, "earlier_messages": []}
    for i, ln in enumerate(lines):
        if ln.startswith("Recent exchanges"):
            tail = "\n".join(lines[i + 1:])
            out["recent_exchanges"] = tail.split("\nKnown brands")[0]
            out["recent_exchange_count"] = out["recent_exchanges"].count("\nUser: ") + out["recent_exchanges"].startswith("User: ")
        elif ln.startswith("Previous response"):
            out["previous_response"] = ln
        elif "earlier" in ln.lower() and "message" in ln.lower():
            out["earlier_messages"].append(ln[:300])
    out["user_block_chars"] = len(block)
    out["user_block"] = block[:8000]
    return out


def call_cost(c: dict[str, Any]) -> float | None:
    p = PRICES.get(c.get("model") or "")
    if not p:
        return None
    cached = c.get("cached_tokens") or 0
    uncached = max((c.get("prompt_tokens") or 0) - cached, 0)
    return (uncached * p[0] + cached * p[1] + (c.get("completion_tokens") or 0) * p[2]) / 1e6


def load_scenarios(path: str, ids: str | None) -> list[dict[str, Any]]:
    import yaml

    rows = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rows = [{"id": r["id"], "turns": [t if isinstance(t, str) else t["text"] for t in r["turns"]]} for r in rows]
    if ids:
        want = {i.strip() for i in ids.split(",") if i.strip()}
        missing = want - {r["id"] for r in rows}
        if missing:
            raise SystemExit(f"unknown scenario ids: {sorted(missing)}")
        rows = [r for r in rows if r["id"] in want]
    return rows


def plan(scenarios: list[dict[str, Any]], contacts: list[str]) -> dict[str, Any]:
    turns = sum(len(s["turns"]) for s in scenarios) * len(contacts)
    cost = turns * EST_TURN_USD
    return {"turns": turns, "cost": cost,
            "text": (f"{len(scenarios)} scenarios x {len(contacts)} contacts = {turns} live console turns "
                     f"(parser + resolver/spec calls), ~{turns * EST_TURN_SECONDS // 60} min, ~${cost:.2f}")}


def print_policies(contacts: list[str]) -> list[dict[str, Any]]:
    from app.database import SessionLocal
    from app.models.base import company_scope
    from app.models.inventory import Warehouse
    from app.services.chatbot.lanes.business.fetch import SPACE_ID
    from app.services.stock_visibility import resolve_policy

    out = []
    db = SessionLocal()
    try:
        for name in contacts:
            cid = CONTACTS[name]
            pol = resolve_policy(db, cid, SPACE_ID)
            if pol is None:
                info = {"contact": name, "policy": None}
            else:
                def codes(ids: Any) -> Any:
                    if ids is None:
                        return None
                    rows = company_scope(db, None).query(Warehouse.warehouse_code).filter(Warehouse.id.in_(list(ids))).all() if ids else []
                    return sorted(r[0] for r in rows)
                info = {"contact": name, "mode": pol.mode, "source": pol.source, "source_label": pol.source_label,
                        "hide_zero_locations": pol.hide_zero_locations,
                        "warehouse_scope": "all active in company scope" if pol.warehouse_ids is None else codes(pol.warehouse_ids),
                        "excluded_warehouses": codes(pol.excluded_warehouse_ids)}
            out.append(info)
            print("stock visibility:", json.dumps(info, default=str), flush=True)
    finally:
        db.close()
    return out


def _clean(v: Any) -> bool:
    return v not in (None, [], {}, False, "")


def _gates(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Every resolver gate dict (has `by_entity_type`) anywhere under `node`; the shape of the enclosing
    envelope differs per lane (resolve_gate.ctx.gate, envelopes[...], ...)."""
    out: list[dict[str, Any]] = []
    if depth > 8:
        return out
    if isinstance(node, dict):
        if "by_entity_type" in node and "tokens" in node:
            out.append(node)
        else:
            for v in node.values():
                out += _gates(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            out += _gates(v, depth + 1)
    return out


def _codes(rows: Any) -> list[str]:
    return [r.get("canonical_code") or r.get("code") for r in rows or [] if isinstance(r, dict)]


def _resolver_outcome(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in trace:
        if e.get("kind") is None and e.get("stage") == "looked_up":
            for gate in _gates(e.get("raw")):
                out.append({
                    "tokens": gate.get("tokens"), "gate_passed": gate.get("gate_passed"), "gate_reason": gate.get("gate_reason"),
                    "resolved": {k: _codes(v) for k, v in (gate.get("by_entity_type") or {}).items()},
                    "alternatives": _codes(gate.get("alternatives")), "unresolved_tokens": gate.get("unresolved_tokens"),
                    "specific_options": gate.get("specific_options"), "clarification": gate.get("gate_clarification") or None,
                    "token_coverage": gate.get("token_coverage"),
                })
    return out


def _id_code_map(trace: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in trace:
        if e.get("kind") is None:
            for gate in _gates(e.get("raw")):
                rows = [r for v in (gate.get("by_entity_type") or {}).values() for r in v or []]
                rows += (gate.get("alternatives") or []) + (gate.get("compatible_entities") or [])
                for r in rows:
                    if isinstance(r, dict) and r.get("uuid") and (r.get("canonical_code") or r.get("code")):
                        out.setdefault(r["uuid"], r.get("canonical_code") or r.get("code"))
    return out


def _summarise(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        return {"rows": None, "first5": [], "note": str(envelope)[:200]}
    rows = envelope.get("items") or envelope.get("answers") or envelope.get("data") or []
    first = []
    for r in rows[:5]:
        if not isinstance(r, dict):
            first.append(str(r)[:120])
            continue
        fields = {f.get("label") or f.get("key"): f.get("value") for f in (r.get("fields") or [])[:SUMMARY_FIELDS] if isinstance(f, dict)}
        first.append({"title": r.get("title") or r.get("product_code"), **fields} if fields or r.get("title") else
                     {k: r[k] for k in list(r)[:SUMMARY_FIELDS]})
    return {"rows": len(rows), "first5": first, "intro": envelope.get("intro")}


def _tools(trace: list[dict[str, Any]], idmap: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for e in trace:
        if e.get("kind") == "tool" and e.get("name"):
            args = {k: v for k, v in (e.get("args") or {}).items() if v is not None}
            for key in [k for k in args if k.endswith("_ids")]:
                if isinstance(args[key], list):
                    args[key.replace("_ids", "_codes")] = [idmap.get(i, i) for i in args.pop(key)]
            out.append({"name": e["name"], "args": args, "result": _summarise(e.get("envelope"))})
    return out


def run_one_turn(contact: str, text: str, session_vars: dict, run_id: str, pvid: str | None) -> dict[str, Any]:
    from app.database import SessionLocal
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import console_service
    from app.services.chatbot.llm_call import capture_usage

    from . import v1_replay

    install_isolation()
    _ISOLATION["run_id"], _ISOLATION["user_block"] = run_id, None
    db = SessionLocal()
    started = time.monotonic()
    try:
        with capture_usage() as usage:
            res = console_service.run_console_turn(db, contact_respond_id=contact, text=text, session_vars=session_vars,
                                                   run_id=run_id, prompt_version_id=pvid)
        latency_ms = int((time.monotonic() - started) * 1000)
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == res.turn_id).first() if res.turn_id else None
        trace = [e for e in ((row.trace if row else None) or []) if isinstance(e, dict)]
    finally:
        db.close()
    verdict = v1_replay._understood(trace)
    derived = verdict["derived"]
    u = verdict["usage"]
    calls = [dict(c) for c in usage]
    parser_seen = any((c.get("prompt_tokens") == u.get("prompt_tokens")) for c in calls) and u.get("prompt_tokens") is not None
    if u.get("prompt_tokens") is not None and not parser_seen:
        calls.insert(0, {"role": "parser", "model": "gpt-5.4-mini", "prompt_tokens": u.get("prompt_tokens"),
                         "cached_tokens": u.get("cached_tokens"), "completion_tokens": u.get("completion_tokens")})
    for c in calls:
        c["cost_usd"] = call_cost(c)
    idmap = _id_code_map(trace)
    reply = "\n".join(t for t in [res.reply_text, *(res.send_messages or [])] if t)
    return {
        "history_seen": history_seen(), "run_id": run_id,
        "reply": reply, "branch_kind": res.branch_kind, "turn_id": res.turn_id, "latency_ms": latency_ms,
        "parser": {
            "intent": derived.get("intent_hint"), "domain_hint": derived.get("domain_hint"),
            "message_type": derived.get("message_type"),
            "entities": [{k: e.get(k) for k in ("raw", "hint", "canonical_code", "confident")} for e in derived.get("entities") or []],
            "find": derived.get("find"), "extracted_specs": derived.get("extracted_specs"),
            "period": {k: derived.get(k) for k in ("date_mode", "date_filter_start", "date_filter_end")},
            "quantity": derived.get("demand_qty"), "open_question_answer": derived.get("open_question_answer"),
            "goal": derived.get("user_goal"),
            "nonnull_keys": {k: v for k, v in derived.items() if _clean(v) and k not in ("routing", "anaphora")},
        },
        "prompt_version_used": u.get("prompt_version"),
        "resolver": _resolver_outcome(trace), "tools": _tools(trace, idmap),
        "llm_calls": calls, "turn_cost_usd": sum(c["cost_usd"] or 0 for c in calls),
        "unpriced_calls": [c.get("model") for c in calls if c["cost_usd"] is None],
        "session_vars": res.session_vars if isinstance(res.session_vars, dict) else None,
        "blocked_outbound": v1_replay.take_blocked(),
    }


def expected_version(pvid: str | None) -> int | None:
    if not pvid:
        return None
    from app.database import SessionLocal
    from app.models.ai_prompt import AIPromptVersion

    db = SessionLocal()
    try:
        row = db.query(AIPromptVersion).filter(AIPromptVersion.id == pvid).first()
        if row is None:
            raise SystemExit(f"prompt version {pvid} not found")
        if row.name != PARSER_KEY:
            raise SystemExit(f"prompt version {pvid} is {row.name!r}, not {PARSER_KEY!r}")
        return row.version
    finally:
        db.close()


def run_walkthrough(scenarios: list[dict[str, Any]], contacts: list[str], out: Path, pvid: str | None,
                    ledger: Any, runner: Any = run_one_turn) -> None:
    from .run import BudgetExceeded, append

    exp = expected_version(pvid)
    path = out / "walkthrough.jsonl"
    done = {(r["scenario"], r["contact"], r["turn"]) for r in map(json.loads, path.read_text().splitlines())} if path.exists() else set()
    for sc in scenarios:
        for name in contacts:
            cid = CONTACTS[name]
            run_id = f"walk-{sc['id']}-{name}-{uuid.uuid4().hex[:6]}"
            session_vars: dict = {}
            for i, text in enumerate(sc["turns"], 1):
                if (sc["id"], name, i) in done:
                    continue  # resumed run: the conversation restarts from this scenario's first undone turn
                ledger.check(EST_TURN_USD)
                rec = runner(cid, text, session_vars, run_id, pvid)
                used = rec["prompt_version_used"]
                rec.update(scenario=sc["id"], contact=name, turn=i, text=text, session_vars_before=session_vars,
                           prompt_version_expected=exp, prompt_ok=(exp is None or used == exp))
                if not rec["prompt_ok"]:
                    print(f"WARNING {sc['id']}/{name}/{i}: parser used prompt version {used}, expected {exp}", flush=True)
                if isinstance(rec.get("session_vars"), dict):
                    session_vars = rec["session_vars"]
                ledger.add(f"walk:{sc['id']}:{name}:{i}", rec["turn_cost_usd"])
                append(path, rec)
                print(f"{sc['id']} {name} t{i} v{used} {rec['latency_ms']}ms ${rec['turn_cost_usd']:.4f} "
                      f"calls={len(rec['llm_calls'])} :: {rec['reply'][:70]!r}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="advisor_spike.walkthrough")
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--contacts", default="staff,dealer")
    ap.add_argument("--ids")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--prompt-version-id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from scripts.advisor_spike import env

    env.bootstrap()
    contacts = [c.strip() for c in args.contacts.split(",") if c.strip()]
    bad = [c for c in contacts if c not in CONTACTS]
    if bad:
        raise SystemExit(f"unknown contacts {bad}; choose from {sorted(CONTACTS)}")
    scenarios = load_scenarios(args.scenarios, args.ids)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    p = plan(scenarios, contacts)
    print(p["text"])
    print("prompt version pinned:", args.prompt_version_id or "(published)")
    if args.dry_run:
        print("dry run: no LLM call made")
        return 0
    from scripts.advisor_spike import v1_replay
    from scripts.advisor_spike.run import BudgetExceeded, Ledger

    v1_replay.install_guard()
    v1_replay.check_lanes_on()
    print_policies(contacts)
    ledger = Ledger(out / "spend.jsonl", args.budget_usd)
    try:
        run_walkthrough(scenarios, contacts, out, args.prompt_version_id, ledger)
    except BudgetExceeded as exc:
        print(f"STOPPED: {exc}")
        return 2
    print(f"done. spend ${ledger.total:.3f} of ${args.budget_usd:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
