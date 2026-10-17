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
            last = {**last, **r} if kind == "memory" else r   # memory notes may arrive in parts: later keys win
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
    asking = (_stage(row, "replied").get("facts") or {}).get("asking")
    return None if asking == "team_pick" else asking  # offering to hand over to a team is not a clarify


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


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def _mentions(text: Any, code: Any) -> bool:
    """The code appears as a whole token (dash and case blind): SRT10 is not in "SRT105"."""
    want = _key(code)
    return bool(want) and any(_key(tok) == want for tok in _TOKEN_RE.findall(str(text)))


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
    # never the `state` record: it is written after the turn, so it is the subject AFTER, not before
    return _codes_of(_mem_focus(row, "before").get("products"))


def _lookup_subjects(row: dict[str, Any]) -> set[str]:
    """Subjects named by the lookup's own strings (order numbers, delivery orders, ...): not products, so not in memory."""
    ids = _uuid_map(row)
    out: set[str] = set()
    for env in _hit_envelopes(row):
        for field in ("entities", "product_codes"):
            for x in env.get(field) or []:
                if isinstance(x, str):
                    out.add(ids.get(x.lower()) or ("" if UUID_RE.fullmatch(x) else _key(x)))
    out.discard("")
    return out


def _tool_subjects(row: dict[str, Any]) -> set[str]:
    """Items the named tools were called for: product ids mapped through uuid_codes / the state lists, plus literal
    code-like args (scope_terms, product_code(s)); other args (dates, space ids) are not subjects."""
    ids = _uuid_map(row)
    out: set[str] = set()
    for t in _tools(row):
        args = t.get("args") or {}
        for name in ("product_ids", "product_id", "scope_terms", "product_code", "product_codes"):
            for text in _strings(args.get(name)):
                if text.lower() in ids:
                    out.add(ids[text.lower()])
                elif re.search(r"\d", text) and not UUID_RE.fullmatch(text):
                    out.add(_key(text))
    return out


def _has_memory_after(row: dict[str, Any]) -> bool:
    """The memory event carries a `focus.after` key (even a None or empty one): the bot's own record of the subject."""
    focus = _kind(row, "memory").get("focus")
    return isinstance(focus, dict) and "after" in focus


def _subject_after(row: dict[str, Any]) -> set[str]:
    codes = _codes_of(_mem_focus(row, "after").get("products"))
    if _has_memory_after(row):
        # the memory event exists: its after focus is the truth, and an empty one means the bot dropped the subject.
        # The state event (the subject BEFORE the turn) and the tool args must not stand in for it; only the lookup's
        # own strings (an order or delivery number is no product) can name a non-product subject. An order the memory
        # holds in its after focus (extra.order) is the subject when no product is: products first, order second.
        held = {_key(o["raw"]) for o in ((_mem_focus(row, "after").get("extra") or {}).get("order") or [])
                if isinstance(o, dict) and isinstance(o.get("raw"), str) and o["raw"].strip()}
        return codes or held or _lookup_subjects(row)
    # older / hand-built rows: the first source that names anything wins; the tool args are the last resort (a lookup
    # that answered with entities of its own outranks the literal terms the tool was called with)
    return (codes or _codes_of((_kind(row, "state").get("focus") or {}).get("products"))
            or _lookup_subjects(row) or _tool_subjects(row))


def _looked_up_envelopes(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in ((_stage(row, "looked_up").get("raw") or {}).get("envelopes") or []) if isinstance(e, dict)]


def _hit_envelopes(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Lookup envelopes that found something: one reporting a miss, an unresolved word or an error names no subject."""
    return [e for e in _looked_up_envelopes(row)
            if e.get("outcome") not in ("not_found", "miss", "unresolved", "error")
            and not e.get("miss") and not e.get("unresolved") and not e.get("error")]


def _resolved_codes(row: dict[str, Any]) -> set[str]:
    """The codes the turn settled on: memory after, else the lookup's own strings, else the verdict's entities."""
    codes = _codes_of(_mem_focus(row, "after").get("products"))
    if codes:
        return codes
    for env in _hit_envelopes(row):
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
    # the loader reads product codes for the uuids in the tool args (a pick lands there, not in memory)
    out.update({str(u).lower(): _key(c) for u, c in (row.get("uuid_codes") or {}).items()})
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
    after value in older rows. A missing `before` is unknown: the `state` record is written after the turn, so it
    never stands in for it."""
    mem = _kind(row, "memory").get("open_question")
    if isinstance(mem, dict) and ("before" in mem or "after" in mem):
        return mem.get("before"), mem.get("after")
    # a hand-built row (its memory event has no focus before/after) records the question before in `state`; in a
    # real trace the `state` event is written after the turn, so it never stands in for the unknown before
    focus = _kind(row, "memory").get("focus")
    hand_built = not (isinstance(focus, dict) and ("before" in focus or "after" in focus))
    return (_kind(row, "state").get("open_question") if hand_built else None), mem


HANDOVER_KINDS = frozenset({"team_pick", "member_offer", "company_pick"})  # turn/pending.py ESCALATION_OFFER_KINDS


def _oq_sig(q: Any) -> Any:
    """An open question by kind and option labels: `asked_at_turn` differs on a re-ask, so whole dicts never match."""
    if not isinstance(q, dict):
        return q
    return (q.get("kind"), tuple(_norm(o.get("label") if isinstance(o, dict) else o) for o in q.get("options") or []))


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
    linked_co = {_company(n) for n in (asker.get("linked_customer_names") or [])}
    leaks: list[str] = []
    for name in watch_customer_names or ():
        if _company(name) not in linked_co and _norm(name) and _norm(name) in low:
            leaks.append(f"customer outside the asker's links: {name}")
    if UUID_RE.search(reply):
        leaks.append("internal id in the reply")
    for field in asker.get("hidden_fields") or []:
        variants = {field.lower(), field.lower().replace("_", " ")}
        if any(v in low for v in variants):
            leaks.append(f"hidden field: {field.replace('_', ' ')}")
    if _norm(asker.get("audience")) == "unlinked":  # a contact with no linked customer sees none of a delivery order
        for kind, value in _do_fields(row):
            if _norm(value) and _norm(value) in low:
                leaks.append(f"customer outside the asker's links: {value}" if kind == "customer"
                             else f"delivery order {kind} shown to a contact with no linked customer")
    return leaks


_COMPANY_SUFFIX = {"sdn", "bhd", "berhad", "pte", "ltd", "plt"}   # legal forms only: "trading" and "enterprise" name a company


def _company(name: Any) -> str:
    """A company name compared without case, punctuation or its legal form (Sdn Bhd, Berhad, Pte Ltd, Plt)."""
    return " ".join(w for w in re.findall(r"[a-z0-9]+", str(name or "").lower()) if w not in _COMPANY_SUFFIX)


_DO_ALIASES = {"customer": {"debtor_name", "customer", "customer_name", "debtor"},
               "driver": {"driver_name", "driver"}, "lorry plate": {"lorry_plate", "plate", "lorry"}}


def _do_fields(row: dict[str, Any]) -> list[tuple[str, str]]:
    """(kind, value) of the customer, driver and lorry plate fields in the full tool results: a dict key, or a
    `{"key": ..., "value": ...}` field entry (the live delivery order result is a JSON string of those)."""
    found: list[tuple[str, str]] = []

    def kind_of(name: Any) -> str | None:
        return next((k for k, names in _DO_ALIASES.items() if str(name).lower() in names), None)

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "key" in x and "value" in x and kind_of(x["key"]) and isinstance(x["value"], str):
                found.append((kind_of(x["key"]), x["value"]))
            for k, v in x.items():
                if kind_of(k) and isinstance(v, str):
                    found.append((kind_of(k), v))
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for t in row.get("tool_results") or []:
        result = t.get("result") if isinstance(t, dict) else None
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                continue
        walk(result)
    return found


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


def _result_text(result: Any) -> str:
    """A tool result as text to check against: a string as it is (real newlines), a structure string by string
    plus its JSON dump (the numbers)."""
    if isinstance(result, str):
        return result
    return _dump(result) + "\n" + "\n".join(_strings(result))


def made_up_facts(row: dict[str, Any]) -> list[str]:
    reply = ORDINAL_RE.sub("", reply_text(row))
    full_results = row.get("tool_results")
    envelopes = ([_result_text(t.get("result")) for t in full_results] if full_results
                 else [_dump(t.get("envelope")) for t in _tools(row)])
    source_parts = [user_message(row), str(row.get("prev_bot_message") or ""), *envelopes,
                    _dump([e for e in _looked_up_envelopes(row)]),
                    _dump(_product_list((_kind(row, "state").get("focus") or {}).get("products"))),
                    _dump(_mem_focus(row, "before")), _dump(_mem_focus(row, "after")),
                    # a picker's option codes and labels (the open question) are things the bot was holding
                    _dump(_kind(row, "state").get("open_question")), _dump(_kind(row, "memory").get("open_question")),
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
    # without the full tool results an absent token cannot be called made up when a named tool ran
    named_ran = not full_results and bool(_tools(row))
    omitted = _kind(row, "apply").get("note") == "payload omitted" and not full_results  # nothing to check against
    if omitted and _tools(row):
        return []  # tools ran and their results were not recorded: nothing to check against, waived (test_a_missing_apply_payload)
    label = "unverifiable" if (truncated or named_ran or omitted) else "made up"

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


KNOWN_FACT_LABELS = {"stock", "eta", "files", "price", "order_status", "product_info", "promotions", "incoming"}
COULD_NOT_FIND_RE = re.compile(r"couldn.?t find|could not find|no (?:products?|results?|items?) (?:found|matched)|not found", re.I)


def _menu_check(row: dict[str, Any], gold: dict[str, Any] | None = None) -> tuple[str, bool, str] | None:
    """A kind menu (the engine's `kind_pick`, labels "Raw (word)" with an `entity_type` each) is for product versus
    customer only; any other menu is a needless question."""
    after = _oq(row)[1]
    menu = (isinstance(after, dict) and after.get("kind") in ("kind_pick", "clarify")) or row.get("branch_kind") == "clarify_menu"
    types = {_norm(o.get("entity_type") or o.get("label")) for o in ((after or {}).get("options") or [])
             if isinstance(o, dict)} if isinstance(after, dict) else set()
    ok = not menu or types == {"product", "customer"}
    if (gold or {}).get("ambiguous"):   # a which-one menu is right for an ambiguous code
        return None
    return ("no_menu", ok, "The bot asked a clarifying menu that is not product versus customer.")


# the engine's stock reply lines (label_catalog.py: "Got stock.", "Only N got stock.", "No stock, ETA ...", "No stock found
# for ...", "yes, we have stock") and their ms / zh renderings
STOCK_WORDS_RE = re.compile(r"\b(?:stock|in stock|available|availability|ada|ready stock|ready|how many left|berapa ada)\b", re.I)
QUANTITY_ASK_RE = re.compile(r"how\s+many|berapa|多少(?!钱)", re.I)


SPEC_REFINE_KEYS = frozenset({"spec", "size", "colour"})   # a refine on these asks for spec info; brand / warehouse do not
FILES_PROMO_SPEC_FACTS = frozenset({"product_attachment", "promotion", "master_products"})
FILES_PROMO_SPEC_LABELS = frozenset({"files", "promotions", "product_info"})
SENTENCE_RE = re.compile(r"(?<=[.?!])\s+|(?<=[？。])")
SPEC_LISTED_RE = re.compile(r"^(?:\d+\.\s+)?" + CODE_RE.pattern, re.M)             # a code-led line, slot lines included
NUMBERED_LINE_RE = re.compile(r"\d+\. ")
ANSWER_BLOCK_RE = re.compile(r"\*[^*\n]+:\*")                       # "*Promotion:* ..." field
CODE_LED_VALUE_RE = re.compile(r"\d+\.\s+" + CODE_RE.pattern + r"[^\n:]*:\s*\S")   # "1. SRTA100: RM 90"


def _files_sent(row: dict[str, Any]) -> int:
    announced = (_stage(row, "replied").get("facts") or {}).get("files")
    if isinstance(announced, int):
        return announced
    return sum(len(t["envelope"].get("attachments") or []) for t in _tools(row) if isinstance(t.get("envelope"), dict))


def _reply_answers(row: dict[str, Any], slice_name: str = "") -> bool:
    """The reply carries an answer outside its how-many sentences and numbered option lines."""
    reply = reply_text(row)
    lines = [x.strip() for line in reply.split("\n")   # a numbered line is kept whole: its "1. " is not a sentence end
             for x in ([line] if NUMBERED_LINE_RE.match(line) else SENTENCE_RE.split(line))
             if x and x.strip() and not QUANTITY_ASK_RE.search(x)]
    # the bot's quantity slots ("1. CODE - 10") and menu options ("1. Office") are part of the question: a numbered
    # line is an answer only with an answer block or a code-led value
    numbered = [x for x in lines if NUMBERED_LINE_RE.match(x)]
    plain = "\n".join(x for x in lines if not NUMBERED_LINE_RE.match(x))
    return (any(ANSWER_BLOCK_RE.search(x) or CODE_LED_VALUE_RE.match(x) for x in numbered)
            or bool(re.search(r"\d", CODE_RE.sub(" ", plain))) or bool(SET_CODE_ROW_RE.search(plain))
            or (slice_name == "spec" and bool(SPEC_LISTED_RE.search("\n".join(lines)))) or _files_sent(row) > 0)


def _quantity_check(row: dict[str, Any], gold: dict[str, Any] | None = None,
                    slice_name: str = "") -> tuple[str, bool, str] | None:
    """Owner ruling (round 29): a how-many question ("how many", "berapa", or a quantity owed after the turn) fails only
    when it takes the place of the answer to a files, promotion or spec-info ask. Such an ask is what the ANSWER KEY
    says (a files / promotion / product-info fact, or an attribute), an attachment item, or a spec item whose message
    has no stock word. No answer = once the how-many sentences are removed, no number outside a product code, no listed
    entry and no file sent. Any other how-many (stock asks, asks of unknown type, answered asks) is fine."""
    after = _oq(row)[1]
    payload = after.get("payload") if isinstance(after, dict) and isinstance(after.get("payload"), dict) else {}
    owed = (list(after.get("owed") or []) if isinstance(after, dict) else []) + list(payload.get("owed") or [])
    reply = reply_text(row)
    if not (QUANTITY_ASK_RE.search(reply) or "quantity" in owed):
        return ("no_qty_ask", True, "The bot asked how many instead of giving the answer.")
    g = gold or {}
    label = _norm(g.get("fact_label"))
    flt = g.get("filter") if isinstance(g.get("filter"), dict) else {}
    spec_refine = slice_name == "followup" and flt.get("key") in SPEC_REFINE_KEYS
    answer_ask = (spec_refine or g.get("fact") in FILES_PROMO_SPEC_FACTS or label in FILES_PROMO_SPEC_LABELS
                  or bool(label and label not in KNOWN_FACT_LABELS) or slice_name == "attachment"
                  or (slice_name == "spec" and not STOCK_WORDS_RE.search(user_message(row))))
    if answer_ask:
        answered = _reply_answers(row, slice_name)
        if not answered:
            return ("no_qty_ask", False, "The bot asked how many instead of giving the answer.")
    return ("qty_ask_ok", True, "The bot asked how many instead of giving the answer.")


def _picker_check(row: dict[str, Any], gold: dict[str, Any] | None = None) -> tuple[str, bool, str] | None:
    """A variant picker (a product_pick list, a stock picker, a one-option did-you-mean) after the answer is a needless
    question unless the gold code is ambiguous."""
    if (gold or {}).get("ambiguous"):
        return None
    before, after = _oq(row)
    if not (isinstance(after, dict) and after.get("kind") == "product_pick"):
        return ("no_picker", True, "The bot left a list of products open to pick from instead of answering.")

    def codes(q: Any) -> set[str]:
        return {_key(o.get("code") or o.get("label")) for o in (q.get("options") or []) if isinstance(o, dict)} if isinstance(q, dict) else set()

    same_list = isinstance(before, dict) and before.get("kind") == "product_pick" and codes(before) == codes(after)
    return ("no_picker", same_list, "The bot left a list of products open to pick from instead of answering.")


def _needless(row: dict[str, Any], gold: dict[str, Any], menu: bool = True,
              shown: bool = False, slice_name: str = "") -> list[tuple[str, bool | None, str]]:
    """The needless-question checks every item gets. `shown`: the answer already showed products, so a list left open
    after it is not a needless picker."""
    picker = None if shown else _picker_check(row, gold)
    return [c for c in ((_menu_check(row, gold) if menu else None), _quantity_check(row, gold, slice_name), picker) if c]


def _result_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The items of every full tool result (a dict or a JSON string), as dicts."""
    out: list[dict[str, Any]] = []
    for t in row.get("tool_results") or []:
        result = t.get("result") if isinstance(t, dict) else None
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                continue
        if isinstance(result, dict):
            out.extend(i for i in result.get("items") or [] if isinstance(i, dict))
    return out


def _item_code(item: dict[str, Any]) -> str:
    for f in item.get("fields") or []:
        if isinstance(f, dict) and (_norm(f.get("label")) == "product code" or f.get("key") == "product_code"):
            return _key(f.get("value"))
    return ""


NONE_WORDS = r"(?:not defined|not on record|not recorded|not available|none|n/a)(?![\w/])"
NONE_VALUE_RE = re.compile(NONE_WORDS.replace("(?![\\w/])", "") + r"?\s*", re.I)   # a field value that is itself a none word, or empty


def _attribute_answered(row: dict[str, Any], attr: str, code: Any) -> bool:
    """An attribute ask is answered by a number after the attribute word (digits inside a product code do not count), or
    by an honest none: the reply says the record has none and a tool result lists the gold code's item with no field
    for that attribute."""
    reply = re.sub(r"[*_~]", "", reply_text(row))   # WhatsApp emphasis markers sit between the word and its colon
    word = re.escape(attr)
    between = re.search(rf"\b{word}\b((?:\s+[\w.-]+){{0,4}})\s*:?\s*\b{NONE_WORDS}", CODE_RE.sub(" ", reply), re.I)
    if between and re.search(r"\d", between.group(1)):   # a number in the words after the attribute is a value
        return True
    says_none = re.search(rf"\bno {word}\b|(?:don.?t|do not|does not|doesn.?t) have (?:the |a |any )?{word}\b"
                          rf"|\b{word}\b(?:\s+[\w-]+){{0,4}}\s*:?\s*\b{NONE_WORDS}", reply, re.I)
    if not says_none:   # a none statement takes the none branch only: a number elsewhere does not rescue it
        return bool(re.search(word + r"[^\n]{0,40}\d", CODE_RE.sub(" ", reply), re.I))
    items = [i for i in _result_items(row) if _item_code(i) == _key(code)] if code else []
    names_attr = any(attr.lower() in _norm(f.get(k)).replace("_", " ")
                     for i in items for f in i.get("fields") or [] if isinstance(f, dict)
                     and not NONE_VALUE_RE.fullmatch(str(f.get("value") if f.get("value") is not None else ""))
                     for k in ("label", "key"))
    return bool(items and not names_attr)


def _code_lookup(row, item):
    gold = item.get("gold") or {}
    code = _key(gold.get("code"))
    clarified = bool(_asking(row))
    checks = []
    if code:
        checks.append(("code", code in _resolved_codes(row), "The bot read a different code than the one asked."))
    checks.extend(_needless(row, gold, slice_name="code_lookup"))
    label = _norm(gold.get("fact_label"))
    if label and label not in KNOWN_FACT_LABELS:   # an attribute ask (weight, ...): the reply needs a value for it
        checks.append(("attribute_value", _attribute_answered(row, label.replace("_", " "), gold.get("code")),
                       "The reply has no value for the attribute asked."))
    if gold.get("fact"):
        checks.append(("fact", _fact_ok(row, gold["fact"]), "The bot looked up a different fact than the one asked."))
    if gold.get("ambiguous"):
        checks.append(("clarified", clarified, "The code was ambiguous but the bot did not ask which one."))
        return checks
    ran = bool(code) and (code in _tool_targets(row) or any(
        _mentions(_dump(t.get("args")), gold["code"]) or _mentions(_dump(t.get("envelope")), gold["code"])
        for t in _tools(row)))
    if code:
        checks.append(("tool_ran", ran, "The lookup tool did not run for that code."))
        # a whole code token, compared dash and case blind: SRT10 is not named by "SRT105"
        named = _mentions(reply_text(row), code)
        checks.append(("named", named, "The reply does not name the code."))
    # asking only counts against the bot when it asked INSTEAD of looking the code up
    # a code that matches nothing in the data (a typo) may be answered with a did-you-mean: the loader flags it
    unresolved = bool(gold.get("code_unresolved"))
    checks.append(("no_clarify", not (clarified and not ran and not unresolved),
                   "The bot asked a clarifying question although the code was clear."))
    return checks


def _squash(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _covered(code: str, want: set[str]) -> bool:
    """A subject code is the typed literal or an expansion of it (it starts with the literal), separators ignored."""
    return any(_squash(code).startswith(_squash(w)) for w in want if _squash(w))


def _subject_check(want: set[str], after: set[str]) -> tuple[str, bool | None, str]:
    """The new subject is in memory after; with no gold subject the check is unknown (None), never a failure."""
    return ("new_subject", (want <= after) if want else None, "The new subject was not picked up.")


def _followup(row, item):
    kind, gold = item.get("kind"), item.get("gold") or {}
    needless = _needless(row, gold, menu=kind in {"and_x", "switch"}, slice_name="followup")
    # a how-many that replaces the answer is reported before the slice's own misses
    first = [c for c in needless if c[0] == "no_qty_ask" and c[1] is False]
    return first + _followup_checks(row, item, kind, gold) + [c for c in needless if c not in first]


NUMBER_UNIT_RE = re.compile(r"\d+(?:\.\d+)?[a-z]+")


def _word_tokens(text: Any) -> list[str]:
    """Whole words of a string. A uuid gives none; a product code (a whitespace-separated word of CODE_RE shape) is ONE
    opaque token; a number with its unit ("300mm") splits into the number and the unit; other text splits on
    non-alphanumerics."""
    text = str(text).strip()
    if UUID_RE.fullmatch(text):
        return []
    out: list[str] = []
    for word in text.split():
        bare = word.strip(".,;:!?()[]\"'")
        if CODE_RE.fullmatch(bare.upper()):
            out.append(re.sub(r"[^a-z0-9]", "", bare.lower()))
        elif NUMBER_UNIT_RE.fullmatch(bare.lower()):
            out.extend(re.findall(r"\d+(?:\.\d+)?|[a-z]+", bare.lower()))
        else:
            out.extend(re.findall(r"\d+(?:\.\d+)?|[a-z0-9]+", word.lower()))   # a decimal stays one number
    return out


def _words_of(*xs: Any) -> set[str]:
    """Lower-case words of the strings among `xs` (a bool or number is no phrase)."""
    return {w for x in xs if isinstance(x, str) for w in re.findall(r"[a-z0-9]+", x.lower())}


def _entity_words(row: dict[str, Any], hints: frozenset[str] = frozenset({"specification", "category", "brand"})) -> set[str]:
    """The words of this turn's spec / category / brand entity phrases (raw, label, canonical_code; a real spec value
    stands for itself)."""
    out: set[str] = set()
    for e in _entities(row):
        if e.get("hint") not in hints and not e.get("spec_key"):
            continue
        value = e.get("spec_value")
        if value is not None and not isinstance(value, bool):   # a real spec value: the phrase around it is not the value
            out |= _words_of(value)
        else:
            out |= _words_of(e.get("raw"), e.get("label"), e.get("canonical_code"))
    return out


def _header_words(row: dict[str, Any]) -> set[str]:
    """The words of the bot's set header phrase ("Here's what you want: <phrase> (N...)"), empty when there is none."""
    m = re.search(r"^Here's what you want:\s*(.*?)\s*\(\d", reply_text(row), re.M)
    return _words_of(m.group(1)) if m else set()


def _followup_checks(row, item, kind, gold):
    # real traces carry a memory event with `focus.after`; older / hand-built rows do not. The rules that only make
    # sense against real traces (a stale old subject staying in memory, the lookup after a refine) apply to those.
    real = _has_memory_after(row)
    before_subject, after_subject = _subject_before(row), _subject_after(row)
    want = {_key(c) for c in gold.get("subject_after") or []}
    _mem_before = _codes_of(_mem_focus(row, "before").get("products"))

    # a typed literal the resolver expanded (prefix family, picker options) counts only for an unresolved gold code
    family = bool(gold.get("code_unresolved"))

    def covered(a: str, w: set[str]) -> bool:
        return _covered(a, w) if family else a in w

    def carried_old() -> bool:
        return bool(want) and not all(covered(a, want) for a in after_subject)

    def new_subject_ok() -> bool | None:
        if not want:
            return None
        if all(any(covered(a, {w}) for a in after_subject) for w in want):
            return True
        if not family:
            return False
        options = [_squash(o.get("code") or o.get("label")) for o in ((_oq(row)[1] or {}).get("options") or [])
                   if isinstance(o, dict)] if isinstance(_oq(row)[1], dict) else []
        return bool(options) and all(any(o.startswith(_squash(w)) for w in want) for o in options)

    if kind == "pick":
        positions = _positions(row)
        # a resolver "Did you mean" pick records no position: the pick was honoured when the subject after is the item
        honoured = gold.get("position") in positions or (
            not positions and bool(gold.get("code"))
            and (_key(gold["code"]) in after_subject or _key(gold["code"]) in _tool_targets(row)))
        checks = [("position", honoured, "The bot picked a different position than the one chosen.")]
        if gold.get("code"):
            code = _key(gold["code"])
            # no named tool ran (the bot asked its next question): the target is unknown, not wrong
            targeted = code in _tool_targets(row) or any(_mentions(_dump(t.get("args")), gold["code"]) for t in _tools(row))
            unknown = not _tools(row) and bool(_asking(row))
            checks.append(("tool_target", None if unknown else targeted, "The lookup tool did not target the chosen item."))
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
        # a cancel is no answer; a question cleared by an arm (casual, escalation) is no answer either: the clear
        # reads as one only when the apply payload was omitted, so the verdict cannot say otherwise
        omitted = _kind(row, "apply").get("note") == "payload omitted"
        same = _oq_sig(question) == _oq_sig(after_q)
        offer = isinstance(after_q, dict) and after_q.get("kind") in HANDOVER_KINDS and not (
            isinstance(question, dict) and question.get("kind") in HANDOVER_KINDS)
        changed = bool(question) and not same and not offer and (after_q is not None or omitted)
        answered = bool(ans.get("picked") or ans.get("mode") not in (None, "", "cancel") or verdict.get("is_affirmative")
                        or verdict.get("continuation") or (changed and ans.get("mode") != "cancel"))
        checks = [
            # a question after the turn with none known before: the before question is unknown, not absent
            ("not_reasked", None if question is None and after_q is not None else not (question and same),
             "The bot asked the same question again."),
            ("answered", answered, "The bot did not read the message as an answer to its question."),
        ]
        if picked:  # the mode is not always recorded, so the option is checked only when it is
            want_option = _norm(gold.get("option"))
            num = re.fullmatch(r"(\d+)\.?", want_option)
            if want_option not in options and num and 0 < int(num.group(1)) <= len(options):  # a numeric gold is the 1-based position in the question
                want_option = options[int(num.group(1)) - 1]
            checks.append(("option", want_option in picked, "The answer was not resolved to the chosen option."))
        return checks
    if kind == "refine":
        flt = gold.get("filter") or {}
        payload_omitted = _kind(row, "apply").get("note") == "payload omitted"  # the entities cannot be read
        extra = _mem_focus(row, "after").get("extra") or {}
        values = {_val(e.get("spec_value")) for e in _entities(row) if e.get("spec_value") is not None}
        # a boolean spec value ("true") carries no words: the entity's own phrase is what the filter names
        values |= {_val(e.get(k)) for e in _entities(row) if (e.get("spec_key") or e.get("hint") == "specification")
                   and (e.get("spec_value") is None or isinstance(e.get("spec_value"), bool))
                   for k in ("raw", "label") if e.get(k)}
        values |= {_val(e.get("canonical_code") or e.get("raw")) for e in _entities(row) if e.get("hint") == "brand"}
        values |= {_val(e.get("spec_value") or e.get("raw")) for e in extra.get("specification") or [] if isinstance(e, dict)}
        values |= {_val(b) for b in (_mem_focus(row, "after").get("brands") or [])}
        # a warehouse filter: the `warehouse` entities and the memory slot of that name
        values |= {_val(e.get("canonical_code") or e.get("raw")) for e in _entities(row) if e.get("hint") == "warehouse"}
        values |= {_val(e.get("canonical_code") or e.get("raw")) for e in (_mem_focus(row, "after").get("warehouse") or [])
                   if isinstance(e, dict)}
        fwords = set(_val(flt.get("value")).split())
        # the entities cannot be read (payload omitted): the set header names the filter
        header_names = bool(fwords) and payload_omitted and fwords <= _header_words(row)
        phrase_read = bool(fwords) and fwords <= _entity_words(row)   # the words may be split across entities
        checks = [
            # narrowing may keep a part of the list: a non-empty subset of the gold subject or of what was held before
            ("subject", (want <= after_subject or bool(after_subject and (after_subject <= want or after_subject <= before_subject)))
             if want else None, "The subject was dropped when narrowing down."),
            ("filter", True if (_val(flt.get("value")) in values or phrase_read or header_names)
             else (None if payload_omitted else False),
             "The new filter was not applied."),
        ]
        if real:  # asking instead of looking up is not a refine that worked
            checks.append(("lookup", bool(_tools(row)), "The bot did not run a lookup after the filter."))
            # the filter must be applied: carried by a lookup's arguments, or held in memory after the turn
            held = {_val(e.get("spec_value") or e.get("raw")) for e in extra.get("specification") or [] if isinstance(e, dict)}
            held |= {_val(b) for b in (_mem_focus(row, "after").get("brands") or [])}
            fv = _val(flt.get("value"))
            # a value already held before the turn proves nothing from memory after: only a lookup's args carry it
            before = _mem_focus(row, "before")
            held_before = {_val(e.get("spec_value") or e.get("raw")) for e in (before.get("extra") or {}).get("specification") or []
                           if isinstance(e, dict)} | {_val(b) for b in before.get("brands") or []}
            if fv in held_before:
                held = set()
            # carried: every filter word is a WHOLE word of ONE argument of ONE call, or the squashed phrase is one word or
            # consecutive words of one string ("ptrap", "p trap"); no substring match ("300" is not in "3006"), no pooling
            ftoks = _word_tokens(_val(flt.get("value")))

            def carries(value: Any) -> bool:
                strs = [_word_tokens(x) for x in _strings(value)]
                pool = {w for toks in strs for w in toks}
                phrase = "".join(ftoks)
                return bool(ftoks) and (all(w in pool for w in ftoks) or any(
                    "".join(toks[i:j]) == phrase for toks in strs for i in range(len(toks)) for j in range(i + 1, len(toks) + 1)))

            carried = any(carries(v) for t in _tools(row) for v in (t.get("args") or {}).values())
            words_held = set()
            if fv not in held_before:
                words_held = (_words_of(*(x for e in (extra.get("specification") or []) + (extra.get("category") or [])
                                          for x in ((e.get("raw"), e.get("label"), e.get("spec_value")) if isinstance(e, dict) else (e,))),
                                        *(_mem_focus(row, "after").get("brands") or [])))
            applied_by_words = bool(fwords) and fwords <= words_held
            checks.append(("applied", fv in held or carried or applied_by_words or (header_names and fv not in held_before),
                           "The filter was read but never applied to a lookup."))
        prev = " ".join(str(row.get("prev_bot_message") or "").lower().split())
        if prev:  # the same answer again is no refinement
            checks.append(("changed_answer", " ".join(reply_text(row).lower().split()) != prev,
                           "The answer is the same as before the filter."))
        return checks
    if kind == "escalation":
        facts = _stage(row, "routed").get("facts") or {}
        why = str((_kind(row, "apply").get("decision") or {}).get("why") or "")
        offered = _oq(row)[1]   # a team-pick style question open after the turn is the hand-over being offered
        routed = (row.get("branch_kind") == "escalation" or facts.get("lane") == "escalation"
                  or why.startswith("escalation")
                  or (isinstance(offered, dict) and offered.get("kind") in HANDOVER_KINDS))
        sent = (_stage(row, "replied").get("facts") or {}).get("files")
        return [("route", routed, "The bot did not hand over to a person."),
                ("no_files", not (isinstance(sent, int) and sent > 0), "The bot sent files instead of handing over.")]
    if kind == "switch":
        # a typed literal that is no exact code but whose family the bot holds (it expanded to the held rows) is a
        # new subject and a change
        expanded = bool(gold.get("code_unresolved") and want and _mem_before and not want & _mem_before
                        and all(any(covered(a, {w}) for a in after_subject) for w in want))
        # a fact switch on the products already held: every gold code is held before the turn (a family literal by cover)
        same_subject = bool(want and _mem_before and all(
            w in _mem_before or (family and any(_covered(m, {w}) for m in _mem_before)) for w in want))
        return [
            ("old_dropped", not carried_old(), "The old subject was carried into the new ask."),
            ("new_subject", new_subject_ok(), "The new subject was not picked up."),
            ("changed", expanded or same_subject or not (_mem_before and after_subject == _mem_before), "The subject did not change."),
        ]
    if kind == "and_x" and gold.get("variant") == "new_fact":
        return [
            ("fact", _fact_ok(row, gold.get("fact")), "The new fact asked was not looked up."),
            ("subject", (want <= after_subject) if want else None, "The subject was dropped when a new fact was asked."),
        ]
    if kind == "and_x":
        return [
            ("fact", _fact_ok(row, gold.get("fact")), "The fact asked was not carried over to the new item."),
            ("old_dropped", not carried_old(), "The old item was carried into the new ask."),
            ("new_subject", new_subject_ok(), "The new item was not picked up."),
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


CLASS_KEYS = ("class", "product_type", "furniture_type")


_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm|cm|m|kg|g|l|litres?|liters?|w|v|inch|in)?")


def _val(x: Any) -> str:
    """A spec / filter value as a comparable string: lower case, "_" = space, a number with or without its unit
    ("300mm", "300 mm", 300) is the bare number."""
    text = re.sub(r"\s+", " ", _norm(x).replace("_", " "))
    m = _UNIT_RE.fullmatch(text)
    if m:
        n = float(m.group(1))
        return str(int(n)) if n == int(n) else str(n)
    return text


# the bot's own "nothing matches" forms ("But none matched.", "But no <X> matched these.", "No products found")
NO_MATCH_RE = re.compile(r"\bnone matched\b|\bbut no\b[^\n.]{0,80}?\bmatched these\b|\bno (?:products?|results?|items?) found\b"
                         r"|\bnone on record\b|\bwe don.?t have any (?!stock\b|incoming\b)"
                         r"|Couldn.?t find: [^\"“”\s][^\n(]*\([^)\n]+\)", re.I)   # the value-miss line has an unquoted value and a label


def _spec(row, item):
    extra = _mem_focus(row, "after").get("extra") or {}
    got = {_val(e.get("spec_value") if e.get("spec_value") is not None else e.get("raw")) for e in _entities(row)
           if e.get("hint") == "specification" or e.get("spec_key")}
    got |= {_val(e.get("spec_value") if e.get("spec_value") is not None else e.get("raw"))
            for e in extra.get("specification") or [] if isinstance(e, dict)}
    got.discard("")
    gold_spec = (item.get("gold") or {}).get("spec") or []
    want = {_val(s.get("value")) for s in gold_spec if s.get("key") not in CLASS_KEYS}
    class_want = {_val(s.get("value")) for s in gold_spec if s.get("key") in CLASS_KEYS}
    if not want and not got and not any(e.get("hint") == "category" for e in _entities(row)):
        return [("spec_set", None, "The spec words were not grounded to the expected set."),
                *([("class", None, "The product class was read differently from the one asked.")] if class_want else [])]
    words = [set(_val(e.get("canonical_code") or e.get("raw")).split()) for e in _entities(row) if e.get("hint") == "category"]
    words += [set(v.split()) for v in got]
    reply = reply_text(row)
    shown = bool(CODE_RE.search(reply)) and not COULD_NOT_FIND_RE.search(reply)   # at least one product, no dead end
    # a catalogue that holds nothing for the spec: the bot saying so is the answer, products listed as well are fine
    answer = (("none_on_record", bool(NO_MATCH_RE.search(reply)), "The bot did not say that no product matches the spec.")
              if (item.get("gold") or {}).get("none_on_record") else ("shown", shown, "The reply showed no product."))
    checks = [("spec_set", want <= got and bool(want or class_want), "The spec words were not grounded to the expected set."),
              answer, *_needless(row, {}, shown=shown, slice_name="spec")]
    if class_want:  # reported, not part of `passed`: the bot records class words as category entities
        checks.append(("class", all(any(set(v.split()) <= w for w in words) for v in class_want),
                       "The product class was read differently from the one asked."))
    return checks


def _type_word(x) -> str:
    return _norm(x).replace("_", " ")


ATTACHMENT_LIST_TOOL = "crm_master_product_attachments_list"


def _stems(text: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in re.findall(r"[a-z0-9]+", text)}


def _type_ok(word: str, accepted: set[str]) -> bool:
    """A file or asked type satisfies the gold type by stem and plural ("Product Videos" is "video"), or by name/code."""
    if word in accepted:
        return True
    ws = _stems(word)
    return bool(ws) and any((ast := _stems(a)) and (ast <= ws or ws <= ast) for a in accepted)


def _result_has_type(result: Any, accepted: set[str]) -> bool:
    """Does one attachments-list result hold a file of an accepted type? A live result is often a JSON string: parsed
    when it is JSON, else searched as plain text for the type name or code."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            text = result.lower().replace("_", " ")
            return any(a in text for a in accepted if a)

    def walk(x: Any) -> bool:
        if isinstance(x, dict):
            return (_type_ok(_type_word(x.get("attachmentType")), accepted) if x.get("attachmentType") else False) or any(
                walk(v) for v in x.values())
        return isinstance(x, list) and any(walk(v) for v in x)

    return walk(result)


def _args_codes(row: dict[str, Any], args: dict[str, Any]) -> set[str]:
    ids = _uuid_map(row)
    out: set[str] = set()
    for name in ("product_ids", "product_id", "scope_terms", "product_code", "product_codes"):
        for text in _strings((args or {}).get(name)):
            if text.lower() in ids:
                out.add(ids[text.lower()])
            elif re.search(r"\d", text) and not UUID_RE.fullmatch(text):
                out.add(_key(text))
    return out


def _honest_none(row: dict[str, Any], accepted: set[str], sent: int, code: Any = None) -> bool:
    """The attachments tool ran (full results), none of its results holds a file of the asked type, nothing was sent
    and the reply does not claim an attachment: the record has none, and the bot said so."""
    results = [t for t in row.get("tool_results") or [] if isinstance(t, dict) and t.get("name") == ATTACHMENT_LIST_TOOL]
    # the tool must have been asked about the item's product: the gold code, else a product held before or after
    # (not the product held AFTER the turn: that is the bot's own answer, so it would prove itself)
    typed = {_key(c) for c in CODE_RE.findall(user_message(row).upper())}
    expected = {_key(code)} if code else (_codes_of(_mem_focus(row, "before").get("products")) | typed)
    named = bool(results) and any(
        (targets := _args_codes(row, t.get("args") or {})) and bool(targets & expected) for t in results)
    return (named and sent == 0 and "attached" not in reply_text(row).lower()
            and not any(_result_has_type(t.get("result"), accepted) for t in results))


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
    asked_ok = bool(asked) and all(_type_ok(a, accepted) for a in asked)
    checks = [("asked_type", asked_ok, "The asked type was read differently from the type requested."),
              *_needless(row, gold, slice_name="attachment")]
    if gold.get("none_on_record"):
        checks.append(("none_on_record", not files and sent == 0,
                       "Files were sent although none exist on record."))
        return checks
    if not files and _honest_none(row, accepted, sent, gold.get("code")):
        return checks + [("none_on_record", True, "Files were sent although none exist on record.")]
    checks += [
        ("files", bool(files) and sent > 0, "No files were sent."),
        ("type", bool(files) and all(_type_ok(_type_word(f.get("attachmentType")), accepted) for f in files),
         "The files sent are not of the type asked."),
    ]
    if isinstance(gold.get("scheme_files"), list) and files:   # owner rule (CP0 c04/c19): a certification scheme is an index
        checks.append(("scheme", all(f.get("fileHash") in set(gold["scheme_files"]) for f in files),
                       "The certificates sent are not of the scheme asked."))
    return checks


def _scope(row, item):
    return _needless(row, item.get("gold") or {}, menu=False, slice_name="scope")


_SLICES = {"code_lookup": _code_lookup, "followup": _followup, "spec": _spec, "attachment": _attachment, "scope": _scope}


# the bot's own header forms only (answer.py build_set_header): "(N)" or "(N, showing A to B)", then optionally the moved-count
# note and the last-page note ("That is all N.", every product was listed on an earlier page)
SET_HEADER_RE = re.compile(r"\((\d+(?:,\d{3})*)(?:, showing \d+ to \d+)?\)(?:\. It was [\d,]+ when you asked\.)?"
                           r"(?P<last>\.?\s*That is all [\d,]+\.)?$")
SET_NUMBERED_RE = re.compile(r"^\d+\. ", re.M)
# a bullet / plain row whose first token is a product code (not "*DC1-SMC:* 0", not "• Sorento: 282")
SET_CODE_ROW_RE = re.compile(r"^(?:[•-]\s+)?(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z][A-Z0-9-]{3,}\b", re.M)
SET_CUT_RE = re.compile(r"showing\s+\d+\s+to\s+\d+", re.I)
SEE_MORE_RE = re.compile(r"\bsee more\b|\breply\s+more\b", re.I)


def _set_list_check(row: dict[str, Any]) -> tuple[str, bool, str] | None:
    """A set reply (its first line ends with a count in parentheses) must list products, and a list cut short says how
    to see more (the header's "showing A to B" or a see-more cue). None when the reply is not a set reply."""
    reply = reply_text(row)
    line = re.search(r"^Here's what you want:[^\n]*", reply, re.M)   # a prefix line above it is allowed
    header = SET_HEADER_RE.search(line.group(0).strip()) if line else None
    if not header or header.group("last"):   # the last page lists nothing by design
        return None
    entries = len(SET_NUMBERED_RE.findall(reply)) or len(SET_CODE_ROW_RE.findall(reply))
    if not entries:
        return ("set_list", False, "The set reply showed no product.")
    complete = entries >= int(header.group(1).replace(",", "")) or SET_CUT_RE.search(reply) or SEE_MORE_RE.search(reply)
    return ("set_list", bool(complete), "The list is cut short without saying how to see more.")


def _tier_spec_check(row: dict[str, Any], item: dict[str, Any]) -> tuple[str, bool, str] | None:
    """A price-tier ask on a spec item is fine only after products were found and all of them are in the spec: the
    codes the tools were called for plus the codes their results list."""
    spec_products = {_key(c) for c in (item.get("gold") or {}).get("spec_products") or []}
    if item.get("slice") != "spec" or not spec_products:
        return None
    ids = _uuid_map(row)
    found: set[str] = set()
    for t in _tools(row):   # product ids that map to a code, and literal codes; scope terms are not products found
        args = t.get("args") or {}
        found |= {ids[x.lower()] for n in ("product_ids", "product_id") for x in _strings(args.get(n)) if x.lower() in ids}
        found |= {_key(x) for n in ("product_code", "product_codes") for x in _strings(args.get(n)) if re.search(r"\d", x)}
    found |= {c for c in (_item_code(i) for i in _result_items(row)) if c}
    if not found:
        return ("tier_spec", False, "The bot asked which price tier before finding any product.")
    return ("tier_spec", found <= spec_products, "The products found do not match the spec asked.")


def _sibling_picker_ok(row: dict[str, Any], item: dict[str, Any], checks: list[tuple[str, bool | None, str]]) -> bool:
    """Owner ruling 17 Oct: after a FULL answer to an EXACT code, an open picker of that code's sibling variants is right
    (answer.py), so it is not a needless picker. Full = every other check is not False; sibling = the option's code head
    (before its first "-") equals the gold code's head, which must hold a digit (the engine's family_code_for). A
    did-you-mean is no answer."""
    gold = item.get("gold") or {}
    raw = gold.get("code") or next(iter(gold.get("subject_after") or []), None)   # a follow-up (and-x, switch) with no gold code names it as the subject
    code = _key(raw)
    if (not code or gold.get("code_unresolved") or not _reply_answers(row, item.get("slice") or "")
            or re.search(r"\b(?:couldn.?t|could not) find\b|\bdid you mean\b", reply_text(row), re.I)
            or any(ok is False for name, ok, _ in checks if name != "no_picker")):
        return False
    after = _oq(row)[1]
    if not (isinstance(after, dict) and after.get("kind") == "product_pick"):
        return False
    stem = _key(str(raw).split("-")[0])
    if not re.search(r"\d", stem):
        return False
    opts = [_key(str(o.get("code") or o.get("label")).split("-")[0]) for o in (after.get("options") or []) if isinstance(o, dict)]
    return bool(opts) and all(o == stem for o in opts)


def grade_turn(row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    checks = _SLICES[item["slice"]](row, item)
    set_list = _set_list_check(row)
    if set_list:   # first, so a set reply with no product is reported as that, not as the slice's own miss
        checks = [set_list, *checks]
    leaks = scope_leaks(row, item.get("asker"), item.get("watch_customer_names") or ())
    made_up = made_up_facts(row)
    # `class` is reported only; `old_dropped` is reported only on real traces (the old subject may stay in memory);
    # a None check is unknown, never a failure
    real = _has_memory_after(row)
    informational = {"class"} | ({"old_dropped"} if real else set())
    info = {name: ok for name, ok, _ in checks if name in informational}   # reported, never deciding
    checks = [c for c in checks if c[0] not in informational]
    if any(n == "no_picker" and ok is False for n, ok, _ in checks) and _sibling_picker_ok(row, item, checks):
        checks = [(n, True if n == "no_picker" else ok, m) for n, ok, m in checks]
    failed = next((msg for name, ok, msg in checks if ok is False), None)
    # A leak fails the item on any slice; made-up facts stay their own bar, counted by `summarise`.
    leak_msg = ("The reply shows an internal id." if any(x.startswith("internal id") for x in leaks)
                else "The reply shows a field hidden from the asker." if any(x.startswith("hidden") for x in leaks)
                else "The reply shows a customer outside the asker's linked customers." if leaks else None)
    failed = failed or leak_msg
    passed: bool | None = failed is None
    after_q = _oq(row)[1]
    tier = bool(isinstance(after_q, dict) and after_q.get("kind") == "tier_pick" and item.get("kind") != "escalation")
    tier_spec = _tier_spec_check(row, item) if tier else None
    if tier_spec:
        checks.append(tier_spec)
    if leak_msg and tier:   # a leak fails a tier ask too, with its own reason
        passed, failed = False, leak_msg
    elif tier_spec and tier_spec[1] is False:
        passed, failed = False, tier_spec[2]
    elif tier:
        passed, failed = None, "The bot asked an access question (price tier); the bench cannot judge it."
    elif passed and checks and all(ok is None for _, ok, _ in checks):
        passed, failed = None, "The bench could not confirm what the bot understood."
    return {
        "item_id": item.get("item_id"), "slice": item["slice"], "kind": item.get("kind"), "passed": passed,
        "reason": "The bot did what the message needed." if passed else failed,
        "checks": {name: ok for name, ok, _ in checks}, "info": info, "leaks": leaks, "made_up": made_up,
        **({"growth": True} if item.get("growth") else {}),
    }


# --------------------------------------------------------------------------- #
# Summary across runs
# --------------------------------------------------------------------------- #


def _stats(runs: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """Unknown turns (passed None) are counted apart and stay out of the rate's denominator."""
    def known(r: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [x for x in r if x["passed"] is not None]

    ns = [len(r) for r in runs]
    # a run with no known result for this group (absent or all unknown) stays out of rate and lowest
    rates = [sum(1 for x in known(r) if x["passed"]) / len(known(r)) for r in runs if known(r)]
    return {"n": round(sum(ns) / len(ns)) if ns else 0,
            "passed": round(sum(sum(1 for x in r if x["passed"]) for r in runs) / len(runs)) if runs else 0,
            "unknown": round(sum(len(r) - len(known(r)) for r in runs) / len(runs)) if runs else 0,
            "runs": len(rates),
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
    made_per_run = [sum(1 for x in run for m in x["made_up"] if not str(m).startswith("unverifiable")) for run in results]
    unverifiable = sum(1 for run in results for x in run for m in x["made_up"] if str(m).startswith("unverifiable"))
    meets = (all(n == 0 for n in leaks_per_run) and all(n == 0 for n in made_per_run)
             and all(name in slices and slices[name]["runs"] == len(results) and slices[name]["rate"] >= bar for name, bar in bars.items()))
    out = {"slices": slices, "growth": growth, "leaks": sum(leaks_per_run), "made_up": sum(made_per_run),
           "unverifiable": unverifiable, "meets": meets}
    if len(results) < 3:
        out.update(meets=False, reason=f"needs at least 3 runs, got {len(results)}")
    return out
