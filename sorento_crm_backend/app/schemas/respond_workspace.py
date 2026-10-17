"""Respond.io workspace admin schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RespondWorkspaceBase(BaseModel):
    space_id: str = Field(..., max_length=64)
    name: Optional[str] = Field(None, max_length=255)
    base_url: Optional[str] = Field(None, max_length=512)
    whatsapp_number: Optional[str] = Field(
        None,
        max_length=32,
        description="Business WhatsApp number (digits, E.164 without '+') for the portal wa.me escape hatch.",
    )
    is_active: bool = True
    is_default: bool = False
    ideation_shared_service_url: Optional[str] = Field(
        None,
        max_length=512,
        description="Ideation shared-service base URL (create_idea intake endpoint host).",
    )
    ideation_product_id: Optional[str] = Field(
        None,
        max_length=64,
        description="Shared-service Product id this workspace's ideas map to (admin config).",
    )
    ideation_embed_connection_id: Optional[str] = Field(
        None,
        max_length=128,
        description="Ideas iframe embed connection id (matches the shared-service registry).",
    )
    ideation_embed_fe_base_url: Optional[str] = Field(
        None,
        max_length=512,
        description="Ideas iframe FE root URL (shared-service frontend; the iframe points here, NOT the backend base).",
    )
    chatbot_retry_ingress_url: Optional[str] = Field(
        None,
        max_length=512,
        description=(
            "Webhook a failed chatbot turn is re-posted to from the trace screen. https "
            "only, and must not resolve to this machine or a private range. Blank turns "
            "Retry off for this workspace."
        ),
    )


class RespondWorkspaceCreate(RespondWorkspaceBase):
    api_key: str = Field(..., min_length=1, description="Plain API key pasted from Respond.io")
    ideation_intake_api_key: Optional[str] = Field(
        None, description="Plain ideation intake API key (Bearer token for create_idea)."
    )
    ideation_embed_signing_secret: Optional[str] = Field(
        None, description="Plain ideation embed signing secret (mints the SSO assertion)."
    )
    chatbot_retry_ingress_key: Optional[str] = Field(
        None,
        description=(
            "Plain chatbot retry key, sent as X-Chatbot-Retry-Key. Write-only: a GET "
            "reports only whether one is stored."
        ),
    )


class RespondWorkspaceUpdate(BaseModel):
    space_id: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = Field(None, max_length=255)
    base_url: Optional[str] = Field(None, max_length=512)
    whatsapp_number: Optional[str] = Field(None, max_length=32)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    api_key: Optional[str] = Field(None, description="When set, replaces stored key")
    ideation_shared_service_url: Optional[str] = Field(None, max_length=512)
    ideation_product_id: Optional[str] = Field(None, max_length=64)
    ideation_intake_api_key: Optional[str] = Field(
        None, description="When set, replaces stored ideation intake key"
    )
    ideation_embed_connection_id: Optional[str] = Field(None, max_length=128)
    ideation_embed_fe_base_url: Optional[str] = Field(None, max_length=512)
    ideation_embed_signing_secret: Optional[str] = Field(
        None, description="When set, replaces stored ideation embed signing secret"
    )
    chatbot_retry_ingress_url: Optional[str] = Field(None, max_length=512)
    chatbot_retry_ingress_key: Optional[str] = Field(
        None, description="When set, replaces the stored chatbot retry key"
    )


class RespondWorkspaceChatbotRetryUpdate(BaseModel):
    """The two chatbot retry fields and NOTHING else (S8a review B2).

    Its own model because it is its own route with a weaker slug. Putting these two on
    `RespondWorkspaceUpdate` and widening the row PUT to `user_management.settings.edit`
    handed that slug `api_key`, `base_url`, `space_id` and `is_default` as well - which is
    the respond.io credential for the whole install, where every outbound call for it goes,
    and which workspace is default. The narrow model is what makes the narrow slug narrow.

    **An omitted field means leave it alone; an explicit null or blank means CLEAR.** The
    two are told apart by `model_fields_set`, not by the value, because "blank means no
    change" is what made the screen's own promise ("Leave blank to turn Retry off") false:
    an operator who suspected the webhook was compromised had no way to disable it.
    """

    chatbot_retry_ingress_url: Optional[str] = Field(
        None,
        max_length=512,
        description=(
            "Webhook a failed chatbot turn is re-posted to. https only, and must not "
            "resolve to this machine or a private range. Explicit null or blank CLEARS "
            "it, which turns Retry off for this workspace."
        ),
    )
    chatbot_retry_ingress_key: Optional[str] = Field(
        None,
        description=(
            "Plain chatbot retry key, sent as X-Chatbot-Retry-Key. Explicit null or blank "
            "CLEARS the stored key; omit the field to leave it untouched."
        ),
    )
    # CHAT-SPEED: the thin n8n sender a webhook-started turn posts its actions to. Same
    # presence rule as the retry pair: omitted leaves it, explicit null or blank CLEARS it.
    chatbot_sender_url: Optional[str] = Field(
        None,
        max_length=512,
        description=(
            "Webhook a turn the respond.io webhook started posts its finished actions to. "
            "https only, and must not resolve to this machine or a private range."
        ),
    )
    chatbot_sender_key: Optional[str] = Field(
        None,
        description=(
            "Plain chatbot sender key, sent as X-Chatbot-Sender-Key. Write-only; explicit "
            "null or blank CLEARS it."
        ),
    )


class RespondWorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    space_id: str
    name: Optional[str] = None
    base_url: Optional[str] = None
    whatsapp_number: Optional[str] = None
    is_active: bool
    is_default: bool = False
    api_key_masked: Optional[str] = None
    ideation_shared_service_url: Optional[str] = None
    ideation_product_id: Optional[str] = None
    ideation_intake_api_key_masked: Optional[str] = None
    ideation_embed_connection_id: Optional[str] = None
    ideation_embed_fe_base_url: Optional[str] = None
    ideation_embed_signing_secret_masked: Optional[str] = None
    chatbot_retry_ingress_url: Optional[str] = None
    # A BOOL, not a masked hint. The sibling ideation secrets echo `****abcd`; this one
    # echoes nothing at all, because it authorises injecting a message into a real
    # customer's WhatsApp conversation and a last-4 is four characters an attacker no
    # longer has to guess (AC-804).
    has_chatbot_retry_key: bool = False
    chatbot_sender_url: Optional[str] = None
    has_chatbot_sender_key: bool = False
    created_at: datetime
    updated_at: datetime


class RespondWorkspaceSelectItem(BaseModel):
    """Minimal row for dropdowns (lead form)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    space_id: str
    name: Optional[str] = None
    is_default: bool = False
