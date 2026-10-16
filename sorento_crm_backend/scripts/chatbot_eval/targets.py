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
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
          r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")  # exact names only: "Marble" and "Mayfair" are not months
DATE_DMY = re.compile(r"\b(\d{1,2})\s+(" + _MONTH + r")\.?,?\s+(\d{4})\b", re.I)
DATE_MDY = re.compile(r"\b(" + _MONTH + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?!\d)")
DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
DATE_MY = re.compile(r"\b(" + _MONTH + r")\.?,?\s+(\d{4})\b", re.I)
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


def _key(x: Any) -> str:
    """A code compared after upper-casing and dropping every non-alphanumeric."""
    return re.sub(r"[^A-Z0-9]", "", str(x).upper()) if x is not None else ""


def _strings(x: Any):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


def _product_list(products: Any) -> list[dict[str, Any]]:
    return [p for p in (products or []) if isinstance(p, dict)]


def _codes_of(products: Any) -> set[str]:
    return {_key(p.get("canonical_code") or p.get("raw")) for p in _product_list(products)
            if p.get("canonical_code") or p.get("raw")}


def _mem_focus(row: dict[str, Any], which: str) -> dict[str, Any]:
    focus = _kind(row, "memory").get("focus") or {}
    part = focus.get(which) if isinstance(focus, dict) else None
    return part if isinstance(part, dict) else {}


def _subject_before(row: dict[str, Any]) -> set[str]:
    codes = _codes_of(_mem_focus(row, "before").get("products"))
    return codes or _codes_of((_kind(row, "state").get("focus") or {}).get("products"))


def _subject_after(row: dict[str, Any]) -> set[str]:
    codes = _codes_of(_mem_focus(row, "after").get("products"))
    return codes or _codes_of((_kind(row, "state").get("focus") or {}).get("products"))


def _looked_up_envelopes(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in ((_stage(row, "looked_up").get("raw") or {}).get("envelopes") or []) if isinstance(e, dict)]


def _resolved_codes(row: dict[str, Any]) -> set[str]:
    """The codes the turn settled on: memory after, else the lookup's own strings, else the verdict's entities."""
    codes = _codes_of(_mem_focus(row, "after").get("products"))
    if codes:
        return codes
    for env in _looked_up_envelopes(row):
        for field in ("entities", "product_codes"):
            codes |= {_key(x) for x in (env.get(field) or []) if isinstance(x, str)}
    if codes:
        return codes
    return {_key(e.get("canonical_code") or e.get("raw")) for e in _entities(row) if e.get("hint") == "product"
            and (e.get("canonical_code") or e.get("raw"))} | \
           {_key(e.get("canonical_code")) for e in _entities(row) if e.get("canonical_code") and not e.get("hint")}


def _uuid_map(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    products = _product_list((_kind(row, "state").get("focus") or {}).get("products")) \
        + _product_list(_mem_focus(row, "before").get("products")) + _product_list(_mem_focus(row, "after").get("products"))
    for p in products:
        if p.get("uuid") and (p.get("canonical_code") or p.get("raw")):
            out[str(p["uuid"]).lower()] = _key(p.get("canonical_code") or p.get("raw"))
    return out


def _tool_targets(row: dict[str, Any]) -> set[str]:
    """Codes the named tool events were called for: uuids mapped through the state + memory product lists,
    plus literal strings in the arguments (scope_terms, product_code(s))."""
    ids = _uuid_map(row)
    out: set[str] = set()
    for t in _tools(row):
        for text in _strings(t.get("args")):
            out.add(ids.get(text.lower(), _key(text)))
    return out


def _fact_ok(row: dict[str, Any], fact: Any) -> bool:
    if not fact:
        return True
    domains = set(_domains(row))
    domains |= set(((_kind(row, "apply").get("plan") or {}).get("domains")) or [])
    domains |= set(_mem_focus(row, "after").get("domains") or [])
    return fact in domains or any(str(fact) in str(t.get("name")) for t in _tools(row))


def _oq(row: dict[str, Any]) -> tuple[Any, Any]:
    """(question before, question after): `memory.open_question` is {before, after} in real traces, the bare
    after value in older rows; the before value is the `state` event's."""
    mem = _kind(row, "memory").get("open_question")
    state_q = _kind(row, "state").get("open_question")
    if isinstance(mem, dict) and ("before" in mem or "after" in mem):
        return mem.get("before") or state_q, mem.get("after")
    return state_q, mem


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


def _dates(text: str) -> tuple[dict[str, str], dict[str, str], str]:
    """(full dates, month-year mentions, text with them removed). Each maps a normalised value
    (`YYYY-MM-DD`, `YYYY-MM`) to the text as written, for the finding."""
    full: dict[str, str] = {}
    months: dict[str, str] = {}

    def put(m: re.Match, d: int, mon: int | None, y: int) -> str:
        if not mon or not 1 <= d <= 31:
            return m.group(0)
        full.setdefault(_norm_date(d, mon, y), m.group(0))
        return " "

    text = DATE_ISO.sub(lambda m: put(m, int(m.group(3)), int(m.group(2)), int(m.group(1))), text)
    text = DATE_DMY.sub(lambda m: put(m, int(m.group(1)), MONTHS.get(m.group(2)[:3].lower()), int(m.group(3))), text)
    text = DATE_MDY.sub(lambda m: put(m, int(m.group(2)), MONTHS.get(m.group(1)[:3].lower()), int(m.group(3))), text)
    text = DATE_SLASH.sub(lambda m: put(m, int(m.group(1)), int(m.group(2)), int(m.group(3))), text)

    def month_year(m: re.Match) -> str:
        mon = MONTHS.get(m.group(1)[:3].lower())
        if not mon:
            return m.group(0)
        months.setdefault(f"{int(m.group(2)):04d}-{mon:02d}", m.group(0))
        return " "

    text = DATE_MY.sub(month_year, text)
    return full, months, text


def _num(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def made_up_facts(row: dict[str, Any]) -> list[str]:
    if _kind(row, "apply").get("note") == "payload omitted":
        return []  # a payload omitted at 32 KB leaves nothing to check against: waived, not failed
    reply = ORDINAL_RE.sub("", reply_text(row))
    full_results = row.get("tool_results")
    envelopes = ([_dump(t.get("result")) for t in full_results] if full_results
                 else [_dump(t.get("envelope")) for t in _tools(row)])
    source_parts = [user_message(row), str(row.get("prev_bot_message") or ""), *envelopes,
                    _dump([e for e in _looked_up_envelopes(row)]),
                    _dump(_product_list((_kind(row, "state").get("focus") or {}).get("products"))),
                    _dump(_mem_focus(row, "before")), _dump(_mem_focus(row, "after")),
                    *(str(r.get("text") or "") for r in _trace(row) if r.get("kind") == "prompt_text")]
    # full tool results from the live runner carry no cap, so a recorded `truncated` flag no longer matters
    truncated = not full_results and any(
        isinstance(t.get("envelope"), dict) and t["envelope"].get("truncated") for t in _tools(row))
    source = "\n".join(source_parts)
    src_dates, src_months, src_rest = _dates(source)
    src_month_keys = {d[:7] for d in src_dates} | set(src_months)
    src_upper = source.upper()
    src_nums = {n for n in (_num(t) for t in NUM_RE.findall(src_rest)) if n is not None}
    src_nums |= {n for n in (_num(t) for t in re.findall(r"\d+", src_rest)) if n is not None}
    rep_dates, rep_months, rep_rest = _dates(reply)
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
    for d, written in rep_dates.items():
        if d not in src_dates:
            add(written)
    for ym, written in rep_months.items():
        if ym not in src_month_keys:
            add(written)
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
    code = _key(gold.get("code"))
    clarified = bool(_asking(row))
    checks = []
    if code:
        checks.append(("code", code in _resolved_codes(row), "The bot read a different code than the one asked."))
    if gold.get("fact"):
        checks.append(("fact", _fact_ok(row, gold["fact"]), "The bot looked up a different fact than the one asked."))
    if gold.get("ambiguous"):
        checks.append(("clarified", clarified, "The code was ambiguous but the bot did not ask which one."))
        return checks
    if code:
        ran = code in _tool_targets(row) or any(
            str(gold["code"]).upper() in _dump(t.get("args")).upper() or str(gold["code"]).upper() in _dump(t.get("envelope")).upper()
            for t in _tools(row))
        checks.append(("tool_ran", ran, "The lookup tool did not run for that code."))
        checks.append(("named", code in _key(reply_text(row)), "The reply does not name the code."))
    checks.append(("no_clarify", not clarified, "The bot asked a clarifying question although the code was clear."))
    return checks


def _followup(row, item):
    kind, gold = item.get("kind"), item.get("gold") or {}
    before_subject, after_subject = _subject_before(row), _subject_after(row)
    want = {_key(c) for c in gold.get("subject_after") or []}
    _mem_before = _codes_of(_mem_focus(row, "before").get("products"))

    def carried_old() -> bool:
        return bool(want) and not after_subject <= want

    if kind == "pick":
        positions = _positions(row)
        # a resolver "Did you mean" pick records no position: the pick was honoured when the subject after is the item
        honoured = gold.get("position") in positions or (not positions and bool(gold.get("code"))
                                                         and _key(gold["code"]) in after_subject)
        checks = [("position", honoured, "The bot picked a different position than the one chosen.")]
        if gold.get("code"):
            code = _key(gold["code"])
            checks.append(("tool_target", code in _tool_targets(row) or any(
                str(gold["code"]).upper() in _dump(t.get("args")).upper() for t in _tools(row)),
                "The lookup tool did not target the chosen item."))
        return checks
    if kind == "answer" and gold.get("idea_ref"):
        return _ideas_answer(row, gold)
    if kind == "answer":
        question, after_q = _oq(row)
        verdict = _kind(row, "apply").get("verdict") or {}
        ans = verdict.get("open_question_answer") or {}
        options = [_norm(o.get("label") if isinstance(o, dict) else o) for o in ((question or {}).get("options") or [])]
        picked = [options[p - 1] for p in (ans.get("picked") or []) if isinstance(p, int) and 0 < p <= len(options)]
        if ans.get("mode") in ("yes", "no"):
            picked.append(ans["mode"])
        answered = bool(ans.get("picked") or ans.get("mode") or verdict.get("is_affirmative") or verdict.get("continuation")
                        or (question and after_q != question))
        checks = [
            ("not_reasked", not (question and question == after_q), "The bot asked the same question again."),
            ("answered", answered, "The bot did not read the message as an answer to its question."),
        ]
        if picked:  # the mode is not always recorded, so the option is checked only when it is
            checks.append(("option", _norm(gold.get("option")) in picked, "The answer was not resolved to the chosen option."))
        return checks
    if kind == "refine":
        flt = gold.get("filter") or {}
        extra = _mem_focus(row, "after").get("extra") or {}
        values = {_norm(e.get("spec_value")) for e in _entities(row) if e.get("spec_value") is not None}
        values |= {_norm(e.get("canonical_code") or e.get("raw")) for e in _entities(row) if e.get("hint") == "brand"}
        values |= {_norm(e.get("spec_value") or e.get("raw")) for e in extra.get("specification") or [] if isinstance(e, dict)}
        values |= {_norm(b) for b in (_mem_focus(row, "after").get("brands") or [])}
        return [
            ("subject", want <= after_subject, "The subject was dropped when refining."),
            ("filter", _norm(flt.get("value")) in values, "The new filter was not applied."),
        ]
    if kind == "switch":
        return [
            ("old_dropped", not carried_old(), "The old subject was carried into the new ask."),
            ("new_subject", want <= after_subject, "The new subject was not picked up."),
            ("changed", not (_mem_before and after_subject == _mem_before), "The subject did not change."),
        ]
    if kind == "and_x" and gold.get("variant") == "new_fact":
        return [
            ("fact", _fact_ok(row, gold.get("fact")), "The new fact asked was not looked up."),
            ("subject", bool(want) and want <= after_subject, "The subject was dropped when a new fact was asked."),
        ]
    if kind == "and_x":
        return [
            ("fact", _fact_ok(row, gold.get("fact")), "The fact asked was not carried over to the new item."),
            ("old_dropped", not carried_old(), "The old item was carried into the new ask."),
            ("new_subject", want <= after_subject, "The new item was not picked up."),
        ]
    return [("known_kind", False, "Unknown follow-up kind.")]


def _held_idea(row: dict[str, Any]) -> Any:
    raw = (_stage(row, "received").get("raw") or {}).get("session_vars") or {}
    sv = raw.get("session_vars") if isinstance(raw.get("session_vars"), dict) else raw
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
    extra = _mem_focus(row, "after").get("extra") or {}
    got = {_norm(e.get("spec_value") or e.get("raw")) for e in _entities(row)
           if e.get("hint") == "specification" or e.get("spec_key")}
    got |= {_norm(e.get("spec_value") or e.get("raw")) for e in extra.get("specification") or [] if isinstance(e, dict)}
    got.discard("")
    want = {_norm(s.get("value")) for s in (item.get("gold") or {}).get("spec") or []}
    return [("spec_set", bool(want) and want <= got, "The spec words were not grounded to the expected set.")]


def _type_word(x) -> str:
    return _norm(x).replace("_", " ")


def _attachment(row, item):
    gold = item.get("gold") or {}
    accepted = {_type_word(x) for x in [gold.get("attachment_type"), gold.get("attachment_type_name"),
                                        *(gold.get("attachment_type_names") or [])] if x}
    files = [f for t in _tools(row) if isinstance(t.get("envelope"), dict)
             for f in (t["envelope"].get("attachments") or []) if isinstance(f, dict)]
    announced = (_stage(row, "replied").get("facts") or {}).get("files")
    sent = announced if isinstance(announced, int) else len(files)
    asked = [_type_word(e.get("canonical_code") or e.get("raw")) for e in _entities(row) if e.get("hint") == "attachment_type"]
    from_memory = (_mem_focus(row, "after").get("extra") or {}).get("attachment_type")
    if isinstance(from_memory, (str, dict)):
        from_memory = [from_memory]
    asked += [_type_word(x.get("canonical_code") or x.get("raw") if isinstance(x, dict) else x) for x in from_memory or [] if x]
    asked_ok = bool(asked) and all(a in accepted for a in asked)
    checks = [("asked_type", asked_ok, "The asked type was read differently from the type requested.")]
    if gold.get("none_on_record"):
        checks.append(("none_on_record", not files and sent == 0,
                       "Files were sent although none exist on record."))
        return checks
    checks += [
        ("files", bool(files) and sent > 0, "No files were sent."),
        ("type", bool(files) and all(_type_word(f.get("attachmentType")) in accepted for f in files),
         "The files sent are not of the type asked."),
    ]
    return checks


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
    out = {"slices": slices, "growth": growth, "leaks": sum(leaks_per_run), "made_up": sum(made_per_run), "meets": meets}
    if len(results) < 3:
        out.update(meets=False, reason=f"needs at least 3 runs, got {len(results)}")
    return out
