"""The portal Conversation view (lane SALES-CONVO, PLAN-sales-conversation-view-30sep.md).

One row per WhatsApp contact linked to a customer assigned to the sales agent, that has at
least one stored message: the customer, the contact, and the latest message. Latest message
first. Read-only (owner ruling 30 Sep, Q1).

Scope chain, each link read from where it already lives:

- agent -> customers: `customers.sales_agent_id`, through the ONE seam
  `stock_ask_service._owning_agent_column` (crew ruling 29 Sep: swap to CONTACT-CUSTOMERS'
  relation in one place);
- customer -> contact: `respond_contact_customers`, company-scoped, tied to the customer's own
  company (a link in another company never puts a stranger on an agent's list);
- contact -> thread: `respond_contacts.respond_io_id` is `chat_histories.contact_id`, and the
  latest row per contact is the same `DISTINCT ON` the CRM inbox runs, narrowed to the agent's
  own contacts first.

`contact_in_scope` is the same query narrowed to one contact, so "may open this thread" and
"is a row of the list" can never disagree.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.access import RespondContact, RespondContactCustomer
from app.models.chat_history import ChatHistory
from app.models.order import Customer
from app.services.conversation_inbox_service import _like_pattern, _snippet


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def _rows_query(db: Session, agent_ids: list[str], *, q: Optional[str] = None, contact_id: Optional[str] = None):
    """The list, unpaged: one row per contact, with its named customer and latest message."""
    from app.services.stock_ask_service import _owning_agent_column

    # The customer a contact is named by when they belong to several of the agent's accounts:
    # the primary link, else the first by name.
    link = (
        select(
            RespondContactCustomer.contact_id.label("contact_id"),
            Customer.customer_name.label("customer_name"),
            Customer.customer_code.label("customer_code"),
        )
        .select_from(RespondContactCustomer)
        .join(
            Customer,
            and_(
                Customer.id == RespondContactCustomer.customer_id,
                Customer.company_id == RespondContactCustomer.company_id,
            ),
        )
        .where(_owning_agent_column().in_(list(agent_ids)))
        .distinct(RespondContactCustomer.contact_id)
        .order_by(
            RespondContactCustomer.contact_id,
            RespondContactCustomer.is_primary.desc(),
            Customer.customer_name,
        )
        .cte("agent_link")
    )
    # The inbox's "latest message per contact" DISTINCT ON, narrowed to THIS agent's contacts
    # before it runs (review 30 Sep, finding 2): the portal polls this every 30s per open tab, so
    # it must not walk every contact's messages the way the staff inbox can afford to.
    agent_contacts = (
        select(RespondContact.respond_io_id)
        .select_from(link)
        .join(RespondContact, RespondContact.id == link.c.contact_id)
        .where(RespondContact.respond_io_id.isnot(None))
    )
    last_msg = (
        select(
            ChatHistory.contact_id.label("contact_key"),
            ChatHistory.sent_at.label("last_at"),
            ChatHistory.message.label("last_message"),
            ChatHistory.type.label("last_direction"),
        )
        .where(ChatHistory.contact_id.in_(agent_contacts))
        .distinct(ChatHistory.contact_id)
        .order_by(ChatHistory.contact_id, ChatHistory.sent_at.desc(), ChatHistory.id.desc())
        .cte("last_msg")
    )

    stmt = (
        select(
            RespondContact.id.label("contact_id"),
            RespondContact.name.label("contact_name"),
            RespondContact.phone_number.label("contact_phone"),
            link.c.customer_name,
            link.c.customer_code,
            last_msg.c.last_at.label("last_message_at"),
            last_msg.c.last_message.label("last_message"),
            last_msg.c.last_direction.label("last_message_direction"),
        )
        .select_from(link)
        .join(RespondContact, RespondContact.id == link.c.contact_id)
        .join(last_msg, last_msg.c.contact_key == RespondContact.respond_io_id)
    )
    if contact_id is not None:
        stmt = stmt.where(RespondContact.id == contact_id)
    needle = (q or "").strip()
    if needle:
        pattern = _like_pattern(needle)
        stmt = stmt.where(
            or_(
                link.c.customer_name.ilike(pattern, escape="\\"),
                link.c.customer_code.ilike(pattern, escape="\\"),
                RespondContact.name.ilike(pattern, escape="\\"),
                RespondContact.phone_number.ilike(pattern, escape="\\"),
                last_msg.c.last_message.ilike(pattern, escape="\\"),
            )
        )
    return stmt.order_by(last_msg.c.last_at.desc(), RespondContact.id.desc())


def _row(row: Any) -> dict[str, Any]:
    return {
        "contact_id": str(row["contact_id"]),
        "customer_name": row["customer_name"],
        "customer_code": row["customer_code"],
        "contact_name": row["contact_name"],
        "contact_phone": row["contact_phone"],
        "last_message_at": _iso(row["last_message_at"]),
        "last_message_snippet": _snippet(row["last_message"]),
        "last_message_direction": row["last_message_direction"],
    }


def list_for_agent(
    db: Session,
    agent_ids: list[str],
    *,
    page: int,
    limit: int,
    q: Optional[str] = None,
) -> dict[str, Any]:
    """The agents' conversations, latest message first, paged like the other portal lists.
    `agent_ids` is every active agent the portal contact is linked to (PORTAL-MULTI-AGENT)."""
    stmt = _rows_query(db, agent_ids, q=q)
    total = int(db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar() or 0)
    rows = db.execute(stmt.offset((page - 1) * limit).limit(limit)).mappings().all()
    return {
        "data": [_row(r) for r in rows],
        "pagination": {"total": total, "page": page, "limit": limit},
        "empty": total == 0,
    }


def contact_in_scope(db: Session, agent_ids: list[str], contact_id: str) -> Optional[dict[str, Any]]:
    """The list row for this contact, or None: a contact that is not a row (an unlinked agent's
    customer, no link, no Respond id, no message yet) has no thread to open here."""
    raw = str(contact_id or "").strip()
    if not raw:
        return None
    row = db.execute(_rows_query(db, agent_ids, contact_id=raw)).mappings().first()
    return _row(row) if row else None
