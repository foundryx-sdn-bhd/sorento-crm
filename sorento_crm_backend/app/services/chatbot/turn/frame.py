# Frame: the conversation state of ONE question, owned by its intent (INTENT-FRAMES, owner
# 4 Oct 2026).
#
# The focus used to be one global sticky bag: every new question merged into it as a
# refinement, so an old ask's entities leaked into a new one ("taiyang only" asked for
# stock after a ranking by sales agent W... answered "Could not find inventory for W...").
# The focus IS the frame now, and `focus.intent` (STUCK-QTY-LOOP) names its owner:
#
#   * a refinement of the same intent narrows the frame (today's reading, unchanged);
#   * a NEW intent starts a clean frame. The old ask's own filters never move over; its
#     subject moves over only when the parser says this message is about the same subject
#     (`entity_op: "reuse"`, `anaphora.backward_reference`, `continuation`), and only on an
#     axis the message did not name itself. No word rules: those parser fields alone;
#   * nothing expires by time or by turn count (owner ruling Q1): a frame ends by context
#     only - the parser's reading of the next message (above), a reset, or an access
#     change, which drops the whole frame, held questions included.
#
# Every `Focus` field belongs to exactly one class below or to `held.FOCUS_HELD`;
# `test_intent_frames.py` fails when a field is added without one.
#
# Pure, like the rest of this package: no I/O. `apply()` calls `on_new_intent`; the
# engine calls `expire` at load and `stamp` where the focus is written.
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.chatbot.turn import held
from app.services.chatbot.turn.decide import NEW_INTENT
from app.services.chatbot.turn.state import Focus, State

__all__ = [
    "CONTACT_AXES", "OWNED_AXES", "STAMPS", "SUBJECT_AXES", "SUBJECT_EXTRA",
    "carries_subject", "expire", "on_new_intent", "stamp",
]

#: What the question is about. `extra` holds kinds of both classes: only `SUBJECT_EXTRA`
#: describes the products or the order; every other kind is the ask's own filter.
SUBJECT_AXES: tuple[str, ...] = ("products", "customers", "warehouse", "extra")
SUBJECT_EXTRA: frozenset[str] = frozenset({"specification", "category", "order"})
#: How that one ask is filtered or routed: never carried into another intent's frame.
OWNED_AXES: tuple[str, ...] = (
    "outstanding_brand_ids", "domains", "document", "status", "sales_channel", "date_window",
    "sort", "low_stock",
)
#: The contact's, not the question's (`apply.RESET_KEEPS`): they survive every frame change.
CONTACT_AXES: tuple[str, ...] = ("tier", "brands")
#: Who owns the frame and under which access it was written.
STAMPS: tuple[str, ...] = ("intent", "frame_access")


def carries_subject(verdict: dict[str, Any]) -> bool:
    """Does the parser say this message is about the subject already in the frame?"""
    anaphora = verdict.get("anaphora")
    return (
        verdict.get("entity_op") == "reuse"
        or (isinstance(anaphora, dict) and anaphora.get("backward_reference") is True)
        or verdict.get("continuation") is True
    )


def _clean(focus: Focus, *, keep_subject: bool) -> Focus:
    """A frame with only the contact's axes, the held questions (`held.py` owns their
    lifecycle) and the stamps - plus the subject when it is carried."""
    keep = (*CONTACT_AXES, *held.FOCUS_HELD, *STAMPS)
    fresh = Focus(**{name: getattr(focus, name) for name in keep})
    if keep_subject:
        fresh.products = list(focus.products)
        fresh.customers = list(focus.customers)
        fresh.warehouse = list(focus.warehouse)
        fresh.extra = {kind: list(rows) for kind, rows in focus.extra.items() if kind in SUBJECT_EXTRA}
    return fresh


def on_new_intent(state: State, verdict: dict[str, Any], trace: Any) -> State:
    """APPLY's first step on a message `held.consume` marked as a new intent: a clean
    frame, the subject carried only on the parser's own signal. The message's own
    entities then land on it through the focus rules, as on any turn."""
    if verdict.get(NEW_INTENT) is not True:
        return state
    keep_subject = carries_subject(verdict)
    trace.rules_fired.append("frame_new_intent")
    if keep_subject:
        trace.rules_fired.append("frame_carries_subject")
    return replace(state, focus=_clean(state.focus, keep_subject=keep_subject))


def _has_frame(focus: Focus) -> bool:
    if focus.intent:
        return True
    return any(getattr(focus, name) for name in (*SUBJECT_AXES, *OWNED_AXES, *held.FOCUS_HELD))


def expire(state: State) -> tuple[State, str | None]:
    """At load: drop the whole frame (held questions and the open question included)
    when it was written under access the contact no longer has (stock mode, escalation,
    reveals: `Profile.access_fp`).

    An unstamped frame (written before this shipped) is kept and stamped on this turn's
    write; an unknown access (a failed read) never counts as a change."""
    focus = state.focus
    if not _has_frame(focus) and state.pending is None:
        return state, None
    current = getattr(state.profile, "access_fp", None)
    if not (focus.frame_access and current and focus.frame_access != current):
        return state, None
    fresh = Focus(**{name: getattr(focus, name) for name in CONTACT_AXES})
    return replace(state, focus=fresh, pending=None), "access_changed"


def stamp(state: State) -> State:
    """Where the focus is written: the access the frame was written under. An empty frame
    carries no stamp; a failed access read keeps the old one."""
    focus = replace(state.focus)
    if not _has_frame(focus) and state.pending is None:
        focus.frame_access = None
    else:
        focus.frame_access = getattr(state.profile, "access_fp", None) or focus.frame_access
    return replace(state, focus=focus)
