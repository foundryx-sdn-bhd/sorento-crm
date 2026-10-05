"""Request/response shapes for the chatbot access model admin API (ACCESS-MODEL S4)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

AudienceTier = Literal["dealer", "office", "end_user"]


class RegistryField(BaseModel):
    key: str
    label: str
    kind: str


class RegistryDomain(BaseModel):
    name: str
    label: str
    supported: bool
    access_section: Optional[str] = None
    group: Optional[str] = None
    access_label: str
    access_description: Optional[str] = None
    escalation_agent_code: Optional[str] = None
    escalation_team_code: Optional[str] = None
    escalation_tier1_team: Optional[str] = None
    fields: list[RegistryField]


class RegistryResponse(BaseModel):
    domains: list[RegistryDomain]


class RoleResponse(BaseModel):
    id: str
    code: str
    name: str
    description: Optional[str] = None
    sees_all_customers: bool
    audience_tier: Optional[str] = None
    domains: list[str]
    fields: list[str]
    contact_count: int


class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sees_all_customers: bool = False
    audience_tier: Optional[AudienceTier] = None


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sees_all_customers: Optional[bool] = None
    audience_tier: Optional[AudienceTier] = None
    clear_audience_tier: bool = False


class RoleGrantsUpdate(BaseModel):
    domains: list[str]
    fields: list[str]


class RoleContact(BaseModel):
    id: str
    name: Optional[str] = None


class ContactRole(BaseModel):
    id: str
    code: str
    name: str


class OverrideItem(BaseModel):
    domain_name: str
    field_key: Optional[str] = None
    granted: bool


class EffectiveAccessResponse(BaseModel):
    domains: list[str]
    attributes: list[str]
    sees_all_customers: bool
    tiers: list[str]


class ContactAccessResponse(BaseModel):
    roles: list[ContactRole]
    overrides: list[OverrideItem]
    effective: EffectiveAccessResponse


class ContactAccessUpdate(BaseModel):
    role_ids: list[str]
    overrides: list[OverrideItem]
