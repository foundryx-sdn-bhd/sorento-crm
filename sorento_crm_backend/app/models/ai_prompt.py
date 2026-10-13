"""AI assistant prompt registry: immutable versions + movable labels.

See ``docs/plans/PLAN-ai-assistant-prompt-registry.md`` §5. Two tables:

- ``ai_prompt_versions`` - immutable, append-only snapshot per ``(name, version)``.
- ``ai_prompt_labels`` - movable pointer, one row per ``(name, label)``.

Publish = repoint a label row's ``version_id``; the version rows never change.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class AIPromptVersion(Base):
    """Immutable, append-only prompt snapshot. Never edited in place."""

    __tablename__ = "ai_prompt_versions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    # Stable key from PROMPT_KEYS (reformulator | router | agent_system | synthesizer | ...).
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Auto-incremented PER name (max(version)+1 on insert), never global.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="text")
    template: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Extracted declared vars, e.g. ["current_date"].
    variables: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    # Reserved: per-version model/params override (unused P1).
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    commit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_ai_prompt_versions_name_version"),
        Index("ix_ai_prompt_versions_name", "name"),
    )


class AIPromptLabel(Base):
    """Movable pointer: one row per ``(name, label)`` -> a version id."""

    __tablename__ = "ai_prompt_labels"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("ai_prompt_versions.id", ondelete="CASCADE"), nullable=False
    )
    # Which LLM this agent runs on, in this environment. NULL = the global
    # `ai_assistant_configs` model, which is what every row meant before these existed.
    # Per-agent because the jobs are not alike: reading a misspelt customer sentence and
    # writing an explanatory paragraph do not want the same model, and one global setting
    # tunes for the hardest of them or for none of them.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # How hard that model thinks (`none` / `minimal` / `low` / `medium` / `high`), sent
    # as OpenAI's `reasoning_effort` or mapped onto a Gemini thinking budget. NULL = the
    # provider's own default, unchanged (PARSER-GEMINI-LITE).
    reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("name", "label", name="uq_ai_prompt_labels_name_label"),
        Index("ix_ai_prompt_labels_name", "name"),
    )
