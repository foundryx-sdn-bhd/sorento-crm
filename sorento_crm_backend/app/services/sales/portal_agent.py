"""Resolve a portal contact to its sales agents (plan 3.5; slice S2).

Lifted from `PriceTagRequestService.lookup_debtors_for_agent` (section 7, "already solved
once"), which now calls this instead of resolving the link itself, so the portal
opportunity form and the price tag debtor lookup share one copy of the rule.

PORTAL-MULTI-AGENT (owner, 16 Oct 2026): one portal contact may be linked to several sales
agents (`sales_agents.contact_id` is not unique), and the portal then answers for ALL of
them together. `agents_for_contact` is the portal's resolver; `agent_for_contact` /
`agent_for_user` keep the one-agent answer only for the CRM staff to-do.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.sales_agent import SalesAgent

logger = logging.getLogger(__name__)


def agents_for_contact(db: Session, contact_id: str) -> List[SalesAgent]:
    """Every ACTIVE sales agent linked to this portal contact, by code then id. An inactive
    agent is left out, and so are its customers; an empty list means the contact is not a
    sales agent (the portal's 403 `NOT_A_SALES_AGENT`)."""
    return (
        db.query(SalesAgent)
        .filter(SalesAgent.contact_id == contact_id, SalesAgent.is_active.is_(True))
        .order_by(SalesAgent.sales_agent, SalesAgent.id)
        .all()
    )


def agent_for_contact(db: Session, contact_id: str) -> Optional[SalesAgent]:
    """The sales agent linked to this portal contact, or ``None``.

    `sales_agents.contact_id` carries no unique constraint, so an unordered `.first()`
    would let Postgres return either row when a contact is linked twice - the same
    salesperson could open a form twice and be answered differently with nothing on
    screen to explain it. Ordered by the agent code and then the id, so the answer is
    always the same, and a second link is logged. The portal reads `agents_for_contact`
    instead; this one answer serves the CRM staff to-do (`agent_for_user`) only.
    """
    agents: List[SalesAgent] = (
        db.query(SalesAgent)
        .filter(SalesAgent.contact_id == contact_id)
        .order_by(SalesAgent.sales_agent, SalesAgent.id)
        .all()
    )
    if not agents:
        return None
    agent = agents[0]
    if len(agents) > 1:
        logger.warning(
            "Contact %s is linked to %s sales agents; the CRM to-do answers for %s.",
            contact_id,
            len(agents),
            agent.sales_agent,
        )
    return agent


def agent_for_user(db: Session, user_id: str) -> Optional[SalesAgent]:
    """The sales agent a CRM user is: `users.respond_contact_id` -> `agent_for_contact`.
    ``None`` for a user with no contact or a contact linked to no agent."""
    from app.models.user import User

    contact_id = db.query(User.respond_contact_id).filter(User.id == user_id).scalar()
    if not contact_id:
        return None
    return agent_for_contact(db, contact_id)
