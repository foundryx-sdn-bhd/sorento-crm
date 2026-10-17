"""Who gets told what about a project, in one place (S5b; AC-E6a, AC-F9, AC-H6).

S3 and S4 both deliberately shipped their alerts as loud log lines with a comment saying the
fan-out belonged to this slice, because the thing they were missing was not the message but
the RECIPIENT RULE: "management" had to mean one auditable thing, not three per-feature
recipient lists that drift.

That rule is `projects.projects.view_all_financials` (grill finding G20), resolved from RBAC
rather than a configured list, so a newly-promoted sales manager starts receiving alerts the
moment they get the role and a departing one stops when the role goes. Nobody has to remember
a per-feature recipient screen.

Everything here is **best-effort**. Each caller has already committed the business fact -- the
price is saved, the PO is recorded, the ladder has moved -- so a notification backend that is
down must never turn a successful save into a 500. Failures warn and return False.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.projects import Project

logger = logging.getLogger(__name__)

# The single definition of "management" for this module (G20).
MANAGEMENT_PERMISSION = "projects.projects.view_all_financials"


def management_user_ids(db: Session) -> List[str]:
    """Active users whose role grants the management permission."""
    from app.models.user import (
        User,
        UserPermission,
        UserRoleAssignment,
        UserRolePermission,
        UserStatus,
    )

    rows = (
        db.query(UserRoleAssignment.user_id)
        .join(UserRolePermission, UserRolePermission.role_id == UserRoleAssignment.role_id)
        .join(UserPermission, UserPermission.id == UserRolePermission.permission_id)
        .join(User, User.id == UserRoleAssignment.user_id)
        .filter(
            UserPermission.slug == MANAGEMENT_PERMISSION,
            # There is no is_active column: ACTIVE vs INACTIVE/BLOCKED lives in `status`,
            # and `is_trashed` is the soft-delete. Both have to be excluded or a departed
            # manager keeps receiving warnings about projects they cannot open.
            #
            # Plain equality, NOT upper(): the live column is a Postgres ENUM named
            # `UserStatus`, so `upper(users.status)` is `function upper("UserStatus") does not
            # exist`. The model declares it as String, so the test schema built from the
            # models accepted upper() happily -- the mismatch only surfaces against the real
            # database. Equality works for both.
            User.status == UserStatus.ACTIVE.value,
            User.is_trashed.is_(False),
        )
        .distinct()
        .all()
    )
    return [str(r[0]) for r in rows]


def approver_user_ids(
    db: Session,
    company_id: Optional[str] = None,
    exclude: Optional[set] = None,
    slug: Optional[str] = None,
) -> List[str]:
    """Active, non-bot users who hold the quotation approve grant AND the quotation's company.

    ``slug`` asks the same question for another grant (the new product application's approve
    and open_code use it); it defaults to the quotation approve grant.

    The company grant is the same shape `cs_routing_service.eligible_pics_query` uses: a role
    that carries the slug says nothing about which company's quotations its holder may see.
    """
    from app.models.company import UserCompany
    from app.models.user import (
        User,
        UserPermission,
        UserRoleAssignment,
        UserRolePermission,
        UserStatus,
    )
    from app.services.company_routing_service import DEFAULT_COMPANY_ID
    from app.services.project_quotation_approval_service import APPROVE_PERMISSION

    rows = (
        db.query(UserRoleAssignment.user_id)
        .join(UserRolePermission, UserRolePermission.role_id == UserRoleAssignment.role_id)
        .join(UserPermission, UserPermission.id == UserRolePermission.permission_id)
        .join(User, User.id == UserRoleAssignment.user_id)
        .join(UserCompany, UserCompany.user_id == User.id)
        .filter(
            UserPermission.slug == (slug or APPROVE_PERMISSION),
            UserCompany.company_id == str(company_id or DEFAULT_COMPANY_ID),
            User.status == UserStatus.ACTIVE.value,
            User.is_trashed.is_(False),
            User.is_integration.is_(False),
        )
        .distinct()
        .all()
    )
    skip = {str(x) for x in (exclude or set()) if x}
    return [str(r[0]) for r in rows if str(r[0]) not in skip]


def _send(
    db: Session,
    *,
    user_ids: List[str],
    notif_type: str,
    title: str,
    body: str,
    data: Dict[str, Any],
    project_id: str,
    dedup_key: str,
    send_email: bool = True,
    send_whatsapp: bool = False,
    send_web_push: bool = False,
    data_for: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> int:
    """Fan out to a de-duplicated recipient set. Returns how many were notified.

    Best-effort per RECIPIENT, not per alert. One try around the whole loop meant the first
    user whose delivery raised -- a stale address, a preference row that fails to load --
    swallowed every remaining manager, and a below-floor price gets approved because two of
    three managers never heard about it. The outer try still covers building the service
    itself, which either works for everybody or for nobody.
    """
    try:
        from app.services.notification_service import NotificationService

        service = NotificationService(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "project notification not sent: type=%s project=%s (%s)",
            notif_type,
            project_id,
            exc,
        )
        return 0

    sent = 0
    for user_id in {u for u in user_ids if u}:
        try:
            service.create_with_channel_preferences(
                user_id=str(user_id),
                type=notif_type,
                title=title,
                body=body,
                data={**data, **data_for(str(user_id))} if data_for else data,
                source_entity_type="project",
                source_entity_id=str(project_id),
                dedup_key=dedup_key,
                event_type=notif_type,
                send_in_app=True,
                send_email=send_email,
                send_whatsapp=send_whatsapp,
                send_web_push=send_web_push,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "project notification not sent to one recipient: type=%s project=%s user=%s (%s)",
                notif_type,
                project_id,
                user_id,
                exc,
            )
            # A statement that failed inside the notification leaves the transaction aborted,
            # so the next recipient would fail for a reason that has nothing to do with them.
            if not db.is_active:
                db.rollback()
    return sent


def floor_breach_dedup_key(event: Dict[str, Any]) -> str:
    """Idempotency key for a below-floor alert: the LINE plus the price that breached.

    Not the line alone, which is what shipped. `create_with_channel_preferences` dedups on
    (user, entity, key, event_type) for ever, so keying on the line meant the FIRST breach
    silenced every later one: a line re-priced above its floor and later dropped below it
    again -- a new decision somebody has to approve -- was invisible.

    Including the price keeps the property that actually matters (saving the same breach twice
    does not re-alert) while letting a genuinely different give-away through.
    """
    price = event.get("unit_price")
    # Normalised so Decimal("80") and Decimal("80.00") are one key, not two.
    try:
        price_key = format(Decimal(str(price)), "f")
    except (InvalidOperation, TypeError, ValueError):
        price_key = str(price)
    return f"{event.get('line_id')}:floor_breach:{price_key}"


def notify_floor_breach(
    db: Session,
    *,
    project: Project,
    event: Dict[str, Any],
    actor_user_id: Optional[str] = None,
) -> int:
    """A quotation line went below its price floor (AC-E6, AC-E6a).

    Fires on the TRANSITION into breach only -- `pop_breach_events` already guarantees that,
    so a line that stays below its floor across five edits alerts once. A per-save alert
    would be an alert storm on exactly the negotiation people are concentrating on.

    The owner is NOT a recipient: they set the price deliberately and telling them what they
    just did is noise. This is an approval signal for management.
    """
    floor = event.get("floor_value")
    price = event.get("unit_price")
    body = (
        f"{project.title}: a line was priced at {price}, below the "
        f"{event.get('floor_level')} floor of {floor}. Approve it or ask for a re-price."
    )
    return _send(
        db,
        user_ids=management_user_ids(db),
        notif_type="project_price_floor_breach",
        title=f"{project.project_code} priced below floor",
        body=body,
        data={
            "project_id": str(project.id),
            "project_code": project.project_code,
            "quotation_id": str(event.get("quotation_id") or ""),
            "line_id": str(event.get("line_id") or ""),
            "unit_price": str(price),
            "floor_value": str(floor),
            "floor_level": event.get("floor_level"),
            "actor_user_id": actor_user_id,
        },
        project_id=str(project.id),
        dedup_key=floor_breach_dedup_key(event),
    )


def _approval_link(project: Project, document) -> str:
    return f"/project-sales/{project.id}/quotation-documents/{document.id}"


def _approval_data(project: Project, document) -> Dict[str, Any]:
    return {
        "project_id": str(project.id),
        "project_code": project.project_code,
        "document_id": str(document.id),
        "document_no": document.document_no,
        # The bell and the email button both read `link`.
        "link": _approval_link(project, document),
    }


def _whatsapp_for(db: Session, project: Project, document, text: str):
    """The per-recipient WhatsApp payload of the `quotation_approval` use case."""
    from app.models.user import User

    link = _approval_link(project, document)
    from app.config import settings

    base = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
    # A WhatsApp template button needs an absolute URL; the in-app link stays relative.
    portal_url = f"{base}{link}" if base else link

    def build(user_id: str) -> Dict[str, Any]:
        user = db.query(User).filter(User.id == user_id).first()
        return {
            "whatsapp_use_case": "quotation_approval",
            "whatsapp_text": text,
            "whatsapp_context_vars": {
                "contact_name": (user.name if user and user.name else "") or "",
                "entity_number": document.document_no,
                "message": text,
                "portal_url": portal_url,
            },
        }

    return build


def notify_quotation_approval_requested(
    db: Session,
    *,
    project: Project,
    document,
    press: Optional[str],
    below: int,
    requester_id: Optional[str],
    requester_name: str,
) -> int:
    """A quotation was sent for approval: tell every approver but the sender. One per press."""
    lines = f"{below} line{'' if below == 1 else 's'} below the price floor"
    text = (
        f"{requester_name} sent quotation {document.document_no} ({project.project_code}) "
        f"for approval: {lines}."
    )
    return _send(
        db,
        user_ids=approver_user_ids(db, document.company_id, {requester_id}),
        notif_type="project_quotation_approval_requested",
        title=f"{project.project_code}: approval needed on {document.document_no}",
        body=text,
        data=_approval_data(project, document),
        project_id=str(project.id),
        dedup_key=f"{document.id}:approval_requested:{press or uuid.uuid4()}",
        send_whatsapp=True,
        data_for=_whatsapp_for(db, project, document, text),
    )


def notify_quotation_approval_decided(
    db: Session,
    *,
    project: Project,
    document,
    approved: bool,
    recipient_id: Optional[str],
    decider_id: Optional[str],
    decider_name: str,
    reason: Optional[str] = None,
) -> int:
    """Approved or sent back: tell whoever asked for it (not the decider)."""
    if not recipient_id or str(recipient_id) == str(decider_id or ""):
        return 0
    verb = "approved" if approved else "sent back"
    body = f"{decider_name} {verb} {document.document_no}."
    if reason:
        body = f"{body} {reason}"
    return _send(
        db,
        user_ids=[recipient_id],
        notif_type=(
            "project_quotation_approval_approved"
            if approved
            else "project_quotation_approval_rejected"
        ),
        title=f"{project.project_code}: {document.document_no} {verb}",
        body=body,
        data=_approval_data(project, document),
        project_id=str(project.id),
        dedup_key=f"{document.id}:approval_{'approved' if approved else 'rejected'}:{uuid.uuid4()}",
        send_web_push=True,
    )


def notify_quotation_approval_withdrawn(
    db: Session,
    *,
    project: Project,
    document,
    actor_id: Optional[str],
    actor_name: str,
    reason: Optional[str],
) -> int:
    """The creator pulled a pending quotation back to change it: tell the approvers."""
    text = (
        f"{actor_name} took quotation {document.document_no} ({project.project_code}) back "
        f"for changes: {reason or ''}"
    ).strip()
    return _send(
        db,
        user_ids=approver_user_ids(db, document.company_id, {actor_id}),
        notif_type="project_quotation_approval_withdrawn",
        title=f"{project.project_code}: {document.document_no} withdrawn",
        body=text,
        data=_approval_data(project, document),
        project_id=str(project.id),
        dedup_key=f"{document.id}:approval_withdrawn:{uuid.uuid4()}",
        send_whatsapp=True,
        data_for=_whatsapp_for(db, project, document, text),
    )


def changes_requested_dedup_key(issue_id: str, note: str) -> str:
    """Idempotency key for a revision request: the ISSUE plus the words that were sent.

    Same shape and same reasoning as `floor_breach_dedup_key`. Keyed on the issue alone, the
    FIRST message would silence every later one, so a customer who comes back with a second,
    different objection would be captured on the row and never announced to anybody. Including
    the message keeps the property that matters (the same submission twice does not re-alert)
    while letting genuinely new feedback through.
    """
    digest = hashlib.sha256(note.strip().encode("utf-8")).hexdigest()[:16]
    return f"{issue_id}:changes_requested:{digest}"


def notify_quotation_changes_requested(
    db: Session,
    *,
    project: Project,
    document,
    record,
    note: str,
    requester_name: Optional[str] = None,
) -> int:
    """The customer will not sign the quotation as it stands (S17).

    Addressed to the person running the pursuit, not to "management": this is a message from a
    customer that somebody has to answer by re-pricing and re-issuing, and management alerts are
    for approvals. A project with no owner falls back to management rather than dropping the
    message on the floor - an unanswered request is the failure this slice exists to prevent.

    The words travel in the BODY, trimmed to what an email preview and a bell dropdown can hold,
    so the salesperson knows whether it needs answering today without opening the CRM. The whole
    note is on the quotation's Signatures tab and in the project's activity feed.
    """
    reference = getattr(record, "our_ref_text", None) or getattr(document, "document_no", "")
    who = (requester_name or "").strip() or "The customer"
    excerpt = " ".join(note.split())
    if len(excerpt) > 400:
        excerpt = f"{excerpt[:400]}..."

    recipients = [str(project.owner_user_id)] if project.owner_user_id else management_user_ids(db)

    return _send(
        db,
        user_ids=recipients,
        notif_type="project_quotation_changes_requested",
        title=f"{project.project_code}: changes requested on {reference}",
        body=f'{who} asked for changes to {reference}: "{excerpt}"',
        data={
            "project_id": str(project.id),
            "project_code": project.project_code,
            "document_id": str(getattr(document, "id", "") or ""),
            "document_no": getattr(document, "document_no", None),
            "issue_id": str(getattr(record, "id", "") or ""),
            "issue_no": getattr(record, "issue_no", None),
            "our_ref": reference,
            "requested_by": requester_name,
        },
        project_id=str(project.id),
        dedup_key=changes_requested_dedup_key(str(getattr(record, "id", "")), note),
    )


def notify_po_mismatch(
    db: Session,
    *,
    project: Project,
    po,
    mismatches: List[Dict[str, Any]],
) -> int:
    """A customer PO line disagrees with what we quoted (AC-F9).

    A mismatch is an exception to chase, so it alerts. Price EROSION from v1 (AC-F9a) is the
    expected outcome of a negotiation and deliberately does not: alerting on it would make
    every well-negotiated PO look broken, which is the fastest way to get alerts muted.

    Both the owner and management are told. The owner has to reconcile it with the
    contractor; management is told because a PO that does not match the quotation is what
    turns into a delivery dispute later.
    """
    if not mismatches:
        return 0

    kinds = sorted({m.get("kind") for m in mismatches if m.get("kind")})
    what = " and ".join(kinds) if kinds else "mismatch"
    lines = ", ".join(
        str(m.get("product_code") or m.get("description") or "unnamed line")
        for m in mismatches[:3]
    )
    more = "" if len(mismatches) <= 3 else f" and {len(mismatches) - 3} more"
    recipients = management_user_ids(db)
    if project.owner_user_id:
        recipients.append(str(project.owner_user_id))

    return _send(
        db,
        user_ids=recipients,
        notif_type="project_po_mismatch",
        title=f"{project.project_code}: PO {po.po_number} does not match the quotation",
        body=(
            f"{len(mismatches)} line(s) show a {what}: {lines}{more}. "
            "Reconcile with the contractor before delivery."
        ),
        data={
            "project_id": str(project.id),
            "project_code": project.project_code,
            "po_id": str(po.id),
            "po_number": po.po_number,
            "mismatch_count": len(mismatches),
            "kinds": kinds,
        },
        project_id=str(project.id),
        # Per PO per shape of problem: adding a third mismatched line to a PO that already
        # alerted is the same conversation.
        dedup_key=f"{po.id}:po_mismatch:{'|'.join(kinds)}",
    )
