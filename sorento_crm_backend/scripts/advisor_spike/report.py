"""Aggregate a run dir to markdown: python -m scripts.advisor_spike.report --out DIR [--write FILE]

Per arm x model: n, tools_ok %, made-up facts (judge), guard catches / fallbacks, rule violations, helpfulness
mean, salesman-move rate (salesman scenarios vs controls), p50/p95 latency (advisor-only and end-to-end; the
v1 parser latency of the turn is added for C, C2 and C2all because the advisor runs after v1's understanding
step in the measured shape; for v1 end-to-end is the whole console turn), tokens and cost per turn.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def pct(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def rate(rows: list[dict[str, Any]]) -> str:
    return f"{100 * sum(1 for r in rows if r['salesman_move'] != 'none') / len(rows):.0f}% ({len(rows)})" if rows else "n/a"


def summarize(name: str, rows: list[dict[str, Any]], judged: dict[str, dict[str, Any]], kinds: dict[str, str]) -> dict[str, Any]:
    n = len(rows)
    j = [judged[r["scenario_id"]] for r in rows if r["scenario_id"] in judged]
    v1 = name == "v1"
    adv_lat, e2e_lat, toks, cost = [], [], [], []
    catches = fallbacks = 0
    for r in rows:
        if v1:
            lat = r["moment"]["latency_ms"]
            adv_lat.append(lat)
            e2e_lat.append(lat)
            toks.append(sum((u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)
                            for u in r["moment"]["llm_usage"]))
            cost.append(r.get("parser_cost_usd", 0.0))
        else:
            adv_lat.append(r["advisor_latency_ms"])
            e2e_lat.append(r["advisor_latency_ms"] + (r.get("v1_parser_latency_ms") or 0))
            toks.append(sum(c["usage"]["input"] + c["usage"]["cache_read"] + c["usage"]["cache_write"] + c["usage"]["output"]
                            for c in r["calls"]))
            cost.append(r["cost_usd"])
            catches += 1 if r["guard"] and not r["guard"][0]["ok"] else 0
            fallbacks += 1 if ("guard_fallback" in r["flags"] or "no_subset" in r["flags"]) else 0
    sal = [x for x in j if kinds.get(x["scenario_id"]) == "salesman"]
    ctl = [x for x in j if kinds.get(x["scenario_id"]) == "control"]
    return {
        "name": name, "n": n, "judged": len(j),
        "tools_ok": f"{100 * sum(1 for x in j if x['tools_ok']['ok']) / len(j):.0f}%" if j else "n/a",
        "made_up": sum(len(x["made_up_facts"]) for x in j),
        "catches": catches, "fallbacks": fallbacks,
        "violations": sum(len(x["rule_violations"]) for x in j),
        "help": f"{statistics.mean(x['helpfulness'] for x in j):.2f}" if j else "n/a",
        "move_sal": rate(sal), "move_ctl": rate(ctl),
        "adv": f"{pct(adv_lat, .5) / 1000:.1f} / {pct(adv_lat, .95) / 1000:.1f}",
        "e2e": f"{pct(e2e_lat, .5) / 1000:.1f} / {pct(e2e_lat, .95) / 1000:.1f}",
        "tokens": f"{statistics.mean(toks):.0f}" if toks else "n/a",
        "cost": f"${statistics.mean(cost):.4f}" if cost else "n/a",
    }


def render(out: Path) -> str:
    v1rows = _load(out / "results_v1.jsonl")
    kinds = {r["scenario_id"]: r.get("kind") for r in v1rows}
    lines = ["| arm / model | n | tools_ok | made-up facts | guard catches | fallbacks | rule viol. | helpfulness | move rate salesman | move rate control | advisor p50/p95 s | end-to-end p50/p95 s | tokens/turn | cost/turn |",
             "|" + "---|" * 14]
    for f in sorted(out.glob("results_*.jsonl")):
        name = f.stem.removeprefix("results_")
        rows = _load(f)
        judged = {r["scenario_id"]: r for r in _load(out / f"judged_{name}.jsonl")}
        s = summarize(name, rows, judged, kinds)
        lines.append(f"| {name} | {s['n']} ({s['judged']} judged) | {s['tools_ok']} | {s['made_up']} | {s['catches']} | "
                     f"{s['fallbacks']} | {s['violations']} | {s['help']} | {s['move_sal']} | {s['move_ctl']} | {s['adv']} | "
                     f"{s['e2e']} | {s['tokens']} | {s['cost']} |")
    spend = _load(out / "spend.jsonl")
    lines += ["", f"Total spend: ${sum(r['cost_usd'] for r in spend):.3f} over {len(spend)} ledger entries.",
              "Latency: advisor = model + tool time of the advisor; end-to-end = advisor + the v1 parser/turn latency of that turn (v1 row: whole console turn)."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--write")
    args = ap.parse_args(argv)
    md = render(Path(args.out))
    if args.write:
        Path(args.write).write_text(md + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
