"""CP0 grader: one pure function per target slice over a persisted turn row (stdlib only).

`grade_turn(row, item)` reads `chatbot.turns` fields (`envelope`, `trace`, `response`) so grading is
the same on a recorded or a replayed turn and costs nothing. Contract: PLAN-eval-bench-cp0-16oct.md.
"""
from __future__ import annotations

import json
import re
from typing import Any

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CODE_RE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z][A-Z0-9-]{3,}\b")
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
DATE_DMY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
DATE_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
ORDINAL_RE = re.compile(r"^\s*\d{1,3}[.)]\s+", re.M)


# --------------------------------------------------------------------------- #
# Reading a row
# --------------------------------------------------------------------------- #


def _trace(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (row.get("trace") or []) if isinstance(r, dict)]


def _kind(row: dict[str, Any], kind: str) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for r in _trace(row):
        if r.get("kind") == kind:
            last = r
    return last


def _stage(row: dict[str, Any], stage: str) -> dict[str, Any]:
    for r in _trace(row):
        if r.get("stage") == stage:
            return r
    return {}


def reply_text(row: dict[str, Any]) -> str:
    rep = (row.get("response") or {}).get("reply")
    t = rep.get("text") if isinstance(rep, dict) else None
    return t if isinstance(t, str) else ""


def user_message(row: dict[str, Any]) -> str:
    m: Any = row.get("envelope") or {}
    for _ in range(6):
        if isinstance(m, dict) and isinstance(m.get("text"), str):
            return m["text"]
        m = m.get("message") if isinstance(m, dict) else None
    return ""


def _entities(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in ((_kind(row, "apply").get("verdict") or {}).get("entities") or []) if isinstance(e, dict)]


def _decision(row: dict[str, Any]) -> str | None:
    return ((_kind(row, "apply").get("decision") or {}).get("kind"))


def _tools(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in _trace(row) if r.get("kind") == "tool" and r.get("name")]


def _domains(row: dict[str, Any]) -> list[str]:
    return list((_stage(row, "routed").get("facts") or {}).get("domains") or [])


def _asking(row: dict[str, Any]) -> Any:
    return (_stage(row, "replied").get("facts") or {}).get("asking")


def _focus_codes(row: dict[str, Any]) -> set[str]:
    prods = (_kind(row, "state").get("focus") or {}).get("products") or []
    return {str(p.get("canonical_code")).upper() for p in prods if isinstance(p, dict) and p.get("canonical_code")}


def _positions(row: dict[str, Any]) -> list[int]:
    pick = _kind(row, "engine_pick")
    if pick.get("positions"):
        return list(pick["positions"])
    verdict = _kind(row, "apply").get("verdict") or {}
    pos = list(verdict.get("reference_positions") or [])
    pos += [p for p in ((verdict.get("open_question_answer") or {}).get("picked") or []) if isinstance(p, int)]
    return pos


def _dump(x: Any) -> str:
    return json.dumps(x, default=str, ensure_ascii=False)


def _norm(v: Any) -> str:
    return str(v).strip().lower() if v is not None else ""


# --------------------------------------------------------------------------- #
# Scope leaks and made-up facts (run on every graded turn)
# --------------------------------------------------------------------------- #


def scope_leaks(row: dict[str, Any], asker: dict[str, Any] | None, watch_customer_names=()) -> list[str]:
    reply = reply_text(row)
    low = reply.lower()
    asker = asker or {}
    linked = {_norm(n) for n in (asker.get("linked_customer_names") or [])}
    leaks: list[str] = []
    for name in watch_customer_names or ():
        if _norm(name) not in linked and _norm(name) and _norm(name) in low:
            leaks.append(f"customer outside the asker's links: {name}")
    if UUID_RE.search(reply):
        leaks.append("internal id in the reply")
    for field in asker.get("hidden_fields") or []:
        variants = {field.lower(), field.lower().replace("_", " ")}
        if any(v in low for v in variants):
            leaks.append(f"hidden field: {field.replace('_', ' ')}")
    return leaks


def _norm_date(d: int, m: int, y: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def _dates(text: str) -> tuple[set[str], str]:
    found: set[str] = set()

    def dmy(m: re.Match) -> str:
        mon = MONTHS.get(m.group(2)[:3].lower())
        if mon:
            found.add(_norm_date(int(m.group(1)), mon, int(m.group(3))))
        return " "

    def mdy(m: re.Match) -> str:
        mon = MONTHS.get(m.group(1)[:3].lower())
        if mon:
            found.add(_norm_date(int(m.group(2)), mon, int(m.group(3))))
        return " "

    def iso(m: re.Match) -> str:
        found.add(m.group(0))
        return " "

    text = DATE_ISO.sub(iso, text)
    text = DATE_DMY.sub(dmy, text)
    text = DATE_MDY.sub(mdy, text)
    return found, text


def _num(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def made_up_facts(row: dict[str, Any]) -> list[str]:
    reply = ORDINAL_RE.sub("", reply_text(row))
    source_parts = [user_message(row)] + [_dump(t.get("envelope")) for t in _tools(row)]
    truncated = any(isinstance(t.get("envelope"), dict) and t["envelope"].get("truncated") for t in _tools(row))
    source = "\n".join(source_parts)
    src_dates, src_rest = _dates(source)
    src_upper = source.upper()
    src_nums = {n for n in (_num(t) for t in NUM_RE.findall(src_rest)) if n is not None}
    src_nums |= {n for n in (_num(t) for t in re.findall(r"\d+", src_rest)) if n is not None}
    rep_dates, rep_rest = _dates(reply)
    codes = CODE_RE.findall(rep_rest)
    rep_rest = CODE_RE.sub(" ", rep_rest)
    findings: list[str] = []
    label = "unverifiable" if truncated else "made up"

    def add(token: str) -> None:
        finding = f"{label}: {token}"
        if finding not in findings:
            findings.append(finding)

    for c in codes:
        if c.upper() not in src_upper:
            add(c)
    for d in sorted(rep_dates):
        if d not in src_dates:
            add(d)
    for tok in NUM_RE.findall(rep_rest):
        n = _num(tok)
        if n is not None and n not in src_nums:
            add(tok)
    return findings


# --------------------------------------------------------------------------- #
# Slices. Each returns an ordered list of (check name, passed, sentence if failed).
# --------------------------------------------------------------------------- #


def _code_lookup(row, item):
    gold = item.get("gold") or {}
    code = str(gold.get("code") or "").upper()
    grounded = {str(e.get("canonical_code")).upper() for e in _entities(row) if e.get("canonical_code")}
    clarified = bool(_asking(row))
    checks = [("code", code in grounded, "The bot read a different code than the one asked.")]
    if gold.get("fact"):
        checks.append(("fact", gold["fact"] in _domains(row), "The bot looked up a different fact than the one asked."))
    if gold.get("ambiguous"):
        checks.append(("clarified", clarified, "The code was ambiguous but the bot did not ask which one."))
        return checks
    ran = any(code in _dump(t.get("args")).upper() or code in _dump(t.get("envelope")).upper() for t in _tools(row))
    checks += [
        ("tool_ran", ran, "No lookup ran for that code."),
        ("named", code in reply_text(row).upper(), "The reply does not name the code."),
        ("no_clarify", not clarified, "The bot asked a clarifying question although the code was clear."),
    ]
    return checks


def _followup(row, item):
    kind, gold = item.get("kind"), item.get("gold") or {}
    decision = _decision(row)
    focus = _focus_codes(row)
    after = {str(c).upper() for c in gold.get("subject_after") or []}

    def carried_old() -> bool:
        return bool(after) and not focus <= after

    if kind == "pick":
        checks = [
            ("decision", decision == "answer", "The bot did not read the message as an answer to its list."),
            ("position", gold.get("position") in _positions(row), "The bot picked a different position than the one chosen."),
        ]
        if gold.get("code"):
            code = str(gold["code"]).upper()
            checks.append(("tool_target", any(code in _dump(t.get("args")).upper() for t in _tools(row)),
                           "The lookup did not target the chosen item."))
        return checks
    if kind == "answer" and gold.get("idea_ref"):
        return _ideas_answer(row, gold)
    if kind == "answer":
        before = _kind(row, "state").get("open_question")
        after_q = _kind(row, "memory").get("open_question")
        verdict = _kind(row, "apply").get("verdict") or {}
        ans = verdict.get("open_question_answer") or {}
        options = [_norm(o.get("label") if isinstance(o, dict) else o) for o in ((before or {}).get("options") or [])]
        picked = [options[p - 1] for p in (ans.get("picked") or []) if isinstance(p, int) and 0 < p <= len(options)]
        if ans.get("mode") in ("yes", "no"):
            picked.append(ans["mode"])
        return [
            ("decision", decision == "answer", "The bot did not read the message as an answer to its question."),
            ("not_reasked", not (before and before == after_q), "The bot asked the same question again."),
            ("option", _norm(gold.get("option")) in picked, "The answer was not resolved to the chosen option."),
        ]
    if kind == "refine":
        flt = gold.get("filter") or {}
        has_filter = any(
            _norm(e.get("spec_key")) == _norm(flt.get("key")) and _norm(e.get("spec_value")) == _norm(flt.get("value"))
            for e in _entities(row)
        )
        return [
            ("decision", decision == "refine", "The bot did not treat the message as a refinement."),
            ("subject", after <= focus, "The subject was dropped when refining."),
            ("filter", has_filter, "The new filter was not applied."),
        ]
    if kind == "switch":
        return [
            ("decision", decision == "new_ask", "The bot did not treat the message as a new subject."),
            ("old_dropped", not carried_old(), "The old subject was carried into the new ask."),
            ("new_subject", after <= focus, "The new subject was not picked up."),
        ]
    if kind == "and_x" and gold.get("variant") == "new_fact":
        return [
            ("decision", decision == "new_ask", "The bot did not treat the message as a new question."),
            ("fact", gold.get("fact") in _domains(row), "The new fact asked was not looked up."),
            ("subject", bool(after) and after <= focus, "The subject was dropped when a new fact was asked."),
        ]
    if kind == "and_x":
        return [
            ("decision", decision == "new_ask", "The bot did not treat the message as a new item."),
            ("fact", gold.get("fact") in _domains(row), "The fact asked was not carried over to the new item."),
            ("old_dropped", not carried_old(), "The old item was carried into the new ask."),
            ("new_subject", after <= focus, "The new item was not picked up."),
        ]
    return [("known_kind", False, "Unknown follow-up kind.")]


def _held_idea(row: dict[str, Any]) -> Any:
    sv = (_stage(row, "received").get("raw") or {}).get("session_vars") or {}
    idea = sv.get("ideation")
    if idea is None and isinstance(sv.get("variables"), dict):
        idea = sv["variables"].get("ideation")
    return idea


def _ideas_answer(row, gold):
    ref = _norm(gold["idea_ref"])
    held = _held_idea(row)
    held_id = _norm(held.get("id") if isinstance(held, dict) else held)
    patch = (_stage(row, "remembered").get("raw") or {}).get("session_patch") or {}
    kept = held_id == ref
    if kept and "ideation" in patch:
        new = patch["ideation"]
        kept = _norm(new.get("id") if isinstance(new, dict) else new) == ref
    reply = reply_text(row).lower().replace("\u2019", "'")
    return [
        ("idea_kept", kept, "The bot dropped the idea it was holding."),
        ("no_restart", "what's your idea" not in reply, "The bot restarted by asking for the idea again."),
    ]


def _spec(row, item):
    got = {(_norm(e.get("spec_key")), _norm(e.get("spec_value"))) for e in _entities(row) if e.get("spec_key")}
    want = {(_norm(s.get("key")), _norm(s.get("value"))) for s in (item.get("gold") or {}).get("spec") or []}
    return [("spec_set", got == want and bool(want), "The spec words were not grounded to the expected set.")]


def _attachment(row, item):
    gold = item.get("gold") or {}
    files = [f for t in _tools(row) if isinstance(t.get("envelope"), dict)
             for f in (t["envelope"].get("attachments") or []) if isinstance(f, dict)]
    said_none = "none on record" in reply_text(row).lower()
    if gold.get("none_on_record"):
        return [("none_on_record", said_none and not files, "The bot sent files or did not say there is none on record.")]
    want = _norm(gold.get("attachment_type"))
    return [
        ("said_none", not said_none, "The bot said there is none on record, but files exist."),
        ("files", bool(files), "No files were sent."),
        ("type", bool(files) and all(_norm(f.get("attachmentType")) == want for f in files),
         "The files sent are not of the type asked."),
    ]


def _scope(row, item):
    return []


_SLICES = {"code_lookup": _code_lookup, "followup": _followup, "spec": _spec, "attachment": _attachment, "scope": _scope}


def grade_turn(row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    checks = _SLICES[item["slice"]](row, item)
    leaks = scope_leaks(row, item.get("asker"), item.get("watch_customer_names") or ())
    made_up = made_up_facts(row)
    failed = next((msg for _name, ok, msg in checks if not ok), None)
    # Leaks and made-up facts are their own zero-tolerance bars, listed on every turn and counted by
    # `summarise`; they decide `passed` only for the scope slice, whose whole job is the leak check.
    if failed is None and item["slice"] == "scope" and leaks:
        failed = ("The reply shows an internal id." if any(x.startswith("internal id") for x in leaks)
                  else "The reply shows a field hidden from the asker." if any(x.startswith("hidden") for x in leaks)
                  else "The reply shows a customer outside the asker's linked customers.")
    passed = failed is None
    return {
        "item_id": item.get("item_id"), "slice": item["slice"], "kind": item.get("kind"), "passed": passed,
        "reason": "The bot did what the message needed." if passed else failed,
        "checks": {name: ok for name, ok, _ in checks}, "leaks": leaks, "made_up": made_up,
        **({"growth": True} if item.get("growth") else {}),
    }


# --------------------------------------------------------------------------- #
# Summary across runs
# --------------------------------------------------------------------------- #


def _stats(runs: list[list[dict[str, Any]]]) -> dict[str, Any]:
    ns = [len(r) for r in runs]
    rates = [(sum(1 for x in r if x["passed"]) / len(r)) if r else 0.0 for r in runs]
    return {"n": round(sum(ns) / len(ns)) if ns else 0, "passed": round(sum(sum(1 for x in r if x["passed"]) for r in runs) / len(runs)) if runs else 0,
            "rate": sum(rates) / len(rates) if rates else 0.0, "lowest": min(rates) if rates else 0.0}


def summarise(results: list[list[dict[str, Any]]], bars: dict[str, float]) -> dict[str, Any]:
    """Growth items are reported under `growth` and stay out of slice n/rate and `meets`; their leaks and
    made-up facts still count, since those bars are zero-tolerance."""
    core = [[x for x in run if not x.get("growth")] for run in results]
    grow = [[x for x in run if x.get("growth")] for run in results]

    def by_slice(runs: list[list[dict[str, Any]]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in sorted({x["slice"] for run in runs for x in run}):
            out[name] = _stats([[x for x in run if x["slice"] == name] for run in runs])
            kinds = sorted({x["kind"] for run in runs for x in run if x["slice"] == name and x.get("kind")})
            if kinds:
                out[name]["kinds"] = {k: _stats([[x for x in run if x["slice"] == name and x.get("kind") == k] for run in runs])
                                      for k in kinds}
        return out

    slices, growth = by_slice(core), by_slice(grow)
    leaks_per_run = [sum(len(x["leaks"]) for x in run) for run in results]
    made_per_run = [sum(len(x["made_up"]) for x in run) for run in results]
    meets = (all(n == 0 for n in leaks_per_run) and all(n == 0 for n in made_per_run)
             and all(name in slices and slices[name]["rate"] >= bar for name, bar in bars.items()))
    return {"slices": slices, "growth": growth, "leaks": sum(leaks_per_run), "made_up": sum(made_per_run), "meets": meets}
