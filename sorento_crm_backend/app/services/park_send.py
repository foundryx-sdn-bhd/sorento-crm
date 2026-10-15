"""Park, then send all parked: the reusable half (`PLAN-oi-staged-notify-14oct.md`, "The
reusable pattern").

Plain functions, no registry. A document service builds one ITEM per parked thing
(`{"context": <the dispatch context>, ...}`), and this module answers the two questions
every such flow shares:

* who does an item's email go to - `group_by_recipient` resolves each item's recipients
  with the first enabled automation of its trigger (the same pick as
  `OrderInquiryReserveService.notified_name`) and groups the items by the first address that
  is not the actor's own, ordered like `AutomationService._send_per_match` one_email (the
  actor's address last, so it lands on Cc and never displaces the real To);
* what will the email look like - `render_preview` renders a group's context through the
  automation's own template, the same render the send uses, so preview == email.

The document service stamps its rows and queues one dispatch per group on its own
post-commit queue; nothing here writes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.automation import Automation
from app.models.email_template import EmailTemplate
from app.services import automation_recipients


def first_automation(db: Session, trigger_type: str) -> Optional[Automation]:
    """The enabled automation a notice for `trigger_type` goes through (oldest first)."""
    return (
        db.query(Automation)
        .filter(Automation.trigger_type == trigger_type, Automation.enabled.is_(True))
        .order_by(Automation.created_at.asc())
        .first()
    )


def _email_key(person: Dict[str, Any]) -> str:
    return (person.get("email") or "").strip().lower()


def order_recipients(
    recipients: Sequence[Dict[str, Any]], actor_email: Optional[str]
) -> List[Dict[str, Any]]:
    """Non-actor addresses first, the actor's own last (one_email's own order)."""
    actor = (actor_email or "").strip().lower()
    if not actor:
        return list(recipients)
    return [r for r in recipients if _email_key(r) != actor] + [
        r for r in recipients if _email_key(r) == actor
    ]


def merge_people(contexts: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """`raisers[]` / `requesters[]` for a group context: every distinct person across the
    items' own singular `raiser` / `requester`, in item order."""
    out: Dict[str, List[Dict[str, Any]]] = {"raisers": [], "requesters": []}
    for key, singular in (("raisers", "raiser"), ("requesters", "requester")):
        seen: set = set()
        for context in contexts:
            person = context.get(singular)
            if person and _email_key(person) not in seen:
                seen.add(_email_key(person))
                out[key].append(person)
    return out


def resolve_ordered(db: Session, automation: Automation, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    ordered = automation_recipients.resolve_recipients(
        db, dict(automation.recipient_config or {}), promotion_context=context
    )
    return order_recipients(ordered, ((context.get("actor") or {}).get("email")))


def group_by_recipient(
    db: Session, trigger_type: str, items: Sequence[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """`(groups, unresolved)`. Each item carries its own dispatch `context` (with `actor`).
    A group is `{"items": [...], "recipients": [ordered, first = To]}`; `recipients` is
    resolved again on the group's merged people so every raiser and requester in it is on
    the email. Items whose recipients resolve to nobody (no enabled automation, or an
    empty list) are `unresolved`."""
    automation = first_automation(db, trigger_type)
    if automation is None:
        return [], list(items)
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    unresolved: List[Dict[str, Any]] = []
    for item in items:
        recipients = resolve_ordered(db, automation, item["context"])
        if not recipients:
            unresolved.append(item)
            continue
        by_key.setdefault(_email_key(recipients[0]), []).append(item)
    groups = []
    for group_items in by_key.values():
        contexts = [i["context"] for i in group_items]
        merged = {**contexts[0], **merge_people(contexts)}
        groups.append({"items": group_items, "recipients": resolve_ordered(db, automation, merged)})
    return groups, unresolved


def render_preview(
    db: Session, trigger_type: str, context: Dict[str, Any], recipients: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """`{to, cc, subject, body_html}` for one group, rendered by the automation's template
    exactly as `AutomationService._send_per_match` renders it (one_email: `recipient` is
    the first address)."""
    from app.services.email_template_service import EmailTemplateService

    automation = first_automation(db, trigger_type)
    template = (
        db.query(EmailTemplate).filter(EmailTemplate.id == automation.email_template_id).first()
        if automation is not None
        else None
    )
    primary = recipients[0]
    subject, body_html = "", ""
    if template is not None:
        rendered = EmailTemplateService(db).render(
            template,
            {
                **context,
                "recipient": {
                    "name": primary.get("name") or primary["email"],
                    "email": primary["email"],
                },
            },
        )
        subject, body_html = rendered["subject"], rendered["body_html"]
    return {
        "to": {"name": primary.get("name") or primary["email"]},
        "cc": [{"name": r.get("name") or r["email"]} for r in recipients[1:]],
        "subject": subject,
        "body_html": body_html,
    }
