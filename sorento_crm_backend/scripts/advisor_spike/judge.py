"""LLM judge (claude-opus-5-5, effort low, structured output) per scenario x arm.

python -m scripts.advisor_spike.judge --out DIR --budget-usd N [--rulings PATH] [--only results_C_sonnet]
Reads <out>/results_*.jsonl (v1 record per scenario from results_v1.jsonl), writes <out>/judged_<name>.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MOVES = ["none", "alternative", "promo", "accessory", "counter_qty", "counter_eta", "project", "other"]
SCHEMA = {
    "type": "object",
    "properties": {
        "tools_ok": {"type": "object", "properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}},
                     "required": ["ok", "why"], "additionalProperties": False},
        "made_up_facts": {"type": "array", "items": {"type": "string"}},
        "rule_violations": {"type": "array", "items": {
            "type": "object", "properties": {"ruling": {"type": "string"}, "why": {"type": "string"}},
            "required": ["ruling", "why"], "additionalProperties": False}},
        "salesman_move": {"type": "string", "enum": MOVES},
        "helpfulness": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "notes": {"type": "string"},
    },
    "required": ["tools_ok", "made_up_facts", "rule_violations", "salesman_move", "helpfulness", "notes"],
    "additionalProperties": False,
}
SYSTEM = """You grade one WhatsApp reply of a furniture-hardware wholesaler's sales bot.
You get the conversation, the final customer message ("moment"), what the scenario expects, the reply, and the
tools the bot called with their (truncated) results.
- tools_ok: did the bot call tools sufficient and appropriate for the ask (and none that were irrelevant)? why = one line.
- made_up_facts: every code, quantity, price, date, policy or promise in the reply that no tool result or the
  customer's own words supports. Empty list when none. A reply that cites a value the tool showed is NOT made up.
- rule_violations: each owner ruling the reply breaks, ruling = the ruling file/rule name from the rules list below,
  why = one line. Empty when none.
- salesman_move: the ONE helpful sales move the reply makes beyond the bare answer: alternative (in-stock
  substitute), promo (live promotion), accessory (matching seat cover / mirror / shelf), counter_qty (what can be
  served now), counter_eta (incoming date), project (project/quotation help), other, or none.
- helpfulness: 1 (useless or wrong) to 5 (exactly what a good salesman would send), judged against the expectation.
Be strict and concrete. Output only the JSON."""


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "...(cut)"


def build_prompt(v1: dict[str, Any], reply: str, tools: list[dict[str, Any]], rulings: str) -> str:
    turns = v1["turns"]
    hist = "\n".join(f"Customer: {t['text']}\nBot: {_trunc(t['reply'] or '', 400)}" for t in turns[:-1]) or "(none)"
    tl = []
    for t in tools[:8]:
        tl.append(f"- {t.get('name')} {json.dumps(t.get('args'), ensure_ascii=False, default=str)[:300]} -> "
                  f"{_trunc(str(t.get('result') or '(result not recorded, %s chars)' % t.get('result_chars')), 1500)}")
    return (f"RULES:\n{rulings}\n\nSCENARIO kind={v1.get('kind')}\nHistory:\n{hist}\n\nMoment (customer): {turns[-1]['text']}\n"
            f"Expectation: {json.dumps(v1.get('expect'), ensure_ascii=False, default=str)}\n\nREPLY:\n{reply}\n\n"
            f"TOOLS CALLED:\n" + ("\n".join(tl) or "(none)"))


def judge_one(provider: Any, v1: dict[str, Any], reply: str, tools: list[dict[str, Any]], rulings: str) -> dict[str, Any]:
    out = provider.complete_json(build_prompt(v1, reply, tools, rulings), SCHEMA, system=SYSTEM)
    return {**out["data"], "judge_cost_usd": out["cost_usd"], "judge_usage": out["usage"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--rulings")
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    from scripts.advisor_spike import env

    env.bootstrap()
    from scripts.advisor_spike import providers
    from scripts.advisor_spike.run import BudgetExceeded, Ledger, append

    out = Path(args.out)
    v1s = {}
    for l in (out / "results_v1.jsonl").read_text().splitlines():
        if l.strip():
            r = json.loads(l)
            v1s[r["scenario_id"]] = r
    rulings = Path(args.rulings).read_text() if args.rulings else (Path(__file__).parent / "prompts" / "advisor_system.md").read_text()
    ledger = Ledger(out / "spend.jsonl", args.budget_usd)
    inner = providers.make_provider("opus")  # complete_json is budget-checked and booked inline below
    try:
        for f in sorted(out.glob("results_*.jsonl")):
            name = f.stem
            if args.only and name != args.only:
                continue
            jpath = out / f"judged_{name.removeprefix('results_')}.jsonl"
            done = {json.loads(l)["scenario_id"] for l in jpath.read_text().splitlines() if l.strip()} if jpath.exists() else set()
            for l in f.read_text().splitlines():
                if not l.strip():
                    continue
                r = json.loads(l)
                sid = r["scenario_id"]
                if sid in done or sid not in v1s:
                    continue
                v1 = v1s[sid]
                if name == "results_v1":
                    reply, tools = v1["moment"]["reply"], v1["moment"].get("tools") or []
                else:
                    reply, tools = r.get("final_reply") or "", r.get("tools_called") or []
                ledger.check(0.03)
                j = judge_one(inner, v1, reply, tools, rulings)
                ledger.add(f"judge:{name}:{sid}", j["judge_cost_usd"])
                append(jpath, {"scenario_id": sid, "arm_file": name, **j})
                print(f"judged {name} {sid} help={j['helpfulness']} move={j['salesman_move']}", flush=True)
    except BudgetExceeded as exc:
        print(f"STOPPED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
