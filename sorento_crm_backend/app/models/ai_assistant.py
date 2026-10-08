"""AI assistant configuration, conversations, messages, and governance events."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.utils.field_encryption import EncryptedText

try:
    from pgvector.sqlalchemy import Vector  # type: ignore
    _HAS_PGVECTOR = True
except Exception:  # pragma: no cover
    Vector = None  # type: ignore[assignment]
    _HAS_PGVECTOR = False


def _uuid_str() -> str:
    return str(uuid.uuid4())


class AIAssistantConfig(Base):
    __tablename__ = "ai_assistant_configs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, server_default="openai")
    model: Mapped[str] = mapped_column(String(128), nullable=False, server_default="gpt-4o-mini")
    temperature: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # The three *_ciphertext columns hold Fernet tokens (EncryptedText); the attribute
    # reads back the plain key. scripts/encrypt_ai_assistant_keys.py encrypts legacy rows.
    api_key_ciphertext: Mapped[str | None] = mapped_column(
        EncryptedText("ai_assistant_configs.api_key_ciphertext"), nullable=True
    )
    # Dedicated Anthropic key - the SCM M5 market web search needs Anthropic while
    # the assistant/explainer runs on the primary (OpenAI) key. DB-configurable.
    anthropic_api_key_ciphertext: Mapped[str | None] = mapped_column(
        EncryptedText("ai_assistant_configs.anthropic_api_key_ciphertext"), nullable=True
    )
    # Dedicated Gemini key - the chatbot media image lane can run on Gemini while
    # the assistant stays on the primary provider. DB-configurable.
    gemini_api_key_ciphertext: Mapped[str | None] = mapped_column(
        EncryptedText("ai_assistant_configs.gemini_api_key_ciphertext"), nullable=True
    )
    enabled_tools: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    rag_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AIAssistantConversation(Base):
    __tablename__ = "ai_assistant_conversations"
    __audit_skip__ = "assistant conversation log"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["AIAssistantMessage"]] = relationship(
        "AIAssistantMessage", back_populates="conversation", cascade="all,delete-orphan"
    )

    __table_args__ = (
        Index("ix_ai_assistant_conversations_user_id", "user_id"),
        Index("ix_ai_assistant_conversations_updated_at", "updated_at"),
    )


class AIAssistantMessage(Base):
    __tablename__ = "ai_assistant_messages"
    __audit_skip__ = "assistant conversation log"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # M2 trace bridge: assistant message -> its per-turn trace (SET NULL so a
    # swept/expired trace never blocks message reads). Nullable: user messages
    # and legacy rows have none.
    trace_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_traces.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    conversation: Mapped[AIAssistantConversation] = relationship("AIAssistantConversation", back_populates="messages")

    __table_args__ = (
        Index("ix_ai_assistant_messages_conversation_id", "conversation_id"),
        Index("ix_ai_assistant_messages_role", "role"),
    )


class AIAssistantGovernanceEvent(Base):
    __tablename__ = "ai_assistant_governance_events"
    __audit_skip__ = "assistant governance log, itself a trail"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_conversations.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    module_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permission_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_ai_assistant_governance_events_user_id", "user_id"),
        Index("ix_ai_assistant_governance_events_event_type", "event_type"),
    )


class AIAssistantUsageLog(Base):
    """Per-assistant-turn usage telemetry (tokens, latency, answered flag).

    One row is written per assistant message produced by the agent loop.
    """

    __tablename__ = "ai_assistant_usage_logs"
    __audit_skip__ = "assistant telemetry"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_messages.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tool_calls_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    was_answered: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Portal contacts (no users.id) - internal respond_contacts.id (Text PK), NOT respond_io_id.
    contact_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True
    )
    # Discriminator: "ai_assistant" (chat) | "ai_extract" (portal form pre-fill).
    feature: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # When feature="ai_extract", the portal form_key, e.g. "portal.stock_inquiry".
    form_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Chatbot memory lane A (contract section 7): `chatbot.turns.id` this row bills,
    # for feature="chatbot_parser" rows. Not a FK - `chatbot.turns` is a log the module
    # owns, and this table must survive a turn row's own retention independently.
    chatbot_turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_ai_assistant_usage_logs_user_id", "user_id"),
        Index("ix_ai_assistant_usage_logs_created_at", "created_at"),
        Index("ix_ai_assistant_usage_logs_message_id", "message_id"),
        Index("ix_ai_assistant_usage_logs_contact_id", "contact_id"),
        Index("ix_ai_assistant_usage_logs_feature_created_at", "feature", "created_at"),
    )


class AIAssistantWishlistCluster(Base):
    """Cluster of unanswered user questions, populated by the nightly job."""

    __tablename__ = "ai_assistant_wishlist_clusters"
    __audit_skip__ = "derived from assistant telemetry"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    representative_question: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    # Optional pgvector representative embedding (nullable if pgvector missing).
    if _HAS_PGVECTOR:
        representative_embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)  # type: ignore[misc]
    else:  # pragma: no cover
        representative_embedding: Mapped[Any | None] = mapped_column(JSONB, nullable=True)  # type: ignore[misc]

    __table_args__ = (
        Index("ix_ai_assistant_wishlist_clusters_count", "count"),
        Index("ix_ai_assistant_wishlist_clusters_last_seen_at", "last_seen_at"),
    )


class AIAssistantUnansweredQuery(Base):
    """Per-message link from an unanswered turn to its assigned cluster."""

    __tablename__ = "ai_assistant_unanswered_queries"
    __audit_skip__ = "assistant telemetry"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_messages.id", ondelete="CASCADE"), nullable=False
    )
    cluster_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_wishlist_clusters.id", ondelete="SET NULL"), nullable=True
    )
    if _HAS_PGVECTOR:
        embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)  # type: ignore[misc]
    else:  # pragma: no cover
        embedding: Mapped[Any | None] = mapped_column(JSONB, nullable=True)  # type: ignore[misc]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("message_id", name="uq_ai_assistant_unanswered_queries_message_id"),
        Index("ix_ai_assistant_unanswered_queries_cluster_id", "cluster_id"),
        Index("ix_ai_assistant_unanswered_queries_created_at", "created_at"),
    )


class AIAssistantTrace(Base):
    """M2 - one root trace per assistant turn (OTel GenAI-shaped).

    Root of the span tree. Field names mirror `gen_ai.*` semconv so a future
    OTLP export is a straight field-map (PLAN Q1). Retention swept by the
    background scheduler (PLAN Q2): `ok` traces past `ai_trace_ttl_days`,
    `error`/`flagged` past `ai_trace_error_ttl_days`.
    """

    __tablename__ = "ai_assistant_traces"
    __audit_skip__ = "assistant telemetry"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    message_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_messages.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_conversations.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    total_tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_cost: Mapped[float | None] = mapped_column(Text, nullable=True)  # optional $ stretch (Q8) - kept as text, unused in M2
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ok")  # ok | error
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")  # thumbs-down / kept-for-review (Q2)
    env: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    span_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_ai_assistant_traces_message_id", "message_id"),
        Index("ix_ai_assistant_traces_conversation_id", "conversation_id"),
        Index("ix_ai_assistant_traces_status_created_at", "status", "created_at"),
        Index("ix_ai_assistant_traces_created_at", "created_at"),
    )


class AIAssistantSpan(Base):
    """M2 - one span per pipeline node under a trace (tree via parent_id)."""

    __tablename__ = "ai_assistant_spans"
    __audit_skip__ = "assistant telemetry"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    trace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_assistant_traces.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    dotted_order: Mapped[str] = mapped_column(Text, nullable=False, server_default="")  # sortable sibling path key
    span_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # LLM|TOOL|RETRIEVER|EMBEDDING|CHAIN|AGENT|GUARDRAIL|EVALUATOR
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ok")  # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # LLM spans
    request_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invocation_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    prompt_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # M1 bridge
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null = fallback used
    # TOOL spans
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # RETRIEVER spans
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    documents: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)  # [{id,content,score}]
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_ai_assistant_spans_trace_id", "trace_id"),
        Index("ix_ai_assistant_spans_trace_dotted", "trace_id", "dotted_order"),
    )
