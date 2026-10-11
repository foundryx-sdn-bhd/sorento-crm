"""CLI: python -m scripts.chatbot_eval --data-dir DIR [options]."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import uuid
from datetime import datetime, timezone

try:
    from psycopg2 import Error as _PgError

    _DB_ERRORS: tuple = (_PgError,)
except ImportError:
    _DB_ERRORS = ()
from pathlib import Path

from .budget import PRICES, Budget
from .cases import DataDirInRepoError, Case, _repo_root, load_cases, parse_case
from .engine import load_engine
from .engine import Outcome
from .judge import CALIBRATION_MIN, GuardedJudge, calibrate, load_judge
from .report import build_report, render_summary
from .trace import DbContactReset, DbTraceReader
from .grade import grade
from .prompts import resolve_prompt
from .runner import CaseResult, NotDryRunError, RunResult, run_all


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
    p.add_argument("--prompt-version")
    p.add_argument("--regrade", metavar="RUN_DIR")
    p.add_argument("--judge-budget-usd", type=float, default=2.0)
    p.add_argument("--baseline")
    p.add_argument("--out")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--judge", default="none", help="openai, anthropic, none or module:Class; sends case text to the provider")
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
                "branch_kind": r.outcome.branch_kind, "tool_errors": r.outcome.tool_errors, "lane_notes": r.outcome.lane_notes,
                "delegated": (r.outcome.raw or {}).get("reply") is None and bool((r.outcome.raw or {}).get("delegate")),
                "previous_bot_reply": r.previous_bot_reply,
                "trace_digest": r.trace_digest, "replay_digests": r.replay_digests,
            }
            fh.write(json.dumps(row) + "\n")


def _inside_repo(path: str) -> bool:
    repo = _repo_root()
    target = Path(path).resolve()
    return repo is not None and (target == repo or repo in target.parents)


def _calibration_items(data_dir: str) -> list[tuple[Case, Outcome, bool]]:
    items = []
    for f in sorted((Path(data_dir) / "calibration").glob("*.json")):
        raw = json.loads(f.read_text())
        items.append((parse_case(raw["case"]), Outcome(reply_text=raw["reply_text"]), bool(raw["human_passed"])))
    return items


def _regrade(args, cases: list[Case]) -> int:
    """Grade stored replies again against the current cases; no engine is built or called."""
    if args.out and _inside_repo(args.out):
        print("--out must be outside the git work tree", file=sys.stderr)
        return 2
    by_id = {c.id: c for c in cases}
    rows = [
        json.loads(x) for x in (Path(args.regrade) / "results.jsonl").read_text().splitlines() if x.strip()
    ]
    results, dropped = [], 0
    for row in rows:
        case = by_id.get(row["case_id"])
        if case is None:
            dropped += 1
            continue
        raw = {"reply": None, "delegate": "stored"} if row.get("delegated") else {}
        errors, notes = [], list(row.get("lane_notes") or [])
        for entry in row.get("tool_errors") or []:
            if entry.startswith("None: ") and "returned HTTP" not in entry:
                notes.append(entry[len("None: ") :])
            else:
                errors.append(entry)
        out = Outcome(
            reply_text=row.get("reply_text") or "", branch_kind=row.get("branch_kind"),
            tool_errors=errors, lane_notes=notes, latency_ms=row.get("latency_ms", 0), raw=raw,
        )
        results.append(
            CaseResult(
                case_id=case.id, split=case.split, topic_code=case.topic_code, language=case.language,
                variant_kind=row.get("variant_kind"), verdict=grade(case.expect, out, row.get("previous_bot_reply")),
                outcome=out, latency_ms=row.get("latency_ms", 0), tokens=row.get("tokens") or {},
                previous_bot_reply=row.get("previous_bot_reply"),
                trace_digest=row.get("trace_digest"), replay_digests=row.get("replay_digests") or [],
            )
        )
    src_path = Path(args.regrade) / "report.json"
    src = json.loads(src_path.read_text()) if src_path.exists() else {}
    run = RunResult(results=results, stopped_reason=None, wall_s=src.get("wall_s", 0.0))
    baseline = json.loads(Path(args.baseline).read_text()) if args.baseline else None
    report = build_report(run, baseline, args.budget_model)
    report.update(
        engine=src.get("engine", "regrade"), split=args.split, variants=src.get("variants", args.variants),
        run_id=src.get("run_id", f"regrade-{uuid.uuid4().hex[:8]}"),
        prompt_version=src.get("prompt_version", "stored"), cases=len(results), regrade_dropped=dropped,
        started_at=src.get("started_at", datetime.now(timezone.utc).isoformat()),
        regraded_from=args.regrade, regraded_at=datetime.now(timezone.utc).isoformat(),
    )
    if args.out:
        _write(args.out, report, run)
    print(render_summary(report, args.split))
    return 0


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
    if args.regrade:
        return _regrade(args, loaded.cases)
    if args.out and _inside_repo(args.out):
        print("--out must be outside the git work tree", file=sys.stderr)
        return 2
    if args.engine == "http" and args.budget_usd is not None and not os.environ.get("EVAL_TRACE_DB_URL"):
        print("--budget-usd with the http engine needs EVAL_TRACE_DB_URL to see token use", file=sys.stderr)
        return 2
    kw = {"base_url": args.base_url, "api_key": os.environ.get("EXTERNAL_API_KEY", ""), "timeout": 120}
    if args.engine == "http":
        if os.environ.get("EVAL_TRACE_DB_URL"):
            kw["trace_reader"] = DbTraceReader(os.environ["EVAL_TRACE_DB_URL"])
        if os.environ.get("EVAL_RESET_DB_URL"):
            try:
                kw["contact_reset"] = DbContactReset(os.environ["EVAL_RESET_DB_URL"])
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
    prompt = {"id": None, "version": None, "template_md5": None, "source": "unknown"}
    trace_dsn = os.environ.get("EVAL_TRACE_DB_URL")
    if args.prompt_version and not trace_dsn:
        print(f"--prompt-version {args.prompt_version} needs EVAL_TRACE_DB_URL to resolve it", file=sys.stderr)
        return 2
    if trace_dsn:
        try:
            prompt = resolve_prompt(trace_dsn, args.prompt_version)
        except (LookupError, ImportError, *_DB_ERRORS) as exc:
            if args.prompt_version:
                print(f"cannot resolve --prompt-version {args.prompt_version}: {type(exc).__name__}", file=sys.stderr)
                return 2
            print(f"production label not resolved: {type(exc).__name__}", file=sys.stderr)
    if args.prompt_version:
        kw["prompt_version"] = prompt["id"]
    judge = load_judge(args.judge)
    if judge is not None:
        try:
            judge = GuardedJudge(judge, args.judge_budget_usd)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    engine = load_engine(args.engine, **kw)
    budget = Budget(args.budget_usd, args.budget_model) if args.budget_usd is not None else None
    baseline = json.loads(Path(args.baseline).read_text()) if args.baseline else None
    calibrated = False
    if judge is not None:
        items = _calibration_items(args.data_dir)
        agreement = calibrate(judge, items)
        calibrated = bool(items) and agreement >= CALIBRATION_MIN
    run_id = f"eval-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        run = run_all(
            engine, loaded.cases, variants=args.variants, max_parallel=args.max_parallel, budget=budget,
            run_id=run_id, seed=args.seed, judge=judge if calibrated else None,
            judge_calibrated=calibrated,
        )
    except NotDryRunError as exc:
        print(f"aborted: {exc}", file=sys.stderr)
        run = exc.run
    seen: dict[str, int] = {}
    for r in run.results:
        parser = (r.trace_digest or {}).get("parser")
        if parser:
            key = "none" if parser.get("prompt_version") is None else str(parser["prompt_version"])
            seen[key] = seen.get(key, 0) + 1
    mismatch = prompt["source"] != "unknown" and any(k != str(prompt["version"]) for k in seen)
    report = build_report(run, baseline, args.budget_model, "calibrated" if calibrated else "uncalibrated")
    report.update(
        engine=args.engine, split=args.split, variants=args.variants, run_id=run_id,
        prompt_version=args.prompt_version or "label", cases=len(loaded.cases), started_at=started_at,
        prompt=prompt, prompt_versions_seen=seen,
        judge_stopped=judge.stopped if judge is not None else None,
        judge_usd=judge.budget.spent if judge is not None else 0.0,
    )
    if mismatch:
        report["prompt_mismatch"] = True
    if args.out:
        _write(args.out, report, run)
    print(render_summary(report, args.split))
    if mismatch:
        print(f"prompt mismatch: resolved version {prompt['version']}, traces show {sorted(seen)}", file=sys.stderr)
    code = {"not_dry_run": 4, "budget": 3, "interrupted": 130}.get(run.stopped_reason, 0)
    return code or (5 if mismatch else 0)


if __name__ == "__main__":
    sys.exit(main())
