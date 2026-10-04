"""Chatbot stock ask v2 S5/S6: the asks record as the CRM Asks tab and the portal's Customer
asks page read and edit it. Names, never bare ids: the row id is the only id on the wire,
and it is what a PATCH addresses."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_serializer


class StockAskResponse(BaseModel):
    id: str
    customer_name: Optional[str] = None
    contact_name: Optional[str] = None
    #: The contact's phone number, for the opened card's header.
    contact_phone: Optional[str] = None
    product_code: str
    product_name: Optional[str] = None
    #: None on an `incoming_eta` / `referred` row (REFER-SALESMAN): the ask named no quantity.
    quantity: Optional[int] = None
    branch: str
    answer_summary: str
    notified_agent: bool
    notify_skip_reason: Optional[str] = None
    state: str
    #: `live` or `console` (a chat console hand test); shown as "Console" on both lists.
    source: str = "live"
    note: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    #: Sales-asks-todo: when `state` last became done, and who did it (a name, not an id).
    done_at: Optional[datetime] = None
    done_by: Optional[str] = None
    #: Only the CRM to-do's "All agents" view names the agent.
    agent_code: Optional[str] = None


class StockAskAgentRef(BaseModel):
    code: str
    name: str


class StockAskTodoResponse(BaseModel):
    """The to-do payload both mounts read (plan 3.2)."""

    today_start: datetime
    open: list[StockAskResponse]
    done_today: list[StockAskResponse]
    truncated: bool
    #: CRM only: whose list this is; null when the caller is linked to no sales agent.
    agent: Optional[StockAskAgentRef] = None

    @field_serializer("today_start")
    def _today_start_utc(self, value: datetime) -> str:
        # Naive UTC in the database, an explicit UTC instant on the wire.
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class StockAskAgentCount(BaseModel):
    agent_id: str
    code: str
    name: str
    open: int


class StockAskMessage(BaseModel):
    """One chat line around an ask: no turn ids, no parser output, no delivery status."""

    id: int
    direction: Literal["in", "out"]
    text: str
    at: datetime


class StockAskConversationResponse(BaseModel):
    messages: list[StockAskMessage]
    ask_message_id: Optional[int] = None
    #: The Respond message id of the `ask_message_id` row, what the shared thread highlights and
    #: jumps to (ASKS-UX). None when that row has no Respond id.
    ask_message_ref: Optional[str] = None
    #: The contact's `respond_contacts.id`, only for the CRM's "Open in Conversations" link.
    contact_id: Optional[str] = None


class StockAskUpdate(BaseModel):
    """What the office (and the sales agent on the portal) edits: state and note only.
    A field left out is left alone."""

    state: Optional[Literal["open", "done"]] = None
    note: Optional[str] = Field(None, max_length=4000)
