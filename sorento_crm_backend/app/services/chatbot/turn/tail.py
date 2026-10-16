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


def carry_live_state(
    state: State, asked: bool, live: Mapping[str, Any], *, non_answer: bool = False
) -> State:
    """QUOTED-REPLY R4 and AC-QR-26 (owner rulings, 7 Oct 2026): what a turn that ran on a quoted
    turn's state writes back. A follow-up question of this turn's own (a pending, or a NEW open
    stock task) replaces the live picker and the live task, as a fresh ask does. Only when the
    turn opened nothing are the live open question and live open task restored (an answered or
    inherited own task never replaces an open live one). A non-answer lane (`non_answer`) that
    asked nothing keeps the WHOLE live focus."""
    from dataclasses import replace

    from app.services.chatbot.turn.task import OPEN

    def _open_stock(focus: Any) -> Any:
        return next((t for t in focus.tasks or () if t.kind == "stock_qty" and t.status == OPEN), None)

    quoted_open = live.get("quoted_open")

    def _inherited(task: Any) -> bool:
        # An open task on the quoted product's own slots, however often it was re-touched,
        # is the quote's, not one this turn opened.
        return task is None or (
            quoted_open is not None and {s.key for s in task.slots} == {s.key for s in quoted_open.slots}
        )

    focus = state.focus
    own = _open_stock(focus)
    if not asked and _inherited(own) and (non_answer or own is not None):
        # A canned / low-signal / clarify / out-of-scope lane answered nothing (or a lane only
        # re-asked the quote's own task): the quoted state is never written as the live state,
        # so the WHOLE live focus stays.
        # The set-aside slots belong to the quoted focus, not to the live one kept here.
        return replace(state, focus=live["focus"], pending=live["pending"], quoted_live=None, held_aside=None)
    live_open = _open_stock(live["focus"])
    mine = _open_stock(focus)
    # AC-QR-26 (owner ruling, 7 Oct 2026): a quoted answer that opened a follow-up question of
    # its own (a pending, or a NEW open stock task) replaces the live picker, as a fresh ask
    # does; the live pending and live open task are then not restored.
    known = [t for t in (live_open, quoted_open) if t is not None]
    new_task = mine is not None and all({s.key for s in mine.slots} != {s.key for s in t.slots} for t in known)
    if asked or new_task:
        return replace(state, pending=state.pending if asked else None, quoted_live=None)
    # An open task still on the quote's own slots was not opened by this turn.
    if live_open is not None and _inherited(mine):
        kept = tuple(t for t in focus.tasks or () if t.kind != "stock_qty")
        focus = replace(focus, tasks=(*kept, live_open))
    return replace(state, focus=focus, pending=state.pending if asked else live["pending"], quoted_live=None)


_ITEM_KEYS = ("entity_type", "uuid", "uuids", "name")


def _option_item(option: dict[str, Any]) -> dict[str, Any] | None:
    position = option.get("position")
    if not isinstance(position, int) or isinstance(position, bool):
        return None
    payload = option.get("payload") if isinstance(option.get("payload"), dict) else {}
    code = option.get("code") or payload.get("value")
    item: dict[str, Any] = {
        "position": position,
        "label": option.get("label") or option.get("name") or code,
        "code": code,
    }
    for key in _ITEM_KEYS:
        if option.get(key):
            item[key] = option[key]
    if payload.get("value") is not None:
        item["value"] = payload["value"]
    return item


def _row_item(position: int, row: dict[str, Any]) -> dict[str, Any]:
    fields = [f for f in row.get("fields") or [] if isinstance(f, dict)]
    code = next(
        (str(f["value"]).strip() for f in fields if f.get("label") == "Product Code" and f.get("value")), None
    )
    first = next((str(f["value"]).strip() for f in fields if f.get("value") not in (None, "")), None)
    label = code or row.get("title") or first or str(position)
    item: dict[str, Any] = {"position": position, "label": label, "code": code}
    if code:
        item["entity_type"] = "product"
    return item


def last_list_after(previous: Any, *, question: Any, sections: Any) -> list[dict[str, Any]] | None:
    """The numbered list this turn showed, for `Focus.last_list`. Question options keep
    their position, label, code and keys; the rows of the one section that has rows are
    numbered by their order. Options win a position clash; several sections with rows
    record no list; nothing shown keeps `previous`."""
    options = [
        item
        for item in (_option_item(o) for o in (getattr(question, "options", None) or []) if isinstance(o, dict))
        if item is not None
    ]
    with_rows = [s for s in (sections or []) if getattr(s, "figures", None)]
    if len(with_rows) > 1:
        return options or None
    rows: list[dict[str, Any]] = []
    if with_rows:
        rows = [
            _row_item(i, fig) for i, fig in enumerate(with_rows[0].figures, start=1) if isinstance(fig, dict)
        ]
    merged = {item["position"]: item for item in rows}
    merged.update({item["position"]: item for item in options})
    if not merged:
        return previous
    return [merged[p] for p in sorted(merged)]


def session_payload(state: State, answer: Any, ctx: Any) -> dict[str, Any]:
    """The five-key state this turn leaves behind - what `persist` writes.

    Split from the write so a DRY RUN can hand the same payload back as
    `TurnResult.session_patch` without writing it (D14, hand-pass-1 finding 2a): the
    console and the replay harness carry state from one test turn to the next through
    that field, and an asking turn whose patch came back None lost its open question on
    the very next turn.
    """
    if getattr(state, "quoted_live", None):
        state = carry_live_state(state, getattr(answer, "question", None) is not None, state.quoted_live)
    from app.services.chatbot.turn import chat_state

    focus = chat_state.restored(state).focus
    if getattr(state, "state_path", False):
        # CHAT-STATE: a turn on the state path records the list it showed.
        from dataclasses import replace

        focus = replace(
            focus,
            last_list=last_list_after(
                focus.last_list,
                question=getattr(answer, "question", None),
                sections=getattr(answer, "sections", None) or [],
            ),
        )
    return {
        "focus": focus_to_wire(focus),
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
