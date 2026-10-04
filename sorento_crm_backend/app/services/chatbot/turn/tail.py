# Tail: persist the five-key session shape (PLAN-chatbot-turn-rearch.md "Tail",
# AC-1532). `pending' = composer.question` exactly (or none); the FOR UPDATE write seam
# is the already-committed `conversation_variables_service.overwrite_for_contact` (its
# own SQL is `SELECT id FROM respond_contacts WHERE respond_io_id = :cid FOR UPDATE`).
#
# The offer carriers, the answer-spending helper and the re-arm markers the old tail
# needed are all gone: a question reaches the next turn ONLY as `open_question`, written
# here from the composer's own question object, so there is nothing left to carry.
from __future__ import annotations

import logging
from typing import Any, Mapping

from app.services.conversation_variables_service import get_for_contact, overwrite_for_contact
from app.services.chatbot.turn.pending import to_wire
from app.services.chatbot.turn.state import State, focus_to_wire


def session_payload(state: State, answer: Any, ctx: Any) -> dict[str, Any]:
    """The five-key state this turn leaves behind - what `persist` writes.

    Split from the write so a DRY RUN can hand the same payload back as
    `TurnResult.session_patch` without writing it (D14, hand-pass-1 finding 2a): the
    console and the replay harness carry state from one test turn to the next through
    that field, and an asking turn whose patch came back None lost its open question on
    the very next turn.
    """
    return {
        "focus": focus_to_wire(state.focus),
        # `pending' = composer.question` exactly (AC-1532) - and when the composer asked
        # nothing, the roster that survived its OWN pick (contract 36) is still open, so
        # it is what stays. "No new question" is not "no question".
        "open_question": to_wire(getattr(answer, "question", None) or state.pending),
        "ideation": getattr(ctx, "ideation", None),
        "access_levels": getattr(ctx, "access_levels", []) or [],
        "contains_flyer": bool(getattr(ctx, "contains_flyer", False)),
        "reply_language": getattr(ctx, "reply_language", None),
    }


logger = logging.getLogger(__name__)


def _last_writer(db: Any, *, respond_io_id: str, turn_id: str | None) -> str | None:
    """The turn that last wrote this contact's state: `_log_session_write`'s own row."""
    from sqlalchemy import text

    try:
        row = db.execute(
            text(
                "SELECT business_id FROM integration_log "
                "WHERE business_table = 'respond_contacts.session_vars' "
                "AND external_reference = :cid AND CAST(business_id AS text) <> :tid "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": respond_io_id, "tid": str(turn_id or "")},
        ).first()
    except Exception:  # noqa: BLE001 - a log lookup must never fail the write
        try:
            db.rollback()  # the write is already committed; leave the session usable
        except Exception:  # noqa: BLE001
            pass
        return None
    return str(row[0]) if row else None


def _as_written_unchanged(base: Mapping[str, Any]) -> dict[str, Any]:
    """`base` as THIS turn would write it had it changed nothing.

    Loading is not lossless: `focus_from_wire` marks every carried entity as not named by
    this message, and `turn_runtime.load_state` ticks the open question's clock. A turn
    that never touched focus therefore still writes a focus that differs from the raw
    stored one, and comparing against the raw `base` would read that as "this turn
    changed it" and clobber the other turn's write (review round 2, M1).
    """
    from app.services.chatbot.turn.pending import from_wire, tick, to_wire as pending_wire
    from app.services.chatbot.turn.state import focus_from_wire

    out = dict(base)
    try:
        out["focus"] = focus_to_wire(focus_from_wire(base.get("focus")))
    except Exception:  # noqa: BLE001 - an unreadable focus just compares raw
        pass
    try:
        out["open_question"] = pending_wire(tick(from_wire(base.get("open_question"))))
    except Exception:  # noqa: BLE001
        pass
    return out


def write_merged(
    db: Any,
    *,
    respond_io_id: str,
    payload: dict[str, Any],
    base: Mapping[str, Any] | None,
    turn_id: str | None,
) -> dict[str, Any]:
    """Write this turn's five keys onto the state AS IT IS NOW, not as it was at the start.

    CHATBOT-QUEUE-FIX (crew decision, 1 Oct): a turn that ran past a queue timeout
    overlaps the slow predecessor for the same contact, and a blind overwrite would erase
    whatever that predecessor wrote in between. Under the row lock: when the stored state
    still equals `base` (what this turn read at its start, the overwhelming case) the
    write is exactly `payload`. Otherwise a key this turn left unchanged keeps the other
    turn's value, and a key BOTH changed is last-write-wins, logged at WARNING with both
    turn ids. The written key set is always `payload`'s (legacy keys still migrate away).
    """
    from sqlalchemy import text

    from app.services.chatbot import session_state

    merged = payload
    clobbered: list[str] = []
    merged_any = False
    if base is not None:
        # The same lock `overwrite_for_contact` takes, taken FIRST so the read below and
        # the write after it are one critical section.
        db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :cid FOR UPDATE"),
            {"cid": respond_io_id},
        )
        stored = get_for_contact(db, respond_io_id=respond_io_id)
        now = session_state.five_keys({"session_vars": stored})
        if any(now.get(k) != base.get(k) for k in payload):
            unchanged = _as_written_unchanged(base)
            merged = {}
            for key, mine in payload.items():
                start = base.get(key)
                # A key absent from the stored TOP level was not written by the other
                # turn: a legacy `{"variables": ...}` row that only gained a top-level
                # `ideation` mid-turn reads every other key as None (review W2).
                theirs = now.get(key) if key in stored else start
                mine_untouched = mine == start or mine == unchanged.get(key)
                if mine_untouched and theirs != start:
                    merged[key] = theirs
                    merged_any = True
                else:
                    merged[key] = mine
                    if theirs != start and theirs != mine:
                        clobbered.append(key)
    written = overwrite_for_contact(db, respond_io_id=respond_io_id, state=merged)
    if clobbered or merged_any:
        # Looked up AFTER the write committed, outside the lock: a failed lookup must
        # not abort the transaction the write rides on (review W1).
        other = _last_writer(db, respond_io_id=respond_io_id, turn_id=turn_id)
        if clobbered:
            logger.warning(
                "chatbot session state: turn %s overwrote %s written by turn %s for "
                "contact %s (both changed it; last write wins)",
                turn_id,
                ", ".join(clobbered),
                other,
                respond_io_id,
            )
        else:
            logger.info(
                "chatbot session state: turn %s merged onto turn %s's write for %s",
                turn_id,
                other,
                respond_io_id,
            )
    return written


def persist(
    state: State,
    answer: Any,
    ctx: Any,
    *,
    base: Mapping[str, Any] | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    db = getattr(ctx, "db", None)
    respond_io_id = getattr(ctx, "contact_respond_id", None)
    return write_merged(
        db,
        respond_io_id=respond_io_id,
        payload=session_payload(state, answer, ctx),
        base=base,
        turn_id=turn_id,
    )
