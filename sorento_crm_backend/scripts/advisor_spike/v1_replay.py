"""Replay a scenario through the real v1 console turn (dry run, in process) and capture what the advisor needs.

Makes LIVE v1 parser calls (gpt-5.4-mini) per turn: the runner budget-gates it. Nothing leaves: RQ enqueues and
Respond sends are refused. The console's own `_lanes_on` (which writes system_settings) is replaced by a check
that the switches are already on; if they are off the replay stops and reports (it never flips them).
"""
from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

AUDIENCE_CONTACTS = {"staff": "487555417", "dealer": "437264483", "unlinked": "423755030"}

_patched: list[tuple[Any, str, Any]] = []
_blocked: list[str] = []
_lock = threading.Lock()


class LanesOff(RuntimeError):
    pass


def _blocker(label: str):
    def blocked(*a: Any, **k: Any) -> Any:
        with _lock:
            _blocked.append(label)
        raise RuntimeError(f"{label} refused: the spike sends nothing")

    return blocked


def install_guard() -> None:
    """Refuse every RQ enqueue and Respond send in this process; neutralise console `_lanes_on`."""
    if _patched:
        return
    import rq

    from app.services import queue_service, respond_messaging_service
    from app.services.chatbot import console_service

    def _swap(owner: Any, attr: str, repl: Any) -> None:
        _patched.append((owner, attr, getattr(owner, attr)))
        setattr(owner, attr, repl)

    _swap(rq.Queue, "enqueue_call", _blocker("rq.Queue.enqueue_call"))
    for original, label in ((queue_service.enqueue_job, "enqueue_job"),
                            (respond_messaging_service.send_text_or_template, "send_text_or_template")):
        import sys

        repl = _blocker(label)
        for mod in list(sys.modules.values()):
            ns = getattr(mod, "__dict__", None)
            if isinstance(ns, dict):
                for name, value in list(ns.items()):
                    if value is original:
                        _patched.append((mod, name, original))
                        setattr(mod, name, repl)

    @contextmanager
    def _noop() -> Iterator[None]:
        yield

    _swap(console_service, "_lanes_on", _noop)
    from . import mcp_local

    mcp_local.patch_v1_mcp()


def check_lanes_on() -> None:
    from app.database import SessionLocal
    from app.services.chatbot import console_service as cs

    db = SessionLocal()
    try:
        enabled, lanes = cs._read_switches(db)
    finally:
        db.close()
    missing = [b for b in cs.BRANCH_KINDS if b not in (lanes or [])]
    if not enabled or missing:
        raise LanesOff(f"business lane enabled={enabled}, branches missing={missing}; not flipping them")


def take_blocked() -> list[str]:
    with _lock:
        out = list(_blocked)
        _blocked.clear()
        return out


def _understood(trace: list[Any]) -> dict[str, Any]:
    """Parser verdict off the `understood` stage: domains, intent, entities (+ the raw derived block)."""
    for e in trace:
        if isinstance(e, dict) and e.get("kind") is None and e.get("stage") == "understood":
            facts = e.get("facts") if isinstance(e.get("facts"), dict) else {}
            raw = e.get("raw") if isinstance(e.get("raw"), dict) else {}
            derived = raw.get("derived") if isinstance(raw.get("derived"), dict) else {}
            domains: list[str] = []
            for d in (facts.get("domain"), derived.get("domain_hint")):
                if isinstance(d, str) and d and d not in domains:
                    domains.append(d)
            for sub in (derived.get("sub_asks") or derived.get("asks") or []):
                d = sub.get("domain") if isinstance(sub, dict) else None
                if isinstance(d, str) and d and d not in domains:
                    domains.append(d)
            return {
                "domains": domains,
                "intent": facts.get("intent") or derived.get("intent_hint"),
                "message_type": facts.get("message_type"),
                "entities": derived.get("entities") or [],
                "user_goal": derived.get("user_goal"),
                "usage": {k: facts.get(k) for k in ("prompt_tokens", "cached_tokens", "completion_tokens", "prompt_version")},
                "derived": derived,
            }
    return {"domains": [], "intent": None, "message_type": None, "entities": [], "user_goal": None, "usage": {}, "derived": {}}


def _scope_from_trace(trace: list[Any], contact: str) -> dict[str, str]:
    """The scope v1 itself stamped (first tool event's args), else the contact id + default workspace."""
    from app.services.chatbot.lanes.business.fetch import space_id_or_default

    for e in trace:
        if isinstance(e, dict) and e.get("kind") == "tool":
            args = (e.get("payload") or e).get("args") or {}
            if args.get("contact_id") is not None:
                return {"contact_id": str(args["contact_id"]), "space_id": space_id_or_default(args.get("space_id"))}
    return {"contact_id": contact, "space_id": space_id_or_default(None)}


def _tool_events(trace: list[Any]) -> list[dict[str, Any]]:
    out = []
    for e in trace:
        if isinstance(e, dict) and e.get("kind") == "tool" and e.get("name"):
            p = e.get("payload") or e
            out.append({"name": p.get("name"), "args": p.get("args"), "result_chars": len(str(p.get("envelope") or ""))})
    return out


def run_turn(contact: str, text: str, session_vars: dict | None, run_id: str) -> dict[str, Any]:
    from app.database import SessionLocal
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import console_service
    from app.services.chatbot.llm_call import capture_usage

    db = SessionLocal()
    started = time.monotonic()
    try:
        with capture_usage() as usage:
            res = console_service.run_console_turn(
                db, contact_respond_id=contact, text=text, session_vars=session_vars, run_id=run_id)
        latency_ms = int((time.monotonic() - started) * 1000)
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == res.turn_id).first() if res.turn_id else None
        trace = [e for e in ((row.trace if row else None) or []) if isinstance(e, dict)]
    finally:
        db.close()
    verdict = _understood(trace)
    reply = "\n".join([t for t in [res.reply_text, *(res.send_messages or [])] if t])
    return {
        "text": text, "reply": reply, "branch_kind": res.branch_kind, "turn_id": res.turn_id,
        "parser": {k: verdict[k] for k in ("domains", "intent", "message_type", "entities", "user_goal")},
        "parser_derived": verdict["derived"],
        "session_vars": res.session_vars if isinstance(res.session_vars, dict) else None,
        "latency_ms": latency_ms, "llm_usage": list(usage) or [verdict["usage"]],
        "tools": _tool_events(trace), "scope": _scope_from_trace(trace, contact),
        "blocked_outbound": take_blocked(),
    }


def replay_scenario(scenario: dict[str, Any], arm_tag: str = "v1") -> dict[str, Any]:
    """scenario: {id, audience, history: [user msgs or {user, bot}], moment}. Returns
    {turns: [turn record...], moment: last record, scope}. History user messages are replayed as real turns
    so session_vars carry; the moment is the last turn."""
    contact = AUDIENCE_CONTACTS[scenario["audience"]]
    run_id = f"advspike-{scenario['id']}-{arm_tag}-{uuid.uuid4().hex[:6]}"
    msgs = [h if isinstance(h, str) else h["user"] for h in scenario.get("history") or []]
    msgs.append(scenario["moment"] if isinstance(scenario["moment"], str) else scenario["moment"]["text"])
    session_vars: dict | None = {}
    turns: list[dict[str, Any]] = []
    for text in msgs:
        rec = run_turn(contact, text, session_vars, run_id)
        rec["session_vars_before"] = session_vars
        if rec["session_vars"] is not None:
            session_vars = rec["session_vars"]
        turns.append(rec)
    return {"scenario_id": scenario["id"], "run_id": run_id, "contact": contact,
            "turns": turns, "moment": turns[-1], "scope": turns[-1]["scope"]}
