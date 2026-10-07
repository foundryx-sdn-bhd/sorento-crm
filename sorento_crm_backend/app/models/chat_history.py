"""High-volume chat history model populated by n8n."""
from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base

# One row per Respond messageId per contact, for traffic that arrives after the
# two-lane mirror went live (UAC AC-J5). Scoped to new rows because legacy rows
# predate the contract and one environment holds a hand-seeded pair sharing an
# id across directions - see alembic/versions/326_chat_history_message_dedupe.py
# for the full reasoning. The literal MUST match the migration and the ingest's
# ON CONFLICT clause, or the arbiter index stops being inferable and the dedupe
# silently stops working.
CHAT_HISTORY_DEDUPE_CUTOVER = "2026-08-14 00:00:00"
CHAT_HISTORY_DEDUPE_PREDICATE = (
    f"message_id IS NOT NULL AND created_at >= TIMESTAMP '{CHAT_HISTORY_DEDUPE_CUTOVER}'"
)


class ChatHistory(Base):
    __tablename__ = "chat_histories"
    __audit_skip__ = "message log, itself a trail"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    channel = Column(String(32), nullable=False)
    contact_id = Column(String(128), nullable=False)
    phone_number = Column(String(32), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=False), nullable=False)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    type = Column(String(32), nullable=False)
    # Respond.io message id + the numbered-options result set the bot sent in
    # that message. Lets conversation-variables resolve a result set by the
    # message the user replied to, not only the latest one.
    message_id = Column(String(64), nullable=True)
    result = Column(JSONB, nullable=True)
    # Set on incoming rows when the user quote-replied to an older message.
    # `reply_to_message_id` matches the `message_id` of that earlier outgoing row,
    # so the chatbot can resolve the pick against its stored `result` set.
    reply_to_message_id = Column(String(64), nullable=True)
    reply_to_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    # --- Round-trip latency telemetry -------------------------------------
    # `sent_at` above is whatever n8n supplied. `respond_ts` is the authoritative
    # Respond-side timestamp, resolved via GET /v2/message/{id}. Both ends of the
    # SLA measurement read `respond_ts` so the two timestamps share one clock - 
    # mixing Respond's clock with ours would bake skew into the p99.
    respond_ts = Column(DateTime(timezone=False), nullable=True)
    # sent | delivered | read | failed | not_sent. Displayed and alerted on
    # separately, but NOT part of the p99: delivery is owned by the recipient's
    # handset, so an offline user must not be able to breach our SLA.
    delivery_status = Column(String(32), nullable=True)
    delivered_ts = Column(DateTime(timezone=False), nullable=True)
    read_ts = Column(DateTime(timezone=False), nullable=True)
    resolve_attempts = Column(Integer, nullable=True, server_default="0")
    # n8n $execution.id, stamped on both saves of one turn. NULL = proactive send,
    # which is excluded from the SLA denominator rather than paired by guesswork.
    turn_id = Column(String(64), nullable=True)
    # Our clock at ingest. Diagnostic only: ingest_at - respond_ts is webhook lag,
    # which is the thing that silently degrades when Respond's webhook misbehaves.
    ingest_at = Column(DateTime(timezone=False), nullable=True)
    # Per-turn conversation state transition (v1), populated by n8n on INCOMING rows
    # only; NULL on outgoing. Opaque jsonb: {v, before, parser_raw, parser_applied,
    # after}. `after: null` means the turn wrote no state (no-access refusal, LLM
    # fallback) - a real signal, distinct from "field absent". Read by the admin thread
    # view. The RAW trace stays internal; the external read contract exposes only a
    # 4-key projection of `after` (domain_hint / intent_hint / entities / dym_offer),
    # via conversation-variables `?message_id=` -> `session_vars.referenced_state`.
    # Widening that projection is a contract change - see
    # conversation_variables_service.get_referenced_state for why the withheld keys
    # are withheld.
    state_trace = Column(JSONB, nullable=True)
    # OUTGOING rows only: the `chatbot.turns.id` whose reply this message carried (QUOTED-REPLY
    # R1, owner ruling 7 Oct 2026). Lets a quote-reply resolve the quoted engine turn by id.
    # NULL = a reply n8n composed alone, a proactive send, or any incoming row.
    chatbot_turn_id = Column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_chat_histories_channel_contact_sent_id", "channel", "contact_id", "sent_at", "id"),
        # The Conversations inbox's "latest message per contact" DISTINCT ON
        # (alembic 330). It does NOT filter on channel - an inbox row is the
        # contact, whatever channel they last used - so the composite above,
        # which leads on `channel`, cannot serve it.
        Index(
            "ix_chat_histories_contact_sent_desc",
            "contact_id",
            sent_at.desc(),
            id.desc(),
        ),
        Index("ix_chat_histories_channel_phone_sent", "channel", "phone_number", "sent_at"),
        Index("ix_chat_histories_channel_type_sent", "channel", "type", "sent_at"),
        Index(
            "ix_chat_histories_contact_message_id",
            "contact_id",
            "message_id",
            postgresql_where=message_id.isnot(None),
        ),
        Index(
            "ix_chat_histories_contact_reply_to",
            "contact_id",
            "reply_to_message_id",
            postgresql_where=reply_to_message_id.isnot(None),
        ),
        # NOTE: a pg_trgm GIN index on `message` also exists in migrated
        # databases (alembic 327_chat_history_trgm) to serve the in-thread
        # `ILIKE '%q%'` search. It is deliberately NOT declared here: it needs
        # the pg_trgm extension, and `Base.metadata.create_all` (the test blank
        # schema) would fail where the extension is absent. Nothing in the ORM
        # references it - the planner picks it up on its own.
        Index(
            "uq_chat_histories_contact_message_dedupe",
            "contact_id",
            "message_id",
            unique=True,
            postgresql_where=text(CHAT_HISTORY_DEDUPE_PREDICATE),
        ),
    )
