"""Respond.io WhatsApp channels, message templates and per-use-case defaults.

Synced from the Respond.io API (see `app/services/respond_template_service.py`):

- ``respond_channels`` - channels of a workspace (``GET /v2/space/channel``).
- ``respond_message_templates`` - WhatsApp templates of a channel
  (``GET /v2/space/channel/{channelId}/template``). Hard-deleted on sync when
  gone from the API; only ``status='approved'`` rows are sendable.
- ``respond_template_defaults`` - one row per auto-send use case mapping a
  template + param mapping used when the contact's 24h window is closed.
  ``template_id`` is ``ON DELETE SET NULL`` so a sync hard-delete leaves the
  row with ``template_name_snapshot`` for the "template was removed" warning.

Plan: docs/plans/PLAN-whatsapp-template-fallback.md
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base

TEMPLATE_DEFAULT_USE_CASES = (
    "complaint",
    "stock_inquiry",
    "purchase_request",
    "sponsorship_form",
    # Portal OTP - sent when a contact verifies on a new device and the 24h
    # window is closed. Map the approved auth/utility template's code param to
    # the ``otp_code`` variable.
    "portal_otp",
    # Sign-in OTP (identity S1, #1280) - an optional override only. Owner
    # ruling (27 Sep 2026): CRM phone sign-in reuses the approved portal_otp
    # template, so settings.phone_signin_otp_use_case ships empty and this use
    # case is not offered on the WhatsApp Templates screen. It is used only if
    # that setting names it AND a template is mapped. Map the code param to
    # ``otp_code``.
    "login_otp",
    # SLA daily summary - bounded template (counts + deep link) sent to a staff
    # member when their 24h window is closed at summary time. Map params to the
    # ``outstanding`` / ``escalated_last_24h`` / ``resolved_last_24h`` counts and
    # ``portal_url`` deep link.
    "sla_daily_summary",
    # SLA staff notifications - assignment + escalation. These already route
    # through send_text_or_template (notification_tasks._send_whatsapp_for_notification);
    # exposing them here lets an admin configure an approved template so the
    # message is uniform whether the staff member's 24h window is open or closed.
    # Map params to ``contact_name`` (the staff name) + ``message`` at minimum;
    # ``entity_number`` / ``status`` when the template carries them.
    "sla_assignment",
    "sla_escalation",
    # Takeover cooldown (PLAN-takeover-cooldown). Distinct from assignment so the
    # out-of-window template wording is accurate per event (not "assigned to you").
    # Each maps params to ``contact_name`` (recipient) + ``message`` at minimum;
    # ``entity_number`` when the template carries it. In-window always sends the real
    # text; out-of-window an unconfigured use-case simply skips WhatsApp (in-app +
    # email still fire) until an admin maps an approved template.
    "sla_takeover_pending",   # -> contested assignee: "someone wants to take over your task"
    "sla_task_moved",         # -> previous assignee: "your task was moved"
    "sla_takeover_cancelled", # -> initiator: takeover rejected / voided
    # SLA deadline extended (PLAN-sla-extend-deadline). Sent to the NEXT escalation
    # tier when the current assignee pushes out the resolution deadline. Map params
    # to ``contact_name`` (recipient) + ``message`` at minimum; ``entity_number`` /
    # ``resolve_due_at`` / ``reason`` when the template carries them.
    "sla_deadline_extended",  # -> next-tier assignee: "the deadline for X was extended"
    # Form handling-lock (PLAN-form-handling-lock). Sent when an escalated form is
    # claimed / taken over / released. Recipients per the notify matrix (affected
    # parties minus the actor). Each maps params to ``contact_name`` (recipient) +
    # ``handler_name`` (who holds/held the lock) + ``message`` at minimum. Out-of-window
    # skips until an admin maps an approved template; in-window sends the real text.
    "sla_handling_claimed",     # -> assignee + other eligible members: "X is now handling this"
    "sla_handling_taken_over",  # -> displaced holder: "X took over handling"
    "sla_handling_released",    # -> eligible pool: "handling released - open again"
    # Form-action undo (PLAN-form-sla-undo). Sent when a committed form action is
    # reversed: the assignee whose spawned task was voided, and the previous holder
    # whose stage reopened (clock restarted). Migration 312c seeds both onto the
    # generic `update` template (sender_name + message + link); remappable here like
    # any other. Map ``message`` at minimum - it carries the full notification body.
    "form_action_voided",     # -> voided assignee: "your task on X no longer applies"
    "form_action_reopened",   # -> reopened holder: "X is back with you, clock restarted"
    # Product discontinued - batch notification to subscribed staff. Sent when their
    # 24h window is closed (the usual case). Map params to ``discontinued_count`` +
    # ``discontinued_link`` (deep link to the product list filtered to that batch).
    "product_discontinued",
    # One closing message per resolved conversation ticket
    # (PLAN-ticket-resolved-closing-message). Sent to the CONTACT. Map params to
    # ``contact_name`` + ``message`` (the enquiry excerpt) at minimum.
    "ticket_resolved",
    # Price tag request status update (PLAN-price-tag-ai-extract-resolver). Sent to
    # the salesperson when their price tag request moves (received, design ready,
    # changes requested, approved, PDF ready, ready for collection, collected,
    # rejected) and their 24h window is closed. Map params to ``message`` at
    # minimum; ``entity_number`` / ``portal_url`` when the template carries them.
    "price_tag_update",
    # Quotation approval (QUOTE-APPROVAL-UX). Sent to the approvers when a below-floor
    # quotation is sent for approval, and again when the creator withdraws it, and their 24h
    # window is closed. Map params to ``contact_name`` (the approver) + ``message`` at
    # minimum; ``entity_number`` (the quotation number) / ``portal_url`` when the template
    # carries them.
    "quotation_approval",
    # Chat reply templates (PLAN-unified-conversation-composer-smart-send). Sent when
    # an admin types a free message in an entity's chat composer while the contact's
    # 24h window is CLOSED - the typed text is wrapped into these per-form templates
    # carrying ``sender_name`` (the replying staff) + ``message`` (the typed text).
    # Distinct from the status-update templates above so the wording reads as a human
    # reply, not a status change. Each MUST map a slot to ``message`` (enforced in
    # set_default). ``conversation_chat`` is the form-less variant for conversation SLA.
    "complaint_chat",
    "stock_inquiry_chat",
    "purchase_request_chat",
    "sponsorship_form_chat",
    "conversation_chat",
    # Supplier container request over WeChat (PLAN-scm-fulfilment-feedback-p4, R10). Sent to
    # the SUPPLIER's chat contact when their 24h window is closed; the body carries the
    # bilingual ask and the public link, so map a slot to ``message`` at minimum. Nothing is
    # seeded: connecting the WeChat channel and approving its template is a Respond.io task
    # with its own go, and until then an out-of-window send is refused with a reason.
    "supplier_request_chat",
    # Ideation idle-draft reminder (PLAN-ideation-intake-redesign-24sep, S4). Sent
    # once, 24h after the last turn on an open draft; due exactly when the 24h
    # free-text window closes, so it will almost always go as a template. Map a
    # slot to ``message`` at minimum - the body carries the fixed reminder text
    # naming the idea's title. Unmapped -> the send is skipped and logged, the
    # draft still closes on schedule (AC-1405).
    "ideation_draft_reminder",
    # Ideation status update (#1355, PLAN-ideation-status-update-29sep). Sent to the
    # requester for every event on the shared service's idea status-event feed
    # (stage change, merged, separated again). ALWAYS this template, never free
    # text. Suggested body "Update on your idea {{1}}: it is now {{2}}. Track it
    # here: {{3}}" maps to ``idea_number`` / ``status_label`` / ``track_url``
    # (``message`` carries the whole sentence). Unmapped -> skipped and logged.
    "ideation_status_update",
    # Chatbot stock ask v2 S4 (PLAN-chatbot-stock-ask-v2-24sep.md, R8). Sent to the
    # customer's SALES AGENT when a dealer's stock ask is answered too big / in stock /
    # no stock no incoming and the dealer contact's "Notify salesman" is on. Map params to
    # ``customer_name`` / ``contact_name`` (the dealer who asked) / ``product`` /
    # ``quantity`` / ``outcome`` / ``asked_at``. Unmapped out-of-window -> the send is
    # skipped and logged failed; in-window it sends the default one-line wording.
    "stock_ask_salesman",
)

# Chat reply use cases - a *_chat / conversation_chat default MUST map a slot to the
# ``message`` variable (the typed text) or the reply loses its whole point.
CHAT_TEMPLATE_USE_CASES = (
    "complaint_chat",
    "stock_inquiry_chat",
    "purchase_request_chat",
    "sponsorship_form_chat",
    "conversation_chat",
    "supplier_request_chat",
)


def _uuid_str() -> str:
    return str(uuid.uuid4())


class RespondChannel(Base):
    __tablename__ = "respond_channels"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    workspace_id = Column(
        UUID(as_uuid=False),
        ForeignKey("respond_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Respond.io numeric channel id (e.g. 453209) - required in template sends.
    respond_channel_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=True)
    # Respond.io channel source, e.g. "whatsapp_business".
    source = Column(String(64), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    synced_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    workspace = relationship("RespondWorkspace")
    templates = relationship(
        "RespondMessageTemplate",
        back_populates="channel",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "respond_channel_id", name="uq_respond_channels_ws_channel"),
        Index("ix_respond_channels_workspace_id", "workspace_id"),
    )


class RespondMessageTemplate(Base):
    __tablename__ = "respond_message_templates"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    channel_id = Column(
        UUID(as_uuid=False),
        ForeignKey("respond_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Respond.io internal template row id (items[].id, e.g. 47212542).
    respond_template_id = Column(Integer, nullable=False)
    # Meta/WhatsApp template id + namespace (items[].templateId / .namespace).
    meta_template_id = Column(String(64), nullable=True)
    namespace = Column(String(64), nullable=True)
    name = Column(String(255), nullable=False)
    language_code = Column(String(16), nullable=False, server_default="en")
    category = Column(String(32), nullable=True)  # UTILITY | MARKETING | AUTHENTICATION
    status = Column(String(32), nullable=False, server_default="pending")  # approved | rejected | ...
    status_detail = Column(String(255), nullable=True)
    # Raw components array from the API (body/header/footer/buttons).
    components = Column(JSONB, nullable=False, server_default="[]")
    # Body component text (contains {{1}}..{{n}}) + derived positional param count.
    body_text = Column(Text, nullable=False, server_default="")
    param_count = Column(Integer, nullable=False, server_default="0")
    synced_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    channel = relationship("RespondChannel", back_populates="templates")

    __table_args__ = (
        UniqueConstraint("channel_id", "respond_template_id", name="uq_respond_templates_channel_tpl"),
        Index("ix_respond_message_templates_channel_id", "channel_id"),
        Index("ix_respond_message_templates_status", "status"),
    )


class RespondTemplateDefault(Base):
    __tablename__ = "respond_template_defaults"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    # One row per auto-send use case (see TEMPLATE_DEFAULT_USE_CASES).
    use_case = Column(String(32), nullable=False, unique=True)
    template_id = Column(
        UUID(as_uuid=False),
        ForeignKey("respond_message_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Survives template hard-delete so the UI can say "template was removed"
    # instead of silently showing "not set".
    template_name_snapshot = Column(String(255), nullable=True)
    # {"1": "contact_name", "2": "message", ...} - positional param -> variable.
    param_mapping = Column(JSONB, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    template = relationship("RespondMessageTemplate")
