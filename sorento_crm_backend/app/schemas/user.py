"""User management schemas."""
from pydantic import BaseModel, ConfigDict, field_validator
from typing import Literal, Optional, List, Any
from datetime import datetime


# Respond Contact Schemas
class RespondContactAccessTypeRef(BaseModel):
    code: str
    name: str
    sort_order: Optional[int] = None


class RespondContactBase(BaseModel):
    phone_number: str
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    respond_io_id: Optional[str] = None  # Respond.io contact id for inbox URL
    workspace_id: Optional[str] = None  # FK to respond_workspaces.id
    # AC-F4: when true, this contact's sponsorship form demands a registered project.
    # Per contact so the requirement can be rolled out one team at a time.
    requires_registered_project: bool = False


class RespondContactCreate(RespondContactBase):
    access_type_codes: Optional[List[str]] = None  # Many-to-many to contact_access_types


class RespondContactUpdate(BaseModel):
    phone_number: Optional[str] = None
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    respond_io_id: Optional[str] = None
    workspace_id: Optional[str] = None
    requires_registered_project: Optional[bool] = None
    access_type_codes: Optional[List[str]] = None  # When set, replaces the contact's M2M assignment
    # Chatbot turn re-architecture (AC-1503): the Profile shelf, written by the
    # Contact > Access "Chatbot" card (AC-1515). `chatbot_recall_enabled` (the recall
    # toggle) is DROPPED, model and DB both (round 3, AC-MEM054).
    chatbot_profile: Optional[dict] = None


class RespondContactResponse(RespondContactBase):
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    respond_io_id: Optional[str] = None
    access_type_codes: List[str] = []
    access_types: List[RespondContactAccessTypeRef] = []
    # Linked customer codes, sorted; filled by the list endpoint (one query per page).
    customer_codes: List[str] = []
    workspace_name: Optional[str] = None
    workspace_space_id: Optional[str] = None
    # Read-only mirror of respond_contacts.outbound_enabled so the contacts grid
    # can show, and flip, who may receive a WhatsApp message. Writes go through
    # POST /api/v1/system/respond-contacts/{id}/outbound, never through a contact
    # update, so this is deliberately absent from RespondContactUpdate.
    outbound_enabled: bool = True
    # Chatbot turn re-architecture (AC-1503). `chatbot_recall_enabled` is DROPPED
    # (round 3, AC-MEM054) - no longer part of this response.
    chatbot_profile: dict = {}
    # Chatbot memory lane A (contract section 5): the contact's own context level,
    # null = follow the system default.
    chatbot_memory_level: Optional[str] = None
    # S6: stock allowance on the contact row, default on.
    chatbot_stock_allowed: bool = True
    # Chatbot stock ask v2 S2 (PLAN-chatbot-stock-ask-v2-24sep.md, R7): both default off.
    notify_salesman: bool = False
    packing_list_allowed: bool = False
    # Issue #1328: the ETA this contact is told carries the +x days offset. Default on.
    chatbot_eta_offset_applied: bool = True
    # ESCALATION-CONTROL: may the chatbot hand this contact to a person. Default on.
    escalation_allowed: bool = True
    # CONTACT-BULK-ACCESS (UAC A2.1): `chatbot_profile.tier` flattened, and whether the
    # contact holds the `purchase_orders.cost` reveal (filled by the list only; null on detail).
    chatbot_tier: Optional[str] = None
    cost_visible: Optional[bool] = None
    # Identity S3 1.7. List rows: linked_user_id/name only (one batched query per
    # page - see `ContactService.list_contacts`). Detail additionally carries
    # is_salesperson/suggested_role_slug and the full linked_user. Both the id/name
    # pair and linked_user are null without `user_management.users.view`.
    linked_user_id: Optional[str] = None
    linked_user_name: Optional[str] = None
    is_salesperson: bool = False
    suggested_role_slug: str = "portal_user"
    linked_user: Optional[dict] = None

    class Config:
        from_attributes = True


class UserRoleBase(BaseModel):
    slug: str
    name: str
    description: Optional[str] = None
    is_protected: bool = False
    is_default: bool = False


class UserRoleCreate(UserRoleBase):
    permissions: Optional[list[str]] = None  # Permission IDs


class UserRoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_protected: Optional[bool] = None
    is_default: Optional[bool] = None
    permissions: Optional[list[str]] = None  # Permission IDs; when set, replaces role's permissions


class UserRoleSimple(BaseModel):
    id: str
    name: str
    
    class Config:
        from_attributes = True


class UserSelectResponse(BaseModel):
    id: str
    name: Optional[str] = None
    email: Optional[str] = None  # a phone-only user has none (identity S0)
    respond_user_id: Optional[str] = None
    respond_synced: Optional[str] = None

    class Config:
        from_attributes = True


class UserLookupItem(BaseModel):
    """People picker row for any signed-in user (never-stuck L10, owner ruling 1 Oct 2026).

    Id and name only. ``respond_user_id`` is present only when the caller asked for
    Respond.io-synced users (the SLA and complaint assignee filters key on it); the route
    drops it otherwise. No email, phone or account state: those stay behind
    ``user_management.users.view`` on ``/users/select``.
    """
    id: str
    name: Optional[str] = None
    respond_user_id: Optional[str] = None

    class Config:
        from_attributes = True


class UserRolePermissionRef(BaseModel):
    """Minimal permission ref for role response."""
    id: str
    slug: str

    class Config:
        from_attributes = True


class UserRoleResponse(UserRoleBase):
    id: str
    created_at: datetime
    permissions: Optional[List[UserRolePermissionRef]] = None

    class Config:
        from_attributes = True

    @field_validator("permissions", mode="before")
    @classmethod
    def permissions_from_orm(cls, v: Any) -> Optional[List[dict]]:
        if v is None:
            return None
        if isinstance(v, list):
            out = []
            for p in v:
                if hasattr(p, "permission") and p.permission:
                    out.append({"id": p.permission.id, "slug": p.permission.slug})
                elif isinstance(p, dict) and "id" in p and "slug" in p:
                    out.append(p)
            return out if out else None
        return None


class UserPermissionBase(BaseModel):
    slug: str
    name: str
    description: Optional[str] = None


class UserPermissionCreate(UserPermissionBase):
    pass


class UserPermissionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class UserPermissionResponse(UserPermissionBase):
    id: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class ProductDiscontinuedScopeIn(BaseModel):
    """One (company, brand) slice on a user's product-discontinued subscription.

    ``company_id`` null = every company (which forces ``brand_id`` null);
    ``brand_id`` null = every brand in that company.
    """

    company_id: Optional[str] = None
    brand_id: Optional[str] = None


class ProductDiscontinuedScopeOut(ProductDiscontinuedScopeIn):
    """A saved scope, resolved to names so the UI never renders a UUID."""

    id: str
    company_name: Optional[str] = None
    brand_code: Optional[str] = None
    brand_name: Optional[str] = None


# The four values `users.notify_push_message_scope` may hold (PLAN-message-push).
# Declared as a Literal so an unknown value is a 422 from FastAPI's validation
# handler rather than a column write nobody notices (UAC AC-M25).
MessagePushScope = Literal["assigned_and_coverage", "assigned_only", "all_contacts", "off"]
DEFAULT_MESSAGE_PUSH_SCOPE: MessagePushScope = "assigned_and_coverage"


class UserBase(BaseModel):
    # Optional since S3 (Q5): a user needs at least one of email or phone, not
    # necessarily both. `create_user` raises `EMAIL_OR_PHONE_REQUIRED` when
    # neither is present after any linked contact's phone is applied.
    email: Optional[str] = None
    name: Optional[str] = None
    contact_number: Optional[str] = None
    status: str = "INACTIVE"
    country: Optional[str] = None
    timezone: Optional[str] = None
    respond_user_id: Optional[str] = None
    respond_synced: Optional[str] = "pending"
    superior_id: Optional[str] = None
    tier: Optional[int] = None  # Conversation SLA policy tier (1, 2, ...)
    daily_sla_summary_subscribed: Optional[bool] = True  # email summary opt-in
    respond_contact_id: Optional[str] = None  # linked WhatsApp contact
    notify_whatsapp: Optional[bool] = False  # legacy; superseded by per-event toggles
    notify_whatsapp_summary: Optional[bool] = False  # daily summary template
    notify_email_on_assignment: Optional[bool] = True
    notify_email_on_escalation: Optional[bool] = True
    notify_whatsapp_on_assignment: Optional[bool] = False
    notify_whatsapp_on_escalation: Optional[bool] = False
    notify_email_on_product_discontinued: Optional[bool] = False
    notify_whatsapp_on_product_discontinued: Optional[bool] = False
    notify_email_on_deadline_extended: Optional[bool] = True
    notify_whatsapp_on_deadline_extended: Optional[bool] = False
    notify_email_on_handling: Optional[bool] = True
    notify_whatsapp_on_handling: Optional[bool] = False
    notify_email_on_mention: Optional[bool] = True
    notify_push_message_scope: MessagePushScope = DEFAULT_MESSAGE_PUSH_SCOPE

    @field_validator("contact_number", mode="before")
    @classmethod
    def _normalize_contact_number_base(cls, v: Any) -> Optional[str]:
        from app.services.phone_utils import normalize_msisdn
        if v is None:
            return None
        return normalize_msisdn(v)


class UserCreate(UserBase):
    role_ids: Optional[list[str]] = None  # If omitted, default role is assigned
    company_ids: Optional[list[str]] = None  # Superadmin-only: user_companies grants


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None
    contact_number: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None
    avatar: Optional[str] = None
    respond_user_id: Optional[str] = None
    respond_synced: Optional[str] = None
    superior_id: Optional[str] = None
    tier: Optional[int] = None
    respond_contact_id: Optional[str] = None
    notify_whatsapp: Optional[bool] = None
    notify_whatsapp_summary: Optional[bool] = None
    daily_sla_summary_subscribed: Optional[bool] = None
    notify_email_on_assignment: Optional[bool] = None
    notify_email_on_escalation: Optional[bool] = None
    notify_whatsapp_on_assignment: Optional[bool] = None
    notify_whatsapp_on_escalation: Optional[bool] = None
    notify_email_on_product_discontinued: Optional[bool] = None
    notify_whatsapp_on_product_discontinued: Optional[bool] = None
    notify_email_on_deadline_extended: Optional[bool] = None
    notify_whatsapp_on_deadline_extended: Optional[bool] = None
    notify_email_on_handling: Optional[bool] = None
    notify_whatsapp_on_handling: Optional[bool] = None
    notify_email_on_mention: Optional[bool] = None
    notify_push_message_scope: Optional[MessagePushScope] = None
    # Replace-all when provided (an empty list clears every scope, i.e. the user
    # hears nothing); untouched when omitted.
    product_discontinued_scopes: Optional[List[ProductDiscontinuedScopeIn]] = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_optional_email(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s.lower() if s else None

    @field_validator("contact_number", mode="before")
    @classmethod
    def normalize_optional_contact_number(cls, v: Any) -> Optional[str]:
        from app.services.phone_utils import normalize_msisdn
        if v is None:
            return None
        return normalize_msisdn(v)


class UserResponse(UserBase):
    id: str
    # Optional on the way out (identity S0): one phone-only user must not break the
    # Users list. Create input still requires an email until S3.
    email: Optional[str] = None
    avatar: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_sign_in_at: Optional[datetime] = None
    email_verified_at: Optional[datetime] = None
    is_trashed: Optional[bool] = False
    is_protected: Optional[bool] = False
    # identity S1 (#1280), plan 5.3: whether the user has a password set (a
    # phone-only user may not). Populated by every manual UserResponse builder
    # (see users.py) - `response_model` silently drops an ORM-only field that
    # isn't declared here. `phone_verified_at` is declared once, with the S3
    # Sign-in fields below.
    has_password: Optional[bool] = None
    roles: Optional[List[UserRoleSimple]] = None  # Assigned roles from user_role_assignments
    superior_name: Optional[str] = None  # Superior's name for display
    # Which slice of the catalogue the product-discontinued notice reports to this
    # user. Populated by every manual UserResponse builder (see users.py) - the
    # field is not an ORM attribute, so inheritance alone would never fill it.
    product_discontinued_scopes: List[ProductDiscontinuedScopeOut] = []
    # The Sign-in section's five fields (S3 1.7). Populated by
    # `app.services.user_contact_link.sign_in_summary` in BOTH manual dict
    # builders (`get_user`, `get_current_user_profile`) - like the scopes above,
    # these are not ORM attributes, so a builder that forgets one silently drops it.
    phone_verified_at: Optional[datetime] = None
    linked_contact: Optional[dict] = None
    phone_differs_from_contact: bool = False
    last_sign_in_method: Optional[str] = None
    needs_invitation: bool = False

    class Config:
        from_attributes = True


class AccessAgentBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    is_active: bool = True
    assign_to_new_internal_contacts: bool = False


class AccessAgentCreate(AccessAgentBase):
    pass


class AccessAgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    assign_to_new_internal_contacts: Optional[bool] = None


class AccessAgentResponse(AccessAgentBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ContactAgentAccessBase(BaseModel):
    respond_contact_id: Optional[str] = None  # FK to respond_contacts
    respond_contact_phone: str  # Keep for backward compatibility
    respond_contact_name: Optional[str] = None  # Keep for backward compatibility
    agent_id: str
    is_allowed: bool = True
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class ContactAgentAccessCreate(ContactAgentAccessBase):
    pass


class ContactAgentAccessUpdate(BaseModel):
    respond_contact_id: Optional[str] = None
    respond_contact_phone: Optional[str] = None
    respond_contact_name: Optional[str] = None
    is_allowed: Optional[bool] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class ContactAgentAccessResponse(ContactAgentAccessBase):
    id: str
    created_at: datetime
    created_by: Optional[str] = None
    synced_to_excel: bool
    last_synced_to_excel: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    agent_code: Optional[str] = None  # Added for frontend display
    agent_name: Optional[str] = None  # Added for frontend display
    # The CONTACT's outbound kill switch (respond_contacts.outbound_enabled), not
    # the grant's. Every grant row for the same contact reports the same value, so
    # the grid can show it per row and de-duplicate by respond_contact_id before
    # writing. None = the row is not linked to a respond_contacts row (legacy,
    # phone-only), which is "unknown", never "reachable".
    outbound_enabled: Optional[bool] = None

    class Config:
        from_attributes = True


class RespondContactLookupRequest(BaseModel):
    identifier: str


class RespondContactLookupResponse(BaseModel):
    contact_name: Optional[str] = None


# Team schemas (for round-robin assignee)
class TeamBase(BaseModel):
    name: str
    description: Optional[str] = None
    parent_team_id: Optional[str] = None


class TeamCreate(TeamBase):
    pass


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_team_id: Optional[str] = None


class TeamMemberPreview(BaseModel):
    """Lightweight member identity for the teams list (human-readable, no UUID leak)."""
    user_id: str
    name: str


class TeamResponse(TeamBase):
    id: str
    created_at: datetime
    member_count: int = 0
    members: list[TeamMemberPreview] = []
    class Config:
        from_attributes = True


class TeamMemberResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    sort_order: Optional[int] = None
    include_in_round_robin: bool = True
    created_at: datetime
    class Config:
        from_attributes = True


class BrandCodesUpdate(BaseModel):
    """Replace a team member's brand tags with this exact set (empty = serves all)."""
    codes: list[str] = []


class AgentTeamAssignment(BaseModel):
    code: str
    team_id: str
    tier: Optional[int] = None  # 1=initial, 2/3=escalation; one team per tier per agent
    # Conversation SLA policy for this team set. One policy per (agent, code); the
    # service casts it onto every tier row of the set. None = unbound.
    policy_id: Optional[str] = None
    # Whether this tier's team is notified when a lower-tier SLA deadline is extended.
    # Default true (the grandparent tier is reached out of the box). Per-tier control.
    notify_on_extension: bool = True


class AgentTeamsUpdate(BaseModel):
    """Legacy: team_ids for backwards compat. Use assignments instead."""
    team_ids: list[str] | None = None
    assignments: list[AgentTeamAssignment] | None = None


class AgentFieldAccessEntry(BaseModel):
    """One tick on the "which fields may this agent reveal" checklist."""

    resource: str
    field_key: str
    #: `null` with a `contact_id` REMOVES the override, so that contact goes back
    #: to following the agent. Needed because "explicitly denied" and "inherits a
    #: denial" are different intentions and an admin must be able to undo the
    #: first without pretending it was the second.
    is_allowed: Optional[bool] = True
    #: Set to write a per-contact exception instead of the agent-wide default.
    #: Everyone holding the agent follows the default unless overridden here.
    contact_id: Optional[str] = None


class AgentFieldAccessUpdate(BaseModel):
    fields: list[AgentFieldAccessEntry] | None = None


# Contact access type catalog (admin CRUD).
#
# PLAN-portal-forms-market-segment D1: `portal_form_types` moved to
# `MarketSegment` (see `app/schemas/market_segment.py`). These schemas
# deliberately carry no such field any more - a stale caller's payload key is
# silently ignored (pydantic's default extra="ignore"), never persisted, and
# never echoed back.
class ContactAccessTypeBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    is_active: bool = True
    sort_order: Optional[int] = None
    # Admin-curated synonyms for fuzzy resolution (e.g. ["customer","homeowner"] → end_user).
    keywords: List[str] = []


class ContactAccessTypeCreate(ContactAccessTypeBase):
    pass


class ContactAccessTypeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    keywords: Optional[List[str]] = None


class ContactAccessTypeResponse(ContactAccessTypeBase):
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# MCP tool catalog (Phase 2 - AccessAgent ↔ Tool ownership)
# ---------------------------------------------------------------------------

class McpToolOut(BaseModel):
    """One row of the MCP tool catalog. Pure catalog - tools carry no agent
    ownership; contact access is enforced per-agent (see
    ``app.services.mcp_access_service``)."""
    id: str
    tool_name: str
    description: str | None = None
    module_key: str = ""

    model_config = ConfigDict(from_attributes=True)
