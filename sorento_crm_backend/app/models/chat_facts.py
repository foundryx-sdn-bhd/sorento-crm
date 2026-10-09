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

from sqlalchemy import (
    REAL,
    Boolean,
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
        Index("ix_chat_ask_facts_asked_at", "asked_at"),
        Index("ix_chat_ask_facts_contact", "contact_respond_id"),
        Index("ix_chat_ask_facts_outcome", "outcome"),
        {"schema": "chatbot"},
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    # `trace` for everything CA-1 writes; CA-4 adds a source for pre-trace history.
    source = Column(Text, nullable=False)
    # The `chatbot.turns` row. Not an FK: a fact is a log line and survives its turn.
    turn_id = Column(UUID(as_uuid=False), nullable=False)
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
    # parser | reply
    resolved_by = Column(Text, nullable=False)


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
