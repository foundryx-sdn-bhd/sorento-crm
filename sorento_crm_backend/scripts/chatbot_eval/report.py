"""Aggregate a run into a report dict and a text-free markdown summary."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from .budget import cost_usd
from .runner import CaseResult, RunResult

GRADER_VERSION = 8  # bump on any grading-rule change
PASS = ("correct", "good_clarify")


def _rate(results: list[CaseResult]) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if r.verdict.label in PASS) / len(results)


def _strict(results: list[CaseResult]) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if r.verdict.label in PASS and not r.verdict.needs_judge) / len(results)


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _group(results: list[CaseResult], attr: str) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        out[str(getattr(r, attr))][r.verdict.label] += 1
    return {k: dict(v) for k, v in out.items()}


LAYER_ORDER = ("understood", "resolved", "tool", "reply")


def _by_layer(results: list[CaseResult]) -> dict:
    out = {}
    for layer in LAYER_ORDER:
        counts = {s: 0 for s in ("pass", "fail", "na", "no_trace")}
        for r in results:
            counts[r.layers.get(layer, "na")] += 1
        scored = counts["pass"] + counts["fail"]
        out[layer] = {**counts, "pass_rate": counts["pass"] / scored if scored else None}
    return out


def _first_failing(results: list[CaseResult]) -> dict:
    out = {layer: 0 for layer in LAYER_ORDER}
    for r in results:
        if r.layers.get("reply") != "fail":
            continue
        first = next((layer for layer in LAYER_ORDER if r.layers.get(layer) == "fail"), "reply")
        out[first] += 1
    return out


def build_report(
    run: RunResult, baseline: dict | None = None, model: str = "gpt-5.4-mini", judge_status: str = "uncalibrated"
) -> dict:
    res = run.results
    originals = [r for r in res if r.variant_kind is None]
    variants = [r for r in res if r.variant_kind is not None]
    tokens = {k: sum(int(r.tokens.get(k, 0) or 0) for r in res) for k in ("prompt", "cached", "output")}
    by_topic_rate = {}
    for topic in {str(r.topic_code) for r in res}:
        by_topic_rate[topic] = _rate([r for r in res if str(r.topic_code) == topic])
    lats = [r.latency_ms for r in res]
    report = {
        "by_topic": _group(res, "topic_code"),
        "by_language": _group(res, "language"),
        "underfit": _rate(originals),
        "gap_dev_hidden": _diff(
            _rate([r for r in originals if r.split == "dev"]), _rate([r for r in originals if r.split == "hidden"])
        ),
        "gap_original_variant": _diff(_rate(originals), _rate(variants)),
        "grader_version": GRADER_VERSION,
        "by_layer": _by_layer(res),
        "first_failing_layer": _first_failing(res),
        "pass_rate": _rate(res),
        "strict_pass_rate": _strict(res),
        "strict_underfit": _strict(originals),
        "judge_status": judge_status,
        "unverified": sum(1 for r in res if r.verdict.needs_judge),
        "pass_rate_by_topic": by_topic_rate,
        "tokens": tokens,
        "est_usd": cost_usd(tokens, model),
        "latency_p50_ms": _pct(lats, 0.5),
        "latency_p95_ms": _pct(lats, 0.95),
        "wall_s": run.wall_s,
        "stopped_reason": run.stopped_reason,
    }
    if baseline is not None:
        base_topics = baseline.get("pass_rate_by_topic", {})
        report["delta"] = {
            "total": _diff(report["pass_rate"], baseline.get("pass_rate")),
            "strict_total": _diff(report["strict_pass_rate"], baseline.get("strict_pass_rate")),
            "comparable": baseline.get("grader_version") == GRADER_VERSION,
            "restricted": bool(baseline.get("_restricted")),
            "matched": baseline.get("_matched"),
            "by_topic": {
                t: _diff(rate, base_topics.get(t)) for t, rate in by_topic_rate.items() if t in base_topics
            },
        }
    return report


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _table(title: str, groups: dict[str, dict[str, int]]) -> list[str]:
    lines = [f"### {title}", "", "| key | counts |", "| --- | --- |"]
    for key in sorted(groups):
        lines.append(f"| {key} | " + ", ".join(f"{k}: {v}" for k, v in sorted(groups[key].items())) + " |")
    return lines + [""]


def render_summary(report: dict, split: str) -> str:
    """Aggregates only: no message text, reply text or case ids."""
    t = report["tokens"]
    lines = [
        f"## Chatbot eval ({split})",
        "",
        f"- pass rate: {_fmt(report.get('pass_rate'))}",
        f"- strict pass rate (judge-free passes only): {_fmt(report.get('strict_pass_rate'))}",
        f"- underfit (originals pass rate): {_fmt(report['underfit'])}; strict {_fmt(report.get('strict_underfit'))}",
        f"- gap dev minus hidden: {_fmt(report['gap_dev_hidden'])}",
        f"- gap originals minus variants: {_fmt(report['gap_original_variant'])}",
        f"- judge: {report.get('judge_status', 'uncalibrated')}; unverified: {report.get('unverified', 0)}",
        f"- tokens: prompt {t['prompt']}, cached {t['cached']}, output {t['output']}; est. USD {report['est_usd']:.4f}",
        f"- latency p50 {report['latency_p50_ms']} ms, p95 {report['latency_p95_ms']} ms; wall {report['wall_s']:.1f} s",
    ]
    if report.get("stopped_reason"):
        lines.append(f"- stopped: {report['stopped_reason']}")
    delta = report.get("delta")
    if delta:
        lines.append(f"- delta total: {_fmt(delta['total'])}")
        lines += [f"- delta {k}: {_fmt(v)}" for k, v in sorted(delta["by_topic"].items())]
    lines.append("")
    return "\n".join(lines + _table("By topic", report["by_topic"]) + _table("By language", report["by_language"]))
