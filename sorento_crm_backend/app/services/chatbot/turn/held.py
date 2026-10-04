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
#   * a question held under other access (stock mode, escalation, reveals) is dropped with
#     the rest of its frame (`turn/frame.py::expire`).
#
# INTENT-FRAMES (owner ruling Q1, 4 Oct 2026): nothing expires by time or by turn count.
# A held question ends only by context: the parser's reading of the next message, an
# access change, or a reset.
#
# What counts as held is the registry below. `test_held_state.py` fails when a field is
# added to `Focus` or `State` without being classified here, and when a pending kind is
# minted that the rule does not know.
#
# Pure, like the rest of this package: no I/O. `engine.py` calls `consume` once the
# verdict is read.
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.decide import NEW_INTENT, decide
from app.services.chatbot.turn.policy_rows import DEFAULT_DOMAIN_ROWS
from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS, is_roster
from app.services.chatbot.turn.state import Focus, State

__all__ = ["CLOSE_OFFER", "NEW_INTENT", "SLOTS", "consume", "held_slots", "without_unanswered_offer"]

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
    "intent", "low_stock", "frame_access",
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
    return replace(state, focus=focus, pending=pending)


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


#: Verdict marker `consume` sets when the open question carries an escalation offer the
#: message did not answer; `apply` closes that offer once it has read the turn
#: (`without_unanswered_offer`), after the arms that settle the question's subject.
CLOSE_OFFER = "_close_offer"


def _offers_escalation(pending: Any) -> bool:
    return pending is not None and (
        pending.kind in ESCALATION_OFFER_KINDS or pending.payload.get("escalate_offered") is True
    )


def without_unanswered_offer(asked: Any, carried: Any) -> Any:
    """INTENT-FRAMES security review B1: an escalation offer lives exactly as long as the
    contact's next message. The offer the message did not answer (`asked`, still carried
    as `carried`) is closed - nothing counts turns: the next message IS the context - and a
    roster keeps its pick but loses the escalate sentence printed under it, so a later
    "ok" never hands the conversation to a person nobody asked for (the AC-816 defect the
    3-turn clock used to bound). A question this turn asked anew is untouched."""
    if carried is None or asked is None or carried.kind != asked.kind or carried.options != asked.options:
        return carried
    if carried.kind in ESCALATION_OFFER_KINDS:
        return None
    if carried.payload.get("escalate_offered") is True:
        payload = {k: v for k, v in carried.payload.items() if k != "escalate_offered"}
        return replace(carried, payload=payload)
    return carried


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
    # INTENT-FRAMES: only a business question asks something new. An escalation request,
    # a clarification or a casual turn is about the frame it is in, whatever intent the
    # parser carried onto it ("please escalate to marketing team" after a stock answer).
    changed = changed and verdict.get("message_type") in ("business_query", None)
    holding = bool(held_slots(state))
    answers = holding and (bool(answered) or _answers(state, verdict))
    starts = changed and not answers
    if starts:
        # INTENT-FRAMES: a message that answers the held question is inside that question's
        # frame whatever intent rides on it (a bare "2" carrying a stale hint picks the
        # tier for the product the roster was asked about, #1353), so only a message that
        # answers nothing starts a new frame.
        verdict[NEW_INTENT] = True
        # An entity the parser copied from the conversation (`current_message: false`) is
        # the OLD frame's, never this message's: the frame alone decides what carries
        # (`frame.on_new_intent`). Left in, an echo replaced the carried settled row with a
        # raw code the resolver widened to its family, or leaked an old product into a
        # question that carries nothing (reviewer B2, live pass 4 Oct 2026).
        verdict["entities"] = [
            e for e in verdict.get("entities") or [] if not (isinstance(e, dict) and e.get("current_message") is False)
        ]
    if intent and (prior is None or starts):
        # The frame's owner changes only when a frame starts: a casual turn or an answer
        # carrying a stale hint never re-labels the frame it is in (reviewer S1).
        state = replace(state, focus=replace(state.focus, intent=intent))
    if not holding:
        return state, None
    if verdict.get("topic_reset") is True:
        return _cleared(state), "topic_reset"
    if changed and not answers:
        # A roster accepts ONLY a position or one of its own offered labels, so it is kept
        # (the new question is still answered as itself; it cannot be captured): "incoming
        # srtwc286" -> "4" -> "check stock" -> "7" still picks (#1352). Reset and access
        # change still drop it.
        keep = state.pending is not None and (is_roster(state.pending.kind) or state.pending.kind in ESCALATION_OFFER_KINDS)
        if _offers_escalation(state.pending):
            verdict[CLOSE_OFFER] = True
        return _cleared(state, keep_pending=keep), "new_intent"
    if not answers and _offers_escalation(state.pending):
        verdict[CLOSE_OFFER] = True
        return state, "offer_unanswered"
    return state, None
