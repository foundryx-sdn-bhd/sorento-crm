"""Schemas for the contact <-> customer link routes (PLAN-contact-customers-29sep D2)."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.order import CustomerResponse


class ContactCustomerLinkResponse(BaseModel):
    # The link row's own id: what the unlink pending action parks on. Never shown.
    id: str
    customer_id: str
    customer_code: str
    customer_name: str
    is_active: bool
    source: str
    sales_agent_id: Optional[str] = None
    sales_agent_code: Optional[str] = None
    sales_agent_name: Optional[str] = None
    # The customer's company: a contact page reads every granted company, so each row says which.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    created_at: datetime


class ContactCustomersResponse(BaseModel):
    data: list[ContactCustomerLinkResponse]


class ContactCustomerLinkCreate(BaseModel):
    # `extra="forbid"`: there is no primary customer in this lane, and the singular
    # `customer_id` is gone, so an old body is a 422 rather than a silently ignored field.
    model_config = ConfigDict(extra="forbid")

    customer_ids: list[str] = Field(min_length=1)


class ContactCustomerLinksResponse(BaseModel):
    data: list[ContactCustomerLinkResponse]


class CustomerLinkedContactResponse(BaseModel):
    # The link row id, then the contact it points at.
    id: str
    contact_id: str
    name: Optional[str] = None
    phone_number: Optional[str] = None
    created_at: datetime


class CustomerLinkedContactsResponse(BaseModel):
    data: list[CustomerLinkedContactResponse]


class AgentCustomerAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_ids: list[str] = Field(min_length=1)


class AgentCustomersAssignedResponse(BaseModel):
    data: list[CustomerResponse]
