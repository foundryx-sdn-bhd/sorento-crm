"""CLI: python -m scripts.chatbot_eval --data-dir DIR [options]."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone

try:
    from psycopg2 import Error as _PgError

    _DB_ERRORS: tuple = (_PgError,)
except ImportError:
    _DB_ERRORS = ()
from pathlib import Path

from .budget import PRICES, Budget, cost_usd
from .cases import DataDirInRepoError, Case, _repo_root, load_cases, parse_case
from .engine import load_engine
from .engine import Outcome
from .judge import CALIBRATION_MIN, GuardedJudge, calibrate, load_judge
from .report import build_report, render_summary
from .trace import DbContactReset, DbTraceReader
from .grade import grade, score_layers
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
    p.add_argument("--topics")
    p.add_argument("--ids-file")
    p.add_argument("--estimate-only", action="store_true")
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
                "trace_digest": r.trace_digest, "replay_digests": r.replay_digests, "layers": r.layers, "needs_judge": bool(r.verdict.needs_judge),
            }
            fh.write(json.dumps(row) + "\n")


def _git_provenance() -> tuple[str | None, bool | None]:
    """HEAD sha (EVAL_APP_SHA overrides) and whether tracked files are modified; None when git is unavailable."""
    here = Path(__file__).resolve().parent
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=here, text=True).strip()
        dirty = bool(
            subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=here, text=True).strip()
        )
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("EVAL_APP_SHA") or None, None
    return os.environ.get("EVAL_APP_SHA") or sha, dirty


def _git_provenance_head() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _server_sha(base_url: str) -> str | None:
    """The git_sha the server's /health names, or None when it is unreachable or silent."""
    import httpx

    try:
        resp = httpx.get(base_url.rstrip("/") + "/health", timeout=30)
        sha = resp.json().get("git_sha")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    return sha.strip() if isinstance(sha, str) and sha.strip() and sha.strip().lower() != "unknown" else None


PROVENANCE_FIELDS = ("code_sha", "prompt_sha256", "prompt_version", "model", "reasoning_effort")


def _provenance_seen(results: list) -> dict[str, list]:
    """Distinct value per provenance field across every result's parser digest, in first-seen order."""
    seen: dict[str, list] = {f: [] for f in PROVENANCE_FIELDS}
    for r in results:
        parser = (r.trace_digest or {}).get("parser")
        if not parser:
            continue
        for f in PROVENANCE_FIELDS:
            if parser.get(f) not in seen[f]:
                seen[f].append(parser.get(f))
    return seen


def _select(cases: list[Case], topics: str | None, ids_file: str | None) -> list[Case]:
    if not topics and not ids_file:
        return cases
    keep: set[str] = set()
    for prefix in [t.strip() for t in (topics or "").split(",") if t.strip()]:
        hit = [c.id for c in cases if c.topic_code == prefix or (c.topic_code or "").startswith(prefix + ".")]
        if not hit:
            raise ValueError(f"unknown topic {prefix!r}: no case matches")
        keep.update(hit)
    if ids_file:
        raw = json.loads(Path(ids_file).read_text())
        ids = raw.get("ids", []) if isinstance(raw, dict) else raw
        known = {c.id for c in cases}
        for cid in ids:
            if cid not in known:
                raise ValueError(f"unknown case id {cid!r}")
        keep.update(ids)
    return [c for c in cases if c.id in keep]


def _load_baseline(path: str | None, results: list) -> dict | None:
    """A report.json file as is; a run dir is cut to the rows this run also has."""
    if not path:
        return None
    p = Path(path)
    if not p.is_dir():
        return json.loads(p.read_text())
    base = json.loads((p / "report.json").read_text())
    mine = {(r.case_id, r.variant_kind) for r in results}
    rows = [
        r for r in (json.loads(x) for x in (p / "results.jsonl").read_text().splitlines() if x.strip())
        if (r["case_id"], r.get("variant_kind")) in mine
    ]
    passed = [r["label"] in ("correct", "good_clarify") for r in rows]
    strict = [ok and not r.get("needs_judge") for ok, r in zip(passed, rows)]
    topics: dict[str, list[bool]] = {}
    for ok, r in zip(passed, rows):
        topics.setdefault(str(r.get("topic_code")), []).append(ok)
    return {
        **base,
        "pass_rate": sum(passed) / len(passed) if passed else None,
        "strict_pass_rate": sum(strict) / len(strict) if strict else None,
        "pass_rate_by_topic": {t: sum(v) / len(v) for t, v in topics.items()},
        "_restricted": True,
        "_matched": len(rows),
    }


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
        final_text = next(
            (v["text"] for v in case.variants if v.get("kind") == row.get("variant_kind")), case.message["text"]
        )
        user_texts = [h["text"] for h in case.history if h.get("role") == "user"] + [final_text]
        verdict = grade(case.expect, out, row.get("previous_bot_reply"), user_texts)
        results.append(
            CaseResult(
                case_id=case.id, split=case.split, topic_code=case.topic_code, language=case.language,
                variant_kind=row.get("variant_kind"), verdict=verdict,
                layers=score_layers(case.expect, row.get("trace_digest"), verdict),
                outcome=out, latency_ms=row.get("latency_ms", 0), tokens=row.get("tokens") or {},
                previous_bot_reply=row.get("previous_bot_reply"),
                trace_digest=row.get("trace_digest"), replay_digests=row.get("replay_digests") or [],
            )
        )
    src_path = Path(args.regrade) / "report.json"
    src = json.loads(src_path.read_text()) if src_path.exists() else {}
    run = RunResult(results=results, stopped_reason=None, wall_s=src.get("wall_s", 0.0))
    baseline = _load_baseline(args.baseline, results)
    report = build_report(run, baseline, args.budget_model)
    report.update(
        engine=src.get("engine", "regrade"), split=args.split, variants=src.get("variants", args.variants),
        run_id=src.get("run_id", f"regrade-{uuid.uuid4().hex[:8]}"),
        prompt_version=src.get("prompt_version", "stored"), cases=len(results), regrade_dropped=dropped,
        started_at=src.get("started_at", datetime.now(timezone.utc).isoformat()),
        git_sha=src.get("git_sha"), git_dirty=src.get("git_dirty"),
        **{k: src[k] for k in ("prompt", "prompt_versions_seen", "prompt_mismatch") if k in src},
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
    try:
        selected = _select(loaded.cases, args.topics, args.ids_file)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    calls = sum(
        (1 + sum(1 for h in c.history if h.get("role") == "user"))
        * (1 + {"none": 0, "one": min(1, len(c.variants)), "all": len(c.variants)}[args.variants])
        for c in selected
    )
    per_call = cost_usd({"prompt": 40_000, "cached": 38_000, "output": 400}, args.budget_model)
    print(f"selected {len(selected)} cases, ~{calls} calls, ~${calls * per_call:.2f}", file=sys.stderr)
    if args.estimate_only:
        return 0
    loaded.cases = selected
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
    server_sha = None
    if args.engine == "http":
        server_sha = _server_sha(args.base_url)
        if not server_sha:
            print("refusing to start: /health reported no git_sha, so the code under test is unknown", file=sys.stderr)
            return 2
    engine = load_engine(args.engine, **kw)
    budget = Budget(args.budget_usd, args.budget_model) if args.budget_usd is not None else None
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
    git_sha, git_dirty = _git_provenance()
    runner_sha = git_sha
    if server_sha:
        runner_sha = _git_provenance_head()
        git_sha = server_sha
    provenance = _provenance_seen(run.results)
    baseline = _load_baseline(args.baseline, run.results)
    report = build_report(run, baseline, args.budget_model, "calibrated" if calibrated else "uncalibrated")
    report.update(
        engine=args.engine, split=args.split, variants=args.variants, run_id=run_id,
        prompt_version=args.prompt_version or "label", cases=len(loaded.cases), started_at=started_at,
        selection={"topics": args.topics.split(",") if args.topics else None, "ids_file": args.ids_file, "cases": len(loaded.cases)},
        prompt=prompt, prompt_versions_seen=seen, git_sha=git_sha, git_dirty=git_dirty,
        runner_sha=runner_sha, provenance=provenance,
        provenance_missing=not any(
            ((r.trace_digest or {}).get("parser") or {}).get(f) is not None
            for r in run.results for f in PROVENANCE_FIELDS
        ),
        provenance_mixed=any(len(v) > 1 for v in provenance.values()),
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
