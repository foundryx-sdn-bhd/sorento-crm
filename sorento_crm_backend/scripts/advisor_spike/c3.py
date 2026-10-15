"""Arm C3: code proposes (options.py), the model picks and words it, code substitutes the sentence.

One model call, no tools. Reply = the v1 reply kept as is + a short addition the model writes: at most one
literal placeholder {OPTn} and at most one short sentence of its own. The guard runs on the model's own words
(placeholder removed) with EMPTY evidence, so any code, number or date it adds fails -> v1 reply.
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import fact_guard, options

SYSTEM = """You help a WhatsApp sales bot sound like a good salesman. The bot already has its answer (the "Current reply"). \
Below are OPTIONS, each a ready-made sentence filled from the company's data. Decide whether ONE option helps this customer now.
Write only the ADDITION that goes after the current reply (the current reply itself is kept as is):
- To use an option, write its placeholder exactly, e.g. {OPT1}, at most once. Never write the option's own words, codes, numbers or dates.
- You may add at most one short sentence of your own, in the customer's language, with NO product codes, numbers, prices, quantities or dates.
- If no option fits, or the current reply already covers it, write exactly: NONE
Output only the addition, nothing else."""


def run_c3(scenario: dict[str, Any], model: str, scenario_turns: list[dict[str, Any]], v1_turn_record: dict[str, Any],
           scope: dict[str, Any], *, provider: Any, build: Any = None) -> dict[str, Any]:
    moment = v1_turn_record
    rec: dict[str, Any] = {"arm": "C3", "model": model, "calls": [], "tools_called": [], "guard": [], "flags": [],
                           "final_reply": None, "v1_reply": moment["reply"], "advisor_latency_ms": 0, "cost_usd": 0.0,
                           "tools_offered": [], "options": [], "chosen": None, "evidence_chars": 0}
    t0 = time.monotonic()
    opts = (build or options.build_options)(scenario, moment, scope, calls_log=rec["tools_called"])
    rec["advisor_latency_ms"] += int((time.monotonic() - t0) * 1000)
    rec["options"] = [{"kind": o.kind, "sentence": o.sentence} for o in opts]
    if not opts:
        rec["flags"].append("no_options")
        rec["final_reply"] = moment["reply"]
        return rec
    prior = [(t["text"], t["reply"]) for t in scenario_turns[:-1]][-3:]
    lines = ["Last exchanges:"] + [f"Customer: {u}\nAssistant: {b}" for u, b in prior] if prior else ["Last exchanges: (none)"]
    lines += ["", f"New message from the customer: {moment['text']}", "", f"Current reply:\n{moment['reply']}", "", "OPTIONS:"]
    lines += [f"OPT{i + 1} ({o.kind}): {o.sentence}" for i, o in enumerate(opts)]
    out = provider.complete([{"role": "user", "content": "\n".join(lines)}], [], SYSTEM)
    rec["calls"].append({"latency_ms": out["latency_ms"], "usage": out["usage"], "cost_usd": out["cost_usd"], "tool_calls": []})
    rec["advisor_latency_ms"] += out["latency_ms"]
    rec["cost_usd"] += out["cost_usd"]
    text = out["text"].strip()
    if not text or text.upper().strip(". ") == "NONE":
        rec["flags"].append("none_chosen")
        rec["final_reply"] = moment["reply"]
        return rec
    holders = re.findall(r"\{OPT(\d+)\}", text)
    if len(holders) > 1 or any(not (1 <= int(h) <= len(opts)) for h in holders):
        rec["flags"] += ["bad_placeholder", "guard_fallback"]
        rec["final_reply"] = moment["reply"]
        return rec
    words = re.sub(r"\{OPT\d+\}", " ", text)
    g = fact_guard.check_reply(words, [], [])
    rec["guard"].append({"ok": g.ok, "unsupported": g.unsupported, "leaks": g.leaks})
    if not g.ok:
        rec["flags"].append("guard_fallback")
        rec["final_reply"] = moment["reply"]
        return rec
    if holders:
        chosen = opts[int(holders[0]) - 1]
        rec["chosen"] = {"index": int(holders[0]), "kind": chosen.kind}
        text = text.replace("{OPT%s}" % holders[0], chosen.sentence)
    rec["final_reply"] = (moment["reply"].rstrip() + "\n\n" + text.strip()).strip()
    return rec
