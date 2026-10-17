"""Project Sales schemas.

No UUID is ever the user-facing identifier of a project: ``project_code`` is, and
every response that names a project carries it. Party and owner FKs are echoed back
with resolved names for the same reason (the FE cursor rule bans UUIDs in the UI).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.services.uuid_path_param import UUID_PATTERN

# A pure, dependency-free lookup table (no DB, no network), so a schema importing it introduces
# no cycle. It lives in services because the PDF renderer needs the same answer.
from app.services import geo_places

# --------------------------------------------------------------------- parties


class ProjectPartyBase(BaseModel):
    party_type: str = Field(
        ...,
        description=(
            "developer | architect | main_contractor | trading_house | consultant"
        ),
    )
    name: str = Field(..., min_length=1, max_length=255)
    registration_no: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=150)
    notes: Optional[str] = None
    customer_id: Optional[str] = Field(
        None,
        description=(
            "Set only once this party actually issues a purchase order. Architects "
            "never buy, which is why parties are not customers."
        ),
    )
    is_active: bool = True


class ProjectPartyCreate(ProjectPartyBase):
    pass


class ProjectPartyUpdate(BaseModel):
    party_type: Optional[str] = None
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    registration_no: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=150)
    notes: Optional[str] = None
    customer_id: Optional[str] = None
    is_active: Optional[bool] = None


class ProjectPartyResponse(ProjectPartyBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_name: Optional[str] = None
    project_count: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ------------------------------------------------------------ types, templates


class ProjectTypeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    code: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = None
    derives_delivery_from_launch: bool = Field(
        False,
        description=(
            "Property developments infer delivery from launch date plus a configurable "
            "lag. Every other type states an explicit delivery window instead."
        ),
    )
    sort_order: int = 0
    is_active: bool = True


class ProjectTypeCreate(ProjectTypeBase):
    pass


class ProjectTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    code: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = None
    derives_delivery_from_launch: Optional[bool] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class ProjectTypeResponse(ProjectTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    template_count: Optional[int] = None


class ProjectTemplateRoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    sort_order: int
    is_active: bool


class ProjectTemplateBase(BaseModel):
    type_id: str
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    is_active: bool = True


class ProjectTemplateCreate(ProjectTemplateBase):
    role_names: List[str] = Field(
        default_factory=list,
        description=(
            "Stakeholder roles this template offers. Seeded default: Decision Maker, "
            "Influencer, Info Provider, Architect."
        ),
    )


class ProjectTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    role_names: Optional[List[str]] = None


class ProjectTemplateResponse(ProjectTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type_name: Optional[str] = None
    roles: List[ProjectTemplateRoleResponse] = Field(default_factory=list)
    has_forked_status_graph: bool = False


# ------------------------------------------------------------------- clashes


class ClashCandidateResponse(BaseModel):
    """One possible duplicate, with everything the user needs to decide.

    AC-C6a: the incumbent is rendered, not merely named. Owner, status and last
    activity are what let someone tell "that is my colleague's live tender" from
    "that is a different phase with a similar name".
    """

    project_id: str
    project_code: str
    title: str
    outcome: str
    status_label: Optional[str] = None
    owner_user_id: Optional[str] = None
    owner_name: Optional[str] = None
    developer_name: Optional[str] = None
    estimated_sales_value: Optional[Decimal] = None
    brands: List[str] = Field(default_factory=list)
    last_activity_at: Optional[datetime] = None
    similarity: float
    blocks: bool


class ClashPreviewRequest(BaseModel):
    developer_party_id: Optional[str] = None
    title: str = Field(..., min_length=1)


class ClashPreviewResponse(BaseModel):
    candidates: List[ClashCandidateResponse] = Field(default_factory=list)
    would_block: bool = False


# ------------------------------------------------------------------- projects


class ProjectRegisterRequest(BaseModel):
    """AC-C3. Only title is structurally required.

    Everything else is knowable later and demanding it up front is how a
    registration screen becomes something people avoid until they have "all the
    details", which is exactly when the duplicate gets created.
    """

    title: str = Field(..., min_length=1)
    developer_party_id: Optional[str] = None
    type_id: Optional[str] = None
    template_id: Optional[str] = None
    owner_user_id: Optional[str] = Field(
        None, description="Defaults to the caller. Reassigning needs projects.manage."
    )
    # The lead this project came from (#1339). Linking marks the lead as Qualify does; a
    # lead another project already carries is refused.
    lead_id: Optional[str] = None
    admin_ref: Optional[str] = Field(None, max_length=64)

    registered_company_name: Optional[str] = None
    location: Optional[str] = None
    address: Optional[str] = None
    architect_party_id: Optional[str] = None
    main_contractor_party_id: Optional[str] = None
    estimated_sales_value: Optional[Decimal] = Field(None, ge=0)
    launch_date: Optional[date] = None
    expected_delivery_from: Optional[date] = None
    expected_delivery_to: Optional[date] = None
    brand_ids: List[str] = Field(default_factory=list)


class ProjectUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1)
    developer_party_id: Optional[str] = None
    type_id: Optional[str] = None
    template_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    loss_reason: Optional[str] = None
    # Project sales admin's filing reference, e.g. PS26-0143. Searchable, and not an
    # identity: it is the string written on every piece of paper for this job, and
    # without it nobody can tie a document back to the file it belongs to.
    admin_ref: Optional[str] = Field(None, max_length=64)

    registered_company_name: Optional[str] = None
    location: Optional[str] = None
    address: Optional[str] = None
    architect_party_id: Optional[str] = None
    main_contractor_party_id: Optional[str] = None
    estimated_sales_value: Optional[Decimal] = Field(None, ge=0)
    launch_date: Optional[date] = None
    expected_delivery_from: Optional[date] = None
    expected_delivery_to: Optional[date] = None
    brand_ids: Optional[List[str]] = None
    # Sent as null to unlink; omitted leaves the link as it is (#1339).
    lead_id: Optional[str] = None

    # AC-G7: critical is a flag settable at any status, never a funnel rung.
    is_critical: Optional[bool] = None
    management_support: Optional[str] = None
    management_notes: Optional[str] = None


class ProjectStatusChangeRequest(BaseModel):
    to_status_id: str
    note: Optional[str] = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_code: str
    title: str
    outcome: str
    loss_reason: Optional[str] = None
    admin_ref: Optional[str] = None

    developer_party_id: Optional[str] = None
    developer_name: Optional[str] = None
    type_id: Optional[str] = None
    type_name: Optional[str] = None
    template_id: Optional[str] = None
    template_name: Optional[str] = None

    # Provenance (AC-O10). All null on a project registered directly, which the detail
    # page renders as "registered directly" rather than as an empty section.
    lead_id: Optional[str] = None
    lead_code: Optional[str] = None
    lead_source: Optional[str] = None
    lead_created_at: Optional[datetime] = None
    lead_owner_user_id: Optional[str] = None

    # The customer, read through the lead (a project has no customer column of its own).
    # Null with no lead, or with a lead whose buyer is not known yet.
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_attn: Optional[str] = None

    status_id: Optional[str] = None
    status_key: Optional[str] = None
    status_label: Optional[str] = None

    owner_user_id: Optional[str] = None
    owner_name: Optional[str] = None

    is_critical: bool = False
    critical_at: Optional[datetime] = None
    management_support: Optional[str] = None
    management_notes: Optional[str] = None

    registered_company_name: Optional[str] = None
    location: Optional[str] = None
    address: Optional[str] = None
    architect_party_id: Optional[str] = None
    architect_name: Optional[str] = None
    main_contractor_party_id: Optional[str] = None
    main_contractor_name: Optional[str] = None
    estimated_sales_value: Optional[Decimal] = None
    launch_date: Optional[date] = None
    expected_delivery_from: Optional[date] = None
    expected_delivery_to: Optional[date] = None
    brands: List[str] = Field(default_factory=list)
    brand_ids: List[str] = Field(default_factory=list)

    last_meaningful_activity_at: Optional[datetime] = None
    days_since_last_activity: Optional[int] = None
    # DERIVED from the earliest open task, never stored (AC-N6). Absent rather than
    # null-dated when there is no open work at all: "nothing planned" and "planned with
    # no date" are different states and the worklist treats them differently.
    next_action_date: Optional[date] = None
    next_action_overdue: bool = False
    open_task_count: int = 0

    # Staleness ladder, stamped by the daily sweep (AC-H6). 0 fine, 1 owner nudged, 2 owner
    # warned and management copied, 3 Unattended -- at which point colleagues may ASK to take
    # the project over. Nothing here ever changes the owner.
    stale_level: int = 0
    stale_reason: Optional[str] = None
    stale_since: Optional[datetime] = None
    is_unattended: bool = False
    can_edit: bool = False

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# --------------------------------------------------------------- stakeholders


class ProjectStakeholderBase(BaseModel):
    person_name: str = Field(..., min_length=1, max_length=255)
    role_id: Optional[str] = Field(
        None,
        description=(
            "A role from the project's template. The role belongs to the pairing, not "
            "the person: the same QS is a decision maker on one tender and an "
            "influencer on the next."
        ),
    )
    party_id: Optional[str] = None
    job_title: Optional[str] = Field(None, max_length=120)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=150)
    influence: Optional[str] = Field(None, description="high | medium | low")
    is_primary: bool = False
    notes: Optional[str] = None


class ProjectStakeholderCreate(ProjectStakeholderBase):
    pass


class ProjectStakeholderUpdate(BaseModel):
    person_name: Optional[str] = Field(None, min_length=1, max_length=255)
    role_id: Optional[str] = None
    party_id: Optional[str] = None
    job_title: Optional[str] = Field(None, max_length=120)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=150)
    influence: Optional[str] = None
    is_primary: Optional[bool] = None
    notes: Optional[str] = None


class ProjectStakeholderResponse(ProjectStakeholderBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    role_name: Optional[str] = None
    party_name: Optional[str] = None


# ------------------------------------------------- collaborators / takeovers


class TakeoverRequestCreate(BaseModel):
    kind: str = Field(..., description="join | dispute")
    reason: str = Field(
        ...,
        min_length=1,
        description=(
            "Mandatory (AC-C7). A takeover with no stated reason gives the manager "
            "nothing to decide on."
        ),
    )


class TakeoverRequestDecision(BaseModel):
    approve: bool
    decision_note: Optional[str] = None


class TakeoverRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    project_code: Optional[str] = None
    project_title: Optional[str] = None
    kind: str
    reason: str
    status: str
    requester_user_id: str
    requester_name: Optional[str] = None
    decided_by: Optional[str] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    created_at: Optional[datetime] = None


class ProjectCollaboratorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    user_id: str
    user_name: Optional[str] = None
    granted_by: Optional[str] = None
    granted_at: Optional[datetime] = None


# ---------------------------------------------------------------------- tasks


class ProjectTemplateTaskBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    task_phase: str = Field(
        "pursuit",
        description=(
            "pursuit = the sales work needed to win; delivery = post-win execution. "
            "A separate axis from category, which is the work-stream."
        ),
    )
    category: Optional[str] = Field(
        None,
        max_length=120,
        description="Work-stream label, free-form per template (Spec-in, Sampling, ...)",
    )
    sort_order: int = 0
    default_offset_days: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Days after registration this task is due. Null means no due date, which "
            "is honest for work whose timing depends on events rather than elapsed days."
        ),
    )
    is_active: bool = True


class ProjectTemplateTaskCreate(ProjectTemplateTaskBase):
    pass


class ProjectTemplateTaskUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    task_phase: Optional[str] = None
    category: Optional[str] = Field(None, max_length=120)
    sort_order: Optional[int] = None
    default_offset_days: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class ProjectTemplateTaskResponse(ProjectTemplateTaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    template_id: str
    # How many live project tasks came from this item. Non-zero means delete is
    # blocked and deactivate is the action (AC-N11), so the UI can say why up front.
    in_use_count: int = 0


class ProjectTaskBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    task_phase: str = "pursuit"
    category: Optional[str] = Field(None, max_length=120)
    assignee_user_id: Optional[str] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    sort_order: int = 0
    linked_entity_type: Optional[str] = Field(
        None, description="quotation_version | sample | purchase_order"
    )
    linked_entity_id: Optional[str] = None


class ProjectTaskCreate(ProjectTaskBase):
    status_id: Optional[str] = Field(
        None, description="Defaults to the task graph's initial status."
    )


class ProjectTaskUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    task_phase: Optional[str] = None
    category: Optional[str] = Field(None, max_length=120)
    assignee_user_id: Optional[str] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    sort_order: Optional[int] = None
    linked_entity_type: Optional[str] = None
    linked_entity_id: Optional[str] = None


class ProjectTaskStatusChangeRequest(BaseModel):
    """Escalate and Stuck carry their context in the SAME request as the move.

    Two calls would leave a window where the task is escalated to nobody, and a client
    that crashed between them would leave it there permanently.
    """

    to_status_id: str
    escalated_to_user_id: Optional[str] = Field(
        None, description="Required when moving to a status keyed `escalate`."
    )
    stuck_reason: Optional[str] = Field(
        None, description="Required when moving to a status keyed `stuck`."
    )


class ProjectTaskResponse(ProjectTaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    project_code: Optional[str] = None
    project_title: Optional[str] = None

    status_id: Optional[str] = None
    status_key: Optional[str] = None
    status_label: Optional[str] = None
    is_open: bool = True

    assignee_name: Optional[str] = None
    escalated_to_user_id: Optional[str] = None
    escalated_to_name: Optional[str] = None
    stuck_reason: Optional[str] = None

    completed_at: Optional[datetime] = None
    is_overdue: bool = False
    days_until_due: Optional[int] = None
    source_template_task_id: Optional[str] = None
    can_edit: bool = False

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectTaskHistoryEntry(BaseModel):
    """One change to a task, from the existing audit listeners (AC-N7).

    No dedicated history table: the audit trail already captures per-field diffs, and a
    second store would drift from it.
    """

    at: datetime
    actor_name: Optional[str] = None
    action: str
    field: Optional[str] = None
    from_value: Optional[str] = None
    to_value: Optional[str] = None


# ----------------------------------------------------------------- S2c leads


class ProjectLeadInformant(BaseModel):
    """Who told us (D6, AC-A2). Never a debtor, never written to `customers`.

    Separate from the BUYER because a BCI sighting has no counterparty at all: the
    trading house only exists once a contractor is awarded. Mixed into create and
    update rather than nested, so the wizard posts one flat body.
    """

    informant_source: Optional[str] = Field(
        None,
        max_length=32,
        description=(
            "Reportable bucket: bci | panel | referral | walk_in | consultant | "
            "architect | contractor | other."
        ),
    )
    informant_ref: Optional[str] = Field(
        None, max_length=180, description="Their own reference, e.g. a BCI project id."
    )
    informant_party_id: Optional[str] = Field(
        None, description="A firm on `project_parties`, when the informant has one."
    )
    informant_contact_name: Optional[str] = Field(
        None,
        max_length=180,
        description="A lone informant with no firm on record is normal.",
    )


class ProjectLeadBase(ProjectLeadInformant):
    title: str = Field(min_length=1)
    customer_id: Optional[str] = Field(
        None,
        description=(
            "The BUYER: the debtor who will issue the PO (D6). Nullable, and usually "
            "unknown on day one -- set it when the contractor is awarded."
        ),
    )
    developer_party_id: Optional[str] = Field(
        None, description="Often unknown at sighting time."
    )
    source: Optional[str] = None
    source_detail: Optional[str] = None
    estimated_value: Optional[Decimal] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    owner_user_id: Optional[str] = None


class ProjectLeadNewCustomer(BaseModel):
    """Create a customer for an organisation that has never bought anything.

    Rows created this way carry ``source='project_lead'`` so order and invoice pickers
    can filter prospects out if the noise becomes real.
    """

    customer_name: str = Field(min_length=1, max_length=255)
    customer_code: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=150)
    phone_number: Optional[str] = Field(None, max_length=50)
    registration_number: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class ProjectLeadCreate(ProjectLeadBase):
    new_customer: Optional[ProjectLeadNewCustomer] = Field(
        None,
        description=(
            "Create the BUYER inline when it is already known and not on file. Ignored "
            "when `customer_id` is given, and omitted entirely on a BCI sighting, which "
            "has no buyer yet."
        ),
    )


class ProjectLeadUpdate(ProjectLeadInformant):
    title: Optional[str] = Field(None, min_length=1)
    customer_id: Optional[str] = None
    developer_party_id: Optional[str] = None
    source: Optional[str] = None
    source_detail: Optional[str] = None
    estimated_value: Optional[Decimal] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    owner_user_id: Optional[str] = None


class ProjectLeadDuplicateHint(BaseModel):
    """Informational only (AC-O3). A lead is never blocked by one of these."""

    lead_id: str
    lead_code: str
    owner_name: Optional[str] = None


class ProjectLeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lead_code: str
    title: str

    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    developer_party_id: Optional[str] = None
    developer_name: Optional[str] = None

    informant_source: Optional[str] = None
    informant_ref: Optional[str] = None
    informant_party_id: Optional[str] = None
    # Resolved from `project_parties` so the screen never has to render the id, per the
    # cursor rule that no UUID reaches the UI.
    informant_party_label: Optional[str] = None
    informant_contact_name: Optional[str] = None

    # The acceptance handshake (D7). `assigned` is not ownership: a lead is only owned
    # once the salesperson accepts it, which is what makes silence measurable.
    acceptance_state: Optional[str] = None
    assigned_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    declined_reason: Optional[str] = None
    declined_at: Optional[datetime] = None

    source: Optional[str] = None
    source_detail: Optional[str] = None
    estimated_value: Optional[Decimal] = None
    location: Optional[str] = None
    notes: Optional[str] = None

    status_id: Optional[str] = None
    status_key: Optional[str] = None
    status_label: Optional[str] = None
    outcome: str = "open"
    disqualified_reason: Optional[str] = None
    qualified_at: Optional[datetime] = None

    owner_user_id: Optional[str] = None
    owner_name: Optional[str] = None

    project_count: int = 0
    possible_duplicates: List[ProjectLeadDuplicateHint] = Field(default_factory=list)
    can_edit: bool = False
    # Diverges from can_edit precisely where it matters: a decline clears the owner and
    # can_edit is owner-or-manager, so whoever raised the lead could otherwise not
    # re-assign the lead that just came back to them.
    can_assign: bool = False

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectLeadStatusChangeRequest(BaseModel):
    to_status_id: str


class ProjectLeadAssignRequest(BaseModel):
    """Hand the lead to a salesperson and start the acceptance clock (AC-A4)."""

    owner_user_id: str = Field(
        min_length=1, description="The salesperson being asked to take it on."
    )
    note: Optional[str] = Field(
        None,
        description=(
            "Context for the assignee, carried in their notification. Not stored on "
            "the lead: the lead's own `notes` belong to the sighting."
        ),
    )


class ProjectLeadDeclineRequest(BaseModel):
    """Refuse an assignment (AC-A5). The reason is free text on purpose.

    Unlike a disqualification, which feeds the conversion report and must come from a
    lookup, this is one salesperson telling marketing why it is not their patch.
    """

    reason: str = Field(min_length=1)


class ProjectLeadAwaitingAcceptanceRow(ProjectLeadResponse):
    """A row of marketing's "who has taken nothing" worklist (AC-A7).

    ``hours_since_assigned`` is computed here so the screen shows the wait without
    doing date maths in the browser, where a naive-UTC timestamp would be read as
    local time and every row would be eight hours out.
    """

    hours_since_assigned: Optional[float] = None


class ProjectLeadQualifyRequest(BaseModel):
    """The confirm step of the wizard. Every field is optional: the lead already knows
    most of it, and the point of qualify is to avoid re-keying what we were told."""

    title: Optional[str] = Field(
        None, description="Defaults to the lead's title. Split a masterplan here."
    )
    developer_party_id: Optional[str] = None
    type_id: Optional[str] = None
    template_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    brand_ids: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = Field(
        None, description="Sales-profile fields, same shape as registration."
    )


class ProjectLeadDisqualifyRequest(BaseModel):
    reason: str = Field(
        min_length=1,
        description=(
            "Must be a value from the `project_lead_disqualify_reason` lookup set: a "
            "free-text reason cannot be reported on."
        ),
    )


class ProjectLeadReasonOption(BaseModel):
    value: str
    label: str


class ProjectLeadDisqualifiedReasonCount(ProjectLeadReasonOption):
    count: int


class ProjectLeadConversionMetrics(BaseModel):
    total: int
    open: int
    qualified: int
    disqualified: int
    decided: int
    conversion_rate: Optional[float] = Field(
        None,
        description=(
            "Qualified / decided. Null rather than 0 when nothing is decided yet: zero "
            "would read as 'we convert nothing'."
        ),
    )
    projects_from_leads: int
    disqualified_reasons: List[ProjectLeadDisqualifiedReasonCount] = Field(
        default_factory=list
    )


class CustomerPortfolioResponse(BaseModel):
    """One customer's leads and projects (AC-O9), the account view.

    Both lists always present, empty when there is nothing: the section renders either
    way per the CRUD standard, with an explicit empty state rather than disappearing.
    """

    leads: List[ProjectLeadResponse] = Field(default_factory=list)
    projects: List[ProjectResponse] = Field(default_factory=list)


# ------------------------------------------------------------ S3 quotations


class ProjectSeriesBase(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: Optional[str] = None
    is_active: bool = True


class ProjectSeriesCreate(ProjectSeriesBase):
    brand_ids: List[str] = Field(
        default_factory=list, description="Picked brands. Empty means every brand."
    )
    category_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Nominated categories. A PARENT covers all its descendants, so this is a "
            "short list of groups rather than a list of every SKU."
        ),
    )


class ProjectSeriesUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    brand_ids: Optional[List[str]] = Field(
        None, description="Sent WHOLE, not as a delta."
    )
    description: Optional[str] = None
    is_active: Optional[bool] = None
    category_ids: Optional[List[str]] = Field(
        None, description="Sent WHOLE, not as a delta."
    )


class ProjectSeriesResponse(ProjectSeriesBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    brand_ids: List[str] = Field(default_factory=list)
    brand_names: List[str] = Field(default_factory=list)
    category_ids: List[str] = Field(default_factory=list)
    category_names: List[str] = Field(default_factory=list)
    covered_category_count: int = Field(
        0,
        description=(
            "Nominated categories plus every descendant -- what the non-standard check "
            "actually compares against."
        ),
    )
    product_count: int = Field(
        0, description="Products nominated BY NAME, independently of any category (S18)."
    )
    product_codes: List[str] = Field(
        default_factory=list,
        description=(
            "Those products' CODES. Codes rather than ids so the screen can name them "
            "without a second lookup, and so no UUID reaches the UI."
        ),
    )
    quotation_count: int = 0


class ProjectSeriesProductImport(BaseModel):
    """A list of product CODES to load onto a series (S18)."""

    codes: List[str] = Field(
        default_factory=list,
        description=(
            "Product codes as the admin has them. Matched on the repo's shared normaliser "
            "(case, dashes and whitespace ignored), so 'CWB 242' finds 'CWB-242'."
        ),
    )
    mode: str = Field(
        "append",
        description=(
            "append = add these to what the series already lists. replace = make the "
            "series say exactly this and nothing else."
        ),
    )


class ProjectSeriesProductImportResponse(BaseModel):
    """What the import DID, including what it could not do.

    ``unmatched_codes`` is the field that matters: a code the catalogue does not carry is
    the admin's data telling them something, and reporting a smaller success number
    instead would hide it.
    """

    series_id: str
    series_name: str
    mode: str
    submitted: int
    unique_codes: int
    matched_codes: int
    added: int
    already_present: int
    removed: int
    product_count: int
    unmatched_codes: List[str] = Field(default_factory=list)
    #: Codes that were a set or a family, not a product: ``{code, via, members}``.
    resolved: List[Dict[str, Any]] = Field(default_factory=list)


class QuotationLineVerdictResponse(BaseModel):
    """The two guardrail answers for ONE draft line, judged now.

    Exists so the editor can flag a line the moment the product is picked or the price
    typed, instead of after the save - and judged by the SAME service functions the save
    uses (`is_in_series`, `resolve_floor`), so the live answer and the persisted one
    cannot disagree. The browser holds no second implementation.

    `floor_value` and `floor_level` ride along so a breach can be argued with ("below the
    Basins floor of RM 850"), the same fields the saved line carries.
    """

    is_non_standard: bool
    is_below_floor: bool
    floor_value: Optional[Decimal] = None
    floor_level: Optional[str] = None


class SeriesProductImportJobResponse(BaseModel):
    """A queued sheet load: the id to watch, and nothing pretending to be a result.

    Deliberately does NOT carry counts. The upload route returns before a single code has
    been read, and a zero here would be indistinguishable from a sheet that genuinely
    matched nothing. The finished report arrives in ``import_jobs.result`` and is served by
    ``GET /system/jobs/{job_id}/status``, in the same shape as
    ``ProjectSeriesProductImportResponse`` so one renderer draws both paths.
    """

    job_id: str
    series_id: str
    mode: str


class SeriesProductRowResponse(BaseModel):
    """One product on a series, as the table on the series page reads it (T2).

    ``derived_floor`` is computed on the server by `project_pricing_service.series_floor`,
    the same function the pricing engine enforces with, and is NULL unless the row carries
    both a price and a percentage. A floor computed a second time in the browser is a
    refusal the user can argue with.
    """

    model_config = ConfigDict(from_attributes=True)

    product_id: str
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    #: NULL means the series has not said, and is never read as zero.
    selling_price: Optional[Decimal] = None
    #: A PERCENT: 6 means 6%.
    max_discount_pct: Optional[Decimal] = None
    derived_floor: Optional[Decimal] = None
    #: True when the product is of the series' brand and under one of its categories.
    in_scope: bool = False


class SeriesSetRowResponse(BaseModel):
    """One product SET priced on a series (SET-QUOTE-LINE), the twin of the product row."""

    model_config = ConfigDict(from_attributes=True)

    product_set_id: str
    set_code: Optional[str] = None
    set_name: Optional[str] = None
    part_codes: List[str] = Field(default_factory=list)
    selling_price: Optional[Decimal] = None
    max_discount_pct: Optional[Decimal] = None
    derived_floor: Optional[Decimal] = None


class SeriesProductPricingUpdate(BaseModel):
    """Set (or clear) one product's price and percentage from the series page.

    Both fields are explicit: sending null CLEARS, because the person emptied the cell. The
    sheet importer treats an absent value as silence instead - a spreadsheet that states no
    price is not asking for one to be deleted.
    """

    selling_price: Optional[Decimal] = Field(None, ge=0)
    max_discount_pct: Optional[Decimal] = Field(None, ge=0, le=100)


class QuotationRecomputeResponse(BaseModel):
    """What re-asking the guardrails changed on one version (S19)."""

    version_id: str
    version_no: int
    quotation_id: str
    scope_label: Optional[str] = None
    line_count: int
    changed_count: int
    now_non_standard: int
    no_longer_non_standard: int
    now_below_floor: int
    no_longer_below_floor: int
    floor_changed: int
    unresolved_products: int = Field(
        0,
        description=(
            "Lines naming a product this company's catalogue does not carry. They read as "
            "off-catalog and stay non-standard, so without this the pass would report "
            "'nothing changed' and leave nobody any the wiser."
        ),
    )
    changed_lines: List[str] = Field(
        default_factory=list,
        description="The lines that moved, named as a person names them: product code, or the description of an off-catalog line.",
    )


class PriceFloorRuleBase(BaseModel):
    mode: str = Field(
        "percent",
        description=(
            "percent = of the product's list price; absolute = a hard amount that "
            "ignores list entirely."
        ),
    )
    value: Decimal
    notes: Optional[str] = None
    is_active: bool = True


class PriceFloorRuleUpsert(PriceFloorRuleBase):
    product_id: Optional[str] = Field(
        None, description="Set for a product-level rule. Mutually exclusive with category_id."
    )
    category_id: Optional[str] = Field(
        None, description="Set for a category-level rule. Covers that category only."
    )


class PriceFloorRuleResponse(PriceFloorRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: Optional[str] = None
    product_code: Optional[str] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    level: str = Field(
        description=(
            "Derived from which key is set, never stored: product | category | system."
        )
    )


class EffectiveFloorSource(BaseModel):
    """The floor in force, and the human-readable name of whatever it came from."""

    rule_id: str
    level: str = Field(
        description="product | category | category_ancestor | system."
    )
    mode: str
    value: Decimal = Field(description="The rule as configured: a percent, or an amount.")
    amount: Optional[Decimal] = Field(
        None,
        description=(
            "The floor in ringgit, when it can be computed. Null for a percentage rule "
            "read against a category, which has no list price to apply it to."
        ),
    )
    source_label: str = Field(
        description=(
            "Product code, category name, or 'Company default'. Never an id: the browser "
            "has no way to resolve one."
        )
    )


class PriceFloorEffectiveResponse(BaseModel):
    """What governs ONE product or ONE category, for the master-data editors.

    ``own_rule`` and ``effective`` answer different questions and both are needed: the
    first says whether this target carries a floor of its own (so whether clearing it is
    even possible), the second says what actually applies once inheritance is taken into
    account.
    """

    target_level: str = Field(description="product | category.")
    target_id: str
    target_label: str
    list_price: Optional[Decimal] = Field(
        None, description="The product's list price. Null for a category."
    )
    own_rule: Optional[PriceFloorRuleResponse] = None
    effective: Optional[EffectiveFloorSource] = None


class ProjectQuotationBase(BaseModel):
    scope_label: str = Field(
        min_length=1, max_length=150, description="e.g. House Units, Common Area, Showroom."
    )
    series_id: Optional[str] = None
    notes: Optional[str] = None


class ProjectQuotationCreate(ProjectQuotationBase):
    pass


class ProjectQuotationUpdate(BaseModel):
    scope_label: Optional[str] = Field(None, min_length=1, max_length=150)
    series_id: Optional[str] = None
    notes: Optional[str] = None


class ProjectQuotationResponse(ProjectQuotationBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    series_name: Optional[str] = None

    outcome: str = "open"
    loss_reason: Optional[str] = None
    loss_reason_label: Optional[str] = None
    decided_at: Optional[datetime] = None

    version_count: int = 0
    # There is no `current_version_id` COLUMN (AC-E3a); this is computed as
    # MAX(version_no) at read time and is safe to render.
    current_version_id: Optional[str] = None
    current_version_no: Optional[int] = None
    current_total: Optional[Decimal] = None

    below_floor_count: int = 0
    non_standard_count: int = 0
    line_count: int = 0

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectQuotationVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    quotation_id: str
    version_no: int
    is_current: bool = Field(
        description="Derived: version_no == MAX(version_no). Everything else is frozen."
    )
    is_issued: bool = Field(
        default=False,
        description="An issue of the parent document went out carrying this version.",
    )
    is_editable: bool = Field(
        default=True,
        description=(
            "Current AND not issued. The flag a screen should gate on: an issued version is "
            "still the highest, so `is_current` alone offers edits the server refuses."
        ),
    )
    frozen_at: Optional[datetime] = None
    issued_by: Optional[str] = None
    issued_by_name: Optional[str] = None
    issued_on: Optional[date] = None
    total_amount: Decimal = Decimal("0")
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


class ProjectQuotationLineBase(BaseModel):
    product_id: Optional[str] = Field(
        None, description="Null for an off-catalog line, which always raises the alert."
    )
    product_set_id: Optional[str] = Field(
        None,
        pattern=UUID_PATTERN,
        description="A product set quoted as ONE line. Never together with product_id (422).",
    )
    description_snapshot: Optional[str] = Field(
        None,
        description="Required when there is no product or set: it is what the customer reads.",
    )
    unit_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("1")
    uom: Optional[str] = None
    unit_type: Optional[str] = Field(
        None, description="house_unit | bathroom | facility | common_area"
    )
    sort_order: int = 0
    notes: Optional[str] = None

    # The columns the printed quotation carries. Writable from this slice on, so a line can be
    # entered the way it will actually be sent rather than typed twice.
    item_label: Optional[str] = Field(
        None,
        max_length=8,
        description="The A / B / C letter. Blank on a sub-line under the letter above it.",
    )
    brand_snapshot: Optional[str] = Field(None, max_length=100)
    technical_spec: Optional[str] = None
    complete_set: Optional[str] = Field(None, max_length=100)
    band_label: Optional[str] = Field(
        None,
        max_length=150,
        description=(
            "A section heading BEFORE this line, free text off the customer's own bill of "
            "quantities. Carried by the line that opens the band."
        ),
    )
    is_rate_only: bool = Field(
        False,
        description="Quoted at a rate and printed, contributing nothing to any total.",
    )
    image_attachment_id: Optional[str] = Field(
        None,
        description=(
            "The photo picked for this line: an image attachment of the line's product. "
            "Refused (422 quotation_line_image_invalid) when it is not."
        ),
    )
    drawing_attachment_id: Optional[str] = Field(
        None,
        description=(
            "The drawing picked for this line: a 'Technical Specifications' image of the "
            "line's product. Refused (422 quotation_line_drawing_invalid) when it is not."
        ),
    )


class ProjectQuotationLineCreate(ProjectQuotationLineBase):
    pass


class ProjectQuotationLineUpdate(BaseModel):
    product_id: Optional[str] = None
    product_set_id: Optional[str] = Field(None, pattern=UUID_PATTERN)
    description_snapshot: Optional[str] = None
    unit_price: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    uom: Optional[str] = None
    unit_type: Optional[str] = None
    sort_order: Optional[int] = None
    notes: Optional[str] = None
    image_attachment_id: Optional[str] = None
    drawing_attachment_id: Optional[str] = None
    item_label: Optional[str] = Field(None, max_length=8)
    brand_snapshot: Optional[str] = Field(None, max_length=100)
    technical_spec: Optional[str] = None
    complete_set: Optional[str] = Field(None, max_length=100)
    band_label: Optional[str] = Field(None, max_length=150)
    is_rate_only: Optional[bool] = None

    @field_validator("quantity")
    @classmethod
    def _quantity_not_null(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        # Runs only when the key is sent: an explicit null would be read as 1 downstream.
        if v is None:
            raise ValueError("Quantity cannot be blank.")
        return v


class ProjectQuotationLineBulkItem(ProjectQuotationLineBase):
    """One line in a whole-set save (S10).

    ``id`` is what tells an existing line from a new one: a line already stored carries the
    id the API gave it, a new one arrives without one. ``sort_order`` is inherited from the
    base for shape compatibility but is IGNORED - position in the array is the order.
    """

    id: Optional[str] = Field(
        None,
        description=(
            "The id of a line already on this version. Omit it for a new line. An id that "
            "is not on this version is refused, never treated as new."
        ),
    )


class ProjectQuotationLineBulkWrite(BaseModel):
    """The FULL desired line set for one version, in display order.

    Whole-set semantics: a stored line whose id is absent from ``lines`` is DELETED, so a
    client must send every line it is showing, not only the ones it changed.
    """

    lines: List[ProjectQuotationLineBulkItem] = Field(
        default_factory=list,
        description=(
            "Every line the version should end up with, in display order. An empty array "
            "clears the version."
        ),
    )


class QuotationLineFileCandidate(BaseModel):
    """One image a line's photo or drawing could be: a signed thumbnail and the original."""

    attachment_id: str
    filename: Optional[str] = None
    url: Optional[str] = None
    preview_url: Optional[str] = None


class QuotationLineImageResponse(BaseModel):
    """The picture cell for one line (S21).

    Declared, and not merely built by the serializer, because `response_model` DROPS any key
    no field declares. `serialize_lines` had been returning `product_image` since S21 and
    FastAPI had been deleting it from every line route on the way out, so the column rendered
    "-" for every product - including ones whose photograph had been chosen correctly. The
    symptom points at the photo (or at `is_primary`), and the cause is here.
    """

    model_config = ConfigDict(from_attributes=True)

    #: chosen | not_chosen | no_photos | off_catalog
    state: str
    #: A signed THUMBNAIL, and only ever on `chosen`.
    url: Optional[str] = None
    #: The signed ORIGINAL, for the viewer that opens on click. The thumbnail is ~320 px and
    #: turns to mush the moment somebody zooms.
    preview_url: Optional[str] = None
    #: Lets the viewer download through the authenticated attachments route.
    attachment_id: Optional[str] = None
    filename: Optional[str] = None
    #: "Product Photos" images somebody could choose between (a drawing is never one). Zero on
    #: `no_photos` and `off_catalog`.
    candidate_count: int = 0
    #: The product's photos, for the picker that writes `image_attachment_id`.
    candidates: List[QuotationLineFileCandidate] = Field(default_factory=list)


class QuotationLineDrawingResponse(BaseModel):
    """The drawing cell for one line (QUOTATION-UX): the product's "Technical Specifications"
    images, printed in TECHNICAL SPEC.

    Declared for the same reason as `QuotationLineImageResponse`: `response_model` drops any key
    no field declares.
    """

    #: chosen (picked or stamped) | default (first by upload) | no_drawings | off_catalog
    state: str
    url: Optional[str] = None
    preview_url: Optional[str] = None
    attachment_id: Optional[str] = None
    filename: Optional[str] = None
    candidate_count: int = 0
    candidates: List[QuotationLineFileCandidate] = Field(default_factory=list)


class QuotationLineImagesResponse(BaseModel):
    """The photo and drawing cells for a product on a line not saved yet (no picks)."""

    product_image: QuotationLineImageResponse
    technical_drawing: QuotationLineDrawingResponse


class ProjectQuotationLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version_id: str
    product_id: Optional[str] = None
    product_set_id: Optional[str] = None
    #: The parts frozen on a set line ({product_id, product_code, description, quantity,
    #: is_driver}); empty for a product line.
    set_parts: List[Dict[str, Any]] = Field(default_factory=list)
    product_code: Optional[str] = None
    description: Optional[str] = None
    list_price: Optional[Decimal] = None
    image_attachment_id: Optional[str] = None
    product_image: Optional[QuotationLineImageResponse] = None
    drawing_attachment_id: Optional[str] = None
    technical_drawing: Optional[QuotationLineDrawingResponse] = None

    unit_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("1")
    uom: Optional[str] = None
    unit_type: Optional[str] = None
    line_total: Decimal = Decimal("0")
    # Quoted at a rate and printed, contributing nothing to any total (AC-C2).
    is_rate_only: bool = False
    item_label: Optional[str] = None
    brand: Optional[str] = None
    technical_spec: Optional[str] = None
    complete_set: Optional[str] = None
    band_label: Optional[str] = None

    is_non_standard: bool = False
    # The floor IN FORCE WHEN PRICED (AC-E7). A later policy change never rewrites it.
    floor_value_applied: Optional[Decimal] = None
    floor_level_applied: Optional[str] = None
    is_below_floor: bool = False

    sort_order: int = 0
    notes: Optional[str] = None


class ProjectQuotationOutcomeRequest(BaseModel):
    outcome: str = Field(description="open | won | lost")
    loss_reason: Optional[str] = Field(
        None,
        description=(
            "Mandatory when outcome is lost, and must be a value from the "
            "`project_quotation_loss_reason` lookup set."
        ),
    )


# --------------------------------------------------- S4 samples and customer POs


class ProjectSampleBase(BaseModel):
    quotation_version_id: str = Field(
        description=(
            "The VERSION, never the quotation: 'which price was the developer looking "
            "at when they approved this finish' is what the binding exists to answer."
        )
    )
    submitted_on: Optional[date] = None
    developer_feedback: Optional[str] = None
    salesperson_notes: Optional[str] = None


class ProjectSampleCreate(ProjectSampleBase):
    pass


class ProjectSampleUpdate(BaseModel):
    quotation_version_id: Optional[str] = None
    submitted_on: Optional[date] = None
    developer_feedback: Optional[str] = None
    salesperson_notes: Optional[str] = None


class ProjectSampleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    quotation_version_id: str
    quotation_id: Optional[str] = None
    scope_label: Optional[str] = None
    version_no: Optional[int] = None
    is_version_current: bool = Field(
        True,
        description=(
            "Derived. False means the version was superseded AFTER this sample went "
            "out, which the panel says plainly rather than hiding."
        ),
    )
    submitted_on: Optional[date] = None
    submitted_by: Optional[str] = None
    submitted_by_name: Optional[str] = None
    developer_feedback: Optional[str] = None
    salesperson_notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectPurchaseOrderBase(BaseModel):
    po_number: str = Field(min_length=1, max_length=100)
    po_source: str = Field(
        "contractor_direct", description="contractor_direct | trading_house"
    )
    quotation_version_id: Optional[str] = Field(
        None,
        description=(
            "The version the contractor was last shown, which is the only fair thing to "
            "compare the PO against (AC-F9). Null is allowed: a PO can arrive before "
            "anything was formally quoted, and it simply gets no mismatch check."
        ),
    )
    issuing_party_id: Optional[str] = None
    po_date: Optional[date] = None
    po_amount: Optional[Decimal] = None
    notes: Optional[str] = None


class ProjectPurchaseOrderCreate(ProjectPurchaseOrderBase):
    pass


class ProjectPurchaseOrderUpdate(BaseModel):
    """The header, and optionally the WHOLE line set, in one save.

    ``lines`` is what makes the PO detail screen an edit view rather than a table that
    writes on every blur: absent means "header only", which is the shape the record-a-PO
    modal sends and must keep working. Present, it is the full desired set and a stored
    line whose id is missing from it is DELETED, so a client must send every line it is
    showing the user. See ``project_po_service.replace_lines``.
    """

    po_number: Optional[str] = Field(None, min_length=1, max_length=100)
    po_source: Optional[str] = None
    quotation_version_id: Optional[str] = None
    issuing_party_id: Optional[str] = None
    po_date: Optional[date] = None
    po_amount: Optional[Decimal] = None
    notes: Optional[str] = None
    lines: Optional[List["ProjectPurchaseOrderLineWrite"]] = Field(
        None,
        description=(
            "Every line the PO should end up with, in display order. Omit the key to leave "
            "the lines untouched; an empty array clears them. Array position IS sort_order."
        ),
    )


class ProjectPurchaseOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    quotation_version_id: Optional[str] = None
    quotation_id: Optional[str] = None
    scope_label: Optional[str] = None
    version_no: Optional[int] = None

    po_source: str = "contractor_direct"
    issuing_party_id: Optional[str] = None
    issuing_party_name: Optional[str] = None
    po_number: str
    po_date: Optional[date] = None
    po_amount: Optional[Decimal] = None
    notes: Optional[str] = None

    line_count: int = 0
    line_total: Decimal = Decimal("0")
    # What still stands between this PO and its sales orders (see the serializer).
    status: Optional[str] = None
    po_confirmed: bool = False
    schedule_confirmed: bool = False
    # Sales orders already OUT of this PO. Editing it stays allowed - corrections are the
    # normal case - but the screen has to be able to say so before somebody saves over it,
    # because those orders do not follow the change.
    published_sales_order_count: int = 0
    model_mismatch_count: int = 0
    price_mismatch_count: int = 0

    # AC-F9a: erosion since v1, as a number rather than a flag. A negotiation is
    # SUPPOSED to move the price; management wants the size of the move.
    v1_total: Optional[Decimal] = None
    drift_delta: Optional[Decimal] = None
    drift_percent: Optional[Decimal] = Field(
        None,
        description="Null when v1 priced nothing: a percentage against zero is not a number.",
    )

    # True only on the response to the create that actually moved the funnel (AC-F10).
    status_moved_to_po_received: bool = False

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectPurchaseOrderLineBase(BaseModel):
    product_id: Optional[str] = None
    product_code: Optional[str] = Field(
        None, description="What the PO itself printed, often the contractor's own code."
    )
    description: Optional[str] = None
    unit_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("1")
    uom: Optional[str] = None
    sort_order: int = 0
    notes: Optional[str] = None


class ProjectPurchaseOrderLineCreate(ProjectPurchaseOrderLineBase):
    pass


class ProjectPurchaseOrderLineUpdate(BaseModel):
    product_id: Optional[str] = None
    product_code: Optional[str] = None
    description: Optional[str] = None
    unit_price: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    uom: Optional[str] = None
    sort_order: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def _quantity_not_null(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        # Runs only when the key is sent: an explicit null would be read as 1 downstream.
        if v is None:
            raise ValueError("Quantity cannot be blank.")
        return v


class ProjectPurchaseOrderLineWrite(ProjectPurchaseOrderLineBase):
    """One line inside a whole-PO save.

    ``id`` is what tells an existing line from a new one: a line already stored carries the
    id the API gave it, a new one arrives without one, and an id that is not on this PO is
    refused rather than treated as new (which would duplicate the row the client meant to
    move). ``sort_order`` is inherited from the base for shape compatibility but is IGNORED --
    position in the array is the order.
    """

    id: Optional[str] = None


# The header schema references the line schema by name, and the line schema needs the base
# that is declared below it. Resolved here, once both exist.
ProjectPurchaseOrderUpdate.model_rebuild()


class ProjectPurchaseOrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    po_id: str
    product_id: Optional[str] = None
    product_code: Optional[str] = None
    description: Optional[str] = None
    unit_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("1")
    uom: Optional[str] = None
    line_total: Decimal = Decimal("0")

    # What the bound version said WHEN THE PO WAS CHECKED (stored, not re-derived).
    quoted_unit_price: Optional[Decimal] = None
    model_mismatch: bool = False
    price_mismatch: bool = False

    sort_order: int = 0
    notes: Optional[str] = None


class ProjectSponsorshipResponse(BaseModel):
    """A sponsorship form linked to this project (AC-F3, AC-F6).

    `project_title` is still carried: it is what the ~28 pre-link rows display, and a
    linked row may still disagree with the free text somebody typed months ago.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    request_number: Optional[str] = None
    request_date: Optional[date] = None
    status: Optional[str] = None
    approval_status: Optional[str] = None
    customer_name: Optional[str] = None
    project_title: Optional[str] = None
    sponsor_subject: Optional[str] = None
    sponsor_subject_other: Optional[str] = None
    total_project_value: Optional[Decimal] = None
    purpose: Optional[str] = None


class SponsorshipYearTotal(BaseModel):
    year: int
    total: Decimal
    form_count: int


class ProjectSponsorshipRollupResponse(BaseModel):
    """AC-F7. Per project AND per year: "what did this development cost us" and "what did
    sponsorship cost us in 2026" are two different management questions."""

    project_id: str
    total: Decimal
    form_count: int
    by_year: List[SponsorshipYearTotal] = Field(default_factory=list)


class SponsorshipConversionResponse(BaseModel):
    """AC-F7's second half. `rate` is null rather than 0 when nothing was sponsored: 0%
    reads as "we sponsor and never win", which is a different and much worse claim."""

    sponsored_projects: int = 0
    converted_projects: int = 0
    rate: Optional[Decimal] = None
    sponsored_spend: Decimal = Decimal("0")


# ------------------------------------------------------- S5a forecast (Group I)


class ForecastYearRow(BaseModel):
    """One delivery year. The three numbers stay in three fields (AC-I2a) so a UI cannot
    stack a speculative figure on top of banked revenue in one column."""

    year: int
    pipeline: Decimal = Decimal("0")
    weighted: Decimal = Decimal("0")
    committed: Decimal = Decimal("0")


class ForecastBandTotals(BaseModel):
    pipeline: Decimal = Decimal("0")
    weighted: Decimal = Decimal("0")
    committed: Decimal = Decimal("0")


class ProjectForecastResponse(BaseModel):
    """AC-I1: three numbers, never blended.

    There is deliberately no `total` field. A single number that mixes a banked PO with a
    10%-probability rumour is the number every spreadsheet produces and nobody can act on,
    and the fastest way to stop it being reported is to not compute it.
    """

    pipeline: Decimal = Decimal("0")
    weighted: Decimal = Decimal("0")
    committed: Decimal = Decimal("0")
    project_count: int = 0
    by_year: List[ForecastYearRow] = Field(default_factory=list)
    undated: ForecastBandTotals = Field(
        default_factory=ForecastBandTotals,
        description=(
            "Projects with no derivable delivery year. Reported rather than dropped: "
            "dropped rows make the buckets disagree with the totals."
        ),
    )


class ConversionResponse(BaseModel):
    won: int = 0
    lost: int = 0
    decided: int = 0
    open: int = 0
    rate: Optional[Decimal] = Field(
        None, description="Null with nothing decided. 0% would claim we lose everything."
    )


class LossReasonCount(BaseModel):
    reason: str
    label: str
    count: int


class SalespersonRow(BaseModel):
    owner_user_id: Optional[str] = None
    owner_name: Optional[str] = None
    project_count: int = 0
    pipeline: Decimal = Decimal("0")
    weighted: Decimal = Decimal("0")
    committed: Decimal = Decimal("0")


class ProjectDashboardResponse(BaseModel):
    """Everything AC-I4 asks for in one read, because the dashboard shows it in one screen
    and four round trips would render it in four stages."""

    forecast: ProjectForecastResponse
    conversion: ConversionResponse
    loss_reasons: List[LossReasonCount] = Field(default_factory=list)
    by_salesperson: List[SalespersonRow] = Field(default_factory=list)
    sponsorship: SponsorshipConversionResponse = Field(
        default_factory=SponsorshipConversionResponse
    )
    delivery_lag_months: int = 30


# ---------------------------------------------------------- quotation documents


class ProjectQuotationDocumentBase(BaseModel):
    your_ref: Optional[str] = None
    doc_date: Optional[date] = None
    attn_name: Optional[str] = None
    subject_title: Optional[str] = None
    cover_letter_html: Optional[str] = None
    terms_html: Optional[str] = None
    signatory_name: Optional[str] = None
    signatory_phone: Optional[str] = None


class ProjectQuotationFormScopeCreate(BaseModel):
    """One scope as the quotation form page sends it on create (#1341): its name, its series
    and the lines already priced under it. Written with the document in the SAME transaction,
    so a save either lands whole or not at all."""

    scope_label: str = Field(
        min_length=1, max_length=150, description="e.g. Townhouse, Guard House, Reception."
    )
    series_id: Optional[str] = None
    notes: Optional[str] = None
    lines: List["ProjectQuotationLineBulkItem"] = Field(
        default_factory=list,
        description="The scope's lines in display order. Empty means an empty scope, never a "
        "placeholder line.",
    )


class ProjectQuotationFormScopeUpdate(BaseModel):
    """One scope as the form sends it on edit (#1341).

    With an ``id`` it is a scope already on this document: the name and series are applied when
    present, and ``lines`` (when present) is the FULL line set of its current version, under the
    same whole-set rule and the same 422 on a frozen or issued version as the bulk line route.
    Without an ``id`` it is a new scope, which needs a name. A saved scope the payload leaves out
    is not touched.
    """

    id: Optional[str] = None
    scope_label: Optional[str] = Field(default=None, max_length=150)
    series_id: Optional[str] = None
    notes: Optional[str] = None
    lines: Optional[List["ProjectQuotationLineBulkItem"]] = None


class ProjectQuotationDocumentCreate(ProjectQuotationDocumentBase):
    """Every field optional on purpose (AC-A2).

    The document arrives already filled in: the reference from the numbering rule, the recipient
    off the project's developer party, the subject from the project title. A create form that
    asked for them would be asking for facts the system already holds.

    The quotation form page (#1341) sends the scopes and their lines with the letterhead, so Add a
    quotation writes nothing until the salesperson presses Save. A recipient value it sends is a
    CORRECTION applied over the party snapshot in the same transaction (the result create-then-edit
    gave before); a blank one keeps the snapshot.
    """

    recipient_name_snapshot: Optional[str] = None
    recipient_address_snapshot: Optional[str] = None
    recipient_phone_snapshot: Optional[str] = None
    scopes: Optional[List[ProjectQuotationFormScopeCreate]] = None


class ProjectQuotationDocumentUpdate(ProjectQuotationDocumentBase):
    """The letterhead, correctable in the edit view.

    The recipient block is here and NOT on the base, so it can be corrected but never supplied at
    creation: AC-A3 snapshots it off the project's developer party, and a create that accepted its
    own recipient would let the two disagree from the first save.

    Correcting is not re-deriving. The snapshot stays the document's own copy, so the customer's
    finance department can take the letter at their own address without touching the party record
    every other document in the project reads, and a party edited next year still cannot rewrite a
    quotation the customer is already holding.
    """

    recipient_name_snapshot: Optional[str] = None
    recipient_address_snapshot: Optional[str] = None
    recipient_phone_snapshot: Optional[str] = None
    scopes: Optional[List[ProjectQuotationFormScopeUpdate]] = Field(
        default=None,
        description="The quotation form's scopes (#1341). Absent leaves every scope as it is.",
    )
    remove_scope_ids: Optional[List[str]] = Field(
        default=None,
        description="Saved scopes Edit quotation deletes (#1341). Refused with 422 "
        "quotation_scope_issued for a scope any version of which has been issued.",
    )


class ProjectQuotationScopeCreate(BaseModel):
    scope_label: str = Field(
        min_length=1, max_length=150, description="e.g. Townhouse, Guard House, Reception."
    )
    series_id: Optional[str] = None
    notes: Optional[str] = None


class ProjectQuotationScopeUpdate(BaseModel):
    scope_label: Optional[str] = Field(default=None, min_length=1, max_length=150)
    sort_order: Optional[int] = None
    notes: Optional[str] = None


class ProjectQuotationScopeSummary(BaseModel):
    """A tab: enough to render it and its total, without its lines."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    scope_label: str
    sort_order: int = 0
    outcome: str = "open"
    current_version_id: Optional[str] = None
    current_version_no: Optional[int] = None
    line_count: int = 0
    # Rate-only lines excluded, so this agrees with the footer under the money column.
    scope_total: Decimal = Decimal("0.00")


class ProjectQuotationIssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    issue_no: int
    our_ref_text: Optional[str] = None
    issued_at: Optional[datetime] = None
    issued_by: Optional[str] = None
    issued_by_name: Optional[str] = None
    grand_total: Decimal = Decimal("0.00")
    scope_count: int = 0

    # The customer's acceptance lives on the issue, but the screen watching for it is the
    # document, so it has to travel with the issue history the document reads.
    customer_signature: Optional["QuotationSignatureResponse"] = None
    accepted_at: Optional[datetime] = None
    is_accepted: bool = False

    # The customer's other answer, travelling with the same issue history: the salesperson reads
    # the feedback on the Signatures tab and revises by hand.
    changes_requested_at: Optional[datetime] = None
    changes_requested_note: Optional[str] = None
    changes_requested_by_name: Optional[str] = None
    is_changes_requested: bool = False


class ProjectQuotationDocumentResponse(ProjectQuotationDocumentBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    document_no: str
    # What the customer quotes back: the number plus the revision they hold.
    our_ref: Optional[str] = None

    recipient_party_id: Optional[str] = None
    recipient_name_snapshot: Optional[str] = None
    recipient_address_snapshot: Optional[str] = None
    recipient_phone_snapshot: Optional[str] = None

    scopes: List[ProjectQuotationScopeSummary] = Field(default_factory=list)
    grand_total: Decimal = Decimal("0.00")

    issue_count: int = 0
    current_issue_no: Optional[int] = None
    is_issued: bool = False

    # ---- where the customer left the revision they currently hold (S17) ----
    # Declared here as well as built into `serialize_document`, because a field the response
    # model does not know about is dropped on the way out however correctly the service computed
    # it. `customer_decision` is the resolved answer - "accepted" | "changes_requested" | None -
    # so the document banner and the project's quotation list cannot disagree about which of the
    # two stamps wins.
    accepted_at: Optional[datetime] = None
    changes_requested_at: Optional[datetime] = None
    changes_requested_note: Optional[str] = None
    changes_requested_by_name: Optional[str] = None
    customer_decision: Optional[str] = None

    # AC-H1 gates issuing on the signature, so it has to survive a page refresh. Forward-declared
    # because the signature schema is defined further down; resolved by the model_rebuild below.
    signatory_signature: Optional["QuotationSignatureResponse"] = None
    is_signed: bool = False

    # ---- the price-floor approval gate (S14-S16) ----
    # NULL is the normal answer: a quotation only enters the approval graph when a line priced
    # below its floor makes a manager necessary. Declared here as well as built into
    # `serialize_document`, because a field the response model does not know about is silently
    # dropped on the way out however correctly the service computed it.
    approval_status_id: Optional[str] = None
    approval_status_key: Optional[str] = None
    approval_status_label: Optional[str] = None
    approval_rejected_reason: Optional[str] = None
    requires_approval: bool = False
    below_floor_line_count: int = 0

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class QuotationApprovalMoveRequest(BaseModel):
    """The salesperson's own move along the approval graph.

    A status id rather than a key, so the client fires the edge the engine actually resolved
    rather than one it guessed at from a name an admin may have re-keyed.
    """

    to_status_id: str = Field(min_length=1)


class QuotationRejectRequest(BaseModel):
    """Sending a below-floor quotation back, with the reason that makes it actionable.

    Required at the schema as well as in the service: a client that omits it entirely should be
    told which field is missing, and a client that sends whitespace should be told to say
    something (which is the service's answer, because only it knows a stripped string is empty).
    """

    reason: str = Field(min_length=1, max_length=1000)


class QuotationReviseRequest(BaseModel):
    """Reopening a pending or approved quotation. The reason is checked by the service, which
    answers a blank one with a code the screen can render."""

    reason: Optional[str] = Field(default=None, max_length=1000)


# ------------------------------------------------ cover letter and terms templates


class QuotationTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    body_html: str = Field(min_length=1)


class QuotationTemplateCreate(QuotationTemplateBase):
    kind: str = Field(description="cover_letter | terms")
    # Optional because the FIRST template of a kind is active on arrival whatever the caller
    # says: a company holding a template with nothing active renders an empty letter.
    is_active: Optional[bool] = None


class QuotationTemplateUpdate(BaseModel):
    """The wording and the name only.

    `kind` is not editable (a letter that became terms would vanish from the section it was
    written for) and neither is `is_active`: switching the active template is its own act with
    its own route, so one code path deactivates the incumbent.
    """

    name: Optional[str] = Field(default=None, max_length=150)
    body_html: Optional[str] = None


class QuotationTemplateResponse(QuotationTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    is_active: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class QuotationMergeFieldResponse(BaseModel):
    """One entry in the merge-field picker, served from the backend registry.

    The placeholder is built server-side so the token syntax has exactly one definition; a picker
    that assembled "{{" + token itself would be a second one, free to drift.
    """

    token: str
    placeholder: str
    label: str
    example: str


# ------------------------------------------------------------- signing


class QuotationSignatureRequest(BaseModel):
    """One captured signature. Drawn, typed or initialled, all arriving as one PNG data URI."""

    signer_name: Optional[str] = Field(None, max_length=200)
    mode: str = Field("draw", description="draw | type | initials")
    image_data_uri: str = Field(min_length=1)
    gps_lat: Optional[Decimal] = None
    gps_lng: Optional[Decimal] = None


class QuotationSignAcceptRequest(QuotationSignatureRequest):
    pass


class QuotationSignChangesRequest(BaseModel):
    """The customer's other answer: they will not sign this as it stands, and here is why.

    `note` is required and length-bounded rather than optional. A request with no words is not a
    request - the salesperson cannot act on it, and it would render on the customer's own page as
    a settled outcome. Whitespace-only gets past `min_length` and is refused by the service, so
    both spellings of "empty" end as 422.
    """

    note: str = Field(min_length=1, max_length=4000)
    # No signature is captured here, so this is the only name the request can carry.
    requester_name: Optional[str] = Field(None, max_length=200)


class QuotationSignatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    signer_name: Optional[str] = None
    mode: str = "draw"
    image_data_uri: Optional[str] = None
    signed_at: Optional[datetime] = None
    ip_address: Optional[str] = None
    # Null when the browser refused. The screen shows "-" rather than hiding the field.
    gps_lat: Optional[Decimal] = None
    gps_lng: Optional[Decimal] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gps_place(self) -> Optional[str]:
        """`"Kajang, Selangor"`: the nearest known town to the coordinates, or None.

        Derived here rather than stored, so it is one offline lookup shared by every surface. The
        PDF calls ``geo_places`` directly and the frontend consumes this field; giving the browser
        its own copy of the place table would let the screen and the document drift apart on where
        somebody signed. Null whenever nothing known is close enough to name honestly, in which
        case the screen shows the coordinates alone.

        The coordinates themselves are never replaced by this. They are the evidence; this is the
        convenience.
        """
        return geo_places.place_label(self.gps_lat, self.gps_lng)


# Both refer to the signature above by name, so they cannot be built until here.
ProjectQuotationIssueResponse.model_rebuild()
ProjectQuotationDocumentResponse.model_rebuild()


class QuotationSignScopeLine(BaseModel):
    item_label: Optional[str] = None
    description: Optional[str] = None
    technical_spec: Optional[str] = None
    brand: Optional[str] = None
    product_code: Optional[str] = None
    quantity: Decimal = Decimal("0")
    unit_price: Decimal = Decimal("0")
    complete_set: Optional[str] = None
    band_label: Optional[str] = None
    is_rate_only: bool = False
    amount: Optional[Decimal] = None


class QuotationSignScope(BaseModel):
    scope_label: str
    scope_total: Decimal = Decimal("0")
    lines: List[QuotationSignScopeLine] = Field(default_factory=list)


class QuotationSignPageResponse(BaseModel):
    """The read-only quotation a customer sees on the counter-sign link.

    Deliberately does NOT carry ids: the page needs no handle on anything, and a public payload
    that leaks internal identifiers invites somebody to try them elsewhere.
    """

    our_ref: Optional[str] = None
    issue_no: int
    doc_date: Optional[date] = None
    subject_title: Optional[str] = None
    sender_name: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_address: Optional[str] = None
    attn_name: Optional[str] = None
    cover_letter: Optional[str] = None
    terms: Optional[str] = None
    signatory_name: Optional[str] = None
    scopes: List[QuotationSignScope] = Field(default_factory=list)
    grand_total: Decimal = Decimal("0")
    sorento_signature: Optional[QuotationSignatureResponse] = None
    customer_signature: Optional[QuotationSignatureResponse] = None
    accepted_at: Optional[datetime] = None
    is_accepted: bool = False
    # Both decisions travel on the page, and the page renders whichever one was reached. A
    # customer who asked for changes and then signed sees Accepted; the request stays as history.
    changes_requested_at: Optional[datetime] = None
    changes_requested_note: Optional[str] = None
    changes_requested_by_name: Optional[str] = None
    is_changes_requested: bool = False
