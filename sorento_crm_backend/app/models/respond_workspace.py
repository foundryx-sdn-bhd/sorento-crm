"""Respond.io workspace configuration (multi-workspace per deployment).

The `commercial_leads` reciprocal relationship is added at runtime by
`app/modules/commercial_core/_base_patches.py` when the commercial_core
module is enabled - keeps this base class commercial-free.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class RespondWorkspace(Base):
    __tablename__ = "respond_workspaces"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    space_id = Column(String(64), nullable=False)
    name = Column(String(255), nullable=True)
    api_key_ciphertext = Column(Text, nullable=False)
    base_url = Column(String(512), nullable=True)
    # Business WhatsApp number for this workspace's channel (E.164 digits).
    # Drives the portal verify page's wa.me click-to-chat escape hatch.
    whatsapp_number = Column(String(32), nullable=True)
    # Ideation pipeline: the shared-service Product this workspace maps to
    # (one sorento deployment -> one shared-service Product of kind=software).
    # Stored as a UUID string; server-side only, NEVER rendered in the FE.
    # NULL keeps ideation dormant/fail-closed for this workspace (no create_idea).
    ideation_product_id = Column(String(64), nullable=True)
    # Ideation shared-service connection (DB-driven, per workspace - mirrors the
    # respond.io api_key encrypted pattern). base URL in plain text; the intake
    # API key stored Fernet-encrypted (decrypt server-side for the create_idea
    # call). Blank => fall back to app.config settings, then fail-closed.
    ideation_shared_service_url = Column(String(512), nullable=True)
    ideation_intake_api_key_ciphertext = Column(Text, nullable=True)
    # Ideas iframe embed SSO (DB-driven, per workspace - mirrors the intake-key
    # pattern above). connection_id + FE base URL in plain text; the signing secret
    # stored Fernet-encrypted (decrypt server-side to mint the SSO assertion). The
    # FE base URL is the shared-service FRONTEND root the iframe points at - distinct
    # from ideation_shared_service_url (the backend base for POST /embed/session).
    # Any blank => fall back to app.config settings, then embed stays dormant.
    ideation_embed_connection_id = Column(String(128), nullable=True)
    ideation_embed_signing_secret_ciphertext = Column(Text, nullable=True)
    ideation_embed_fe_base_url = Column(String(512), nullable=True)
    # Chatbot retry ingress (AC-804). The trace screen's Retry re-posts a failed turn's
    # ORIGINAL respond.io body here, so it re-enters at the same ingress a live message
    # uses. Config lives on the workspace ROW, not the environment: it is per tenant, and
    # `.env` does not scale past one (owner ruling, 5 Sep 2026).
    #
    # URL in plain text, key Fernet-encrypted, the same split the ideation intake pair
    # above uses. Blank URL = Retry is off for this workspace and the endpoint answers
    # 409 rather than guessing; a dev machine that silently injected into production n8n
    # would answer a real customer from a developer's click.
    #
    # The URL is validated by `app/services/outbound_url_guard.py` on save AND on use,
    # because an admin-editable outbound URL is an SSRF primitive otherwise.
    chatbot_retry_ingress_url = Column(String(512), nullable=True)
    chatbot_retry_ingress_key_ciphertext = Column(Text, nullable=True)
    # CHAT-SPEED: where a turn the respond.io webhook started posts its finished actions
    # (the thin n8n sender; egress stays in n8n, D9). Same guard on save and on use as the
    # retry pair above; the key is sent as `X-Chatbot-Sender-Key` and never echoed.
    chatbot_sender_url = Column(String(512), nullable=True)
    chatbot_sender_key_ciphertext = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    is_default = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    respond_contacts = relationship("RespondContact", back_populates="workspace")

    __table_args__ = (
        Index("ix_respond_workspaces_space_id", "space_id"),
        Index("ix_respond_workspaces_is_active", "is_active"),
        Index(
            "uq_respond_workspaces_one_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
        ),
    )
