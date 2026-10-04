"""The chatbot turn engine's vocabularies and payload shapes - ONE declaration each.

H28 is enum drift: n8n listed `branch_kind` in the router, again in the Switch it fed,
and again in `escalate-catalog`, and the three lists stopped agreeing without anything
failing. Every vocabulary the engine speaks is therefore declared exactly once here, as a
tuple plus the `Literal` built from it, and `tests/chatbot/test_contracts.py` greps the
package for a second copy.

Naming follows the wire, not Python taste: `qf` keys, `ctx` keys and `session_vars`
members keep the names n8n uses, because a rename is a contract break with the caller and
with 1,535 captured fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.chatbot import jsc
from app.services.chatbot.turn import policy_rows


class ParserOutputError(ValueError):
    """The parser's emission SUCCEEDED (a real answer came back) but its SHAPE is
    malformed - a declared key holding the wrong container type. Distinct from
    `head.parser.ParserError` (the call itself failed - no answer at all).

    Declared here, not in `head/parser.py`, because `turn/apply.py` (pure, AC-1520 -
    no imports from `head`/`dialogue`/`tail`/`engine`) is the reader that validates a
    malformed emission today (`entities` must be an array) and needs to raise it
    without crossing that boundary. `head/parser.py` re-exports the same class so a
    caller that imports it from there (the old `head/output_exchange.py`'s own name
    for this error, AC-1592 test triage) still finds it.
    """


# --------------------------------------------------------------------------- #
# Domain vocabulary (D9, AC-1501/AC-1594). Used to live here as `DOMAIN_SPEC`, one
# hand-authored dict with five views over it; the chatbot turn re-architecture (S0/S6)
# moved the data to the `chatbot_domains` / `chatbot_entity_kinds` tables, loaded at
# runtime as `turn.policy.Policy` (`turn.policy.load_policy` / `default_policy`). What
# stays here is only what a live database read cannot supply: `DOMAIN_HINTS` /
# `INTENT_HINTS` below build `Literal` types, which Python resolves at IMPORT time, so
# they read the frozen seed (`turn.policy_rows.DEFAULT_DOMAIN_ROWS` - the SAME data a
# migrated database is seeded from, one copy) rather than a database row.
#
# Deliberately NOT folded into the policy tables, and this is unchanged from D9's own
# discipline: the tables that carry a per-domain HAZARD rather than a per-domain fact.
# `AXIS_BY_DOMAIN`, `DOMAIN_SUBJECT_AXIS`, `DOMAIN_SUBJECT_HINT`, `DOMAIN_BLOCKED_HINTS`,
# `MEMBER_OFFER_FILTER_HINTS` and `DOMAIN_BROADEN_BLOCKED_HINTS` each carry a hand-earned
# annotation naming the live turn that put a row there (owner rulings K2 to K4, C1, the
# 2026-08-09 promotion-brand leak). They stay where they are, in `head/output_exchange.py`,
# next to their evidence.
# --------------------------------------------------------------------------- #

# Tools the chatbot MAY call that no domain answers FROM. Two kinds, and neither is an
# oversight: reads for surfaces the chatbot does not route to by domain at all (projects,
# complaints, SLA), and cross-cutting helpers a lane reaches for by name rather than by
# cosine search (`crm_lookup_resolve`, `user_guides_read`,
# `crm_system_tool_capabilities_summary`). Named rather than left implicit so
# `CHATBOT_READ_ONLY_TOOLS` below is the exact union of "claimed by exactly one domain"
# and "claimed by nobody, on purpose" - which is what makes the allow-list a derived view
# instead of a third list to keep in step.
UNDOMAINED_CHATBOT_TOOLS: tuple[str, ...] = (
    "crm_complaint_analytics",
    "crm_complaints_list",
    "crm_lookup_resolve",
    "crm_project_detail",
    "crm_project_forecast",
    "crm_project_quotations_list",
    "crm_projects_list",
    "crm_sla_conversation_event_logs_list",
    "crm_sla_conversation_tracking_dashboard",
    "crm_sla_conversation_tracking_list",
    "crm_system_tool_capabilities_summary",
    "user_guides_read",
)

# --------------------------------------------------------------------------- #
# Parser vocabularies (AC-109). Values are the parser prompt's own OUTPUT block.
# --------------------------------------------------------------------------- #

MESSAGE_TYPES = (
    "request_for_help",
    "business_query",
    "clarification",
    "casual",
    "unknown",
)
MessageType = Literal[MESSAGE_TYPES]  # type: ignore[valid-type]

# Every declared intent, flattened out of the frozen domain seed (D9). One tuple, and the
# mapping from an intent back to its domain is now a property of the table rather than a
# fact a reader had to know: `check_po` means `purchase_order` because that is the row it
# is on. Read from `policy_rows.DEFAULT_DOMAIN_ROWS` rather than a database row because a
# `Literal` type is resolved at IMPORT time (AC-1594) - the S0 migration seeds a real
# database from this SAME list, so the two can never disagree.
INTENT_HINTS: tuple[str, ...] = tuple(
    intent for row in policy_rows.DEFAULT_DOMAIN_ROWS for intent in row["intents"]
)
IntentHint = Literal[INTENT_HINTS]  # type: ignore[valid-type]

DOMAIN_HINTS: tuple[str, ...] = tuple(row["name"] for row in policy_rows.DEFAULT_DOMAIN_ROWS)
DomainHint = Literal[DOMAIN_HINTS]  # type: ignore[valid-type]


def named_count(top_n: Any) -> int | None:
    """The count a message named (the parser's `top_n`), or None: a bool, a non-int or a
    value <= 0 names none. THE one reading, shared by the engine (arming the counted-set
    carry), `turn/apply.py` (reading the answer to "how many should I show?") and the
    fetch (slicing the set), so the three cannot disagree about one turn (reviewer N2 on
    PR #833: `True` or `0` once withheld the rows while arming no carry)."""
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        return None
    return top_n


def coerce_domain_hint(value: Any) -> Any:
    """A `domain_hint` outside the enum above, coerced to null. THE one guard.

    F3 (review, 7 Sep 2026). Evidence turn b5b19cec-dccc-4eda-b766-1aeb1362957b: the
    parser tagged `domain_hint: "purchasing"` for "IBWB248什么时候会到仓库？" - "purchasing"
    is a TEAM name (`lanes/escalation.ESCALATION_TEAMS`), not a domain - and
    `select_tool`'s
    `source_id LIKE '%purchasing%'` filter matched nothing (every incoming tool's
    `source_id` is `implemented::crm_incoming_stock_*`), so the turn ended `not_found`.
    That filter is GONE since 8 Sep 2026 (the lane picks the domain row's own first
    tool, and a team name resolves to no row), so the same turn ends the same way for a
    plainer reason. The guard stays: a domain outside the enum must not reach any reader.

    A domain reaches a turn from exactly TWO places, and both call this:

    * the parser's emission, at `head/output_exchange._post_process`;
    * the contact's STORED memory, at `engine._drop_unknown_carried_domain` - which is what turn
      fca4aa5e-806b-4403-aa2e-fc2d0961fb2d proved the emission guard alone does not
      cover. That turn's parser emitted a clean `incoming`, and the carried
      `variables.domain_hint: "purchasing"` (written by an earlier turn, or by n8n) was
      adopted over it by `resolve_gate.retype_shipment_miss` - one of EIGHT sites that
      inherit the carried domain verbatim.

    Guarding the two entry points rather than the eight inheritors is what keeps this one
    place: every reader downstream, `select_tool` included, is then trusted as-is.
    """
    return value if value in DOMAIN_HINTS else None

# `BARE_ENTITY_TYPE_BY_DOMAIN`, `DOMAIN_SWITCH_WORDS`, `DEFAULT_UNSUPPORTED_DOMAINS` and
# `DOMAIN_CLAIMED_TOOLS` used to live here as views over `DOMAIN_SPEC` (AC-931). Gone
# (AC-1594): the data is `chatbot_domains` now, read as `turn.policy.Policy` -
# `turn.policy.domain_switch_words(policy)` and `lanes/business/fetch.select_tool` /
# `CHATBOT_READ_ONLY_TOOLS` for the tool views, `app/modules/chatbot/lane_vocabulary.
# default_unsupported_domains()` for the core-side doorway `SystemSetting.
# chatbot_unsupported_domains`'s own default and `settings.py`'s null-reset table already
# went through (AC-002 unchanged by S6 - core still may not import this package).
# `BARE_ENTITY_TYPE_BY_DOMAIN` has no runtime reader left; nothing replaces it.

# `SUGGESTED_TEAMS` used to live here too (AC-931). Gone (AC-1594): NOT derivable from
# `chatbot_domains.escalation_team_code` - two of the eight (`purchasing_certification`,
# `it_admin`) answer from no domain of their own, so a domain-table union would silently
# drop them. The eight codes are escalation-lane vocabulary, not domain data, and now live
# in `lanes/escalation.py` as `ESCALATION_TEAMS`, the module that actually owns routing a
# turn to a team.
#
# The team `output_exchange`'s routing chain falls to when this turn named none, its domain
# derives none and no previous turn carried one - the HARD default at the end of the
# nullish chain, so this body never emits a null team. Named here because TWO readers need
# the same word: the chain that writes it, and the escalation lane's inheritance guard
# (AC-815 / review of #706 B1), which must not mistake a carried default for a previous
# turn's real routing.
DEFAULT_SUGGESTED_TEAM = "customer_service"

SUGGESTED_AGENTS = (
    "general_enquiries",
    "order_enquiries",
    "incoming_stock_enquiries",
    "marketing_form",
    "it_support",
    # deriveRouting's `ideate` case is the single source of truth for this one: the
    # access check keys on suggested_agent and an unknown agent fails CLOSED.
    "ideation",
)
SuggestedAgent = Literal[SUGGESTED_AGENTS]  # type: ignore[valid-type]

# The agent an escalation's round-robin draw falls to when the parser named none - a
# person the customer never asked FOR by role still needs `agent_code` filled in
# (`/external/next-assignee` 400s on neither `agent_id` nor `agent_code`); the first,
# catch-all member of `SUGGESTED_AGENTS` is what the retired head chain defaulted to.
DEFAULT_SUGGESTED_AGENT = SUGGESTED_AGENTS[0]

ENTITY_HINTS = (
    "product",
    "promotion",
    "customer",
    "transporter",
    "inbound_shipment",
    "warehouse",
    "attachment",
    "form",
    "order",
    "category",
    "brand",
    "attachment_type",
    # Fix round 8 on PR #833: a product property from the specification registry
    # (`head/grounding.py`), never a document type.
    "specification",
)
EntityHint = Literal[ENTITY_HINTS]  # type: ignore[valid-type]

# `selection_context` is written by compile-current-state and read by the head to decide
# what an otherwise meaningless reply ("1", "dealer", "yes") refers to.
SELECTION_CONTEXTS = (
    "disambiguation",
    "suggest_offer",
    "member_offer",
    "tier_offer",
    "team_clarify",
    "company_clarify",
)
SelectionContext = Literal[SELECTION_CONTEXTS]  # type: ignore[valid-type]

# --------------------------------------------------------------------------- #
# Engine vocabularies
# --------------------------------------------------------------------------- #

# The 13 arms `route-turn` decides between, in ladder order, plus `media_denied`
# (chatbot media-into-turn, S2): a photo or voice note the intake step refused
# (quota, burst, disabled number, clip too long) or could not complete (extraction
# failed, timed out) before a parser ever ran.
BRANCH_KINDS = (
    "access_denied",
    "escalate_offer",
    "out_of_scope",
    "ideate",
    "offer_hold",
    "escalation_declined",
    "check_promotion",
    "low_signal",
    "clarify_menu",
    "not_supported",
    "stock_denied",
    "demand_qty",
    "business_query",
    "media_denied",
)
BranchKind = Literal[BRANCH_KINDS]  # type: ignore[valid-type]

# The arms whose first n8n node was a STRIPPING `tag-*` Set. On these the router emits
# `{branch_kind}` and nothing else, so the fan-in downstream sees what it always saw.
TAG_ONLY_BRANCH_KINDS: frozenset[str] = frozenset(
    {
        "escalate_offer",
        "escalation_declined",
        "clarify_menu",
        "not_supported",
        "demand_qty",
    }
)

# Trace stages (AC-003, AC-007). One record per stage, in this order, on a full turn.
TURN_STAGES = (
    "received",
    "understood",
    "access",
    "routed",
    "looked_up",
    "replied",
    "remembered",
    "sent",
)
TurnStage = Literal[TURN_STAGES]  # type: ignore[valid-type]

TRACE_STATUSES = ("ok", "failed", "skipped")
TraceStatus = Literal[TRACE_STATUSES]  # type: ignore[valid-type]

# `chatbot.turns.stage` records WHERE a turn stopped. It is a superset of TURN_STAGES:
# four failure points sit outside the trace timeline - `intake` is before the first
# trace record exists (H5, AC-107), `queued` is the S7 per-contact wait (AC-710),
# `casual_llm` is the S4 clarifier call (AC-403), and `delegated` is a turn an n8n lane
# took over and never finished, failed by the sweep (AC-260,
# `app/services/chatbot_turn_sweep.py`). `media_intake` (chatbot media-into-turn, S2)
# is a fifth: an extraction that failed or outlived the sync wait stops there, before
# the parser ever ran.
TURN_FAILURE_STAGES = TURN_STAGES + ("intake", "queued", "casual_llm", "delegated", "media_intake")
TurnFailureStage = Literal[TURN_FAILURE_STAGES]  # type: ignore[valid-type]
# Enforced where the column is written (`engine._close_turn`), so a typo'd stage fails
# loudly instead of landing in the row and reading as an unknown state on the trace
# screen.

TURN_STATUSES = ("queued", "processing", "delegated", "done", "failed")
TurnStatus = Literal[TURN_STATUSES]  # type: ignore[valid-type]

# What the CALLER executes, in order (D4/D9: the CRM never sends).
ACTION_KINDS = (
    "send_message",
    "send_attachments",
    "assign_conversation",
    "add_comment",
    "update_contact_fields",
)
ActionKind = Literal[ACTION_KINDS]  # type: ignore[valid-type]

# Which injector delivered this envelope (D15). The engine never behaves differently on
# it; it exists so a trace row can say where a duplicate came from.
INGRESS_KINDS = ("webhook", "poller", "retry", "console")
IngressKind = Literal[INGRESS_KINDS]  # type: ignore[valid-type]

# The business lane's resolve+gate exits (S6a). Taken from the four `resolve-exit-*`
# bodies' own `_exit_kind` literals, in the order `sub-main-processing`'s `resolve-arm`
# Switch tests them - NOT guessed, because that Switch is what routes on the value and a
# fifth word here would be an arm nothing is wired to.
EXIT_KINDS = ("continue", "access_ask", "not_found", "offer")
ExitKind = Literal[EXIT_KINDS]  # type: ignore[valid-type]

# The six named contract fields every `resolve-exit-*` arm adds beside `_exit_kind`, in
# the order the bodies list them. `sub-main-processing`'s stand-in chain reads exactly
# these - `resolve-gate` / `aggregate-gate` / `annotate-incoming-gate` test them for
# `!== null`, and the `resolve-entity` / `disallowed-entity-gate` / `build-ctx-resolved` /
# `Aggregate` / `tier-gate` / `annotate-incoming-picker` stand-ins re-emit them - so a
# missing key is an n8n node that silently stops executing, not a missing field.
EXIT_CONTRACT_FIELDS = (
    "resolved",
    "gate",
    "ctx_resolved",
    "aggregate",
    "tier_gate",
    "annotate_incoming",
)

# `requested_attributes`' timeline sentinel (H46). `output-structurer` reads it as
# `.some(k => String(k ?? '').trim() === '__all__')` - CONTAINS the sentinel, not IS it
# alone - and a review once called that guard redundant because every mutation it was
# tested against emitted the sentinel by itself. Declared here, with `is_timeline` beside
# it, so S6b's port consumes the one reading instead of re-deriving it.
TIMELINE_SENTINEL = "__all__"


def is_timeline(requested_attributes: Any) -> bool:
    """`_isTimeline`: does `requested_attributes` CONTAIN the sentinel (H46)?

    Mixed emission (`['__all__', 'gatepass_date']`) is timeline mode, exactly as it is in
    n8n. The parser is *instructed* to emit the sentinel alone, but an instruction is not
    an invariant, and reading this as equality would flip a real turn's behaviour.
    """
    if not isinstance(requested_attributes, list):
        return False
    return any(
        jsc.js_string("" if key is None else key).strip() == TIMELINE_SENTINEL
        for key in requested_attributes
    )

# `pending` marker kinds (R3). These replace the two frozen string contracts the JS
# matched with a regex over the previous reply.
PENDING_KINDS = (
    "escalation_offer",
    "team_clarify",
    "company_clarify",
    "tier_ask",
    "member_offer",
    # PLAN-chatbot-outstanding-report.md, S4 points 4/5.
    "outstanding_scope",
    "outstanding_detail",
    # PLAN-chatbot-sales-report.md, S4 wiring point 7: the sales report's OWN
    # detail offer, sharing the mechanism `outstanding_detail` built rather than a
    # copy of it - see `DETAIL_OFFER_KINDS` below.
    "sales_report_detail",
)
PendingKind = Literal[PENDING_KINDS]  # type: ignore[valid-type]

# PLAN-chatbot-sales-report.md, S4 wiring point 7 (captain ruling 2): the ONE shared
# constant every site that used to test the literal `"outstanding_detail"` reads
# instead - a membership test, not a second copy of the arm. `sales_report_detail`'s
# own offer has exactly the shape `outstanding_detail`'s already has (a roster of one
# or more scopes, re-run with `detail=...`, sticky across a pick/casual turn, closes
# on a decline or a second unreadable reply) - so every arm that reads
# `kind in DETAIL_OFFER_KINDS` handles both. The KIND itself decides which tool the
# re-run calls (`head/output_exchange.py::_apply_outstanding_pending` stamps
# `order_status: "sales_report"` when `kind == "sales_report_detail"`, the outstanding
# scope's own word otherwise) - `outstanding_filters["tool"]` rides along on the
# stored filter set too, but nothing in this package reads it back; it exists for a
# caller inspecting the stored session state, not for this re-run decision.
DETAIL_OFFER_KINDS: tuple[str, ...] = ("outstanding_detail", "sales_report_detail")

#: The sales report's drill-downs (lane SALES-REPORT, AC-SR-28): the values a
#: `sales_report_detail` option carries, each the `group_by` its pick re-runs with. Read
#: ONLY for that kind - the outstanding report's so/do/both picks are untouched.
SALES_REPORT_GROUP_BYS: tuple[str, ...] = ("customer", "product", "delivery_order", "sales_agent")

# PLAN-chatbot-top-x-hot-selling-24sep.md "Lane wiring (S4)" point 5: the `order_status`
# values that read sales figures under the `sales_orders.sales_report` reveal key (the
# owner's access ruling, 26 Sep 2026: no new key). The engine's grant-before-roster
# check reads this one tuple, so the asks cannot be gated differently. `sales_analysis`
# (PLAN-retail-sales-reports-26sep S1, #1269) is gated by the same key.
SALES_FIGURE_STATUSES: tuple[str, ...] = ("sales_report", "top_selling", "sales_analysis")

# --------------------------------------------------------------------------- #
# Session state (R2: every key compile-current-state writes, nothing dropped)
# --------------------------------------------------------------------------- #

SESSION_VAR_KEYS = (
    "message_type",
    "intent_hint",
    "domain_hint",
    "user_goal",
    "query_scope",
    "query_brands",
    "access_levels",
    "entities",
    "routing",
    "escalation",
    "response",
    "last_result_set",
    "selection_context",
    "date_filter_start",
    "date_filter_end",
    "date_mode",
    "requested_attributes",
    "match_mode",
    "contains_flyer",
    "dym_offer",
    "dym_candidates",
    "ideation",
    "dym_last_result_set",
    "tier_menu",
    "picker_last_result_set",
    "picker_families",
    "picker_domain",
    "picker_selection_context",
    "picker_families_carried",
    "routing_roster_plan",
    "routing_brand",
    "routing_brand_source",
    "routing_company",
    "routing_companies",
    # R3: the persisted marker that replaces the frozen-string reads.
    "pending",
)


class Pending(BaseModel):
    """What the bot is waiting for, recorded rather than re-read out of its own words."""

    model_config = ConfigDict(extra="forbid")

    kind: PendingKind
    team: str | None = None
    domain: str | None = None
    # `member_offer` only: how many more turns the roster stays on the customer's screen
    # (AC-816 rule 1, `tail/pending.MEMBER_OFFER_TTL`). Absent on every other kind, and on
    # a marker written by n8n, which has no clock - the reader treats absence as "open".
    ttl: int | None = None
    # `team_clarify` only (AC-821 / AC-822): the teams the ask actually OFFERED, as
    # `{team, label}` - the slug the router acts on beside the exact string the customer
    # saw on the quick reply. One list, so a tap can never resolve to a team the ask did
    # not name, and `output_exchange._team_clarify_pick` can compare the reply against OUR
    # OWN string by equality instead of trying to understand it. Absent on a marker written
    # before this shipped, which is why that reader treats absence as "parser answer only".
    options: list[dict[str, Any]] | None = None
    # `outstanding_scope` / `outstanding_detail` only (R22, owner round 9, 13 Sep 2026):
    # this question has already been RE-PRINTED once over a reply that answered nothing
    # (AC-1143(c)). The next such reply closes it instead of printing a third copy. A
    # boolean, not a countdown: R2 keeps the offer sticky across picks and casual turns,
    # and a TTL would close it behind a customer who is still reading it. Absent on every
    # other kind and on a marker written before this shipped, which reads as "not yet".
    reprinted: bool | None = None


class LegacyVariables(BaseModel):
    """`respond_contacts.session_vars.variables`, allowlisted (H15, AC-203).

    Renamed from `SessionVars` (chatbot turn re-architecture S0, AC-1504): that name now
    names the NEW five-key session shape below. This is the pre-rearch flat shape n8n's
    outer loop still writes to `session_vars.variables` - unrelated to, and untouched by,
    the new `SessionVars`/`Focus` pair.

    `extra = "forbid"` is what stops a harness key or a stray diagnostic leaking into a
    customer's session: the JS built a fresh object literal per writer, so anything a
    writer happened to set survived. The tail (S2) is what enforces this on the write
    path; S1 only reads.
    """

    model_config = ConfigDict(extra="forbid")

    message_type: Any = None
    intent_hint: Any = None
    domain_hint: Any = None
    user_goal: Any = None
    query_scope: Any = None
    query_brands: Any = None
    access_levels: Any = None
    entities: Any = None
    routing: Any = None
    escalation: Any = None
    response: Any = None
    last_result_set: Any = None
    selection_context: Any = None
    date_filter_start: Any = None
    date_filter_end: Any = None
    date_mode: Any = None
    requested_attributes: Any = None
    match_mode: Any = None
    contains_flyer: Any = None
    dym_offer: Any = None
    dym_candidates: Any = None
    ideation: Any = None
    dym_last_result_set: Any = None
    tier_menu: Any = None
    picker_last_result_set: Any = None
    picker_families: Any = None
    picker_domain: Any = None
    picker_selection_context: Any = None
    picker_families_carried: Any = None
    routing_roster_plan: Any = None
    routing_brand: Any = None
    routing_brand_source: Any = None
    routing_company: Any = None
    routing_companies: Any = None
    # S4 point 4 (PLAN-chatbot-outstanding-report.md): the parsed product/dates/
    # customer/location, carried across the scope-question turn and the detail-offer
    # turn (see `tail/compile_state.py`).
    outstanding_filters: Any = None
    # R16 (owner round 5, 13 Sep 2026): the delivery status the question was asked about,
    # written only on a turn that named one and read back by the head's `reuse` carry -
    # the same axis-of-the-question role `date_filter_start` and `requested_attributes`
    # above already have (see `tail/compile_state.py`).
    order_status: Any = None
    # PLAN-chatbot-sales-report.md, S4 wiring point 2 (captain ruling 1): the sales
    # report's own channel filter, persisted beside `order_status` and carried by
    # the SAME R16 reuse arm - a sales report ask that hit the gate's ambiguous-
    # customer picker names no channel word on the pick turn that resumes it.
    sales_channel: Any = None
    pending: Pending | None = None


# --------------------------------------------------------------------------- #
# The turn re-architecture's session shape (AC-1504, PLAN-chatbot-turn-rearch.md
# "Design > State"). NOT wired into the live engine yet (S0 declares the shape; S2's
# `turn/apply.py` and S3's tail are what read and write it for real) - `LegacyVariables`
# above stays what n8n's outer loop persists until then.
# --------------------------------------------------------------------------- #


class Focus(BaseModel):
    """The persisted conversation focus - `session_vars.focus`.

    `document` and `status` replace the legacy flat `order_status` (contract 34, "focus
    slots with replace, reset, reuse"; hazard: a stored `order_status` is read once and
    mapped forward - `conversation_variables_service.get_for_contact`). `document` is a
    LIST of document kinds (`["DO"]`, `["SO", "DO"]`), never a third "both" value.
    """

    model_config = ConfigDict(extra="forbid")

    # ONE shape, not two. These are the axes `turn/state.py::Focus` carries and
    # `focus_to_wire` writes, field for field - the turn re-architecture's whole point is
    # that there is a single scope, so the working object and the stored object cannot be
    # different objects with a mapping between them. The mapping is what lost a ledger
    # FAMILY on the way out (a singular `customer` cannot hold the two ledgers a "chin
    # chun" pick resolves to, contract 103 / D7).
    #
    # Every entity axis is a LIST OF ENTITY DICTS (`{raw, canonical_code, uuid, ...}`),
    # never a list of bare codes: what a pick resolved to and what the customer typed are
    # both needed next turn, and a code alone keeps neither.
    products: list[dict[str, Any]] = Field(default_factory=list)
    customers: list[dict[str, Any]] = Field(default_factory=list)
    warehouse: list[dict[str, Any]] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    # #1262 slice 9 (F1a) follow-up: the outstanding report's own brand carry - see
    # `turn/state.py::Focus.outstanding_brand_ids`'s own docstring for why this is a
    # dedicated field, never `brands` above.
    outstanding_brand_ids: list[str] = Field(default_factory=list)
    tier: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    document: list[str] = Field(default_factory=list)
    status: str | None = None
    # PLAN-chatbot-sales-report.md S4 wiring point 2: the sales report's channel filter,
    # an axis beside `status` - `turn/state.py::Focus` carries the same field and
    # `focus_to_wire` writes it, one shape, not two.
    sales_channel: str | None = None
    date_window: dict[str, Any] | None = None
    # The PO/SPO sort axis {"by", "dir"} (PLAN-po-spo-warehouse-29sep S5); `Focus.sort`.
    sort: dict[str, Any] | None = None
    # AC-1317: where a counted-set answer got to, `{set_key, offset}`.
    set_page: dict[str, Any] | None = None
    # Round 4 R5: the ask an open clarify question was about, `{term, options, ask}`.
    set_clarify: dict[str, Any] | None = None
    # PLAN-chatbot-top-x-hot-selling-24sep.md "Lane wiring (S4)" point 8: the top selling
    # ask's own axes while `status == "top_selling"` (`turn/state.py::Focus.top_selling`).
    top_selling: dict[str, Any] | None = None
    # LOWSTOCK-FILTER-ASK: the question `required_fields` left open, one turn long
    # (`turn/state.py::Focus.required_ask`).
    required_ask: dict[str, Any] | None = None
    # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner ruling
    # 24 Sep 2026) for chatbot-stock-ask-v2 S3: the open tasks, carried INSIDE the
    # focus rather than on a session key of their own. Declared here because this
    # model is `extra="forbid"` and `turn/state.py::focus_to_wire` writes the key on
    # every turn - a shape the session validator did not know would fail the write
    # itself.
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    # STUCK-QTY-LOOP (`turn/state.py::Focus.intent` / `held_turn` / `held_access`): the
    # central held-question rule's facts, written by `focus_to_wire` only when set.
    intent: str | None = None
    held_turn: int | None = None
    held_access: str | None = None
    # Any entity kind without a named axis above, keyed by kind. A kind this turn's
    # policy narrows on but the Focus never declared still has somewhere safe to sit
    # rather than being dropped on the way to the session.
    extra: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class SessionVars(BaseModel):
    """The five-key session shape (AC-1504). Byte-compatible hazard (contract 129,
    "the five-key session shape must stay byte-compatible for #930's parked lane and for
    live contacts mid-conversation at deploy"): exactly these five keys, nothing more.

    `extra = "forbid"` for the same H15 reason `LegacyVariables` carries it - nothing
    outside this list may leak into a customer's session.
    """

    model_config = ConfigDict(extra="forbid")

    focus: Focus = Field(default_factory=Focus)
    open_question: dict[str, Any] | None = None
    ideation: dict[str, Any] | None = None
    access_levels: list[str] = Field(default_factory=list)
    contains_flyer: bool = False


# The widths the `chatbot.turns` columns actually have. Validated on the way IN so an
# over-long identifier is a 422 naming the field, never a truncation error at insert.
MAX_MESSAGE_ID_LENGTH = 128

# --------------------------------------------------------------------------- #
# Transport (the plan's "Transport contract with n8n")
# --------------------------------------------------------------------------- #


class Envelope(BaseModel):
    """The redis queue item `A` the two injectors both post, unchanged (D15).

    `message` is the respond.io webhook body; `contact` is the respond.io contact record
    n8n already looked up. Everything else is optional metadata about HOW the turn
    arrived, and the engine's behaviour depends on none of it except `is_test` /
    `test_run_id` (D14).
    """

    model_config = ConfigDict(extra="allow")

    message: dict[str, Any]
    contact: dict[str, Any]
    test_run_id: str | None = None
    is_test: bool = False
    mode: str | None = None
    scope: str | None = None
    ingress: IngressKind = "webhook"
    # Gate 4 (shadow mode). Same reason as `messageId`: it lands in a VARCHAR(128).
    shadow_of: str | None = Field(default=None, max_length=128)
    # PLAN-chatbot-media-into-turn.md, AC-1805 (review round S3): n8n's OWN transition-
    # window shape, while its media pipeline still runs upstream of `/chat/turn` during
    # the cutover - `{envelope: {..., message, media: <patched item>}}`. Read by
    # `media_intake.patched_upstream()`; `None` on every other envelope, including
    # every one this repo's own tests build.
    media: dict[str, Any] | None = None

    @field_validator("message")
    @classmethod
    def _identifiers_must_fit_their_columns(cls, message: dict[str, Any]) -> dict[str, Any]:
        """`messageId` is stored in a VARCHAR(128); an over-long one is the CALLER's bug.

        Without this the insert raises a `StringDataRightTruncation` deep in the engine and
        the caller sees a 500 - indistinguishable from the CRM being down, and n8n retries
        it. 422 naming the field says which byte to fix.
        """
        inner = message.get("message")
        message_id = inner.get("messageId") if isinstance(inner, dict) else None
        if message_id is not None and len(str(message_id)) > MAX_MESSAGE_ID_LENGTH:
            raise ValueError(
                f"message.message.messageId is longer than {MAX_MESSAGE_ID_LENGTH} "
                "characters, which is the width of the column it is stored in"
            )
        return message

    @field_validator("contact")
    @classmethod
    def _contact_must_identify_somebody(cls, contact: dict[str, Any]) -> dict[str, Any]:
        """`contact.id` is the respond.io contact id, and nothing works without it.

        Validated HERE so a caller that omits it gets a 422 naming the field, which is a
        misconfigured integration telling its operator what is wrong. Left to the engine
        it was a `ValueError` raised before the turn row existed, which the endpoint's
        generic handler turned into a bare 500 - the same symptom as a real outage, with
        nothing to distinguish them.
        """
        if contact.get("id") in (None, ""):
            raise ValueError(
                "contact.id is required: it is the respond.io contact id every session "
                "read, access check and turn row keys on"
            )
        return contact

    @property
    def dry_run(self) -> bool:
        """D14: a test envelope does ZERO writes outside `chatbot.turns`.

        Both signals mean the same thing and either is enough - the clone sets
        `test_run_id`, the chat console sets `is_test`, and `mode` is n8n's own
        pre-existing marker. Evaluated BEFORE any side-effecting service, never after
        (H37: n8n called next-assignee first and guarded second).
        """
        return bool(self.is_test or self.test_run_id or (self.mode and self.mode != "live"))


# `Action` and `Reply` are deliberately NOT modelled. `TurnResponse` carries both as plain
# dicts: an action's payload differs per kind, `reply.result_set` is whatever the lane
# produced, and `response_model` silently DROPS anything a model does not declare - so a
# half-right model is worse than none. The vocabulary that DOES need pinning is
# `ACTION_KINDS` above, and the endpoint tests assert the keys survive the wire.


class TurnRequest(BaseModel):
    """`POST /api/v1/external/chat/turn` body."""

    model_config = ConfigDict(extra="forbid")

    envelope: Envelope


class TurnResponse(BaseModel):
    """`POST /api/v1/external/chat/turn` 200 body.

    `delegate` names the n8n lane that must still run; `null` means the CRM finished the
    turn and the caller only sends. `session_patch` is populated on a dry run only, so a
    console or clone turn can be inspected without anything having been written.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    # The ENGINE's dry-run verdict, not an echo of the request: `is_test`, `test_run_id`
    # and a non-live `mode` all produce it (D14), and n8n gates its egress on this rather
    # than on re-deriving the same rule from the envelope it sent.
    is_test: bool = False
    ctx: dict[str, Any] | None = None
    item: dict[str, Any] | None = None
    branch_kind: BranchKind | None = None
    delegate: str | None = None
    reply: dict[str, Any] | None = None
    # The caller executes these IN ORDER. Declared as dicts because the payload
    # differs per kind and `response_model` silently DROPS anything undeclared.
    actions: list[dict[str, Any]] = Field(default_factory=list)
    session_patch: dict[str, Any] | None = None
    # S6a: the business lane's resolve+gate result, i.e. exactly the item
    # `sub-resolve-and-gate` used to return. `delegate` names the lane; this is what that
    # lane resumes on, and n8n's `resolve-item` re-emits it verbatim into `resolve-arm`.
    # Null on every arm the CRM does not run the lane for, and it disappears at S6c when
    # the CRM finishes the turn itself.
    delegate_payload: dict[str, Any] | None = None
    duplicate: bool = False


class CompleteRequest(BaseModel):
    """`POST /api/v1/external/chat/turn/{turn_id}/complete` body (AC-201).

    The `sub-output` trigger contract, unchanged: `item` plus the eleven nullable
    producer outputs the sub's RS-9 carrier stubs re-emit today. Each value is a
    producer's WHOLE output, verbatim, because the tail's by-name reads dig into it
    (`result.result.xd.block`, `answer.outcome_fragment['central-exchange']`) and
    reshaping it here would move the very bytes the replay corpus grades.

    `ctx` is optional: `/turn` already persisted it on the row, which is where the tail
    reads it from. Accepting it anyway means the n8n node can keep sending exactly what
    it sends today - a caller that has to DELETE a field to be accepted is a caller that
    breaks on the next deploy.
    """

    model_config = ConfigDict(extra="forbid")

    item: dict[str, Any]
    ctx: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    resolved: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
    offer_hold: dict[str, Any] | None = None
    suggest_offer: dict[str, Any] | None = None
    not_found: dict[str, Any] | None = None
    incoming_picker: dict[str, Any] | None = None
    access_choice: dict[str, Any] | None = None
    crossdomain_render: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    clarify: dict[str, Any] | None = None


class CompleteResponse(BaseModel):
    """`POST .../complete` 200 body.

    `reply` is `{text, quick_replies, result_set, attachments_src}` - the four values
    `sub-sendmsg` and `send-attachments` reach for by name today, so each of their
    expressions becomes one read (AC-207). A plain dict for the same reason `Reply` is
    not modelled above: `result_set` is whatever the lane produced, and `response_model`
    silently DROPS anything a model does not declare.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    reply: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    # D14: populated on a dry run only, so a console or clone turn can be inspected
    # without anything having been written.
    session_patch: dict[str, Any] | None = None
    # The ROW's `is_test`, decided on the envelope at `/turn` and repeated here so n8n's
    # `test-guard` can log what it recorded instead of sent without carrying the head's
    # answer across two calls. Not a second switch: every action already carries the same
    # value on `dry_run`.
    is_test: bool = False


# Which branch kinds still hand back to an n8n lane. After S1 that is all of them: the
# head decides and n8n answers. Each later slice REMOVES entries here (S3 takes eight,
# S4 one, S5 one, S6 three), and S7 empties it and deletes `delegate` entirely. Derived
# from BRANCH_KINDS minus what the CRM already completes, so the two can never disagree.
# The business lane's three arms (S6). They converge on ONE n8n lane (`business_query`)
# and on ONE module here (`lanes/business/`), so the three travel together everywhere:
# into `CRM_COMPLETED_BRANCH_KINDS` (the code can finish them), into
# `SELF_CLOSING_BRANCH_KINDS` (their own module closes the row), and out of
# `lanes/canned.py`'s set (it does not compose them). `lanes.business.ENTRY_BY_BRANCH_KIND`
# is the runtime map; this is the same three as a contract, so `contracts` needs no import
# of a lane.
BUSINESS_BRANCH_KINDS: frozenset[str] = frozenset(
    {"business_query", "check_promotion", "stock_denied"}
)

CRM_COMPLETED_BRANCH_KINDS: frozenset[str] = frozenset(
    {
        # S3 - the canned lanes, offer-hold and ideation. Each is answered from the
        # prompt registry or (for `ideate`) from the `crm_ideation_turn` MCP tool, and
        # every one of them ends the turn inside `run_turn`.
        "access_denied",
        "escalate_offer",
        "escalation_declined",
        "clarify_menu",
        "not_supported",
        "demand_qty",
        "offer_hold",
        "ideate",
        # S4 - the small-talk clarifier.
        "low_signal",
        # S5 - the escalation lane, which hands the turn to a person.
        "out_of_scope",
    }
    # S6 - the business lane's three arms: resolve, gate, fetch, answer and miss all run
    # in process, so the turn no longer leaves the CRM for an n8n sub-workflow. Declared
    # separately below because two other places need the three by name.
    | BUSINESS_BRANCH_KINDS
)
# The kinds that have a lane MODULE of their own and close their own turn row once that
# lane has answered: `low_signal` is `lanes/casual.py` (S4) and `out_of_scope` is
# `lanes/escalation.py` (S5). Every other kind the CRM completes is composed by
# `lanes/canned.py`, which projects its own set off the two above rather than repeating
# either, and closes in `_run_stages`' canned block. Declared HERE because the engine and
# the canned module both need the answer, and two literals would drift the moment a slice
# adds a lane - which is exactly what S5 landing on S3 would otherwise have done. The
# three business arms are in here for the same reason: `lanes/business/` composes and
# closes them itself, through `complete_turn`, and `canned.py` must not try to.
SELF_CLOSING_BRANCH_KINDS: frozenset[str] = (
    frozenset({"low_signal", "out_of_scope"}) | BUSINESS_BRANCH_KINDS
)

# What a dry run prints where a SEAM would have supplied a value (D14, AC-507). One
# token, so a reader of a preview action can tell at a glance that nothing behind it
# happened. Declared here rather than in the lane that uses it (`out_of_scope`, for the
# assignee and the SLA timestamps) so a second lane that stands a value in reuses the one
# spelling of "nothing happened" rather than a near-miss of it. It is an operator token:
# it belongs on `status` and the trace facts and must never reach a `send_message`,
# because the executor executes actions and nothing else. (`ideate` used to stand its
# whole reply in; since #1179 a dry-run ideate turn calls the tool as a test turn and
# sends the tool's real words.)
PREVIEW = "<preview>"


# `DELEGATED_BRANCH_KINDS` used to be the complement of the set above and is GONE: with
# `system_settings.chatbot_completed_lanes` in the decision, "delegated" is no longer a
# property of the build at all - the same kind delegates or completes depending on data -
# so a module-level constant claiming otherwise could only mislead. `delegate.delegate_for`
# is the one place that answers the question, and it needs both halves to do it.
