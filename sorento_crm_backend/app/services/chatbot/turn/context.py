"""Turn context assembly under a token budget (chatbot memory lane A, contract
section 6 / PLAN-chatbot-memory-26sep.md section 6). One pure function,
`assemble`, replaces the string-joining `parser.build_user_block` used to do
directly - that function becomes a caller of this one (level "off").

No side effects. No database import of any kind, no provider call - every layer's
content is handed in already loaded (a profile row, closed-frame summaries,
live-episode messages, focus, the open question, the current message); this
module only decides how much of it fits and in what order.

Tokens are estimated as `ceil(utf8_bytes / 3)` (contract section 6.2) - a rough,
declared-conservative count checked against the provider on real turns, never
trusted as exact.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

#: security review 26 Sep 2026 (S2): L5's project/about/note/list values are
#: JSON-quoted so a value carrying `;` or a newline can never be mistaken for a
#: segment boundary or a line of its own - the SAME reason L5 is built from a
#: structured fact list rather than a joined string re-split on `;` below.
_JSON_QUOTED_KEYS: frozenset[str] = frozenset({"project", "about", "note"})

#: Per-layer caps, est. tokens (contract section 6.2's own table).
CAPS: dict[str, int] = {"L1": 600, "L2": 350, "L3": 450, "L4": 250, "L5": 150}

#: The sum of the layer caps - no separate global rule (contract section 6.2).
TOTAL_CAP = 1800

_PREV_RESPONSE_CUT_BYTES = 600
#: 128, not 200 (reviewer pass at d89110c0, S2): three earlier messages at 200 bytes
#: each put the conversation level at +270 est. tokens over Off, against AC-MEM053's
#: +200. At 128 the worst case is +198 (conversation), +446 (episodes), +596 (full).
_EARLIER_MESSAGE_CUT_BYTES = 128
_SUMMARY_CUT_BYTES = 720
_CURRENT_MESSAGE_CUT_BYTES = 1500
_MAX_EARLIER_MESSAGES = 3
_MAX_PENDING_OPTIONS = 10

#: L5 drop order when the profile slice is over its cap (contract section 6.2's
#: own "note first, then project, then usual_sites" - continued past those three
#: with the rest of the vocabulary's least-identifying keys, least critical first).
_L5_DROP_ORDER: tuple[str, ...] = (
    "note", "about", "project", "usual_sites", "usual_brands", "usual_products", "role",
    "salesperson",
)


def est_tokens(value: str) -> int:
    """`ceil(utf8_bytes / 3)` - the estimator every cap in this module is stated
    in (contract section 6.2)."""
    if not value:
        return 0
    return math.ceil(len(value.encode("utf-8")) / 3)


#: How much of one exchange's text the parser reads (PR #1247 round 8). A ten-line
#: point-form question is about 250 characters; a stock answer for ten products is
#: longer, and its head is what a short reply refers to. Lives here, not in
#: `head/parser.py`, so the assembler and `parser.build_user_block` share one copy.
EXCHANGE_TEXT_CAP = 500


def exchange_text(value: Any) -> str:
    """One line of one exchange: newlines become " / ", at most `EXCHANGE_TEXT_CAP`."""
    text = " / ".join(part.strip() for part in str(value or "").splitlines() if part.strip())
    if len(text) > EXCHANGE_TEXT_CAP:
        return text[:EXCHANGE_TEXT_CAP] + "..."
    return text


def recent_exchange_lines(
    recent_exchanges: list[tuple[str, str]] | None, previous_response: str
) -> list[tuple[str, str]]:
    """PR #1247 round 8's "Recent exchanges" pairs as the `User:` / `Assistant:` lines
    the parser reads, oldest first. The newest reply IS the Previous response line, so
    it is not paid for twice - but only when it really is that reply (review S4);
    otherwise it is printed. `previous_response` is the already-normalized line value."""
    if not recent_exchanges:
        return []
    lines: list[tuple[str, str]] = []
    last = len(recent_exchanges) - 1
    for index, (user_text, assistant_text) in enumerate(recent_exchanges):
        assistant_line = (
            "Assistant: (the Previous response)"
            if index == last and str(assistant_text or "").strip() == previous_response.strip()
            else f"Assistant: {exchange_text(assistant_text)}"
        )
        lines.append((f"User: {exchange_text(user_text)}", assistant_line))
    return lines


_RECENT_EXCHANGES_HEADER = "Recent exchanges, oldest first:"


def known_brands_line(brands: list[dict[str, Any]] | None) -> str | None:
    """#1262 slice 9 (F1a): the `Known brands:` line - the live `Brand` rows for the
    contact's own companies as `name (code)` pairs, deduped by name (a brand active in
    more than one of the contact's companies prints once), `None` when nothing is left.
    Shared by `parser.build_user_block` and `assemble`, so both print the same line."""
    seen: set[str] = set()
    pairs: list[str] = []
    for row in brands or []:
        name = str((row or {}).get("brand_name") or "").strip()
        code = str((row or {}).get("brand_code") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name} ({code})" if code else name)
    if not pairs:
        return None
    return f"Known brands: {', '.join(pairs)}"


def promotion_kinds_line(kinds: list[dict[str, Any]] | None) -> str | None:
    """PROMO-SUBJECT-FILTER R5: the `Promotion kinds:` line, `type_code (name)` pairs read from
    `promotion_types` every turn, so a kind added as a row is heard on the next message."""
    pairs = [
        f"{code} ({name})" if name else code
        for code, name in (
            (str((k or {}).get("type_code") or "").strip(), str((k or {}).get("type_name") or "").strip())
            for k in kinds or []
        )
        if code
    ]
    return f"Promotion kinds: {'; '.join(pairs)}" if pairs else None


def product_types_line(names: list[str] | None) -> str | None:
    """PROMO-SUBJECT-FILTER round 3 (AC-29): the `Product types:` line, the live class names,
    so the parser answers a product type with a listed name."""
    listed = [n for n in dict.fromkeys(str(n or "").strip() for n in names or []) if n]
    return f"Product types: {', '.join(listed)}" if listed else None


def product_details_line(details: list[tuple[str, list[str]]] | None) -> str | None:
    """PROMO-SUBJECT-FILTER AC-36: the `Product details:` line, `spec_key (value, ...)` per
    registry key, so the parser answers a qualifier with a listed key and value."""
    parts = [f"{key} ({', '.join(values)})" for key, values in details or [] if key and values]
    return f"Product details: {'; '.join(parts)}" if parts else None


def _brands_and_kinds(layers: "ContextLayers") -> str | None:
    parts = [
        p
        for p in (
            known_brands_line(layers.brands),
            product_types_line(layers.product_types),
            product_details_line(layers.product_details),
            promotion_kinds_line(layers.promotion_kinds),
        )
        if p
    ]
    return "\n".join(parts) if parts else None


def _collapse_whitespace(value: str) -> str:
    """Newlines and repeated whitespace folded to single spaces (security review
    26 Sep 2026, S2) - a multi-line entity or fact value must never break this
    module's own line-based structure, and stray formatting is not something the
    parser needs to see."""
    return " ".join(value.split())


def _cut_bytes(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _previous_response_normalized(value: str) -> str:
    """The historic "Previous turn (kind)" -> "Previous turn" collapse
    (`head/parser.py::build_user_block`'s own regex substitution), replicated
    with plain string ops - the `turn/` package's own purity guard forbids the
    module a regex library import would need."""
    prefix = "previous turn ("
    if not value.lower().startswith(prefix):
        return value
    close = value.find(")")
    if close == -1:
        return value
    inner = value[len(prefix) : close]
    if inner and all(ch.isalpha() or ch == "_" for ch in inner):
        return "Previous turn" + value[close + 1 :]
    return value


@dataclass
class ContextLayers:
    """Everything `assemble` needs, already loaded - no I/O inside this module."""

    level: str
    #: A list of `{"key", "value"}` dicts, in vocabulary order - the structured
    #: shape `profile_facts.structured_slice` returns (security review 26 Sep
    #: 2026, S2). `None`/empty means no profile slice at all.
    profile_facts: list[dict[str, Any]] | None
    summaries: list[str] | None
    earlier_messages: list[dict[str, Any]] | None
    previous_response: str | None
    current_subject: str | None
    pending_kind: str | None
    pending_options: list[str] | None
    settings_profile_line: str | None
    current_message: str
    reply_to: str | None
    media_line: str | None
    #: PR #1247 (stock ask v2 S3 / rounds 8 and 9), carried through the merge with
    #: main: the `Open task: ...` lines (`parser.open_task_lines`), the one question on
    #: the table as its `Open question: {...}` line, and the last exchanges as
    #: `(user, assistant)` pairs straight from `turn_runtime.recent_exchanges`. Kept
    #: whole at every level: they are what a short reply answers.
    task_lines: list[str] | None = None
    open_question_line: str | None = None
    recent_exchanges: list[tuple[str, str]] | None = None
    #: #1301 (#1262 slice 9, F1a), carried through the round 5 merge with main: the
    #: live `Brand` rows (`turn_runtime.active_brands`), rendered as the one `Known
    #: brands:` line at every level. Kept whole, like the task lines: it is what a
    #: misspelt brand word is resolved against.
    brands: list[dict[str, Any]] | None = None
    #: PROMO-SUBJECT-FILTER R5: the live `promotion_types` rows (`{type_code, type_name}`),
    #: one `Promotion kinds:` line, so the parser fills `promotion_kind` from the table.
    promotion_kinds: list[dict[str, Any]] | None = None
    #: PROMO-SUBJECT-FILTER round 3 (AC-29): the live class names, one `Product types:` line.
    product_types: list[str] | None = None
    #: AC-36: the registry's descriptor keys and values, one `Product details:` line.
    product_details: list[tuple[str, list[str]]] | None = None
    #: CHAT-STATE: the rendered `State:` block. When set it replaces the five legacy state
    #: lines (Current subject, Open task, Open question, Pending, Open question options)
    #: and is never trimmed.
    state_block: str | None = None


def _l5_segment(fact: dict[str, Any]) -> str:
    """One fact rendered as `"key value"` - `project`/`about`/`note` and any
    list-kind value are JSON-quoted (security review 26 Sep 2026, S2), so a `;`,
    a newline or a quote inside the value can never be mistaken for a boundary
    between two of this layer's own segments or lines. Whitespace is collapsed
    first either way."""
    key = fact.get("key", "")
    value = fact.get("value")
    label = key.replace("_", " ")
    if key in _JSON_QUOTED_KEYS or isinstance(value, list):
        if isinstance(value, list):
            value = [_collapse_whitespace(str(v)) for v in value]
        else:
            value = _collapse_whitespace(str(value))
        # `ensure_ascii=False`: a Chinese `about` stays readable, never `\u....`
        # (reviewer pass at d89110c0, N2).
        return f"{label} {json.dumps(value, ensure_ascii=False)}"
    return f"{label} {_collapse_whitespace(str(value))}"


def _render_l5(profile_facts: list[dict[str, Any]] | None) -> tuple[str, bool]:
    if not profile_facts:
        return "", False
    header = "About this contact:"
    # Structured facts, in the caller's own (vocabulary) order - never a joined
    # string re-split on `;`, which corrupts the moment a JSON-quoted value
    # carries one inside its own quotes (S2).
    facts = list(profile_facts)

    def _fits(rows: list[dict[str, Any]]) -> bool:
        return est_tokens(header + "\n" + "; ".join(_l5_segment(f) for f in rows)) <= CAPS["L5"]

    if _fits(facts):
        return header + "\n" + "; ".join(_l5_segment(f) for f in facts), False

    dropped = False
    for key in _L5_DROP_ORDER:
        before = len(facts)
        facts = [f for f in facts if f.get("key") != key]
        if len(facts) != before:
            dropped = True
        if _fits(facts):
            return header + "\n" + "; ".join(_l5_segment(f) for f in facts), dropped

    # Every droppable key is gone and it is still over cap - a hard byte cut on
    # what is left is the last resort.
    dropped = True
    body = "; ".join(_l5_segment(f) for f in facts)
    header_bytes = len((header + "\n").encode("utf-8"))
    body = _cut_bytes(body, max(0, CAPS["L5"] * 3 - header_bytes))
    return header + "\n" + body, dropped


def _render_l4(summaries: list[str] | None) -> tuple[str, bool]:
    if not summaries:
        return "", False
    header = "Recent conversations:"
    # `summaries` arrives newest-first (the natural order of "closed frames by
    # recency, limit 3") - printed oldest first (AC-MEM065: "3 summaries printed
    # oldest first, however old"), so the actual OLDEST entry is at the END of
    # this reversed list and dropped from there first, never the newest.
    ordered = [_cut_bytes(_collapse_whitespace(s), _SUMMARY_CUT_BYTES) for s in reversed(summaries)]

    def _render(rows: list[str]) -> str:
        return header + "\n" + "\n".join(f"- {r}" for r in rows)

    dropped = False
    text = _render(ordered)
    while ordered and est_tokens(text) > CAPS["L4"]:
        ordered.pop(0)
        dropped = True
        text = _render(ordered)
    if not ordered:
        return "", dropped
    return text, dropped


def _render_l3(
    level: str,
    earlier_messages: list[dict[str, Any]] | None,
    previous_response: str | None,
    recent_exchanges: list[tuple[str, str]] | None = None,
) -> tuple[str, bool]:
    dropped = False
    previous_text = ""
    if previous_response:
        raw = str(previous_response)
        previous_text = _previous_response_normalized(_cut_bytes(raw, _PREV_RESPONSE_CUT_BYTES))
        if len(raw.encode("utf-8")) > _PREV_RESPONSE_CUT_BYTES:
            dropped = True

    # PR #1247 round 8's exchanges, compared against the FULL normalized previous
    # response (the same comparison `parser.build_user_block` makes), not the cut one.
    exchanges = recent_exchange_lines(
        recent_exchanges, _previous_response_normalized(str(previous_response or ""))
    )
    exchange_user_texts = {user_line[len("User: ") :] for user_line, _ in exchanges}

    # `earlier_messages` arrives oldest first (the header names it so) - dropping
    # the oldest one under budget pressure pops from the FRONT of this list. A message
    # the exchanges below already print is not paid for twice.
    messages: list[dict[str, Any]] = []
    if level in ("conversation", "episodes", "full"):
        messages = list((earlier_messages or [])[:_MAX_EARLIER_MESSAGES])
        if earlier_messages and len(earlier_messages) > _MAX_EARLIER_MESSAGES:
            dropped = True
        messages = [
            row for row in messages if exchange_text(row.get("text")) not in exchange_user_texts
        ]

    def _render(rows: list[dict[str, Any]], pairs: list[tuple[str, str]]) -> str:
        parts = []
        if rows:
            lines = "\n".join(
                f"- {row.get('created_at', '')} you: "
                f"{_cut_bytes(_collapse_whitespace(str(row.get('text') or '')), _EARLIER_MESSAGE_CUT_BYTES)}"
                for row in rows
            )
            parts.append("Earlier in this conversation (oldest first):\n" + lines)
        if pairs:
            parts.append(
                "\n".join([_RECENT_EXCHANGES_HEADER, *(line for pair in pairs for line in pair)])
            )
        if previous_text:
            parts.append(f"Previous response: {previous_text}")
        return "\n".join(parts)

    # Oldest first, and the older layer first: the episode's earlier messages go
    # before any exchange does, and the newest exchange is the last thing dropped.
    text = _render(messages, exchanges)
    while messages and est_tokens(text) > CAPS["L3"]:
        messages.pop(0)
        dropped = True
        text = _render(messages, exchanges)
    while len(exchanges) > 1 and est_tokens(text) > CAPS["L3"]:
        exchanges.pop(0)
        dropped = True
        text = _render(messages, exchanges)
    return text, dropped


def _render_l2(
    current_subject: str | None,
    pending_kind: str | None,
    pending_options: list[str] | None,
    task_lines: list[str] | None = None,
    open_question_line: str | None = None,
    brands_line: str | None = None,
    state_block: str | None = None,
) -> tuple[str, bool]:
    if state_block:
        # Never cut: the block is the whole state, and the shrink loop below only trims a subject.
        return "\n".join(p for p in (brands_line, state_block) if p), False
    dropped = False
    options_line: str | None = None
    if pending_options:
        options = list(pending_options)
        extra = 0
        if len(options) > _MAX_PENDING_OPTIONS:
            extra = len(options) - _MAX_PENDING_OPTIONS
            options = options[:_MAX_PENDING_OPTIONS]
            dropped = True
        options_line = "Open question options: " + "; ".join(options)
        if extra:
            options_line += f" (+{extra} more)"

    # "Kept whole: the open question kind" (contract section 6.2) - the pending
    # line is never touched; only the subject shrinks under pressure.
    pending_line = (
        f"Pending: the assistant is waiting for a {pending_kind} reply." if pending_kind else None
    )
    subject_text = str(current_subject) if current_subject else None

    def _render(subj: str | None) -> str:
        parts = []
        # #1301: the brand list is the most stable line in this layer, so it goes
        # first, and it is kept whole (only the subject shrinks).
        if brands_line:
            parts.append(brands_line)
        if subj:
            parts.append(f"Current subject: {subj}")
        # PR #1247: the open task lines and the Open question object, in the same
        # order `parser.build_user_block` prints them, and kept whole like the
        # pending line - only the subject shrinks.
        parts.extend(task_lines or [])
        if open_question_line:
            parts.append(open_question_line)
        if pending_line:
            parts.append(pending_line)
        if options_line:
            parts.append(options_line)
        return "\n".join(parts)

    text = _render(subject_text)
    subject_bytes = len((subject_text or "").encode("utf-8"))
    while subject_text and est_tokens(text) > CAPS["L2"] and subject_bytes > 0:
        subject_bytes = max(0, subject_bytes - 100)
        subject_text = _cut_bytes(subject_text, subject_bytes) or None
        dropped = True
        text = _render(subject_text)
    return text, dropped


def _render_l1(current_message: str, reply_to: str | None, media_line: str | None) -> tuple[str, bool]:
    raw_message = str(current_message or "")
    message = _cut_bytes(raw_message, _CURRENT_MESSAGE_CUT_BYTES)
    dropped = len(raw_message.encode("utf-8")) > _CURRENT_MESSAGE_CUT_BYTES

    lines: list[str] = []
    if reply_to:
        # The quote is cut FIRST when the layer is over budget (contract section
        # 6.2) - whatever room the (kept-whole) message left, capped at 600 bytes.
        message_bytes = len(message.encode("utf-8"))
        quote_budget = max(0, min(600, CAPS["L1"] * 3 - message_bytes))
        quote = _cut_bytes(str(reply_to), quote_budget)
        if quote:
            lines.append(f"reply to: {quote}")
        if len(str(reply_to).encode("utf-8")) > len(quote.encode("utf-8")):
            dropped = True
    if media_line:
        lines.append(str(media_line))
    lines.append(f"Current user message: {message}")
    return "\n".join(lines), dropped


def _assemble_off(layers: ContextLayers) -> tuple[str, dict[str, Any]]:
    """Byte-identical to today's `parser.build_user_block` output - level `off`
    carries none of the new layers, the settings `Profile:` line only when it has
    a value (contract section 2)."""
    previous = _previous_response_normalized(str(layers.previous_response or ""))
    lines = [
        f"Previous response: {previous}",
        f"Current user message: {layers.current_message}",
    ]
    if not layers.state_block:
        if layers.current_subject:
            lines.append(f"Current subject: {layers.current_subject}")
        lines.extend(layers.task_lines or [])
        if layers.open_question_line:
            lines.append(layers.open_question_line)
        if layers.pending_kind:
            lines.append(f"Pending: the assistant is waiting for a {layers.pending_kind} reply.")
        if layers.pending_options:
            lines.append("Open question options: " + "; ".join(layers.pending_options))
    if layers.settings_profile_line:
        lines.append(layers.settings_profile_line)
    brands_line = _brands_and_kinds(layers)
    if brands_line:
        lines.append(brands_line)
    if layers.state_block:
        lines.append(layers.state_block)
    exchanges = recent_exchange_lines(layers.recent_exchanges, previous)
    if exchanges:
        lines.append(_RECENT_EXCHANGES_HEADER)
        lines.extend(line for pair in exchanges for line in pair)
    text = "\n".join(lines)
    report = {
        "level": "off",
        "layers": [
            {"layer": name, "est_tokens": 0, "cap": cap, "dropped": False} for name, cap in CAPS.items()
        ],
        "total_est_tokens": est_tokens(text),
        "cap": TOTAL_CAP,
    }
    return text, report


def assemble(layers: ContextLayers) -> tuple[str, dict[str, Any]]:
    """The parser's user block, under budget (contract section 6.1/6.2).

    `level == "off"` is a completely separate, legacy-ordered path (today's
    `build_user_block`, byte for byte) - every other level renders the new,
    most-stable-first order: About this contact, Recent conversations, Earlier
    in this conversation, Previous response, Current subject, Pending / options,
    Current user message.
    """
    if layers.level == "off":
        return _assemble_off(layers)

    l5_text, l5_dropped = ("", False)
    if layers.level == "full":
        l5_text, l5_dropped = _render_l5(layers.profile_facts)

    l4_text, l4_dropped = ("", False)
    if layers.level in ("episodes", "full"):
        l4_text, l4_dropped = _render_l4(layers.summaries)

    l3_text, l3_dropped = _render_l3(
        layers.level, layers.earlier_messages, layers.previous_response, layers.recent_exchanges
    )
    l2_text, l2_dropped = _render_l2(
        layers.current_subject,
        layers.pending_kind,
        layers.pending_options,
        layers.task_lines,
        layers.open_question_line,
        _brands_and_kinds(layers),
        layers.state_block,
    )
    l1_text, l1_dropped = _render_l1(layers.current_message, layers.reply_to, layers.media_line)

    blocks = [b for b in (l5_text, l4_text, l3_text, l2_text, l1_text) if b]
    text = "\n".join(blocks)

    report = {
        "level": layers.level,
        "layers": [
            {"layer": "L5", "est_tokens": est_tokens(l5_text), "cap": CAPS["L5"], "dropped": l5_dropped},
            {"layer": "L4", "est_tokens": est_tokens(l4_text), "cap": CAPS["L4"], "dropped": l4_dropped},
            {"layer": "L3", "est_tokens": est_tokens(l3_text), "cap": CAPS["L3"], "dropped": l3_dropped},
            {"layer": "L2", "est_tokens": est_tokens(l2_text), "cap": CAPS["L2"], "dropped": l2_dropped},
            {"layer": "L1", "est_tokens": est_tokens(l1_text), "cap": CAPS["L1"], "dropped": l1_dropped},
        ],
        "total_est_tokens": est_tokens(text),
        "cap": TOTAL_CAP,
    }
    return text, report
