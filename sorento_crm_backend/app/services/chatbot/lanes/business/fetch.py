"""Port of `sub-fetch-results` + `sub-get-results` (S6b, AC-604 to AC-606).

The business lane's fetch step: pick ONE tool, call it over MCP, render the answer
deterministically. Six node bodies become six functions, line for line against the exported
JavaScript, with the same `jsc` shim S6a uses for JS truthiness / `String()` / `Number()`.

Three hazards are fixed here rather than reproduced, and each says so at its own site:

* **H53** - `sub-get-rag` is GONE, SQL and vector alike. The tool is read straight off
  the domain row's own first tool (`select_tool` below), so this module names no
  table, writes no SQL, and makes no provider call. Measured over the 740 business turns
  in the 7 Sep 2026 prod copy, the embedding pick WAS the domain's first-listed tool on
  every turn, and the seeding chain the search depended on cannot run in the deployed
  backend image at all: production's tool RAG has been frozen since 2 June 2026.
* **H52** - the MCP endpoint is `settings.ai_assistant_mcp_url`, bound in `services.py`.
  n8n bakes a raw IP endpoint into TWO nodes; this module contains no host, no port and no
  scheme at all, and `call_tool` is a pass-through onto whatever client it is handed. The
  absence is mechanical: `test_s6b_fetch_lane.py` greps this file's source for both.
* **H11** - `tool-filter.js` returns `[]` on zero tools and that empty array is
  indistinguishable from "ran and found nothing to say". `tool_filter` keeps the empty
  item list for parity (D8) and adds `outcome`, which the caller can act on.
* **H58** - the pick used to be an argmax over an embedded catalogue that contains WRITE
  tools (`crm_order_cancel`, `crm_complaint_close`, the two purchase-request approvals,
  `crm_it_support_ticket_create`, `crm_ideation_turn`), and `tool_filter` takes the single
  candidate with no further test. `CHATBOT_READ_ONLY_TOOLS` below is the allow-list, and
  since the candidate is now the domain row's own first tool the hazard is structural
  rather than scored: nothing outside that table can be named, and `ensure_read_only`
  refuses anything off the list at both call seams anyway. The embedded POOL keeps the
  write tools, on purpose - the in-app AI assistant retrieves them and confirms with a
  human before each one, which is a gate this chatbot does not have.

**H43 is moot, not fixed.** The n8n query's `$4` is `domain`, LIKE-matched against
`source_id`, and some live call sites never bind it. In process `domain` is a parameter of
one function call, so `domain=None` means "no tool" by construction and can never mean
"the caller forgot to wire a parameter".

**H49, the tool-selection distribution.** `crm_order_management_orders_by_product_list` has
never been selected in any capture graded so far, so this module has NO per-tool branch
keyed on it. `entity_ids_transformer`'s `DATE_PARAMS` / `ORDER_TOOLS` tables are copied
verbatim from the JS and are LOOKUP TABLES, not branches: they name that tool because the
JS does, and porting them minus one row would be a silent behaviour change on the day it is
first selected. The measurement that would justify an actual branch has not been taken.

**H7 is answered by construction:** `output_structurer` is deterministic string building.
There is no answer LLM anywhere in this lane (D10), so the "orphaned answer LLM" hazard has
nothing to attach to.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.chatbot import jsc
from app.services.chatbot.contracts import (
    SALES_REPORT_GROUP_BYS,
    UNDOMAINED_CHATBOT_TOOLS,
    is_timeline,
    named_count,
)
from app.services.chatbot.turn.policy import default_policy
from app.services.chatbot.turn import policy_rows
from app.services.product_spec_registry import display_spec_value

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# tool-filter
# --------------------------------------------------------------------------- #

ToolOutcome = Literal["picked", "not_found"]


@dataclass(frozen=True)
class ToolPick:
    """`tool-filter`'s item list, plus the outcome its empty array could not express.

    `items` is BYTE-EQUAL to the JS (D8): one item on a pick, zero on none. `outcome` is
    H11's fix and is always set, so "no tool matched" is never mistaken for "nothing ran".
    """

    items: list[dict[str, Any]] = field(default_factory=list)
    outcome: ToolOutcome = "not_found"


def _score(tool: Any) -> float:
    """`Number.isFinite(Number(t?.similarity)) ? n : -Infinity`.

    The default matters and it is NOT `None`: an ABSENT `similarity` is `undefined`, and
    `Number(undefined)` is NaN, so the tool sorts LAST. An explicit `null` is
    `Number(null) === 0`, which sorts above a negative score. Reading both as `None` would
    collapse the two and quietly promote a tool that carries no score at all.
    """
    n = jsc.js_number(jsc.get(tool, "similarity", jsc.UNDEFINED))
    if isinstance(n, float) and (jsc.is_nan(n) or n in (float("inf"), float("-inf"))):
        return float("-inf")
    return float(n)


def _label(tool: Any) -> str:
    """`String(t?.name ?? '')`."""
    return jsc.nullish_str(jsc.get(tool, "name"))


def tool_filter(candidates: Any, *, has_product: bool | None) -> ToolPick:
    """ONE tool per turn: highest `similarity`, tiebreak `name` ASC.

    The BODY is n8n's, unchanged and graded byte for byte against 38 captures (D8), which
    is why the ranking is still here after the pick stopped being a ranking. `select_tool`
    now hands it exactly one candidate off the domain row (similarity 1.0), so the sort has
    one element and the argmax is the identity - the node keeps working the way its
    captures say it does, and nothing about how the candidate was chosen leaked into it.

    Emitting exactly one item is structural, not incidental: the per-tool fan-out that used
    to sit downstream is deleted, so two items here would run the whole fetch, compile and
    send chain twice - two WhatsApp messages to one customer.

    By the time a candidate list reaches this function it is already final: `run_fetch`
    stamps `_tool_pick.source` on the OUTPUT rather than reaching in here, so this stays a
    ported node with no CRM-specific rule grafted onto it.
    """
    raw_tools = jsc.array(candidates)
    # `sort((a,b) => cmp(score(b), score(a)) || cmp(label(a), label(b)))`, and Python's
    # sort is stable like the JS engine's, so equal keys keep the input order.
    ordered = sorted(raw_tools, key=lambda t: (-_score(t), _label(t)))
    if not ordered:
        # 0 tools in, 0 items out - today's behaviour exactly. The OUTCOME is what makes
        # the difference visible (H11); the item list stays empty for parity.
        return ToolPick(items=[], outcome="not_found")
    best = ordered[0]
    picked_name = _label(best)
    return ToolPick(
        items=[
            {
                "json": {
                    **(best if isinstance(best, dict) else {}),
                    "name": picked_name,
                    "_tool_pick": {
                        "chosen": picked_name,
                        "rejected": [
                            {"name": _label(t), "similarity": _score(t)} for t in ordered[1:]
                        ],
                        "count": len(raw_tools),
                        "has_product": has_product,
                    },
                }
            }
        ],
        outcome="picked",
    )


def select_tool(domain: str | None) -> list[dict[str, Any]]:
    """The domain's tool, read off `turn.policy.default_policy()` (AC-1594: was
    `contracts.DOMAIN_SPEC`, now the frozen seed `chatbot_domains` is migrated from). No
    embedding, no database, no network.

    `[{"name": default_policy().domain(domain).tools[0], "similarity": 1.0}]` for a
    domain with a non-empty `tools` tuple, `[]` for everything else: no domain, a domain
    outside the
    table, and the two domains that answer from nothing (`goods_receive`, `ideate`). The
    empty list reaches `tool_filter` and ends the turn `not_found`, exactly as a zero-row
    search did (H11).

    NULL DOMAIN IS A NARROWING, and a deliberate one: the search ran UNFILTERED when
    `domain` was null, so such a turn could still come back with a tool, and this returns
    nothing. Measured on `sorento_ai_automation_0907`: of 994 `business_query` turns, 0
    reached the fetch step with a null or missing `domain_hint`, so the narrowing has no
    measured effect. A tool picked by cosine distance alone, with no domain to answer
    from, was never a defensible answer anyway.

    **Why the vector search went (owner ruling, 8 Sep 2026: "we can drop the rag from
    chatbot lane").** The candidate set was already this literal, and it is small: 4 tools
    for `master_products`, 3 for the next three domains, 2 for two more, 1 for the last
    four - median 2. Measured over every business turn in the 7 Sep 2026 prod copy
    (`sorento_ai_automation_0907`, `chatbot.turns.trace` `_tool_pick.chosen`, 740 turns),
    the pick was the domain's FIRST-LISTED tool on all 740; not one variant
    (`orders_by_product_list`, `incoming_stock_by_product`, `incoming_stock_shipments`,
    `brands_list`, `product_categories_list`, `units_of_measure_list`,
    `promotion_attachments_list`, `promotion_products_list`,
    `resource_attachments_catalogue`, `resource_attachments_current_stock_list`,
    `warehouses_list`, `certificates_list`) was ever chosen. One embedding call per turn
    was deciding a question with one answer.

    It also could not be trusted to keep deciding it. The search needed a registry row and
    an embedded chunk per tool, and that seeding chain cannot run in the deployed backend
    image (the MCP catalogue is not in it, PR #748), so production's tool pool has been
    frozen since 2 June 2026: every tool added after that date was unretrievable, and "last
    in for SRT62-GM" answered "no spo_allocation matched these" with 10 fully-received
    allocations in the table. Reading the table makes that failure class impossible instead
    of monitored.

    The FIRST entry of each `tools` tuple is therefore a contract. The rest stay where they
    are as allow-list members for the probes and the cross-domain rung
    (`CHATBOT_READ_ONLY_TOOLS` is derived from `DOMAIN_CLAIMED_TOOLS`), not as candidates.

    A `domain` outside the parser's own declared enum should never reach this call in the
    first place - `contracts.coerce_domain_hint` guards both ways in: the parser's emission
    in `output_exchange.py`, and the contact's carried memory in `engine.py`. Evidence turn
    b5b19cec-dccc-4eda-b766-1aeb1362957b arrived with `domain_hint: "purchasing"`, a TEAM
    name, and ended `not_found`; one that got past both guards would end the same way here,
    by falling off the table rather than by zeroing a `LIKE` filter.
    """
    row = default_policy().domain(domain) if domain else None
    if row is None or not row.tools:
        return []
    return [{"name": row.tools[0], "similarity": 1.0}]


# --------------------------------------------------------------------------- #
# tier-probe-plan / tier-probe-collect
# --------------------------------------------------------------------------- #


def tier_probe_plan(tier_gate: dict[str, Any] | None) -> list[dict[str, Any]]:
    """One item per entitled tier, so the probe can ask "any promotions here at all?".

    Per tier and not one batched call because promotion rows carry NO access level - there
    is no key to match a batched answer back on, and reading the tier out of a filename is
    the same string-guessing that mislabelled 1,934 rows on the brand work.

    An empty plan returns ONE defensive item rather than `[]`: returning nothing would skip
    every downstream node including the ask itself, and the customer would get silence
    instead of a degraded ask.
    """
    tg = tier_gate if isinstance(tier_gate, dict) else {}
    raw_plan = tg.get("tier_probe_plan")
    plan: list[Any] = raw_plan if isinstance(raw_plan, list) else []
    if len(plan) == 0:
        return [
            {
                "json": {
                    **tg,
                    "probe_tier": None,
                    "probe_access_levels": [],
                    "probe_skipped": True,
                }
            }
        ]
    return [
        {
            "json": {
                **tg,
                "probe_tier": jsc.get(p, "tier"),
                # the compound names for THIS tier alone, from the same `recompose` the
                # answer lane uses - never string-built, never the raw entitlement
                "probe_access_levels": (
                    jsc.get(p, "access_levels")
                    if isinstance(jsc.get(p, "access_levels"), list)
                    else []
                ),
                "probe_skipped": False,
            }
        }
        for p in plan
    ]


def tier_probe_collect(
    tier_gate: dict[str, Any] | None, *, plan_items: Any, probe_results: Any
) -> dict[str, Any]:
    """Fold the N per-tier probe answers back into ONE item.

    PAIRING is positional and that is an ordering ASSUMPTION, so it is checked rather than
    trusted: if the counts disagree every tier falls back to `null` (unknown), the renderer
    drops the annotation and the ask still works. A wrong pairing would tell a customer "no
    promotion" about a tier that has them, which is worse than silence.

    AVAILABILITY is deliberately generous - `has_result is True` OR a non-empty `answers` -
    because the envelope has carried both over time and the failure direction matters: a
    false "no promotion" hides real files, a false "has promotion" costs one wasted pick.
    """
    base = tier_gate if isinstance(tier_gate, dict) else {}
    results = list(jsc.array(probe_results))
    plan = list(jsc.array(plan_items))

    availability: dict[str, bool] | None = None
    if len(plan) > 0 and len(plan) == len(results) and jsc.get(plan[0], "probe_skipped") is not True:
        availability = {}
        for index, plan_item in enumerate(plan):
            tier = jsc.get(plan_item, "probe_tier")
            if not jsc.truthy(tier):
                continue
            j = results[index] if jsc.truthy(results[index]) else {}
            rows = jsc.get(j, "answers") if isinstance(jsc.get(j, "answers"), list) else []
            availability[tier] = jsc.get(j, "has_result") is True or len(rows) > 0

    # `None` availability means "we could not determine this", NOT "nothing is available".
    # The renderer and the router both read it that way.
    any_available = any(availability.values()) if availability is not None else True
    return {
        **base,
        "tier_availability": availability,
        "tier_available_list": (
            [t for t in default_policy().tier_order if availability.get(t)]
            if availability is not None
            else None
        ),
        "tier_any_available": any_available,
        "_tier_probe_count": len(results),
        "_tier_probe_planned": len(plan),
    }


# --------------------------------------------------------------------------- #
# entity-ids-transformer
# --------------------------------------------------------------------------- #

TYPE_TO_PARAM: dict[str, str] = {
    "product": "product_ids",
    "promotion": "promotion_ids",
    "order": "order_ids",
    "customer_order": "order_ids",
    "order_number": "order_ids",
    "customer": "customer_ids",
    "transporter": "transporter_ids",
    "form": "form_ids",
    "shipment": "shipment_ids",
    "inbound_shipment": "shipment_ids",
    # PLURAL, and the singular is a silent drop: `crm_master_product_attachments_list` -
    # the tool the dym / sibling / incoming probes hit - accepts ONLY the plural, and when
    # the singular was sent the probes got EVERY attachment and a Technical-Specifications
    # row was reported as "has certificate".
    "attachment_type": "attachment_type_ids",
    "attachment": "attachment_ids",
    "certificate": "certificate_ids",
    # Four tools accept it: `crm_inventory_stock_balance_list`,
    # `crm_inventory_warehouses_list`, `crm_procurement_spo_allocations_last_receipt_list`
    # and `crm_procurement_po_last_cost_list`.
    "warehouse": "warehouse_ids",
    # #1262 slice 9 (F1a), AC-S9-4/AC-S9-6: `crm_outstanding_report` and the order
    # tools that already take `product_ids` (orders list, orders by product) all
    # take `brand_ids` the same way.
    "brand": "brand_ids",
}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)


def is_uuid(value: Any) -> bool:
    """The one shape check for "is this a real uuid" (#1262 slice 2, F1c) - every seam
    that writes a `uuid` field or a `*_ids` filter off a bare string calls this rather
    than growing its own regex. A kind-pick's printed label ("Sorento (customer)") is
    not one, and neither is an unresolved code the resolver never placed; only a real
    `_UUID_RE`-shaped id is.
    """
    return bool(jsc.truthy(value) and _UUID_RE.match(jsc.js_string(value)))


def _resolved_brand_ids(semantic_input: Any) -> list[str]:
    """The brand ids `lanes/business/__init__._resolve_outstanding_brand_ids` resolved
    for this turn off the live brand list (the key keeps its first caller's name), real
    uuids only - the same `is_uuid` guard every other `<entity>_ids` argument uses."""
    return [b for b in jsc.array(jsc.get(semantic_input, "outstanding_brand_ids")) if is_uuid(b)]


def entity_has_resolved_uuid(entity: dict[str, Any]) -> bool:
    """The single-entity half of `entity_ids_transformer`'s own `missing_or_bad_uuid`
    read, lifted out so a second caller can ask the SAME question rather than a second
    uuid rule: a real, `_UUID_RE`-shaped id, not a bare code the resolver could not
    place. `turn/fetch.py::_climb` is that second caller (R1 addendum, AC-1688) - a
    cross-domain rung must never run for a spec where nothing in it would actually
    narrow the rung's own tool call, the exact test this transformer's loop already
    applies per entity before building `*_ids`.
    """
    uuid = jsc.get(entity, "uuid") if jsc.truthy(entity) else None
    return is_uuid(uuid)


# Tools that answer a DOCUMENT request and must be given something to narrow by.
# `crm_resource_attachments_list`'s own contract says it: "They named NO document at all ->
# this tool returns NOTHING, by design. `contact_id` alone is NOT a narrowing filter". The
# transformer is uuid-only, so an entity it could not resolve contributes no `*_ids` key at
# all, and the call went out carrying `view` / `contact_id` / `space_id` only - a listing of
# every attachment the contact is entitled to, which is a directory dump, not an answer.
#
# SF6 (security review, PLAN-chatbot-last-purchase-cost.md, 12 Sep 2026):
# `crm_procurement_po_last_cost_list` joins for the same reason - a brand/category-only ask
# builds no `*_ids` at all (neither hint maps to a `TYPE_TO_PARAM` entry) and a
# warehouse-only ask builds `warehouse_ids` with no `product_ids`, so without this either
# would fall through to the service's UNSCOPED branch: every product's cost at that
# warehouse, or across the whole table, none of them named by the customer. Refused as an
# absence instead, same as an unfiltered document ask.
# `ENTITY_FILTER_REQUIRED_TOOLS` and `PRODUCT_ID_REQUIRED_TOOLS` moved to
# `turn/policy_rows.py` (AC-1594, S6): hand-curated per-TOOL exceptions, not domain or
# kind data, so they have no row in either policy table - see that module's own comment
# for why they live there instead. Referenced here as `policy_rows.X` (not imported by
# name) so this module keeps no attribute of either name.

# What counts as narrowing on those tools: every entity-id param the transformer can emit,
# plus the document-type filters the tool takes by name.
NARROWING_PARAMS: frozenset[str] = frozenset(TYPE_TO_PARAM.values()) | frozenset(
    {
        "attachment_type_code",
        "attachment_type_codes",
        "attachment_type_id",
        "directory_id",
        "uploaded_by",
    }
)


def has_narrowing_filter(args: Any, *, tool_name: str | None = None) -> bool:
    """True when the built args carry at least one non-empty narrowing key.

    `tool_name` in `policy_rows.PRODUCT_ID_REQUIRED_TOOLS` narrows the bar to
    `product_ids` specifically (SF6) - every other `policy_rows.
    ENTITY_FILTER_REQUIRED_TOOLS` member keeps the generic "any narrowing param" rule.
    """
    if not isinstance(args, dict):
        return False
    if tool_name in policy_rows.PRODUCT_ID_REQUIRED_TOOLS:
        return jsc.truthy(args.get("product_ids"))
    return any(jsc.truthy(args.get(key)) for key in NARROWING_PARAMS)



# Params the tool takes as a SCALAR string rather than a list. Empty today: the
# `attachment_type_id` singular was removed when the plural landed. Kept because the shape
# matters as much as the spelling - sending an array where a scalar is expected is the same
# silent drop as sending the wrong name.
SCALAR_PARAMS: frozenset[str] = frozenset()

#: The sales analysis's `group_by` -> the route's `rows` (the years are always across).
#: "month" is months down the side; "year" and no axis said are one line per channel.
SALES_ANALYSIS_GROUP_BY = {"": "channel", "year": "channel", "month": "month"}
#: The bases `GET /sales/analysis` answers (finance S1 added invoiced); anything else,
#: or none, is Delivered, the report's default.
SALES_ANALYSIS_BASES = ("ordered", "delivered", "invoiced")

DATE_PARAMS: dict[str, tuple[str, str]] = {
    "crm_order_management_orders_list": ("actual_delivery_date_from", "actual_delivery_date_to"),
    "crm_order_management_orders_by_product_list": (
        "actual_delivery_date_from",
        "actual_delivery_date_to",
    ),
    "crm_incoming_stock_list": ("eta_from", "eta_to"),
    "crm_incoming_stock_by_product": ("eta_from", "eta_to"),
    "crm_incoming_stock_shipments": ("eta_from", "eta_to"),
    "crm_marketing_promotions_list": ("period_from", "period_to"),
    "crm_resource_attachments_list": ("uploaded_at_from", "uploaded_at_to"),
    "crm_resource_attachments_catalogue": ("uploaded_at_from", "uploaded_at_to"),
    "crm_sla_conversation_event_logs_list": ("date_from", "date_to"),
    "crm_procurement_po_placed_list": ("expected_date_from", "expected_date_to"),
    # S4 point 8 (PLAN-chatbot-outstanding-report.md): the report's own contract
    # is order_date, never actual_delivery_date - a pending DO by definition has
    # none, and the SO arm has no delivery date at all.
    "crm_outstanding_report": ("order_date_from", "order_date_to"),
    # PLAN-chatbot-sales-report.md S4 wiring point 5: the report's own contract, on
    # the bucket date (required_date, else order_date) - never actual_delivery_date.
    "crm_sales_report": ("date_from", "date_to"),
    # PLAN-chatbot-top-x-hot-selling-24sep.md S4 point 6: the same bucket date. No date
    # sends none: the ROUTE defaults to the current calendar year and echoes it (as
    # built on PR #1263, superseding the plan's lane-side default).
    "crm_top_selling_report": ("date_from", "date_to"),
    # AC-71: the low stock report's window narrows which sales orders the fresh plan
    # counts as demand - the run's own "plan until" pair, under the route's names.
    "crm_low_stock_report": ("date_from", "date_to"),
    # PLAN-retail-sales-reports-26sep S1: the period of the sales analysis, by order date.
    "crm_sales_analysis": ("date_from", "date_to"),
}

def _current_myt_year() -> int:
    """S18: the current CALENDAR YEAR in Malaysia time (UTC+8, no DST) - the SAME
    formula `engine._current_date_directive` uses for "today" in the parser prompt,
    so this default and that directive can never disagree about what year it is."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(hours=8)).year


# S4 point 3 (AC-1131 fetch half): so_outstanding/do_outstanding/outstanding_both ->
# so/do/both. Bare "outstanding" is deliberately absent - it is resolved by the
# field-reveal gate in `run_fetch`, not by this table (D13).
ORDER_STATUS_TO_SCOPE: dict[str, str] = {
    "so_outstanding": "so",
    "do_outstanding": "do",
    "outstanding_both": "both",
}

# D13, S2 (security review, 13 Sep 2026): the one sentence a contact without
# `sales_orders.outstanding` sees in front of EITHER SO-gated answer -
# `crm_outstanding_report`'s own redirect to the DO block (`_outstanding_report_
# output` below) and the legacy `so_outstanding` bucket's redirect to `outstanding`
# (`lanes/business/__init__.py::run_fetch`). One literal, not two, so the two
# refusals can never drift apart in wording.
SO_NOT_ENABLED_MESSAGE = "Sales order figures are not enabled for your account."

ORDER_TOOLS: frozenset[str] = frozenset(
    {"crm_order_management_orders_list", "crm_order_management_orders_by_product_list"}
)

# A3/A5/A6 (chatbot-growth-r1): the tools whose `group_by` this transformer
# passes straight through as a query param (the backend validates the axis;
# see `ORDER_GROUP_BY_AXES` / each route's own set - not re-validated here).
GROUP_BY_TOOLS: frozenset[str] = frozenset(
    {
        "crm_order_management_orders_list",
        "crm_procurement_po_placed_list",
    }
)

# PLAN-po-spo-warehouse-29sep S6: the parser's `sort_by` mapped to each PO/SPO tool's own
# `sort` key. A key a tool has no column for is absent, so it sends nothing and the tool's
# own default order answers. `SORT_DEFAULT_DIR` is the direction when the parser named none.
SORT_KEY_BY_TOOL: dict[str, dict[str, str]] = {
    "crm_procurement_po_placed_list": {
        "date": "po_date",
        "expected_date": "expected_date",
        "quantity": "ordered_qty",
        "outstanding": "outstanding_qty",
        "product": "product",
        "supplier": "supplier",
    },
    "crm_procurement_spo_allocations_last_receipt_list": {
        "date": "spo_date",
        "expected_date": "spo_date",
        "quantity": "spo_quantity",
        "received_date": "gr_date",
        "received_quantity": "gr_quantity",
    },
}
#: A tool's sort key that orders by a RESTRICTED field, and the field-reveal key that
#: permits it (the same key the presenter's `restrict()` names for that field). Without
#: the grant the sort is not sent (security review, PR #1373, finding 1).
RESTRICTED_SORT_KEYS: dict[str, dict[str, str]] = {
    "crm_procurement_po_placed_list": {"supplier": "purchase_orders.supplier"},
}
SORT_DEFAULT_DIR: dict[str, str] = {
    "date": "desc",
    "expected_date": "asc",
    "quantity": "desc",
    "outstanding": "desc",
    "received_date": "desc",
    "received_quantity": "desc",
    "product": "asc",
    "supplier": "asc",
}

# A6: the one tool with its OWN `top_n` param (default 1, "last 3 in"); every
# other GROUP_BY_TOOLS/ORDER_TOOLS member aliases `top_n` to `limit` instead
# (above), since it has no `top_n` param of its own.
TOP_N_DIRECT_TOOLS: frozenset[str] = frozenset(
    {
        "crm_procurement_spo_allocations_last_receipt_list",
        # PLAN-chatbot-last-purchase-cost.md: this tool has its own `top_n` too.
        "crm_procurement_po_last_cost_list",
    }
)

# n8n hard-codes this and OVERRIDES the `semantic_input` value with it (which carried the
# identical string in all 24 sampled executions). D5 says the respond.io space id comes
# from the default respond workspace row, and it scopes to the VALUE, not to a list of
# call sites - so the port takes it as a parameter with n8n's own literal as the default.
# The replay pins the literal, so no fixture moves; production passes the workspace row's.
SPACE_ID = "364817"


def space_id_or_default(space_id: Any) -> str:
    """The ONE workspace fallback, so the four tool call sites cannot disagree.

    `entity-ids-transformer`, `Call 'sub-get-results'`, `crossdomain-probe` and the three
    did-you-mean probes all send the same workspace, and an install with no default
    respond workspace row must not have some of them send `null` while the others send
    n8n's literal - the MCP read is scoped by it.
    """
    return space_id if space_id else SPACE_ID

# `tier-probe`'s own tool. The per-tier question is "are there any promotions at this tier
# at all", so it is the promotions read, asked once per entitled tier - promotion rows carry
# no access level, so there is no key a single batched answer could be matched back on.
TIER_PROBE_TOOL = "crm_marketing_promotions_list"


# The row cap a counted set's fetch asks each leg's tool for: the tool's own maximum
# `limit` (`api/v1/inventory/stock.py` allows 5000, the attachment and promotion lists
# `MAX_PAGE_LIMIT` 1000, `/incoming-stock/list` 50). Up to `answer.SET_LIST_MAX`
# products at about 20 rows each fit the first three.
SET_ROW_LIMIT: dict[str, int] = {
    "crm_inventory_stock_balance_list": 1000,
    "crm_master_product_attachments_list": 1000,
    "crm_marketing_promotion_products_list": 1000,
    "crm_incoming_stock_list": 50,
}


def _set_named_count(semantic_input: Any) -> int | None:
    """How many of a counted set the customer asked to see (`top_n`), or None."""
    return named_count(jsc.get(semantic_input, "top_n"))


def _set_list_size(semantic_input: Any) -> int:
    """How many products of a counted set one reply lists: the named count, never past
    `answer.SET_LIST_MAX`. Deferred import: `answer.py` imports from this module."""
    from app.services.chatbot.lanes.business import answer as answer_mod

    named = _set_named_count(semantic_input)
    return min(named, answer_mod.SET_LIST_MAX) if named else answer_mod.SET_LIST_MAX


def _names_the_ask(attribute: str) -> bool:
    """Is this requested attribute the ask's own word ("incoming", "eta", "stock",
    "cert"), rather than a field of the answer? Owner hand test of rounds 4 to 6 on PR
    #833, item 1: an incoming answer closed each row with "*Incoming:* not recorded yet"
    for the word that asked for it. Deferred import: `predicate` is a sibling module."""
    from app.services.chatbot.lanes.business.predicate import names_a_leg

    return names_a_leg(attribute)


def _rendered_product_count(items: list[Any]) -> int | None:
    """Distinct products the rendered rows name: a row's "Product Code" field, else its
    title (the stock tool's availability rows carry no fields). None when no row names
    one, so a result type this header never fires for is left alone."""
    codes: set[str] = set()
    for it in items:
        code = ""
        for f in jsc.get(it, "fields") or []:
            if isinstance(f, dict) and f.get("label") == "Product Code":
                code = jsc.nullish_str(f.get("value")).strip()
                break
        if not code and not (jsc.get(it, "fields") or []):
            code = jsc.nullish_str(jsc.get(it, "title")).strip()
        if code:
            codes.add(code)
    return len(codes) if codes else None


def _self_reference(semantic_input: Any) -> bool:
    """CHATBOT-SELFREF-SCOPE (owner rule Q6a, hand test 30 Sep 2026): "my sales" means
    ALL the asker's linked accounts. An account the conversation carried from an earlier
    report (`outstanding_carried_customer_ids`, the offer's own subject) is what a "1" or
    "this month only" narrows, never what "my" means, so on a self-reference turn the
    carry does not replace this turn's own ids (the links, or the account the message
    itself named)."""
    return jsc.get(semantic_input, "self_reference") is True


def entity_ids_transformer(
    trigger: dict[str, Any] | None, *, space_id: str | None = None
) -> dict[str, Any]:
    """The MCP tool's arguments, built from the gate's already-resolved entities.

    `space_id` defaults to n8n's own literal so a replay is byte-equal; production passes
    the default respond workspace's (D5), which is the same value on this install and the
    right one on any other.
    """
    trig = trigger if isinstance(trigger, dict) else {}
    semantic_input: Any = trig.get("semantic_input")
    entities: Any = trig.get("entities")

    params: dict[str, list[Any]] = {}
    seen_uuids: set[Any] = set()
    unmapped_types: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for e in jsc.array(entities):
        entity_type = jsc.get(e, "entity_type") if jsc.truthy(e) else None
        uuid = jsc.get(e, "uuid") if jsc.truthy(e) else None
        if not entity_has_resolved_uuid(e):
            skipped.append(
                {"code": jsc.get(e, "code") if jsc.truthy(e) else None, "reason": "missing_or_bad_uuid"}
            )
            continue
        if uuid in seen_uuids:
            continue
        param = TYPE_TO_PARAM.get(entity_type) if isinstance(entity_type, str) else None
        if not param:
            unmapped_types.append({"entity_type": entity_type, "uuid": uuid})
            continue
        seen_uuids.add(uuid)
        bucket = params.setdefault(param, [])
        if uuid not in bucket:  # `Set.add` - insertion-ordered and deduped
            bucket.append(uuid)

    out: dict[str, Any] = {}
    truncated: list[dict[str, Any]] = []
    for param, values in params.items():
        if param not in SCALAR_PARAMS:
            out[param] = values
            continue
        out[param] = values[0]
        # The gate normally narrows to exactly one. If more ever arrives, record it rather
        # than truncate in silence.
        if len(values) > 1:
            truncated.append({"param": param, "kept": values[0], "dropped": values[1:]})

    out["_diagnostics"] = {
        "entities_in": len(jsc.array(entities)),
        "total_uuids_passed": sum(
            len(v) if isinstance(v, list) else (1 if jsc.truthy(v) else 0) for v in out.values()
        ),
        "scalar_truncated": truncated,
        "skipped": skipped,
        "unmapped_types": unmapped_types,
    }

    out["view"] = "render"
    # `out.date_mode = semantic_input?.date_mode` - an ABSENT key leaves `undefined` here,
    # and `JSON.stringify` drops an undefined value rather than writing null. So the key is
    # omitted, not nulled, when `semantic_input` does not carry it. Measured: 7 of the 20
    # `entity-ids-transformer` captures have no `date_mode` at all.
    if jsc.has(semantic_input, "date_mode"):
        out["date_mode"] = semantic_input["date_mode"]

    tool_name = jsc.js_string(trig["tool"]).strip() if jsc.truthy(trig.get("tool")) else ""
    start = jsc.get(semantic_input, "date_filter_start") or trig.get("date_filter_start")
    end = jsc.get(semantic_input, "date_filter_end") or trig.get("date_filter_end")
    date_params = DATE_PARAMS.get(tool_name)
    if date_params:
        if jsc.truthy(start):
            out[date_params[0]] = start
        if jsc.truthy(end):
            out[date_params[1]] = end

    # Same undefined-drop rule as `date_mode` above. `contact_id` and `space_id` are
    # re-assigned unconditionally at the bottom, so their absence here is invisible; these
    # four assignments are kept in the JS's own order anyway, because key order is what a
    # reader diffs.
    for key in ("contact_id", "space_id", "access_levels", "is_active"):
        if jsc.has(semantic_input, key):
            out[key] = semantic_input[key]

    # S4 points 2/7/9 (PLAN-chatbot-outstanding-report.md): crm_outstanding_report's
    # OWN contract (`product_code` a string, `scope`, `customer_ids`, `warehouse_codes`
    # csv, `order_date_from/to` above) is not the generic UUID-list shape every other
    # order tool takes, so it is built here rather than through TYPE_TO_PARAM.
    if tool_name == "crm_outstanding_report":
        out.pop("product_ids", None)
        out.pop("warehouse_ids", None)
        # AC-1119: one rule for which code this report is about, shared with both filter
        # builders (`outstanding_product_codes`).
        picked_codes = outstanding_product_codes(entities, semantic_input)
        if len(picked_codes) == 1:
            out["product_code"] = picked_codes[0]
        elif picked_codes:
            # The SEVERAL-code form, only when there are several: one code keeps the
            # argument it has always travelled under, so an ordinary outstanding ask is
            # byte-identical to before.
            out["product_codes"] = picked_codes
        scope = jsc.get(semantic_input, "outstanding_scope")
        if jsc.truthy(scope):
            out["scope"] = jsc.js_string(scope)
        warehouse_codes = jsc.get(semantic_input, "outstanding_warehouse_codes")
        if isinstance(warehouse_codes, list) and warehouse_codes:
            out["warehouse_codes"] = warehouse_codes
        # #1262 slice 9 (F1a): the brand word(s) this turn named, already resolved to
        # ids by `_resolve_report_product_and_location`'s own brand step (the token
        # never reached the shared resolver, so this is the only place it becomes an
        # id) - a brand alone is a valid subject for this report (AC-S9-4).
        # N1 (security review, 26 Sep 2026): only real uuids ever forward - the same
        # `is_uuid` guard every other `<entity>_ids` argument in this function uses,
        # never a bare `isinstance(..., list)` check that would let a non-uuid string
        # reach the tool call.
        brand_ids = _resolved_brand_ids(semantic_input)
        if brand_ids:
            out["brand_ids"] = brand_ids
        # AC-1105 (review round, 13 Sep 2026): the WORD the customer typed, echoed by
        # the route onto its own body so the presenter can render "IB (BRW-IB, MWH-IB)".
        # The lane never re-renders that header itself: one writer, one wording.
        location_token = jsc.js_string(jsc.get(semantic_input, "outstanding_location_token") or "")
        # The route caps it at 32 characters (it is a word, not a filter). `warehouse_code`
        # is `String(50)`, so an exact match on a very long code could otherwise 422 the
        # whole report over a header echo; the codes still travel, the word just does not.
        if location_token and len(location_token) <= 32 and isinstance(warehouse_codes, list) and warehouse_codes:
            out["location_token"] = location_token
        # D13/AC-1141: the same wire trip for the withheld SO half, so the refusal line
        # prints IN PLACE (after the header, before the DO block) rather than in front
        # of the whole reply.
        if jsc.truthy(jsc.get(semantic_input, "outstanding_so_refused")):
            out["so_refused"] = True
        # AC-1132: the scope-answer's carried customer_ids are ALREADY resolved UUIDs
        # (restored by `head/output_exchange.py`, never re-parsed) - they win over
        # whatever THIS turn's own (empty) entity list produced.
        # #1262 slice 2 (F1c): a session's carried customer subject is a resolved
        # uuid or it is nothing - a kind-pick's printed label ("Sorento (customer)")
        # riding on this same key must never reach the tool as a `customer_ids` filter.
        carried_customers = [
            u for u in jsc.array(jsc.get(semantic_input, "outstanding_carried_customer_ids")) if is_uuid(u)
        ]
        if carried_customers and not _self_reference(semantic_input):
            out["customer_ids"] = carried_customers
        # AC-1138 (D10 on main): "1"/"2" against an open detail offer re-runs THIS
        # SAME tool with `detail=so|do` - the MCP layer swaps in the numbered list
        # (S4 point 5); the route's own computation is unchanged by it.
        detail_pick = jsc.get(semantic_input, "outstanding_detail_pick")
        if detail_pick in ("so", "do", "both"):
            out["detail"] = detail_pick

    # PLAN-chatbot-sales-report.md S4 wiring point 5: `crm_sales_report`'s OWN
    # contract - the SAME shape `crm_outstanding_report` above builds by hand
    # (`product_code` a string, `customer_ids`, `warehouse_codes` csv, `date_from`/
    # `date_to` above), plus `channel` (S4 point 5's `sales_channel` -> `channel`).
    if tool_name == "crm_sales_report":
        out.pop("product_ids", None)
        out.pop("warehouse_ids", None)
        picked_code = sales_report_product_code(entities, semantic_input)
        if jsc.truthy(picked_code):
            out["product_code"] = picked_code
        warehouse_codes = jsc.get(semantic_input, "outstanding_warehouse_codes")
        if isinstance(warehouse_codes, list) and warehouse_codes:
            out["warehouse_codes"] = warehouse_codes
        location_token = jsc.js_string(jsc.get(semantic_input, "outstanding_location_token") or "")
        if location_token and len(location_token) <= 32 and isinstance(warehouse_codes, list) and warehouse_codes:
            out["location_token"] = location_token
        # AC-1132-equivalent: the offer's carried customer_ids are ALREADY resolved
        # UUIDs (restored by `head/output_exchange.py`, never re-parsed) - they win
        # over whatever THIS turn's own (empty) entity list produced.
        # #1262 slice 2 (F1c): same uuid-only rule as `crm_outstanding_report` above.
        carried_customers = [
            u for u in jsc.array(jsc.get(semantic_input, "outstanding_carried_customer_ids")) if is_uuid(u)
        ]
        if carried_customers and not _self_reference(semantic_input):
            out["customer_ids"] = carried_customers
        # R-B3 (reviewer finding, Phase 3 fix round): the turn's OWN sales_channel wins
        # when given; a pick or a refinement of an open sales_report_detail offer names
        # no channel of its own, so the offer's CARRIED channel (restored by
        # `head/output_exchange.py::_apply_outstanding_pending`) is the fallback.
        channel = jsc.get(semantic_input, "sales_channel")
        if not jsc.truthy(channel):
            channel = jsc.get(semantic_input, "outstanding_carried_channel")
        if jsc.truthy(channel):
            out["channel"] = jsc.js_string(channel)
        # AC-SR-28: a drill-down picked off the open `sales_report_detail` offer re-runs
        # this SAME tool with that `group_by` (the offer's own value, `SALES_REPORT_GROUP_BYS`);
        # the window, accounts, location and channel ride on the carried keys above.
        detail_pick = jsc.get(semantic_input, "outstanding_detail_pick")
        if detail_pick in SALES_REPORT_GROUP_BYS:
            out["group_by"] = detail_pick

        # S18 (owner ruling, mid-lane): a PRODUCT-ONLY ask (a resolved product, no
        # customer) with no date window defaults to the CURRENT CALENDAR YEAR
        # (Malaysia time) - a customer ask, or a customer+product ask, with no date
        # stays all dates (S4, unchanged). Built HERE, in the lane, never the route:
        # `date_from`/`date_to` absent still means all dates when the route is
        # called directly (e.g. n8n). "All dates" said in words (`broaden_axis ==
        # "date"`, the SAME field R15's date-window-drop already reads) turns the
        # default off - no word table, the parser already decided it.
        if (
            jsc.truthy(out.get("product_code"))
            and not jsc.truthy(out.get("customer_ids"))
            and not jsc.truthy(out.get("date_from"))
            and not jsc.truthy(out.get("date_to"))
            and jsc.lower_or_empty(jsc.get(semantic_input, "broaden_axis")) != "date"
        ):
            year = _current_myt_year()
            out["date_from"] = f"{year}-01-01"
            out["date_to"] = f"{year}-12-31"
            # Mutated onto `semantic_input` too (not just `out`, this function's own
            # return value): `output_structurer` -> `_sales_report_output` ->
            # `_sales_report_filters_from_ctx` reads `ctx["semantic_input"]` - the
            # SAME dict object `run_fetch` built `trigger` with - to arm the detail
            # offer's stored filter set, so the default has to land there too, or a
            # later "1" would list every date instead of the year just answered.
            if isinstance(semantic_input, dict):
                semantic_input["date_filter_start"] = out["date_from"]
                semantic_input["date_filter_end"] = out["date_to"]

    # PLAN-chatbot-top-x-hot-selling-24sep.md "Lane wiring (S4)" point 7:
    # `crm_top_selling_report`'s own contract. Everything comes off the parser's own
    # fields, carried on the focus slot `semantic_input["top_selling"]`
    # (`turn/apply._top_selling_rules`), never the message text.
    if tool_name == "crm_top_selling_report":
        out.pop("product_ids", None)
        out.pop("warehouse_ids", None)
        slot = jsc.get(semantic_input, "top_selling")
        slot = slot if isinstance(slot, dict) else {}
        out["rank_by"] = jsc.js_string(slot.get("rank_by"))
        if slot.get("basis") in ("delivered", "ordered"):
            out["basis"] = slot["basis"]
        category_grain = slot.get("rank_group") == "category"
        if category_grain:
            out["group"] = "category"
        # A category grain ranks categories against each other, so a category FILTER
        # has nothing to narrow (plan, filters matrix: ignored, the header prints all).
        category_ids = jsc.get(semantic_input, "top_selling_category_ids")
        if isinstance(category_ids, list) and category_ids and not category_grain:
            out["category_ids"] = category_ids
        chosen = slot.get("customer_ids")
        if isinstance(chosen, list) and chosen:
            # "1" to "customer or sales agent?" (owner retest, 27 Sep 2026).
            out["customer_ids"] = [jsc.js_string(i) for i in chosen]
        dealer_ids = slot.get("dealer_customer_ids")
        if isinstance(dealer_ids, list) and dealer_ids:
            # A linked dealer's own ledgers its words named (`engine._top_selling_dealer_scope`).
            out["customer_ids"] = [jsc.js_string(i) for i in dealer_ids]
        # The agent and brand the ranking is narrowed by (`engine._top_selling_narrowing`,
        # owner retest 27 Sep 2026), never a customer.
        agent_ids = [jsc.js_string(i) for i in (slot.get("agent_ids") or []) if jsc.truthy(i)]
        agent_ids += [
            jsc.get(e, "uuid")
            for e in jsc.array(entities)
            if isinstance(e, dict) and e.get("entity_type") == "sales_agent" and e.get("uuid")
        ]
        if agent_ids:
            out["sales_agent_ids"] = list(dict.fromkeys(agent_ids))
        brand_ids = [jsc.js_string(i) for i in (slot.get("brand_ids") or []) if jsc.truthy(i)]
        if brand_ids:
            out["brand_ids"] = brand_ids
        if slot.get("rank_direction") == "bottom":
            # "cold selling", "least sold": the least sold first.
            out["direction"] = "bottom"
        channel = jsc.get(semantic_input, "sales_channel")
        if jsc.truthy(channel):
            out["channel"] = jsc.js_string(channel)
        detail_code = jsc.js_string(slot.get("detail_code") or "").strip()
        top_n = slot.get("top_n")
        if detail_code:
            # A picked row: that code's customers and months, same filters and basis.
            out["detail_code"] = detail_code
        elif isinstance(top_n, (int, float)) and not isinstance(top_n, bool) and top_n >= 1:
            # Owner, 30 Sep 2026: no 100 cap ("100, 200"). Only the shared safety ceiling
            # bounds it, and never silently: the lane notes it above the reply
            # (`lanes/business.top_selling_ceiling_note`).
            from app.services.sales_report_service import TOP_SELLING_N_CEILING

            out["n"] = min(int(top_n), TOP_SELLING_N_CEILING)
        else:
            # No N named: the full count and no rows, so the reply states the total
            # and asks how many (owner, PR #1258 05:32Z) without the whole book being
            # sent to be counted (review N1). One row comes back as is.
            out["count_only"] = True

    # PLAN-low-stock-report S6 (AC-66/AC-71): this tool's own contract is CODES too - the
    # route resolves warehouse and product CODES, and a UUID would silently match nothing.
    # So the generic UUID params are POPPED rather than left to be dropped by FastMCP,
    # where a replay diff could not show they had gone. The warehouse codes come off
    # `semantic_input` (set by `run_fetch` from the SAME token resolution the outstanding
    # report uses, so a location word like "IB" is already expanded to exact codes); the
    # product codes come off the entities' own resolved codes.
    if tool_name == "crm_low_stock_report":
        out.pop("warehouse_ids", None)
        out.pop("product_ids", None)
        # The warehouse scope comes off `semantic_input` (the token resolution `run_fetch`
        # already did); the product scope is read off the entities handed in. Those
        # entities have ALREADY been pruned to the ones the CURRENT MESSAGE named, in
        # `run_fetch`'s low-stock override (console round 3, defect B) - this function
        # cannot tell a carried entity from a fresh one, and `replace_combine` merges the
        # previous turn's into the gate's list, which is how a bare "low stock report"
        # came to be scoped to two products from an earlier question and planned "0 of 0".
        warehouse_codes = jsc.get(semantic_input, "low_stock_warehouse_codes")
        if isinstance(warehouse_codes, list) and warehouse_codes:
            out["warehouse_codes"] = warehouse_codes
        product_codes: list[str] = []
        for e in jsc.array(entities):
            if not isinstance(e, dict) or jsc.js_string(e.get("entity_type")) != "product":
                continue
            code = e.get("code") or e.get("canonical_code")
            if jsc.truthy(code) and jsc.js_string(code) not in product_codes:
                product_codes.append(jsc.js_string(code))
        if product_codes:
            out["product_codes"] = product_codes
        # LOWSTOCK-FILTER-ASK: the workbook filters `run_fetch` settled before calling
        # (`low_stock_ask.route_filters`). Absent keys keep the old whole-book call.
        filters = jsc.get(semantic_input, "low_stock_filters")
        if isinstance(filters, dict):
            for key in ("categories", "suppliers"):
                if filters.get(key):
                    out[key] = list(filters[key])
            if filters.get("split") and filters["split"] != "none":
                out["split"] = filters["split"]
        # #892 (console, 14 Sep): this tool REQUIRES both ids - the route 422s without
        # them, and they drive the company scope the write needs - so it must NOT depend on
        # the shared tail below surviving a refactor or a None `semantic_input`. Carry them
        # explicitly here, from the trigger first (`run_fetch` stamps `contact_id` on it),
        # then `semantic_input`, exactly the resolution the bottom of this function uses.
        _lsr_contact = trig.get("contact_id")
        if _lsr_contact is None:
            _lsr_contact = jsc.get(semantic_input, "contact_id")
        out["contact_id"] = jsc.nullish_str(_lsr_contact).strip()
        out["space_id"] = space_id_or_default(
            space_id if space_id is not None else jsc.get(semantic_input, "space_id")
        )

    # PLAN-retail-sales-reports-26sep S1: the sales analysis names an AXIS, a channel, a
    # basis, a company and a period - never an entity id. "by month" is months down the
    # side and the years across (the yearly comparison); anything else is one line per
    # channel, the years across. No period said is this calendar year as at today (G6).
    if tool_name == "crm_sales_analysis":
        for key in ("product_ids", "customer_ids", "warehouse_ids", "limit", "group_by", "top_n"):
            out.pop(key, None)
        group_by = jsc.js_string(jsc.get(semantic_input, "group_by") or "")
        # Every axis mapped by name (review round 2 S1). Any other `group_by` never gets
        # here: `run_fetch` says it cannot draw it and fetches nothing.
        out["rows"] = SALES_ANALYSIS_GROUP_BY[group_by]
        out["cols"] = "year"
        channel = jsc.get(semantic_input, "sales_channel")
        if channel in ("dealer", "project"):
            out["channel"] = channel
        basis = jsc.get(semantic_input, "sales_basis")
        out["basis"] = basis if basis in SALES_ANALYSIS_BASES else "delivered"
        company = jsc.get(semantic_input, "sales_company")
        if jsc.truthy(company):
            out["company"] = jsc.js_string(company).strip()
        if not jsc.truthy(out.get("date_from")) and not jsc.truthy(out.get("date_to")):
            from app.services.reports.registry import today_malaysia

            today = today_malaysia()
            out["date_from"] = f"{today.year}-01-01"
            out["date_to"] = today.isoformat()
        top_n = jsc.get(semantic_input, "top_n")
        if isinstance(top_n, int) and top_n > 0:
            out["n"] = top_n
        _sa_contact = trig.get("contact_id")
        if _sa_contact is None:
            _sa_contact = jsc.get(semantic_input, "contact_id")
        out["contact_id"] = jsc.nullish_str(_sa_contact).strip()
        out["space_id"] = space_id_or_default(
            space_id if space_id is not None else jsc.get(semantic_input, "space_id")
        )

    # S2 (review round, 13 Sep 2026): a warehouse entity on a PLAIN order ask.
    # `TYPE_TO_PARAM` maps it to `warehouse_ids`, which NEITHER order-list tool declares
    # ("any DO for X at BRW" therefore sent a param the MCP dropped, and the answer was
    # presented as if it had been scoped to BRW). Both tools take `warehouse_codes`
    # (S3), so the resolved CODE is what travels; the id is removed rather than left to
    # be dropped somewhere the operator cannot see it.
    if tool_name in ORDER_TOOLS and "warehouse_ids" in out:
        out.pop("warehouse_ids", None)
        codes: list[str] = []
        for e in jsc.array(entities):
            if not isinstance(e, dict) or jsc.js_string(e.get("entity_type")) != "warehouse":
                continue
            code = e.get("code") or e.get("canonical_code")
            if jsc.truthy(code) and jsc.js_string(code) not in codes:
                codes.append(jsc.js_string(code))
        if codes:
            out["warehouse_codes"] = codes

    # #1262 fix lane round 2, B1 (AC-S9-6): the orders tools take the SAME resolved
    # brand ids the outstanding report does. The live brand never reaches the shared
    # resolver (`turn_runtime.resolve_kinds`'s order-domain strip), so it is never a
    # gate row with a uuid and `TYPE_TO_PARAM["brand"]` alone could not carry it here.
    if tool_name in ORDER_TOOLS:
        brand_ids = _resolved_brand_ids(semantic_input)
        if brand_ids:
            out["brand_ids"] = brand_ids

    # order_status (order tools only): "outstanding" | "delivered" | "so_outstanding"
    # (A3, AC-905); omitted when null.
    if tool_name in ORDER_TOOLS and jsc.get(semantic_input, "order_status") in (
        "outstanding",
        "delivered",
        "so_outstanding",
    ):
        out["order_status"] = jsc.get(semantic_input, "order_status")

    # A quantity ask makes the CRM aggregate. A plain DO list sends nothing: the key is
    # ABSENT, never false, so the MCP payload is unchanged.
    req_attrs = (
        jsc.get(semantic_input, "requested_attributes")
        if isinstance(jsc.get(semantic_input, "requested_attributes"), list)
        else []
    )
    if tool_name in ORDER_TOOLS and any(
        jsc.nullish_str(a).strip() == "quantity" for a in req_attrs
    ):
        out["include_summary"] = True
        # `include_pipeline` (fix, 7 Sep 2026): opt-in, separately from
        # `include_summary` above, for the SAME reason `include_specs` /
        # `include_sellable` below are opt-in rather than a server-level default - the
        # MCP server is shared with n8n, and n8n's own quantity-ask workflow already
        # sends `include_summary=true` today. The CRM asks for the three-line pipeline
        # by name; a caller that only asks `include_summary` gets the summary shape it
        # got before this plan. Both `ORDER_TOOLS` members declare the param on their
        # ToolSpec (`catalog.py`: orders_list and orders_by_product_list), which is what
        # keeps the MCP from stripping it - pinned by
        # `test_growth_fix_opt_in_envelope_fields.py`.
        #
        # S2 (security review, 13 Sep 2026): NOT requested when `so_bucket_refused`
        # is set - the pipeline summary carries `so_outstanding_qty`, the SAME figure
        # the redirect below exists to withhold, and asking for it here would hand it
        # straight back on a plain quantity ask over the DO bucket the redirect
        # switched to. `include_summary` above stays: a bare DO quantity is not
        # gated.
        if not jsc.truthy(jsc.get(semantic_input, "so_bucket_refused")):
            out["include_pipeline"] = True

    # A1/A2 (chatbot-growth-r1), opt-in from THIS caller (fix, 7 Sep 2026): these two
    # used to be defaulted ON in `sorento_crm_mcp/server.py`'s
    # `TOOL_DEFAULT_QUERY_PARAMS` for every caller of the shared MCP server - n8n
    # included, which never asked for either and has no field-reveal filter of its
    # own. Moved here so only the CRM's own turn opts in, per the reason that turn
    # actually has:
    #   - `include_specs`: a product SPEC question ("SRTWC8517 spec", "wattage of
    #     X") - `check_product` intent or the `master_products` domain, the same
    #     pair A1's presenter branch reads.
    #   - `include_sellable`: only when the contact's OWN access grants
    #     `inventory.sellable` (Slice C's `contact_field_reveals`, read off `ctx.access`
    #     the same way A2's restricted-field drop reads it) - asking for a number the
    #     renderer would then have to hide is pointless, and every other contact's call
    #     stays the pre-A2 shape.
    if tool_name == "crm_master_products_list" and (
        jsc.get(semantic_input, "intent_hint") == "check_product"
        or jsc.get(semantic_input, "domain_hint") == "master_products"
    ):
        out["include_specs"] = True
    if tool_name == "crm_inventory_stock_balance_list":
        access = trig.get("access") if isinstance(trig.get("access"), dict) else {}
        attributes = access.get("attributes") if isinstance(access.get("attributes"), list) else []
        if "inventory.sellable" in attributes:
            out["include_sellable"] = True
        # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner
        # ruling 24 Sep 2026) for chatbot-stock-ask-v2 S3, D13/D20: the dealer's OWN
        # quantity, per product, as the `{product uuid: int}` map the route declares
        # and parses (`parse_requested_quantities`). Absent, never null, on a turn
        # that states no quantity at all - the tool's scalar `requested_qty` contract
        # (n8n, direct callers) is untouched, and an ask with no quantity still comes
        # back `needs_quantity`, which is what OPENS the task in the first place.
        #
        # MUST cross the MCP as a JSON STRING, not a native dict (review round 3, the
        # live pass): the route (`app/api/v1/inventory/stock.py`) declares
        # `requested_quantities: Optional[str]`, and the MCP's `_compile_tool` types
        # every query param off `_scalar_union` (no dict case) unless the tool is
        # listed in `TOOL_OBJECT_QUERY_PARAMS` - sent as a string here either way, so
        # the value round-trips identically whichever path compiles it.
        #
        # Review round 6, finding B: NOT key-sorted. The map is built in the order
        # the dealer named the products (`turn_runtime._spec_quantities`, over the
        # spec's own entities), and sorting the keys here would throw that order away
        # - the reply then notes the products back in an order the dealer never used.
        quantities = jsc.get(semantic_input, "requested_quantities")
        if isinstance(quantities, dict) and quantities:
            out["requested_quantities"] = json.dumps(
                dict(quantities), separators=(",", ":")
            )

    # group_by / top_n (A3, AC-909/AC-910): additive parser keys, uniform across
    # every list tool this plan touches. `top_n` aliases to `limit` for the
    # order tools (the tool has no `top_n` param of its own); A6's SPO tool
    # reads `top_n` directly (see `TOP_N_DIRECT_TOOLS`).
    group_by = jsc.get(semantic_input, "group_by")
    if tool_name in GROUP_BY_TOOLS and jsc.truthy(group_by):
        out["group_by"] = jsc.js_string(group_by)
    top_n = jsc.get(semantic_input, "top_n")
    if jsc.truthy(top_n):
        if tool_name in TOP_N_DIRECT_TOOLS:
            out["top_n"] = top_n
        elif tool_name in ORDER_TOOLS or tool_name in GROUP_BY_TOOLS:
            out["limit"] = top_n
    # PLAN-po-spo-warehouse-29sep S6: the sort axis, for the two PO/SPO tools only.
    sort_by = jsc.get(semantic_input, "sort_by")
    mapped = SORT_KEY_BY_TOOL.get(tool_name, {}).get(sort_by) if isinstance(sort_by, str) else None
    # Security review (PR #1373, finding 1): a sort on a RESTRICTED field orders the rows
    # by a value the contact may not see - the same side channel the restricted-field
    # drop below refuses for `group_by=supplier` (rows clustered by supplier with the
    # names blanked still say which lines share one). Refused HERE, before the call, so
    # the tool's own default order answers; the drop cannot re-order rows after the fact.
    sort_perm = RESTRICTED_SORT_KEYS.get(tool_name, {}).get(mapped) if mapped else None
    if sort_perm is not None:
        access = trig.get("access") if isinstance(trig.get("access"), dict) else {}
        attributes = access.get("attributes") if isinstance(access.get("attributes"), list) else []
        if sort_perm not in attributes:
            mapped = None
    if mapped:
        out["sort"] = mapped
        sort_dir = jsc.get(semantic_input, "sort_dir")
        out["dir"] = sort_dir if sort_dir in ("asc", "desc") else SORT_DEFAULT_DIR[sort_by]

    # A counted set (the resolver's `predicate` rode through the gate) lists PRODUCTS,
    # never a ROW count: `limit` is the tool's own row cap (a stock answer carries a row
    # per warehouse, a cert answer a row per file), so it is left alone and the list is
    # cut by slicing `product_ids` itself - to the count the customer named (`top_n`),
    # else to `answer.SET_LIST_MAX` (owner ruling, 26 Sep 2026: a set that fits one
    # message is listed in full; a longer one is answered with its count and a question,
    # `output_structurer` below). No paging: nothing is kept for a "more".
    if trig.get("predicate") is not None and isinstance(out.get("product_ids"), list):
        out["product_ids"] = out["product_ids"][: _set_list_size(semantic_input)]
        # Reviewer B3 on PR #833: the tool's own DEFAULT row cap (50) cut a listed set
        # short - a stock row per location, a cert row per file - so 40 basins came back
        # as about 8. The listed products need all their rows: ask for the tool's
        # maximum. The header still counts what actually rendered (`output_structurer`).
        if tool_name in SET_ROW_LIMIT:
            out["limit"] = SET_ROW_LIMIT[tool_name]

    # R29/AC-1354: a scheme-narrowed certificate leg's own certificate ids
    # ride the SAME predicate block, straight through under the SAME arg
    # name `TYPE_TO_PARAM["certificate"]` already maps to (`certificate_ids`)
    # - so `crm_master_product_attachments_list` narrows to those files
    # alone (a product certified under both PPS and WCM must not have its
    # WCM file rendered for a PPS question). Absent on a bare certificate
    # leg - `predicate.certificate_ids` itself is present only on the scheme
    # form, so nothing extra is sent and every certificate file still
    # renders, exactly as it does today.
    predicate = trig.get("predicate")
    if isinstance(predicate, dict) and predicate.get("certificate_ids"):
        out["certificate_ids"] = predicate["certificate_ids"]

    # PLAN-chatbot-customer-scope-29sep.md D4, the LAST step before `contact_id`: a
    # customer-scoped contact's `customer_ids` are its linked customers on every
    # customer-scoped tool, whatever the earlier steps built. Defence in depth behind
    # `engine._customer_scope_gate`, which never lets another customer reach this far.
    scope_ids = jsc.get(semantic_input, "scope_customer_ids") if isinstance(semantic_input, dict) else None
    if isinstance(scope_ids, list) and scope_ids and tool_name in CUSTOMER_SCOPED_TOOLS:
        requested = out.get("customer_ids")
        requested = requested if isinstance(requested, list) else ([requested] if requested else [])
        outside = [str(c) for c in requested if str(c) not in {str(i) for i in scope_ids}]
        if outside:
            raise ScopeViolation(
                f"{tool_name} asked for a customer outside the contact's scope", dropped=outside
            )
        out["customer_ids"] = requested or list(scope_ids)
        out.pop("customer_query", None)

    # COERCE, THEN TRIM, and the ORDER is the whole point. `contact_id` arrives as BOTH an
    # int and a SPACE-PADDED string in production, in adjacent executions: five spine call
    # sites write `{{ ... .json.id }} ` with a trailing space inside the template. A number
    # has no `.trim`, so `String()` must come first - `.trim().toString()` is a TypeError on
    # the int the answer path actually receives. `String(x ?? '')` handles int, string,
    # padded string, null and undefined without knowing which caller sent what, and lands
    # on `''` rather than `undefined`, which would DROP the key rather than send it blank.
    # N-4 (security review, 20 Sep 2026): a blank `contact_id` here is NOT a scope that
    # matches nothing - that fail-closed rule is `resolve_contact_company_scope`'s own
    # (`company_scope_resolver.py`), which the chatbot ENGINE already calls once, in
    # process, before this tool call, to stamp the session's company scope (see
    # `engine.py`'s own comment above `_contact_company_scope`). This `out["contact_id"]`
    # is a separate value, sent to the MCP tool call itself, and the ROUTER dependency
    # that reads it (`_resolve_api_key_scope`, `company_scope_resolver.py:233-237`)
    # returns `None` for a blank `contact_id`/`space_id` pair - `None` means ALL
    # companies (AC-F1, backward-compat), the opposite of "matches nothing".
    raw_contact = trig.get("contact_id")
    if raw_contact is None:
        raw_contact = jsc.get(semantic_input, "contact_id")
    out["contact_id"] = jsc.nullish_str(raw_contact).strip()
    out["space_id"] = space_id_or_default(space_id)
    return out


# --------------------------------------------------------------------------- #
# The MCP call (H52)
# --------------------------------------------------------------------------- #


def parse_mcp_content(raw: Any) -> Any:
    """The MCP client's `content[]` blocks, each parsed on its own.

    `MCPRuntimeClient.call_tool` JOINS every text block with a newline, which turns two
    JSON documents into one unparseable string. n8n's own `findPayload` walks the blocks
    and takes the FIRST that carries a render envelope, so the port has to see them
    separately too - joining is what would lose a tool that answers in two chunks.
    """
    if not isinstance(raw, str):
        return raw
    parsed = _safe_json(raw)
    if parsed is not None:
        return parsed
    blocks = [_safe_json(chunk) for chunk in raw.split("\n") if chunk.strip()]
    for block in blocks:
        if isinstance(block, dict) and _find_payload(block) is not None:
            return block
    for block in blocks:
        if isinstance(block, dict):
            return block
    return raw


class ToolNotAllowed(RuntimeError):
    """H58: this tool is not one the chatbot may call, so it is not called at all.

    Its own type rather than a bare `RuntimeError` because the caller answers it
    differently from an MCP failure: a refusal is not "the read did not work", it is "the
    read was never allowed", and `run_fetch` records it as the `tool_not_allowed` outcome
    so the reason is on the trace an operator reads.
    """


class ScopeViolation(ToolNotAllowed):
    """PLAN-chatbot-customer-scope-29sep.md D4: a customer-scoped contact's fetch asked for
    a customer outside its links. Never a tool call; `run_fetch` answers it with the
    refusal line, not the generic "not allowed" text. `dropped` names the ids outside the
    links, for the trace (CHATBOT-SELFREF-SCOPE R4)."""

    def __init__(self, message: str, *, dropped: list[str] | None = None) -> None:
        super().__init__(message)
        self.dropped: list[str] = list(dropped or [])


#: The MCP tools that return per-customer data: every catalogue tool whose `query_params`
#: carry `customer_ids` (pinned by `tests/chatbot/test_customer_scope_fetch.py`).
CUSTOMER_SCOPED_TOOLS: frozenset[str] = frozenset(
    {
        "crm_order_management_orders_list",
        "crm_order_management_orders_by_product_list",
        "crm_outstanding_report",
        "crm_sales_report",
        "crm_top_selling_report",
        "crm_order_analytics",
        "crm_master_customers_list",
    }
)


# The MCP tools this chatbot may call. An ALLOW-list, so a tool that is not named here is
# refused - a new write tool added to the MCP catalogue is out by default rather than in
# until somebody remembers to deny it.
#
# **Why a literal and not a walk of `sorento_crm_mcp/catalog.py`.** The deployed backend
# image does not contain that package: `sorento_crm/docker-compose.yml` builds the backend
# with `context: ./sorento_crm_backend`, the Dockerfile's `COPY . .` therefore copies only
# the backend, `mcp` is not in `requirements.txt` and no volume mounts it. A catalogue read
# on the turn path would raise `ModuleNotFoundError` in every container, the path fallback
# in `mcp_tool_capability_service._load_catalog_specs` would look for `/sorento_crm_mcp/`
# and fail too, and `run_fetch`'s broad `except` would turn EVERY live business turn into
# "MCP tool X failed". A frozen set costs nothing and cannot fail.
#
# The catalogue is still the source of truth for WHICH names belong here, and the two are
# pinned together by `tests/chatbot/test_tool_pool_is_read_only.py`, which imports the
# catalogue (available in CI and in a checkout, never in the container) and asserts this
# set EQUALS "method GET, or the spec's own `read_only` flag". Drift in either direction
# fails CI, which is where the catalogue is readable, instead of at 3am in production.
#
# The six the audit found, deliberately absent: `crm_complaint_close`, `crm_order_cancel`,
# `crm_purchase_request_approve`, `crm_purchase_request_reject`,
# `crm_it_support_ticket_create`, `crm_ideation_turn`. They stay in the MCP catalogue and
# in the in-app assistant's embedded pool - it retrieves them ON PURPOSE and gates each
# behind a user confirmation and a permission check. The chatbot has no user to confirm
# with, which is the whole difference.
#
# **Where the names live (D9, AC-931, AC-1594).** Still a frozen set, for every reason
# above - it is simply no longer a THIRD list. Each name is either claimed by exactly one
# domain (`turn.policy.default_policy().domains[*].tools`, the frozen seed
# `chatbot_domains` is migrated from) or named in `contracts.UNDOMAINED_CHATBOT_TOOLS` as
# claimed by nobody on purpose, and this set is their union. This list is the ALLOW-list
# for every seam a tool name can reach the MCP client through (the probes and the
# cross-domain rung name their tool directly); the one tool a turn is ANSWERED from is
# `default_policy().domain(domain).tools[0]`, read by `select_tool`. A STATIC allow-list
# on purpose, not a live per-turn `Policy` read: this is a security boundary
# (`ensure_read_only` below), and an owner editing `chatbot_domains.tools` must not
# silently widen what the chatbot may call without a deliberate migration/review.
# `tests/chatbot/test_tool_pool_is_read_only.py` still pins the whole union against the MCP
# catalogue's read-only set, unchanged.
CHATBOT_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    tool for row in default_policy().domains for tool in row.tools
) | frozenset(UNDOMAINED_CHATBOT_TOOLS)


def ensure_read_only(name: Any) -> None:
    """Refuse a tool the chatbot may not call. Raises `ToolNotAllowed`, returns nothing.

    ONE rule, called at the TWO seams a tool name can reach the MCP client through: this
    module's `call_tool` (the fetch step and the tier probe) and `services._mcp_call` (the
    answer and miss-suggest probes, which call the bundle directly). Two call sites of one
    function rather than two rules: whichever path a name arrives on, the same set decides.
    """
    if jsc.js_string(name) not in CHATBOT_READ_ONLY_TOOLS:
        raise ToolNotAllowed(
            f"MCP tool {name} is not allowed: the chatbot may only call read tools"
        )


def call_tool(name: str, args: dict[str, Any], *, mcp: Any) -> Any:
    """One MCP tool call, passed straight through (D10) - if the tool only READS.

    No re-shaping in either direction: the arguments are what `entity_ids_transformer`
    built and the result is what the tool returned, so `output_structurer` still sees the
    presenter shape it was written against. The endpoint is whatever `mcp` was constructed
    with, and `services.py` builds it from `settings.ai_assistant_mcp_url` - this module
    names no host, no scheme and no port.

    **The allow-list check is HERE, at the egress, and it is not defensive coding (H58).**
    The tool used to be chosen by cosine similarity over a pool that contains write tools,
    so the only thing standing between a customer's phrasing and `crm_order_cancel` was
    that no phrasing had scored it first. `select_tool` now reads the name off the
    domain row, so a write tool cannot be PICKED at all; this is what stops one being
    CALLED however else it was named - the tier probe, and any tool name that arrived on a
    payload rather than from the domain table.
    """
    ensure_read_only(name)
    return mcp.call_tool(name, args)


# --------------------------------------------------------------------------- #
# output-structurer
# --------------------------------------------------------------------------- #

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Identity is an ALLOW-LIST, not a label test: short, stable, and shared with the renderers.
# `company_name` is identity because it says WHICH company's row this is - without it a
# two-company answer would read as a one-company answer.
IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "product_code",
        "product_name",
        "shipment_number",
        "shipping_container_number",
        "batch_number",
        "remaining_incoming_quantity",
        "warehouse_allocations",
        "unallocated_quantity",
        "warehouse",
        "system_location",
        "quantity_on_hand",
        "company_name",
    }
)

# ETA and its delay are kept always, asked for or not: the ETA is the public answer to
# "where is my container", and the cross-domain renderer sorts incoming rows on it. The
# delay is IMPLIED by the ETA, never NAMED, so `req_attrs` does not carry it: a denied
# delay is stripped upstream (`app/services/field_access.py`) and gets no "can't share"
# note, a blank delay gets no "not recorded yet" note. Only an explicit `eta_delay_date`
# in `requested_attributes` turns those notes on.
ALWAYS_KEPT_KEYS: frozenset[str] = frozenset({"estimated_arrival_date", "eta_delay_date"})

# Chronological order of an inbound container's clearance checkpoints. Mirrors the
# admin-editable `statuses` rows (entity_type "inbound_shipment", by sort_order) as they
# stand on prod; hardcoded because output_structurer is a pure function with no session.
CLEARANCE_CHECKPOINT_ORDER: tuple[str, ...] = (
    "loading_date", "etc_date", "etd_date", "estimated_arrival_date", "eta_delay_date",
    "inspection_date", "approval_date", "gatepass_date", "warehouse_arrival_date",
    "informed_collection_date", "collection_date",
)


def _safe_json(value: Any) -> Any:
    try:
        return json.loads(value)
    except Exception:  # noqa: BLE001 - `try { JSON.parse } catch { return null }`
        return None


def _as_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_json(value)
    return value if isinstance(value, dict) else None


def _find_payload(j: Any) -> dict[str, Any] | None:
    """The render envelope, wherever the MCP client happened to wrap it."""
    if not jsc.truthy(j):
        return None
    if isinstance(jsc.get(j, "items"), list) or jsc.truthy(jsc.get(j, "portal_url")) or jsc.truthy(
        jsc.get(j, "token")
    ):
        return j if isinstance(j, dict) else None
    for key in ("result", "response", "toolResult", "output", "json", "text"):
        o = _as_obj(jsc.get(j, key))
        if o is not None and (
            isinstance(o.get("items"), list) or jsc.truthy(o.get("portal_url")) or jsc.truthy(o.get("token"))
        ):
            return o
    if isinstance(jsc.get(j, "content"), list):
        for c in jsc.get(j, "content"):
            o = _as_obj(jsc.get(c, "text") if jsc.truthy(c) else None)
            if o is not None and (
                isinstance(o.get("items"), list)
                or jsc.truthy(o.get("portal_url"))
                or jsc.truthy(o.get("token"))
            ):
                return o
    return None


def _rendered_answers(e: dict[str, Any]) -> list[Any]:
    """The grouped rows in the order the numbered message printed them.

    Same walk and same skip conditions as the group render below, so "row 2 on screen" and
    `answers[1]` cannot disagree. Falls back to the flat `items` when a group carries none,
    because an empty `answers` would break the positional pick outright rather than shift
    it.
    """
    out: list[Any] = []
    for grp in e.get("groups") or []:
        if not isinstance(grp, dict) or not isinstance(grp.get("items"), list):
            continue
        out.extend(grp["items"])
    return out or (e.get("items") or [])


def group_axis(ctx: Any) -> str:
    """The `group_by` axis this turn asked for, off the trigger's `semantic_input`.

    Read twice - once to filter the grouped rows, once to decide whether the AXIS itself
    is a restricted value - so it is one function rather than two inline digs through a
    value that arrives as a dict on a live turn and as a JSON STRING on some captured n8n
    triggers (`output_structurer` already has to handle both).
    """
    si: Any = (ctx or {}).get("semantic_input") if isinstance(ctx, dict) else None
    if isinstance(si, str):
        si = _safe_json(si)
    if not isinstance(si, dict):
        return ""
    return jsc.js_string(si.get("group_by") or "").strip()


#: What an answer says when the tool found nothing. Named so a caller that decides NOT to
#: call a tool (`turn_runtime.make_tool_runner`'s unfiltered-fetch guard) says it in the
#: same words, rather than printing a header with nothing under it.
NO_RESULT_INTRO = "No matching results found."


def _extract_envelope(j: Any) -> dict[str, Any]:
    empty = {
        "items": [],
        "attachments": [],
        "action_links": [],
        "intro": NO_RESULT_INTRO,
        "has_result": False,
    }
    p = _find_payload(j)
    if p is None:
        return empty
    if isinstance(p.get("items"), list):
        return p  # render envelope
    if jsc.truthy(p.get("portal_url")):  # raw portal-link tool
        return {
            "items": [],
            "attachments": [],
            "action_links": [
                {"label": "Portal Link", "url": p["portal_url"], "type": "portal_link"}
            ],
            "intro": "Here is your portal link.",
            "has_result": True,
        }
    return empty


def _fmt_ts(iso: Any) -> str | None:
    """`new Date(iso)` then local `getDate()`/`getMonth()`/... - components, verbatim.

    Every captured `last_updated_at` is a NAIVE ISO string, which `new Date` reads as local
    time and `getDate()` then reads straight back, so the rendered components equal the
    input's (measured on all 20 `output-structurer` captures). Implemented as a component
    read rather than through a timezone, which is also what makes it deterministic.
    """
    if not jsc.truthy(iso):
        return None
    text = jsc.js_string(iso)
    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2}))?", text
    )
    if not match:
        return None
    year, month, day = match.group(1), match.group(2), match.group(3)
    date = f"{day}/{month}/{year}"
    hour, minute, second = match.group(4), match.group(5), match.group(6)
    if hour is None:
        return date
    if (int(hour), int(minute), int(second)) == (0, 0, 0):
        return date  # midnight -> date only
    return f"{date} {hour}:{minute}:{second}"


# The JS renders an empty value and an object joiner with an EM DASH, and the access note
# with one too. Written as escapes, not as the character: the repo forbids an em dash in
# anything WE write, and these are neither ours nor prose - they are three literals from a
# node body that ships, and changing them would change what a customer is sent. Nothing in
# the 20 graded captures reaches any of the three, so this is faithfulness, not evidence.
_EM_DASH = "\u2014"


def _fmt_value(v: Any) -> str:
    if v is None or v == "":
        return _EM_DASH
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str):
        # DO-ASK-SIMPLIFY tester pass 2: a value's own '*' (a product code "*REPLACE")
        # would pair with the next label's bold marker and leave a stray '*' in WhatsApp.
        # It prints as the look-alike U+2217, which WhatsApp does not read as formatting.
        return ((_fmt_ts(v) or v) if _ISO_RE.match(v) else v).replace("*", "\u2217")
    if isinstance(v, (int, float)):
        return jsc.js_string(v)
    if isinstance(v, list):
        return ", ".join(_fmt_value(x) for x in v)
    if isinstance(v, dict):
        parts = [
            (_fmt_value(x) if isinstance(x, (dict, list)) else jsc.js_string(x))
            for x in v.values()
            if x is not None and x != ""
        ]
        return f" {_EM_DASH} ".join(parts) if parts else _EM_DASH
    return jsc.js_string(v)


def _has_key(f: Any) -> bool:
    """`Object.prototype.hasOwnProperty.call(f, 'key')`.

    PRESENCE, never `=== null`: the CRM OMITS `key` where a presenter has no source key,
    and testing for null instead would treat an unkeyed field as keyed.
    """
    return isinstance(f, dict) and "key" in f


def _humanise(key: str) -> str:
    text = re.sub(r"_date\Z", "", key).replace("_", " ")
    return text[:1].upper() + text[1:] if text else text


# Types whose rows carry ALIAS variants of one name. A debtor master holds "NAME",
# "NAME [A/C I]", "NAME [A/C II]" as separate accounts of the same customer, and naming all
# three reads as three customers. Product codes are NOT in here and must never be: a variant
# code that extends another code ("SRT100-BL" under "SRT100") is a different product.
_ALIAS_COLLAPSE_TYPES: frozenset[str] = frozenset({"customer"})


def _axis_labelled_subject(entities: Any) -> str:
    """What was searched, said the way the customer said it: "customer X, product Y".

    The line this feeds used to flatten every `entities[].code` into one comma list, so a
    multi-alias customer arrived as an INTERNAL debtor code plus one bullet per alias row -
    "no order records for 300-H070, HANLIM TRADING SDN BHD [A/C I], HANLIM TRADING SDN BHD,
    RPACC" - un-labelled, so it read as four separate things.

    Three STRUCTURAL rules, no text patterns over the customer's words (D11):

    * **A customer is named by the resolver's own `display_name`, never by
      `canonical_code`.** Live, "hanlim" resolves to TWO `customers` rows - one reached by
      `customer_code` (`300-H070`) and one by the denormalised `debtor_name` - on two
      different uuids, so the per-uuid de-dupe keeps both and the longest-code tie-break
      below can never fire between them. The account code is an internal identifier the
      customer has never seen. `gate.run_gate` carries `display_name` for exactly this,
      and a record without one falls back to its code, which is the whole of today's
      behaviour for every other type.
    * An alias row collapses into its base, for the types that HAVE aliases only - which
      is what prints ONE line for "HANLIM TRADING SDN BHD" and "HANLIM TRADING SDN BHD
      [A/C II]". It works on the LABEL, so it could not match a code against a name.
    * The label comes from `_AXES`, the miss lane's own axis vocabulary, so the two lanes
      cannot drift. A type no axis claims keeps today's bare, unlabelled code.
    """
    # `_AXES` lives in `answer.py`, which imports THIS module, so the import is
    # function-level to keep that cycle open. One vocabulary, one place: a second copy here
    # would drift the moment an axis is added.
    from app.services.chatbot.lanes.business.answer import _AXES

    by_record: dict[str, dict[str, str]] = {}
    unkeyed: list[dict[str, str]] = []
    for x in jsc.array(entities):
        code = jsc.nullish_str(jsc.get(x, "code")).strip()
        uuid = jsc.nullish_str(jsc.get(x, "uuid")).strip()
        # The resolver's human label wins where it gave one (customers today); everything
        # else keeps the canonical code, which for a product IS what the customer typed.
        label = jsc.nullish_str(jsc.get(x, "display_name")).strip() or code
        # Whatever is about to be PRINTED is what must not be a uuid: for types with no
        # code the resolver fills `code` with the record's OWN uuid, and four promotion
        # uuids once printed under "no promotions records for ...".
        if not label or label == uuid or _UUID_RE.match(label):
            continue
        row = {"type": jsc.nullish_str(jsc.get(x, "entity_type")).strip().lower(), "code": label}
        if not uuid:
            unkeyed.append(row)
            continue
        held = by_record.get(uuid)
        if held is None or len(label) > len(held["code"]):
            by_record[uuid] = row
    rows = [*by_record.values(), *unkeyed]

    kept: list[dict[str, str]] = []
    for row in rows:
        lowered = row["code"].lower()
        if row["type"] in _ALIAS_COLLAPSE_TYPES and any(
            other is not row
            and other["type"] == row["type"]
            and other["code"].lower() != lowered
            and lowered.startswith(other["code"].lower())
            for other in rows
        ):
            continue  # an alias of a base this axis already names
        if any(k["code"].lower() == lowered for k in kept):
            continue
        kept.append(row)

    parts: list[str] = []
    claimed: set[str] = set()
    for axis in _AXES:
        types = {jsc.nullish_str(t).strip().lower() for t in (axis.get("types") or [])}
        claimed |= types
        codes = [r["code"] for r in kept if r["type"] in types]
        if codes:
            # #1262 slice 3 (F5), AC-S3-3: above 5, name the COUNT, never every one
            # of them - a catalogue-wide sweep (5,857 products) joined every "match"
            # into a single miss line that spanned three WhatsApp messages. At or
            # under the cap the customer's own subject still prints in full.
            if len(codes) > 5:
                parts.append(f"the {len(codes)} {jsc.js_string(axis['label']).lower()}s searched")
                continue
            # "A", "A and B", "A, B and C" - the last join is a word so the axis boundary
            # stays readable next to the comma that separates axes.
            listed = (
                codes[0]
                if len(codes) == 1
                else f"{', '.join(codes[:-1])} and {codes[-1]}"
            )
            parts.append(f"{jsc.js_string(axis['label']).lower()} {listed}")
    # A type no axis claims keeps today's bare code, so a promotion or an attachment type is
    # still named rather than dropped by a vocabulary that does not know it.
    leftovers = [r["code"] for r in kept if r["type"] not in claimed]
    if leftovers:
        parts.append(", ".join(leftovers))
    return ", ".join(parts)


def _date_window_phrase(semantic_input: Any) -> str:
    """", dates 01/09/2026 to 30/09/2026" - the window that was searched, or "" for none.

    A miss inside a date window is a different fact from a miss over all time, and the
    customer cannot tell which they got without being told.
    """
    si = semantic_input if isinstance(semantic_input, dict) else {}
    start = _fmt_ts(si.get("date_filter_start")) if jsc.truthy(si.get("date_filter_start")) else None
    end = _fmt_ts(si.get("date_filter_end")) if jsc.truthy(si.get("date_filter_end")) else None
    if start and end:
        return f", dates {start}" if start == end else f", dates {start} to {end}"
    if start:
        return f", dates from {start}"
    if end:
        return f", dates up to {end}"
    return ""


def _names_a_shipment(ctx: dict[str, Any]) -> bool:
    """A bare container ask ("incoming TIIU6323920") is a timeline ask, not an ETA-only one.

    Evidence: live turn f07632b6-d56d-4036-944c-8200462caac3 - "incoming TIIU6323920" parses
    to `requested_attributes: []` with entity `{"raw": "TIIU6323920", "hint":
    "inbound_shipment", "confident": true}`. A question that names a specific container and
    asks for no particular attribute is a timeline ask: every recorded checkpoint comes out,
    chronologically, exactly as the `__all__` sentinel does today.

    Checked against the RESOLVED entity list first - `entity_type` is the field
    `gate.run_gate` stamps and `entity_ids_transformer`'s `TYPE_TO_PARAM` keys on - and only
    falls back to the parser's own `hint` when the resolved list carries no type at all (the
    gate ran empty, so there is nothing else to check).
    """
    entities = ctx.get("entities") if isinstance(ctx.get("entities"), list) else []
    typed = [e for e in entities if jsc.truthy(e) and jsc.truthy(jsc.get(e, "entity_type"))]
    if typed:
        return any(
            jsc.js_string(jsc.get(e, "entity_type")).strip() == "inbound_shipment" for e in typed
        )
    semantic_input = ctx.get("semantic_input")
    if isinstance(semantic_input, str):
        semantic_input = _safe_json(semantic_input)
    parsed_entities = (
        jsc.get(semantic_input, "entities") if isinstance(semantic_input, dict) else None
    )
    return any(
        jsc.truthy(e) and jsc.js_string(jsc.get(e, "hint")).strip() == "inbound_shipment"
        for e in jsc.array(parsed_entities)
    )


#: D12 (owner ruling, 8 Sep 2026, turn 8f4a8526 "SRTJC802A-1500 product details"): the
#: BASE fields a product answer ALWAYS carries, whatever `requested_attributes` says -
#: item 8's `_PRODUCT_IDENTITY_LABELS` (Product Code, Company only) meant an asked word
#: that named no base field of its own (or a wrongly-emitted `["price"]` for a message
#: that named no property at all) dropped List Price, Dimensions, Product Name and
#: Description from the reply. Discontinued flag and attachments are not `fields` rows
#: at all and were never affected by this.
_PRODUCT_IDENTITY_LABELS: frozenset[str] = frozenset(
    {"Product Code", "Product Name", "Description", "List Price", "Dimensions", "Company"}
)

_SPEC_KEY_PREFIX = "spec:"


def _normalize_spec_word(v: Any) -> str:
    """Lowercase, trim, collapse `_`/`-` to a space - the same normalization on
    both sides of a spec-vocabulary match (a registry key and a customer's own
    word disagree on separators, never on letters)."""
    return re.sub(r"[_\-]+", " ", jsc.nullish_str(v).strip().lower()).strip()


#: D12: since a base field is now ALWAYS on the page (`_PRODUCT_IDENTITY_LABELS`), an
#: asked word naming one is no longer a thing to PICK (item 8's `_BASE_FIELD_BY_ASK`
#: is retired) - it only means "do not add a miss line for this word", because the
#: field it names is already there. Single-token entry matches the whole ask EXACTLY;
#: only the one multi-token entry ("list price") may be contained in a longer ask -
#: same discipline as item 8's own matching, kept for the same reason ("seat size"
#: must not read as a hit on "size").
#:
#: Was a module-level frozenset here (AC-1594, S6): the words are `chatbot_entity_kinds`.
#: `base_property_words`' KEYS now, read through `Policy` (AC-1535) - which is a superset
#: of the old literal (also "discontinued" and "brand", the S0 migration's own seed), a
#: deliberate widening: a "discontinued" ask no longer needs its own miss line either.
def _names_a_base_property(norm: str) -> bool:
    words = default_policy().kind("product")
    for w in (words.base_property_words if words is not None else {}):
        if norm == w or (" " in w and w in norm):
            return True
    return False


_MISS_CODES_CAP = 5


def _tokens(norm: str) -> set[str]:
    return set(norm.split())


def _project_product_specs(
    e: dict[str, Any], req_attrs: list[Any], hidden_spec_keys: list[Any] | None = None
) -> None:
    """A1 (AC-901/AC-902): the product spec projection, product envelopes ONLY.

    A SEPARATE branch from the clearance/incoming projection above - deliberately
    not folded into it. That one is gated on `field_vocabulary` truthiness and
    would, for a no-attribute ask, strip every keyed field down to the identity
    allow-list; the compact "Specs:" line below IS the no-attribute case, so
    reusing that gate would delete the very thing this function exists to add.

    No requested_attributes: every item keeps its base fields, plus ONE synthetic
    "Specs:" field naming EVERY populated spec key, in registry order (D1, 12 Sep
    2026 owner finding: "and N more" left no way to see the rest, so there is no
    cap and no truncation - the field lists all of them).

    With requested_attributes (item 8 / D12, 8 Sep 2026), every item ALWAYS keeps its
    base fields (`_PRODUCT_IDENTITY_LABELS`: Product Code, Product Name, Description,
    List Price, Dimensions, Company) - a base field is never dropped by an ask, and an
    asked word is never "matched" to one; it is on the page regardless. Only the SPEC
    keys are on demand, per asked word:
      1. SPEC KEYS BY TOKEN CONTAINMENT - exact match on the normalised key or label
         first, else every key whose key OR label tokens contain every asked token
         ("material" reaches both `material` and `seat_material` via "Seat cover
         material"; "seat cover material" reaches `seat_material` only), rendered in
         registry order (the order the presenter emitted them).
      2. ONE MISS LINE PER ASKED WORD, not per item, for a word that matched NO spec key
         on that item - EXCEPT a word naming a base property (`_names_a_base_property`:
         price / list price / harga / cost / dimension(s) / size / ukuran / saiz /
         description / name), which produces no miss line at all: the field it names is
         already on the page, so "not recorded" would contradict what the reply just
         showed. A genuine spec miss goes into ONE `spec_misses` entry, rendered once
         AFTER the items by `output_structurer` - "*<label>:* not recorded for A, B, C
         (+N more)", label from the registry when the word matches a known key/label,
         else the asked word; codes capped at `_MISS_CODES_CAP`. (Not `summary_items`:
         that slot is the quantity-summary mode and suppresses the item rows, fetch.py's
         items loop.)
    An item with no spec hit for any asked word keeps its base fields only - byte
    identity with the no-attribute path's own field set.

    `hidden_spec_keys` (PLAN-spec-visibility-policy.md "Chatbot seam", AC-15/AC-16):
    every `spec:<key>` field whose key is hidden is dropped BEFORE either branch
    above runs - before the "Specs:" summary is built and before asked-word
    matching - and hidden keys are removed from the vocabulary used for matching
    too, so a hidden key can never register a hit. An asked word naming a hidden
    key (exact key/label, or every asked token contained in it - the same rule as
    an ordinary hit) produces ONE `spec_hidden:<key>` miss, "<label>: not
    available", instead of the ordinary "not recorded" line - and instead of, not
    in addition to it.
    """
    hidden = frozenset(str(k) for k in (hidden_spec_keys or []))
    vocab_raw = e.get("spec_vocabulary")
    vocab: dict[str, str] = vocab_raw if isinstance(vocab_raw, dict) else {}
    # (spec_key, label, norm_key, norm_label) per registry row, registry order kept.
    # Hidden keys are excluded here - "removed from the vocabulary used for
    # matching" - and kept in a SEPARATE list below, only for naming the "not
    # available" line's label.
    vocab_rows: list[tuple[str, str, str, str]] = [
        (str(key), str(label), _normalize_spec_word(key), _normalize_spec_word(label))
        for key, label in vocab.items()
        if str(key) not in hidden
    ]
    hidden_vocab_rows: list[tuple[str, str, str, str]] = [
        (str(key), str(label), _normalize_spec_word(key), _normalize_spec_word(label))
        for key, label in vocab.items()
        if str(key) in hidden
    ]

    def vocab_label_for(norm: str) -> str | None:
        """The registry label a miss line names: exact match first, else the one row
        whose key or label contains every asked token (several -> the first)."""
        for _k, label, nk, nl in vocab_rows:
            if norm in (nk, nl):
                return label
        toks = _tokens(norm)
        for _k, label, nk, nl in vocab_rows:
            if toks and (toks <= _tokens(nk) or toks <= _tokens(nl)):
                return label
        return None

    def hidden_ref_for(norm: str) -> tuple[str, str] | None:
        """`(registry key, label)` when the asked word names a HIDDEN key - same
        matching rule as `vocab_label_for`, over the hidden-only rows."""
        for k, label, nk, nl in hidden_vocab_rows:
            if norm in (nk, nl):
                return (k, label)
        toks = _tokens(norm)
        for k, label, nk, nl in hidden_vocab_rows:
            if toks and (toks <= _tokens(nk) or toks <= _tokens(nl)):
                return (k, label)
        return None

    # The normalised word AND the word the customer actually typed, PAIRED (review, nit
    # 10). Two parallel lists went out of step the moment `req_attrs` carried a blank or a
    # word that normalised away - `req_attrs[asked_norms.index(norm)]` then named a
    # DIFFERENT attribute in the "no X recorded" sentence, and it only shows on the one
    # phrasing that has a blank in it.
    asked: list[tuple[str, str]] = []
    seen_norms: set[str] = set()
    for raw in req_attrs:
        text = jsc.nullish_str(raw).strip()
        if not text:
            continue
        norm = _normalize_spec_word(text)
        if norm and norm not in seen_norms:
            asked.append((norm, text))
            seen_norms.add(norm)

    # Asked words that name a HIDDEN key, resolved once (not per item): a
    # `spec_hidden:<key>` miss instead of the ordinary flow, never both.
    hidden_asks: dict[str, tuple[str, str]] = {}
    for norm, _asked_word in asked:
        ref = hidden_ref_for(norm)
        if ref is not None:
            hidden_asks[norm] = ref

    missed_codes: dict[str, list[str]] = {
        norm: [] for norm, _ in asked if norm not in hidden_asks
    }

    # AC-17 / code review S2: the keys ACTUALLY removed from this envelope, for
    # the turn trace's `spec_visibility.dropped` - not vocabulary membership,
    # which says nothing about whether the product this turn showed even
    # carried the key.
    dropped_keys: set[str] = set()

    for it in e.get("items") or []:
        if not jsc.truthy(it) or not isinstance(jsc.get(it, "fields"), list):
            continue
        fields: list[dict[str, Any]] = it["fields"]
        code = None
        for f in fields:
            if isinstance(f, dict) and f.get("label") == "Product Code":
                code = f.get("value")
                break
        base = [
            f
            for f in fields
            if not (isinstance(f, dict) and jsc.js_string(f.get("key") or "").startswith(_SPEC_KEY_PREFIX))
        ]
        # Hidden keys are dropped HERE, before either branch below runs - the
        # "Specs:" summary and the asked-word matching both read `spec_fields`.
        raw_spec_fields = [
            f
            for f in fields
            if isinstance(f, dict) and jsc.js_string(f.get("key") or "").startswith(_SPEC_KEY_PREFIX)
        ]
        spec_fields = []
        for f in raw_spec_fields:
            raw_key = jsc.js_string(f.get("key") or "")[len(_SPEC_KEY_PREFIX):]
            if raw_key in hidden:
                dropped_keys.add(raw_key)
            else:
                # R7 (round 4 on PR #833, "why all the values are snake case?"): a stored
                # value never reaches a reply as stored. The MCP sends the CRM's plain
                # words once it carries `display_value`; this reads any slug that still
                # arrives the same way (`display_spec_value`), so an older MCP cannot leak
                # "cold_only" either.
                if isinstance(f.get("value"), str):
                    f["value"] = display_spec_value(f["value"])
                spec_fields.append(f)

        if not asked:
            # No attribute asked: base fields untouched, plus the compact summary -
            # EVERY populated spec key, in registry order, no cap (D1).
            # The write is UNCONDITIONAL. `base` is this item's fields minus every
            # `spec:` field, so it is what the item must end up with whether or not a
            # summary follows; guarding the write on `spec_fields` left `it["fields"]`
            # as the ORIGINAL list the moment the visible set came back empty - and
            # with every key hidden (a Contact override hiding all of them, measured
            # 14 Sep 2026 on turn 8c432988) that original list still carried every
            # hidden `spec:` field, which `output_structurer` then rendered one line
            # per key. A product with no spec fields at all keeps exactly the fields
            # it has today: `base` IS `fields` there.
            fields_out = list(base)
            if spec_fields:
                summary = ", ".join(f"{f.get('label')}: {f.get('value')}" for f in spec_fields)
                fields_out.append({"key": "specs_summary", "label": "Specs", "value": summary})
            it["fields"] = fields_out
            continue

        # An attribute was asked: identity fields + the base fields + the spec keys it names.
        # D12: base fields are ALWAYS kept - no ask can drop or add one.
        kept_base = [
            f for f in base if isinstance(f, dict) and f.get("label") in _PRODUCT_IDENTITY_LABELS
        ]
        spec_norms: list[tuple[dict[str, Any], str, str]] = []  # (field, norm_key, norm_label)
        for f in spec_fields:
            raw_key = jsc.js_string(f.get("key") or "")[len(_SPEC_KEY_PREFIX):]
            spec_norms.append((f, _normalize_spec_word(raw_key), _normalize_spec_word(f.get("label"))))

        matched: list[dict[str, Any]] = []
        seen_field_ids: set[int] = {id(f) for f in kept_base}
        for norm, _asked_word in asked:
            # 1. spec keys: an exact key/label match AND every key whose key or label
            #    tokens contain every asked token - ALL of them, in registry order.
            #    Runs REGARDLESS of whether `norm` also names a hidden key (code
            #    review S1): "thickness" with `thickness` hidden and
            #    `board_thickness` visible must still render the visible field -
            #    the hidden field is already gone from `spec_fields` above, so it
            #    can never itself be matched here, but a DIFFERENT visible key
            #    whose tokens contain the same asked word (like `board_thickness`
            #    containing "thickness") is a real, separate hit.
            toks = _tokens(norm)
            contained = [
                f
                for f, nk, nl in spec_norms
                if norm in (nk, nl) or (toks and (toks <= _tokens(nk) or toks <= _tokens(nl)))
            ]
            hit = bool(contained)
            for f in contained:
                if id(f) not in seen_field_ids:
                    matched.append(f)
                    seen_field_ids.add(id(f))
            if norm in hidden_asks:
                # Handled once, after the items loop, as a `spec_hidden:` miss
                # instead of - never in addition to - the ordinary "not
                # recorded" one, whatever the visible-key matching above found.
                continue
            # 2. a spec miss - UNLESS the word names a base property, which is already
            #    on the page (kept_base, above) and needs no "not recorded" line.
            if not hit and not _names_a_base_property(norm):
                missed_codes[norm].append(jsc.js_string(code))
        it["fields"] = kept_base + matched

    # 3. one miss line per asked word, rendered ONCE after the items - ordinary
    # "not recorded" misses first, then one `spec_hidden:` "not available" line
    # per asked word naming a hidden key (AC-16).
    misses: list[dict[str, Any]] = []
    for norm, asked_word in asked:
        if norm in hidden_asks:
            continue
        codes = missed_codes.get(norm) or []
        if not codes:
            continue
        label = vocab_label_for(norm) or asked_word
        listed = ", ".join(codes[:_MISS_CODES_CAP])
        extra = len(codes) - _MISS_CODES_CAP
        value = f"not recorded for {listed}" + (f" (+{extra} more)" if extra > 0 else "")
        misses.append({"key": f"spec_miss:{norm}", "label": label, "value": value})
    for norm, _asked_word in asked:
        ref = hidden_asks.get(norm)
        if ref is None:
            continue
        key, label = ref
        misses.append({"key": f"spec_hidden:{key}", "label": label, "value": "not available"})
    if misses:
        e["spec_misses"] = misses

    # AC-17 / code review S2: what this function actually removed, for the turn
    # trace (`run_fetch`'s `spec_visibility` event) - always set when this
    # function ran, even `[]`, so the trace reads "ran, nothing to drop" rather
    # than reaching for a vocabulary check of its own.
    e["spec_hidden_dropped"] = sorted(dropped_keys)


# --------------------------------------------------------------------------- #
# crm_outstanding_report - never the generic envelope below (S4 point 5)
# --------------------------------------------------------------------------- #


#: Both shapes the presenter writes: the numbered line when TWO scopes are offered, and
#: R9's single sentence when only one is (`Reply 1 for the sales order list.`). One
#: pattern per scope, so a reader here cannot drift from what the customer can see.
_SO_LIST_OFFER_RE = re.compile(
    r"(?m)^\d+\.\s*Sales order list\s*$|^Reply \d+ for the sales order list\.$"
)
_DO_LIST_OFFER_RE = re.compile(
    r"(?m)^\d+\.\s*Delivery order list\s*$|^Reply \d+ for the delivery order list\.$"
)


def _outstanding_offer_from_text(text: str) -> list[dict[str, Any]]:
    """The same offer, read off ALREADY-RENDERED text (production: `present_response`
    rendered this server-side, so there is no `so`/`do` block left to inspect here) -
    the option lines the S1 presenter itself writes are the only source of truth
    left, and reusing them can never disagree with what the customer is looking at."""
    rows: list[dict[str, Any]] = []
    if _SO_LIST_OFFER_RE.search(text):
        rows.append({"idx": len(rows) + 1, "label": "Sales order list", "value": "so"})
    if _DO_LIST_OFFER_RE.search(text):
        rows.append({"idx": len(rows) + 1, "label": "Delivery order list", "value": "do"})
    if len(rows) == 2:
        # R14 (owner ruling, 13 Sep 2026): with both lists on offer, the presenter adds
        # `3. Both lists` - so the stored roster carries it too, or the position the
        # customer can see would resolve against nothing.
        rows.append({"idx": 3, "label": "Both lists", "value": "both"})
    return rows


def outstanding_product_codes(entities: Any, semantic_input: Any) -> list[str]:
    """WHICH products this report is about, in one place (AC-1119, reviewer N5 +
    console run 4 finding 5).

    `run_fetch` matches the gate's products against the codes the customer TYPED and
    stamps the winner as `semantic_input.outstanding_product_code`; that one wins, alone.
    Only when no candidate equalled a typed code (a prefix or spec-search hit, where the
    family member is the only answer there is) do the product entities themselves stand.

    A LIST, since hand pass 3: "all" over a ten-variant product roster picks ten codes,
    and the old rule took the FIRST entity and reported on one of them under a header
    that named it (turn 0a6f0379, 16 Sep 2026). One entity gives a list of one and
    behaves exactly as before.

    Every caller that names the product goes through here - the tool arguments, the
    detail offer's stored filters and the scope question's stored filters - so the
    question, the answer and the header can never disagree about which codes were asked
    about, which is exactly what console run 4 read on the scope-question arm.
    """
    si = semantic_input if isinstance(semantic_input, dict) else {}
    carried = [jsc.js_string(c) for c in jsc.array(si.get("outstanding_product_codes")) if jsc.truthy(c)]
    if carried:
        return carried
    typed = si.get("outstanding_product_code")
    if jsc.truthy(typed):
        return [jsc.js_string(typed)]
    codes: list[str] = []
    for e in jsc.array(entities):
        if not isinstance(e, dict) or jsc.js_string(e.get("entity_type")) != "product":
            continue
        # `gate.py` renames the resolver's `canonical_code` to `code` when it builds
        # `compatible_entities`; a caller that hands entities straight in (this module's
        # own tests) still spells it `canonical_code`.
        code = e.get("code") or e.get("canonical_code")
        if jsc.truthy(code) and jsc.js_string(code) not in codes:
            codes.append(jsc.js_string(code))
    return codes


#: The shortest a `crm_sales_report` `product_code` may be - the route 422s
#: `product_code_too_short` under it (AC-1627, S19).
_SALES_REPORT_MIN_PREFIX = 3


def sales_report_product_code(entities: Any, semantic_input: Any) -> str:
    """WHICH product the SALES report is about, as ONE string.

    `outstanding_product_codes` is the shared rule for which codes are in play (AC-1119);
    this route's own parameter is different in kind, because `crm_sales_report`'s
    `product_code` is a PREFIX that covers a family (S19), not an exact code, and the
    route declares no `product_codes` list at all.

    So a carry of SEVERAL codes - "all" over a product roster, `outstanding_carried_
    product_codes` - travels as the longest prefix they share, which covers exactly them
    (and any sibling sitting between them) rather than reporting on the first alone under
    a header naming it (the outstanding report's own turn 0a6f0379, 16 Sep 2026). One
    code, which is every ordinary ask, is returned verbatim and this is a no-op.
    """
    codes = outstanding_product_codes(entities, semantic_input)
    if not codes:
        return ""
    if len(codes) == 1:
        return codes[0]
    shared = codes[0]
    for code in codes[1:]:
        while shared and not code.upper().startswith(shared.upper()):
            shared = shared[:-1]
    return shared if len(shared) >= _SALES_REPORT_MIN_PREFIX else codes[0]


#: The offer BLOCK the presenter appended, in either form: R9's single sentence, or the
#: numbered list and every option line under it, to the end of the reply.
_OFFER_BLOCK_RE = re.compile(
    r"(?ms)^(?:Reply \d+ for the .+? list\.|Reply with a number for detail:\n(?:\d+\..*\n?)+)\s*\Z"
)


def _outstanding_offer_block(text: str) -> str:
    """The exact bytes of the offer the customer was shown, or "".

    Kept VERBATIM rather than re-rendered (AC-1102, 13 Sep 2026): the offer's wording is
    the MCP presenter's, the backend container cannot import that module, and a second
    renderer here drifted from it the first time the presenter's wording changed - a
    single-scope report offered `Reply 1 for the sales order list.` and its own re-print
    answered with the numbered form, two wordings for one offer in one conversation.
    Storing what was rendered is the only copy that cannot disagree with itself.
    """
    match = _OFFER_BLOCK_RE.search(text or "")
    return match.group(0).strip() if match else ""


def _outstanding_filters_from_ctx(ctx: dict[str, Any]) -> dict[str, Any]:
    """The SAME `outstanding_filters` shape `business/__init__.py::_outstanding_filters_from`
    builds for the scope-question ask - the detail offer carries the identical filter
    set forward so a later "1"/"2" re-runs the tool with them, unchanged (D10)."""
    semantic_input = ctx.get("semantic_input")
    if isinstance(semantic_input, str):
        semantic_input = _safe_json(semantic_input)
    semantic_input = semantic_input if isinstance(semantic_input, dict) else {}
    product_codes = outstanding_product_codes(ctx.get("entities"), semantic_input)
    customer_ids: list[Any] = []
    for e in jsc.array(ctx.get("entities")):
        if not isinstance(e, dict):
            continue
        if e.get("entity_type") == "customer":
            uid = e.get("uuid")
            # #1262 slice 2 (F1c): never a kind-pick's printed label or an
            # unresolved code, only a real uuid.
            if is_uuid(uid) and uid not in customer_ids:
                customer_ids.append(uid)
    if not customer_ids:
        # R13: a CUSTOMER-subject answering turn resolved no entity this turn - the ids
        # rode in on the carried filter set, and they have to ride back out on it too, or
        # the offer this hit arms loses the only subject it has.
        customer_ids = [
            uid
            for uid in jsc.array(semantic_input.get("outstanding_carried_customer_ids"))
            if is_uuid(uid)
        ]
    return {
        "product_code": product_codes[0] if product_codes else None,
        # Only when there are SEVERAL: one code keeps the single key every reader
        # already speaks, so an ordinary ask's stored filters are unchanged.
        **({"product_codes": product_codes} if len(product_codes) > 1 else {}),
        "date_filter_start": semantic_input.get("date_filter_start"),
        "date_filter_end": semantic_input.get("date_filter_end"),
        "customer_ids": customer_ids,
        "warehouse_codes": semantic_input.get("outstanding_warehouse_codes") or [],
        # AC-1132/AC-1138 (review round): the location is part of the filter set, and
        # the TOKEN travels with the codes - the answering turn has to print the same
        # header ("IB (BRW-IB, MWH-IB)") as the turn that asked.
        "location_token": semantic_input.get("outstanding_location_token"),
        # AC-1157 (R15): WHICH scope this report was run for. The offer exists only
        # because a report ran, so the scope is settled by the time this filter set is
        # built - and a REFINEMENT of that offer ("this month only") has to re-run the
        # same scope rather than re-ask a question the customer already answered.
        "scope": semantic_input.get("outstanding_scope"),
        # #1262 slice 9 (F1a) follow-up: the SAME already-resolved brand ids
        # `_resolve_report_product_and_location` wrote onto this same `semantic_input`
        # earlier in this fetch - the report just ran with them, so the detail offer
        # (and any refinement of it) carries them forward the same way it does
        # `customer_ids` above.
        "brand_ids": [
            b for b in jsc.array(semantic_input.get("outstanding_brand_ids")) if is_uuid(b)
        ],
    }


def _outstanding_report_output(result: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """S4 point 5 (AC-1114b/AC-1135/AC-1138/AC-1141): `crm_outstanding_report` never
    goes through the generic envelope below - the report's shape (two named blocks,
    each with its own By location / By customer subgroup) has no row list to build
    items from.

    `result` is the MINIMAL envelope `sorento_crm_mcp.presenters.present_response`
    returns for this tool: `response` (the already-rendered reply, used VERBATIM) and
    `has_result`. There is no second, local rendering of the report here - a lane that
    re-derived the header from a raw payload could disagree with the text the customer
    is reading, and the backend container cannot import `sorento_crm_mcp` to share the
    presenter (`CHATBOT_READ_ONLY_TOOLS`'s own docstring above explains why).

    `has_result` has to come off the wire: the rendered header prints on a TOTAL MISS
    too ("Product: ... / No open sales order."), so reading the text would call every
    miss an answer and the existing escalate offer (AC-1107) would never fire - which
    is exactly what the 13 Sep console check saw.
    """
    envelope = result if isinstance(result, dict) else {}
    if "response" in envelope:
        text = jsc.js_string(envelope.get("response") or "")
        has_result = envelope.get("has_result") is True
    else:
        # The render never happened (an MCP that returned the raw body, or a failure
        # fallback - #1262 slice 1, F2: a raised tool call's own error text, now that
        # `call_tool` raises on `isError` instead of returning it as a string). A bare
        # string here is never a rendered report, so it is never treated as a result -
        # `has_result: True` for one is what let a tool-error string answer the miss
        # lane and print "Error executing tool ..." verbatim.
        text = result if isinstance(result, str) else jsc.js_string(result)
        has_result = False
    offer = _outstanding_offer_from_text(text)

    outstanding_ask = (
        {
            "kind": "outstanding_detail",
            "last_result_set": offer,
            # NOT a filter: the offer's own text, carried in the same bag because it
            # travels to exactly the same place and is needed by exactly the same turn -
            # the one that re-prints an offer the customer has not answered yet.
            "filters": {**_outstanding_filters_from_ctx(ctx), "offer_text": _outstanding_offer_block(text)},
        }
        if offer
        else None
    )
    return {
        # Review round (26 Sep 2026), SF1: `has_result: False` alone did not stop
        # `turn/compose.py` printing this same bare string VERBATIM whenever it rode
        # in on `lane_text` (only the literal substring "Error executing tool" was
        # ever stripped, AC-S1-3's own narrow fix) - a bare, non-envelope string is
        # NEVER a rendered report, so it must never reach `response` either, the
        # same "never treated as a result" rule `has_result` already states above.
        "response": text if "response" in envelope else "",
        "response_intro": None,
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": has_result,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        "outstanding_ask": outstanding_ask,
        # AC-1139: the report carries its OWN Product / Customer / Location / Order date
        # lines, so `tail/compile_state.py` must skip the generic search-scope header it
        # prints above a delivery-order answer. Read there off this marker.
        "outstanding_report": True,
    }


def _sales_report_filters_from_ctx(ctx: dict[str, Any]) -> dict[str, Any]:
    """The `outstanding_filters` shape the sales-report lane arms its own detail
    offer with (PLAN-chatbot-sales-report.md S4 wiring point 7). `tool` names WHICH
    report this stored filter set belongs to - carried for a reader of the stored
    session state, but the re-run itself is decided by the PENDING KIND, not this
    key: `head/output_exchange.py::_apply_outstanding_pending` stamps `order_status:
    "sales_report"` off `kind == "sales_report_detail"` directly, the same way an
    `outstanding_detail` offer's own scope decides its re-run."""
    semantic_input = ctx.get("semantic_input")
    if isinstance(semantic_input, str):
        semantic_input = _safe_json(semantic_input)
    semantic_input = semantic_input if isinstance(semantic_input, dict) else {}
    product_code = sales_report_product_code(ctx.get("entities"), semantic_input)
    customer_ids: list[Any] = []
    for e in jsc.array(ctx.get("entities")):
        if not isinstance(e, dict):
            continue
        if e.get("entity_type") == "customer":
            uid = e.get("uuid")
            if uid and uid not in customer_ids:
                customer_ids.append(uid)
    if not customer_ids:
        # R13-equivalent: a CUSTOMER-subject answering turn resolved no entity this
        # turn - the ids rode in on the carried filter set, and they have to ride
        # back out on it too, or the offer this hit arms loses its only subject.
        customer_ids = [
            uid for uid in jsc.array(semantic_input.get("outstanding_carried_customer_ids")) if uid
        ]
    return {
        "tool": "crm_sales_report",
        "product_code": product_code,
        "date_filter_start": semantic_input.get("date_filter_start"),
        "date_filter_end": semantic_input.get("date_filter_end"),
        "customer_ids": customer_ids,
        "warehouse_codes": semantic_input.get("outstanding_warehouse_codes") or [],
        "location_token": semantic_input.get("outstanding_location_token"),
        "channel": semantic_input.get("sales_channel"),
    }


def _sales_report_output(result: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """S4 wiring point 7 (AC-1654/AC-1655/AC-1658): `crm_sales_report` never goes
    through the generic envelope below - mirrors `_outstanding_report_output` for
    the sibling tool, the SAME reason: the report's shape has no row list to build
    items from.

    AC-SR-28: the drill-down offer is the presenter's own `options` roster (`{idx, label,
    value, aliases}`, numbered by the presenter, the one writer of the numbering), never a
    regex over the reply text. Those rows become the `sales_report_detail` open question.
    """
    envelope = result if isinstance(result, dict) else {}
    if "response" in envelope:
        text = jsc.js_string(envelope.get("response") or "")
        has_result = envelope.get("has_result") is True
        # CHATBOT-SELFREF-SCOPE (owner hand test, 30 Sep 2026): a rendered report with
        # no month on the asker's OWN accounts ("my sales this year", or any ask of a
        # customer-scoped contact) is a final answer - "No sales found." - never the
        # miss lane's escalate offer: there is nothing to escalate about one's own
        # account. A miss on a NAMED product or customer keeps AC-1658's offer, since a
        # wrong code is worth a person's look.
        semantic_input = ctx.get("semantic_input")
        if isinstance(semantic_input, str):
            semantic_input = _safe_json(semantic_input)
        own_accounts = isinstance(semantic_input, dict) and (
            semantic_input.get("self_reference") is True or bool(semantic_input.get("scope_customer_ids"))
        )
        if not has_result and own_accounts and text.strip().endswith(SALES_REPORT_MISS_MESSAGE):
            has_result = True
    else:
        # Mirrors `_outstanding_report_output`'s own fix (#1262 slice 1, F2): a bare
        # string here is never a rendered report, so it is never treated as a result.
        text = result if isinstance(result, str) else jsc.js_string(result)
        has_result = False
    offer = [
        {
            "idx": row.get("idx"),
            "label": row.get("label"),
            "value": row.get("value"),
            "aliases": [a for a in jsc.array(row.get("aliases")) if isinstance(a, str)],
        }
        for row in jsc.array(envelope.get("options"))
        if isinstance(row, dict) and row.get("value") in SALES_REPORT_GROUP_BYS and row.get("idx")
    ]

    outstanding_ask = (
        {
            "kind": "sales_report_detail",
            "last_result_set": offer,
            "filters": {
                **_sales_report_filters_from_ctx(ctx),
                "offer_text": _sales_report_offer_block(text),
            },
        }
        if offer
        else None
    )
    return {
        # Mirrors `_outstanding_report_output`'s own SF1 fix (review round, 26 Sep
        # 2026): a bare, non-envelope string is never a rendered report, so it must
        # never reach `response` either - only "Error executing tool" ever got
        # stripped downstream, so any other unrendered string rode through verbatim.
        "response": text if "response" in envelope else "",
        "response_intro": None,
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": has_result,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        "outstanding_ask": outstanding_ask,
        # AC-1659: this report carries its OWN Customer / Product / Channel /
        # Location / Delivery date header, so `tail/compile_state.py` must skip the
        # generic search-scope header (the same marker `crm_outstanding_report`
        # already reuses this for, S4 point 9).
        "outstanding_report": True,
    }


#: The heading the presenter prints above the drill-down options (`_sales_report`).
_SALES_REPORT_DRILL_HEADING = "*Drill down:*"


def _sales_report_offer_block(text: str) -> str:
    """The drill-down block exactly as the customer was shown it (heading plus the numbered
    options), kept verbatim for the out-of-range re-ask (AC-1102's rule)."""
    at = (text or "").rfind(_SALES_REPORT_DRILL_HEADING)
    return text[at:].strip() if at >= 0 else ""


#: The miss line for an unrendered low stock payload (N6). The presenter carries the same
#: wording for its own error envelope; this copy covers only the "render never happened"
#: fallback, where the presenter's text never reached this function.
_LOW_STOCK_ERROR_TEXT = "Could not run the low stock report right now."
#: The presenter's own error line for `crm_sales_analysis`, for a body it never rendered.
_SALES_ANALYSIS_ERROR_TEXT = "Could not run the sales report right now."
#: The presenter's own miss line for `crm_sales_report` (`presenters.SALES_REPORT_MISS_MESSAGE`,
#: AC-1607): the one way a rendered report says it found no month.
SALES_REPORT_MISS_MESSAGE = "No sales found."


def _low_stock_report_output(result: Any, *, fallback: str = _LOW_STOCK_ERROR_TEXT) -> dict[str, Any]:
    """PLAN-low-stock-report S6 (AC-66): `crm_low_stock_report`'s own envelope.

    `_outstanding_report_output`'s shape, with one difference that is the whole point:
    `attachments` is carried through from the presenter's envelope instead of being an
    empty list. `engine._attachments_src` turns a non-empty list into a `send_attachments`
    action, and that action IS the in-turn delivery of the workbook (AC-43).

    The reply text is the presenter's, verbatim - one writer, one wording, for the same
    reason the outstanding report's is: the backend container cannot import
    `sorento_crm_mcp`, so a second rendering here could disagree with the text the customer
    is reading and nothing would catch it.

    `has_result` comes off the wire and is True on every presenter branch - ready, pending,
    busy and error are all terminal answers the bot gives verbatim. The presenter renders an
    error as the "could not run" line (reviewer S1/N6, refined by the console: a False here
    routes into the inventory domain's GENERIC miss, the wrong wording for a failed run), so
    the lane must NOT force the turn onto the miss path; it carries the presenter's text and
    `has_result` through unchanged.
    """
    envelope = result if isinstance(result, dict) else {}
    if "response" in envelope:
        text = jsc.js_string(envelope.get("response") or "")
        has_result = envelope.get("has_result") is True
        attachments = envelope.get("attachments")
    else:
        # The render never happened (an MCP that returned a RAW route body - `{status:
        # error|busy|...}` - or a failure fallback). This tool has a side effect and no
        # safe default text, so state the error line verbatim as a terminal answer rather
        # than stringing a raw status dict into the reply or dropping into the generic
        # inventory miss.
        text = fallback
        has_result = True
        attachments = None
    return {
        "response": text,
        "response_intro": None,
        "answers": [],
        "attachments": attachments if isinstance(attachments, list) else [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": has_result,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        # The report's own terminal answer on every branch: the miss handler never
        # answers over it (`answer_bridge._miss_triggers`, owner hand test 3 Oct 2026).
        "low_stock_report": True,
    }


#: `chatbot_domains.forms.tools[0]` (`policy_rows.py`) - the one tool this domain calls.
_FORMS_LIST_TOOL = "crm_forms_management_forms_list"


def _forms_browse_ask(e: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Item 1 (17 Sep 2026): a forms BROWSE that found several forms arms a `form_pick`
    roster beside the numbered list it already prints, so "1" can answer it - the same
    `outstanding_ask` shape `_outstanding_report_output` hands `envelope_of` (read there
    as `lane_ask`), built off the MCP presenter's own `id` (`sorento_crm_mcp.presenters.
    _forms`, `_FORMS_LIST_KEEP_BROWSE`).

    Never when this call already NAMED a form: `ctx["entities"]` carrying a `form` entity
    means the pick already resolved (the customer's own "1", or the resolver matched a
    name) and this is the narrowed lookup that answer runs, not a fresh browse to arm
    another roster over.
    """
    if any(
        isinstance(ent, dict) and ent.get("entity_type") == "form"
        for ent in jsc.array(ctx.get("entities"))
    ):
        return None
    items = [it for it in (e.get("items") or []) if isinstance(it, dict) and jsc.truthy(it.get("id"))]
    if len(items) < 2:
        # One form, or none: nothing to choose between (`narrow.decide`'s own
        # `_choices() <= 1` rule for a roster policy, read here for the same reason).
        return None
    return {
        "kind": "form_pick",
        "last_result_set": [
            {"idx": i + 1, "label": it.get("title"), "value": it["id"]}
            for i, it in enumerate(items)
        ],
        "filters": {},
    }


def _top_selling_output(result: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """PLAN-chatbot-top-x-hot-selling-24sep.md "Lane wiring (S4)" point 9. The
    presenter's minimal envelope (`presenters._top_selling_envelope`) is the reply
    verbatim, beside the two sibling reports: the ranking carries its own header, so
    the generic search-scope header is skipped (`outstanding_report`, AC-1958).

    * A ranking hit arms its printed rows as a sticky `top_selling_pick` roster (owner,
      PR #1258 05:32Z: the list behaves like the customer and product pickers), through
      the same `outstanding_ask` hand-off the detail offers use; `compose._lane_question`
      builds it with `turn/pending.top_selling_pick`.
    * The how-many reply, the detail reply and a refusal arm nothing (`result_set` is
      empty on all three), so an open list stays open across a detail.
    * `has_result: false` is the miss (AC-1957): the not-found path, escalate offer and
      all."""
    envelope = result if isinstance(result, dict) else {}
    semantic_input = ctx.get("semantic_input") if isinstance(ctx.get("semantic_input"), dict) else {}
    if "response" in envelope:
        text = jsc.js_string(envelope.get("response") or "")
        # Owner retest (27 Sep 2026): a ranking with no sales is an ANSWER ("No sales
        # found." under its own header), never "Could not find order" and never the
        # escalation offer with its routing picker.
        has_result = envelope.get("has_result") is True or (
            envelope.get("result_type") == "top_selling" and bool(text.strip())
        )
    else:
        text = result if isinstance(result, str) else jsc.js_string(result)
        has_result = bool(text.strip())
    notes = [jsc.js_string(n) for n in jsc.array(semantic_input.get("top_selling_notes")) if jsc.truthy(n)]
    if notes and text.strip():
        text = "\n".join(notes) + "\n\n" + text
    rows = [r for r in jsc.array(envelope.get("result_set")) if isinstance(r, dict)]
    slot = semantic_input.get("top_selling") if isinstance(semantic_input.get("top_selling"), dict) else {}
    outstanding_ask = (
        {
            "kind": "top_selling_pick",
            "last_result_set": rows,
            "filters": {"tool": "crm_top_selling_report", **{k: v for k, v in slot.items() if k != "detail_code"}},
        }
        if rows and envelope.get("result_type") == "top_selling"
        else None
    )
    return {
        "response": text,
        "response_intro": None,
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": has_result,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        "outstanding_ask": outstanding_ask,
        "outstanding_report": True,
        # The how-many reply ASKS (reviewer B2, PR #1273): recorded on the slot so the
        # next bare number is its count, while a list or a single row asks nothing and
        # the next ask naming the ranking starts fresh.
        "top_selling_asked": "how_many" if envelope.get("result_type") == "top_selling_how_many" else None,
        # The item codes the ranking listed, in rank order: an outstanding ask after it
        # asks about exactly these (fix lane round 8, `apply._hop_to_report`). An empty
        # list on a ranking that listed none (how many, no sales, a category ranking),
        # None on a detail or a refusal, which leave the listed ranking standing.
        "top_selling_codes": (
            [jsc.js_string(r.get("code")) for r in rows if r.get("entity_type") == "product" and jsc.truthy(r.get("code"))]
            if envelope.get("result_type") in ("top_selling", "top_selling_how_many")
            else None
        ),
        "top_selling_drop": list(semantic_input.get("top_selling_drop") or []) or None,
    }


def _dmy(value: Any) -> str:
    text = jsc.js_string(value or "")[:10]
    parts = text.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else text


def output_structurer(result: Any, ctx: dict[str, Any] | None) -> dict[str, Any]:
    """The MCP render envelope becomes a WhatsApp message. Deterministic, no LLM (H7).

    `result` is what the MCP client returned (n8n's `$('MCP Client1').first().json`) and
    `ctx` is the trigger (`semantic_input`, `entities`), so the two by-name reads become
    parameters and nothing else about the body changes.
    """
    ctx = ctx if isinstance(ctx, dict) else {}
    if jsc.js_string(ctx.get("tool") or "") == "crm_outstanding_report":
        return _outstanding_report_output(result, ctx)
    if jsc.js_string(ctx.get("tool") or "") == "crm_sales_report":
        return _sales_report_output(result, ctx)
    if jsc.js_string(ctx.get("tool") or "") == "crm_low_stock_report":
        return _low_stock_report_output(result)
    if jsc.js_string(ctx.get("tool") or "") == "crm_top_selling_report":
        return _top_selling_output(result, ctx)
    if jsc.js_string(ctx.get("tool") or "") == "crm_sales_analysis":
        # The same envelope: the presenter's text and, when there is one, the Excel. It
        # states its own scope (company, channel, basis, period), so the order domain's
        # generic Customer/Product/Dates header must not print above it.
        return {
            **_low_stock_report_output(result, fallback=_SALES_ANALYSIS_ERROR_TEXT),
            "own_header": True,
        }
    e = _extract_envelope(result)
    # Read once, for both the restricted-field drop below and the spec-visibility
    # drop (PLAN-spec-visibility-policy.md "Chatbot seam") - one contact, one
    # `ctx.access`, two consumers.
    access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}

    # S2 (security review, 13 Sep 2026): `run_fetch` redirected a customer-only
    # `so_outstanding` ask (no product, so the outstanding-report override above
    # never runs) to `outstanding` (the DO bucket) because the contact lacks
    # `sales_orders.outstanding`. Prefixed onto the reply here, not there - this
    # generic envelope is the only place this tool's own text gets composed.
    ctx_semantic_input = ctx.get("semantic_input") if isinstance(ctx.get("semantic_input"), dict) else {}
    so_bucket_refusal = (
        SO_NOT_ENABLED_MESSAGE if jsc.truthy(ctx_semantic_input.get("so_bucket_refused")) else None
    )

    # -- restricted-field drop (A2/A5/A6, general rule) ---------------------- #
    # A presenter marks a field or summary item RESTRICTED by putting its key in
    # the envelope's `restricted_fields` (field key -> permission key, e.g.
    # "inventory.sellable", "purchase_orders.supplier" - `sorento_crm_mcp.
    # presenters._Builder.restrict`). Dropped here unless the contact's access
    # grants that permission. `ctx["access"]["attributes"]` is a list; today
    # `head/access.py::check_access` always returns it as None (Slice C wires it
    # from `contact_field_reveals`), so None is read as the EMPTY grant set and
    # every restricted field is hidden by construction - which is exactly what
    # keeps a stock/PO/SPO answer byte-identical to before this rule existed
    # (AC-903). The MCP itself stays unfiltered; this is the ONE place a
    # restricted field is ever dropped for the chatbot.
    restricted = e.get("restricted_fields")
    if isinstance(restricted, dict) and restricted:
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, list) else set()

        def _keep_field(f: Any) -> bool:
            """Keep, drop, or SWAP. A restricted field that carries `granted_value` is
            a plain field with a granted suffix (D1, the compact stock block's
            "*Total:* 51 (O/S: 36)"): under the grant its value becomes the granted one,
            without it the plain value stays and the suffix never reaches the reader.
            A restricted field with no `granted_value` is dropped whole, as before."""
            if not _has_key(f):
                return True
            perm = restricted.get(jsc.js_string(f["key"]))
            if perm is None:
                return True
            has_granted_value = isinstance(f, dict) and "granted_value" in f
            if perm in granted:
                if has_granted_value:
                    f["value"] = f.pop("granted_value")
                return True
            if has_granted_value:
                f.pop("granted_value", None)
                return True
            return False

        def _filter_rows(rows: Any) -> None:
            for row in rows or []:
                if jsc.truthy(row) and isinstance(jsc.get(row, "fields"), list):
                    row["fields"] = [f for f in row["fields"] if _keep_field(f)]

        _filter_rows(e.get("items"))
        _filter_rows(e.get("summary_items"))
        # A3/A5's GROUPED shape carries the same rows a second time, and the first
        # cut of this rule filtered `items` only - so `group_by=product` on a PO
        # question rendered every supplier a dealer must never see, and
        # `group_by=supplier` printed the restricted value as the SECTION HEADING,
        # where no field filter could ever reach it. Both are closed here: every
        # group's rows go through the same `_keep_field`, and the AXIS itself is
        # refused below when the grant is not held.
        for grp in e.get("groups") or []:
            if not (jsc.truthy(grp) and isinstance(grp, dict)):
                continue
            _filter_rows(grp.get("items"))
            _filter_rows(grp.get("summary_items"))

        # THE HEADING IS NOT A FIELD. A group's `label` is the axis value itself, so a
        # restricted axis leaks by being grouped ON, whatever the rows carry. The answer
        # is to drop the grouping and answer FLAT rather than to redact the headings:
        # "Supplier A / Supplier B" with the names blanked still tells the dealer how many
        # suppliers there are and which rows share one, and an ungrouped list is the
        # honest answer to a question we may not break down.
        # The axis NAME and the restricted FIELD KEY are the same word by construction:
        # a tool may only group on an axis it renders as a field, and `group_by=supplier`
        # groups on the `supplier` field the presenter marked restricted. So the lookup
        # is the same `restricted` map, with no second table to keep in step.
        axis = group_axis(ctx)
        axis_perm = restricted.get(axis) if axis else None
        if axis_perm is not None and axis_perm not in granted:
            e["groups"] = []
            e["group_by_dropped"] = axis

    # -- requested-attribute projection ------------------------------------- #
    # The CRM dumps every clearance field the caller may see, by design: it prevents the
    # LEAK, this prevents the DUMP. KEY-based, never label-based: a label table here was
    # drift by construction and silently broke a sort when a label was renamed.
    semantic_input: Any = ctx.get("semantic_input")
    if isinstance(semantic_input, str):
        semantic_input = _safe_json(semantic_input)
    semantic_input = semantic_input if isinstance(semantic_input, dict) else {}
    req_attrs: list[Any] = (
        semantic_input.get("requested_attributes")
        if isinstance(semantic_input.get("requested_attributes"), list)
        else []
    )

    # A1 (AC-901/AC-902): the product spec projection, gated on the envelope's OWN
    # result_type - never on `field_vocabulary`/`spec_vocabulary` truthiness, so a
    # product with zero derived specs (no `spec_vocabulary` at all) still gets the
    # plain today's-four-fields answer rather than being skipped by accident.
    if jsc.js_string(e.get("result_type") or "") == "products":
        hidden_raw = access_ctx.get("hidden_spec_keys")
        hidden_spec_keys = hidden_raw if isinstance(hidden_raw, list) else []
        _project_product_specs(e, req_attrs, hidden_spec_keys)

    # H46: CONTAINS the sentinel, not IS it. `contracts.is_timeline` is the one declaration
    # (S6a put it there for exactly this consumer); re-deriving it here is what let a
    # mutation test "prove" the `not timeline` guard below was redundant.
    timeline = is_timeline(req_attrs) or (not req_attrs and _names_a_shipment(ctx))
    keep_keys = set(ALWAYS_KEPT_KEYS)
    for k in req_attrs:
        kk = jsc.nullish_str(k).strip()
        if kk:
            keep_keys.add(kk)

    # A checkpoint ask ("when is gatepass?") implies every EARLIER checkpoint in the
    # container's journey - a customer asking about gatepass wants the whole story up to
    # it, not one isolated date. `req_attrs` itself is untouched (echoed back, and drives
    # the "not recorded yet" notes below): only `keep_keys` grows.
    expanded = False
    if not timeline:
        checkpoint_idx = [
            CLEARANCE_CHECKPOINT_ORDER.index(kk)
            for k in req_attrs
            if (kk := jsc.nullish_str(k).strip()) in CLEARANCE_CHECKPOINT_ORDER
        ]
        if checkpoint_idx:
            keep_keys.update(CLEARANCE_CHECKPOINT_ORDER[: max(checkpoint_idx) + 1])
            expanded = True

    # SCOPE GUARD: projection touches the CLEARANCE-gated incoming envelope ONLY. Gate on
    # what the envelope IS, not on whether keys happen to be present - resource attachments
    # are keyed too, and gating on "anything keyed" would drop both fields of a document
    # answer and render it with nothing at all.
    clearance_envelope = jsc.js_string(e.get("result_type") or "") == "incoming_stock" or bool(
        jsc.truthy(e.get("field_vocabulary"))
    )
    any_keyed = clearance_envelope and any(
        any(_has_key(f) for f in (jsc.get(it, "fields") or []))
        for it in (e.get("items") or [])
        if jsc.truthy(it)
    )
    for it in (e.get("items") or []) if any_keyed else []:
        if not jsc.truthy(it) or not isinstance(jsc.get(it, "fields"), list):
            continue
        it["fields"] = [
            f
            for f in it["fields"]
            if (not _has_key(f))
            or jsc.js_string(f["key"]) in IDENTITY_KEYS
            or timeline
            or jsc.js_string(f["key"]) in keep_keys
        ]

    # -- chronological order -------------------------------------------------- #
    # Containers arrive on a timeline and the CRM returns them in no stable order. Rows
    # with NO usable ETA sort LAST, never first: a missing date must not masquerade as
    # "arriving soonest". Ties keep their original order.
    if any_keyed:

        def _eta_of(it: Any) -> str:
            f = jsc.find(
                jsc.get(it, "fields") or [],
                lambda x: _has_key(x) and jsc.js_string(x["key"]) == "estimated_arrival_date",
            )
            v = jsc.nullish_str(jsc.get(f, "value")) if jsc.truthy(f) else ""
            return v if _DATE_RE.match(v) else ""

        items = e.get("items") or []
        if any(_eta_of(it) for it in items):
            e["items"] = [
                it
                for _, _, it in sorted(
                    ((_eta_of(it), i, it) for i, it in enumerate(items)),
                    key=lambda triple: (triple[0] == "", triple[0], triple[1]),
                )
            ]

    # -- timeline field order: dates chronological, sorted IN PLACE ----------- #
    # A timeline should READ as a timeline. The CRM's own order is NARRATIVE, not
    # value-ordered. Only the SEQUENCE within the date block is this node's business:
    # LAYOUT belongs to the CRM, and an earlier version that re-emitted `[...facts,
    # ...dates]` dragged the ETA below the quantity and undid a merged CRM change.
    # A field counts as a date by its VALUE, never by its key name. An expanded checkpoint
    # ask is a partial timeline and reads the same way.
    if timeline or expanded:

        def _date_of(f: Any) -> str | None:
            v = jsc.get(f, "value") if jsc.truthy(f) else None
            return v[:10] if isinstance(v, str) and _DATE_RE.match(v) else None

        for it in e.get("items") or []:
            if not jsc.truthy(it) or not isinstance(jsc.get(it, "fields"), list):
                continue
            slots = [i for i, f in enumerate(it["fields"]) if _date_of(f)]
            if len(slots) < 2:
                continue  # nothing to reorder
            ordered_fields = sorted(
                (it["fields"][i] for i in slots), key=lambda f: _date_of(f) or ""
            )
            for n, slot in enumerate(slots):
                it["fields"][slot] = ordered_fields[n]

    # -- denied vs not-yet-reached ------------------------------------------- #
    # "There is no gatepass date yet" is a LIE when the truth is "you may not see it".
    denied_map: dict[str, Any] = {}
    for d in ((e.get("field_access") or {}).get("denied") or []) if isinstance(
        e.get("field_access"), dict
    ) else []:
        key = jsc.nullish_str(jsc.get(d, "field")).strip()
        if key:
            denied_map[key] = d

    # Label source, in order: what the CRM called it on a row that DOES carry the key, then
    # the CRM's own `field_vocabulary`, then the denial entry's label, then the key
    # humanised. No local vocabulary table - that is the drift this rebuild removed.
    label_by_key: dict[str, Any] = {}
    for it in e.get("items") or []:
        for f in (jsc.get(it, "fields") or []) if jsc.truthy(it) else []:
            if _has_key(f) and jsc.truthy(f.get("label")) and jsc.js_string(f["key"]) not in label_by_key:
                label_by_key[jsc.js_string(f["key"])] = f["label"]
    raw_vocab = e.get("field_vocabulary")
    vocab: dict[str, Any] = raw_vocab if isinstance(raw_vocab, dict) else {}

    def _label_for(k: str, d: Any) -> str:
        return jsc.js_string(
            label_by_key.get(k)
            or vocab.get(k)
            or (jsc.get(d, "label") or jsc.get(d, "field_label") if jsc.truthy(d) else None)
            or _humanise(k)
        )

    # PER-ROW absence, GLOBAL denial: different kinds of fact, different rendering. Absence
    # is per-ROW data (one container had the date, three did not); denial is per-CONTACT
    # permission, identical on every row, so it stays ONE line.
    if any_keyed and not timeline:
        for it in e.get("items") or []:
            if not jsc.truthy(it) or not isinstance(jsc.get(it, "fields"), list):
                continue
            have = {jsc.js_string(f["key"]) for f in it["fields"] if _has_key(f)}
            for k in req_attrs:
                kk = jsc.nullish_str(k).strip()
                if not kk or kk in have or kk in denied_map or _names_the_ask(kk):
                    continue
                it["fields"].append(
                    {"key": kk, "label": _label_for(kk, None), "value": "not recorded yet"}
                )

    access_notes: list[str] = []
    # The `not timeline` here IS LOAD-BEARING, and the reasoning that once called it
    # redundant was wrong: `is_timeline` CONTAINS the sentinel, so a mixed
    # `['__all__', 'eta_delay_date']` with that key denied WOULD emit the note without it.
    # Every fixture used the sentinel alone, so the mutation test was pointed at the one
    # shape that cannot discriminate.
    for k in req_attrs if (any_keyed and not timeline) else []:
        kk = jsc.nullish_str(k).strip()
        if not kk:
            continue
        d = denied_map.get(kk)
        if not d:
            continue  # absence is annotated per row above
        access_notes.append(
            f"I can't share the {_label_for(kk, d).lower()} {_EM_DASH} "
            "please check with the office."
        )

    # Chatbot stock ask v2 S3 fix round 1, Blocking 1 (R6/R14/AC-SA312): once every
    # product in an `availability` reply has a branch, the presenter's per-item title
    # (`sorento_crm_mcp.presenters._availability_line`) already carries the whole R14
    # sentence - product, quantity, and the answer. A shared intro in front of it, or a
    # position number ("1. ") on it, both say something the four fixed sentences never
    # said: the old `_AVAILABILITY_NO` intro read as a statement about OUR stock even
    # for a `too_big` entry with plenty on hand, which R6 B1 forbids ("never reveal
    # stock"). So an answered `availability` reply gets no intro line and no numbering
    # (plan sample (g)); the presenter itself already returns "" for `intro` in this
    # case (see `_availability_intro`).
    stock_availability_answered = bool(
        jsc.js_string(e.get("result_type") or "") == "stock_availability"
        and isinstance(e.get("items"), list)
        and len(e["items"])
        and not any(
            jsc.truthy(jsc.get(jsc.get(it, "flags"), "needs_quantity"))
            for it in e["items"]
            if jsc.truthy(it)
        )
    )

    # The PRESENTER owns the intro whenever it emits `summary_items` - it states the page
    # geometry there, and this override would replace it with a sentence that says less.
    qs_presenter_owns_intro = bool(
        isinstance(e.get("summary_items"), list)
        and len(e["summary_items"])
        and e.get("has_result") is True
    )
    # Hand pass 12, Group A: a carried order-domain status/document MAY stay on focus
    # across a domain change ("remembering is one thing, whether I use it is another" -
    # owner ruling) - what must stop is a NON-ORDER tool's reply using it. `ctx["tool"]`
    # is this turn's own tool, so the gate is the same `ORDER_TOOLS` membership
    # `entity_ids_transformer` already uses for the tool-args side of this same carry.
    order_status = semantic_input.get("order_status")
    tool_is_order = jsc.js_string(ctx.get("tool") or "").strip() in ORDER_TOOLS
    if (
        tool_is_order
        and jsc.truthy(e.get("has_result"))
        and isinstance(e.get("items"), list)
        and len(e["items"])
        and order_status in ("outstanding", "delivered")
        and not qs_presenter_owns_intro
    ):
        e["intro"] = (
            "Here are the outstanding orders I found."
            if order_status == "outstanding"
            else "Here are the delivered orders I found."
        )

    # PR #1329 fix round: a dealer's incoming answer (`presenters._incoming_dealer`) is
    # one line per product plus the salesperson line - the same un-numbered, intro-less
    # shape as an answered `availability` reply.
    dealer_incoming = bool(
        jsc.js_string(e.get("result_type") or "") == "incoming_dealer"
        and isinstance(e.get("items"), list)
        and len(e["items"])
    )
    plain_lines = stock_availability_answered or dealer_incoming

    msg = (
        ""
        if plain_lines
        else jsc.js_string(e.get("intro") or "Here are the results.").strip() + "\n\n"
    )
    if isinstance(ctx.get("predicate"), dict):
        # Round 3 W1 (owner hand test on PR #833, "the message too long already"): a
        # counted set's header says what the list is; the tool's own intro under it
        # ("Stock summary for the requested products.") says it again.
        msg = ""

    # The summary follows the ANSWER, not the rows: `has_result is True`, never truthiness,
    # because a boolean arriving as the STRING "false" is truthy and would print a summary
    # onto a no-result reply. Byte-inert without `summary_items`.
    qs_render = bool(
        isinstance(e.get("summary_items"), list)
        and len(e["summary_items"])
        and e.get("has_result") is True
        and isinstance(e.get("items"), list)
        and len(e["items"])
    )
    if qs_render:
        for si in e["summary_items"]:
            if not isinstance(si, dict) or not isinstance(si.get("fields"), list):
                continue  # a hostile entry is skipped, never thrown on
            summary_lines = "\n".join(
                # `${f.label}` on an absent key renders the WORD `undefined`, not "None"
                # - and this string is sent to a customer.
                f"*{jsc.js_string(f.get('label', jsc.UNDEFINED))}:* {_fmt_value(f.get('value'))}"
                for f in si["fields"]
                if isinstance(f, dict)
            )
            if summary_lines:
                msg += summary_lines + "\n\n"

    action_links = e.get("action_links") or []
    for i, link in enumerate(action_links):
        label = jsc.get(link, "label")
        url = jsc.get(link, "url", jsc.UNDEFINED)
        msg += (
            f"{i + 1}. *{jsc.js_string(label) if jsc.truthy(label) else 'Link'}:* "
            f"{jsc.js_string(url)}\n"
        )
    if len(action_links):
        msg += "\n"

    # Fix round 8 on PR #833 (owner retest of round 7: "the answer should look exactly
    # when i check stock by product code, ... this applies to incoming, product attachment
    # etc and every other domain"): a counted set's rows are the product-code rows, from
    # the same tool in the same mode, through `_item_line` below. Only the intro differs.
    # W4: a page that continues a list numbers its rows where the list stands.
    set_predicate = ctx.get("predicate") if isinstance(ctx.get("predicate"), dict) else None
    set_row_offset = int(jsc.get(set_predicate, "offset") or 0) if set_predicate is not None else 0

    def _item_line(position: int, it: Any, *, numbered: bool = True) -> str:
        field_lines = "\n".join(
            f"*{jsc.js_string(jsc.get(f, 'label', jsc.UNDEFINED))}:* "
            f"{_fmt_value(jsc.get(f, 'value'))}"
            for f in (jsc.get(it, "fields") or [])
        )
        # An item with no fields at all is named by its own title: the stock tool's
        # availability mode renders the product and nothing else, so a dealer is never
        # shown a quantity (without this it printed as a bare "1. "), and an answered
        # `availability` item's title carries the whole verdict sentence (ported from
        # PR #1118 for chatbot-stock-ask-v2 S3, review round 3, Observation B).
        if not field_lines:
            field_lines = jsc.nullish_str(jsc.get(it, "title")).strip()
        # Chatbot stock ask v2 S3 fix round 1, Blocking 1 (R14/AC-SA312): an answered
        # `availability` item's title already starts "<code> x <Q>:", so it is not
        # numbered (plan sample (g) shows plain lines, not a numbered list).
        line = field_lines if not numbered else f"{position}. {field_lines}"
        flags = jsc.get(it, "flags")
        if jsc.truthy(flags) and jsc.truthy(jsc.get(flags, "discontinued")):
            line += "\n⚠️  *(PRODUCT DISCONTINUED)*"
        if jsc.truthy(flags) and jsc.truthy(jsc.get(flags, "expired")):
            line += "\n⚠️  *(EXPIRED)*"
        if jsc.truthy(flags) and jsc.truthy(jsc.get(flags, "unallocated")):
            line += "\n\U0001f6a9  *(PENDING ALLOCATION)*"
        elif jsc.truthy(flags) and jsc.truthy(jsc.get(flags, "partially_allocated")):
            line += "\n\U0001f6a9  *(PARTIAL ALLOCATION)*"
        return line

    # A3 (AC-905/AC-906): grouped sections, ONE generic branch for every tool - the
    # presenter already mapped `groups[].rows` through the same row->item builder
    # the flat list uses (`sorento_crm_mcp.presenters`), so this only adds a
    # heading per bucket and keeps the running item number global across groups
    # (a positional pick still resolves against `answers`, which stays the FLAT
    # `e.get("items")` below - grouping is presentation only, never carried state).
    groups_render = bool(
        isinstance(e.get("groups"), list) and len(e["groups"]) and not qs_render
    )
    if groups_render:
        position = 0
        for grp in e["groups"]:
            if not isinstance(grp, dict) or not isinstance(grp.get("items"), list):
                continue
            label = jsc.js_string(grp.get("label") or grp.get("key") or "").strip()
            if label:
                msg += f"*{label}*\n"
            for it in grp["items"]:
                position += 1
                msg += _item_line(position, it) + "\n\n"

    # Ported from PR #1118 (not merged), review round 3 (Observation B): while ANY
    # product still needs a quantity (D14), `intro` (`_noted_and_missing_question`)
    # has already said everything relevant - the items themselves carry no fields at
    # all and, before the `_item_line` title fallback above existed, printed as a
    # numbered list of nothing. Suppressed only while the ask is open; once every
    # product has a quantity the items DO carry the per-product verdict line and must
    # print, same as `qs_render`/`groups_render` above.
    stock_ask_render = bool(
        jsc.js_string(e.get("result_type") or "") == "stock_availability"
        and isinstance(e.get("items"), list)
        and any(
            jsc.truthy(jsc.get(jsc.get(it, "flags"), "needs_quantity"))
            for it in e["items"]
            if jsc.truthy(it)
        )
    )

    # A quantity ask prints the SUMMARY ONLY: the two order perspectives are separate
    # questions and the parser already separates them. The ROWS are suppressed from the
    # MESSAGE, never from the STATE - `answers` below is untouched, so a positional pick
    # still resolves against the same page rows. And ONLY the numbered list goes: the
    # multi-company note reads `e.items` for attribution and must keep seeing the real rows.
    if set_predicate is not None and stock_ask_render:
        # Fix round 8 on PR #833: a dealer on availability only, still owing a quantity,
        # reads the set's products by code (the availability item's own title, as the
        # product-code answer names it), then the tool's own question - the set intro
        # replaced the intro that carried it.
        for i, it in enumerate(e.get("items") or []):
            title = jsc.nullish_str(jsc.get(it, "title")).strip()
            if title:
                msg += f"{i + 1 + set_row_offset}. {title}\n\n"
        question = jsc.js_string(e.get("intro") or "").strip()
        if question:
            msg += question + "\n\n"
    for i, it in enumerate(
        [] if (qs_render or groups_render or stock_ask_render) else (e.get("items") or [])
    ):
        msg += _item_line(i + 1 + set_row_offset, it, numbered=not plain_lines) + "\n\n"
    if dealer_incoming and jsc.truthy(e.get("closing")):
        # The presenter's dealer closing is the refer line (`presenters.py`, `closing`);
        # printed through `turn/refer.py` so the turn is marked for Customer asks
        # (CUSTOMER-ASKS-REFER-ONLY).
        from app.services.chatbot.turn import refer

        msg += refer.sentence() + "\n\n"
    # Item 8: the product projection's miss lines, one per asked word, AFTER the items
    # (`_project_product_specs`). Byte-inert when the key is absent.
    for miss in e.get("spec_misses") or []:
        if isinstance(miss, dict):
            msg += f"*{jsc.js_string(miss.get('label', jsc.UNDEFINED))}:* {_fmt_value(miss.get('value'))}\n\n"

    # -- multi-company: name the companies that came back EMPTY --------------- #
    # A FOUND row already says which company it belongs to. What the customer cannot see is
    # the company that WAS searched and returned nothing.
    raw_lookup_cos = e.get("lookup_companies")
    lookup_cos: list[Any] = raw_lookup_cos if isinstance(raw_lookup_cos, list) else []
    if len(lookup_cos) > 1:

        def _co_of_row(it: Any) -> str:
            f = jsc.find(
                jsc.get(it, "fields") or [],
                lambda x: jsc.truthy(x)
                and (jsc.js_string(jsc.get(x, "key") or "") == "company_name" or jsc.get(x, "label") == "Company"),
            )
            return jsc.nullish_str(jsc.get(f, "value")).strip() if jsc.truthy(f) else ""

        shown_cos = {c for c in (_co_of_row(it) for it in (e.get("items") or [])) if c}
        # NEVER assert absence from a NEGATIVE. Rows present but unattributed means the CRM
        # did not stamp them, and declaring every lookup company silent underneath rows we
        # just printed is a worse statement than the one this block exists to fix.
        can_attribute = not len(e.get("items") or []) or len(shown_cos) > 0
        # `code` is the canonical code the customer recognises, never a uuid: for types with
        # no code the resolver fills it with the record's OWN uuid, and four promotion uuids
        # once printed under "no promotions records for ...".
        entities0 = ctx.get("entities")
        if isinstance(entities0, str):
            entities0 = _safe_json(entities0)
        subject = _axis_labelled_subject(entities0)
        # The noun comes from the envelope's own `result_type`, so there is no local
        # vocabulary here to go stale.
        noun = jsc.js_string(e.get("result_type") or "").replace("_", " ").strip()
        what = (f"{noun} records" if noun else "records") + (
            f" found for {subject}{_date_window_phrase(semantic_input)}" if subject else ""
        )
        silent = [
            n
            for n in (jsc.nullish_str(jsc.get(c, "name")).strip() for c in lookup_cos)
            if n and n not in shown_cos
        ]
        if can_attribute and silent:
            # ONE sentence for all the silent companies, not one per company. The subject is
            # the same in every copy, so repeating it per company said the customer's own
            # name back to them once per lookup - and on a total miss (nothing found
            # anywhere) that is the whole reply, twice over. A single silent company is
            # byte-identical to before.
            names = (
                silent[0]
                if len(silent) == 1
                else f"{', '.join(silent[:-1])} and {silent[-1]}"
            )
            msg += f"*{names}:* no {what}." + "\n\n"

    if access_notes:
        msg += "\n".join(access_notes) + "\n\n"

    # Chatbot stock ask v2 S3 fix round 2, Blocking 1 (R6 B1, R14, AC-SA312): production
    # always sets `last_updated_at` (`StockService._apply_stock_visibility`, every policy
    # mode, availability included), so an answered `availability` reply would otherwise
    # end in a line that is not one of the four R6 sentences and whose digits are ours -
    # exactly what the intro and the numbering were already gated off of above, for the
    # same reason. `compact` and `detailed` are untouched (R10): neither is a
    # `stock_availability` reply.
    #
    # Owner hand test 26 Sep, slice 1: keyed on the RESULT TYPE, not on "answered". The
    # quantity question ("How many units do you need?") is an availability reply too,
    # and it printed the timestamp on T1, T3, T8, T13 and T16.
    stock_availability_reply = jsc.js_string(e.get("result_type") or "") == "stock_availability"
    ts = None if stock_availability_reply else _fmt_ts(e.get("last_updated_at"))
    if ts:
        msg += f"_Data last updated: {ts}_"

    # E2 (attribute-first asks, AC-1316): a HAS turn's set-answer header, PREPENDED
    # as its own line ahead of everything above - the block itself (intro, items,
    # summaries, ...) is untouched. Deferred-import: `answer.py` imports FROM this
    # module (`DATE_PARAMS`, `space_id_or_default`), so a module-level import here
    # would be circular.
    #
    # `set_header` also travels out as its OWN key (below, `out["set_header"]`), not
    # only baked into `response` - R-d (owner hand pass 7, 19 Sep 2026) has
    # `turn/compose.py` print this arm's `lane_text` VERBATIM as the whole section
    # whenever the envelope carries one, rows or not (`turn/compose.py`'s own R-d
    # comment), so without a header of its own a counted-set answer's `lane_text`
    # would render with no leading count/attribute line at all.
    predicate = ctx.get("predicate") if isinstance(ctx.get("predicate"), dict) else None
    set_header: str | None = None
    # A set too long for one message is answered with its count and a question, and no
    # rows (owner ruling, 26 Sep 2026) - `answers` and the files go with them below, so
    # neither a positional pick nor an attachment send can reach a row nobody was shown.
    set_withheld = False
    if predicate is not None:
        from app.services.chatbot.lanes.business import answer as answer_mod

        qualifying_total = int(jsc.get(predicate, "qualifying_total") or 0)
        require = jsc.get(predicate, "require") or {}
        class_labels = jsc.array(jsc.get(predicate, "class_labels"))
        # A recount of a carried set (`turn_runtime.page_the_set`, the answer to "how
        # many should I show?") runs no class read of its own and names its noun itself.
        set_noun = jsc.nullish_str(jsc.get(predicate, "set_noun")).strip() or answer_mod.set_noun_for(class_labels)
        # `set_noun_for` is always plural (its own contract, AC-1316) - singular
        # only for the ONE-qualifying-product header ("1 tap has ...", never
        # "1 taps has ...") is the class label ITSELF (REV-N2/AC-1337), never a
        # naive "-1 char" strip of the pluralised noun: that guess turned
        # "bathroom accessories" into "bathroom accessorie", not the real
        # singular "bathroom accessory". Only the single-label case has one to
        # use; the "products" fallback (zero or blended labels) has no
        # singular of its own and keeps its old strip.
        if qualifying_total == 1:
            single_labels = [label for label in class_labels if label and label.strip()]
            if len(single_labels) == 1:
                set_noun = single_labels[0].strip().lower()
            elif set_noun.endswith("s"):
                set_noun = set_noun[:-1]
        named = _set_named_count(semantic_input)
        # W4: a page that continues a list starts past what was already listed.
        offset = int(jsc.get(predicate, "offset") or 0)
        remaining = max(qualifying_total - offset, 0)
        if named is None and qualifying_total > answer_mod.SET_LIST_MAX and jsc.get(predicate, "breakdown"):
            # Fix round 9 on PR #833: the count and the breakdown by the next attribute.
            shown = 0
            set_withheld = True
        elif named is None and qualifying_total > answer_mod.SET_LIST_MAX:
            # Nothing splits the set (one brand, every key alike): list what fits rather
            # than a bare count with nothing to pick from.
            shown = min(answer_mod.SET_LIST_MAX, remaining)
        elif named is None:
            shown = remaining
        else:
            shown = min(named, answer_mod.SET_LIST_MAX, remaining)
        if not set_withheld:
            # Reviewer B3 on PR #833 (R8/AC-1330 restored): the header states what the
            # rows show, never what was asked for. A tool that still cut rows at its
            # cap rendered fewer PRODUCTS than were sent, and the header must say
            # "Here are the first N" over those, never claim the list is complete.
            rendered = _rendered_product_count(e.get("items") or [])
            if rendered is not None and rendered < shown:
                shown = rendered
        header = answer_mod.build_set_header(
            qualifying_total,
            shown,
            set_noun,
            require,
            description=jsc.get(predicate, "description"),
            not_understood=jsc.get(predicate, "unrecognized_terms"),
            offset=offset,
            previous_total=jsc.get(predicate, "previous_total"),
            breakdown=jsc.get(predicate, "breakdown"),
        )
        set_header = header
        # Round 3 W4: the default brand answered, so the reply closes with the others -
        # above the footer, which stays the last line as it is on the product-code answer.
        # Fix round 9 on PR #833: a word the set reader could not use closes the reply in
        # the same place ("Couldn't find: ..."), the "did not match" line.
        other_brands = "\n".join(
            line
            for line in (
                answer_mod.not_understood_line(jsc.get(predicate, "unrecognized_terms")),
                answer_mod.other_brands_line(jsc.get(predicate, "other_brands"), require),
            )
            if line
        )
        if set_withheld:
            msg = header
        else:
            body = msg.strip()
            footer = f"_Data last updated: {ts}_" if ts else ""
            if other_brands:
                if footer and body.endswith(footer):
                    body = f"{body[: -len(footer)].strip()}\n\n{other_brands}\n\n{footer}"
                else:
                    body = f"{body}\n\n{other_brands}"
            msg = f"{header}\n\n{body}"
        if set_withheld and other_brands:
            msg = f"{msg.strip()}\n\n{other_brands}"

    final_response = msg.strip()
    if so_bucket_refusal:
        final_response = f"{so_bucket_refusal}\n\n{final_response}"

    out: dict[str, Any] = {
        "response": final_response,
        "response_intro": e.get("intro"),
        # The counted-set header alone (AC-1316/AC-1317), so a reader that renders its
        # own rows can still prefix the right line - see the note above `predicate`.
        "set_header": set_header,
        # Integration round 11 on PR #833: whether that header names a DESCRIBED set
        # (`predicate.description`), which is the one intro line of a carried set's
        # follow-up ("cert" after "gunmetal basin") and so is never withheld with it.
        "set_described": bool(predicate is not None and jsc.array(jsc.get(predicate, "description"))),
        # GROUPED: the flat `items` order and the NUMBERED order the customer just read
        # are two different orders, and `answers` is what a positional pick ("2") resolves
        # against - so a grouped answer used to hand back a different record than the one
        # numbered 2 on screen (review, should-fix 4). Flattened in RENDER order, which is
        # the only order the customer can be talking about. Ungrouped, this is `items`
        # unchanged, so nothing else moves.
        "answers": [] if set_withheld else (_rendered_answers(e) if groups_render else e.get("items")),
    }
    # Spread-in, not defaulted: a reply with no summary keeps EXACTLY the keys it has today.
    if qs_render:
        out["summary_items"] = e["summary_items"]
    if groups_render:
        out["groups"] = e["groups"]
    out.update(
        {
            "attachments": [] if set_withheld else (e.get("attachments") or []),
            "action_links": e.get("action_links") or [],
            "last_updated_at": e.get("last_updated_at") or None,
            "has_result": bool(jsc.truthy(e.get("has_result"))),
            "alternatives": e["alternatives"] if isinstance(e.get("alternatives"), list) else [],
            "relaxed_axis": e.get("relaxed_axis") if "relaxed_axis" in e else None,
            "field_access": e.get("field_access") if "field_access" in e else None,
            "requested_attributes": req_attrs,
            # false = the CRM served no keyed fields, so projection and access notes were
            # skipped. Sustained false with a non-empty `requested_attributes` means the MCP
            # process needs restarting, NOT that the parser stopped emitting keys.
            "keys_served": any_keyed,
        }
    )
    if len(lookup_cos) > 1:
        out["lookup_companies"] = lookup_cos
    if isinstance(e.get("stock_availability"), list):
        # Ported from PR #1118 (not merged), D25: the backend states, per product,
        # whether a quantity is still required - the one fact the open stock task is
        # built from (`turn/task.py::tasks_after_reply`). Carried through UNREAD by
        # this lane: the sentence the dealer sees is the MCP presenter's, as it
        # already is for every other row.
        out["stock_availability"] = [
            row for row in e["stock_availability"] if isinstance(row, dict)
        ]
    if jsc.js_string(ctx.get("tool") or "") == _FORMS_LIST_TOOL:
        # Beside `out["answers"]`, never instead of it: the numbered list still prints
        # through the generic per-row grammar, and `forms_ask` only gives `envelope_of`
        # something to arm a pick from (item 1).
        out["forms_ask"] = _forms_browse_ask(e, ctx)
    return out


# --------------------------------------------------------------------------- #
# fetch-result
# --------------------------------------------------------------------------- #


def fetch_result(
    item: dict[str, Any] | None, *, tool: Any = None, tier_probe: Any = None
) -> dict[str, Any]:
    """`sub-fetch-results`' ONE exit, three mutually exclusive arms.

    Content, not node identity, tells them apart - and each discriminating field is one the
    respective DOWNSTREAM reader already depends on, not a new assumption:
    `tier_any_available` is the boolean `if-tier-has-any` reads, `error` is the field the
    error path already reads, and anything else is the normal CRM envelope.

    `tier_probe` is ALWAYS present on the result arm (a value or `None`), never omitted: the
    tier-probe arm ran on about 3 of 220 live turns that reach this exit at all, so the key
    has to be stable or its reader needs a guard nobody wrote.
    """
    j = item if isinstance(item, dict) else {}
    if isinstance(j.get("tier_any_available"), bool):
        return {**j, "_fetch_arm": "tier-ask"}
    if isinstance(j.get("error"), str):
        return {**j, "_fetch_arm": "error"}
    return {**j, "tool": tool, "tier_probe": tier_probe, "_fetch_arm": "result"}
