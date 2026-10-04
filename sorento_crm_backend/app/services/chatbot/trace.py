"""The turn trace: one record per stage, written as each stage ends (AC-007, D13).

The operator journey is the whole point. `summary` and `why` are SENTENCES the engine
composes from structured state - never the customer's text, never JSON - so the screen in
S2b renders words a non-engineer can read ("Understood as a business query about product
SRTWC8517, dealer tier" / "Routed to the business lane: access allowed, no escalation
asked"). `facts` is a small flat dict for the row's key/value chips, and `raw` is the
technical payload behind "Technical details", byte-capped the way the AI-assistant trace
already caps its spans.

Nothing here decides anything. A stage that did not run for a lane is simply absent, not
recorded as `skipped` with an empty body - AC-252 says an unrun stage is omitted.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.services.chatbot.contracts import TraceStatus, TurnStage

# One stage's `raw` payload, capped. A parser response plus a session blob is a few KB;
# 32 KB leaves room for a large result set without letting one turn's trace dominate the
# row. Over the cap, the payload is replaced by a note naming its size, so the reader is
# told the truth instead of being shown a silently truncated object.
RAW_BYTE_CAP = 32_768


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# The whole ENVELOPE and the whole RESPONSE, capped the same way and for the same reason:
# `chatbot.turns` is written once per customer message forever, and one media-heavy
# envelope or one result set of ten thousand rows would otherwise sit in the row for the
# life of the table. Larger than the trace's per-stage cap because both are load-bearing -
# a duplicate delivery replays `response`, and Retry re-injects `envelope` - so the cap has
# to be a guardrail against the pathological case, not a routine truncation.
#
# Twice the endpoint's 256 KB body limit on purpose: a body the route ACCEPTED must never
# come back capped, or Retry would re-inject a note. This only ever fires on a document the
# engine grew itself (a huge result set) or on an internal caller that skipped the route.
DOCUMENT_BYTE_CAP = 524_288


def cap_document(value: Any, *, limit: int = DOCUMENT_BYTE_CAP) -> Any:
    """`_cap` for a whole stored document. Over the limit, a note that names the size."""
    return _cap(value, limit=limit)


# One MCP envelope, inside a `tool` event. Smaller than the per-stage `raw` cap and
# capped at WRITE time rather than only on read, because this is what lands in
# `chatbot.turns.trace` forever: a result set of ten thousand rows would otherwise sit in
# the row for the life of the table, and the turn-detail screen renders this one inline
# where a stage's `raw` only ever appears behind "technical details".
#
# TRUNCATE, never drop, and on ENCODED BYTES rather than code points: a code-point cut on a
# multibyte (e.g. Chinese) envelope either undercounts against the byte cap or slices a
# character in half. `ensure_ascii=False` for the same reason - the default escapes every
# non-ASCII character to a pure-ASCII `\uXXXX` sequence, which could never be cut
# mid-character and would make the distinction moot.
#
# The read side (`trace_detail._cap_envelope`, the trace-UI lane) applies the same cap to
# the same shape, so a payload already capped here passes through it untouched. Two copies
# until that lane merges, on purpose: this module must not import a file that does not
# exist on this branch.
TOOL_ENVELOPE_BYTE_CAP = 8_192


def cap_envelope(envelope: Any, *, limit: int = TOOL_ENVELOPE_BYTE_CAP) -> Any:
    """A tool envelope, truncated to `limit` bytes with a marker saying so."""
    if envelope is None:
        return None
    try:
        encoded = json.dumps(envelope, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - a trace must never fail the turn
        return {"truncated": True, "note": "payload is not JSON-serialisable"}
    encoded_bytes = encoded.encode("utf-8")
    if len(encoded_bytes) <= limit:
        return json.loads(encoded)
    return {
        "truncated": True,
        "bytes": len(encoded_bytes),
        "head": encoded_bytes[:limit].decode("utf-8", errors="ignore"),
    }


def _cap(raw: Any, *, limit: int = RAW_BYTE_CAP) -> Any:
    if raw is None:
        return None
    try:
        encoded = json.dumps(raw, default=str)
    except Exception:  # noqa: BLE001 - a trace must never fail the turn
        return {"note": "payload is not JSON-serialisable"}
    if len(encoded) <= limit:
        return json.loads(encoded)
    return {
        "note": f"payload omitted: {len(encoded)} bytes exceeds the {limit} byte cap",
    }


class TurnTrace:
    """An ordered list of stage records, plus the clock for the stage in progress."""

    __slots__ = ("_records", "_events", "_started_perf", "_started_iso")

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        # A9 (chatbot-growth-r1): sub-events WITHIN a stage - one MCP tool call, one
        # cross-domain rung probe, which restricted fields a turn saw/granted/dropped.
        #
        # A SEPARATE LIST from `_records`, but the SAME persisted array. They are held
        # apart in memory so `persisted()` can put every stage record first and every
        # event after, in one pass, whatever order the turn produced them in - the
        # trace screen reads the stage timeline top to bottom, and an event interleaved
        # into it would read as a stage that has no summary. On the way out they are
        # one list, `chatbot.turns.trace`, because Slice D's reader
        # (`trace_detail.py`, the trace-UI lane) composes the turn-detail view from
        # exactly that column and a second column would be a migration for nothing.
        #
        # An event is `{"kind": ..., "at": ..., **payload}` - `kind` FLAT, not nested
        # under a wrapper, because that is the shape the reader keys on
        # (`_kind_records`) and the same key is what tells the two apart:
        # `test_trace_legibility.py` demands a plain-language summary/why/raw of every
        # entry WITHOUT a `kind`, and a tool call's raw args and envelope are not prose.
        self._events: list[dict[str, Any]] = []
        self._started_perf: float | None = None
        self._started_iso: str | None = None

    @classmethod
    def resume(cls, records: Any) -> "TurnTrace":
        """Continue the trace a delegated turn already started (S2's `/complete`).

        The head wrote `received` through `routed` and closed the row; the tail appends
        `replied` and `remembered` to the SAME array, so the screen renders one timeline
        rather than two half-turns the operator has to join up mentally.
        """
        trace = cls()
        if isinstance(records, list):
            entries = [r for r in records if isinstance(r, dict)]
            # Split back into the two lists the way `persisted()` joined them, so a tail
            # that appends `replied` and `remembered` still lands them among the STAGES
            # rather than after the head's tool event.
            trace._records = [r for r in entries if r.get("kind") is None]
            trace._events = [r for r in entries if r.get("kind") is not None]
        trace.start()
        return trace

    def start(self) -> None:
        """Mark the beginning of the next stage. Idempotent within a stage."""
        self._started_perf = time.perf_counter()
        self._started_iso = _now_iso()

    def record(
        self,
        stage: TurnStage,
        *,
        status: TraceStatus = "ok",
        summary: str,
        why: str,
        facts: dict[str, Any] | None = None,
        error: str | None = None,
        raw: Any = None,
    ) -> None:
        """Append one stage record and reset the clock for the next stage."""
        started_perf = self._started_perf if self._started_perf is not None else time.perf_counter()
        self._records.append(
            {
                "stage": stage,
                "status": status,
                "started_at": self._started_iso or _now_iso(),
                "ms": int((time.perf_counter() - started_perf) * 1000),
                "summary": summary,
                "why": why,
                "facts": facts or {},
                "error": error,
                "raw": _cap(raw),
            }
        )
        self.start()

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        """A sub-event WITHIN the current stage (A9, chatbot-growth-r1): one MCP tool
        call, one cross-domain rung probe, or which restricted fields a turn saw /
        granted / dropped. See the `_events` slot's own comment for why this is a
        SEPARATE list from `record()`'s, not an entry appended to it.

        `payload` is SPREAD into the entry beside `kind` and `at`, not nested under a
        `payload` key: the reader keys on the flat shape
        (`trace_detail._kind_records` reads `entry["name"]` / `["args"]` /
        `["envelope"]` / `["ms"]` straight off it).

        An `envelope` value is capped to 8 KB with a `truncated` marker first
        (`cap_envelope`), then the WHOLE entry rides through the same 32 KB `_cap` a
        stage's `raw` does, as a backstop for an unexpectedly large `args`. An entry
        that trips the backstop keeps its `kind` and `at` and carries the note in
        place of its fields, so the screen says what happened rather than showing a
        silently shortened object.
        """
        entry = dict(payload)
        if "envelope" in entry:
            entry["envelope"] = cap_envelope(entry["envelope"])
        capped = _cap(entry)
        self._events.append(
            {
                "kind": kind,
                "at": _now_iso(),
                **(capped if isinstance(capped, dict) else {"value": capped}),
            }
        )

    @property
    def records(self) -> list[dict[str, Any]]:
        return self._records

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._events

    def persisted(self) -> list[dict[str, Any]]:
        """What goes into `chatbot.turns.trace`: the stage records, then the events.

        The ONE thing `_close_turn` is handed, so an event cannot be persisted by some
        call sites and dropped by others - which is what "not yet persisted anywhere"
        meant before, and why Slice D's reader saw no `tool` entry on any real turn.
        """
        return [*self._records, *self._events]

    def stages(self) -> list[str]:
        return [r["stage"] for r in self._records]


# --------------------------------------------------------------------------- #
# Sentence builders. D11: composed from STRUCTURED STATE, never from the
# customer's words and never by an LLM.
# --------------------------------------------------------------------------- #

_LANE_WORDS = {
    "access_denied": "Access refused",
    "escalate_offer": "Escalation offer",
    "out_of_scope": "Escalation",
    "ideate": "Idea capture",
    "offer_hold": "Holding an offer open",
    "escalation_declined": "Escalation declined",
    "check_promotion": "Business query: promotion",
    "low_signal": "Small talk",
    "clarify_menu": "Asked to clarify",
    "not_supported": "Not supported",
    "stock_denied": "Stock access refused",
    "demand_qty": "Asked for a quantity",
    "business_query": "Business query",
}


def lane_words(branch_kind: str | None, domain_hint: str | None = None) -> str:
    """The lane in plain words, for the Turn line's status row (AC-251)."""
    base = _LANE_WORDS.get(branch_kind or "", "Unknown lane")
    if branch_kind == "business_query" and domain_hint:
        return f"{base}: {domain_hint.replace('_', ' ')}"
    return base


MAX_ENTITY_CHARS = 40


def _clip(value: str) -> str:
    return value if len(value) <= MAX_ENTITY_CHARS else value[: MAX_ENTITY_CHARS - 1] + "\u2026"


def understood_summary(qf: dict[str, Any]) -> str:
    """"Understood as a business query about product SRTWC8517, dealer tier"."""
    message_type = (qf.get("message_type") or "unknown").replace("_", " ")
    parts = [f"Understood as {message_type}"]
    domain = qf.get("domain_hint")
    if domain:
        parts.append(f"about {str(domain).replace('_', ' ')}")
    entities = qf.get("entities") if isinstance(qf.get("entities"), list) else []
    # Truncated: an entity's `raw` is CUSTOMER TEXT, and an unbounded one turns a one-line
    # summary into a paragraph on the trace screen (and a long row in the turn record).
    named = [
        _clip(str(e.get("canonical_code") or e.get("raw")))
        for e in entities
        if isinstance(e, dict) and (e.get("canonical_code") or e.get("raw"))
    ][:3]
    if named:
        parts.append("(" + ", ".join(named) + ")")
    tiers = qf.get("access_levels") if isinstance(qf.get("access_levels"), list) else []
    if tiers:
        parts.append(", ".join(str(t).replace("_", " ") for t in tiers) + " tier")
    return " ".join(parts)


def routed_why(branch_kind: str, qf: dict[str, Any], access_allowed: bool, *, lane: str | None = None) -> str:
    """One sentence naming the reason this lane won, from state only."""
    escalation = qf.get("escalation") if isinstance(qf.get("escalation"), dict) else {}
    if branch_kind == "out_of_scope" and lane == "escalation_barred":
        return (
            "Escalation withheld: the customer asked for a person, but this contact may not "
            "escalate, so they are referred to their salesman and nothing is handed over."
        )
    if branch_kind == "access_denied":
        return "Routed to the refusal: this contact is not granted the agent the turn needs."
    if branch_kind == "out_of_scope":
        return "Routed to escalation: the customer asked for a person or accepted the offer."
    if branch_kind == "escalation_declined":
        return "Routed to the decline: the customer turned the escalation offer down."
    if branch_kind == "low_signal":
        return "Routed to small talk: no domain and no decisive intent were understood."
    if branch_kind == "clarify_menu":
        return "Routed to the clarify menu: the turn was understood as a clarification."
    if branch_kind == "not_supported":
        return "Routed to the refusal: the domain asked about is not supported."
    if branch_kind == "ideate":
        return "Routed to idea capture: the turn proposes an idea."
    if branch_kind == "offer_hold":
        return "Held the previous offer open: the reply neither picked nor abandoned it."
    if escalation.get("is_escalation_confirmation") is True:
        return "Routed by the escalation confirmation the customer gave."
    access_words = "access allowed" if access_allowed else "access refused"
    return f"Routed to the {lane_words(branch_kind).lower()}: {access_words}, no escalation asked."


def replied_summary(reply: dict[str, Any], branch_kind: str | None) -> str:
    """"Replied with a 6-row list and 2 quick replies." - what was actually sent."""
    rows = len((reply.get("session_patch") or {}).get("variables", {}).get("last_result_set") or [])
    quick = reply.get("quick_replies")
    quick_count = len([q for q in str(quick).split(",") if q.strip()]) if quick else 0
    parts = [f"Replied on the {lane_words(branch_kind).lower()} lane"]
    if rows:
        parts.append(f"with {rows} row{'s' if rows != 1 else ''} the customer can pick from")
    if quick_count:
        parts.append(f"and {quick_count} quick repl{'ies' if quick_count != 1 else 'y'}")
    return " ".join(parts) + "."


# Keys whose value is machinery rather than memory. They are still PERSISTED (R2 keeps
# every key); they are simply not worth a row on a screen whose whole point is "what will
# the bot carry into the next turn".
_MEMORY_NOISE: frozenset[str] = frozenset({"response", "user_goal"})


def memory_delta(*, before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """Kept / New / Cleared session keys (AC-254), from the two states themselves.

    A key is CLEARED when it had a meaningful value and now has none - `None`, `[]`, `{}`
    or `""` all count, because "the bot no longer remembers the picker" is the operator's
    question and an empty list is the same answer as a missing key.
    """

    def _has(state: dict[str, Any], key: str) -> bool:
        value = state.get(key)
        return not (value is None or value == [] or value == {} or value == "")

    keys = (set(before) | set(after)) - _MEMORY_NOISE
    kept, new, cleared = [], [], []
    for key in sorted(keys):
        had, has = _has(before, key), _has(after, key)
        if had and has:
            kept.append(key)
        elif has:
            new.append(key)
        elif had:
            cleared.append(key)
    return {"kept": kept, "new": new, "cleared": cleared}


def remembered_summary(delta: dict[str, list[str]], *, dry_run: bool) -> str:
    """"Remembered 4 things, learned 2, forgot 1." / the dry-run form."""
    lead = "Would remember" if dry_run else "Remembered"
    return (
        f"{lead} {len(delta['kept'])} thing{'s' if len(delta['kept']) != 1 else ''} from "
        f"before, learned {len(delta['new'])}, forgot {len(delta['cleared'])}."
    )
