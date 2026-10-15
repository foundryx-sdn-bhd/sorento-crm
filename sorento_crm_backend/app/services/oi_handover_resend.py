"""Send to purchasing: rebuild an order inquiry's open rows into the handover email and send
it again (`documentation/plans/scm/PLAN-oi-resend-purchasing-15oct.md`).

The automatic `order_inquiry_handover` email goes out once, post-commit, for the lines a
write changed, and only through an ENABLED automation (`AutomationService.dispatch_event`).
While that automation was switched off (14-15 Oct) nothing was sent and nothing was
recorded, and there was no way to send it again. This is that way, single or bulk:

* the content is the OI's open rows (`raised` / `partly_linked`), each built by the same
  `ProjectOrderInquiryService.handover_item` the automatic email queues, aggregated by the same
  `_build_handover_context` (same template, same per-line SO files), and marked "(Resent)";
* the recipients and template are the handover automation's own, enabled or not, through
  `park_send` (the reserve flow's preview-then-send pattern);
* each send is one `AutomationService.send_match` run, so it leaves an `automation_runs` row
  and a `notifications` + `notification_deliveries` row, which `last_sent` reads back.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.automation import Automation
from app.models.notification import Notification
from app.models.project_so import (
    INQUIRY_PARTLY_LINKED,
    INQUIRY_RAISED,
    OrderInquiry,
    OrderInquiryRow,
)
from app.services import park_send
from app.services.error_handler import AppException

logger = logging.getLogger(__name__)

TRIGGER_TYPE = "order_inquiry_handover"
SOURCE_KIND = "order_inquiry_handover_resend"
#: Purchasing has not acted on these yet; a placed or actioned row was already handled.
OPEN_STATES = (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED)
MAX_INQUIRIES = 50


def handover_automation(db: Session) -> Optional[Automation]:
    """The handover automation, switched on or off: the enabled one when there is one (the
    pick `park_send.first_automation` makes), else the oldest."""
    return park_send.first_automation(db, TRIGGER_TYPE) or (
        db.query(Automation)
        .filter(Automation.trigger_type == TRIGGER_TYPE)
        .order_by(Automation.created_at.asc())
        .first()
    )


class OrderInquiryHandoverResend:
    def __init__(self, db: Session):
        self.db = db

    def _inquiries(self, inquiry_ids: Sequence[str]) -> List[OrderInquiry]:
        found = {
            str(inquiry.id): inquiry
            for inquiry in self.db.query(OrderInquiry)
            .filter(OrderInquiry.id.in_(list(inquiry_ids)))
            .all()
        }
        if any(str(inquiry_id) not in found for inquiry_id in inquiry_ids):
            raise AppException(
                404, "This order inquiry no longer exists.", code="order_inquiry_not_found"
            )
        seen: set = set()
        ordered = []
        for inquiry_id in inquiry_ids:
            if str(inquiry_id) not in seen:
                seen.add(str(inquiry_id))
                ordered.append(found[str(inquiry_id)])
        return ordered

    def _context(
        self, inquiry: OrderInquiry, actor_user_id: str
    ) -> Optional[Tuple[Dict[str, Any], int]]:
        """`(context, line_count)` for one OI, or `None` when nothing is open."""
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
            _build_handover_context,
        )

        rows = (
            self.db.query(OrderInquiryRow)
            .filter(
                OrderInquiryRow.order_inquiry_id == inquiry.id,
                OrderInquiryRow.state.in_(OPEN_STATES),
            )
            .order_by(OrderInquiryRow.id.asc())
            .all()
        )
        if not rows:
            return None
        service = ProjectOrderInquiryService(self.db)
        # A partly linked row asks only for what no document carries yet: the linked part
        # is already on order, and re-asking for it invites a double order.
        linked = service._linked_qty_by_row([str(row.id) for row in rows])
        # `actor` stays the OI's raiser (`handover_item`'s own fallback), so "Raised by" in
        # the email is true and the raiser is on Cc as on the automatic email; the presser
        # is recorded as `sent_by`.
        items = [
            service.handover_item(
                row,
                kind="raised",
                qty=(
                    Decimal(str(row.qty)) - linked[str(row.id)]
                    if str(row.id) in linked
                    else None
                ),
            )
            for row in rows
        ]
        attachments: Dict[str, List[Dict[str, Any]]] = {}
        core_line_ids = [item["core_line_id"] for item in items if item.get("core_line_id")]
        if core_line_ids:
            try:
                from app.services import so_line_attachments

                attachments = so_line_attachments.handover_attachments(self.db, core_line_ids)
            except Exception:  # noqa: BLE001 - files never stop the email, as on the automatic send
                logger.exception("handover resend: could not load line attachments")
        context, _source_id = _build_handover_context(items, attachments)
        handover = context["handover"]
        handover["subject_scope"] = f"{handover['subject_scope']} (Resent)"
        handover["headline"] = (
            f"RESENT: {handover['headline']}" if handover.get("headline") else "RESENT"
        )
        handover["resent"] = True
        context["sent_by"] = self._presser_name(actor_user_id)
        return context, len(items)

    def _presser_name(self, user_id: str) -> Optional[str]:
        from app.models.user import User

        user = self.db.get(User, user_id)
        return (user.name or user.email) if user is not None else None

    def _last_sent(self, inquiry_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """The newest handover email on record per OI, automatic or resent."""
        from app.services.order_inquiry_reserve_service import as_utc

        rows = (
            self.db.query(Notification.created_at, Notification.data)
            .filter(
                Notification.type == "automation_email",
                Notification.data["trigger_type"].astext == TRIGGER_TYPE,
                Notification.data["source_id"].astext.in_([str(i) for i in inquiry_ids]),
            )
            .order_by(Notification.created_at.desc())
            .all()
        )
        out: Dict[str, Dict[str, Any]] = {}
        for created_at, data in rows:
            data = data or {}
            source_id = str(data.get("source_id"))
            if source_id in out:
                continue
            resent = data.get("source_kind") == SOURCE_KIND
            out[source_id] = {
                "at": as_utc(created_at),
                "kind": "resent" if resent else "automatic",
                "by": data.get("sent_by") if resent else None,
            }
        return out

    def _plan(self, inquiry_ids: Sequence[str], actor_user_id: str):
        """`(automation, sendable, skipped)`; sendable = `[(inquiry, context, line_count,
        recipients)]` in the order asked."""
        if not inquiry_ids:
            raise AppException(422, "Choose at least one order inquiry.")
        if len(inquiry_ids) > MAX_INQUIRIES:
            raise AppException(422, f"Send at most {MAX_INQUIRIES} order inquiries at once.")
        inquiries = self._inquiries(inquiry_ids)
        automation = handover_automation(self.db)
        sendable, skipped = [], []
        for inquiry in inquiries:
            built = self._context(inquiry, actor_user_id)
            if built is None:
                skipped.append(_skip(inquiry, "nothing_open"))
                continue
            context, line_count = built
            recipients = (
                park_send.resolve_ordered(self.db, automation, context) if automation else []
            )
            if not recipients:
                skipped.append(_skip(inquiry, "no_recipient"))
                continue
            sendable.append((inquiry, context, line_count, recipients))
        return automation, sendable, skipped

    def preview(self, inquiry_ids: Sequence[str], *, actor_user_id: str) -> Dict[str, Any]:
        automation, sendable, skipped = self._plan(inquiry_ids, actor_user_id)
        last_sent = self._last_sent([str(inquiry.id) for inquiry, *_rest in sendable])
        emails = []
        for inquiry, context, line_count, recipients in sendable:
            email = park_send.render_preview(
                self.db, TRIGGER_TYPE, context, recipients, automation=automation
            )
            email.update(
                inquiry_id=str(inquiry.id),
                inquiry_no=inquiry.inquiry_no,
                inquiry_count=1,
                line_count=line_count,
                last_sent=last_sent.get(str(inquiry.id)),
            )
            emails.append(email)
        return {
            "emails": emails,
            "inquiry_total": len(emails),
            "unresolved": [],
            "skipped": skipped,
            "automation_enabled": bool(automation is not None and automation.enabled),
        }

    def send(self, inquiry_ids: Sequence[str], *, actor_user_id: str) -> Dict[str, Any]:
        """One email per OI. Each is its own committed automation run, so one failure is
        reported and the rest still go."""
        from app.services import automation_triggers
        from app.services.automation_service import AutomationService

        automation, sendable, skipped = self._plan(inquiry_ids, actor_user_id)
        sent = 0
        for inquiry, context, _line_count, _recipients in sendable:
            match = automation_triggers.TriggerMatch(
                context=context, source_kind=SOURCE_KIND, source_id=str(inquiry.id)
            )
            try:
                AutomationService(self.db).send_match(automation, match)
                sent += 1
            except Exception:  # noqa: BLE001 - reported per OI, never a stuck screen
                logger.exception("handover resend failed for order inquiry %s", inquiry.id)
                skipped.append(_skip(inquiry, "failed"))
        return {"emails_sent": sent, "items_sent": sent, "skipped": skipped}


def _skip(inquiry: OrderInquiry, reason: str) -> Dict[str, Any]:
    return {"inquiry_id": str(inquiry.id), "inquiry_no": inquiry.inquiry_no, "reason": reason}
