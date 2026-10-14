"""Who gets told when a committed form action is reversed (AC-N-1..N-7).

Three audiences, and each is told a different thing:
 - the person the form went BACK to: you hold this again;
 - the person whose task was VOIDED: that task is gone, by whom, and why;
 - the contact: a correction, but only when the committed action had already told
    them something (AC-N-5) - an undo of a purely internal transition does not message
    the customer.

Every send is best-effort. The reversal has already committed by the time this runs, so
raising here would report a completed undo as a failure.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _tracker(db: Session, tracking_id: Optional[str]):
    if not tracking_id:
        return None
    from app.models.sla import ConversationSLATracking

    return (
        db.query(ConversationSLATracking)
        .filter(ConversationSLATracking.id == str(tracking_id))
        .first()
    )


def _entity_number(db: Session, source_entity_type: str, source_entity_id: str) -> str:
    from app.services.form_sla_service import _resolve_entity_number

    return (
        _resolve_entity_number(db, source_entity_type, source_entity_id)
        or str(source_entity_id)[:8]
    )


def _actor_name(db: Session, user_id: Optional[str]) -> str:
    if not user_id:
        return "an administrator"
    from app.models.user import User

    user = db.query(User).filter(User.id == str(user_id)).first()
    return ((user.name or user.email) if user else None) or "an administrator"


def notify_undo(db: Session, record, *, actor_id: Optional[str], reason: str) -> None:
    from app.services.form_sla_service import _form_detail_link
    from app.services.notification_service import NotificationService

    from app.services.form_action_registry import get_action

    action = get_action(record.action_key)
    label = (action.label if action else "action").lower()
    number = _entity_number(db, record.source_entity_type, record.source_entity_id)
    link = _form_detail_link(record.source_entity_type, str(record.source_entity_id))
    who = _actor_name(db, actor_id)
    service = NotificationService(db)

    def _send(
        user_id: Optional[str],
        title: str,
        body: str,
        event_type: str,
        tracker=None,
        use_case: str = "form_action_reopened",
    ) -> None:
        # Never notify the person who pressed Undo about their own action (AC-N-1/N-2).
        if not user_id or (actor_id and str(user_id) == str(actor_id)):
            return
        data: dict = {"link": link, "reason": reason}
        if tracker is not None:
            # The whatsapp_* keys are what lets the delivery render an APPROVED
            # template when the recipient's 24h window is closed. Without them the
            # sender falls back to the sla_assignment use case with no vars - the
            # escalation template filled with dashes, telling the recipient nothing.
            try:
                from app.services.form_sla_service import build_sla_whatsapp_data

                data.update(
                    build_sla_whatsapp_data(
                        db,
                        tracker,
                        str(user_id),
                        body,
                        use_case=use_case,
                        reason=reason,
                        extra_vars={"sender_name": who},
                    )
                )
            except Exception:
                logger.warning(
                    "Could not build WhatsApp template data for undo notification "
                    "on tracker %s; out-of-window sends will be skipped",
                    getattr(tracker, "id", None),
                )
        try:
            service.create_with_channel_preferences(
                user_id=str(user_id),
                type="sla_form_action_undone",
                title=title,
                body=body,
                data=data,
                source_entity_type=record.source_entity_type,
                source_entity_id=str(record.source_entity_id),
                event_type=event_type,
                send_in_app=True,
                send_email=True,
                send_whatsapp=True,
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )
        except Exception:
            logger.exception("Undo notification failed for user %s", user_id)

    voided = _tracker(db, record.spawned_tracking_id)
    if voided is not None:
        _send(
            getattr(voided, "assigned_to_id", None),
            f"Your task on {number} was voided",
            f"{who} undid the {label} on {number}, so the task assigned to you no longer "
            f"applies. Reason: {reason}",
            "form_action_undone_voided",
            tracker=voided,
            use_case="form_action_voided",
        )

    reopened = _tracker(db, record.prior_tracking_id)
    if reopened is not None:
        _send(
            getattr(reopened, "assigned_to_id", None),
            f"{number} is back with you",
            f"{who} undid the {label} on {number}, so it has returned to you and the SLA "
            f"clock has restarted. Reason: {reason}",
            "form_action_undone_reopened",
            tracker=reopened,
            use_case="form_action_reopened",
        )

    # Always, regardless of `tells_contact`. That flag describes whether the FORWARD
    # action messaged the contact; it is not a reason to keep a reversal from them. The
    # contact can see this form's status in the portal, so a status moving backwards is
    # news whether or not the move forwards was announced.
    _notify_contact(db, record, number=number, reason=reason)


# Status codes are stored snake_case; the contact should read words, not column values.
_STATUS_LABELS = {
    "draft": "Draft",
    "submitted": "Submitted",
    "pending_approval": "Pending Approval",
    "approved": "Approved",
    "rejected": "Rejected",
    "processed_by_cs": "Processed by Customer Service",
    "closed": "Closed",
    "voided": "Voided",
    "fulfilled": "Fulfilled",
}


def _status_label(value) -> str:
    code = (str(value or "")).strip().lower()
    return _STATUS_LABELS.get(code) or code.replace("_", " ").title() or "its previous state"


def _pr_display_status(header) -> str:
    """The state a PR/SF actually SHOWS, not the raw `status` column.

    PR/SF carry two parallel fields - `status` (lifecycle) and `approval_status` - so a
    form sitting at "pending approval", or even approved, can still read
    `status='submitted'`. Reporting the raw column tells the contact their form went
    "back to Submitted" when the screen in front of them says "Pending Approval".

    This mirrors `getDisplayStatus` in PurchaseRequestsList.tsx exactly. Two copies of
    one rule is a smell; the honest fix is a single status field, which is a schema
    decision and not this feature's to make.
    """
    lifecycle = (str(getattr(header, "status", None) or "")).strip().lower()
    approval = (str(getattr(header, "approval_status", None) or "")).strip().lower()

    # Terminal CS states outrank the approval decision.
    if lifecycle == "processed_by_cs":
        return "Processed by Customer Service"
    if lifecycle == "closed":
        return "Closed"
    if lifecycle == "voided":
        return "Voided"
    if approval == "pending":
        return "Pending Approval"
    if approval == "approved":
        return "Approved"
    if approval == "rejected":
        return "Rejected"
    if lifecycle == "submitted":
        return "Submitted"
    return _status_label(lifecycle) if lifecycle else "Draft"


def _correction_text(number: str, restored: str, reason_part: str) -> str:
    return (
        f"Update on {number}: the status has been changed back to {restored}."
        f"{reason_part} We will let you know once it is decided again."
    )


def _notify_contact(db: Session, record, *, number: str, reason: str) -> None:
    """Tell the contact WHAT changed back and WHY.

    They were already told the form had moved on, and that message cannot be unsent, so
    the correction has to be specific: naming only "under review again" leaves them
    guessing which state it returned to and why it moved at all. Sent through the same
    path the original action used, so it lands in the same thread and writes the same
    outbox row on success and on failure.
    """
    try:
        entity_type = record.source_entity_type
        entity_id = str(record.source_entity_id)
        clean_reason = (reason or "").strip().rstrip(".")
        reason_part = f" Reason: {clean_reason}." if clean_reason else ""

        if entity_type in ("purchase_request", "sponsorship_form"):
            from app.services.procurement_service import PurchaseRequestService

            service = PurchaseRequestService(db)
            header = service.get_request(entity_id)
            if not getattr(header, "contact_id", None):
                return
            # Read the state AFTER the inverse ran - what it was actually put back to,
            # not what we assume the previous state was - and report the DISPLAYED state
            # rather than the raw column, so it matches the screen and the portal.
            restored = _pr_display_status(header)
            service._send_purchase_request_contact_message(
                header,
                message_text=_correction_text(number, restored, reason_part),
                extra_context_vars={
                    "update": f"Changed back to {restored}.{reason_part}",
                    "portal_url": service._purchase_request_portal_or_view_url(
                        header, entity_id
                    ),
                    "view_url": (service._build_request_view_url(entity_id) or "").strip(),
                },
            )
            return

        if entity_type == "stock_inquiry":
            from app.services.procurement_service import StockInquiryService
            from app.services.respond_identifier import resolve_send_identifier

            service = StockInquiryService(db)
            inquiry = service.get_inquiry(entity_id)
            # `contact_id` is the INTERNAL respond_contacts UUID; the Respond.io send
            # API needs the contact's respond_io_id. Resolve exactly the way the
            # service's own reply path does (inbox URL first, then contact lookup) -
            # passing the raw FK 400s on every send, silently, in the except below.
            identifier = service._identifier_from_respond_inbox_url(
                getattr(inquiry, "respond_inbox_url", None)
            ) or resolve_send_identifier(db, getattr(inquiry, "contact_id", None))
            if not identifier:
                return
            restored = _status_label(getattr(inquiry, "status", None))
            text = _correction_text(number, restored, reason_part)
            service._enqueue_stock_inquiry_respond_message(
                inquiry_id=entity_id,
                identifier=str(identifier),
                message_text=text,
                respond_user_id=getattr(inquiry, "last_responded_by", None) or "",
                crm_sender_user_id=None,
                space_id=getattr(inquiry, "space_id", None),
                extra_context_vars={"update": f"Changed back to {restored}.{reason_part}"},
            )
            return

        if entity_type == "complaint":
            from app.services.complaints_service import ComplaintService
            from app.services.respond_identifier import resolve_send_identifier

            service = ComplaintService(db)
            complaint = service.get_complaint(entity_id)
            identifier = service._identifier_from_respond_inbox_url(
                getattr(complaint, "respond_inbox_url", None)
            ) or resolve_send_identifier(db, getattr(complaint, "contact_id", None))
            if not identifier:
                return
            restored = _status_label(getattr(complaint, "status", None))
            text = _correction_text(number, restored, reason_part)
            service._enqueue_respond_message_for_complaint(
                complaint_id=entity_id,
                identifier=str(identifier),
                display_message=text,
                respond_user_id=getattr(complaint, "last_responded_by", None) or "",
                crm_sender_user_id=None,
                space_id=getattr(complaint, "space_id", None),
                extra_context_vars={"update": f"Changed back to {restored}.{reason_part}"},
            )
            return

        if entity_type == "ticket":
            # The submitter was told "your ticket has been resolved" and that message
            # cannot be unsent - route the correction through the same notifier, which
            # picks in-app + email for user submitters and WhatsApp (outbox-logged)
            # for respond-contact submitters.
            from app.models.tickets import Ticket
            from app.services.ticket_notification_service import notify_submitter_text

            ticket = db.query(Ticket).filter(Ticket.id == entity_id).first()
            if ticket is None:
                return
            restored = _status_label(getattr(ticket, "status", None))
            notify_submitter_text(
                db,
                ticket=ticket,
                title=f"Update on {number}: status changed back",
                text=_correction_text(number, restored, reason_part),
            )
            return

        logger.info(
            "No contact-send path for %s; contact not corrected for %s",
            entity_type,
            entity_id,
        )
    except Exception:
        logger.exception("Contact correction failed for form action %s", record.id)
