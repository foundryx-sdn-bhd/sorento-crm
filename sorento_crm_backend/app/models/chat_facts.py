"""Chat analytics CA-1: the fact tables behind the Chat overview report.

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md. Three tables in the
chatbot module's own Postgres schema, next to `chatbot.turns`, so an uninstall drops the
module's data and nothing else.

**There is no column that can hold what a customer wrote.** No message, no summary, no
reply text, no contact name or phone: a fact is an outcome, a domain, a tool list and the
CRM ids of the products and brands involved. The analytics screens therefore cannot leak
customer words, because the words were never copied here (CA-1 privacy contract; the schema
is asserted in `tests/chatbot/test_chat_facts_episode.py`).

**`company_id` here is a SNAPSHOT of the contact's primary customer link, not row ownership.**
The model is deliberately not a `CompanyScopedMixin`: one bot serves every company, an
unlinked contact has no company at all, and the writer runs from scripts and the scheduler
with no company scope. The report dataset declares scope `none` and justifies it there.

`episode_id` and `ChatEpisodeFact.frame_id` carry no foreign key: retention trims
`conversation_frames` to the newest 20 per contact, and the analytic row must outlive its
frame. The facts are derived data; the traces remain the source of truth, so the sweep can
rebuild any row.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.sql import func

from app.database import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class ChatAskFact(Base):
    """One customer ask, flattened from the closed turn that answered it."""

    __tablename__ = "chat_ask_facts"
    __audit_skip__ = "derived analytics fact, rebuilt from chatbot.turns by the sweep"
    __table_args__ = (
        UniqueConstraint("turn_id", "ask_seq", name="uq_chat_ask_facts_turn_seq"),
        UniqueConstraint("history_message_id", "ask_seq", name="uq_chat_ask_facts_history_seq"),
        CheckConstraint(
            "(turn_id IS NULL) <> (history_message_id IS NULL)", name="ck_chat_ask_facts_one_source"
        ),
        Index("ix_chat_ask_facts_asked_at", "asked_at"),
        Index("ix_chat_ask_facts_contact", "contact_respond_id"),
        Index("ix_chat_ask_facts_outcome", "outcome"),
        {"schema": "chatbot"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    # `trace` (CA-1, from a turn) or `history_backfill` (CA-4, from a `chat_histories` row).
    source = Column(Text, nullable=False)
    # The `chatbot.turns` row. Not an FK: a fact is a log line and survives its turn.
    turn_id = Column(UUID(as_uuid=False), nullable=True)
    # The incoming `chat_histories.id` of a backfilled message (CA-4). Exactly one of this and
    # `turn_id` is set. Not an FK, for the same reason.
    history_message_id = Column(BigInteger, nullable=True)
    ask_seq = Column(SmallInteger, nullable=False, server_default=text("1"), default=1)

    contact_respond_id = Column(Text, nullable=False)
    contact_id = Column(Text, nullable=True)
    customer_id = Column(UUID(as_uuid=False), nullable=True)
    company_id = Column(Text, nullable=True)
    # dealer | staff | unknown
    audience = Column(Text, nullable=False)
    # dealer | office | not_allowed | unknown: who the contact is to the business, snapshotted
    # when the ask is recorded (first match: a linked user or an unlinked contact with AI on is
    # office; any customer link is dealer; AI switched off is not_allowed).
    contact_type = Column(Text, nullable=False)

    asked_at = Column(DateTime(timezone=True), nullable=False)
    channel = Column(Text, nullable=False, server_default=text("'whatsapp'"), default="whatsapp")

    domain = Column(Text, nullable=True)
    intent = Column(Text, nullable=True)
    outcome = Column(Text, nullable=False)
    failure_reason = Column(Text, nullable=True)
    branch_kind = Column(Text, nullable=True)
    tools = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"), default=list)
    rows_offered = Column(Integer, nullable=True)
    reply_ms = Column(Integer, nullable=True)
    is_media = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    phantom_dropped = Column(Boolean, nullable=False, server_default=text("false"), default=False)

    episode_id = Column(UUID(as_uuid=False), nullable=True)

    # CA-2 owns these; a trace row leaves them empty.
    topic_code = Column(Text, nullable=True)
    taxonomy_version = Column(SmallInteger, nullable=True)
    label_model = Column(Text, nullable=True)
    label_prompt_version = Column(Text, nullable=True)
    labelled_at = Column(DateTime(timezone=True), nullable=True)
    label_confidence = Column(REAL, nullable=True)
    # en | ms | zh | mixed | other, named by the CA-2 labeller in the same call as the topic.
    language = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatAskItem(Base):
    """A product or brand an ask NAMED (the customer's own words resolved to a CRM code) or
    was SHOWN (a row of the reply's result set). Unique per ask, kind, ref and role."""

    __tablename__ = "chat_ask_items"
    __audit_skip__ = "derived analytics fact, rebuilt from chatbot.turns by the sweep"
    __table_args__ = (
        UniqueConstraint("ask_id", "kind", "ref_id", "role", name="uq_chat_ask_items_ask_kind_ref_role"),
        Index("ix_chat_ask_items_ref_id", "ref_id"),
        {"schema": "chatbot"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    ask_id = Column(
        UUID(as_uuid=False),
        ForeignKey("chatbot.chat_ask_facts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # product | brand
    kind = Column(Text, nullable=False)
    # The CRM id (`products.id` / `brands.id`) as text.
    ref_id = Column(Text, nullable=False)
    # named | shown
    role = Column(Text, nullable=False)
    # parser | reply | lookup | resolver_pass
    resolved_by = Column(Text, nullable=False)
    # The CA-2 labeller's own score for a `resolver_pass` item; null for the others.
    confidence = Column(REAL, nullable=True)


class ChatEpisodeFact(Base):
    """One closed episode, snapshotted at the moment its frame was written."""

    __tablename__ = "chat_episode_facts"
    __audit_skip__ = "derived analytics fact, written with the conversation frame it describes"
    __table_args__ = ({"schema": "chatbot"},)

    # `conversation_frames.id`. No FK: the frame is trimmed, this row stays.
    frame_id = Column(UUID(as_uuid=False), primary_key=True)
    contact_respond_id = Column(Text, nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=False)
    domain = Column(Text, nullable=True)
    intent = Column(Text, nullable=True)
    turn_count = Column(Integer, nullable=False)
    # Non-noise turns.
    ask_count = Column(Integer, nullable=False)
    failed_turns = Column(Integer, nullable=False)
    last_outcome = Column(Text, nullable=True)
    handover = Column(Boolean, nullable=False)
    close_reason = Column(Text, nullable=True)
    # Null until CA-2 judges them.
    resolved = Column(Boolean, nullable=True)
    asked_again = Column(Boolean, nullable=True)


#: The pre-trace eras CA-4 backfills: `(first day, day after the last)`, Malaysia dates. Era D
#: is only the 5 to 22 Sep messages the engine never traced (crew ruling Q5); from 5 Sep every
#: traced message has its facts from the trace. Shared by the extractor and the report notes.
HISTORY_ERAS: dict[str, tuple[date, date]] = {
    "A": (date(2026, 4, 17), date(2026, 7, 22)),
    "B": (date(2026, 7, 22), date(2026, 8, 19)),
    "C": (date(2026, 8, 19), date(2026, 9, 5)),
    "D": (date(2026, 9, 5), date(2026, 9, 23)),
}


class ChatBackfillRun(Base):
    """One run of the history backfill over one era (chat analytics CA-4).

    Counts, tokens and a progress cursor only: no column holds what a customer or the bot
    wrote. `messages_eligible` is the era's whole eligible count when the run started, which
    is what the report's coverage line divides by.
    """

    __tablename__ = "chat_backfill_runs"
    __audit_skip__ = "the run table is itself the record of the job"
    __table_args__ = ({"schema": "chatbot"},)

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    era = Column(Text, nullable=False)
    # running | done | failed
    status = Column(Text, nullable=False)
    messages_eligible = Column(Integer, nullable=False, server_default=text("0"), default=0)
    messages_read = Column(Integer, nullable=False, server_default=text("0"), default=0)
    messages_skipped = Column(Integer, nullable=False, server_default=text("0"), default=0)
    asks_written = Column(Integer, nullable=False, server_default=text("0"), default=0)
    calls = Column(Integer, nullable=False, server_default=text("0"), default=0)
    failed_calls = Column(Integer, nullable=False, server_default=text("0"), default=0)
    tokens_in = Column(Integer, nullable=False, server_default=text("0"), default=0)
    tokens_out = Column(Integer, nullable=False, server_default=text("0"), default=0)
    tokens_cached = Column(Integer, nullable=False, server_default=text("0"), default=0)
    model = Column(Text, nullable=True)
    prompt_version = Column(Text, nullable=True)
    taxonomy_version = Column(SmallInteger, nullable=True)
    row_limit = Column(Integer, nullable=True)
    # The highest `chat_histories.id` of the last committed batch.
    last_history_id = Column(BigInteger, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # The exception class of a run that stopped, never its message (it may quote a prompt).
    error = Column(Text, nullable=True)


class ChatTopic(Base):
    """One node of the topic taxonomy (chat analytics CA-2).

    A subject has no parent and is one of the parser's domains, or `general`; a leaf nests
    under a subject (`inventory.availability`). Seeded and changed only by migration: a
    taxonomy bump is a new `version`, so a trend only ever compares labels of one version.
    """

    __tablename__ = "chat_topics"
    __audit_skip__ = "reference rows written only by migration"
    __table_args__ = ({"schema": "chatbot"},)

    code = Column(Text, primary_key=True)
    parent_code = Column(Text, ForeignKey("chatbot.chat_topics.code"), nullable=True)
    label = Column(Text, nullable=False)
    definition = Column(Text, nullable=False)
    # The taxonomy version that introduced the row.
    version = Column(SmallInteger, nullable=False)
    # active | retired
    status = Column(Text, nullable=False, server_default=text("'active'"), default="active")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatLabelAudit(Base):
    """One ask drawn into the topic audit sample for a taxonomy version, and the verdict a
    person gave it (chat analytics CA-2).

    There is no column for the customer's words: the audit screen reads the message from the
    turn envelope at request time and never stores it.
    """

    __tablename__ = "chat_label_audits"
    __audit_skip__ = "the audit table is itself the review record"
    __table_args__ = (
        UniqueConstraint("ask_id", "taxonomy_version", name="uq_chat_label_audits_ask_version"),
        {"schema": "chatbot"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    ask_id = Column(
        UUID(as_uuid=False),
        ForeignKey("chatbot.chat_ask_facts.id", ondelete="CASCADE"),
        nullable=False,
    )
    taxonomy_version = Column(SmallInteger, nullable=False)
    drawn_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # right | wrong_topic | wrong_subject
    verdict = Column(Text, nullable=True)
    corrected_topic = Column(Text, ForeignKey("chatbot.chat_topics.code"), nullable=True)
    # right | wrong
    products_verdict = Column(Text, nullable=True)
    # The reviewer's user id as text.
    reviewed_by = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
