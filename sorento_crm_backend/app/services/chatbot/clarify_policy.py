"""CLARIFY-POLICY S1: the loop breaker (`documentation/plans/chatbot/PLAN-clarify-policy-11oct.md`,
UAC AC-1..AC-7, owner ruling Q4 11 Oct 2026).

A clarification is fingerprinted by what the BOT asked (ask kind, parameter, subject code),
never by the user's text and never by the reply wording. The fingerprint is stored with the
bot's own reply (`chatbot.turns.response["ask_fingerprint"]`, beside the `reply` that
`turn_runtime.previous_reply_text` reads), so there is no session schema change. When a turn
would send the same clarification a third time in a row, the reply becomes the handover offer
(or, for a contact who is offered no hand-off, a plain line saying what is missing, and with
escalation barred "Please refer to your salesman."). Never a guessed answer.

Applied once, at the single exit every turn passes (`engine._run_turn`, `apply_loop_breaker`),
so every ask site is covered, including those that arm no pending question (the DO / SO period
ask, the attachment type ask, the domain menu, the LLM clarifier).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Q4 (a): how many times in a row the same clarification may be sent; the next one breaks.
#: The ONE place this limit is read (`should_break`), so the DB-configurable policy can
#: replace it here.
MAX_IDENTICAL_ASKS = 2

#: The key the fingerprint is stored under on the turn's stored response.
RESPONSE_KEY = "ask_fingerprint"

#: Plain words for what is missing, by ask parameter, for a contact offered no hand-off.
_MISSING_WORDS = {
    "date_range": "the period",
}


def ask_fingerprint(kind: str, param: str, subject: str | None) -> str:
    """The identity of one clarification: kind, parameter and subject code (case-insensitive,
    trimmed). Takes no text, so wording can never enter the check (AC-2)."""
    code = (subject or "").strip().casefold()
    return f"{(kind or '').strip()}|{(param or '').strip()}|{code}"


def should_break(previous: list[str | None], current: str | None) -> bool:
    """True when the bot's last `MAX_IDENTICAL_ASKS` replies all carried `current` (AC-1).
    `previous` is oldest first; a non-clarification reply is None and resets the count (AC-4)."""
    if not current or len(previous) < MAX_IDENTICAL_ASKS:
        return False
    return all(f == current for f in previous[-MAX_IDENTICAL_ASKS:])


def _subject_code(focus: Any) -> str | None:
    """The turn's subject, as codes off the focus the turn ended with (products first, then
    customers): resolved codes, never the message text."""
    if not isinstance(focus, dict):
        return None
    for key in ("products", "customers"):
        codes = sorted(
            {
                str(row.get("canonical_code") or row.get("uuid") or "").strip()
                for row in focus.get(key) or []
                if isinstance(row, dict)
            }
            - {""}
        )
        if codes:
            return ",".join(codes)
    return None


def turn_fingerprint(trace: Any) -> tuple[str | None, str | None]:
    """`(fingerprint, param)` of the clarification this turn's reply carries, read off the
    turn's own trace records (what the bot decided, not what it said), else `(None, None)`.

    * `do_range` with `asked` / `so_list` with `period_asked`: the DO / SO period ask;
    * the turn plan's `ask` (`apply` record, e.g. `attachment_type_ask`);
    * the turn plan's `clarification` lane (the domain menu, the LLM clarifier)."""
    records = [r for r in trace or [] if isinstance(r, dict)]
    focus = None
    plan: dict[str, Any] = {}
    kind = param = None
    for rec in records:
        name = rec.get("kind")
        if name == "state" and isinstance(rec.get("focus"), dict):
            focus = rec["focus"]
        elif name == "apply" and isinstance(rec.get("plan"), dict):
            plan = rec["plan"]
        elif name == "do_range" and rec.get("asked"):
            kind, param = "period", "date_range"
        elif name == "so_list" and rec.get("period_asked"):
            kind, param = "period", "date_range"
    if kind is None and isinstance(plan.get("ask"), str) and plan["ask"]:
        kind, param = plan["ask"], "ask"
    if kind is None and plan.get("lane") == "clarification":
        kind, param = "clarification", "topic"
    if kind is None:
        return None, None
    return ask_fingerprint(kind, param or "", _subject_code(focus)), param


def previous_fingerprints(
    db: Any, *, contact_respond_id: str, ingress: str | None, is_test: bool, turn_id: Any
) -> list[str | None]:
    """The fingerprints stored with this contact's last `MAX_IDENTICAL_ASKS` completed replies,
    oldest first, scoped exactly as `turn_runtime.previous_reply_text` scopes its read."""
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import turn_runtime

    query = db.query(ChatbotTurn.response).filter(
        ChatbotTurn.contact_respond_id == str(contact_respond_id),
        ChatbotTurn.status == "done",
        ChatbotTurn.is_test.is_(bool(is_test)),
        ChatbotTurn.id != turn_id,
    )
    if str(ingress or "") == turn_runtime._CONSOLE_INGRESS:
        query = query.filter(ChatbotTurn.ingress == turn_runtime._CONSOLE_INGRESS)
    else:
        query = query.filter(ChatbotTurn.ingress != turn_runtime._CONSOLE_INGRESS)
    rows = query.order_by(*turn_runtime._newest_turn_first(ChatbotTurn)).limit(MAX_IDENTICAL_ASKS).all()
    out = [(r[0] or {}).get(RESPONSE_KEY) if isinstance(r[0], dict) else None for r in rows]
    return list(reversed(out))


def break_reply(profile: Any, param: str | None) -> str:
    """Q4: never a guessed answer. The handover offer when this contact is offered one, the
    refer line when its escalation is barred (AC-6), else a plain line naming what is missing."""
    from app.services.chatbot import escalation_control
    from app.services.chatbot.turn import state as turn_state
    from app.services.chatbot_reply_copy import CHATBOT_REPLY_ESCALATE_OFFER_NO_TEAM

    if turn_state.escalation_barred(profile):
        return escalation_control.barred_reply(profile)
    if turn_state.offers_escalation(profile):
        return CHATBOT_REPLY_ESCALATE_OFFER_NO_TEAM
    return f"I still need {_MISSING_WORDS.get(param or '', 'more detail')} to answer this."


def apply_loop_breaker(result: Any, session_factory: Any, *, turn_id: Any, dry_run: bool) -> None:
    """The one choke point (`engine._run_turn`, after the arms have finished): stamp this
    turn's ask fingerprint on its stored response and, on a third identical ask, replace the
    reply with `break_reply`, disarm any pending question and store no fingerprint (the
    breaking reply is not a clarification, AC-1). Best effort: a failure leaves the turn as
    composed."""
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import turn_runtime

    if getattr(result, "duplicate", False) or not isinstance(getattr(result, "reply", None), dict):
        return
    db = session_factory()
    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        if row is None or not isinstance(row.response, dict):
            return
        current, param = turn_fingerprint(row.trace)
        if current is None:
            return
        previous = previous_fingerprints(
            db,
            contact_respond_id=row.contact_respond_id,
            ingress=row.ingress,
            is_test=bool(row.is_test),
            turn_id=row.id,
        )
        if not should_break(previous, current):
            row.response = {**row.response, RESPONSE_KEY: current}
            db.commit()
            return
        profile, _recall = turn_runtime.load_profile(db, str(row.contact_respond_id or ""))
        text = break_reply(profile, param)
        old_text = (result.reply or {}).get("text")
        reply = {**result.reply, "text": text, "quick_replies": None}
        actions = [
            {**a, "text": text, "quick_replies": None}
            if isinstance(a, dict) and a.get("kind") == "send_message" and a.get("text") == old_text
            else a
            for a in (result.actions or [])
        ]
        stored = row.response
        row.response = {
            **stored,
            **({"reply": {**(stored.get("reply") or {}), **reply}} if "reply" in stored else {}),
            **({"actions": actions} if "actions" in stored else {}),
            RESPONSE_KEY: None,
            "loop_broken": current,
        }
        trace = [dict(r) if isinstance(r, dict) else r for r in (row.trace or [])]
        for record in trace:
            if not isinstance(record, dict) or record.get("stage") not in ("sent", "replied"):
                continue
            raw = record.get("raw")
            if not isinstance(raw, dict):
                continue
            if isinstance(raw.get("reply"), dict):
                record["raw"] = raw = {**raw, "reply": {**raw["reply"], "text": text}}
            if isinstance(raw.get("actions"), list):
                record["raw"] = {**raw, "actions": actions}
        trace.append({"kind": "loop_breaker", "fingerprint": current, "previous": previous})
        row.trace = trace
        if not dry_run:
            _disarm_pending(db, row)
        db.commit()
        result.reply, result.actions = reply, actions
        if isinstance(result.session_patch, dict) and result.session_patch.get("open_question"):
            result.session_patch = {**result.session_patch, "open_question": None}
    except Exception:  # noqa: BLE001 - the breaker never fails a turn that answered
        db.rollback()
        logger.warning("chatbot: the loop breaker did not run", exc_info=True)
    finally:
        db.close()


def _disarm_pending(db: Any, row: Any) -> None:
    """A live turn's session: the breaking reply arms no pending question (AC-1). Through the
    session store's own read and overwrite (`conversation_variables_service`)."""
    from app.services import conversation_variables_service as session_store

    respond_io_id = str(row.contact_respond_id or "")
    state = session_store.get_for_contact(db, respond_io_id=respond_io_id)
    if state.get("open_question"):
        session_store.overwrite_for_contact(db, respond_io_id=respond_io_id, state={**state, "open_question": None})
