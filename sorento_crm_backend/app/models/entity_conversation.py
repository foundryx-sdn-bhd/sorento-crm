"""Polymorphic activity / internal-note threads keyed by (entity_type, entity_id).

General-purpose substrate. Commercial features write to it but the model is
intentionally domain-agnostic so other modules can reuse it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class EntityConversationMessage(Base):
    __tablename__ = "entity_conversation_messages"
    __audit_skip__ = "message log, itself a trail"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(String(36), nullable=False)
    scope = Column(String(32), nullable=False)
    is_system = Column(Boolean, nullable=False, server_default="false")
    body_html = Column(Text, nullable=False, server_default="")
    author_user_id = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # A submitter's comment on a form (scope 'form_comment') has no CRM user.
    author_contact_id = Column(Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    author = relationship("User", foreign_keys=[author_user_id])

    __table_args__ = (
        Index(
            "ix_entity_conversation_messages_lookup",
            "entity_type",
            "entity_id",
            "scope",
            "created_at",
        ),
        Index("ix_entity_conversation_messages_author", "author_user_id"),
        # Mirrors migrations 150 + fcom_0002, so a schema built by create_all (tests, CI
        # bootstrap) refuses what the migrated database refuses. Widen both together.
        CheckConstraint(
            "entity_type IN ('commercial_project','commercial_master_quotation',"
            "'commercial_project_task','commercial_master_quotation_task','commercial_lead',"
            "'complaint','stock_inquiry','purchase_request','sponsorship_form')",
            name="ck_entity_conversation_messages_entity_type",
        ),
        CheckConstraint(
            "scope IN ('activity','internal_note','form_comment')",
            name="ck_entity_conversation_messages_scope",
        ),
    )
