"""Portal "Conversation" view (lane SALES-CONVO, PLAN-sales-conversation-view-30sep.md).

Mounted at ``/api/v1/public/portal`` beside the Customer asks routes. Auth: the portal token.

The view belongs to a portal contact linked to one or more active sales agents
(`sales_agents_for_contact`, the same resolution Customer asks uses). It lists the WhatsApp
conversations of the customers assigned to any of those agents and reads one thread at a time through the SAME service core the CRM's
ticket drawer and Conversations inbox read (`ConversationSLATrackingService`), after its own
scope check. Read-only: no reply, no note (owner ruling 30 Sep, Q1). Messages only: the staff
internal notes on a contact are NOT served here (security review 30 Sep: a portal token is not a
staff session, and the notes table is not company-scoped), so there is no `/comments` twin.
A contact linked to no agent gets 403 `NOT_A_SALES_AGENT`; the per-contact `conversation`
switch off gets 403 `FORM_TYPE_NOT_VISIBLE`; a contact outside the agent's customers is a 404
on every thread read.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1.public.portal import get_portal_token
from app.database import get_db
from app.models.portal import PortalToken
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.portal_conversation import PortalConversationRow
from app.services import portal_conversation_service, price_tag_request_service
from app.services.error_handler import AppException, handle_not_found
from app.services.portal_form_visibility_service import switched_form_types
from app.services.portal_service import CONVERSATION_FORM_TYPE
from app.services.sla_service import ConversationSLATrackingService

router = APIRouter(tags=["public-portal-conversations"])


def _agent_ids(db: Session, token: PortalToken) -> list[str]:
    """The same two-step gate `portal_customer_asks._agent_ids` runs, for this kind's switch.
    Kept local rather than shared: that router is being reshaped by lane ASKS-UX (#1385)."""
    agents = price_tag_request_service.sales_agents_for_contact(db, token.contact_id)
    if not agents:
        raise AppException(
            status_code=403,
            message="Conversations are for sales agents only.",
            code="NOT_A_SALES_AGENT",
        )
    if CONVERSATION_FORM_TYPE not in switched_form_types(db, token.contact_id):
        raise AppException(
            status_code=403,
            message="Conversation is not available for your account.",
            code="FORM_TYPE_NOT_VISIBLE",
        )
    return [agent.id for agent in agents]


def _contact_in_scope(db: Session, agent_ids: list[str], contact_id: str) -> str:
    """The `respond_contacts.id` of a row of these agents' list, or a 404. Only that id is
    accepted: a phone number or a Respond id is a guessable key, and this is not the CRM's
    permission-gated inbox."""
    row = portal_conversation_service.contact_in_scope(db, agent_ids, contact_id)
    if row is None:
        raise handle_not_found("Conversation", contact_id)
    return row["contact_id"]


@router.get("/conversations", response_model=ListResponse[PortalConversationRow])
def portal_list_conversations(
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=MAX_PAGE_LIMIT),
    q: Optional[str] = Query(None, max_length=100),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """One row per customer contact with a chat, latest message first. `q` matches the
    customer name or code, the contact name, the phone, or the last message."""
    agent_ids = _agent_ids(db, token)
    return portal_conversation_service.list_for_agent(db, agent_ids, page=page, limit=limit, q=q)


@router.get("/conversations/{contact_id}/page")
def portal_conversation_page(
    contact_id: str,
    before: Optional[str] = Query(None, description="Message id to page OLDER than (exclusive)"),
    after: Optional[str] = Query(None, description="Message id to page NEWER than (exclusive)"),
    around: Optional[str] = Query(None, description="Message id to centre the window on"),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """One scroll-back window of the contact's thread: byte-identical to the CRM's
    contact-keyed page, the same service core answers both."""
    agent_ids = _agent_ids(db, token)
    if len([c for c in (before, after, around) if c]) > 1:
        raise HTTPException(status_code=422, detail="Pass at most one of before, after, around.")
    contact_pk = _contact_in_scope(db, agent_ids, contact_id)
    return ConversationSLATrackingService(db).fetch_contact_thread_page(
        contact_pk, before=before, after=after, around=around, limit=limit
    )


@router.get("/conversations/{contact_id}/search")
def portal_conversation_search(
    contact_id: str,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(100, ge=1, le=MAX_PAGE_LIMIT),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """In-thread search, same shape as the CRM's."""
    agent_ids = _agent_ids(db, token)
    contact_pk = _contact_in_scope(db, agent_ids, contact_id)
    return ConversationSLATrackingService(db).search_contact_thread(contact_pk, q=q, limit=limit)
