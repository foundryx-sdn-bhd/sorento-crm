"""Registry of every email event the system can emit.

Adding a new event = add a row here. `seed_event_configs` upserts into
`email_event_configs` on startup so the admin UI lists it immediately.

Each row gives operators:
- a per-event enable/disable switch (`enabled`)
- optional rate / window overrides
- optional coalesce window (collapses bursts to one email per recipient)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.email_outbox import EmailEventConfig


@dataclass(frozen=True)
class EventDef:
    event_key: str
    display_name: str
    description: str
    priority: int = 10
    coalesce_window_seconds: Optional[int] = None


EMAIL_EVENT_REGISTRY: list[EventDef] = [
    EventDef(
        "password_reset",
        "Password reset",
        "Reset-password link to user's registered email.",
        priority=0,
    ),
    EventDef(
        "user_invitation",
        "User invitation",
        "Initial invite email when a user is created or re-invited.",
        priority=0,
    ),
    EventDef(
        "purchase_request_approval_link",
        "Purchase request / sponsorship approval link",
        "One-time approval link sent to the approver when a request is sent for approval.",
        priority=0,
    ),
    EventDef(
        "purchase_request_approved",
        "Purchase request / sponsorship approved",
        "Notifies the requester that their PR / sponsorship was approved.",
    ),
    EventDef(
        "purchase_request_rejected",
        "Purchase request / sponsorship rejected",
        "Notifies the requester that their PR / sponsorship was rejected.",
    ),
    EventDef(
        "stock_inquiry_created",
        "Stock inquiry created (internal)",
        "Alerts Project Sales team when an internal user creates a stock inquiry.",
        coalesce_window_seconds=10,
    ),
    EventDef(
        "stock_inquiry_created_external",
        "Stock inquiry created (portal/external)",
        "Alerts Project Sales team when a stock inquiry comes from the portal or external API.",
        coalesce_window_seconds=10,
    ),
    EventDef(
        "form_comment_from_submitter",
        "Form comment from submitter",
        "Tells the form's SLA handler that the submitter commented on their form in the portal.",
        coalesce_window_seconds=10,
    ),
    EventDef(
        "complaint_created_external",
        "Complaint created (portal)",
        "Alerts the complaint handler team when a portal user files a complaint.",
        coalesce_window_seconds=10,
    ),
    EventDef(
        "ticket_assigned",
        "Ticket assigned",
        "Direct notice to the ticket assignee when a ticket is assigned to them.",
    ),
    EventDef(
        "ticket_team_new",
        "Ticket new (team CC)",
        "Tier-1 team CC when a new ticket lands.",
        coalesce_window_seconds=10,
    ),
    EventDef(
        "ticket_responded",
        "Ticket responded",
        "Notifies the ticket submitter when their ticket gets a response.",
    ),
    EventDef(
        "ticket_resolved",
        "Ticket resolved",
        "Notifies the ticket submitter when their ticket is resolved.",
    ),
    EventDef(
        "external_product_attachment",
        "Product attachment linkage (external API)",
        "Notifies attachment uploaders when n8n / external API links files to a product. "
        "Coalesced because bulk uploads can fire many callbacks in seconds - this is the "
        "code path that caused the mail-server incident.",
        coalesce_window_seconds=20,
    ),
    EventDef(
        "external_promotion_uploader_notice",
        "Promotion created (external API)",
        "Notifies attachment uploaders when a promotion is created using their files.",
        coalesce_window_seconds=20,
    ),
    EventDef(
        "external_form_created",
        "Form created (external API)",
        "Notifies attachment uploaders when a form record is created via external API "
        "linked to their files.",
        coalesce_window_seconds=20,
    ),
    EventDef(
        "external_packing_list_created",
        "Packing list created (external API)",
        "Notifies attachment uploaders when a packing list is created via external API "
        "linked to their files.",
        coalesce_window_seconds=20,
    ),
    EventDef(
        "form_sla_assigned",
        "Form SLA assigned",
        "Notifies the assignee when a form SLA tracker is initiated.",
    ),
    EventDef(
        "form_sla_escalated",
        "Form SLA escalated",
        "Notifies the next-tier owner when an SLA tracker passes its due_at.",
    ),
    EventDef(
        "form_sla_updated",
        "Form SLA state change",
        "Notifies the assignee on SLA state transitions (responded, resolved, etc.).",
    ),
    EventDef(
        "sla_deadline_extended",
        "SLA deadline extended",
        "Notifies the next escalation tier when an assignee extends a task's "
        "resolution deadline.",
    ),
    EventDef(
        "import_job_completed",
        "Bulk import job completed",
        "Notifies the job owner when a bulk import reaches a terminal state.",
    ),
    EventDef(
        "workflow_form_state_transition",
        "Workflow form state change",
        "Email step in a workflow form builder transition rule.",
        coalesce_window_seconds=10,
    ),
    EventDef(
        "daily_sla_summary",
        "Daily SLA digest",
        "Per-user daily digest of SLA performance and outstanding assignments.",
        priority=20,
    ),
    EventDef(
        "supplier_loading_notice",
        "Supplier loading notice",
        "Bilingual pack-and-ship notice sent to a supplier when a Loading Plan is approved, "
        "carrying the plan document as an attachment.",
        priority=5,
    ),
    EventDef(
        "ticket_comment_mention",
        "Mentioned in an internal note",
        "Emails a user when a teammate mentions them in an internal note on a "
        "conversation ticket. Gated on the user's notify_email_on_mention toggle.",
    ),
    EventDef(
        "notification_delivery",
        "Generic notification delivery",
        "Fallback event_key for legacy NotificationDelivery rows whose event_type does not "
        "map to a more specific registry entry.",
    ),
    EventDef(
        "onboarding_intake_link",
        "Onboarding intake link",
        "Sends a requester the link they submit their team's details through. "
        "Priority 0: nothing else in the flow can start until they have it.",
        priority=0,
    ),
    EventDef(
        "onboarding_submitted",
        "Onboarding submission received",
        "Confirms to the requester that their batch reached the review queue.",
    ),
    EventDef(
        "onboarding_completed",
        "Onboarding batch complete",
        "Tells the requester what was created, what already existed, and what failed.",
    ),
]


def _event_by_key() -> dict[str, EventDef]:
    return {e.event_key: e for e in EMAIL_EVENT_REGISTRY}


EVENT_REGISTRY_INDEX: dict[str, EventDef] = _event_by_key()


def get_event_def(event_key: str) -> Optional[EventDef]:
    return EVENT_REGISTRY_INDEX.get(event_key)


def seed_event_configs(db: Session) -> int:
    """Idempotent upsert. Returns number of rows inserted (existing rows are NOT overwritten - 
    operators may have toggled `enabled` off or set overrides we must preserve)."""
    existing = {row.event_key for row in db.query(EmailEventConfig.event_key).all()}
    created = 0
    for evt in EMAIL_EVENT_REGISTRY:
        if evt.event_key in existing:
            continue
        db.add(
            EmailEventConfig(
                event_key=evt.event_key,
                display_name=evt.display_name,
                description=evt.description,
                enabled=True,
            )
        )
        created += 1
    if created:
        db.commit()
    return created
