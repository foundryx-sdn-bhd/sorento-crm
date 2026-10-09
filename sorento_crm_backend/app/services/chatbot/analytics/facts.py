"""The database half of the chat facts (CA-1): turn trace in, fact rows out.

`record_turn` writes the fact for one closed live turn (the engine calls it right after
the turn commits), `sweep` fills every closed live turn that has none (the one-time load
and the nightly safety net), and `snapshot_episode` writes the analytic row of a closed
episode from inside the frame writer's own transaction. The classification itself lives in
`outcome.py`, which is pure; this module only reads the trace, looks the contact and the
catalogue up, and writes.

**The writer runs with no company scope.** It is called from the engine, a script and the
scheduler, none of which carries a caller. `products`, `brands` and `respond_contact_customers`
are company-owned, so an ORM read of them on such a session returns nothing (fail closed),
and a dealer would be recorded as `unknown`. They are therefore read as CORE tables
(`Model.__table__`), which the ORM scope listener does not touch - the same trick, for the
same reason, as `reports/datasets/sponsorship_forms.py`.

**Nothing here copies what a customer wrote.** The turn dict handed to `outcome` carries no
message; the fact keeps codes and CRM ids only.

**Failure never costs a turn.** The engine calls `record_turn` after its own commit inside
a try/except, and `write_episode_for_reset` calls `snapshot_episode` inside a SAVEPOINT, so
an analytics error leaves the turn closed and the frame written. The sweep refills what a
failure skipped.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.access import RespondContact, RespondContactCustomer
from app.models.chat_facts import ChatAskFact, ChatAskItem, ChatEpisodeFact
from app.models.chatbot_turn import ChatbotTurn
from app.models.conversation_frame import ConversationFrame
from app.models.product import Brand, Product
from app.models.user import User
from app.services.chatbot.analytics import outcome as outcome_mod

logger = logging.getLogger(__name__)

# Core tables, not mapped entities (see the module docstring).
_CONTACTS = RespondContact.__table__
_LINKS = RespondContactCustomer.__table__
_USERS = User.__table__
_PRODUCTS = Product.__table__
_BRANDS = Brand.__table__
_FRAMES = ConversationFrame.__table__
_TURNS = ChatbotTurn.__table__
_FACTS = ChatAskFact.__table__
_ITEMS = ChatAskItem.__table__
_EPISODES = ChatEpisodeFact.__table__

#: The two states a turn ENDS in (`engine._TERMINAL_STATUSES`); `delegated` is a handover.
_CLOSED = ("done", "failed")

#: The largest value of the `reply_ms` integer column.
_MAX_REPLY_MS = 2_147_483_647

#: Turns fetched per round trip by the sweep. The trace is the heavy column.
_SWEEP_CHUNK = 100


def _turn_dict(row: ChatbotTurn) -> dict[str, Any]:
    """The slice of a turn `outcome` reads. Deliberately has no `message`."""
    return {
        "id": row.id,
        "created_at": row.created_at,
        "branch_kind": row.branch_kind,
        "status": row.status,
        "trace": row.trace if isinstance(row.trace, list) else [],
    }


def _eligible(row: ChatbotTurn | None) -> bool:
    """A closed LIVE turn. Test turns and console turns never become facts (D14)."""
    return (
        row is not None
        and not row.is_test
        and row.ingress != "console"
        and row.status in _CLOSED
    )


# ------------------------------------------------------------------------ who asked


def _who(db: Session, contact_respond_id: str) -> dict[str, Any]:
    """The contact's identity as a fact records it: audience, contact type and the primary
    customer.

    `audience` (the design column): `staff` when a user is linked to the contact, else
    `dealer` when it has a customer link, else `unknown`.

    `contact_type` (what the owner reads), first match: `office` when a user is linked,
    `dealer` when the contact has any customer link, `not_allowed` when the contact's AI
    switch is off, `office` otherwise (an unlinked contact the bot answers is someone inside),
    and `unknown` when there is no contact row at all.

    The customer is the PRIMARY link (a person can be the contact for two accounts; the
    partial unique index allows one primary), else the first by id.
    """
    contact = db.execute(
        select(_CONTACTS.c.id, _CONTACTS.c.chatbot_allowed)
        .where(_CONTACTS.c.respond_io_id == contact_respond_id)
        .order_by(_CONTACTS.c.created_at.asc(), _CONTACTS.c.id.asc())
        .limit(1)
    ).first()
    if contact is None:
        return {
            "contact_id": None, "customer_id": None, "company_id": None,
            "audience": "unknown", "contact_type": "unknown",
        }
    contact_id = contact.id

    link = db.execute(
        select(_LINKS.c.customer_id, _LINKS.c.company_id)
        .where(_LINKS.c.contact_id == contact_id)
        .order_by(_LINKS.c.is_primary.desc(), _LINKS.c.id.asc())
        .limit(1)
    ).first()
    is_staff = db.execute(
        select(_USERS.c.id).where(_USERS.c.respond_contact_id == contact_id).limit(1)
    ).first()
    if is_staff is not None:
        audience, contact_type = "staff", "office"
    elif link is not None:
        audience, contact_type = "dealer", "dealer"
    else:
        audience = "unknown"
        contact_type = "office" if contact.chatbot_allowed else "not_allowed"
    return {
        "contact_id": str(contact_id),
        "customer_id": str(link.customer_id) if link is not None else None,
        "company_id": str(link.company_id) if link is not None and link.company_id else None,
        "audience": audience,
        "contact_type": contact_type,
    }


# ------------------------------------------------------------------------- items


def _lookup(db: Session, table: Any, code_column: Any, codes: list[str]) -> dict[str, list[tuple[str, str | None]]]:
    """`{lower(code): [(id, company_id), ...]}` for the codes that equal a catalogue code
    exactly, ignoring case. Never a prefix or a name match: a wrong item is worse than none."""
    if not codes:
        return {}
    lowered = sorted({c.lower() for c in codes})
    found: dict[str, list[tuple[str, str | None]]] = {}
    for row in db.execute(
        select(table.c.id, table.c.company_id, code_column).where(func.lower(code_column).in_(lowered))
    ):
        found.setdefault(str(row[2]).lower(), []).append(
            (str(row[0]), str(row[1]) if row[1] else None)
        )
    return found


def _pick(candidates: list[tuple[str, str | None]], company_id: str | None) -> str | None:
    """One code can exist in two companies. Prefer the fact's own company, else the only
    match, else none: guessing would credit one company's product to another's ask."""
    if company_id:
        mine = [ref for ref, owner in candidates if owner == company_id]
        if len(mine) == 1:
            return mine[0]
    return candidates[0][0] if len(candidates) == 1 else None


def _items(db: Session, flat: dict[str, Any], company_id: str | None) -> list[dict[str, str]]:
    """The product and brand items of one ask: named by the parser, shown by the reply."""
    named = flat["named_codes"]
    shown = flat["shown_codes"]
    products = _lookup(
        db, _PRODUCTS, _PRODUCTS.c.product_code, [*named.get("product", []), *shown]
    )
    brands = _lookup(db, _BRANDS, _BRANDS.c.brand_code, named.get("brand", []))

    out: dict[tuple[str, str, str], str] = {}

    def add(kind: str, ref: str | None, role: str, resolved_by: str) -> None:
        if ref is not None:
            out.setdefault((kind, ref, role), resolved_by)

    for code in named.get("product", []):
        add("product", _pick(products.get(code.lower(), []), company_id), "named", "parser")
    for code in named.get("brand", []):
        add("brand", _pick(brands.get(code.lower(), []), company_id), "named", "parser")
    for code in shown:
        add("product", _pick(products.get(code.lower(), []), company_id), "shown", "reply")
    return [
        {"kind": kind, "ref_id": ref, "role": role, "resolved_by": by}
        for (kind, ref, role), by in out.items()
    ]


# ------------------------------------------------------------------------ one turn


def _is_media(envelope: Any) -> bool:
    """An attachment rides on the inner respond message. The stored envelope is
    `Envelope.model_dump()`, so that message is `message.message.message` (the engine's
    `_inner_message`). Its words are not read."""
    node: Any = envelope
    for key in ("message", "message", "message"):
        node = node.get(key) if isinstance(node, dict) else None
    return bool(isinstance(node, dict) and node.get("attachment"))


def _reply_ms(row: ChatbotTurn) -> int | None:
    if row.started_at is None or row.finished_at is None:
        return None
    ms = max(0, int((row.finished_at - row.started_at).total_seconds() * 1000))
    # A turn that sat open for weeks (a stalled handover closed late) has no reply time worth
    # charting, and would overflow the integer column and cost the whole fact row.
    return ms if ms <= _MAX_REPLY_MS else None


def _superseded(db: Session, row: ChatbotTurn) -> bool:
    """A later attempt of the same respond message is already recorded: that one is the ask.

    One fact per respond `message_id`, the latest attempt (a manual Retry is a new turn,
    attempt 2, of the same message). Recording an older attempt after the newer one must
    not bring the superseded row back. Judged on the RECORDED later attempt, not on a later
    turn merely existing: an in-flight or unrecorded retry has not replaced anything yet.
    """
    if row.message_id is None:
        return False
    later = select(_FACTS.c.id).join(_TURNS, _TURNS.c.id == _FACTS.c.turn_id).where(
        _TURNS.c.contact_respond_id == row.contact_respond_id,
        _TURNS.c.message_id == row.message_id,
        _TURNS.c.is_test.is_(False),
        _TURNS.c.attempt > row.attempt,
    )
    return db.execute(later.limit(1)).first() is not None


def _drop_older_attempts(db: Session, row: ChatbotTurn) -> None:
    """The fact rows of this message's earlier attempts go (their items cascade)."""
    if row.message_id is None:
        return
    older = select(_TURNS.c.id).where(
        _TURNS.c.contact_respond_id == row.contact_respond_id,
        _TURNS.c.message_id == row.message_id,
        _TURNS.c.is_test.is_(False),
        _TURNS.c.attempt < row.attempt,
    )
    db.execute(delete(_FACTS).where(_FACTS.c.turn_id.in_(older)))


def _write(db: Session, row: ChatbotTurn) -> int:
    """Insert the fact (and items) for one eligible turn. Returns 1 when a row landed,
    0 when it was superseded or already there. Does not commit."""
    if _superseded(db, row):
        return 0
    flat = outcome_mod.flatten(_turn_dict(row))
    who = _who(db, row.contact_respond_id)
    _drop_older_attempts(db, row)

    fact_id = db.execute(
        pg_insert(_FACTS)
        .values(
            id=str(uuid.uuid4()),
            source="trace",
            turn_id=row.id,
            ask_seq=1,
            contact_respond_id=row.contact_respond_id,
            contact_id=who["contact_id"],
            customer_id=who["customer_id"],
            company_id=who["company_id"],
            audience=who["audience"],
            contact_type=who["contact_type"],
            asked_at=row.created_at,
            channel="whatsapp",
            domain=flat["domain"],
            intent=flat["intent"],
            outcome=flat["outcome"],
            failure_reason=flat["failure_reason"],
            branch_kind=flat["branch_kind"],
            tools=flat["tools"],
            rows_offered=flat["rows_offered"],
            reply_ms=_reply_ms(row),
            is_media=_is_media(row.envelope),
            phantom_dropped=flat["phantom_dropped"],
        )
        .on_conflict_do_nothing(index_elements=[_FACTS.c.turn_id, _FACTS.c.ask_seq])
        .returning(_FACTS.c.id)
    ).scalar()
    if fact_id is None:
        return 0

    items = _items(db, flat, who["company_id"])
    if items:
        db.execute(
            pg_insert(_ITEMS).on_conflict_do_nothing(),
            [{"id": str(uuid.uuid4()), "ask_id": fact_id, **item} for item in items],
        )
    return 1


def record_turn(db: Session, turn_id: str) -> bool:
    """Write the fact row (and items) for one closed live turn. Commits its own work.

    False for a turn that is not a closed live one (test, console, still open, unknown) and
    for one that adds nothing (superseded by a later attempt, or already recorded).
    """
    row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    if not _eligible(row):
        return False
    written = _write(db, row)
    db.commit()
    return bool(written)


def sweep(db: Session, *, since: datetime, until: datetime | None = None) -> int:
    """Every closed live turn created in `[since, until)` with no fact row. Returns the
    number of rows written. Idempotent: a second pass over the same range writes nothing.

    One bad turn is logged and skipped inside its own SAVEPOINT, so it cannot sink the
    rest of the load; the next sweep tries it again.
    """
    has_fact = select(_FACTS.c.id).where(_FACTS.c.turn_id == _TURNS.c.id).exists()
    query = (
        select(_TURNS.c.id)
        .where(
            _TURNS.c.created_at >= since,
            _TURNS.c.is_test.is_(False),
            _TURNS.c.ingress != "console",
            _TURNS.c.status.in_(_CLOSED),
            ~has_fact,
        )
        .order_by(_TURNS.c.created_at.asc())
    )
    if until is not None:
        query = query.where(_TURNS.c.created_at < until)
    ids = [str(r[0]) for r in db.execute(query)]

    written = 0
    for start in range(0, len(ids), _SWEEP_CHUNK):
        chunk = ids[start : start + _SWEEP_CHUNK]
        rows = (
            db.query(ChatbotTurn)
            .filter(ChatbotTurn.id.in_(chunk))
            .order_by(ChatbotTurn.created_at.asc())
            .all()
        )
        for row in rows:
            if not _eligible(row):
                continue
            try:
                with db.begin_nested():
                    written += _write(db, row)
            except Exception:  # noqa: BLE001 - one turn must not sink the load
                logger.warning("chat facts sweep: turn %s skipped", row.id, exc_info=True)
        db.commit()
    return written


# ----------------------------------------------------------------------- episodes


def _as_utc(when: datetime) -> datetime:
    """Frame timestamps are naive UTC; the fact columns are timestamptz."""
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def _latest_attempts(rows: list[ChatbotTurn]) -> list[ChatbotTurn]:
    """Only the latest attempt of a respond message counts toward an episode, as it does
    toward the facts: a failed first try that was retried is one turn, not two."""
    newest: dict[str, int] = {}
    for r in rows:
        if r.message_id is not None:
            newest[r.message_id] = max(newest.get(r.message_id, 0), r.attempt)
    return [r for r in rows if r.message_id is None or r.attempt == newest[r.message_id]]


def snapshot_episode(db: Session, frame_id: str, turn_rows: list[ChatbotTurn]) -> None:
    """Write the analytic row of a closed episode and stamp `episode_id` on its asks.

    Called by `memory.write_episode_for_reset` right after the frame insert and before the
    retention trim, so the row exists before the frame can ever be trimmed. It does NOT
    commit: it joins the caller's transaction, and the caller wraps it in a SAVEPOINT.

    The counts come from the TURNS, not from fact rows: a turn whose recorder failed still
    counts, so an episode's numbers do not depend on the sweep having run. A turn with no
    fact row simply gets no `episode_id` (the sweep writes it later; the next close does
    not revisit it, which is the accepted cost).
    """
    frame = db.execute(
        select(
            _FRAMES.c.contact_respond_id,
            _FRAMES.c.domain,
            _FRAMES.c.intent,
            _FRAMES.c.close_reason,
            _FRAMES.c.opened_at,
            _FRAMES.c.closed_at,
            _FRAMES.c.started_at,
            _FRAMES.c.last_activity_at,
        ).where(_FRAMES.c.id == frame_id)
    ).first()
    if frame is None or not turn_rows:
        return

    ordered = _latest_attempts(sorted(turn_rows, key=lambda r: r.created_at))
    outcomes = [outcome_mod.turn_outcome(_turn_dict(r))[0] for r in ordered]
    opened = frame.opened_at or frame.started_at
    closed = frame.closed_at or frame.last_activity_at

    db.execute(
        pg_insert(_EPISODES)
        .values(
            frame_id=frame_id,
            contact_respond_id=frame.contact_respond_id,
            opened_at=_as_utc(opened),
            closed_at=_as_utc(closed),
            domain=frame.domain,
            intent=frame.intent,
            turn_count=len(ordered),
            ask_count=sum(1 for o in outcomes if o != "noise"),
            failed_turns=sum(1 for o in outcomes if o in outcome_mod.FAILED_OUTCOMES),
            last_outcome=outcomes[-1],
            handover=any(o == "handover" for o in outcomes),
            close_reason=frame.close_reason,
        )
        .on_conflict_do_nothing(index_elements=[_EPISODES.c.frame_id])
    )
    db.execute(
        update(_FACTS)
        .where(_FACTS.c.turn_id.in_([r.id for r in turn_rows]))
        .values(episode_id=frame_id)
    )
