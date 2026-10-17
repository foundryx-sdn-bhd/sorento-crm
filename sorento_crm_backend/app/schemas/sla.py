"""SLA management schemas."""
from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator, model_serializer, ConfigDict
from typing import Annotated, Optional
from datetime import datetime, timezone
from decimal import Decimal
import uuid


def _coerce_optional_message_id(v):
    """Accept int, numeric string, or null for external message ids (e.g. n8n)."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("message_id must be an integer")
    if isinstance(v, str):
        s = v.strip()
        if not s or s.lower() in ("null", "undefined", "[null]", "[undefined]"):
            return None
        try:
            return int(s)
        except ValueError as e:
            raise ValueError("message_id must be a valid integer") from e
    if isinstance(v, float):
        if not v.is_integer():
            raise ValueError("message_id must be a whole number")
        return int(v)
    if isinstance(v, int):
        return v
    raise ValueError("message_id must be an integer")


OptionalMessageId = Annotated[Optional[int], BeforeValidator(_coerce_optional_message_id)]


class SLAPolicyTierBase(BaseModel):
    policy_id: str
    tier_level: int
    tier_name: str
    response_hours: Decimal  # supports decimals, e.g. 0.5 = 30 minutes
    resolution_hours: Decimal = Decimal("24")


class SLAPolicyTierCreate(SLAPolicyTierBase):
    # Optional in the body: the route injects it from the /{policy_id}/tiers path,
    # so the frontend never has to send it. Validation must not reject its absence.
    policy_id: Optional[str] = None


class SLAPolicyTierUpdate(BaseModel):
    tier_level: Optional[int] = None
    tier_name: Optional[str] = None
    response_hours: Optional[Decimal] = None
    resolution_hours: Optional[Decimal] = None


class SLAPolicyTierResponse(SLAPolicyTierBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class SLAPolicyBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    is_active: bool = True


class SLAPolicyCreate(SLAPolicyBase):
    tiers: Optional[list[SLAPolicyTierCreate]] = None


class SLAPolicyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class SLAPolicyResponse(SLAPolicyBase):
    id: str
    created_at: datetime
    updated_at: datetime
    tiers_count: Optional[int] = 0
    tracking_count: Optional[int] = 0
    
    model_config = ConfigDict(from_attributes=True)


class ConversationSLATrackingBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    policy_id: str
    current_tier: int
    assigned_to: Optional[str] = None  # Keep for backward compatibility
    assigned_to_id: Optional[str] = None  # FK to users
    initiated_at: datetime
    current_tier_started_at: datetime
    due_at: datetime  # Response deadline
    due_at_resolution: Optional[datetime] = None  # Resolution deadline (current_tier_started_at + tier.resolution_hours)
    escalated_at: Optional[datetime] = None
    escalation_reason: Optional[str] = None
    is_responded: bool = False
    responded_at: Optional[datetime] = None
    responded_by: Optional[str] = None
    response_time: Optional[Decimal] = None
    is_resolved: bool = False
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    respond_contact_id: Optional[str] = None  # FK to respond_contacts
    resolution_duration: Optional[Decimal] = None
    agent_id: Optional[str] = None  # FK to access_agents.id
    team_set_code: Optional[str] = None
    # The brand this tracker routes within (lower-case brands.brand_code), null =
    # all brands. Echoed back so n8n can see which ladder the CRM picked.
    brand_code: Optional[str] = None
    message_id: Optional[int] = None  # External message id; kept when resolved (audit)


class ConversationSLATrackingCreate(BaseModel):
    # Ignored: the backend resolves policy from (agent_code, team_set_code). Accepted
    # only as a transition fallback during rollout, and current_tier is forced to 1.
    policy_id: Optional[str] = None
    current_tier: Optional[int] = None
    assigned_to: Optional[str] = None  # Keep for backward compatibility
    assigned_to_id: Optional[str] = None  # FK to users
    initiated_at: Optional[datetime] = None  # Auto-populated to now if not provided
    current_tier_started_at: Optional[datetime] = None  # Auto-populated to now if not provided
    # due_at is calculated from policy tier, not provided by requester
    escalated_at: Optional[datetime] = None  # Will be reset to None
    escalation_reason: Optional[str] = None  # Will be reset to None
    is_responded: bool = False
    responded_at: Optional[datetime] = None  # Will be reset to None
    responded_by: Optional[str] = None  # Will be reset to None
    response_time: Optional[Decimal] = None  # Will be reset to None
    is_resolved: bool = False  # Will be reset to False
    resolved_at: Optional[datetime] = None  # Will be reset to None
    resolved_by: Optional[str] = None  # Will be reset to None
    respond_contact_id: Optional[str] = None  # FK to respond_contacts
    resolution_duration: Optional[Decimal] = None  # Will be reset to None
    # Required: the (agent_code, team_set_code) pair is the policy-resolution key.
    # The backend owns policy selection - n8n's policy_id/current_tier are ignored.
    agent_code: str  # resolved → agent_id FK in service
    team_set_code: str  # team set; (agent_code, team_set_code) → SLA policy
    # Optional routing inputs from n8n, both already resolved on its side. Blank /
    # absent brand = unknown, which narrows nothing: the whole team round-robins. A
    # company_id that does not exist is ignored and the contact decides, because
    # routing must never break on a bad field.
    brand_code: Optional[str] = None
    company_id: Optional[str] = None
    message_id: OptionalMessageId = None
    # Identity of a conversation intervention ticket: the message that asked for a
    # human. When absent, the service falls back to message_id, then (with neither)
    # to the legacy one-open-per-contact singleton - see create_tracking.
    source_message_id: Optional[str] = None
    # The trigger message's own text (the enquiry) - stored verbatim so the
    # worklist snippet and the drawer's quoted header never re-fetch it.
    source_message_text: Optional[str] = None
    contact_phone_number: str  # Required field

    @field_validator('agent_code', 'team_set_code')
    @classmethod
    def _require_non_empty(cls, v, info):
        if v is None or not str(v).strip():
            raise ValueError(f"{info.field_name} is required and cannot be empty")
        return str(v).strip()

    @field_validator('contact_phone_number')
    @classmethod
    def validate_contact_phone_number(cls, v):
        """Validate contact_phone_number is not empty, null, or undefined."""
        if v is None:
            raise ValueError("contact_phone_number is required and cannot be null or undefined")
        v_str = str(v).strip()
        if not v_str or v_str.lower() in ['null', 'undefined', '[undefined]', '[null]']:
            raise ValueError("contact_phone_number is required and cannot be empty, null, or undefined")
        return v_str


class ConversationSLATrackingUpdate(BaseModel):
    current_tier: Optional[int] = None
    current_tier_started_at: Optional[datetime] = None
    assigned_to: Optional[str] = None
    due_at: Optional[datetime] = None
    due_at_resolution: Optional[datetime] = None
    escalated_at: Optional[datetime] = None
    escalation_reason: Optional[str] = None
    is_responded: Optional[bool] = None
    responded_at: Optional[datetime] = None
    responded_by: Optional[str] = None
    response_time: Optional[Decimal] = None
    is_resolved: Optional[bool] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_duration: Optional[Decimal] = None
    resolution_time: Optional[Decimal] = None
    agent_code: Optional[str] = None  # resolved → agent_id FK in service
    team_set_code: Optional[str] = None
    message_id: OptionalMessageId = None

    @model_validator(mode='after')
    def map_resolution_time(self):
        """Allow resolution_time as an alias for resolution_duration."""
        if self.resolution_duration is None and self.resolution_time is not None:
            self.resolution_duration = self.resolution_time
        return self


class ConversationSLATestOverrideRequest(BaseModel):
    """Gated by sla_management.conversation_sla_tracking.test_override. At least one field required in the request body.

    Set ``is_resolved``/``is_responded`` to False to reopen a resolved row for retesting;
    due_at is recomputed from the (possibly overridden) current_tier_started_at + tier hours.
    ``agent_code`` resolves to the agent_id FK; ``team_set_code`` is stored verbatim (pass
    null/empty on either to clear)."""
    assigned_to_id: Optional[str] = None
    current_tier_started_at: Optional[datetime] = None
    initiated_at: Optional[datetime] = None
    is_responded: Optional[bool] = None
    is_resolved: Optional[bool] = None
    agent_code: Optional[str] = None
    team_set_code: Optional[str] = None

    @model_validator(mode='after')
    def at_least_one_field(self):
        updatable = {
            "assigned_to_id", "current_tier_started_at", "initiated_at",
            "is_responded", "is_resolved", "agent_code", "team_set_code",
        }
        if not (self.model_fields_set & updatable):
            raise ValueError(
                "Provide at least one of: assigned_to_id (including null to unassign), "
                "current_tier_started_at, initiated_at, is_responded, is_resolved, "
                "agent_code, or team_set_code."
            )
        return self


class ConversationSLATrackingStatusUpdate(BaseModel):
    is_responded: Optional[bool] = None
    responded_at: Optional[datetime] = None
    responded_by: Optional[str] = None
    response_time: Optional[Decimal] = None
    is_resolved: Optional[bool] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_duration: Optional[Decimal] = None
    resolution_time: Optional[Decimal] = None

    @model_validator(mode='after')
    def map_resolution_time(self):
        """Allow resolution_time as an alias for resolution_duration."""
        if self.resolution_duration is None and self.resolution_time is not None:
            self.resolution_duration = self.resolution_time
        return self


class ConversationSLAEscalateRequest(BaseModel):
    """Request body for external escalation API (by respond_contact_id and policy_id).

    respond_contact_id: CRM respond_contacts.id, Respond.io id, or phone (E.164 / variants).
    """
    respond_contact_id: str
    # AC-I5: the exact ticket to escalate. When present it WINS - the server
    # escalates that row and never re-resolves by contact. GET
    # /integration/due-escalations already returns one item per row, so the
    # scheduler can pass the id straight through; without it, a contact holding
    # several open tickets gets a most-recent-open pick, which can escalate a
    # different sibling than the one that actually breached. Optional so the
    # old contact-scoped contract keeps working.
    tracking_id: Optional[str] = None
    # Optional: a contact can hold several open tickets at once (per-enquiry,
    # not one merged conversation), so the server resolves a MOST-RECENT-OPEN
    # pick for this (contact, policy) pair - see get_tracking_by_contact_and_
    # policy - and uses THAT row's policy. policy_id here is only a fallback
    # when no open row is found - legacy exact-match lookup. Ignored entirely
    # when tracking_id is given.
    policy_id: Optional[str] = None
    # Target tier after escalation (1 - 3), must be greater than the row's current tier.
    # Omit (None) for signal-only escalation: the server escalates to current tier + 1,
    # or returns escalated=false when already at tier 3.
    current_tier: Optional[int] = None
    # Omit for signal-only callers (e.g. the scheduled n8n runner, which doesn't know the
    # tier before the call): the server fills an auto-escalation reason using from_tier.
    escalation_reason: Optional[str] = None
    team_set_code: Optional[str] = None  # Optional team set key to resolve tier within a set

    @field_validator("respond_contact_id")
    @classmethod
    def validate_respond_contact_id(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError("respond_contact_id is required")
        return str(v).strip()

    @field_validator("policy_id", "tracking_id")
    @classmethod
    def validate_policy_id(cls, v):
        # Optional fallback: blank / explicit null normalizes to None. The server
        # prefers the open tracking row's own policy (agent-team-tied).
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return str(v).strip()

    @field_validator("escalation_reason")
    @classmethod
    def validate_reason(cls, v):
        # Optional: explicit null / blank normalizes to None (same as omitting the field);
        # the service fills the auto-escalation default. Validators don't run on the
        # default, so None must be accepted here too or explicit-null callers get a 422.
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return str(v).strip()


class ConversationSLAAgentRepliedRequest(BaseModel):
    """AC-I4: a staff member replied to a contact from the Respond app.

    `contact_id` accepts the same identifiers as the sibling reads: CRM
    `respond_contacts.id`, the Respond.io contact id, or the contact's phone.
    `replied_by` is the person who actually typed the reply - a Respond user id,
    a CRM `users.id`, or an email. An id that maps to nobody is NOT an error
    (see the endpoint): a Respond user with no CRM account is a real state.
    """
    contact_id: str
    replied_by: str
    replied_at: Optional[datetime] = None

    @field_validator("contact_id", "replied_by")
    @classmethod
    def validate_required(cls, v):
        if v is None or not str(v).strip():
            raise ValueError("must not be blank")
        return str(v).strip()


class ConversationSLAAgentRepliedResponse(BaseModel):
    """AC-I4 outcome. Always returned with 200, including every skip.

    `skipped_reason` is null when a ticket was stamped, otherwise "ambiguous"
    (2+ open unanswered and the replier does not own exactly one) or
    "no_open_ticket" (nothing unanswered for this contact, including the
    idempotent replay of a reply already recorded).
    """
    matched: bool = False
    tracking_id: Optional[str] = None
    skipped_reason: Optional[str] = None
    open_ticket_count: int = 0


class ConversationSLAOpenCountResponse(BaseModel):
    """AC-I2: how many OPEN conversation-scope tickets a contact holds.

    `contact_id` is the CRM `respond_contacts.id` the caller's identifier resolved
    to, or null when nothing matched. An unknown contact is `open_count: 0` at
    200, never a 404 - n8n reads this to decide whether it may tell the contact
    their conversation is closed and resolved.
    """
    contact_id: Optional[str] = None
    open_count: int = 0


class SLAPolicySimple(BaseModel):
    """Simple policy reference for tracking responses."""
    id: str
    code: str
    name: str
    
    model_config = ConfigDict(from_attributes=True)


class AgentSimple(BaseModel):
    """Simple access agent reference for tracking responses."""
    id: str
    code: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class ContactSimple(BaseModel):
    """Simple contact reference for tracking responses."""
    id: str
    phone_number: str
    name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class UserSuperiorSimple(BaseModel):
    """Superior of assigned user (for tooltip)."""
    name: Optional[str] = None
    email: Optional[str] = None


class UserSimple(BaseModel):
    """Simple user reference for tracking responses."""
    id: str
    email: Optional[str] = None  # a phone-only assignee has none (identity S0)
    name: Optional[str] = None
    superior: Optional[UserSuperiorSimple] = None  # For assignee tooltip in tracking detail

    model_config = ConfigDict(from_attributes=True)


class ConversationSLAEventLogResponse(BaseModel):
    """SLA event log response schema."""
    id: str
    sla_tracking_id: str
    event_type: str
    from_tier: Optional[int] = None
    to_tier: Optional[int] = None
    event_at: datetime
    from_time: Optional[datetime] = None  # For response/resolution events, stores initiated_at
    duration: Optional[Decimal] = None  # Duration in hours, calculated for response/resolution events
    reason: Optional[str] = None
    assigned_to: Optional[str] = None  # Keep for backward compatibility
    assigned_to_id: Optional[str] = None
    due_at: Optional[datetime] = None
    response_time: Optional[Decimal] = None
    resolution_time: Optional[Decimal] = None
    reminder_count: int = 0
    last_reminder_at: Optional[datetime] = None
    created_at: datetime
    # Related objects
    assigned_user: Optional[UserSimple] = None
    # Computed fields
    assigned_user_name: Optional[str] = None  # From assigned_user.name
    assigned_user_email: Optional[str] = None  # From assigned_user.email
    
    model_config = ConfigDict(from_attributes=True)
    
    @model_serializer(mode='wrap', when_used='json')
    def serialize_model(self, serializer, info):
        """Custom serialization to convert timezone-aware datetimes to naive UTC strings."""
        data = serializer(self)
        # Convert timezone-aware datetimes to naive UTC
        datetime_fields = ['event_at', 'from_time', 'due_at', 'last_reminder_at', 'created_at']
        for field in datetime_fields:
            if field in data and data[field] is not None:
                value = getattr(self, field)
                if isinstance(value, datetime) and value.tzinfo is not None:
                    # Convert to UTC and remove timezone for naive representation
                    data[field] = value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        return data
    
    @field_validator('sla_tracking_id', mode='before')
    @classmethod
    def convert_uuid(cls, v):
        """Convert UUID objects to strings."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    @field_validator('assigned_to', mode='before')
    @classmethod
    def convert_text_field(cls, v):
        """Convert UUID objects to strings for text fields."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    @model_validator(mode='after')
    def populate_computed_fields(self):
        """Populate computed fields from relationships."""
        # Populate assigned_user_name and assigned_user_email from assigned_user relationship
        if self.assigned_user:
            self.assigned_user_name = self.assigned_user.name
            self.assigned_user_email = self.assigned_user.email
        else:
            # If no assigned_user relationship, set to None
            self.assigned_user_name = None
            self.assigned_user_email = None
        
        return self


class ConversationSLAEventLogCreate(BaseModel):
    sla_tracking_id: str
    event_type: str
    from_tier: Optional[int] = None
    to_tier: Optional[int] = None
    event_at: Optional[datetime] = None
    from_time: Optional[datetime] = None  # For response/resolution events, stores initiated_at
    duration: Optional[Decimal] = None  # Duration in hours, calculated for response/resolution events
    reason: Optional[str] = None
    assigned_to: Optional[str] = None  # Keep for backward compatibility
    assigned_to_id: Optional[str] = None  # FK to users
    # Prior-tier owner snapshotted at escalation time (before assigned_to_id overwrite).
    from_assigned_to_id: Optional[str] = None  # FK to users (escalated-FROM)
    due_at: Optional[datetime] = None
    response_time: Optional[Decimal] = None
    resolution_time: Optional[Decimal] = None
    trigger: Optional[str] = None  # 'auto' | 'manual' (escalation events)
    triggered_by_id: Optional[str] = None  # human who manually escalated


class ConversationSLATrackingResponse(ConversationSLATrackingBase):
    id: str
    created_at: datetime
    updated_at: datetime
    synced_to_excel: bool = False
    last_synced_to_excel: Optional[datetime] = None
    policy: Optional[SLAPolicySimple] = None
    event_logs: Optional[list[ConversationSLAEventLogResponse]] = []
    # Foreign key fields
    respond_contact_id: Optional[str] = None
    respond_io_id: Optional[str] = None  # Respond.io contact id for inbox URL
    assigned_to_id: Optional[str] = None
    # Related objects
    contact: Optional[ContactSimple] = None
    assigned_user: Optional[UserSimple] = None
    agent: Optional[AgentSimple] = None  # FK to access_agents
    # Renamed field for API consumers
    response_duration: Optional[Decimal] = None
    # Computed fields for backward compatibility
    agent_code: Optional[str] = None  # Resolved from agent.code FK
    contact_phone: Optional[str] = None  # From contact.phone_number or respond_contact_phone
    contact_name: Optional[str] = None  # From contact.name or respond_contact_name
    assigned_user_name: Optional[str] = None  # From assigned_user.name
    assigned_user_email: Optional[str] = None  # From assigned_user.email
    # Bare wa.me digits of the current assignee, for the extension banner link.
    assigned_user_wa_phone: Optional[str] = None
    assigned_user_superior_name: Optional[str] = None  # From assigned_user.superior (for tooltip)
    assigned_user_superior_email: Optional[str] = None  # From assigned_user.superior (for tooltip)
    # Escalated-FROM owner (latest escalation event's from_assigned_to_id) for the
    # escalation banner: WHO missed at the prior tier + their wa.me digits.
    escalated_from_name: Optional[str] = None
    escalated_from_wa_phone: Optional[str] = None
    responded_by_user_name: Optional[str] = None  # Looked up from responded_by user ID
    resolved_by_user_name: Optional[str] = None  # Looked up from resolved_by user ID
    # Average times calculated from event logs
    average_response_time: Optional[Decimal] = None  # Average duration from event logs with event_type="response"
    average_resolution_time: Optional[Decimal] = None  # Average duration from event logs with event_type="resolution"
    # Time-in-tier and time-remaining (computed; response timers stop when is_responded, resolution when is_resolved)
    time_in_tier_response_seconds: Optional[float] = None
    time_remaining_response_seconds: Optional[float] = None
    time_in_tier_resolution_seconds: Optional[float] = None
    time_remaining_resolution_seconds: Optional[float] = None
    resolution_due_at: Optional[datetime] = None  # current_tier_started_at + tier.resolution_hours
    # Tier KPI hours (for frontend to color response_time / resolution_duration)
    tier_response_hours: Optional[Decimal] = None
    tier_resolution_hours: Optional[Decimal] = None
    # Idempotent-update indicators: true when the caller tried to resolve an
    # already-resolved tracking. The body is still returned so the caller can
    # branch its own routing without parsing a 4xx error envelope.
    already_resolved: Optional[bool] = False
    updated_in_request: Optional[bool] = True
    # AC-I3: true when the caller set is_responded on a tracking that was already
    # responded. The responded-family fields are dropped (clocks untouched) and the
    # call still answers 200 - respond is idempotent exactly like resolve.
    already_responded: Optional[bool] = False
    # AC-E3: true when a PUT is_responded=true from the n8n Respond-app-reply
    # fallback was skipped because the resolved assignee holds 2+ open tickets
    # for this contact (ambiguous which enquiry they answered) - see
    # ConversationSLATrackingService.is_ambiguous_fallback_response.
    ambiguous_responded_skipped: Optional[bool] = False
    # Idempotent-create indicator: true when create found an open conversation
    # tracking for the contact and returned it (message_id refreshed) instead of
    # creating a new row. n8n branches on this to skip new-conversation steps.
    already_active: Optional[bool] = False
    # Form-SLA fields (populated for form-scoped listings): the originating entity
    # and a human-readable reference / next action so the Form SLA list mirrors the
    # conversation list without exposing UUIDs.
    source_entity_type: Optional[str] = None
    source_entity_id: Optional[str] = None
    reference: Optional[str] = None  # entity number (complaint/inquiry/request/ticket)
    next_action: Optional[str] = None  # stage-derived next action label

    @model_serializer(mode='wrap', when_used='json')
    def serialize_model(self, serializer, info):
        """Custom serialization to convert timezone-aware datetimes to naive UTC strings."""
        data = serializer(self)
        # Convert timezone-aware datetimes to naive UTC
        datetime_fields = [
            'initiated_at', 'current_tier_started_at', 'due_at', 'due_at_resolution', 'escalated_at',
            'responded_at', 'resolved_at', 'created_at', 'updated_at', 'last_synced_to_excel',
            'resolution_due_at'
        ]
        for field in datetime_fields:
            if field in data and data[field] is not None:
                value = getattr(self, field)
                if isinstance(value, datetime) and value.tzinfo is not None:
                    # Convert to UTC and remove timezone for naive representation
                    data[field] = value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        return data
    
    @field_validator('policy_id', mode='before')
    @classmethod
    def convert_policy_id_uuid(cls, v):
        """Convert UUID objects to strings for policy_id."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    @field_validator('assigned_to', 'resolved_by', mode='before')
    @classmethod
    def convert_text_fields(cls, v):
        """Convert UUID objects to strings for text fields."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    @model_validator(mode='after')
    def populate_computed_fields(self):
        """Populate computed fields from relationships."""
        if self.response_duration is None and self.response_time is not None:
            self.response_duration = self.response_time

        # Populate contact_phone and contact_name from contact relationship
        if self.contact:
            self.contact_phone = self.contact.phone_number
            self.contact_name = self.contact.name
        else:
            self.contact_phone = None
            self.contact_name = None

        # Populate assigned_user_name and assigned_user_email from assigned_user relationship
        if self.assigned_user:
            self.assigned_user_name = self.assigned_user.name
            self.assigned_user_email = self.assigned_user.email
        else:
            self.assigned_user_name = None
            self.assigned_user_email = None

        # Populate agent_code from agent FK relationship
        if self.agent and self.agent_code is None:
            self.agent_code = self.agent.code

        return self


# Must stay in lockstep with form_sla_service.FORM_SLA_TYPES; a type in one but not
# the other is a live bug (see tests/test_form_sla_types_consistency.py).
_FORM_SLA_TYPES = (
    "stock_inquiry",
    "purchase_request",
    "sponsorship_form",
    "complaint",
    "ticket",
    "workflow_submission",
    "price_tag_request",
    "sample_request",
)


class FormSLAConfigBase(BaseModel):
    source_entity_type: str
    stage_code: str
    policy_id: str
    agent_code: str
    team_set_code: Optional[str] = None
    start_event: str
    respond_event: Optional[str] = None
    resolve_event: Optional[str] = None
    next_config_id: Optional[str] = None
    advance_on_event: Optional[str] = None
    # Seconds an in-app action on this stage waits before it applies, so the actor can
    # take it back before anyone is told. NULL = use the global default (which is 0).
    grace_seconds: Optional[int] = Field(default=None, ge=0, le=600)
    is_active: bool = True
    notify_assignee: bool = True
    notify_on_escalation: bool = True

    @field_validator("source_entity_type")
    @classmethod
    def _check_source_entity_type(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in _FORM_SLA_TYPES:
            raise ValueError(
                f"source_entity_type must be one of {_FORM_SLA_TYPES}; got {v!r}"
            )
        return v

    @field_validator("stage_code", "agent_code", "start_event")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must be a non-empty string")
        return v


class FormSLAConfigCreate(FormSLAConfigBase):
    pass


class FormSLAConfigUpdate(BaseModel):
    source_entity_type: Optional[str] = None
    stage_code: Optional[str] = None
    policy_id: Optional[str] = None
    agent_code: Optional[str] = None
    team_set_code: Optional[str] = None
    start_event: Optional[str] = None
    respond_event: Optional[str] = None
    resolve_event: Optional[str] = None
    next_config_id: Optional[str] = None
    advance_on_event: Optional[str] = None
    grace_seconds: Optional[int] = Field(default=None, ge=0, le=600)
    is_active: Optional[bool] = None
    notify_assignee: Optional[bool] = None
    notify_on_escalation: Optional[bool] = None


class FormSLAConfigResponse(FormSLAConfigBase):
    id: str
    created_at: datetime
    updated_at: datetime
    policy_code: Optional[str] = None
    policy_name: Optional[str] = None
    next_stage_code: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
