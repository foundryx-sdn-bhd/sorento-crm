"""Replay cases through an engine and grade the final turn."""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .budget import Budget
from .cases import Case
from .engine import Engine, Outcome
from .grade import Verdict, grade
from .judge import Judge, apply_judge


class NotDryRunError(RuntimeError):
    """A turn came back without the dry-run stamp; the whole run aborts. `run` holds the partial result."""

    run: "RunResult | None" = None


class BudgetStop(Exception):
    """Spend cap reached before this turn could start."""


@dataclass
class CaseResult:
    case_id: str
    split: str
    topic_code: str | None
    language: str | None
    variant_kind: str | None
    verdict: Verdict
    outcome: Outcome
    latency_ms: int
    tokens: dict
    previous_bot_reply: str | None = None


@dataclass
class RunResult:
    results: list[CaseResult] = field(default_factory=list)
    stopped_reason: str | None = None
    wall_s: float = 0.0


def run_case(
    engine: Engine,
    case: Case,
    text: str,
    run_id: str,
    *,
    variant_kind: str | None = None,
    budget: Budget | None = None,
    judge: Judge | None = None,
    judge_calibrated: bool = False,
) -> CaseResult:
    total = {"prompt": 0, "cached": 0, "output": 0}
    state: dict | None = {}
    prev_bot = next((h.get("text") for h in reversed(case.history) if h.get("role") == "bot"), None)
    reset = getattr(engine, "reset_contact", None)
    if reset is not None:
        reset(case)

    def spend(tokens: dict) -> None:
        for k in total:
            total[k] += int(tokens.get(k, 0) or 0)
        if budget is not None:
            budget.add(tokens)

    def step(turn_text: str) -> Outcome:
        if budget is not None and budget.exceeded:
            raise BudgetStop()
        out = engine.turn(case, turn_text, state, run_id)
        if not out.dry_run:
            who = "" if case.split == "hidden" else f" {case.id}"
            raise NotDryRunError(f"case{who}: turn was not a dry run")
        spend(out.tokens)
        return out

    for h in case.history:
        if h.get("role") == "user":
            out = step(h["text"])
            if out.state is not None:
                state = out.state
            prev_bot = out.reply_text
    final = step(text)
    verdict = grade(case.expect, final, prev_bot)
    if verdict.needs_judge and judge is not None and judge_calibrated:
        result = judge.score(case, final)
        if result is not None:
            verdict = apply_judge(verdict, result, True)
    return CaseResult(
        case_id=case.id, split=case.split, topic_code=case.topic_code, language=case.language,
        variant_kind=variant_kind, verdict=verdict, outcome=final, latency_ms=final.latency_ms, tokens=total,
        previous_bot_reply=prev_bot,
    )


def run_all(
    engine: Engine,
    cases: list[Case],
    *,
    variants: str = "none",
    max_parallel: int = 1,
    budget: Budget | None = None,
    run_id: str,
    seed: int = 0,
    judge: Judge | None = None,
    judge_calibrated: bool = False,
) -> RunResult:
    rng = random.Random(seed)
    jobs: list[tuple[Case, str, str | None]] = []
    for case in cases:
        jobs.append((case, case.message["text"], None))
        if variants == "all":
            jobs += [(case, v["text"], v.get("kind")) for v in case.variants]
        elif variants == "one" and case.variants:
            v = rng.choice(case.variants)
            jobs.append((case, v["text"], v.get("kind")))

    stopped = False
    aborted = False
    results: list[CaseResult | None] = [None] * len(jobs)

    def work(group: list[tuple[int, tuple[Case, str, str | None]]]) -> None:
        nonlocal stopped, aborted
        for idx, (case, text, kind) in group:
            if aborted:
                return
            scoped = f"{run_id}:{case.id}" + (f":{kind}" if kind else "")
            try:
                results[idx] = run_case(
                    engine, case, text, scoped, variant_kind=kind, budget=budget,
                    judge=judge, judge_calibrated=judge_calibrated,
                )
            except BudgetStop:
                stopped = True
            except NotDryRunError:
                aborted = True
                raise
            except Exception as exc:  # noqa: BLE001
                results[idx] = CaseResult(
                    case_id=case.id, split=case.split, topic_code=case.topic_code, language=case.language,
                    variant_kind=kind, verdict=Verdict("error", [f"harness: {type(exc).__name__}"]),
                    outcome=Outcome(reply_text=""), latency_ms=0, tokens={"prompt": 0, "cached": 0, "output": 0},
                )

    # Engine memory is keyed by contact, so cases sharing a respond_id run one after another.
    groups: dict[str, list] = {}
    for idx, job in enumerate(jobs):
        groups.setdefault(str(job[0].contact.get("respond_id", "")), []).append((idx, job))
    started = time.monotonic()
    failure: NotDryRunError | None = None
    interrupted = False
    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        try:
            list(pool.map(work, groups.values()))
        except NotDryRunError as exc:
            failure = exc
        except KeyboardInterrupt:
            aborted = True
            interrupted = True
    done = [r for r in results if r is not None]
    if failure is not None:
        reason = "not_dry_run"
    elif interrupted:
        reason = "interrupted"
    elif stopped or (budget is not None and budget.exceeded and len(done) < len(jobs)):
        reason = "budget"
    else:
        reason = None
    run = RunResult(results=done, stopped_reason=reason, wall_s=time.monotonic() - started)
    if failure is not None:
        failure.run = run
        raise failure
    return run
