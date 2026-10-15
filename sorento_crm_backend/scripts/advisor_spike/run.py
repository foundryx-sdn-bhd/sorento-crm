"""CLI: python -m scripts.advisor_spike.run --arms v1,C,C2,C2all --models sonnet,mini --scenarios PATH
       [--ids S01,K02] --out DIR --budget-usd N [--dry-run]

Scenario file: .jsonl (one object per line) or .json (list). Object: {id, kind: salesman|control, audience:
staff|dealer|unlinked, history: [user msg, ...], moment: str, expect: {...}}. The v1 arm replays history + moment
through the real console turn (live v1 parser calls) and its record feeds every advisor arm of that scenario.
Results: <out>/results_v1.jsonl, <out>/results_<arm>_<model>.jsonl (resumable by scenario id), <out>/spend.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Dry-run estimates. Per advisor model call: input tokens (system + tools + conversation) by arm, 70% of
# them cache reads after the first call of a turn, ~350 output tokens; ~2.5 calls per turn.
EST_IN_TOKENS = {"C3": 1500, "C": 7000, "C2": 7000, "C2opus": 7000, "C2all": 28000}
EST_OUT_TOKENS = 350
EST_CACHED_FRACTION = 0.7
EST_CALLS_PER_TURN = 2.5
CALLS_PER_TURN = {"C3": 1.0}  # one model call, no tools
EST_V1_TURN_USD = 0.006
EST_V1_TURN_SECONDS = 6
EST_JUDGE_IN, EST_JUDGE_OUT = 3500, 350
MODEL_OF = {"sonnet": "claude-sonnet-5-5", "mini": "gpt-5.4-mini", "opus": "claude-opus-5-5"}
ARM_MODELS_DEFAULT = {"C2all": ["sonnet"], "C2opus": ["opus"]}


def est_call_usd(arm: str, model_key: str) -> float:
    from scripts.advisor_spike.providers import cost_usd

    n = EST_IN_TOKENS[arm]
    cached = int(n * EST_CACHED_FRACTION)
    return cost_usd(MODEL_OF[model_key], {"input": n - cached, "cache_read": cached, "output": EST_OUT_TOKENS})


def est_judge_usd() -> float:
    from scripts.advisor_spike.providers import cost_usd

    return cost_usd("claude-opus-5-5", {"input": EST_JUDGE_IN, "output": EST_JUDGE_OUT})


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path, budget: float) -> None:
        self.path, self.budget, self.lock = path, budget, threading.Lock()
        self.total = 0.0
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    self.total += json.loads(line).get("cost_usd", 0.0)

    def check(self, est: float) -> None:
        if self.total + est > self.budget:
            raise BudgetExceeded(f"spend {self.total:.3f} + est {est:.3f} would cross budget {self.budget:.2f}")

    def add(self, label: str, cost: float, **extra: Any) -> None:
        with self.lock:
            self.total += cost
            with self.path.open("a") as fh:
                fh.write(json.dumps({"ts": time.time(), "label": label, "cost_usd": cost, **extra}) + "\n")


class BudgetedProvider:
    def __init__(self, inner: Any, ledger: Ledger, label: str, est: float) -> None:
        self.inner, self.ledger, self.label, self.est = inner, ledger, label, est

    def complete(self, *a: Any, **k: Any) -> dict[str, Any]:
        self.ledger.check(self.est)
        out = self.inner.complete(*a, **k)
        self.ledger.add(self.label, out["cost_usd"], usage=out["usage"])
        self.est = max(self.est, out["cost_usd"])
        return out


def _normalize(r: dict[str, Any]) -> dict[str, Any]:
    r = dict(r)
    r["scenario_kind"] = r.get("kind")  # the miner's kind (alt_out_of_stock|promo|...|control)
    r["kind"] = "control" if r.get("kind") == "control" else "salesman"
    r["history"] = [h if isinstance(h, str) else h["user"] for h in (r.get("history") or [])]
    r["followup"] = bool(r.get("followup"))
    return r


def load_scenarios(path: str, ids: str | None, only_followups: bool = False) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml

        rows = yaml.safe_load(text)
    elif path.endswith(".json"):
        rows = json.loads(text)
    else:
        rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    rows = [_normalize(r) for r in rows]
    if ids:
        wanted = [i.strip() for i in ids.split(",") if i.strip()]
        missing = set(wanted) - {r["id"] for r in rows}
        if missing:
            raise SystemExit(f"unknown scenario ids: {sorted(missing)}")
        rows = [r for r in rows if r["id"] in wanted]
    if only_followups:
        rows = [r for r in rows if r["followup"]]
    return rows


def arm_models(arm: str, models: list[str]) -> list[str]:
    return ARM_MODELS_DEFAULT.get(arm, models) if arm in ARM_MODELS_DEFAULT else models


def arm_scenarios(arm: str, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in scenarios if s["followup"]] if arm == "C2opus" else scenarios


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(l)["scenario_id"] for l in path.read_text().splitlines() if l.strip()}


def append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def plan(scenarios: list[dict[str, Any]], arms: list[str], models: list[str], out: Path) -> dict[str, Any]:
    v1_todo = [s for s in scenarios if s["id"] not in done_ids(out / "results_v1.jsonl")]
    v1_turns = sum(len(s.get("history") or []) + 1 for s in v1_todo)
    adv = [(a, m) for a in arms if a != "v1" for m in arm_models(a, models)]
    lines = [f"scenarios: {len(scenarios)} ({sum(1 for s in scenarios if s['followup'])} followups)  arms: {arms}  models: {models}"]
    cost = 0.0
    tokens = 0
    total_calls = 0
    lines.append(f"v1 replay: {v1_turns} live console turns (parser calls), ~{v1_turns * EST_V1_TURN_SECONDS // 60} min, "
                 f"~${v1_turns * EST_V1_TURN_USD:.2f}")
    cost += v1_turns * EST_V1_TURN_USD
    judgments = len(scenarios) if "v1" in arms or adv else 0
    for a, m in adv:
        todo = [s for s in arm_scenarios(a, scenarios) if s["id"] not in done_ids(out / f"results_{a}_{m}.jsonl")]
        calls = len(todo) * CALLS_PER_TURN.get(a, EST_CALLS_PER_TURN)
        tk = calls * (EST_IN_TOKENS[a] + EST_OUT_TOKENS)
        c = calls * est_call_usd(a, m)
        lines.append(f"{a} x {m}: {len(todo)} turns, ~{calls:.0f} model calls, ~{tk / 1e6:.2f}M tokens, ~${c:.2f}")
        cost += c
        tokens += tk
        total_calls += calls
        judgments += len(todo)
    jc = judgments * est_judge_usd()
    lines.append(f"judge (opus, one per scenario x arm): {judgments} calls, ~{judgments * (EST_JUDGE_IN + EST_JUDGE_OUT) / 1e6:.2f}M tokens, ~${jc:.2f}")
    cost += jc
    lines.append(f"TOTAL: ~{total_calls:.0f} advisor calls + {judgments} judge calls + {v1_turns} v1 turns, "
                 f"~{(tokens) / 1e6:.1f}M advisor tokens, ~${cost:.2f}")
    return {"text": "\n".join(lines), "cost": cost}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="advisor_spike.run")
    ap.add_argument("--arms", default="v1,C,C2")
    ap.add_argument("--models", default="sonnet,mini")
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--ids")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-followups", action="store_true")
    args = ap.parse_args(argv)

    from scripts.advisor_spike import env

    env.bootstrap()  # DATABASE_URL guard (localhost + sorento_cagent_stack) before anything imports app
    env.harden_proxy()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    bad = [a for a in arms if a not in ("v1", "C", "C2", "C2all", "C2opus", "C3")] + [m for m in models if m not in ("sonnet", "mini")]
    if bad:
        raise SystemExit(f"unknown arm/model: {bad}")
    scenarios = load_scenarios(args.scenarios, args.ids, args.only_followups)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    p = plan(scenarios, arms, models, out)
    print(p["text"])
    if args.dry_run:
        print("dry run: no LLM call made")
        return 0
    ledger = Ledger(out / "spend.jsonl", args.budget_usd)
    if p["cost"] + ledger.total > args.budget_usd:
        print(f"WARNING: estimate ${p['cost']:.2f} + spent ${ledger.total:.2f} exceeds budget ${args.budget_usd:.2f}; will stop at the line")

    from scripts.advisor_spike import advisor, providers, v1_replay

    v1_replay.install_guard()
    v1_replay.check_lanes_on()
    adv_arms = [a for a in arms if a != "v1"]
    provs = {m: providers.make_provider(m) for m in sorted({m for a in adv_arms for m in arm_models(a, models)})}
    v1_path = out / "results_v1.jsonl"
    cached_v1 = {}
    if v1_path.exists():
        cached_v1 = {json.loads(l)["scenario_id"]: json.loads(l) for l in v1_path.read_text().splitlines() if l.strip()}
    try:
        for sc in scenarios:
            v1 = cached_v1.get(sc["id"])
            if v1 is None:
                n_turns = len(sc.get("history") or []) + 1
                ledger.check(n_turns * EST_V1_TURN_USD)
                v1 = v1_replay.replay_scenario(sc)
                v1["kind"], v1["expect"] = sc.get("kind"), sc.get("expect")
                v1["facts_note"], v1["scenario_kind"], v1["followup"] = sc.get("facts_note"), sc.get("scenario_kind"), sc["followup"]
                v1_cost = 0.0
                for t in v1["turns"]:
                    for u in t["llm_usage"]:
                        if u.get("prompt_tokens") is not None:
                            cached = u.get("cached_tokens") or 0
                            v1_cost += providers.cost_usd("gpt-5.4-mini", {
                                "input": max((u["prompt_tokens"] or 0) - cached, 0), "cache_read": cached,
                                "output": u.get("completion_tokens") or 0})
                v1["parser_cost_usd"] = v1_cost
                ledger.add(f"v1:{sc['id']}", v1_cost)
                append(v1_path, v1)
                cached_v1[sc["id"]] = v1
                print(f"v1 {sc['id']} done ({v1['moment']['latency_ms']} ms)", flush=True)
            for a in adv_arms:
                if sc not in arm_scenarios(a, [sc]):
                    continue
                for m in arm_models(a, models):
                    path = out / f"results_{a}_{m}.jsonl"
                    if sc["id"] in done_ids(path):
                        continue
                    prov = BudgetedProvider(provs[m], ledger, f"{a}:{m}:{sc['id']}", est_call_usd(a, m))
                    if a == "C3":
                        from scripts.advisor_spike import c3

                        provm = providers.MODEL_IDS[m]
                        rec = c3.run_c3(sc, provm, v1["turns"], v1["moment"], v1["scope"], provider=prov)
                    else:
                        rec = advisor.run_advisor(a, providers.MODEL_IDS[m], v1["turns"], v1["moment"], v1["scope"], provider=prov)
                    rec.update(scenario_id=sc["id"], kind=sc.get("kind"), model_key=m,
                               v1_parser_latency_ms=v1["moment"]["latency_ms"])
                    append(path, rec)
                    print(f"{a}/{m} {sc['id']} flags={rec['flags']} ${rec['cost_usd']:.4f}", flush=True)
    except BudgetExceeded as exc:
        print(f"STOPPED: {exc}")
        return 2
    print(f"done. spend ${ledger.total:.3f} of ${args.budget_usd:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
