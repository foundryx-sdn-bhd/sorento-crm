# Memory: episode write (AC-1546), recall (AC-1547), the profile hint block
# (AC-1548) - PLAN-chatbot-turn-rearch.md "State: three shelves". Episodes reuse the
# existing `conversation_frames` table and embedding queue; recall is a plain
# contact-scoped read (never another contact's frames), the embed call proves the
# seam AC-1547 names without depending on a worker having drained it in the same test.
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.chatbot_turn import ChatbotTurn
from app.models.conversation_frame import ConversationFrame
from app.services.chatbot.turn.episode_digest import (
    SMALL_TALK_SUMMARY,
    digest,
    episode_line,
    readable_summary,
    topic_domain,
)
from app.services.chatbot.turn.state import Profile

logger = logging.getLogger(__name__)

#: Contract section 2 / PLAN 6.0 (round 3, AC-MEM051): the four values a context level
#: is ever stored as. "past" was RENAMED "episodes" in round 3 - the stored value
#: only, the FE label stays "Past conversations" (6.0's own table header). "off" is a
#: legitimate OWN level (a contact explicitly opted out even while the system default
#: is on), so it wins over the system default exactly like the other three.
VALID_MEMORY_LEVELS: tuple[str, ...] = ("off", "conversation", "episodes", "full")


def resolve_level(contact_level: str | None, system_memory: dict[str, Any] | None) -> str:
    """The level a turn actually reads (contract section 2, AC-MEM051 - renamed from
    `effective_level` in round 3, same truth table): the contact's own level when it
    is one of the four valid values (INCLUDING "off" - an explicit opt-out beats the
    system default); otherwise the system default when memory is switched on;
    otherwise "off". A RETIRED value name (e.g. the old "past") is not a recognised
    level and falls through exactly like any other unknown string."""
    if contact_level in VALID_MEMORY_LEVELS:
        return contact_level
    memory = system_memory or {}
    if memory.get("enabled") is True:
        default_level = memory.get("default_level")
        if default_level in VALID_MEMORY_LEVELS:
            return default_level
    return "off"


def resolve_level_source(contact_level: str | None, system_memory: dict[str, Any] | None) -> str:
    """Where `resolve_level`'s answer came from (AC-MEM052/056), for the `context`
    trace event: "own" (the contact's own level decided it, off included), "default"
    (the system default, because the contact has none and the switch is on), or
    "off" (neither - the two-argument function's own final fallback). Kept as a
    SEPARATE function rather than changing `resolve_level`'s return shape: the
    pre-round-3 truth-table test already pins it as a bare string."""
    if contact_level in VALID_MEMORY_LEVELS:
        return "own"
    memory = system_memory or {}
    if memory.get("enabled") is True and memory.get("default_level") in VALID_MEMORY_LEVELS:
        return "default"
    return "off"


#: Contract section 3 / PLAN 5.5 (Q7 ruling): retention is a COUNT, never a date.
#: Trimmed on write, never by a scheduled sweep.
KEEP_EPISODES = 20


def _naive_utc(when: datetime) -> datetime:
    """A naive UTC `datetime`, whatever timezone-awareness it arrived with - every
    frame timestamp is naive UTC, same as every other backend timestamp."""
    if when.tzinfo is not None:
        return when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def contact_is_human_intervened(contact: Any) -> bool:
    """`is-human-intervened` off an envelope's `contact`: a staff member has taken the
    chat over, so its topic reset closes no episode. One reading for the live engine and
    the backfill (reviewer pass at d89110c0, S16)."""
    from app.services.chatbot import jsc

    row = jsc.find(
        jsc.get(contact, "custom_fields"),
        lambda x: jsc.get(x, "name") == "is_human_intervened",
    )
    return jsc.to_boolean(jsc.get(row, "value")) is True


def _turn_message_text(row: ChatbotTurn) -> str:
    """The customer's own text for this turn, read off the stored envelope - never the
    reply, never the parser's derived verdict (AC-MEM022: outcome reads structured
    data, never text; this is display-only, for `last_user_message`)."""
    envelope = row.envelope if isinstance(row.envelope, dict) else {}
    inner = (envelope.get("message") or {}).get("message") or {}
    text_value = (inner.get("message") or {}).get("text")
    return str(text_value) if text_value else ""


def _turn_to_digest_dict(row: ChatbotTurn) -> dict[str, Any]:
    """Project one `chatbot.turns` row into `episode_digest.digest()`'s input shape."""
    trace = row.trace if isinstance(row.trace, list) else []
    return {
        "id": row.id,
        "created_at": row.created_at,
        "branch_kind": row.branch_kind,
        "status": row.status,
        "message": _turn_message_text(row),
        "trace": trace,
        "result_refs": [],
    }


def _trim_to_newest(db: Session, *, contact_respond_id: str, is_test: bool) -> None:
    """Contract section 3 / PLAN 5.5: the newest `KEEP_EPISODES` frames survive, per
    contact AND per world - writing the 21st deletes the oldest in the SAME
    transaction as the insert. Never a scheduled sweep."""
    keep_ids = (
        select(ConversationFrame.id)
        .where(
            ConversationFrame.contact_respond_id == contact_respond_id,
            ConversationFrame.is_test.is_(is_test),
        )
        .order_by(ConversationFrame.last_activity_at.desc())
        .limit(KEEP_EPISODES)
    )
    db.query(ConversationFrame).filter(
        ConversationFrame.contact_respond_id == contact_respond_id,
        ConversationFrame.is_test.is_(is_test),
        ~ConversationFrame.id.in_(keep_ids),
    ).delete(synchronize_session=False)


def write_episode_for_reset(
    db: Session,
    *,
    contact_respond_id: str,
    is_test: bool,
    resetting_turn_id: str,
) -> ConversationFrame | None:
    """Close the episode a topic-reset turn just ended (contract section 3).

    The range is this contact's `chatbot.turns` on the same `is_test` WORLD that are
    not yet part of any of this contact-and-world's frames, excluding the resetting
    turn itself, oldest first - read through the ORM (never raw SQL: `chatbot.turns`
    lives in its own Postgres schema and only ORM constructs go through the test
    fixture's `schema_translate_map`, `turn_runtime.turn_number`'s own docstring).
    Membership rather than a `last_activity_at` cutoff, so a turn that lands with a
    created_at at or before the last frame's boundary (commit-order jitter, a backfill
    replaying history) is still picked up.

    Two guard rails around the membership test, both needed once a caller (the
    backfill script) can call this over turns that ALL already exist rather than one
    at a time as the live engine does:

    * An UPPER bound at the resetting turn's own `created_at`, when it is a real row
      (a synthetic id - `test_two_calls_over_the_same_range_produce_one_frame` - has
      none, and skips the bound). Without it, membership alone cannot tell a turn
      that has not happened yet (in insertion order) from one that is simply not yet
      closed, and a full-history backfill run - every turn already in the table -
      would close ranges out of order.
    * A LOWER bound at the OLDEST currently-surviving frame's `started_at`, when one
      exists. Retention trims by COUNT (`KEEP_EPISODES`), on purpose (contract section
      3 / PLAN 5.5): once a frame ages out, its turns must stay forgotten, not get
      re-derived into a new frame the next time this runs over the same history - or a
      repeated backfill would churn forever instead of being idempotent.

    Empty range writes nothing. The insert is `ON CONFLICT DO NOTHING` against the
    unique `(contact_respond_id, is_test, turn_ids[1])` index, so two callers racing
    over the identical range produce exactly one frame; the loser reads back `None`
    rather than the winner's row (D14: this path has no reason to disagree with the
    winner's summary). No embedding is enqueued (contract section 3: recall is
    deleted in S3; this writer never had a reader to serve).
    """
    upper_bound = (
        db.query(ChatbotTurn.created_at)
        .filter(ChatbotTurn.id == resetting_turn_id)
        .scalar()
    )
    lower_bound = (
        db.query(ConversationFrame.started_at)
        .filter(
            ConversationFrame.contact_respond_id == contact_respond_id,
            ConversationFrame.is_test.is_(is_test),
        )
        .order_by(ConversationFrame.started_at.asc())
        .limit(1)
        .scalar()
    )
    already_closed = (
        select(func.unnest(ConversationFrame.turn_ids))
        .where(
            ConversationFrame.contact_respond_id == contact_respond_id,
            ConversationFrame.is_test.is_(is_test),
        )
        .scalar_subquery()
    )
    query = db.query(ChatbotTurn).filter(
        ChatbotTurn.contact_respond_id == contact_respond_id,
        ChatbotTurn.is_test.is_(is_test),
        ChatbotTurn.id != resetting_turn_id,
        # `turn_ids` is `ARRAY(Text)` (it also carries synthetic ids in tests and the
        # pre-rearch reader) while `chatbot.turns.id` is UUID - cast to compare.
        ~cast(ChatbotTurn.id, String).in_(already_closed),
    )
    if is_test:
        # The only test frames are the console's (D14's named exception), so the range
        # is console turns only, never another dry run of the same contact (Prompts
        # screen, API) - reviewer pass at d89110c0, S15.
        query = query.filter(ChatbotTurn.ingress == "console")
    if upper_bound is not None:
        query = query.filter(ChatbotTurn.created_at <= upper_bound)
    if lower_bound is not None:
        query = query.filter(ChatbotTurn.created_at >= lower_bound)
    rows = query.order_by(ChatbotTurn.created_at.asc()).all()
    if not rows:
        return None

    result = digest([_turn_to_digest_dict(r) for r in rows])
    turn_ids = [r.id for r in rows]
    started_at = _naive_utc(rows[0].created_at)
    last_activity_at = _naive_utc(rows[-1].created_at)
    closed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    table = ConversationFrame.__table__
    stmt = (
        pg_insert(table)
        .values(
            id=str(uuid.uuid4()),
            contact_id=contact_respond_id,
            contact_respond_id=contact_respond_id,
            space_id="0",
            channel="whatsapp",
            domain=result.get("domain"),
            intent=result.get("intent"),
            entities=result.get("entities") or {},
            active_entities=result.get("entities") or {},
            tools_used=result.get("tools_used") or [],
            result_refs=result.get("result_refs") or [],
            turn_ids=turn_ids,
            summary=result.get("summary"),
            last_user_message=result.get("last_user_message"),
            status="closed",
            close_reason=result.get("close_reason") or "topic_switch",
            started_at=started_at,
            opened_at=started_at,
            last_activity_at=last_activity_at,
            closed_at=closed_at,
            is_test=is_test,
        )
        .on_conflict_do_nothing(
            index_elements=[
                table.c.contact_respond_id,
                table.c.is_test,
                text("(turn_ids[1])"),
            ]
        )
        .returning(table.c.id)
    )
    inserted_id = db.execute(stmt).scalar()
    if inserted_id is None:
        # ON CONFLICT DO NOTHING fired: another writer already closed this exact
        # range. Nothing of ours landed, so there is nothing of ours to trim for.
        return None

    if not is_test:
        # Chat analytics (CA-1): snapshot the episode BEFORE the trim can drop its frame.
        # A SAVEPOINT, because a database error aborts the whole Postgres transaction and
        # the frame (and the trim) must survive an analytics fault. Imported here, and
        # called as a module attribute, so the tests can swap it.
        from app.services.chatbot.analytics import facts as chat_facts

        try:
            with db.begin_nested():
                chat_facts.snapshot_episode(db, inserted_id, rows)
        except Exception:  # noqa: BLE001 - analytics must never lose a frame
            logger.warning("chat facts: episode snapshot for frame %s failed", inserted_id, exc_info=True)

    _trim_to_newest(db, contact_respond_id=contact_respond_id, is_test=is_test)
    db.commit()

    return db.query(ConversationFrame).filter(ConversationFrame.id == inserted_id).first()


#: How many of the open conversation's newest turns the switch detector and the
#: staff screen read (their `trace` documents are the heavy column). A run of more
#: small-talk turns than this after the last business turn reads as no open domain,
#: so it switches nothing - the safe side.
OPEN_TOPIC_SCAN = 20


def _open_turns_query(
    db: Session, *, contact_respond_id: str, is_test: bool, exclude_turn_id: str | None
) -> Any:
    """This contact-and-world's turns in no frame yet: the open conversation. The same
    world rules `write_episode_for_reset` applies (console turns only on the test side,
    S15)."""
    already_closed = (
        select(func.unnest(ConversationFrame.turn_ids))
        .where(
            ConversationFrame.contact_respond_id == contact_respond_id,
            ConversationFrame.is_test.is_(is_test),
        )
        .scalar_subquery()
    )
    query = db.query(ChatbotTurn).filter(
        ChatbotTurn.contact_respond_id == contact_respond_id,
        ChatbotTurn.is_test.is_(is_test),
        ~cast(ChatbotTurn.id, String).in_(already_closed),
    )
    if exclude_turn_id is not None:
        query = query.filter(ChatbotTurn.id != exclude_turn_id)
    if is_test:
        query = query.filter(ChatbotTurn.ingress == "console")
    return query


def open_turns_newest(
    db: Session,
    *,
    contact_respond_id: str,
    is_test: bool,
    exclude_turn_id: str | None = None,
    limit: int = OPEN_TOPIC_SCAN,
) -> list[ChatbotTurn]:
    """The open conversation's newest `limit` turns, newest first."""
    return (
        _open_turns_query(
            db, contact_respond_id=contact_respond_id, is_test=is_test, exclude_turn_id=exclude_turn_id
        )
        .order_by(ChatbotTurn.created_at.desc())
        .limit(limit)
        .all()
    )


def open_topic_domain(
    db: Session, *, contact_respond_id: str, is_test: bool, exclude_turn_id: str | None = None
) -> str | None:
    """The open conversation's current domain: its newest turn that planned one (fix
    lane round 3, R1 and R2). None when the open turns planned no domain at all."""
    for row in open_turns_newest(
        db, contact_respond_id=contact_respond_id, is_test=is_test, exclude_turn_id=exclude_turn_id
    ):
        domain = topic_domain(_turn_to_digest_dict(row))
        if domain:
            return domain
    return None


#: S4 (plan 7.1, AC-MEM082): a history question is answered from what memory holds,
#: numbered. The open conversation needs level `conversation` or above; closed ones
#: need `episodes` or above (plan 6.0). Up to 5, newest first.
HISTORY_ITEMS = 5
_OPEN_LEVELS = ("conversation", "episodes", "full")
_CLOSED_LEVELS = ("episodes", "full")


def _item_text(summary: str) -> str:
    return summary.strip().rstrip(".").strip()


def frame_line(frame: ConversationFrame) -> str:
    """One closed conversation as the recall reply and the parser's memory layer both
    print it (fix round 6): `Mon 28 Sep, Stock: Asked about stock for SRTWC286 and got
    an answer.` The day is the conversation's last turn; a summary still in the old tag
    shape reads from the frame's own columns until the backfill rewrites it."""
    return episode_line(
        frame.last_activity_at,
        frame.domain,
        readable_summary(frame.summary, frame.domain, frame.entities),
    )


def history_items(
    db: Session,
    *,
    contact_respond_id: str,
    is_test: bool,
    current_turn_id: str | None,
    level: str,
) -> list[str]:
    """What this contact asked, newest first, as deterministic summaries: the open
    conversation (a digest of its turns, the current one excluded) and the newest
    closed conversations of this contact and world. No figure is ever in a summary
    (`episode_digest`), so a list built from these can never quote a stale number."""
    items: list[str] = []
    if level in _OPEN_LEVELS:
        open_rows = list(
            reversed(
                open_turns_newest(
                    db,
                    contact_respond_id=contact_respond_id,
                    is_test=is_test,
                    exclude_turn_id=current_turn_id,
                )
            )
        )
        if open_rows:
            open_digest = digest([_turn_to_digest_dict(r) for r in open_rows])
            # Only when it names something: a digest of pure small talk says nothing.
            if open_digest.get("summary") and any(
                a.get("domain") or a.get("entities") for a in (open_digest.get("asks") or [])
            ):
                items.append(
                    _item_text(episode_line(open_rows[-1].created_at, open_digest.get("domain"), open_digest["summary"]))
                )
    if level in _CLOSED_LEVELS:
        frames = (
            db.query(ConversationFrame)
            .filter(
                ConversationFrame.contact_respond_id == contact_respond_id,
                ConversationFrame.is_test.is_(is_test),
                ConversationFrame.status == "closed",
            )
            .order_by(ConversationFrame.last_activity_at.desc())
            .limit(HISTORY_ITEMS)
            .all()
        )
        # A conversation that asked nothing names nothing to run again.
        items.extend(
            _item_text(frame_line(f))
            for f in frames
            if readable_summary(f.summary, f.domain, f.entities) != SMALL_TALK_SUMMARY
        )
    return items[:HISTORY_ITEMS]


def open_summary(db: Session, *, contact_respond_id: str, is_test: bool, current_turn_id: str | None) -> str | None:
    """The live conversation's summary line (AC-MEM088: what a handover carries), or
    None when its turns named nothing."""
    items = history_items(
        db,
        contact_respond_id=contact_respond_id,
        is_test=is_test,
        current_turn_id=current_turn_id,
        level="conversation",
    )
    return items[0] if items else None


# `recall`, `_by_recency`, `_similarity_of_frame`, `_frame_out` and `episodes_block`
# (the AC-1547 second-parse recall path and its own hint block) are DELETED here
# (chatbot memory lane A, contract section 3, Q5 ruling) - the last-3-summaries
# context layer (`turn/context.py`'s L4) replaces what recall existed to do, in
# the SAME parser call rather than a second one. `/external/memory/frames/search`
# keeps answering (`FrameService.search_frames`, never routed through this
# module).


def profile_block(profile: Profile) -> str:
    """`Profile:` rendered on every parse that has something to say (AC-1548) -
    tier, language, default ledgers. Empty (AC-MEM042, chatbot memory lane A) when
    none of the three are set, so `parser.build_user_block`'s `if profile_block:`
    guard skips the line entirely rather than sending a bare "Profile:" header."""
    lines: list[str] = []
    if profile.tier:
        lines.append(f"tier: {profile.tier}")
    if profile.language:
        lines.append(f"language: {profile.language}")
    if profile.default_ledgers:
        lines.append(f"default_ledgers: {', '.join(profile.default_ledgers)}")
    if not lines:
        return ""
    return "\n".join(["Profile:", *lines])
