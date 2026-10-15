"""The advisor loop: arms C, C2, C2all (CHAT-ADVISOR-SPIKE contract).

C     system + user block (v1 parser verdict of the moment, carried state, last 3 exchanges); subset tools.
C2    raw transcript (every prior user message and bot reply from the v1 replay, then the moment); same subset.
C2all C2 with every ADVISOR_READ_ONLY tool.
Loop: at most MAX_CALLS model calls for the tool loop; the final text goes through the fact guard; one retry
naming the unsupported tokens (an extra model call, outside the cap); a second failure falls back to the v1 reply.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import fact_guard, subset

MAX_CALLS = 4
RESULT_CAP_CHARS = 20000
PROMPT_FILE = Path(__file__).parent / "prompts" / "advisor_system.md"
_domain_tools_cache: dict[str, list[str]] = {}


def load_system() -> str:
    if not PROMPT_FILE.exists():
        PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROMPT_FILE.write_text("You are the Sorento sales assistant. Use the tools; never state a fact a tool did not give.\n")
    return PROMPT_FILE.read_text(encoding="utf-8")


def load_domain_tools() -> dict[str, list[str]]:
    """chatbot_domains name -> tools (read only)."""
    if not _domain_tools_cache:
        from app.database import SessionLocal
        from app.models.chatbot_policy import ChatbotDomain

        db = SessionLocal()
        try:
            for row in db.query(ChatbotDomain).order_by(ChatbotDomain.sort_order).all():
                _domain_tools_cache[row.name] = list(row.tools or [])
        finally:
            db.close()
    return _domain_tools_cache


def _exchanges(turns: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(t["text"], t["reply"]) for t in turns]


def _c_block(turns: list[dict[str, Any]], moment: dict[str, Any]) -> str:
    prior = _exchanges(turns[:-1])[-3:]
    state = moment.get("session_vars_before")
    parts = [
        "Parser verdict for the new message (from the existing understanding step):",
        json.dumps({k: moment["parser"].get(k) for k in ("domains", "intent", "message_type", "entities", "user_goal")},
                   ensure_ascii=False, default=str),
        "",
        "Carried conversation state before this message:",
        (json.dumps(state, ensure_ascii=False, default=str)[:1500] if state else "(none)"),
        "",
        "Last exchanges:",
    ]
    for u, b in prior:
        parts += [f"Customer: {u}", f"Assistant: {b}"]
    if not prior:
        parts.append("(none)")
    parts += ["", f"New message from the customer: {moment['text']}"]
    return "\n".join(parts)


def _c2_messages(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for t in turns[:-1]:
        msgs.append({"role": "user", "content": t["text"]})
        msgs.append({"role": "assistant", "text": t["reply"] or "(no reply)", "tool_calls": []})
    msgs.append({"role": "user", "content": turns[-1]["text"]})
    return msgs


def _blank(arm: str, model: str, moment: dict[str, Any]) -> dict[str, Any]:
    return {"arm": arm, "model": model, "calls": [], "tools_called": [], "guard": [], "flags": [],
            "final_reply": None, "v1_reply": moment["reply"], "advisor_latency_ms": 0, "cost_usd": 0.0,
            "tools_offered": []}


def run_advisor(
    arm: str,
    model: str,
    scenario_turns: list[dict[str, Any]],
    v1_turn_record: dict[str, Any],
    scope: dict[str, Any],
    *,
    provider: Any = None,
    list_tools: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    call_tool: Callable[[str, dict[str, Any], dict[str, Any]], str] | None = None,
    domain_tools: dict[str, list[str]] | None = None,
    system: str | None = None,
) -> dict[str, Any]:
    """scenario_turns: v1 replay records (each {text, reply, parser, ...}), the last one the moment."""
    from . import mcp_local, providers

    assert arm in ("C", "C2", "C2all", "C2opus"), arm
    mode = "C2" if arm == "C2opus" else arm
    list_tools = list_tools or mcp_local.list_tool_schemas
    call_tool = call_tool or mcp_local.call_tool
    provider = provider or providers.make_provider(model)
    system = system if system is not None else load_system()
    moment = v1_turn_record
    rec = _blank(arm, model, moment)

    if mode == "C2all":
        names = sorted(subset.ADVISOR_READ_ONLY)
    else:
        names = subset.select_tools(moment["parser"].get("domains") or [],
                                    domain_tools=domain_tools if domain_tools is not None else load_domain_tools(),
                                    read_only=subset.ADVISOR_READ_ONLY)
        if not names:
            rec["flags"].append("no_subset")
            rec["final_reply"] = moment["reply"]
            return rec
    tools = list_tools(names)
    rec["tools_offered"] = [t["name"] for t in tools]
    if not tools:
        rec["flags"].append("no_subset")
        rec["final_reply"] = moment["reply"]
        return rec

    messages = ([{"role": "user", "content": _c_block(scenario_turns, moment)}] if mode == "C"
                else _c2_messages(scenario_turns))
    user_texts = [t["text"] for t in scenario_turns]
    evidence: list[str] = []

    def model_call() -> dict[str, Any]:
        out = provider.complete(messages, tools, system)
        rec["calls"].append({"latency_ms": out["latency_ms"], "usage": out["usage"], "cost_usd": out["cost_usd"],
                             "tool_calls": [c["name"] for c in out["tool_calls"]]})
        rec["advisor_latency_ms"] += out["latency_ms"]
        rec["cost_usd"] += out["cost_usd"]
        return out

    text = ""
    for _ in range(MAX_CALLS):
        out = model_call()
        if not out["tool_calls"]:
            text = out["text"].strip()
            break
        messages.append({"role": "assistant", "text": out["text"], "tool_calls": out["tool_calls"]})
        results = []
        for tc in out["tool_calls"]:
            if tc["name"] not in rec["tools_offered"]:
                content = f"ERROR: tool {tc['name']} is not available"
            else:
                t0 = time.monotonic()
                try:
                    content = call_tool(tc["name"], tc["args"], scope)
                except Exception as exc:  # noqa: BLE001
                    content = f"ERROR: {type(exc).__name__}: {exc}"[:500]
                rec["advisor_latency_ms"] += int((time.monotonic() - t0) * 1000)
            evidence.append(content)
            rec["tools_called"].append({"name": tc["name"], "args": tc["args"], "result_chars": len(content),
                                        "result": content[:RESULT_CAP_CHARS]})
            results.append({"id": tc["id"], "name": tc["name"], "content": content[:RESULT_CAP_CHARS]})
        messages.append({"role": "tool", "results": results})
    else:
        rec["flags"].append("loop_exhausted")
        rec["final_reply"] = moment["reply"]
        rec["flags"].append("guard_fallback")
        return rec

    if not text:
        rec["flags"] += ["empty_reply", "guard_fallback"]
        rec["final_reply"] = moment["reply"]
        return rec

    g = fact_guard.check_reply(text, evidence, user_texts)
    rec["guard"].append({"ok": g.ok, "unsupported": g.unsupported, "leaks": g.leaks})
    if not g.ok:
        bad = g.unsupported + [f"(internal id) {x}" for x in g.leaks]
        messages.append({"role": "assistant", "text": text, "tool_calls": []})
        messages.append({"role": "user", "content": (
            "Your reply contains values that no tool result or customer message supports: "
            + "; ".join(bad) + ". Rewrite the reply without them (call a tool if you need the fact), "
            "and never show internal ids.")})
        out = model_call()
        retry = out["text"].strip()
        if out["tool_calls"]:
            for tc in out["tool_calls"]:
                rec["tools_called"].append({"name": tc["name"], "args": tc["args"], "result_chars": 0, "result": "(retry tool call not executed)"})
            retry = ""
        g2 = fact_guard.check_reply(retry, evidence, user_texts) if retry else None
        rec["guard"].append({"ok": bool(g2 and g2.ok), "unsupported": g2.unsupported if g2 else ["(no text)"],
                             "leaks": g2.leaks if g2 else []})
        if g2 and g2.ok:
            rec["flags"].append("guard_retry_ok")
            text = retry
        else:
            rec["flags"].append("guard_fallback")
            text = moment["reply"]
    rec["final_reply"] = text
    return rec
