# Held: the ONE rule for every question the bot is holding open (STUCK-QTY-LOOP, owner
# 4 Oct 2026).
#
# Live 4 Oct 02:56-03:00: an availability contact's open stock task replayed "How many
# units for each? 1. SRTWB1086 - ..." on every message - a low stock report, the switch
# to compact and "clear" included - because each kind of held question had its own
# lifecycle and none of them knew about the others' (task.run's RESUME arm took any
# entity-less inventory message as "back to the stock check"). The owner's rule, for
# every question or picker the bot can hold:
#
#   * a held question captures a reply only when the reply ANSWERS it, read off the
#     parser's own fields (no keyword rules);
#   * a message with a different intent that answers nothing is a new question: every
#     held question goes;
#   * a reset (`topic_reset`, the parser's "clear" / "never mind") clears them all;
#   * they expire: `HELD_TTL_TURNS` turns after they last changed;
#   * a question held under other access (stock mode, escalation, reveals) is dropped.
#
# What counts as held is the registry below. `test_held_state.py` fails when a field is
# added to `Focus` or `State` without being classified here, and when a pending kind is
# minted that the rule does not know.
#
# Pure, like the rest of this package: no I/O. Three seams in `engine.py` call it:
# `expire` at load (before the parser sees an open question), `consume` once the verdict
# is read, and `stamp` where the five keys are written.
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.decide import NEW_INTENT, decide
from app.services.chatbot.turn.policy_rows import DEFAULT_DOMAIN_ROWS
from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS, from_wire, is_roster, to_wire
from app.services.chatbot.turn.state import Focus, State, focus_from_wire

__all__ = ["NEW_INTENT", "HELD_TTL_TURNS", "SLOTS", "consume", "expire", "held_slots", "stamp"]

#: Turns a held question survives after it last changed. Escalation offers keep their
#: own shorter clock (`pending.OFFER_TTL`, 3). Six: a roster picked row by row changes on
#: every pick, so only a question nobody has touched for six turns lapses.
HELD_TTL_TURNS = 6

#: The intents the routing table knows (`policy_rows.DEFAULT_DOMAIN_ROWS`, every row's
#: `intents`). An intent outside it never counts as a change of question.
KNOWN_INTENTS: frozenset[str] = frozenset(
    intent for row in DEFAULT_DOMAIN_ROWS for intent in (row.get("intents") or [])
)

#: The `Focus` fields that hold a question the next message can answer.
FOCUS_HELD: tuple[str, ...] = ("tasks", "required_ask", "set_page", "set_clarify", "top_selling")
#: The `Focus` fields that hold no question: the subject and the stamps themselves.
FOCUS_NOT_HELD: tuple[str, ...] = (
    "products", "customers", "warehouse", "brands", "outstanding_brand_ids", "tier",
    "domains", "document", "status", "sales_channel", "date_window", "sort", "extra",
    "intent", "held_turn", "held_access",
)
#: The `State` fields. `ideation` is the ideate lane's draft pointer: the MCP intake tool
#: owns it and closes it on the draft's own terminal status (`lanes/ideate.py`), and the
#: tail writes it from that tool's reply, not from this state - so it is not held here.
STATE_HELD: tuple[str, ...] = ("pending",)
STATE_NOT_HELD: tuple[str, ...] = ("focus", "profile", "turn_no", "ideation")


def _focus_slot(name: str, empty: Any) -> tuple[Callable[[State], bool], Callable[[Focus], None]]:
    def is_held(state: State) -> bool:
        return bool(getattr(state.focus, name))

    def clear(focus: Focus) -> None:
        setattr(focus, name, empty)

    return is_held, clear


#: slot -> (is it held?, clear it on a focus copy). `state.pending` is every kind of open
#: question at once (`pending.PENDING_KINDS` and every narrower-minted roster): the rule
#: never reads the kind, so a new kind needs no arm here.
SLOTS: dict[str, tuple[Callable[[State], bool], Callable[[Focus], None] | None]] = {
    "state.pending": (lambda state: state.pending is not None, None),
    "focus.tasks": _focus_slot("tasks", ()),
    "focus.required_ask": _focus_slot("required_ask", None),
    "focus.set_page": _focus_slot("set_page", None),
    "focus.set_clarify": _focus_slot("set_clarify", None),
    "focus.top_selling": _focus_slot("top_selling", None),
}


def held_slots(state: State) -> list[str]:
    """The slots holding a question right now."""
    return [name for name, (is_held, _clear) in SLOTS.items() if is_held(state)]


def _cleared(state: State, *, keep_pending: bool = False) -> State:
    focus = replace(state.focus)
    for _name, (_is_held, clear) in SLOTS.items():
        if clear is not None:
            clear(focus)
    pending = state.pending if keep_pending else None
    if pending is None:
        focus.held_turn = None
        focus.held_access = None
    return replace(state, focus=focus, pending=pending)


def expire(state: State) -> tuple[State, str | None]:
    """At load: drop every held question built under other access, or past its TTL.

    An unstamped held state (written before this rule shipped, or by n8n) is kept and gets
    stamped on this turn's write: it is judged from then on, never dropped wholesale."""
    if not held_slots(state):
        return state, None
    focus = state.focus
    current = getattr(state.profile, "access_fp", None)
    if focus.held_access and current and focus.held_access != current:
        return _cleared(state), "access_changed"
    if focus.held_turn is not None and state.turn_no - focus.held_turn > HELD_TTL_TURNS:
        return _cleared(state), "ttl"
    return state, None


def _answers(state: State, verdict: dict[str, Any]) -> bool:
    """Does this message answer a held question, by the parser's own fields?

    The parser's declared answer (`open_question_answer`, `answers_open_question`), an
    escalation offer accepted, declined or picked from, a position or an offered label
    picked off the open question (`decide`'s ANSWER), or a quantity the stock task claims."""
    answer = verdict.get("open_question_answer")
    if isinstance(answer, dict) and answer.get("mode"):
        return True
    declared = verdict.get("answers_open_question")
    if isinstance(declared, dict) and (declared.get("resolved") is True or declared.get("picks")):
        return True
    escalation = verdict.get("escalation") if isinstance(verdict.get("escalation"), dict) else {}
    if state.pending is not None and state.pending.kind in ESCALATION_OFFER_KINDS and (
        escalation.get("is_escalation_confirmation") is True
        or escalation.get("escalation_declined") is True
        or escalation.get("company_pick")
    ):
        return True
    if state.pending is not None and decide(verdict, state.focus, state.pending).answers:
        return True
    for task in state.focus.tasks or ():
        impl = task_mod.TASK_KINDS.get(task.kind)
        if (
            impl is not None
            and task.status != task_mod.ANSWERED
            and impl.claims(verdict)
            and task_mod._only_task_slots(task, verdict, allow_quantities=True)
        ):
            return True
    return False


def _answers_only_by_pick(pending: Any) -> bool:
    """Does this open question accept nothing but a pick off its own options?"""
    return pending is not None and (pending.kind in ESCALATION_OFFER_KINDS or is_roster(pending.kind))


def consume(
    state: State, verdict: dict[str, Any], *, answered: list[str] | tuple[str, ...] = ()
) -> tuple[State, str | None]:
    """Once the verdict is read: a reset clears every held question, and so does a message
    of a different intent that answers none of them. `answered` names the held slots an
    engine reader answered from their own offered options this turn (the clarify pick, the
    set count, the required ask's reply, a top selling answer): an answer, never dropped.

    Records this message's intent on the focus (the last one named is kept through a
    message that names none), and marks the verdict `NEW_INTENT` when the intent changed,
    so `decide()` reads a would-be refinement as a fresh ask: the old ask's carried
    entities never ride into the new one (crew report 2, "taiyang only")."""
    intent = verdict.get("intent_hint")
    intent = intent if isinstance(intent, str) and intent else None
    prior = state.focus.intent
    # Only a change between two KNOWN intents counts: `intent_hint` is the parser's own
    # snake_case phrase, and a synonym it drifts to between turns is not a new question.
    changed = bool(intent and prior and intent != prior and {intent, prior} <= KNOWN_INTENTS)
    if changed:
        verdict[NEW_INTENT] = True
    if intent and intent != prior:
        state = replace(state, focus=replace(state.focus, intent=intent))
    if not held_slots(state):
        return state, None
    if verdict.get("topic_reset") is True:
        return _cleared(state), "topic_reset"
    if changed and not answered and not _answers(state, verdict):
        # A question that accepts ONLY a position or one of its own offered labels is kept
        # (the new question is still answered as itself; it cannot be captured): an
        # escalation offer (`decide`, #1323, with its own `pending.OFFER_TTL` clock; the
        # subject a miss offered it over is what "cert?" next is about, #833) and a roster
        # (`pending.is_roster`; "incoming srtwc286" -> "4" -> "check stock" -> "7" still
        # picks, #1352). Reset, TTL and access change still drop both.
        return _cleared(state, keep_pending=_answers_only_by_pick(state.pending)), "new_intent"
    return state, None


def _view(focus: Focus, pending: Any) -> tuple[Any, ...]:
    """The held questions as comparable values. The escalation offers' own clock (`ttl`)
    ticks every turn and is not a change to the question."""
    question = to_wire(pending)
    if question is not None:
        question["payload"] = {k: v for k, v in (question.get("payload") or {}).items() if k != "ttl"}
    return (
        question,
        [task_mod.task_to_wire(task) for task in focus.tasks or ()],
        focus.required_ask,
        focus.set_page,
        focus.set_clarify,
        focus.top_selling,
    )


def stamp(state: State, stored: Mapping[str, Any] | None, *, question: Any = None) -> State:
    """Where the five keys are written: stamp the turn and the access the held questions
    were (re)asked under. `question` is the open question being written when it is not
    `state.pending` (the composer's own). Nothing held clears the stamp; an unchanged held
    state keeps its old one, so the TTL counts from the last change."""
    written = question if question is not None else state.pending
    after = replace(state, pending=written)
    focus = replace(state.focus)
    if not held_slots(after):
        focus.held_turn = None
        focus.held_access = None
        return replace(state, focus=focus)
    stored = stored or {}
    was = _view(focus_from_wire(stored.get("focus")), from_wire(stored.get("open_question")))
    if was != _view(state.focus, written) or focus.held_turn is None:
        focus.held_turn = state.turn_no
        # An unknown access (a failed read) keeps the stamp it had rather than writing one
        # the next turn would read as a change.
        focus.held_access = getattr(state.profile, "access_fp", None) or focus.held_access
    return replace(state, focus=focus)
