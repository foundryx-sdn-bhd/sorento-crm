"""Request and response shapes of the `sales` module (plan 3.7, 3.8; slices S6, S1 and S2)."""
from __future__ import annotations

import uuid
import uuid as _uuid
from datetime import date as DateType
from datetime import datetime
from decimal import Decimal
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


def _clean_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Team name is required.")
    return cleaned


def _require_uuid(value: Optional[str]) -> Optional[str]:
    """An id field is a UUID or the request is malformed - 422, not a 500 three calls
    later when a raw-SQL-shaped string reaches a `::uuid` cast (Phase 3 fix S3)."""
    if value is None:
        return None
    try:
        _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("must be a valid id")
    return value


def _require_title(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Title is required.")
    return cleaned


class SalesTeamCreate(BaseModel):
    name: str = Field(..., max_length=120)
    sales_agent_ids: List[str] = Field(default_factory=list)
    is_active: bool = True
    #: The day a picked agent who is in another team starts counting here (T2). Default today.
    moves_on: Optional[DateType] = None
    #: One of the team's agents (W1); one not in `sales_agent_ids` is added to them.
    leader_sales_agent_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        return _clean_name(value)


class SalesTeamUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    is_active: Optional[bool] = None
    #: Optional: the team page saves the name, Active and the agents in ONE transaction, so a
    #: refused rename cannot leave the agents half-saved (review round 1, N1).
    sales_agent_ids: Optional[List[str]] = None
    moves_on: Optional[DateType] = None
    #: Sent: set the leader (null clears it). Not sent: the leader stays, unless left out (W1).
    leader_sales_agent_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        return _clean_name(value)


class SalesTeamMembersUpdate(BaseModel):
    sales_agent_ids: List[str]
    moves_on: Optional[DateType] = None


class SalesTeamAgentRef(BaseModel):
    sales_agent_id: str
    label: str


class SalesTeamMemberResponse(BaseModel):
    sales_agent_id: str
    code: str
    name: Optional[str] = None
    label: str
    valid_from: Optional[DateType] = None
    valid_to: Optional[DateType] = None
    #: Left the team on or before the date shown; drawn muted with a "Left <date>" pill (S6-15).
    left: bool = False


class SalesTeamMove(BaseModel):
    sales_agent_id: str
    label: str
    from_team_id: str
    from_team_name: str


class SalesTeamListItem(BaseModel):
    id: str
    name: str
    is_active: bool
    leader_sales_agent_id: Optional[str] = None
    member_count: int
    members: List[SalesTeamAgentRef]
    #: Team targets with a period containing today (S1, "Targets now" column).
    targets_now: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SalesTeamDetail(BaseModel):
    id: str
    name: str
    is_active: bool
    #: The team's leader now, one of its agents (W1); None when none is picked.
    leader_sales_agent_id: Optional[str] = None
    leader_label: Optional[str] = None
    #: Members on the date shown, not counting those who have left.
    member_count: int
    #: The date the members are read for (`on`, default today).
    on: DateType
    members: List[SalesTeamMemberResponse]
    #: Agents this write moved out of another team; empty on a read.
    moved: List[SalesTeamMove] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SalesTeamAgentOption(BaseModel):
    id: str
    code: str
    label: str
    #: The team the agent is in now, so the picker can say "(now in Central)".
    team_id: Optional[str] = None
    team_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Opportunities (plan 3.4, 3.5; slice S2)
# ---------------------------------------------------------------------------


class SalesOpportunityLineInput(BaseModel):
    product_id: str
    qty: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    #: Left out or null: the product's list price, the price the dealer flyer prints (F7).
    unit_price: Optional[Decimal] = Field(None, ge=0, max_digits=15, decimal_places=2)

    @field_validator("product_id")
    @classmethod
    def _product_id(cls, value):
        return _require_uuid(value)


#: Shared bounds for both CRM and portal create/update bodies (Phase 3 fix S3): an
#: unbounded `expected_amount`/`lines` list is a 500 or a silent truncation somewhere
#: downstream, not a validation error where a caller can see and fix it.
_AMOUNT_FIELD_OPTIONAL = Field(None, ge=0, max_digits=15, decimal_places=2)
_LINES_FIELD = Field(None, max_length=100)


class SalesOpportunityCreate(BaseModel):
    """CRM create (S2-7). Portal create is `PortalSalesOpportunityCreate` below - it has
    no `sales_agent_id`/`source`/`sales_order_id` fields, so those keys in a portal body
    are ignored rather than accepted (section 16)."""

    title: str = Field(..., min_length=1, max_length=200)
    #: Left out: the sum of the line amounts (F7).
    expected_amount: Optional[Decimal] = _AMOUNT_FIELD_OPTIONAL
    expected_close_date: DateType
    customer_id: Optional[str] = None
    prospect_name: Optional[str] = Field(None, max_length=200)
    #: CRM only: stamped from the customer when omitted (S2-7).
    sales_agent_id: Optional[str] = None
    lines: Optional[List[SalesOpportunityLineInput]] = _LINES_FIELD

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        return _require_title(value)

    @field_validator("customer_id", "sales_agent_id")
    @classmethod
    def _ids(cls, value):
        return _require_uuid(value)


class SalesOpportunityUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    expected_amount: Optional[Decimal] = _AMOUNT_FIELD_OPTIONAL
    expected_close_date: Optional[DateType] = None
    customer_id: Optional[str] = None
    prospect_name: Optional[str] = Field(None, max_length=200)
    sales_agent_id: Optional[str] = None
    status_id: Optional[str] = None
    lost_reason: Optional[str] = Field(None, max_length=150)
    #: Won only; must be the same customer as the opportunity (S2-7).
    sales_order_id: Optional[str] = None
    lines: Optional[List[SalesOpportunityLineInput]] = _LINES_FIELD

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        return _require_title(value) if value is not None else value

    @field_validator("customer_id", "sales_agent_id", "status_id", "sales_order_id")
    @classmethod
    def _ids(cls, value):
        return _require_uuid(value)


class PortalSalesOpportunityCreate(BaseModel):
    """No `sales_agent_id` (from the token only, S2-3), no `source`, no
    `sales_order_id` (only the CRM sends it, section 16)."""

    title: str = Field(..., min_length=1, max_length=200)
    #: Left out: the sum of the line amounts (F7).
    expected_amount: Optional[Decimal] = _AMOUNT_FIELD_OPTIONAL
    expected_close_date: DateType
    customer_id: Optional[str] = None
    prospect_name: Optional[str] = Field(None, max_length=200)
    lines: Optional[List[SalesOpportunityLineInput]] = _LINES_FIELD

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        return _require_title(value)

    @field_validator("customer_id")
    @classmethod
    def _ids(cls, value):
        return _require_uuid(value)


class PortalSalesOpportunityUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    expected_amount: Optional[Decimal] = _AMOUNT_FIELD_OPTIONAL
    expected_close_date: Optional[DateType] = None
    customer_id: Optional[str] = None
    prospect_name: Optional[str] = Field(None, max_length=200)
    status_id: Optional[str] = None
    lost_reason: Optional[str] = Field(None, max_length=150)
    lines: Optional[List[SalesOpportunityLineInput]] = _LINES_FIELD

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        return _require_title(value) if value is not None else value

    @field_validator("customer_id", "status_id")
    @classmethod
    def _ids(cls, value):
        return _require_uuid(value)


class SalesOpportunityLineResponse(BaseModel):
    id: str
    product_id: str
    product_code: str
    product_name: str
    qty: Decimal
    unit_price: Optional[Decimal] = None
    #: qty x unit_price, null when the line has no price.
    line_amount: Optional[Decimal] = None


class SalesOpportunityTransition(BaseModel):
    to_status_id: str
    key: str
    label: str


class SalesOpportunityResponse(BaseModel):
    id: str
    opportunity_no: str
    title: str
    customer_id: Optional[str] = None
    customer_code: Optional[str] = None
    customer_name: Optional[str] = None
    prospect_name: Optional[str] = None
    sales_agent_id: Optional[str] = None
    sales_agent_label: Optional[str] = None
    status_id: Optional[str] = None
    stage_key: Optional[str] = None
    stage_label: Optional[str] = None
    win_probability: Optional[Decimal] = None
    outcome: str
    expected_amount: Decimal
    expected_close_date: DateType
    lost_reason: Optional[str] = None
    lost_reason_label: Optional[str] = None
    sales_order_id: Optional[str] = None
    sales_order_no: Optional[str] = None
    source: str
    created_by_label: Optional[str] = None
    created_by_contact_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    stage_changed_at: Optional[datetime] = None
    lines: List[SalesOpportunityLineResponse] = Field(default_factory=list)
    available_transitions: List[SalesOpportunityTransition] = Field(default_factory=list)


class SalesOpportunityCustomerOptionItem(BaseModel):
    customer_id: str
    customer_code: str
    customer_name: str


class SalesOpportunityProspectOption(BaseModel):
    name: str


class SalesOpportunityBlockedOption(BaseModel):
    name: str
    message: str


class SalesOpportunityCustomerOptionsResponse(BaseModel):
    items: List[SalesOpportunityCustomerOptionItem]
    prospect: Optional[SalesOpportunityProspectOption] = None
    blocked: Optional[SalesOpportunityBlockedOption] = None


class SalesOpportunityStageOption(BaseModel):
    id: str
    key: str
    label: str
    win_probability: Optional[Decimal] = None
    is_active: bool
    is_terminal: bool


class SalesOpportunityLostReasonOption(BaseModel):
    value: str
    label: str


class SalesOpportunityMeta(BaseModel):
    stages: List[SalesOpportunityStageOption]
    lost_reasons: List[SalesOpportunityLostReasonOption]


class SalesOpportunitySalesOrderOption(BaseModel):
    id: str
    so_number: str
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    order_date: Optional[DateType] = None


# --------------------------------------------------------------------------------------
# Targets (slice S1; plan 3.1, 3.2, 3.8, 16.3)
# --------------------------------------------------------------------------------------

def _uuid_str(value):
    """A UUID, kept as the canonical string the models use. Anything else is a 422."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        raise ValueError("must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError("must be a UUID") from exc


#: An id sent by the client: validated as a UUID so a bad one is 422, never a database error.
UuidStr = Annotated[str, BeforeValidator(_uuid_str)]

Metric = Literal["amount", "quantity"]
Basis = Literal["ordered", "delivered"]
ProductScope = Literal["all", "categories", "products", "brands"]
SplitUnit = Literal["day", "week", "month"]


def _clean_target_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Target name is required.")
    return cleaned


class SalesTargetAgentFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sales_agent_id: UuidStr
    #: The agent's figure for each period of the team target.
    target_value: float = Field(..., ge=0)


CommissionMethod = Literal["none", "marginal", "retroactive"]


ThresholdUnit = Literal["pct", "amount", "points"]
Rounding = Literal["sen", "ringgit"]


class SalesTargetTierRateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UuidStr
    rate: float = Field(..., ge=0, le=99999)


class SalesTargetTierIn(BaseModel):
    """One commission tier (plan 3.3, UAC S4-1; one engine, lane SALES-TARGET-DEALER)."""

    model_config = ConfigDict(extra="forbid")

    #: `pct` targets: the achievement % the tier starts at; 0 is "from the first ringgit".
    from_pct: Optional[float] = Field(None, ge=0, le=99999)
    #: `amount` / `points` targets: the threshold in RM (units) or points.
    threshold: Optional[float] = Field(None, ge=0, le=9999999999999)
    #: Amount target: % of the achieved amount. Quantity target: RM per unit.
    rate: float = Field(0, ge=0, le=99999)
    #: RM paid once per period when the tier is reached.
    bonus_amount: Optional[float] = Field(None, ge=0)
    #: A "+ Additional" line, paid on top of the main tier (they stack).
    is_additional: bool = False
    #: `tiers_per_period` targets: the start of the period this tier belongs to.
    period_start: Optional[DateType] = None
    #: Dealer targets: a rate per counted account; only these accounts earn on the tier.
    account_rates: Optional[List[SalesTargetTierRateIn]] = Field(None, max_length=50)


class SalesTargetFigureIn(BaseModel):
    """One period's figure in the batched save (the owner's retest of 27 Sep, F1)."""

    model_config = ConfigDict(extra="forbid")

    period_id: UuidStr
    target_value: float = Field(..., ge=0)


class SalesTargetNewAgentFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: DateType
    target_value: float = Field(..., ge=0)


class SalesTargetNewAgent(BaseModel):
    """A member of the team with no figure yet, given one in the batched save (F1)."""

    model_config = ConfigDict(extra="forbid")

    sales_agent_id: UuidStr
    #: By the team period's start; a period left out starts at 0.
    figures: List[SalesTargetNewAgentFigure] = Field(default_factory=list, max_length=104)


class SalesTargetCreate(BaseModel):
    """`extra="forbid"`: the round 3 fields `start_month`, `months`, `periodicity` are 422 (S1-1)."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: Literal["agent", "team", "dealer"]
    sales_agent_id: Optional[UuidStr] = None
    sales_team_id: Optional[UuidStr] = None
    #: Dealer targets: the accounts counted, in order.
    customer_ids: Optional[List[UuidStr]] = Field(None, max_length=50)
    #: Dealer targets: the account paid; defaults to the first counted account.
    payout_customer_id: Optional[UuidStr] = None
    name: str = Field(..., max_length=120)
    metric: Metric
    basis: Basis = "ordered"
    product_scope: ProductScope = "all"
    category_ids: List[UuidStr] = Field(default_factory=list, max_length=500)
    product_ids: List[UuidStr] = Field(default_factory=list, max_length=500)
    brand_ids: List[UuidStr] = Field(default_factory=list, max_length=500)
    #: Both required; optional here only so a missing one is refused by the service in plain
    #: words ("A target needs both a start date and an end date."), not "Field required".
    start_date: Optional[DateType] = None
    end_date: Optional[DateType] = None
    split_every: Optional[int] = Field(None, ge=1, le=99)
    split_unit: Optional[SplitUnit] = None
    #: Agent targets: the figure written to every period. Rejected on a team target (T3).
    target_value: Optional[float] = Field(None, ge=0)
    #: Team targets: one child agent target per entry; the team figure is their sum (S1-27).
    agent_figures: Optional[List[SalesTargetAgentFigure]] = Field(None, max_length=200)
    commission_method: CommissionMethod = "none"
    threshold_unit: ThresholdUnit = "pct"
    rounding: Rounding = "sen"
    tiers_per_period: bool = False
    tiers: List[SalesTargetTierIn] = Field(default_factory=list, max_length=600)

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        return _clean_target_name(value)


#: What a person reads for each header field a PATCH may not empty (no snake_case in the UI).
_REQUIRED_HEADER_LABELS = {
    "name": "Target name",
    "metric": "Measure",
    "basis": "Counts",
    "product_scope": "Applies to",
    "commission_method": "How tiers pay",
    "threshold_unit": "Threshold in",
    "rounding": "Round to",
    "tiers_per_period": "Tiers",
    "customer_ids": "Accounts",
    "tiers": "Tiers",
    "figures": "Figures",
    "new_agents": "Agent figures",
}


class SalesTargetUpdate(BaseModel):
    """The header. The subject is not editable (not in this schema, so it is 422)."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, max_length=120)
    metric: Optional[Metric] = None
    basis: Optional[Basis] = None
    product_scope: Optional[ProductScope] = None
    category_ids: Optional[List[UuidStr]] = Field(None, max_length=500)
    product_ids: Optional[List[UuidStr]] = Field(None, max_length=500)
    brand_ids: Optional[List[UuidStr]] = Field(None, max_length=500)
    start_date: Optional[DateType] = None
    end_date: Optional[DateType] = None
    #: Sent as null (both): the split is turned off.
    split_every: Optional[int] = Field(None, ge=1, le=99)
    split_unit: Optional[SplitUnit] = None
    commission_method: Optional[CommissionMethod] = None
    threshold_unit: Optional[ThresholdUnit] = None
    rounding: Optional[Rounding] = None
    tiers_per_period: Optional[bool] = None
    #: Dealer targets: replaces the counted accounts.
    customer_ids: Optional[List[UuidStr]] = Field(None, max_length=50)
    #: Dealer targets: sent as null, payment goes back to the first counted account.
    payout_customer_id: Optional[UuidStr] = None
    #: Replaces every tier; `[]` removes them.
    tiers: Optional[List[SalesTargetTierIn]] = Field(None, max_length=600)
    #: Every changed figure in one request (F1): this target's periods, or on a team target its
    #: agents' periods. Applied before the header, so a date change keeps them (S1-5).
    figures: Optional[List[SalesTargetFigureIn]] = Field(None, max_length=5000)
    #: Team targets: members with no figure yet, given one (F1).
    new_agents: Optional[List[SalesTargetNewAgent]] = Field(None, max_length=200)

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        return _clean_target_name(value)

    @model_validator(mode="after")
    def _no_null_for_required(self):
        # Leaving a field out keeps it; sending null for one the header cannot be without is
        # a 422 here, never a NOT NULL violation at the database. Only the split may be null
        # (it turns the split off).
        # The dates are the service's (one plain-words message for either end, F6).
        for key, label in _REQUIRED_HEADER_LABELS.items():
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{label} cannot be empty.")
        return self


class SalesTargetPeriodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_value: float = Field(..., ge=0)


class SalesTargetChildCreate(BaseModel):
    """Add figure: a member of the team with no figure yet gets their own child (S1-28)."""

    model_config = ConfigDict(extra="forbid")

    sales_agent_id: UuidStr
    target_value: float = Field(0, ge=0)


class SalesTargetMemberRef(BaseModel):
    sales_agent_id: str
    label: str


class SalesTargetRow(BaseModel):
    """One target period containing `on`, or a "No target" row (every target field null)."""

    target_id: Optional[str] = None
    target_no: Optional[str] = None
    name: Optional[str] = None
    subject_kind: str
    sales_agent_id: Optional[str] = None
    sales_team_id: Optional[str] = None
    subject_label: str
    #: Agent rows: the team whose membership covers `on`. Team rows: the team itself.
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    #: Agent rows under a team filter: the last day of a stay that ended on or before `on`.
    left_on: Optional[DateType] = None
    #: Team rows: the members on `on`, for the Agents pills. Null on agent rows.
    members: Optional[List[SalesTargetMemberRef]] = None
    metric: Optional[str] = None
    basis: Optional[str] = None
    product_scope: Optional[str] = None
    scope_labels: List[str] = Field(default_factory=list)
    period_id: Optional[str] = None
    period_start: Optional[DateType] = None
    period_end: Optional[DateType] = None
    #: The whole list (`all=true`): the target's own first day. Its last day is `end_date`.
    start_date: Optional[DateType] = None
    end_date: Optional[DateType] = None
    target_value: Optional[float] = None
    achieved_value: Optional[float] = None
    achieved_pct: Optional[float] = None
    parent_target_id: Optional[str] = None
    #: Dealer rows: the counted accounts' labels and the paid account's.
    account_labels: Optional[List[str]] = None
    account_codes: Optional[List[str]] = None
    payout_label: Optional[str] = None
    threshold_unit: Optional[str] = None
    #: The current period's commission (null with no tiers).
    commission_earned: Optional[float] = None


class SalesTargetList(BaseModel):
    on: DateType
    rows: List[SalesTargetRow]
    #: Non-cancelled lines with no agent, ordered in the calendar month of `on` (S1-14).
    unassigned_amount: float
    #: Active agents with no team membership covering `on`.
    no_team_count: int


class SalesTargetScopeItem(BaseModel):
    #: The category, product or brand id.
    id: str
    #: `category`, `product` or `brand`, so the record's pickers can seed their selected names.
    kind: str
    #: "CODE - Name", never an id.
    label: str


class SalesTargetParentRef(BaseModel):
    id: str
    name: str
    target_no: str


class SalesTargetAccountOut(BaseModel):
    customer_id: str
    code: str
    name: str
    #: "A/C I" when the account has a level, else the customer's name.
    label: str
    achieved_value: Optional[float] = None


class SalesTargetCommissionLine(BaseModel):
    #: `main` or `additional`.
    kind: str
    threshold_rm: Optional[float] = None
    amount: float


class SalesTargetPeriodOut(BaseModel):
    id: str
    period_start: DateType
    period_end: DateType
    target_value: float
    achieved_value: float
    achieved_pct: Optional[float] = None
    #: The period containing `on`.
    is_current: bool
    #: Plan 3.3: null when the target has no tiers.
    commission_earned: Optional[float] = None
    bonus_earned: Optional[float] = None
    commission_lines: List[SalesTargetCommissionLine] = Field(default_factory=list)
    #: 1-based main tier reached, null below the first.
    tier_reached: Optional[int] = None
    #: Points targets: the RM per point in force on the period's last day.
    point_ratio: Optional[float] = None
    ratio_missing: bool = False
    #: Dealer targets: each counted account's achievement in the period.
    accounts: List[SalesTargetAccountOut] = Field(default_factory=list)


class SalesTargetTierRateOut(BaseModel):
    customer_id: str
    rate: float


class SalesTargetTierOut(BaseModel):
    from_pct: Optional[float] = None
    threshold: Optional[float] = None
    rate: Optional[float] = None
    bonus_amount: Optional[float] = None
    is_additional: bool = False
    period_start: Optional[DateType] = None
    account_rates: Optional[List[SalesTargetTierRateOut]] = None


class SalesTargetChildPeriod(BaseModel):
    id: str
    period_start: DateType
    target_value: float


class SalesTargetChild(BaseModel):
    target_id: str
    target_no: str
    sales_agent_id: str
    label: str
    periods: List[SalesTargetChildPeriod]


class SalesTargetDetail(BaseModel):
    id: str
    target_no: str
    name: str
    subject_kind: str
    sales_agent_id: Optional[str] = None
    sales_team_id: Optional[str] = None
    subject_label: str
    #: Agent targets: the team the agent is in on `on`.
    subject_team_name: Optional[str] = None
    parent: Optional[SalesTargetParentRef] = None
    metric: str
    basis: str
    product_scope: str
    start_date: DateType
    end_date: DateType
    split_every: Optional[int] = None
    split_unit: Optional[str] = None
    #: "Ordered", "Delivered", or "Delivered (by DO date)" once any DO line is linked.
    counts_label: str
    scope: List[SalesTargetScopeItem]
    periods: List[SalesTargetPeriodOut]
    children: List[SalesTargetChild]
    members_without_figure: List[SalesTargetMemberRef]
    child_count: int
    commission_method: str = "none"
    threshold_unit: str = "pct"
    rounding: str = "sen"
    tiers_per_period: bool = False
    tiers: List[SalesTargetTierOut] = Field(default_factory=list)
    #: Dealer targets: the counted accounts and the paid one.
    accounts: List[SalesTargetAccountOut] = Field(default_factory=list)
    payout_account: Optional[SalesTargetAccountOut] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SalesTargetOptionAgent(BaseModel):
    id: str
    code: str
    label: str
    team_id: Optional[str] = None
    team_name: Optional[str] = None


class SalesTargetOptionMember(BaseModel):
    sales_agent_id: str
    label: str
    valid_from: Optional[DateType] = None
    valid_to: Optional[DateType] = None


class SalesTargetOptionTeam(BaseModel):
    id: str
    name: str
    is_active: bool
    #: Every stay, so the modal can offer the members overlapping the chosen range.
    members: List[SalesTargetOptionMember]


class SalesTargetOptionCategory(BaseModel):
    id: str
    label: str
    parent_category_id: Optional[str] = None


class SalesTargetOptionBrand(BaseModel):
    id: str
    label: str


class SalesPointRatioIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_from: DateType
    rm_per_point: float = Field(..., gt=0, le=1000000)


class SalesPointRatioOut(BaseModel):
    id: str
    effective_from: DateType
    rm_per_point: float


class SalesPointRatioList(BaseModel):
    rows: List[SalesPointRatioOut]


class SalesTargetImportRow(BaseModel):
    sheet: str
    row: int
    code: Optional[str] = None
    name_in_file: Optional[str] = None
    account_in_file: Optional[str] = None
    #: The CRM customer's name, when the code matched.
    customer: Optional[str] = None
    #: `mapped` or `not_representable`.
    status: str
    reason: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    target_keys: List[str] = Field(default_factory=list)
    target_nos: List[str] = Field(default_factory=list)


class SalesTargetImportSummary(BaseModel):
    rows: int
    mapped: int
    not_representable: int
    targets_new: int
    targets_updated: int


class SalesTargetImportResult(BaseModel):
    dry_run: bool
    summary: SalesTargetImportSummary
    rows: List[SalesTargetImportRow]
    #: Sheets the reader skipped, with why.
    notes: List[str] = Field(default_factory=list)


class SalesTargetOptions(BaseModel):
    agents: List[SalesTargetOptionAgent]
    teams: List[SalesTargetOptionTeam]
    categories: List[SalesTargetOptionCategory]
    brands: List[SalesTargetOptionBrand]


# --------------------------------------------------------------------------------------
# Portal My target (fix lane round 2, F2)
# --------------------------------------------------------------------------------------


class PortalMyTargetOpportunity(BaseModel):
    id: str
    opportunity_no: str
    title: str
    customer_or_prospect: Optional[str] = None
    stage_label: Optional[str] = None
    expected_close_date: DateType
    #: What it adds if won: the expected amount, or the lines' quantity on a quantity target.
    value: Decimal


class PortalMyTarget(BaseModel):
    target_id: str
    target_no: str
    name: str
    metric: str
    basis: str
    counts_label: str
    product_scope: str
    scope_labels: List[str]
    start_date: DateType
    end_date: DateType
    target_value: Decimal
    achieved_value: Decimal
    gap_value: Decimal
    pipeline_value: Decimal
    projected_value: Decimal
    short_value: Decimal
    opportunities: List[PortalMyTargetOpportunity]


class PortalMyTargets(BaseModel):
    today: DateType
    targets: List[PortalMyTarget]
