"""Schemas for the Respond.io contact outbound switch screen.

The screen is System Management -> Respond.io Contacts; the column it drives is
`respond_contacts.outbound_enabled` (see `app.services.respond_outbound_service`).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import ListResponse

# A single bulk request is bounded: the UI selects at most one page of rows, and
# "everything" has its own explicit flag rather than a 100k-element list.
MAX_BULK_CONTACT_IDS = 1000


class RespondContactOutboundRow(BaseModel):
    """One contact, as the outbound screen shows it.

    `id` is the mutation target and the grid row key only - the UI never renders
    it (no UUIDs on screen).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: Optional[str] = None
    phone_number: Optional[str] = None
    respond_io_id: Optional[str] = None
    outbound_enabled: bool
    # AI-ALLOWED-GATE: may the chatbot answer this contact at all.
    chatbot_allowed: bool


class RespondOutboundCounts(BaseModel):
    """How many contacts we can currently message, and how many we cannot."""

    enabled: int
    disabled: int
    total: int


class RespondContactOutboundListResponse(ListResponse[RespondContactOutboundRow]):
    """The list, plus the whole-table audit line above it.

    `counts` deliberately ignores the search/filter: it answers "how much of our
    customer base is silenced right now", which a filtered count cannot.
    """

    counts: RespondOutboundCounts


class RespondContactOutboundUpdate(BaseModel):
    """Flip one contact."""

    enabled: bool


class RespondContactOutboundBulkRequest(BaseModel):
    """Flip a selection, or every contact there is.

    `contact_ids` and `all` are mutually exclusive and one of them is required.
    Guessing which the caller meant is not an option when the wrong guess
    silences an entire customer base.
    """

    enabled: bool
    contact_ids: Optional[List[str]] = Field(
        default=None, min_length=1, max_length=MAX_BULK_CONTACT_IDS
    )
    all: bool = False

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "RespondContactOutboundBulkRequest":
        if self.all and self.contact_ids:
            raise ValueError("Pass either contact_ids or all=true, not both.")
        if not self.all and not self.contact_ids:
            raise ValueError("Pass contact_ids, or all=true to change every contact.")
        return self


class RespondContactOutboundBulkResponse(BaseModel):
    """`requested` is what we were asked to set; `changed` is what actually moved."""

    requested: int
    changed: int
    counts: RespondOutboundCounts
