"""CLI: python -m scripts.chatbot_eval --data-dir DIR [options]."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import uuid
from pathlib import Path

from .budget import PRICES, Budget
from .cases import DataDirInRepoError, load_cases
from .engine import load_engine
from .judge import load_judge
from .report import build_report, render_summary
from .runner import NotDryRunError, run_all


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chatbot_eval")
    p.add_argument("--data-dir", default=os.environ.get("EVAL_DATA_DIR"))
    p.add_argument("--split", choices=["dev", "hidden", "all"], default="all")
    p.add_argument("--variants", choices=["none", "one", "all"], default="none")
    p.add_argument("--engine", default="http")
    p.add_argument("--base-url", default=os.environ.get("EVAL_BASE_URL", "http://localhost:8000"))
    p.add_argument("--max-parallel", type=int, default=1)
    p.add_argument("--budget-usd", type=float)
    p.add_argument("--budget-model", choices=sorted(PRICES), default="gpt-5.4-mini")
    p.add_argument("--baseline")
    p.add_argument("--out")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--judge", choices=["openai", "anthropic", "none"], default="none")
    p.add_argument("--judge-calibrated", action="store_true")
    return p


def _write(out: str, report: dict, run) -> None:
    path = Path(out)
    if path.suffix == ".json":
        report_path, rows_path = path, path.with_suffix(".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
        report_path, rows_path = path / "report.json", path / "results.jsonl"
    report_path.write_text(json.dumps(report, indent=2))
    with rows_path.open("w") as fh:
        for r in run.results:
            row = {
                "case_id": r.case_id, "split": r.split, "topic_code": r.topic_code, "language": r.language,
                "variant_kind": r.variant_kind, "label": r.verdict.label, "reasons": r.verdict.reasons,
                "latency_ms": r.latency_ms, "tokens": r.tokens, "reply_text": r.outcome.reply_text,
            }
            fh.write(json.dumps(row) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.data_dir:
        print("--data-dir or EVAL_DATA_DIR is required", file=sys.stderr)
        return 2
    try:
        loaded = load_cases(args.data_dir, None if args.split == "all" else args.split)
    except DataDirInRepoError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if loaded.invalid:
        for path, reason in loaded.invalid:
            print(f"invalid case: {path}: {reason}", file=sys.stderr)
        return 2
    engine = load_engine(
        args.engine, base_url=args.base_url, api_key=os.environ.get("EXTERNAL_API_KEY", ""), timeout=120
    )
    budget = Budget(args.budget_usd, args.budget_model) if args.budget_usd is not None else None
    baseline = json.loads(Path(args.baseline).read_text()) if args.baseline else None
    try:
        run = run_all(
            engine, loaded.cases, variants=args.variants, max_parallel=args.max_parallel, budget=budget,
            run_id=f"eval-{uuid.uuid4().hex[:8]}", seed=args.seed, judge=load_judge(args.judge),
            judge_calibrated=args.judge_calibrated,
        )
    except NotDryRunError as exc:
        print(f"aborted: {exc}", file=sys.stderr)
        return 4
    report = build_report(run, baseline, args.budget_model)
    if args.out:
        _write(args.out, report, run)
    print(render_summary(report, args.split))
    return 3 if run.stopped_reason == "budget" else 0


if __name__ == "__main__":
    sys.exit(main())
