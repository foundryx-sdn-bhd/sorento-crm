"""Roll-up and markdown output for a battery run (UAC-8). Pure."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

PROVISIONAL_BELOW = 0.9
EXCERPT = 240


def conversation_result(turn_rows: list[dict[str, Any]]) -> str:
    return "pass" if turn_rows and all(r.get("ok") for r in turn_rows) else "fail"


def combine_reps(results: list[str]) -> str:
    if results and all(r == "pass" for r in results):
        return "pass"
    if results and all(r == "fail" for r in results):
        return "fail"
    return "flaky"


def build_results(convs: list[Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One result per conversation from the per-turn rows: {id, source, domain, audience, v2, v1,
    first_fail, classes}. A mode that was not run reads None."""
    by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_key[(r["conversation"], r["mode"], r.get("rep", 1))].append(r)
    out: list[dict[str, Any]] = []
    for c in convs:
        res: dict[str, Any] = {"id": c.id, "source": c.source, "domain": c.domain, "audience": c.audience,
                               "v2": None, "v1": None, "first_fail": None, "classes": []}
        for mode in ("v2", "v1"):
            reps = sorted({k[2] for k in by_key if k[0] == c.id and k[1] == mode})
            if not reps:
                continue
            per_rep = []
            for rep in reps:
                turns = sorted(by_key[(c.id, mode, rep)], key=lambda r: r["turn"])
                per_rep.append(conversation_result(turns))
                bad = next((t for t in turns if not t.get("ok")), None)
                if bad is not None and (res["first_fail"] is None or bad["turn"] < res["first_fail"]):
                    res["first_fail"] = bad["turn"]
                    res["classes"] = _classes(bad)
            res[mode] = combine_reps(per_rep)
        out.append(res)
    return out


def _classes(row: dict[str, Any]) -> list[str]:
    names = [c["check"] for c in row.get("checks") or []]
    judge = row.get("judge") or {}
    if judge.get("verdict") in ("fail", "judge_error"):
        names.extend(judge.get("classes") or [judge["verdict"]])
    return names


def _cell(v: str | None) -> str:
    return v if v else "-"


def _totals(results: list[dict[str, Any]], key: str) -> list[str]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        groups[r[key]].append(r)
    lines = [f"| {key} | n | v2 pass | v1 pass |", "| --- | --- | --- | --- |"]
    for name in sorted(groups):
        g = groups[name]
        v2 = [r for r in g if r["v2"]]
        v1 = [r for r in g if r["v1"]]
        lines.append(f"| {name} | {len(g)} | {sum(1 for r in v2 if r['v2'] == 'pass')}/{len(v2)} "
                     f"| {sum(1 for r in v1 if r['v1'] == 'pass')}/{len(v1)} |")
    return lines


def render_table(results: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    agreement = meta.get("judge_agreement")
    lines = ["# PV2 battery", ""]
    for mode in ("v2", "v1"):
        m = meta.get(mode) or {}
        lines.append(f"- {mode} prompt: id `{m.get('id')}`, version {m.get('number')}, md5 `{m.get('md5')}`")
    lines.append(f"- git: `{meta.get('git_sha')}`")
    if meta.get("mcp_path"):
        lines.append(f"- MCP package: {meta['mcp_path']}")
    if meta.get("embedding_key"):
        lines.append(f"- Embedding key: {meta['embedding_key']}")
    if agreement is None or agreement < PROVISIONAL_BELOW:
        shown = "not calibrated" if agreement is None else f"{agreement:.2f}"
        lines.append(f"- judge agreement: {shown} (PROVISIONAL, below {PROVISIONAL_BELOW})")
    else:
        lines.append(f"- judge agreement: {agreement:.2f}")
    lines += ["", "| conversation | source | domain | audience | v2 | v1 | first failing turn | class |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        first = r["first_fail"] if r.get("first_fail") is not None else "-"
        lines.append(f"| {r['id']} | {r['source']} | {r['domain']} | {r['audience']} | {_cell(r['v2'])} "
                     f"| {_cell(r['v1'])} | {first} | {', '.join(r.get('classes') or []) or '-'} |")
    for key in ("domain", "audience", "source"):
        lines += ["", f"## By {key}", ""] + _totals(results, key)
    return "\n".join(lines) + "\n"


def render_failures(rows: list[dict[str, Any]]) -> str:
    lines = ["# Failing turns", ""]
    if not rows:
        lines.append("None.")
    for r in rows:
        reply = (r.get("reply") or "").replace("\n", " ")
        if len(reply) > EXCERPT:
            reply = reply[:EXCERPT] + "..."
        lines += [f"## {r['conversation']} turn {r['turn']}" + (f" ({r['mode']})" if r.get("mode") else ""),
                  f"- text: {r.get('text')}", f"- reply: {reply}",
                  "- reasons: " + "; ".join(r.get("reasons") or []), f"- turn_id: {r.get('turn_id')}", ""]
    return "\n".join(lines) + "\n"
