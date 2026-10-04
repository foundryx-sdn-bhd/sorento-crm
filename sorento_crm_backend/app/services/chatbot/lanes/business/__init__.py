"""The business lane. S6a owns resolve + gate; fetch (S6b) and answer (S6c) follow.

`run_until_exit` is the ONE call site the engine makes into this package. It exists so
S6a can ship without restructuring `engine.py`: the head still routes, and three of its
arms now come here first and hand n8n a resolve+gate result instead of nothing.

**Which arms, and why those three.** `sorento-consume-main`'s `route` Switch wires
`check_promotion` (arm 8) to `tag-entry-access-check` and both `stock_denied` (arm 11, via
`Edit Fields2`) and `business_query` (the fallback) to `tag-entry-resolve`; both tags then
call `sub-main-processing`, which calls `sub-resolve-and-gate`. So the three arms are the
sub's three real call sites, `entry` is the tag they carried, and `not_allowed_check_stock`
is `Edit Fields2`' one field.

After S6a the CRM returns `delegate = "business_query"` with `delegate_payload` = the
sub's own output item. n8n's `sub-main-processing` enters at `resolve-arm`; its stand-in
chain (`resolve-gate` / `aggregate-gate` / `annotate-incoming-gate` plus the five
name-preserving Code nodes) re-emits the six contract fields, so every by-name reader
downstream is unchanged and the old wiring is one edge away from being restored.
"""
from __future__ import annotations

from typing import Any

import logging
import time

from app.services.chatbot import contracts
from app.services.chatbot import copy as reply_copy
from app.services.chatbot import jsc
from app.services.chatbot import do_ask
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business import low_stock_ask
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.lanes.business import services as business_services
from app.services.chatbot.turn import policy_rows
from app.services.chatbot.lanes.business.services import (
    FetchServices,
    ResolveGateServices,
    outstanding_brand_echo,
    outstanding_customer_echo,
    resolve_warehouse_token,
)

logger = logging.getLogger(__name__)

# `branch_kind` -> the `entry` its `tag-entry-*` node stamps. The three arms that reach
# `sub-resolve-and-gate`, and nothing else: an arm absent from this map never enters the
# lane, which is what keeps `run_until_exit` a no-op for the other ten.
ENTRY_BY_BRANCH_KIND: dict[str, str] = {
    "check_promotion": "access_check",
    "stock_denied": "resolve",
    "business_query": "resolve",
}

# The n8n lane the caller must still run after S6a. One name for all three arms because
# they converge on ONE node (`resolve-arm`), and the arm they take there is `_exit_kind`.
DELEGATE = "business_query"

# D13 (PLAN-chatbot-outstanding-report.md): the field-reveal key that gates SO figures
# on the outstanding report. Read the way `answer._CROSSDOMAIN_RUNG_GRANT` reads its own
# key - `ctx["access"]["attributes"]`, the same set `output_structurer`'s restricted-field
# drop reads. Declared here (not in `answer.py`) because `run_fetch`, this module's own
# function, is the one place that checks it, before any tool call.
_OUTSTANDING_SO_GRANT = "sales_orders.outstanding"

# PLAN-chatbot-sales-report.md, S4 wiring point 4 (AC-1651): the sales report's OWN
# field-reveal key. Unlike `_OUTSTANDING_SO_GRANT` above there is no fallback scope
# here - the report has nothing partial to answer with, so its absence refuses the
# WHOLE ask before any fetch, with one line and nothing else.
_SALES_REPORT_GRANT = "sales_orders.sales_report"
#: The one sentence a contact without that key sees, verbatim (AC-1651) - the sibling
#: of `fetch.SO_NOT_ENABLED_MESSAGE` / `LOW_STOCK_NOT_ENABLED_MESSAGE` above, a
#: literal for the same reason: one wording, in one place.
SALES_REPORT_NOT_ENABLED_MESSAGE = "Sales report is not enabled for your account."
#: PLAN-retail-sales-reports-26sep S1: the sales analysis tool, gated by the SAME key.
_SALES_ANALYSIS_TOOL = "crm_sales_analysis"

#: PLAN-chatbot-top-x-hot-selling-24sep.md "Lane wiring (S4)". The ranking's tool, and
#: the fixed lines the lane sends BEFORE any fetch when the ask is not settled yet
#: (owner rulings 26 Sep 2026: no default metric, never assume the grain or the basis).
#: The MCP presenter declares the same three literals for its goldens; the backend keeps
#: its own copy because its container does not ship `sorento_crm_mcp`, and
#: `tests/chatbot/test_top_selling_lane.py` pins the two equal.
TOP_SELLING_TOOL = "crm_top_selling_report"
TOP_SELLING_ASK_METRIC = "By quantity or by amount?"
TOP_SELLING_ASK_GROUP = (
    "Do you want the top items inside one category, or the categories ranked against each other?"
)
TOP_SELLING_ASK_BASIS = "Delivered (transferred to DO) or ordered?"
TOP_SELLING_REFUSED_OTHER_CUSTOMER = "Sorry, I can only share sales figures for your own account."
#: Owner retest of round 4 (27 Sep 2026, R8): one short question in words when a message
#: inside a ranking cannot be placed.
TOP_SELLING_UNCLEAR = "Sorry, I didn't get that. What would you like to change in the ranking?"

#: PLAN-low-stock-report S6 (AC-62/AC-64). The intent that overrides the inventory domain's
#: default tool pick, the tool it picks, and the per-contact key that gates it - all three
#: read in ONE place (`run_fetch`), before any tool call, because this tool's fetch creates
#: a reorder run.
_LOW_STOCK_INTENT = "low_stock_report"
_LOW_STOCK_TOOL = "crm_low_stock_report"
_LOW_STOCK_GRANT = "scm.low_stock_report"
#: The one sentence a contact without that key sees, verbatim (AC-64) -
#: `fetch.SO_NOT_ENABLED_MESSAGE`'s sibling, and a literal for the same reason: one
#: wording, in one place, so the lane and the route's own 403 cannot drift.
LOW_STOCK_NOT_ENABLED_MESSAGE = "Low stock report is not enabled for your account."
#: Console round 3, defect A: what the customer hears when the CALL ITSELF failed - the
#: lane's MCP client gives up at `chatbot_mcp_timeout_seconds` (10 s), and a run that
#: outlasts it raises `httpx.ReadTimeout` in `call_tool`. The generic lane failure line
#: ("Sorry, I ran into a problem understanding that") is wrong twice over: the bot
#: understood perfectly, and the report is very likely still being built and about to be
#: pushed. Same wording as the presenter's own error line, so the customer hears ONE
#: sentence for this tool's failures however they arise.
LOW_STOCK_UNAVAILABLE_MESSAGE = "Could not run the low stock report right now."

_OUTSTANDING_SCOPE_OPTIONS: tuple[dict[str, Any], ...] = (
    {"idx": 1, "label": "Sales orders", "value": "so"},
    {"idx": 2, "label": "Delivery orders", "value": "do"},
    {"idx": 3, "label": "Both", "value": "both"},
)


def _outstanding_filters_from(entities: Any, semantic_input: dict[str, Any]) -> dict[str, Any]:
    """S4 point 4's `outstanding_filters`: the parsed product/dates/customer/location,
    carried across the scope-question turn and the detail-offer turn so neither has to
    re-parse the message that named them."""
    # AC-1119 / console run 4 finding 5: the SAME rule the tool arguments use - the code
    # the customer typed wins over a family sibling. Shared, because the question, its
    # answer and the header all have to name one product.
    product_codes = fetch_mod.outstanding_product_codes(entities, semantic_input)
    customer_ids: list[Any] = []
    for e in entities or []:
        if not isinstance(e, dict):
            continue
        if e.get("entity_type") == "customer":
            uid = e.get("uuid")
            # #1262 slice 2 (F1c): never a kind-pick's printed label or an
            # unresolved code, only a real uuid.
            if fetch_mod.is_uuid(uid) and uid not in customer_ids:
                customer_ids.append(uid)
    if not customer_ids:
        # R13/R15: the same fallback `fetch._outstanding_filters_from_ctx` makes. A turn
        # that RE-ASKS this question (an out-of-range answer, or R15's refinement of an
        # open one) resolved no customer of its own - the ids rode in on the carried
        # filter set, already resolved, and they have to ride back out on it too or the
        # re-asked question loses the only subject it has.
        customer_ids = [
            uid
            for uid in jsc.array(semantic_input.get("outstanding_carried_customer_ids"))
            if fetch_mod.is_uuid(uid)
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
        # AC-1132 (review round, 13 Sep 2026): the TOKEN travels with the codes, so the
        # answering turn prints the same `Location: IB (BRW-IB, MWH-IB)` header the
        # asking turn did instead of re-running over every warehouse.
        "location_token": semantic_input.get("outstanding_location_token"),
        # #1262 slice 9 (F1a) follow-up: `semantic_input["outstanding_brand_ids"]` is
        # ALREADY the resolved uuids by the time this runs - `run_fetch` calls
        # `_resolve_report_product_and_location` (which resolves a fresh brand token OR
        # falls back to `outstanding_carried_brand_ids`, R13/R15/D10) before ever
        # reaching the scope-ask arm this feeds. No second fallback needed here.
        "brand_ids": [
            b for b in jsc.array(semantic_input.get("outstanding_brand_ids")) if fetch_mod.is_uuid(b)
        ],
    }


def _low_stock_not_enabled() -> dict[str, Any]:
    """AC-64: refuse the low stock ask BEFORE any fetch, and end in the team picker.

    Two things ride on this fragment. The refusal LINE is the reply's first line, verbatim
    (`LOW_STOCK_NOT_ENABLED_MESSAGE`) - the customer is told plainly that the report is not
    enabled for them, not given a vague miss. And the outcome is `not_found`, which is the
    lane's existing route into `_run_miss_half` - the same path a total miss takes
    (`TestTotalMissEscalates`), so the escalate offer and the team picker that follow are
    the ones already in place rather than a second copy of them here.

    `escalate` states on the fragment itself that this turn must end that way, so a reader
    of the fragment (or of a trace) can see the intent without replaying the miss half.

    No tool is called, so no reorder run is created - which is the whole point of gating
    here rather than letting the route answer 403 after the fetch.
    """
    structured: dict[str, Any] = {
        "response": LOW_STOCK_NOT_ENABLED_MESSAGE,
        "response_intro": None,
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": False,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
        "outcome": "not_found",
        "escalate": True,
        "response": LOW_STOCK_NOT_ENABLED_MESSAGE,
    }


def _low_stock_unavailable(header: str | None = None) -> dict[str, Any]:
    """Console round 3, defect A: the low stock CALL failed - say so in this tool's own
    words, never the lane's generic "I ran into a problem understanding that".

    The lane's MCP client gives up after `chatbot_mcp_timeout_seconds` (10 s). The route
    now answers `pending` inside that budget (`_sync_wait_seconds`'s cap), so this path is
    the remainder: a genuine transport or tool failure. It is a TERMINAL answer rather than
    the miss half's not-found arm (`has_result: True`, like the presenter's own error
    envelope) - the miss half would compose the inventory domain's generic "Could not find
    inventory" line, which says the wrong thing about a report that failed to build.
    `escalate` still rides on the fragment for any consumer that offers the team picker.
    """
    # LOWSTOCK-FILTER-ASK (owner hand test, 3 Oct 2026): every low stock reply names the
    # filters it was asked with, the failure line included.
    text = f"{header}\n{LOW_STOCK_UNAVAILABLE_MESSAGE}" if header else LOW_STOCK_UNAVAILABLE_MESSAGE
    structured: dict[str, Any] = {
        "response": text,
        "low_stock_report": True,
        "response_intro": None,
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
        "escalate": True,
        "response": text,
    }


def _outstanding_scope_ask(
    entities: Any, semantic_input: dict[str, Any], *, db: Any = None
) -> dict[str, Any]:
    """AC-1130: ARM the scope question, no fetch this turn - the FIRST ask, filters
    freshly parsed off this turn's resolved entities."""
    return _outstanding_scope_ask_from_filters(
        _outstanding_filters_from(entities, semantic_input), db=db
    )


def _outstanding_scope_filter_lines(
    filters: dict[str, Any], *, customer_name: str = "", brand_name: str = ""
) -> list[str]:
    """The scope question's header: the SAME four lines the report prints, in the same
    order and the same words (`sorento_crm_mcp.presenters._outstanding_report`, the
    `Product:` / `Customer:` / `Location:` / `Order date:` block), one writer for every
    scope question - the first ask, R15's refinement re-ask, an out-of-range re-ask and
    the ask resumed after a customer picker (R16).

    R19 (owner ruling, 13 Sep 2026): EVERY line prints, every time, `all` where the
    filter was not given. The previous rule here printed only the filters that were set,
    which read fine until a customer picked one off a picker and got back `Product:
    SRTKT39SS` and nothing else: "i have chosen the customer already ... what about the
    customer, sometimes i might even have dates, location filters, they should be stated
    down in this message also." A question about to search is owed the same statement of
    scope the answer gets.

    The wording is the presenter's and is duplicated here, for the reason
    `_outstanding_offer_block` already records about the offer text: the report's header
    is rendered MCP-side and the backend container does not carry that package. The rule
    is the same one - this copy FOLLOWS the presenter and invents nothing - and both
    copies are pinned by tests that assert the exact bytes.

    The `Customer:` line prints NAMES, never the resolved ids: they are uuids, and a
    uuid never reaches a customer's screen. `customer_name` is handed in already
    rendered by `outstanding_customer_echo` - the REPORT's own `_customer_echo`, run
    over the same ids - so the question and the answer cannot say different things
    about the same filter (R19b). They did: the question printed the picker's roster
    label with its company-code suffix ("CHIN CHUN HARDWARE SDN BHD (MCH, SRT)") while
    the report named the ledger rows. AC-1163's distinct, first-seen rule comes with
    it, because it lives in that one function.

    `brand_name` (#1262 slice 9 follow-up, F1a): the SAME additive-only rule the MCP
    presenter's `_outstanding_header_lines` uses for its own fifth `Brand:` line - it
    prints ONLY when filled, never `all`, because an ordinary (no-brand) ask never named
    one at all (unlike Product/Customer/Location/Order date, which are always one of
    this report's four axes whether or not the customer named a value for it).
    """
    # The `Product:` line names every code the question is about, the way the `Customer:`
    # line names every ledger: "all" over a ten-variant roster is one question about ten
    # products, and naming one of them read as a report about that one (turn 0a6f0379).
    product_codes = [
        jsc.js_string(c).strip() for c in jsc.array(filters.get("product_codes")) if jsc.truthy(c)
    ]
    product_code = (
        ", ".join(product_codes)
        if product_codes
        else jsc.js_string(filters.get("product_code") or "").strip()
    )

    codes = [jsc.js_string(c) for c in jsc.array(filters.get("warehouse_codes")) if jsc.truthy(c)]
    token = jsc.js_string(filters.get("location_token") or "").strip()
    if codes:
        # The presenter's own rule: an exact code prints alone (brackets would only
        # repeat it), a word that resolved to several codes names them.
        location = (
            f"{token} ({', '.join(codes)})" if token and [token] != codes else token or ", ".join(codes)
        )
    else:
        location = "all"

    order_date = order_date_text(
        filters.get("date_filter_start"), filters.get("date_filter_end")
    )

    lines = [
        f"Product: {product_code or 'all'}",
        f"Customer: {jsc.js_string(customer_name or '').strip() or 'all'}",
        f"Location: {location}",
        f"Order date: {order_date}",
    ]
    brand_label = jsc.js_string(brand_name or "").strip()
    if brand_label:
        lines.append(f"Brand: {brand_label}")
    return lines


def order_date_text(start: Any, end: Any) -> str:
    """What the `Order date:` line SAYS: `01/09/2026 to 30/09/2026`, one date when the
    two match, `all` when the fetch ran with no window at all.

    One writer, two readers: the scope question's own four-line header above, and the
    answer header a fetch that ran with a window now carries
    (`turn_runtime.envelope_of` -> `turn/compose.py`, browser pass 6 item 4 - the
    September window reached the tool and the reply never said so). Two copies of this
    would let the question and the answer state the same window in different words.
    """
    first = _outstanding_ddmmyyyy(start)
    last = _outstanding_ddmmyyyy(end)
    if first and last:
        return first if first == last else f"{first} to {last}"
    return first or last or "all"


def _outstanding_ddmmyyyy(value: Any) -> str:
    """`2026-09-01` -> `01/09/2026`; anything else -> "" (nothing to print)."""
    text = jsc.js_string(value or "").strip().split("T")[0]
    parts = text.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return ""
    return f"{parts[2]}/{parts[1]}/{parts[0]}"


def _outstanding_scope_ask_from_filters(
    filters: dict[str, Any], *, db: Any = None
) -> dict[str, Any]:
    """AC-1130/AC-1132: ARM the scope question, no fetch this turn. Builds the SAME
    `structured` shape `output_structurer`'s `crm_outstanding_report` branch returns for
    a real hit (`response` + `outstanding_ask`), so `tail/compile_state.py` reads both
    through one code path - see that function's own docstring. `filters` is either
    freshly parsed (the first ask) or carried forward unchanged (an out-of-range
    re-ask, AC-1132)."""
    # R19: the report's own four header lines, always all four - see
    # `_outstanding_scope_filter_lines`. (R13 dropped the `Product:` line on a
    # customer-only ask because it printed empty; the answer to that is a value on every
    # line, `all` included, not a missing line.)
    # R19b: the names come from the customer ROWS, through the report's own echo, so the
    # question and the report cannot disagree about the same filter. `db` is None outside
    # a real turn (this module's own direct `run_fetch` tests), which reads as `all` -
    # the same no-op a turn with no customer gets.
    header = "".join(
        f"{line}\n"
        for line in _outstanding_scope_filter_lines(
            filters,
            customer_name=(
                outstanding_customer_echo(db, filters.get("customer_ids")) if db is not None else ""
            ),
            # #1262 slice 9 follow-up: the SAME "db is None -> no-op" rule, one echo
            # over one join, so the question and the report never name the brand
            # differently either (R19b's own rule, extended).
            brand_name=(
                outstanding_brand_echo(db, filters.get("brand_ids")) if db is not None else ""
            ),
        )
    )
    text = (
        header + "Outstanding for which document?\n"
        "1. Sales orders (not yet transferred to DO)\n"
        "2. Delivery orders (not yet delivered)\n"
        "3. Both"
    )
    structured: dict[str, Any] = {
        "response": text,
        "outstanding_ask": {
            "kind": "outstanding_scope",
            "last_result_set": [dict(row) for row in _OUTSTANDING_SCOPE_OPTIONS],
            "filters": filters,
        },
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        # AC-1139: this question carries nothing but itself - `tail/compile_state.py`
        # reads the marker and skips the generic search-scope header.
        "outstanding_report": True,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
    }


def _outstanding_offer_closed(parse_output: dict[str, Any], db: Any) -> dict[str, Any]:
    """R22(a): the customer DECLINED the open outstanding question - one acknowledgement,
    and nothing armed.

    ONE short line, from the registry's own `offer_declined` key ("Okay, noted."),
    rendered through the same `copy.render` path every canned reply uses so the owner can
    edit the wording without a deploy. The first cut used `clarify_menu` - the closest
    line that existed - and the live check showed why that is wrong: "I see you're trying
    to decline, Let me understand more. Are you asking about any of these? ..." re-opens a
    conversation the customer has just closed. `escalation_declined` is the other decline
    line and names an escalation nobody asked for; `offer_declined` is its sibling for
    every other offer the bot makes.

    Same `structured` shape as the re-offer below, minus the `outstanding_ask` - that
    absence is the whole difference, and it is what stops `tail/compile_state.py` arming
    the question again.
    """
    templates = reply_copy.resolve(db) if db is not None else reply_copy.fallback_copy()
    structured: dict[str, Any] = {
        "response": templates.render("offer_declined"),
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        # This reply carries no search of its own, so the generic scope header has
        # nothing to disclose about it (AC-1139's own marker).
        "outstanding_report": True,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
    }


def top_selling_ceiling_note(slot: dict[str, Any]) -> str | None:
    """The line said above a ranking whose named N passed the shared safety ceiling
    (owner, 30 Sep 2026: no 100 cap, and no silent clamp either). `fetch.py` asks the
    route for the ceiling; this says so. None for every N at or under it, and for a
    detail pick, which lists no ranking."""
    from app.services.sales_report_service import TOP_SELLING_N_CEILING

    top_n = slot.get("top_n")
    if slot.get("detail_code") or isinstance(top_n, bool) or not isinstance(top_n, (int, float)):
        return None
    if int(top_n) <= TOP_SELLING_N_CEILING:
        return None
    # Promises no count: fewer items than the ceiling may have sold.
    return f"I can list at most the top {TOP_SELLING_N_CEILING:,} in one reply."


def _top_selling_question(slot: dict[str, Any]) -> tuple[str, str] | None:
    """S4 point 8a: the one question an unsettled top selling ask gets, in this order:
    the grain when the parser could not tell a category filter from a category ranking,
    the metric when none was named, the basis when a word could mean either. None once
    the ask is settled. A picked row (`detail_code`) is never asked about: the ranking
    it came from already ran. Returns `(axis, line)`: the axis is what the reply records
    as asked (`top_selling_asked`), so the next message is read as its answer only if it
    answers that axis (`turn/apply._top_selling_waiting`, reviewer B2 on PR #1273)."""
    if slot.get("detail_code"):
        return None
    if slot.get("rank_group") == "unclear":
        return "group", TOP_SELLING_ASK_GROUP
    if slot.get("rank_by") not in ("quantity", "amount"):
        return "metric", TOP_SELLING_ASK_METRIC
    if slot.get("basis") == "unclear":
        return "basis", TOP_SELLING_ASK_BASIS
    return None


def _top_selling_category_ids(
    slot: dict[str, Any], *, db: Any
) -> tuple[list[str], bool, list[str], list[str]]:
    """The category filter a top selling ask carries, as ids: a picked category row's
    exact code (`category_code`), else the category words the messages named
    (`category_words`). Returns `(ids, named, ambiguous, unknown)`. `ambiguous` is what
    ONE word matched when it matched several (category codes, or class labels): the lane
    asks which (reviewer S1, PR #1273), it never takes them all. `unknown` is every word
    that matched nothing: the lane says so and keeps the ranking (owner retest, 27 Sep
    2026), never a miss and never a silent widening said nowhere.

    A word matching no category code or name goes through the catalogue's class
    vocabulary (`services.resolve_category_class`, the stock ask's own words): category
    names are copies of their codes on live rows, so "water closet" only meets its
    categories there. `db is None` (a direct `run_fetch` test) resolves nothing."""
    code = jsc.js_string(slot.get("category_code") or "").strip()
    words = [jsc.js_string(w).strip() for w in (slot.get("category_words") or []) if jsc.truthy(w)]
    tokens = [code] if code else words
    if not tokens or db is None:
        return [], bool(tokens), [], []
    ids: list[str] = []
    unknown: list[str] = []
    for token in tokens:
        matched = business_services.resolve_category_token(db, token)
        if len(matched) > 1:
            return [], True, [row[1] for row in matched], []
        found = [row[0] for row in matched]
        if not found and not code:
            found, labels = business_services.resolve_category_class(db, token)
            if not found and len(labels) > 1:
                return [], True, labels, []
        if not found:
            unknown.append(token)
        for category_id in found:
            if category_id not in ids:
                ids.append(category_id)
    return ids, True, [], unknown


def _top_selling_ask_who(who: dict[str, Any]) -> str:
    """Owner retest (27 Sep 2026): a word naming a customer AND a sales agent is asked
    about in one short question; "1" takes the customer, "2" the agent."""
    word = jsc.js_string(who.get("word") or "").strip()
    customer = jsc.js_string(who.get("customer_label") or "").strip()
    customer_part = f"customer {customer}" if customer else f"a customer named '{word}'"
    agent = jsc.js_string(who.get("agent_label") or "").strip() or word
    return (
        f"Do you mean {customer_part} or sales agent {agent}? "
        "Reply 1 for the customer, 2 for the sales agent."
    )


def _top_selling_ask_category(codes: list[str]) -> str:
    """The category question: the codes only (owner, 26 Sep ~07:40Z, code only)."""
    return f"Which category do you mean? Reply with one code: {', '.join(codes)}"


def _fixed_reply(
    text: str, *, top_selling_asked: str | None = None, required_ask: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One fixed line and nothing else, before any fetch: the refusal and the top
    selling questions. `has_result: True` and no `outstanding_ask` keep it OFF the miss
    lane (no escalate offer) and arm nothing; the carried `focus.status` is what makes
    the next message land on the same ask. `outstanding_report` skips the generic
    search-scope header."""
    structured: dict[str, Any] = {
        "response": text,
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        "outstanding_report": True,
        # The top selling question this line asks, if any (`_top_selling_question`'s
        # axis); `engine.py` records it on the slot for the next turn to read.
        "top_selling_asked": top_selling_asked,
        # LOWSTOCK-FILTER-ASK: the `required_fields` slot this line leaves open, if any.
        "required_ask": required_ask,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
    }


def _so_list_reply(text: str) -> dict[str, Any]:
    """`_fixed_reply`, marked as the SO list's answer (`fetch.so_list`): the engine closes an
    open outstanding offer on it, so the period the customer types next answers the list
    rather than narrowing that report (crew-tester re-run on PR #1435, 3 Oct 2026)."""
    fragment = _fixed_reply(text)
    fragment["fetch"]["so_list"] = True
    return fragment


def _sales_report_not_enabled() -> dict[str, Any]:
    """S4 wiring point 4 (AC-1651): refuse a sales-report ask BEFORE any fetch, with
    ONE line and nothing else.

    There is no fallback scope here (unlike D13's SO/DO redirect on the outstanding
    report), so there is nothing partial to answer with - `has_result: True` and no
    `outstanding_ask` (same shape as `_outstanding_offer_closed`'s decline reply)
    keeps this OFF the miss lane entirely: no escalate offer, nothing armed.
    """
    structured: dict[str, Any] = {
        "response": SALES_REPORT_NOT_ENABLED_MESSAGE,
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        # AC-1139/S4 point 9's own marker: this reply carries nothing but itself, so
        # the generic search-scope header must not print above it either.
        "outstanding_report": True,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
    }


def _which_customer_ask() -> dict[str, Any]:
    """#1262 slice 2 (F1c), AC-S2-3: the turn's only customer subject is a stored id
    that never resolved to a real uuid (a kind-pick's printed label, never re-typed) -
    the report must not run over no customer at all ("Customer: all"). Same shape as
    `_sales_report_not_enabled` (`has_result: True`, no `outstanding_ask`): nothing is
    armed, so the miss lane and any escalation offer stay off this reply entirely.
    """
    structured: dict[str, Any] = {
        "response": "Which customer is this outstanding report for?",
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        "outstanding_report": True,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
    }


def _sales_analysis_axis_unsupported(group_by: str) -> dict[str, Any]:
    """A sales analysis asked by an axis it cannot draw (customer, product, ...): ONE line
    naming the axes it can, no figure and no fetch (`_sales_report_not_enabled`'s shape)."""
    structured: dict[str, Any] = {
        "response": (
            f"I can't break sales down by {group_by} yet. "
            "I can show them by month, by year or by channel."
        ),
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        "outstanding_report": True,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
    }


def _outstanding_detail_reoffer(
    filters: dict[str, Any], rows: list[dict[str, Any]], *, kind: str = "outstanding_detail"
) -> dict[str, Any]:
    """AC-1143(c): an out-of-range number against an OPEN detail offer re-prints that
    offer, fetches nothing, and leaves it open - the same `structured` shape (and so the
    same one code path in `tail/compile_state.py`) the scope question's own re-ask uses.

    The option lines are the stored rows themselves, never rebuilt from a scope guess, so
    the customer reads back exactly the list they replied to. `kind` (PLAN-chatbot-
    sales-report.md ruling 2) is whichever `DETAIL_OFFER_KINDS` member was actually
    open - `outstanding_detail` and `sales_report_detail` share this one function."""
    # AC-1102: the SAME text the customer is already looking at, kept verbatim from the
    # turn that offered it (`fetch._outstanding_offer_block`). Re-rendering it here is
    # what produced two wordings for one offer: a single-scope report offers R9's one
    # sentence and the re-print answered with the numbered form. The rebuild below is the
    # fallback for a filter set stored before the text was carried.
    text = jsc.js_string(filters.get("offer_text") or "").strip()
    if not text:
        text = "Reply with a number for detail:\n" + "\n".join(
            f"{jsc.js_string(row.get('idx'))}. {jsc.js_string(row.get('label'))}" for row in rows
        )
    structured: dict[str, Any] = {
        "response": text,
        "outstanding_ask": {
            "kind": kind,
            "last_result_set": [dict(row) for row in rows],
            "filters": filters,
        },
        "answers": [],
        "attachments": [],
        "action_links": [],
        "last_updated_at": None,
        "has_result": True,
        "alternatives": [],
        "relaxed_axis": None,
        "field_access": None,
        "requested_attributes": [],
        "keys_served": False,
        "outstanding_report": True,
    }
    item = fetch_mod.fetch_result(structured, tool=None, tier_probe=None)
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {"fetch": item},
        "fetch": item,
    }


def handles(branch_kind: str | None) -> bool:
    """Does the business lane own this arm?"""
    return branch_kind in ENTRY_BY_BRANCH_KIND


def run_until_exit(
    ctx: dict[str, Any],
    item: dict[str, Any],
    *,
    branch_kind: str,
    services: ResolveGateServices,
    space_id: str | None = None,
    probe_default_start: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run resolve + gate for one turn and return the `{delegate, payload}` fragment.

    `item` is the router's own item, forwarded UNCHANGED.

    `not_allowed_check_stock` is the spine's `Edit Fields2`, which sits on the ONE edge
    `If7`'s TRUE output takes (`If7 -> Edit Fields2 -> If8`) and sets the single boolean
    `validator` reads. S6a left it unstamped because the reader then was n8n's own
    `validator`, which reads `$('Edit Fields2')` by name; at S6c the CRM IS the validator
    and reads the flag off this payload, so the arm that carries it has to carry it here.
    The route already gates the arm: `stock_denied` can only be decided when
    `system_settings.chatbot_stock_denial_enabled` is on (R1), so no second flag read is
    needed - with the switch off no turn reaches this branch at all.
    """
    # AC-1132 out-of-range re-ask: `head/output_exchange.py::_post_process` restores
    # the carried product as a RAW (unresolved) entity, since this turn re-typed no
    # product for the resolver to match against - so resolve+gate's own gate would
    # find nothing compatible and exit `not_found` before `run_fetch` ever saw the
    # flag it actually checks. Skipped here, before that gate runs at all; `run_fetch`
    # (via the ENGINE's normal `_exit_kind == "continue"` path) is where the flag is
    # actually read and the SAME question gets re-armed.
    parse_output_peek = ((ctx.get("parse") or {}).get("output")) or {}
    # R13 (live failure, 13 Sep 2026): an ANSWERING turn whose subject was CARRIED has
    # nothing of its own in its parse - the message is a position, and the subject came
    # off the stored filters already resolved. resolve+gate would find nothing to put in
    # scope and exit with the order lane's "I need at least one filter", which is what the
    # owner read. Same short-circuit the re-ask arms use, for the same reason: there is
    # nothing here to resolve.
    #
    # BOTH subjects, not just the customer (R21, owner round 8, 13 Sep 2026): the head
    # gives an answering turn ONE entity when the stored subject is a product - a
    # synthetic `{raw: <code>, hint: product}` it mints from the carried code itself - and
    # that entity is not a token the resolver should look up. D10 already says so in
    # words: the carried code WINS over any re-resolution, because re-resolving it is how
    # a family sibling took its place. So a turn carrying a subject and naming nothing
    # BEYOND it skips resolve+gate whichever kind of subject it is; before R21 the product
    # half still went through, and a scope answer after an all-pick died at the gate.
    carried_code = jsc.js_string(parse_output_peek.get("outstanding_carried_product_code") or "")
    carried_subject = bool(
        jsc.array(parse_output_peek.get("outstanding_carried_customer_ids"))
    ) or bool(carried_code)
    names_only_the_carried_subject = all(
        jsc.js_string(jsc.get(e, "canonical_code") or "") == carried_code and bool(carried_code)
        for e in jsc.array(parse_output_peek.get("entities"))
        if jsc.truthy(e)
    )
    carried_subject_answer = carried_subject and names_only_the_carried_subject

    # R-S3 (reviewer finding, Phase 3 fix round, item 7): an ungranted contact's
    # sales-report ask is refused HERE, before resolve+gate ever runs - `order_status`
    # and `ctx.access` are both already known at this entry, the earliest single seam
    # that has both. Skipping resolve+gate means the ambiguous-customer PICKER never
    # renders for a contact who could not read the answer anyway (it is an interactive,
    # multi-choice prompt that names real customer matches - showing it first and
    # refusing only once the customer picks leaks that enumeration for nothing).
    # `run_fetch`'s own `_SALES_REPORT_GRANT` check stays as the second line of
    # defence, for a re-entry path that calls it directly (this module's own tests).
    access_ctx_peek = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
    granted_raw_peek = access_ctx_peek.get("attributes")
    granted_peek = (
        set(granted_raw_peek) if isinstance(granted_raw_peek, (list, tuple, set, frozenset)) else set()
    )
    sales_report_ungranted = (
        jsc.js_string(parse_output_peek.get("order_status") or "") == "sales_report"
        and _SALES_REPORT_GRANT not in granted_peek
    )
    # PLAN-retail-sales-reports-26sep S1: a sales ANALYSIS ask names no product or
    # customer to resolve (a company, a channel, a period), so resolve+gate has nothing to
    # do and would exit `not_found`; `run_fetch` gates and answers it.
    sales_analysis_ask = (
        jsc.js_string(parse_output_peek.get("order_status") or "") == "sales_analysis"
    )
    if (
        isinstance(parse_output_peek.get("outstanding_reask_filters"), dict)
        or isinstance(parse_output_peek.get("outstanding_detail_reask"), dict)
        # R22(a): a decline names nothing at all, so resolve+gate would exit `not_found`
        # and answer with the order lane's miss text instead of the acknowledgement.
        or jsc.truthy(parse_output_peek.get("outstanding_offer_declined"))
        or carried_subject_answer
        or sales_report_ungranted
        or sales_analysis_ask
    ):
        return {
            "delegate": DELEGATE,
            "payload": {"gate": {}, "tier_gate": None, "ctx": ctx, "_exit_kind": "continue"},
        }

    entry = ENTRY_BY_BRANCH_KIND[branch_kind]
    payload = resolve_gate.run(
        ctx,
        entry,
        item,
        services=services,
        space_id=space_id,
        probe_default_start=probe_default_start,
        dry_run=dry_run,
    )
    if branch_kind == "stock_denied":
        payload["not_allowed_check_stock"] = True
    return {"delegate": DELEGATE, "payload": payload}


def _fetch_semantic_input(
    parse_output: dict[str, Any],
    *,
    tier_gate: dict[str, Any] | None,
    contact_id: Any,
    space_id: str | None,
) -> dict[str, Any]:
    """`Call 'sub-get-results'`'s `semantic_input`, all thirteen fields plus growth r1's two.

    It was `{}`, and that was not a small omission: `access_levels`, `is_active`,
    `date_mode`, `order_status`, `requested_attributes` and both date filters all reach the
    MCP tool through this object, so an empty one silently widened every read and left the
    timeline and per-field denial paths dead.

    `access_levels` comes from `tier-gate.access_levels_recomposed` when the tier gate ran
    - the tier x brand recomposition is computed ONCE, there - and from the parser's own
    list otherwise, which is the legacy behaviour off the promotion lane.
    """
    tg = tier_gate if isinstance(tier_gate, dict) else None
    if tg is not None:
        access_levels = tg.get("access_levels_recomposed")
    else:
        access_levels = (
            parse_output.get("access_levels")
            if isinstance(parse_output.get("access_levels"), list)
            else []
        )
    return {
        "message_type": parse_output.get("message_type"),
        "intent_hint": parse_output.get("intent_hint"),
        "domain_hint": parse_output.get("domain_hint"),
        "user_goal": parse_output.get("user_goal"),
        "access_levels": access_levels,
        "contact_id": jsc.js_string(contact_id) if contact_id is not None else None,
        "space_id": fetch_mod.space_id_or_default(space_id),
        "date_mode": parse_output.get("date_mode"),
        "date_filter_start": parse_output.get("date_filter_start"),
        "date_filter_end": parse_output.get("date_filter_end"),
        "is_active": parse_output.get("is_active"),
        "order_status": parse_output.get("order_status"),
        "requested_attributes": (
            parse_output.get("requested_attributes")
            if parse_output.get("requested_attributes") is not None
            else []
        ),
        # Growth r1 (AC-909 / AC-910). Fifteen fields now, and these two are the reason the
        # docstring above says an empty object is not a small omission: `entity_ids_
        # transformer` reads `group_by` and `top_n` off THIS object, so without them the
        # transformer's grouping and top-n arms were unreachable from a live turn no matter
        # what the parser emitted. `.get` reads a pre-growth-r1 emission (which carries
        # neither key, see `output_exchange._EXEMPT_FROM_REQUIRED`) as null, and the
        # transformer omits a null rather than sending one.
        "group_by": parse_output.get("group_by"),
        "top_n": parse_output.get("top_n"),
        # AC-1132: the outstanding-scope answer's carried customer_ids (ALREADY
        # resolved UUIDs, not a raw token to re-resolve) - `entity_ids_transformer`
        # restores them onto `crm_outstanding_report`'s own args directly.
        "outstanding_carried_customer_ids": parse_output.get("outstanding_carried_customer_ids"),
        # AC-1138: "1"/"2" against an OPEN outstanding_detail offer - which detail list
        # to render, passed straight through to the MCP tool's own `detail` param.
        "outstanding_detail_pick": parse_output.get("outstanding_detail_pick"),
        # PLAN-chatbot-sales-report.md S4 wiring point 5 (AC-1652): the parser's OWN
        # channel value, never the message text - passed straight through to
        # `crm_sales_report`'s own `channel` param.
        "sales_channel": parse_output.get("sales_channel"),
        # PLAN-retail-sales-reports-26sep S1: the sales analysis's basis (ordered |
        # delivered) and the company the contact named, straight to the tool's params.
        "sales_basis": parse_output.get("sales_basis"),
        "sales_company": parse_output.get("sales_company"),
        # CHATBOT-SELFREF-SCOPE (owner rule Q6a): "my" / "me" / "our" means ALL the
        # asker's linked accounts. Read by `entity_ids_transformer`'s two report arms
        # (a carried account never narrows a self-reference ask) and by
        # `_sales_report_output` (a miss on the asker's own accounts is a final answer).
        "self_reference": parse_output.get("self_reference") is True,
        # R-B3 (reviewer finding, Phase 3 fix round): the sales_report_detail
        # offer's stored channel, restored by `head/output_exchange.py::
        # _apply_outstanding_pending` - `fetch.py` reads this ONLY when THIS
        # turn's own `sales_channel` above is absent (the turn's own value wins).
        "outstanding_carried_channel": parse_output.get("outstanding_carried_channel"),
        # S18 (owner ruling, mid-lane): `fetch.py`'s crm_sales_report arg builder
        # reads this to tell "no date window at all" apart from "the customer said
        # 'all dates'" - `broaden_axis` lives on the full parser output, not this
        # object's other twelve fields, so it has to be named explicitly here too.
        "broaden_axis": parse_output.get("broaden_axis"),
        # PLAN-chatbot-top-x-hot-selling-24sep.md S4 point 8: the top selling ask's own
        # axes (`turn_runtime.lane_parse_output` projects them off the focus).
        "top_selling": parse_output.get("top_selling"),
        # PLAN-po-spo-warehouse-29sep S6: the PO/SPO sort axis (the parser's own, or the
        # focus's carried one projected by `turn_runtime.lane_parse_output`). Named here
        # for the same reason `group_by`/`top_n` are: `entity_ids_transformer` reads
        # them off THIS object (reviewer blocker 1, PR #1373: without them the sort never
        # reached a tool on a live turn).
        "sort_by": parse_output.get("sort_by"),
        "sort_dir": parse_output.get("sort_dir"),
        # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner
        # ruling 24 Sep 2026) for chatbot-stock-ask-v2 S3, D13/D20:
        # `{product uuid: quantity}`, built by `turn_runtime._spec_quantities` from
        # the open task's slots or from this message's own per-entity quantities.
        # Named explicitly for the same reason `group_by`/`top_n` are:
        # `entity_ids_transformer` reads it off THIS object, so without a field here
        # the map could never reach the tool.
        "requested_quantities": parse_output.get("requested_quantities"),
    }


def _error_fragment(reason: str, *, outcome: str | None = None) -> dict[str, Any]:
    """`fetch-result`'s `error` arm, as the fragment the engine records a failure from.

    `outcome` separates the two things this arm carries, and the separation is
    load-bearing: `not_found` is a GENUINE ABSENCE (H11's zero-tool case - the question
    was understood and nothing matches it), while an absent `outcome` is an
    INFRASTRUCTURE failure (the MCP call raised, or the tool returned an error envelope).
    Only the first may be told to the customer as "I could not find anything". It rides
    on the ITEM as well as the fragment because `complete_answer` receives the item, not
    the fragment.
    """
    item = fetch_mod.fetch_result(
        {"error": reason, **({"outcome": outcome} if outcome is not None else {})}
    )
    fragment: dict[str, Any] = {
        "kind": "error",
        "_fetch_arm": item["_fetch_arm"],
        "error": reason,
        "fetch": item,
    }
    if outcome is not None:
        fragment["outcome"] = outcome
    return fragment


#: D9 (owner console pass, 8 Sep 2026, turn 8f4356a3): a document tool that does not answer
#: within the lane's tool budget is an ABSENCE the customer can act on (the miss lane, with
#: its escalate offer), never the generic error reply. Only these tools, and only on a
#: timeout: a stock or order read that fails stays an infrastructure failure, because
#: telling a customer "no stock" off a dead read would assert an absence nobody measured.
ATTACHMENT_TOOLS: frozenset[str] = frozenset(
    {
        "crm_master_product_attachments_list",
        "crm_certificates_list",
        "crm_marketing_promotion_attachments_list",
        "crm_resource_attachments_list",
        "crm_resource_attachments_catalogue",
        "crm_resource_attachments_current_stock_list",
    }
)


def _fetch_failure_outcome(tool_name: Any, exc: BaseException) -> str | None:
    """`"not_found"` for a document tool that timed out (the miss lane answers it, with the
    escalate offer); None for every other failure - today's hard failure, unchanged."""
    if jsc.js_string(tool_name) not in ATTACHMENT_TOOLS:
        return None
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return "not_found"
    return None


def _refinement_product_resolves(db: Any, token: str) -> bool:
    """Whether a refinement's own raw product-hint TOKEN resolves to a REAL
    product (R-S4, Phase 3 fix round) - through the SAME in-process resolver
    `resolve_gate` calls for a typed entity (`business_services.production_
    services(db).resolve_entity`, the route behind `POST /api/v1/system/
    references/resolve`), for this ONE token alone. The gate itself is skipped
    on a refinement turn (see `_resolve_report_product_and_location`'s own
    docstring), so there is no OTHER live resolution this seam can read off
    `entities` - re-calling the resolver directly is the only way to tell a
    real code ("SRTWT7445") from a word that merely LOOKS like a refinement
    ("cheaper") apart, without guessing from its shape.

    `db is None` (this module's own direct `run_fetch` tests, per the sibling
    docstring) resolves nothing, the same no-op the warehouse loop below has
    for the identical reason."""
    if db is None or not token:
        return False
    resolve_entity = business_services.production_services(db).resolve_entity
    body = {
        "query": token,
        "tokens": [token],
        "match_mode": "or",
        "allowed_entity_types": ["product"],
        "limit": 1,
    }
    try:
        result = resolve_entity(body)
    except Exception:  # noqa: BLE001 - an unresolvable token is a miss, not a hard failure
        logger.warning(
            "sales/outstanding report: refinement product resolve failed for %r",
            token, exc_info=True,
        )
        return False
    resolutions = jsc.array(jsc.get(result, "resolutions"))
    matches = jsc.array(jsc.get(resolutions[0], "matches")) if resolutions else []
    return any(
        jsc.truthy(m) and jsc.js_string(jsc.get(m, "entity_type")).lower() == "product"
        for m in matches
    )


def typed_brand_words(parse_output: dict[str, Any]) -> set[str]:
    """The brand-hinted words this turn (or a refinement) typed, listed or not."""
    words = {
        jsc.js_string(e.get("raw") or e.get("canonical_code") or "").strip()
        for e in [
            *jsc.array(parse_output.get("entities")),
            *jsc.array(parse_output.get("outstanding_refinement_entities")),
        ]
        if isinstance(e, dict) and jsc.js_string(e.get("hint") or "").strip().lower() == "brand"
    }
    words.discard("")
    return words


def _resolve_outstanding_brand_ids(
    parse_output: dict[str, Any],
    semantic_input: dict[str, Any],
    *,
    db: Any,
) -> None:
    """#1262 slice 9 (F1a), round 3 section 6 step 4: the brand word(s) this turn (or
    a refinement) named, resolved to the live brand's OWN ids - the SAME live read
    `turn_runtime.active_brands` uses (case-insensitive exact on name or code, the
    one place "which brand does this word mean" is answered). `db` is None outside
    a real turn (this module's own direct `run_fetch` tests), same no-op every
    other db-gated read here already has. The token never reached the shared
    resolver at all (`turn_runtime.resolve_kinds`'s own pre-resolver intercept), so
    this is the FIRST and only place it becomes an id.

    Review round (26 Sep 2026), SF4: split out of `_resolve_report_product_and_
    location` and called EARLY in `run_fetch`, before the order-outstanding subject
    gate reads `has_brand` - a brand-hinted entity naming a word that matches NO
    live brand (an unlisted brand) must not count as a subject at all, and the only
    way to tell "named a real brand" from "named a brand-shaped word" apart is to
    actually resolve it here first.
    """
    if semantic_input.get("outstanding_brand_ids"):
        return
    brand_tokens = typed_brand_words(parse_output)
    if brand_tokens and db is not None:
        from app.services.chatbot.turn_runtime import active_brands, brand_entity_word, brand_rows_for_word

        # #1262 fix lane round 7, R1: the brand is read off the TYPED word alone
        # (`brand_rows_for_word`: exact, whole word, else the nearest brand by
        # spelling), never off the parser's `canonical_code`, which for a misspelt word
        # is the model's guess and was "Mocha" on one run and "Sorento" on the next.
        live = active_brands(db)
        brand_ids: list[str] = []
        for e in [
            *jsc.array(parse_output.get("entities")),
            *jsc.array(parse_output.get("outstanding_refinement_entities")),
        ]:
            if not isinstance(e, dict) or jsc.js_string(e.get("hint") or "").strip().lower() != "brand":
                continue
            for row in brand_rows_for_word(live, brand_entity_word(e)):
                if row["id"] not in brand_ids:
                    brand_ids.append(row["id"])
        if brand_ids:
            semantic_input["outstanding_brand_ids"] = brand_ids
    if not brand_tokens and not semantic_input.get("outstanding_brand_ids"):
        # #1262 fix lane round 4, S6: only a turn that typed NO brand word rides the
        # carried brand. A typed brand that matched no live row is still "another
        # brand" (plan ruling 13): it ends the carry, and is said back as unplaced.
        # R13/R15/D10, the same fallback the customer-id carry above and
        # `_outstanding_filters_from`'s own docstring make: an ANSWERING turn (a scope
        # pick, an out-of-range re-ask, a refinement) typed no brand word of its own -
        # the ids rode in on the carried filter set, already resolved, and have to ride
        # back out on it too or the re-run silently drops the very brand the question
        # was scoped to.
        # N1 (security review, 26 Sep 2026): a carried brand id is a resolved uuid or
        # it is nothing - the same `is_uuid` guard the customer-id carry above uses,
        # never a bare truthiness check that would let a non-uuid string through.
        carried_brand_ids = [
            b for b in jsc.array(parse_output.get("outstanding_carried_brand_ids")) if fetch_mod.is_uuid(b)
        ]
        if carried_brand_ids:
            semantic_input["outstanding_brand_ids"] = carried_brand_ids


def _resolve_report_product_and_location(
    parse_output: dict[str, Any],
    entities: Any,
    semantic_input: dict[str, Any],
    *,
    db: Any,
) -> None:
    """The product-code and location resolution BOTH report overrides need
    (`crm_outstanding_report` and `crm_sales_report`), fixed ONCE at this seam for
    both (captain ruling 3, PLAN-chatbot-sales-report.md): mutates `semantic_input`
    in place with `outstanding_product_code` / `outstanding_warehouse_codes` /
    `outstanding_location_token`.

    -- AC-1119 (reviewer N5): the code the customer TYPED is the subject -- The gate
    hands a report the whole prefix FAMILY: a single product token takes the
    resolver's OR-mode, which calls a candidate exact only when `match_tier ==
    "exact"` - a tier `entity_resolver._prefix_probe_product` never stamps (product
    rows come back "prefix" / "substring" / "trgm"). Picked here rather than in the
    shared gate: this is the report's own contract (exact code, no sibling
    expansion). D10: an ANSWERING turn ("1"/"2"/a scope word) carries the product
    the offer was made about, and that wins outright - re-resolving the carried
    token is what let a family sibling take its place.

    R17/ruling 3: a REFINEMENT's own product entity lives OFF `parse_output
    ["entities"]` (R17 keeps it there so it dies with the offer), and on a
    refinement turn the gate itself is SKIPPED (`run_until_exit`'s carried-subject
    short-circuit), so the GATE-resolved `entities` list is always empty there and
    the typed-codes match below can never fire. Read directly off the offer-scoped
    `outstanding_refinement_entities` copy instead - the same seam the warehouse
    loop below already reads for a location word, and for the same reason: this
    report matches its subject EXACTLY (AC-1119), so no gate resolution is needed.

    R-S4 (Phase 3 fix round): a refinement entity is used ONLY if it actually
    RESOLVES to a real product (`_refinement_product_resolves`) - unlike the
    warehouse loop, which reads a token against a closed, small vocabulary
    (`resolve_warehouse_token`'s own `warehouses` query), a product-hint word the
    resolver cannot match ("cheaper") used to be sent straight through as
    `product_code=cheaper`, silently dropping the customer's actual (carried)
    subject in the tool call instead of leaving it alone.

    -- S4c (D5, AC-1133 pipeline half): the location word, before any fetch -- Read
    off the RAW parsed entities (`parse_output`), never the gated `entities` above -
    those only ever carry what the GENERIC resolver matched, and D5's
    exact-then-suffix grammar is this report's own rule, not that resolver's job (a
    suffix token like "IB" is not any single warehouse's own code, so the generic
    resolver never returns it). `db` is None outside a real turn (this module's own
    direct `run_fetch` tests), which is a no-op, same as no location word at all.
    """
    carried_codes = [
        jsc.js_string(c)
        for c in jsc.array(parse_output.get("outstanding_carried_product_codes"))
        if jsc.truthy(c)
    ]
    carried_code = parse_output.get("outstanding_carried_product_code")
    if carried_codes:
        # The SEVERAL-code form of the same carry: `outstanding_product_codes` reads
        # this list, so the answering turn re-runs for every code the question named.
        semantic_input["outstanding_product_codes"] = carried_codes
    elif jsc.truthy(carried_code):
        semantic_input["outstanding_product_code"] = jsc.js_string(carried_code)
    else:
        typed_codes = {
            jsc.js_string(e.get("raw") or "").strip().casefold()
            for e in jsc.array(parse_output.get("entities"))
            if isinstance(e, dict) and jsc.js_string(e.get("hint") or "") == "product"
        }
        typed_codes.discard("")
        for e in jsc.array(entities):
            if not isinstance(e, dict) or e.get("entity_type") != "product":
                continue
            code = e.get("code") or e.get("canonical_code")
            if jsc.truthy(code) and jsc.js_string(code).strip().casefold() in typed_codes:
                semantic_input["outstanding_product_code"] = jsc.js_string(code)
                break

        if not jsc.truthy(semantic_input.get("outstanding_product_code")):
            for e in jsc.array(parse_output.get("outstanding_refinement_entities")):
                if not isinstance(e, dict) or jsc.js_string(e.get("hint") or "") != "product":
                    continue
                token = jsc.js_string(e.get("raw") or e.get("canonical_code") or "").strip()
                if token and _refinement_product_resolves(db, token):
                    semantic_input["outstanding_product_code"] = token
                break

    for e in [
        *jsc.array(parse_output.get("entities")),
        *jsc.array(parse_output.get("outstanding_refinement_entities")),
    ]:
        if not isinstance(e, dict) or jsc.js_string(e.get("hint") or "") != "warehouse":
            continue
        token = jsc.js_string(e.get("raw") or e.get("canonical_code") or "").strip()
        if not token or db is None:
            break
        codes = resolve_warehouse_token(db, token)
        if codes:
            semantic_input["outstanding_warehouse_codes"] = codes
            semantic_input["outstanding_location_token"] = token
        break  # D5/AC-1105: one location word per turn

    # AC-1132/AC-1138: the SCOPE-ANSWER and DETAIL-PICK turns re-type no location
    # word at all (they are "2" / "1"), so the loop above finds nothing. The codes
    # the asking turn already resolved are restored here - the alternative is a
    # re-run over every warehouse under a header that says otherwise.
    if not semantic_input.get("outstanding_warehouse_codes"):
        carried_wh_codes = parse_output.get("outstanding_carried_warehouse_codes")
        carried_token = jsc.js_string(
            parse_output.get("outstanding_carried_location_token") or ""
        ).strip()
        if (
            not (isinstance(carried_wh_codes, list) and carried_wh_codes)
            and carried_token
            and db is not None
        ):
            # The focus carries the WORD the customer said; a focus written by a turn
            # that predates the codes riding along with it (or by any build that only
            # ever kept the word) still has to answer for the same warehouses, and a
            # location word resolves the same way on every turn - unlike a product
            # code, which D10 forbids re-resolving because a family sibling can take
            # its place.
            carried_wh_codes = resolve_warehouse_token(db, carried_token)
        if isinstance(carried_wh_codes, list) and carried_wh_codes:
            semantic_input["outstanding_warehouse_codes"] = carried_wh_codes
            semantic_input["outstanding_location_token"] = carried_token or None

    _resolve_outstanding_brand_ids(parse_output, semantic_input, db=db)


def run_fetch(
    payload: dict[str, Any],
    *,
    services: FetchServices,
    dry_run: bool = False,
    space_id: str | None = None,
    trace: Any = None,
    db: Any = None,
) -> dict[str, Any]:
    """S6b: the fetch step, the next call site after `run_until_exit`'s `continue` exit.

    `payload` is the resolve+gate output item - what `sub-fetch-results` receives as
    `ctx_resolved` + `tier_gate` today. The return is a FRAGMENT in the same shape
    `run_until_exit` produces, so the engine keeps one call per lane stage.

    Three arms, from `fetch-result`'s own three:

    * `tier_ask` - the customer must pick an access tier before anything is fetched, so
      the per-tier probe plan runs and its answers are folded back in. It must NOT fall
      through to an ordinary result delegate while a tier is unresolved; S6c renders the
      copy from `tier_probe`.
    * `error` - the tool returned an error item, or the call itself failed. n8n carries
      `onError: continueErrorOutput` on that node for exactly this reason: a transient MCP
      failure is an ANSWERABLE outcome, not a dead turn.
    * `result` - S6c is not built, so the turn still delegates to n8n's business lane, and
      the fetch's own output rides on `delegate_payload` so the next slice has something to
      answer from without re-fetching.

    D14: `dry_run` performs the SAME reads. A test turn that skipped the fetch would prove
    nothing about production, and this lane writes nothing either way - the only write on
    the whole turn is `chatbot.turns`, which the engine owns. The parameter is taken (and
    unused) so the engine's call site reads the same as every other lane's.

    `trace` (A9, chatbot-growth-r1): the turn's live `TurnTrace`, optional - when given,
    "the read" below records one `tool` event (`name`, `args`, the envelope, `ms`) via
    `trace.add`. `None` is a no-op, so every existing caller (and every world/replay test)
    is unaffected by omitting it.

    `db` (S4c, PLAN-chatbot-outstanding-report.md): the live Session, optional - only
    `crm_outstanding_report`'s own location resolution (D5, AC-1133) reads it, through
    `services.resolve_warehouse_token`. `None` is a no-op there too (a direct `run_fetch`
    call, this module's own tests), same as no location word at all.
    """
    # One exception to the paragraph above: `crm_low_stock_report` WRITES (a run, a file,
    # a push to WhatsApp), so a dry run is told to the tool, which then never pushes
    # (LOWSTOCK-FILTER-ASK tester finding: a console turn sent the real workbook).
    raw_gate = payload.get("gate")
    gate: dict[str, Any] = raw_gate if isinstance(raw_gate, dict) else {}
    raw_tier_gate = payload.get("tier_gate")
    tier_gate: dict[str, Any] | None = raw_tier_gate if isinstance(raw_tier_gate, dict) else None

    ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else {}
    parse_output = ((ctx.get("parse") or {}).get("output")) or {}
    contact_id = (ctx.get("contact") or {}).get("id")
    entities = gate.get("compatible_entities") or []
    semantic_input = _fetch_semantic_input(
        parse_output, tier_gate=tier_gate, contact_id=contact_id, space_id=space_id
    )
    # PLAN-chatbot-customer-scope-29sep.md D3/D4: a customer-scoped contact's turn
    # (`engine._customer_scope_gate`). The engine refuses a customer word outside its links (one fixed line, before any
    # fetch); here the links ride to `entity_ids_transformer`, which forces them on every customer-scoped tool.
    customer_scope = ctx.get("customer_scope") if isinstance(ctx.get("customer_scope"), dict) else {}
    scoped_to_links = bool(customer_scope.get("enforced") and customer_scope.get("ids"))
    if scoped_to_links:
        semantic_input["scope_customer_ids"] = list(customer_scope["ids"])
    # CHATBOT-SELFREF-SCOPE (owner hand test, 30 Sep 2026, "Mr Loo"): a contact WITH linked
    # accounts asking for sales is asking about those accounts, staff tier or not - the
    # company analysis route refuses any linked contact outright (`analysis.py:253-258`,
    # AC-S1-23), so sending a linked staff contact there answers "Sorry, I can only share
    # sales figures for your own account" to the very person whose own accounts exist.
    # Links win: the customer sales report over them. Staff without links keep the analysis.
    has_own_accounts = bool(customer_scope.get("ids"))
    # SO-NUMBER-ASK (PLAN-so-number-ask.md): SO numbers the resolver could not place
    # (`turn_runtime` hands them over as `so_numbers`) are answered from `sales_orders`
    # here, before any tool pick: the orders list reads the DO book and answered an SO ask
    # with the links' DOs. Gated by the SO reveal key (owner ruling Q5), refused outside
    # the links (Q2), one card per SO (Q4).
    so_numbers = [w for w in jsc.array(parse_output.get("so_numbers")) if isinstance(w, str) and w]
    if so_numbers and parse_output.get("domain_hint") == "order":
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, (list, tuple, set, frozenset)) else set()
        if _OUTSTANDING_SO_GRANT not in granted:
            if trace is not None:
                trace.add("so_status", {"refused": "not_granted", "needs": _OUTSTANDING_SO_GRANT})
            return _fixed_reply(fetch_mod.SO_NOT_ENABLED_MESSAGE)
        if db is not None:
            from app.services.chatbot import so_status

            linked = list(customer_scope.get("linked") or []) if customer_scope.get("enforced") else None
            looked = so_status.lookup(db, so_numbers, linked=linked)
            if trace is not None:
                trace.add(
                    "so_status",
                    {
                        "asked": so_numbers,
                        "cards": len(looked.cards),
                        "refused": looked.refused,
                        "missing": looked.missing,
                    },
                )
            fragment = _fixed_reply(
                so_status.reply_text(looked, refusal=str(customer_scope.get("refusal") or ""))
            )
            # The reply names its own missing numbers, so the resolver's own `not_found`
            # exit (nothing placed the SO words) is not a second miss to compose
            # (`answer_bridge._miss_triggers`).
            fragment["fetch"]["so_status"] = True
            return fragment
    # SO-NUMBER-ASK, the SO list ("all my sales orders"): the customers in scope (the
    # links on a scoped or "my" turn, a resolved customer otherwise), behind the same SO
    # reveal key (Q4), over a period of at most 31 days asked like the DO list's (Q1).
    customer_ids = [
        str(e.get("uuid"))
        for e in jsc.array(entities)
        if isinstance(e, dict) and e.get("entity_type") == "customer" and fetch_mod.is_uuid(e.get("uuid"))
    ]
    if parse_output.get("so_list") is True and not customer_ids:
        # The "Sales order list" pick off an outstanding summary: its customers ride on the
        # answered offer's carry (`turn_runtime.outstanding_carry`), never re-typed. A
        # scoped contact's carry is held to its links all the same.
        customer_ids = [
            u for u in jsc.array(parse_output.get("outstanding_carried_customer_ids")) if fetch_mod.is_uuid(u)
        ]
        if scoped_to_links:
            own = {str(i) for i in customer_scope["ids"]}
            customer_ids = [u for u in customer_ids if str(u) in own] or list(customer_scope["ids"])
    if parse_output.get("so_list") is True and parse_output.get("domain_hint") == "order" and customer_ids and db is not None:
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, (list, tuple, set, frozenset)) else set()
        if _OUTSTANDING_SO_GRANT not in granted:
            if trace is not None:
                trace.add("so_list", {"refused": "not_granted", "needs": _OUTSTANDING_SO_GRANT})
            return _fixed_reply(fetch_mod.SO_NOT_ENABLED_MESSAGE)
        from app.services.chatbot import so_status
        from app.services.ledger_family import family_words

        names_by_id = so_status.customer_names(db, customer_ids)
        start = do_ask._parse(parse_output.get("date_filter_start"))
        end = do_ask._parse(parse_output.get("date_filter_end"))
        asked = so_status.period_reply(start, end, family_words(list(names_by_id.values())))
        if asked is not None:
            if trace is not None:
                trace.add("so_list", {"period_asked": True, "customers": len(names_by_id)})
            return _so_list_reply(asked)
        end = end or do_ask.today_myt()
        start, end = (start, end) if start <= end else (end, start)
        if trace is not None:
            trace.add("so_list", {"from": start.isoformat(), "to": end.isoformat(), "customers": len(names_by_id)})
        return _so_list_reply(so_status.list_text(db, names_by_id, start, end))
    # #1262 fix lane round 3, B1-r2: an order turn's brand ids are resolved ONCE, by
    # `turn_runtime.order_brand_filter` in the tool runner (typed words first, else the
    # brand the conversation carries), and the header names the same ids. Taken as is,
    # so `_resolve_outstanding_brand_ids` below never makes a second, separate answer.
    pre_resolved_brand_ids = [
        b for b in jsc.array(parse_output.get("outstanding_brand_ids")) if fetch_mod.is_uuid(b)
    ]
    if pre_resolved_brand_ids:
        semantic_input["outstanding_brand_ids"] = pre_resolved_brand_ids

    def probe(tool: str, probe_entities: Any, probe_levels: Any) -> Any:
        """One `sub-get-results` call: build the args the same way, then the same seam."""
        trigger = {
            "tool": tool,
            "entities": probe_entities,
            "semantic_input": {**semantic_input, "access_levels": probe_levels},
            "contact_id": contact_id,
        }
        args = fetch_mod.entity_ids_transformer(trigger, space_id=space_id)
        return fetch_mod.parse_mcp_content(
            fetch_mod.call_tool(tool, args, mcp=_McpSeam(services.mcp_call))
        )

    # ── AC-1132 out-of-range: re-ask the SAME outstanding_scope question ──────
    # `head/output_exchange.py::_post_process` stamps this when the previous turn's
    # `outstanding_scope` ask was answered with a number that named no option. It
    # carries the ALREADY-KNOWN filters directly, independent of `entities`/gate
    # resolution (this turn re-typed no product at all, so there is nothing there to
    # resolve) - the same reason the tier-ask arm below short-circuits before any
    # tool pick.
    # ── AC-1143(c): re-print the SAME detail offer, fetch nothing ─────────────
    # ── R22(a): the customer left the question - acknowledge, arm nothing ────
    if jsc.truthy(parse_output.get("outstanding_offer_declined")):
        return _outstanding_offer_closed(parse_output, db)

    detail_reask = parse_output.get("outstanding_detail_reask")
    if isinstance(detail_reask, dict):
        reoffer_rows = [r for r in jsc.array(detail_reask.get("rows")) if isinstance(r, dict)]
        if reoffer_rows:
            # PLAN-chatbot-sales-report.md ruling 2: absent (a marker written before
            # this shipped) reads as `outstanding_detail`, so a session open at
            # deploy keeps re-printing the kind it always did.
            reask_kind = detail_reask.get("kind") or "outstanding_detail"
            return _outstanding_detail_reoffer(
                detail_reask.get("filters") or {}, reoffer_rows, kind=reask_kind
            )

    reask_filters = parse_output.get("outstanding_reask_filters")
    if isinstance(reask_filters, dict):
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, (list, tuple, set, frozenset)) else set()
        if _OUTSTANDING_SO_GRANT in granted:
            return _outstanding_scope_ask_from_filters(reask_filters, db=db)

    # ── the tier ask, with its per-tier probe ────────────────────────────────
    # `if-tier-ask` sits UPSTREAM of the rag call in n8n: there is nothing to fetch until
    # the customer has said which tier they mean. The probe plan is what makes the ask
    # honest ("Dealer - has promotion"), so it runs here rather than being skipped.
    if payload.get("tier_ask") is True or (tier_gate or {}).get("tier_ask") is True:
        plan_items = [i["json"] for i in fetch_mod.tier_probe_plan(tier_gate or payload)]
        probe_results: list[Any] = []
        for plan_item in plan_items:
            if plan_item.get("probe_skipped") is True:
                probe_results.append({})
                continue
            try:
                probe_results.append(
                    probe(
                        fetch_mod.TIER_PROBE_TOOL,
                        entities,
                        plan_item.get("probe_access_levels") or [],
                    )
                )
            except fetch_mod.ScopeViolation as violation:
                # The same refusal as the main call below; no tool was called.
                if trace is not None:
                    trace.add(
                        "customer_scope",
                        {
                            "refused": "customer_not_permitted",
                            "reason": "tier_probe_customer_ids_outside_links",
                            "tool": fetch_mod.TIER_PROBE_TOOL,
                            "dropped": violation.dropped,
                        },
                    )
                return _fixed_reply(str(customer_scope.get("refusal") or ""))
            except Exception:  # noqa: BLE001 - an unprobed tier is "unknown", never "none"
                logger.warning("chatbot: tier probe did not run", exc_info=True)
                probe_results.append(None)
        collected = fetch_mod.tier_probe_collect(
            tier_gate or payload, plan_items=plan_items, probe_results=probe_results
        )
        item = fetch_mod.fetch_result({**payload, **collected})
        return {
            "kind": "tier_ask",
            "_fetch_arm": item["_fetch_arm"],
            "tier_probe": collected,
            "fetch": item,
        }

    # ── tool selection ───────────────────────────────────────────────────────
    # ONE candidate, read off the domain row - no embedding call, no database read, so
    # nothing here can fail and there is nothing to catch. Measured over the 740 business
    # turns in the 7 Sep 2026 prod copy, the vector search this replaced chose the domain's
    # first-listed tool on every one of them.
    domain = parse_output.get("domain_hint") or (tier_gate or {}).get("tier_pick_domain")

    # -- whole-domain grant gate (D6, PLAN-chatbot-last-purchase-cost.md) --------
    # A domain in `DOMAIN_GRANT_REQUIRED` is refused OUTRIGHT without the grant, before
    # any tool is picked or called: a PO row with its cost stripped is a different
    # answer from the one asked, so the per-field drop `output_structurer` already does
    # is not enough on its own (belt and braces, AC-18). Checked here, not in the
    # per-field drop, because "the tool ran and came back empty" and "the tool never ran"
    # read differently on the trace an operator sees.
    from app.services.chatbot.lanes.business import answer as answer_mod

    need = answer_mod.DOMAIN_GRANT_REQUIRED.get(jsc.js_string(domain) if domain else "")
    if need:
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, list) else set()
        if need not in granted:
            if trace is not None:
                trace.add("domain_grant", {"domain": domain, "skipped": "not_granted", "needs": need})
            return _error_fragment(
                f"{domain} needs the {need} grant, which this contact does not hold",
                outcome="access_denied",
            )

    candidates = fetch_mod.select_tool(domain)

    has_product = (
        any(isinstance(e, dict) and e.get("entity_type") == "product" for e in entities)
        if isinstance(entities, list)
        else None
    )
    pick = fetch_mod.tool_filter(candidates, has_product=has_product)
    if pick.items:
        # HOW the tool was chosen, on the trace an operator reads. Stamped here rather than
        # inside `tool_filter`, which is a ported node graded against 38 captures.
        pick.items[0]["json"].setdefault("_tool_pick", {})["source"] = "domain_spec"
    if pick.outcome == "not_found":
        # H11: zero tools is an OUTCOME, not an empty turn. The engine gets something to
        # say rather than a fragment that looks like a lane which never ran.
        return _error_fragment("no MCP tool matched this question", outcome="not_found")

    # ── the read ─────────────────────────────────────────────────────────────
    tool_item = pick.items[0]["json"]
    tool_name = jsc.js_string(tool_item.get("name") or "")

    # S4 point 2 (PLAN-chatbot-outstanding-report.md), REWRITTEN by R13 (owner ruling,
    # 13 Sep 2026): domain "order" + an outstanding `order_status` picks the report over
    # the plain order-list tool `tool_filter` would otherwise have chosen. A PRODUCT is no
    # longer required - a customer is subject enough ("when we generate the outstanding
    # summary for customer and for product it is different, they should be the same"), and
    # the legacy `so_outstanding` bucket is no longer reachable for an outstanding ask at
    # all. What is still required is a SUBJECT: with neither a product nor a customer
    # resolved there is nothing to report on, and the plain order lane keeps that ask.
    # PLAN-low-stock-report S6 (AC-62/AC-64): the low stock INTENT picks the tool, not the
    # inventory domain - `crm_low_stock_report` is appended to that domain's pool, never
    # `tools[0]`, so `tool_filter` would otherwise hand every low stock ask to
    # `crm_inventory_stock_balance_list`. The outstanding override's own shape.
    if jsc.js_string(parse_output.get("intent_hint") or "") == _LOW_STOCK_INTENT:
        tool_name = _LOW_STOCK_TOOL
        tool_item = {"name": tool_name, "_tool_pick": {"source": "low_stock_override"}}

        # THE GATE, before any fetch (AC-64). Every other tool on the chatbot's read list
        # is a read; this one's fetch CREATES A REORDER RUN and sends a workbook, so a
        # refused contact must be turned away HERE rather than by the route answering 403
        # after the lane has already called it. The route checks the same key again
        # (AC-41) - two gates, because this one protects the side effect and that one
        # protects the data.
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = (
            set(granted_raw)
            if isinstance(granted_raw, (list, tuple, set, frozenset))
            else set()
        )
        if _LOW_STOCK_GRANT not in granted:
            return _low_stock_not_enabled()

        # LOWSTOCK-FILTER-ASK (owner ruling, 2 Oct 2026): nothing runs until the product
        # category is settled - taken from the message, else asked (`low_stock_ask`, the
        # shared `required_fields` helper). A product-code ask names its own scope and
        # is not asked. The answering turn carries the first message's grouping,
        # supplier word and location on the slot.
        answering = isinstance(parse_output.get("required_ask"), dict)
        named_product = any(
            isinstance(e, dict)
            and e.get("current_message") is True
            and jsc.js_string(e.get("hint") or "") == "product"
            for e in jsc.array(parse_output.get("entities"))
        )
        if answering or not named_product:
            settled = low_stock_ask.settle(
                db, parse_output, include_supplier=low_stock_ask.SUPPLIER_GRANT in granted
            )
            if trace is not None:
                trace.add("required_ask", {"ask": "low_stock_report", "done": settled.done,
                                           "values": settled.values})
            if not settled.done:
                if settled.slot is not None and not answering:
                    settled.slot.setdefault("extras", {})["warehouse_entities"] = [
                        e for e in jsc.array(parse_output.get("entities"))
                        if isinstance(e, dict) and e.get("current_message") is True
                        and jsc.js_string(e.get("hint") or "") == "warehouse"
                    ]
                return _fixed_reply(settled.reply or "", required_ask=settled.slot)
            semantic_input["low_stock_filters"] = low_stock_ask.route_filters(settled)
            if answering:
                # The first message's location word rides the slot (a date window
                # already rides the focus); re-read here as if typed this turn.
                parse_output = {
                    **parse_output,
                    "entities": [*jsc.array(parse_output.get("entities")),
                                 *jsc.array(settled.extras.get("warehouse_entities"))],
                }

        # ── B (console round 3): a report ask is a FRESH SCOPE ────────────────
        # `entity_op = replace_combine` merges the PREVIOUS turn's session entities into
        # the gate's list, so a bare "low stock report" asked after an unrelated product
        # question arrived carrying two products (`current_message: false`) and planned
        # "0 of 0". Unlike the outstanding report - whose carried filters are deliberate,
        # because an answering turn ("1", "both") has no subject of its own - a low stock
        # ask always states its own scope, so ONLY entities the CURRENT message named may
        # narrow the run. Everything carried is dropped.
        current_tokens = {
            jsc.js_string(tok).strip().casefold()
            for e in jsc.array(parse_output.get("entities"))
            if isinstance(e, dict) and e.get("current_message") is True
            for tok in (e.get("raw"), e.get("canonical_code"))
            if jsc.truthy(tok)
        }
        current_tokens.discard("")

        # The location word, resolved to EXACT codes before the call: the route takes
        # codes and does no suffix matching, so a token like "IB" has to be expanded here
        # - the same `resolve_warehouse_token` pass, and the same one-word-per-turn rule,
        # the outstanding report uses below.
        for e in jsc.array(parse_output.get("entities")):
            if not isinstance(e, dict) or jsc.js_string(e.get("hint") or "") != "warehouse":
                continue
            if e.get("current_message") is not True:
                continue  # carried from an earlier turn - not this ask's scope
            token = jsc.js_string(e.get("raw") or e.get("canonical_code") or "").strip()
            if not token or db is None:
                break
            codes = resolve_warehouse_token(db, token)
            if codes:
                semantic_input["low_stock_warehouse_codes"] = codes
            break

        # The resolved entity list is PRUNED here, not in the transformer: only this side
        # has `parse_output`, and the gate's entities carry no `current_message` flag, so
        # the typed tokens are the only way to tell this turn's subjects from the ones the
        # session carried in. Pruning the list (rather than publishing a second one) keeps
        # the transformer's own contract - it still reads product codes off the entities it
        # is handed - so every other tool's arg building is untouched.
        #
        # The match is SUBSTRING, not equality (reviewer round 3, item 2). The resolver
        # answers a typed prefix with the full variant code, so "low stock for the CB100
        # sink" arrives as raw "CB100" against a resolved "CB100-BL-DIY": equality pruned
        # the very product the customer named and widened the run to the whole book - the
        # opposite of what defect B's prune is for. Any token the CURRENT message carried
        # is still the test, so a carried entity nothing was typed about still drops.
        def _named_this_turn(entity: dict) -> bool:
            codes = [
                jsc.js_string(entity.get(field) or "").strip().casefold()
                for field in ("code", "canonical_code")
            ]
            return any(
                tok in code for code in codes if code for tok in current_tokens
            )

        entities = [
            e
            for e in jsc.array(entities)
            if isinstance(e, dict) and _named_this_turn(e)
        ]

    order_status_raw = jsc.js_string(parse_output.get("order_status") or "").strip()

    # R-S3 (reviewer finding, Phase 3 fix round, item 7): checked here, regardless of
    # whether a subject resolved - `run_until_exit`'s OWN bypass (above this module,
    # the earliest seam that knows both `order_status` and `ctx.access`) already stops
    # resolve+gate from ever running for an ungranted sales-report ask, so `entities`
    # arrives empty and the subject-gated branch below would never fire at all. This
    # is what still answers the refusal on that path, and is the SECOND line of
    # defence (mirrors `DOMAIN_GRANT_REQUIRED`'s own trace shape above) for a
    # re-entry path that calls `run_fetch` directly (this module's own tests).
    if order_status_raw in contracts.SALES_FIGURE_STATUSES:
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, (list, tuple, set, frozenset)) else set()
        if _SALES_REPORT_GRANT not in granted:
            if trace is not None:
                trace.add(
                    "domain_grant",
                    {"domain": domain, "skipped": "not_granted", "needs": _SALES_REPORT_GRANT},
                )
            return _sales_report_not_enabled()

    has_customer = (
        any(isinstance(e, dict) and e.get("entity_type") == "customer" for e in entities)
        if isinstance(entities, list)
        else False
    )
    # #1262 slice 9 (F1a), AC-S9-4: a brand alone is a valid subject for the
    # outstanding report. Resolved here (not merely hinted): SF4 (review round, 26
    # Sep 2026) - "brand XYZ" naming an unlisted word must never count as a subject,
    # so this must test the RESOLVED `outstanding_brand_ids` (the SAME live lookup
    # `_resolve_report_product_and_location` performs, called early here so its
    # result is ready before the subject gate below reads it), never any
    # brand-hinted entity regardless of whether it matched a live brand.
    _resolve_outstanding_brand_ids(parse_output, semantic_input, db=db)
    has_brand = bool(semantic_input.get("outstanding_brand_ids"))
    # #1262 slice 2 (F1c): a carried customer id is a subject only when it is a real
    # uuid - a kind-pick's printed label ("Sorento (customer)") riding on this same
    # key is not a resolved customer, and must never count as one here either.
    raw_carried_customer_ids = jsc.array(parse_output.get("outstanding_carried_customer_ids"))
    carried_customer_ids = [u for u in raw_carried_customer_ids if fetch_mod.is_uuid(u)]
    carried_customer_unusable = bool(raw_carried_customer_ids) and not carried_customer_ids
    # R13: on an ANSWERING turn the subject is whatever the stored filters carry - the
    # product code, the customer ids, or both - and neither needs resolving again: they
    # were resolved on the turn that asked.
    carried_subject = (
        bool(jsc.truthy(parse_output.get("outstanding_carried_product_code")))
        or bool(jsc.array(parse_output.get("outstanding_carried_product_codes")))
        or bool(carried_customer_ids)
    )
    if (
        domain == "order"
        and not (has_product or has_customer or has_brand or carried_subject)
        and carried_customer_unusable
        and (order_status_raw == "outstanding" or order_status_raw in fetch_mod.ORDER_STATUS_TO_SCOPE)
    ):
        # AC-S2-3: the ONLY subject this turn has is an unusable stored customer (the
        # label, never re-typed) - never run the report over no customer at all
        # ("Customer: all"); ask, with no tool call.
        return _which_customer_ask()
    if (
        domain == "order"
        and (has_product or has_customer or has_brand or carried_subject)
        and (order_status_raw == "outstanding" or order_status_raw in fetch_mod.ORDER_STATUS_TO_SCOPE)
    ):
        tool_name = "crm_outstanding_report"
        tool_item = {"name": tool_name, "_tool_pick": {"source": "outstanding_override"}}

        # -- AC-1119/S4c/D5/R17 (ruling 3): product-code + location resolution, the
        # ONE seam shared with `crm_sales_report` below. --------------------------- #
        _resolve_report_product_and_location(parse_output, entities, semantic_input, db=db)

        # -- R19: the NAMES for the question's own `Customer:` line ------------ #
        # Worked out once, here, and stored on both filter builders. The ids are uuids
        # and a uuid never reaches a customer's screen, so the question has to carry the
        # names the resolution already produced - this turn's, when the gate resolved a
        # customer, and otherwise the ones the CARRIED entities still hold (an answering
        # or refining turn resolves nothing of its own; the report's own header is built
        # from the ids by the route, which has the `customers` table to read).

        # -- D13: the sales_orders.outstanding field-reveal gate, before any fetch -- #
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, (list, tuple, set, frozenset)) else set()
        has_so_grant = _OUTSTANDING_SO_GRANT in granted

        # S4 point 3 (AC-1130): bare "outstanding" + the grant arms the scope
        # question instead of fetching. `outstanding_scope_ask_candidate` is a
        # PURELY SYNTACTIC flag `head/output_exchange.py::_post_process` sets
        # BEFORE resolve+gate even runs (domain/product/bare-order_status, no
        # grant knowledge there) - the grant check happens HERE, the only place
        # that already reads `ctx.access`, so a direct `run_fetch` call (this
        # module's own tests) that never went through that step just fetches,
        # which is what `TestToolPick` pins.
        if (
            order_status_raw == "outstanding"
            and jsc.truthy(parse_output.get("outstanding_scope_ask_candidate"))
            and has_so_grant
        ):
            return _outstanding_scope_ask(entities, semantic_input, db=db)

        scope = fetch_mod.ORDER_STATUS_TO_SCOPE.get(order_status_raw, "both")
        so_refused = False
        if scope in ("so", "both") and not has_so_grant:
            scope = "do"
            # AC-1140 vs AC-1141: a BARE "outstanding" silently redirects to `do` (no
            # question was ever asked, so there is nothing to refuse); an EXPLICIT SO
            # ask ("so_outstanding" / "outstanding_both") gets the refusal line.
            so_refused = order_status_raw != "outstanding"
        semantic_input["outstanding_scope"] = scope
        semantic_input["outstanding_so_refused"] = so_refused
    elif order_status_raw == "sales_analysis" and not has_own_accounts:
        # PLAN-retail-sales-reports-26sep S1: a sales ANALYSIS (a company's sales by
        # month, by year, by channel) is the reports kernel's own query, answered as the
        # whole table in text and the same query as an Excel (Owner ruling 26 Sep 07:16
        # Q2). An override like the two reports beside it, never `tools[0]`; the grant
        # was checked above, before anything was fetched.
        group_by = jsc.js_string(parse_output.get("group_by") or "")
        if group_by and group_by not in fetch_mod.SALES_ANALYSIS_GROUP_BY:
            # Review round 2 S1: "sales by customer" is not answered with a by-channel
            # table and no word that the ask was changed; it is said, and nothing fetched.
            return _sales_analysis_axis_unsupported(group_by)
        tool_name = _SALES_ANALYSIS_TOOL
        tool_item = {"name": tool_name, "_tool_pick": {"source": "sales_analysis_override"}}
    elif (
        domain == "order"
        and (has_product or has_customer or carried_subject)
        and order_status_raw == "sales_report"
    ) or (order_status_raw == "sales_analysis" and has_own_accounts):
        # S4 wiring point 3 (AC-1650): domain "order" + a resolved product OR
        # customer + `order_status: "sales_report"` picks THIS tool - one more
        # branch beside the outstanding override above, never `tools[0]`.
        #
        # CHATBOT-SELFREF-SCOPE B2 (production, 30 Sep 2026): a CUSTOMER-SCOPED contact
        # asking for "my sales this month" is parsed as a sales ANALYSIS, but that is a
        # company's own totals (`/api/v1/sales/analysis` refuses any contact linked to a
        # customer account, AC-S1-23), so the ask went out as `crm_sales_analysis` and
        # came back with nothing. Its sales figures are its linked customers' (parent
        # plan Q6a, Q8a): the customer-facing report, over the links, the way every
        # other sales ask of a scoped contact runs. Its reply is TEXT (the presenter's
        # month blocks), never a file.
        tool_name = "crm_sales_report"
        if order_status_raw == "sales_analysis":
            tool_item = {"name": tool_name, "_tool_pick": {"source": "customer_scope_sales_report"}}
            # A linked STAFF contact is not forced by the engine (not enforced), so its
            # links may not have been handed over as entities ("sales this month" with no
            # "my" resolves no customer at all): the links are the report's subject either
            # way, or the route answers 422 `subject_required`.
            if not any(isinstance(e, dict) and e.get("entity_type") == "customer" for e in entities):
                entities = [
                    *[e for e in entities if isinstance(e, dict)],
                    *[
                        {"uuid": cid, "entity_type": "customer", "code": code or name, "display_name": name, "scope": True}
                        for cid, name, code in (customer_scope.get("linked") or [])
                    ],
                ]
            if trace is not None:
                trace.add(
                    "customer_scope",
                    {
                        "decision": "sales_analysis_answered_as_sales_report",
                        "reason": "company_sales_analysis_is_not_a_linked_contacts_figure",
                        "ids": list(customer_scope["ids"]),
                    },
                )
        else:
            tool_item = {"name": tool_name, "_tool_pick": {"source": "sales_report_override"}}

        # -- S4 wiring point 4 (AC-1651): the gate, BEFORE any fetch. There is no
        # fallback scope here (unlike D13's SO/DO redirect above), so the absence
        # of the grant refuses the WHOLE ask, with no fetch and nothing armed. -- #
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, (list, tuple, set, frozenset)) else set()
        if _SALES_REPORT_GRANT not in granted:
            return _sales_report_not_enabled()

        # -- AC-1119/S4c/D5/R17 (ruling 3): the SAME product-code + location
        # resolution the outstanding override above uses, fixed once at this one
        # seam for both tools. --------------------------------------------------- #
        _resolve_report_product_and_location(parse_output, entities, semantic_input, db=db)
    elif domain == "order" and order_status_raw == "top_selling":
        # PLAN-chatbot-top-x-hot-selling-24sep.md S4 point 5 (AC-1950): the ranking needs
        # no subject, so no subject rule; never `tools[0]`. Gated on the sales report's
        # own key (owner ruling 26 Sep: no new key), checked above for every sales
        # figure status, before anything below can fetch.
        tool_name = TOP_SELLING_TOOL
        tool_item = {"name": tool_name, "_tool_pick": {"source": "top_selling_override"}}
        slot = semantic_input.get("top_selling")
        slot = slot if isinstance(slot, dict) else {}
        if slot.get("dealer_refused"):
            # Reviewer S2 (PR #1273): a linked dealer named a customer outside its own
            # ledgers (`engine._top_selling_dealer_scope`). No picker, no fetch.
            if trace is not None:
                trace.add("top_selling", {"refused": "customer_not_permitted"})
            return _fixed_reply(TOP_SELLING_REFUSED_OTHER_CUSTOMER)
        if slot.get("unclear"):
            # Owner retest of round 4 (27 Sep 2026, R6/R8): a message inside a ranking
            # the parser could not place is asked about in one short line, never sent to
            # the out of scope lane or the customer service routing.
            if trace is not None:
                trace.add("top_selling", {"asked": "unclear"})
            return _fixed_reply(TOP_SELLING_UNCLEAR)
        who = slot.get("who")
        if isinstance(who, dict):
            if trace is not None:
                trace.add("top_selling", {"asked": "who", "word": who.get("word")})
            return _fixed_reply(_top_selling_ask_who(who), top_selling_asked="who")
        question = _top_selling_question(slot)
        if question is not None:
            axis, line = question
            if trace is not None:
                trace.add("top_selling", {"asked": line})
            # A word this message named that the ranking cannot narrow by is said above
            # the question too, once (PR #1273 round 7: "by william" asked the metric and
            # the unknown agent was never said).
            unknown = [
                f"I don't know '{word}' as a {kind}." if kind else f"I don't know '{word}'."
                for kind, word in (slot.get("unknown") or [])
                if jsc.truthy(word)
            ]
            return _fixed_reply("\n\n".join([" ".join(unknown), line]) if unknown else line, top_selling_asked=axis)
        category_ids, _category_named, ambiguous, unknown = _top_selling_category_ids(slot, db=db)
        if ambiguous:
            if trace is not None:
                trace.add("top_selling", {"asked": "category", "codes": ambiguous})
            return _fixed_reply(_top_selling_ask_category(ambiguous), top_selling_asked="category")
        # Owner retest (27 Sep 2026): a word the ranking cannot narrow by is said in one
        # line above the ranking, which runs without it; the category word is then
        # forgotten (`top_selling_drop`), the brand and agent words were never kept.
        notes = [
            f"I don't know '{word}' as a {kind}." if kind else f"I don't know '{word}'."
            for kind, word in (slot.get("unknown") or [])
            if jsc.truthy(word)
        ]
        notes += [f"I don't know '{word}' as a category." for word in unknown]
        ceiling_note = top_selling_ceiling_note(slot)
        if ceiling_note:
            notes.append(ceiling_note)
        if unknown:
            semantic_input["top_selling_drop"] = ["category_words", "category_code"]
        semantic_input["top_selling_notes"] = notes
        semantic_input["top_selling_category_ids"] = category_ids
    elif tool_name in fetch_mod.ORDER_TOOLS and order_status_raw == "so_outstanding":
        # S2 (security review, 13 Sep 2026), narrowed by R13: the LEGACY bucket now only
        # catches an `so_outstanding` ask with NO subject at all (no product and no
        # customer) - every ask with one goes to the report above. The redirect stays for
        # exactly that remainder, because the bucket serves the same per-SO outstanding
        # quantities D13 gates: without the grant, redirect to `outstanding` (the DO
        # bucket) rather than call the SO bucket, and prefix the reply
        # (`output_structurer`'s generic path reads `so_bucket_refused`).
        access_ctx = ctx.get("access") if isinstance(ctx.get("access"), dict) else {}
        granted_raw = access_ctx.get("attributes")
        granted = set(granted_raw) if isinstance(granted_raw, (list, tuple, set, frozenset)) else set()
        if _OUTSTANDING_SO_GRANT not in granted:
            semantic_input["order_status"] = "outstanding"
            semantic_input["so_bucket_refused"] = True

    # DO-ASK-SIMPLIFY security S1 (PR #1433): a transporter named on a DO list ask would
    # filter the rows by it, telling the contact who carried each one. Without the
    # `delivery_orders.transporter` reveal the ask is refused before any fetch (review S5:
    # dropping the filter silently would answer a different question).
    if tool_name in fetch_mod.ORDER_TOOLS and not do_ask.granted(ctx, do_ask.TRANSPORTER_KEY):
        if any(isinstance(e, dict) and e.get("entity_type") == "transporter" for e in entities):
            if trace is not None:
                trace.add("field_reveal", {"refused": "transporter_filter", "tool": tool_name})
            return _fixed_reply(do_ask.TRANSPORTER_REFUSED)

    trigger = {
        "tool": tool_name,
        "entities": entities,
        "semantic_input": semantic_input,
        "contact_id": contact_id,
        # A2 (chatbot-growth-r1): read by `output_structurer`'s restricted-field
        # drop, which is the ONLY consumer of `access.attributes`. Slice C wires
        # `check_access` to fill it from `contact_field_reveals`; until then it is
        # always None, so every restricted field stays hidden by construction.
        "access": ctx.get("access"),
        # E2 (attribute-first asks): the resolver's `predicate` block, carried
        # through the gate untouched (`resolved`/`gate` are the same mutated dict,
        # `gate.py`'s own C4 bypass reads it off `resolver.get("predicate")` the
        # same way) - the ONLY place a HAS turn's `require`/`qualifying_total`
        # reach the fetch step at all. Absent on an ordinary turn, so
        # `fetch.entity_ids_transformer`'s own `trig.get("predicate") is not None`
        # check (limit=5, S1's E1) and `output_structurer`'s header (below) both
        # stay byte-inert for every non-HAS turn.
        "predicate": gate.get("predicate"),
    }
    predicate = gate.get("predicate")
    if isinstance(predicate, dict) and not predicate.get("qualifying_total"):
        # Reviewer B2 on PR #833: a described set with nothing in it has no product to
        # send, and the tool called without one answers about every product. The miss
        # lane names the set, the scheme or the unknown word instead.
        return _error_fragment("the described set qualifies nothing", outcome="not_found")
    try:
        args = fetch_mod.entity_ids_transformer(trigger, space_id=space_id)
        if dry_run and tool_name == _LOW_STOCK_TOOL:
            args["dry_run"] = True
    except fetch_mod.ScopeViolation as violation:
        # D4: the defence behind the engine's gate. No tool is called.
        if trace is not None:
            trace.add(
                "customer_scope",
                {
                    "refused": "customer_not_permitted",
                    "reason": "fetch_customer_ids_outside_links",
                    "tool": tool_name,
                    "dropped": violation.dropped,
                },
            )
        return _fixed_reply(str(customer_scope.get("refusal") or ""))
    # DO-ASK-SIMPLIFY rules 3-4 (owner, 2 Oct 2026; every contact, owner 4 Oct): a DO list ask with no
    # range, or a range over 31 days, is answered with one line and fetches nothing.
    # After the customer-scope backstop above (security S2), so the question never
    # names a customer outside the contact's links.
    range_line = do_ask.range_reply(
        ctx=ctx,
        tool_name=tool_name,
        order_tools=fetch_mod.ORDER_TOOLS,
        entities=entities,
        semantic_input=semantic_input,
    )
    if range_line is not None:
        if trace is not None:
            trace.add("do_range", {"asked": range_line.split("\n", 1)[0]})
        return _fixed_reply(range_line)
    # CHATBOT-SELFREF-SCOPE (owner rule Q6a): on a self-reference turn a carried account
    # did not narrow the report (`fetch._self_reference`); say so on the trace.
    carried_ids = [u for u in jsc.array(semantic_input.get("outstanding_carried_customer_ids")) if fetch_mod.is_uuid(u)]
    if (
        trace is not None
        and semantic_input.get("self_reference") is True
        and carried_ids
        and isinstance(args.get("customer_ids"), list)
        and args["customer_ids"] != carried_ids
    ):
        trace.add(
            "customer_scope",
            {
                "decision": "self_reference_uses_own_ids_not_the_carry",
                "tool": tool_name,
                "ids": list(args["customer_ids"]),
                "dropped": [u for u in carried_ids if u not in args["customer_ids"]],
            },
        )
    if (
        tool_name in policy_rows.ENTITY_FILTER_REQUIRED_TOOLS
        and not fetch_mod.has_narrowing_filter(args, tool_name=tool_name)
    ):
        # Nothing the customer named resolved, so no filter could be built, and the document
        # tools answer an unfiltered call with the whole library. Refused as an ABSENCE (the
        # question was understood and nothing narrows it), which is the miss lane's own
        # not-found arm - never as a listing of every file the contact may see.
        return _error_fragment(
            f"{tool_name} needs a document or entity filter and none could be built",
            outcome="not_found",
        )
    _tool_started = time.perf_counter()
    try:
        raw = fetch_mod.call_tool(tool_name, args, mcp=_McpSeam(services.mcp_call))
    except fetch_mod.ToolNotAllowed as refused:
        # H58: the top hit was a WRITE tool and nothing was called. Recorded with its own
        # outcome rather than as a tool failure, because the two need different reading:
        # a failure means the read did not work and may work next time, this means the
        # question routed somewhere the read-only chatbot must never go, and the tool's
        # name on the trace is what tells whoever tunes the pool which one to look at.
        logger.warning("chatbot: refused MCP tool %s", tool_name)
        return _error_fragment(str(refused), outcome="tool_not_allowed")
    except Exception as exc:  # noqa: BLE001 - `onError: continueErrorOutput`, verbatim
        logger.warning("chatbot: MCP tool %s failed", tool_name, exc_info=True)
        if tool_name == _LOW_STOCK_TOOL:
            # Console round 3, defect A: a run that outlasts the client's 10 s raised
            # `httpx.ReadTimeout` here and the customer read "I ran into a problem
            # understanding that" - about a report the worker was still building and would
            # push. This tool says its own line instead.
            filters = semantic_input.get("low_stock_filters")
            return _low_stock_unavailable(filters.get("header") if isinstance(filters, dict) else None)
        return _error_fragment(
            f"MCP tool {tool_name} failed: {exc}", outcome=_fetch_failure_outcome(tool_name, exc)
        )

    envelope = fetch_mod.parse_mcp_content(raw)
    if trace is not None:
        # A9: ONE call, ONE tool, ONE envelope this turn - the same "the read" this
        # whole function is named for. `envelope` rides through `trace.add`'s own
        # 32 KB cap, so a large result set never grows the trace unbounded.
        trace.add(
            "tool",
            {
                "name": tool_name,
                "args": args,
                "envelope": envelope,
                "ms": int((time.perf_counter() - _tool_started) * 1000),
            },
        )
    # The ERROR check comes BEFORE the render: an error envelope has no rows, and rendering
    # it first would build a "No matching results found." message for a turn that failed.
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), str):
        return _error_fragment(envelope["error"])

    structured = fetch_mod.output_structurer(envelope, trigger)
    # LOWSTOCK-FILTER-ASK (owner hand test, 3 Oct 2026): EVERY low stock reply - ready,
    # pending, busy, empty, error - opens with the filters it was built with, "Low stock
    # report (water closet, supplier X, by supplier)", so a narrowed file never reads as
    # the whole book. The ready line's own "Low stock report - as of" takes the header in
    # place; any other line gets it above.
    filters = semantic_input.get("low_stock_filters") if tool_name == _LOW_STOCK_TOOL else None
    if isinstance(filters, dict) and filters.get("header") and isinstance(structured, dict):
        response = jsc.js_string(structured.get("response") or "")
        if response.startswith("Low stock report - as of"):
            structured["response"] = filters["header"] + response[len("Low stock report"):]
        else:
            structured["response"] = f"{filters['header']}\n{response}" if response else filters["header"]
    if trace is not None:
        restricted = envelope.get("restricted_fields") if isinstance(envelope, dict) else None
        if isinstance(restricted, dict) and restricted:
            access = trigger.get("access") if isinstance(trigger.get("access"), dict) else {}
            granted_raw = access.get("attributes")
            granted = list(granted_raw) if isinstance(granted_raw, list) else []
            # A9: which restricted keys this turn's envelope carried, which the
            # contact's access actually granted, and which were therefore dropped -
            # the same "attributes is a list, today always None" contract A2 reads.
            dropped = [k for k in restricted if restricted[k] not in granted]
            # The GROUP AXIS, when `output_structurer` refused it (blocker 1, AC-907):
            # a restricted value used as a section heading is a leak no field filter can
            # reach, so the axis is dropped and the answer rendered flat - and the trace
            # has to say which axis went, or the operator reads an ungrouped answer to a
            # grouped question with no reason anywhere.
            axis_dropped = envelope.get("group_by_dropped") if isinstance(envelope, dict) else None
            if axis_dropped:
                dropped.append(f"group_by:{axis_dropped}")
            trace.add(
                "reveals",
                {
                    "restricted_fields_seen": sorted(restricted.keys()),
                    "granted": sorted(granted),
                    "dropped": sorted(dropped),
                },
            )
        # AC-17 (PLAN-spec-visibility-policy.md "Chatbot seam"), beside `reveals`:
        # which spec keys this contact has hidden, and which of them the
        # projection ACTUALLY REMOVED from this envelope (code review S2:
        # `spec_hidden_dropped`, which `_project_product_specs` sets on `e` -
        # the SAME object as `envelope`, `output_structurer`'s own `group_by_
        # dropped` reads back the identical way - not vocabulary membership,
        # which says nothing about whether the product this turn showed even
        # carried the key). Only for a PRODUCT envelope: every other result
        # type never runs the projection at all, so the entry would always be
        # empty noise.
        is_product_envelope = (
            isinstance(envelope, dict)
            and jsc.js_string(envelope.get("result_type") or "") == "products"
        )
        if is_product_envelope:
            access = trigger.get("access") if isinstance(trigger.get("access"), dict) else {}
            hidden_raw = access.get("hidden_spec_keys")
            hidden_list = sorted(hidden_raw) if isinstance(hidden_raw, list) else []
            if hidden_list:
                dropped_raw = envelope.get("spec_hidden_dropped")
                dropped_list = sorted(dropped_raw) if isinstance(dropped_raw, list) else []
                trace.add(
                    "spec_visibility",
                    {"hidden": hidden_list, "dropped": dropped_list},
                )
    item = fetch_mod.fetch_result(structured, tool=tool_item, tier_probe=None)
    # SEC-B1/AC-1333: the RECOMPOSED access_levels this turn's tool call actually
    # carried (`semantic_input`'s own, built above from `tier_gate.access_levels_
    # recomposed` when a tier gate ran) - `turn_runtime.set_page_carry` stores this
    # in the set_page carry so the answer to "how many should I show?" recounts under
    # the SAME tier, rather than the bare parser's own (empty, on a bare count) list.
    item["access_levels"] = semantic_input.get("access_levels")
    return {
        "kind": "result",
        "_fetch_arm": item["_fetch_arm"],
        "delegate": DELEGATE,
        "delegate_payload": {**payload, "fetch": item},
        "fetch": item,
    }


class _McpSeam:
    """Adapts the `mcp_call(name, args)` seam to `call_tool`'s client-shaped parameter.

    `call_tool` is the ported node and takes something with `.call_tool(name, args)`; the
    bundle is a plain callable so a test can stub it in one line. This is the two-line
    adapter that lets the production path go through the ported function rather than
    around it.
    """

    __slots__ = ("_call",)

    def __init__(self, call: Any) -> None:
        self._call = call

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self._call(name, arguments)


__all__ = [
    "DELEGATE",
    "ENTRY_BY_BRANCH_KIND",
    "handles",
    "run_fetch",
    "run_until_exit",
]


# NOT ON THE TURN PATH since the re-architecture: `run_turn` calls `run_fetch` above and
# composes with `turn/compose.py`. Zero callers under `app/`; still driven directly by the
# KEPT-node replay corpus (`tests/chatbot/test_replay.py`, `divergences.py`) and by
# `test_crossdomain_ladder.py`, which is why it is not deleted yet - contract 3's ladder
# now lives in `turn/fetch.py::_climb`.
def complete_answer(
    payload: dict[str, Any],
    *,
    turn_id: str,
    ctx: dict[str, Any],
    item: dict[str, Any],
    branch_kind: str,
    services: Any,
    session_factory: Any,
    space_id: str | None = None,
    dry_run: bool = False,
    crossdomain_ladder: dict[str, list[str]] | None = None,
    trace: Any = None,
) -> dict[str, Any]:
    """S6c: finish the business turn in process, and return `{reply, actions, ...}`.

    The third and last call site into this package, after `run_until_exit` and `run_fetch`.
    It runs the ANSWER half - `validator`, `promo-picker`, the three cross-domain nodes and
    `build-result` - then `If6` dispatches into `sub-answer` or the miss lane, and the
    `sub-output` fragments go to the S2 tail (`engine.complete_turn`), which composes the
    reply and writes the session exactly as it does for every other completed lane.

    `payload` is `run_until_exit`'s output with `run_fetch`'s `fetch` folded in, i.e. the
    same object `delegate_payload` carries today.

    **The ITEM handed to the tail is the LANE's own output, never `route-turn`'s** - the
    same rule S4 learned the hard way. `complete_turn`'s entry gate runs `escalate-catalog`
    only when the item carries a `branch_kind`, and `route-turn`'s (`business_query`) is a
    kind the catalog has no case for, so it would fall through to an EMPTY response and the
    reply would come out blank.

    **The three non-answer arms stamp the tail's own kind, because the spine does.** Live
    puts a Set node on each of those edges - `build-suggest-offer -> tag-not-found`,
    `access-level-choice-message -> tag-access-choice`, and the two gate pickers reach
    `tag-not-found` through `build-suggest-offer` - and `escalate-catalog` is what turns
    their `escalate_message` into the customer's `response`. Without the stamp the catalog
    is skipped, the compile-state ladder finds no `response` on the item (the miss lane
    writes `escalate_message`, not `response`) and the customer reads nothing: H11's empty
    turn, arriving through the CRM instead of through n8n. The ANSWER half carries no kind
    and must not: its live path (`central-exchange -> ... -> compile-current-state`) has no
    tag node, so the catalog is correctly skipped there.

    **No database session is held across the probes.** Everything this function reads from
    the database was read before it was called; `services` is the injected seam pair, and
    the only session opened here is the tail's own, after every probe has answered.
    """
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business import answer as answer_mod
    from app.services.chatbot.lanes.business import miss_suggest as miss_mod
    from app.services.chatbot.lanes.business import sub_answer as sub_answer_mod

    parser = ((ctx.get("parse") or {}).get("output")) or {}
    session_block = ctx.get("session") or {}
    contact = ctx.get("contact") or {}
    contact_id = contact.get("id")
    resolved = payload.get("resolved") if isinstance(payload.get("resolved"), dict) else {}
    gate = payload.get("gate") if isinstance(payload.get("gate"), dict) else {}
    fetch = payload.get("fetch") if isinstance(payload.get("fetch"), dict) else {}
    exit_kind = payload.get("_exit_kind")
    # The entitlement union the resolver's aggregate returned, when it ran. Two readers:
    # `crossdomain-probe`'s access-level intersection and the promotion entitlement-miss
    # sentence. `None` is "the aggregate did not run", which both arms handle.
    aggregate = payload.get("aggregate") if isinstance(payload.get("aggregate"), dict) else None
    entities_names = aggregate.get("name") if aggregate is not None else None

    fragments: dict[str, Any] = {
        "ctx": ctx,
        "resolved": resolved,
        "gate": gate,
        # SEC-B1/AC-1333: the recomposed access_levels THIS fetch actually used
        # (None on any arm that never called the tool - tier_ask, error, offer)
        # - `compile_state._set_page_carry` reads it off `values["access_levels_
        # used"]` for the set_page carry, and it is what tells that function
        # whether a set answer actually rendered this turn at all.
        "access_levels_used": fetch.get("access_levels"),
    }
    lane_item: dict[str, Any]

    # `fetch-result`'s own arm names, spelled the way IT spells them: `tier-ask` with a
    # hyphen (`fetch.fetch_result`), `error` and `result` without one.
    fetch_arm = fetch.get("_fetch_arm")
    if exit_kind == "access_ask" or fetch_arm == "tier-ask":
        # The customer has to name an access tier before anything can be read.
        # `access-level-choice-message` renders the ask, and S6b's per-tier probe is what
        # makes it honest ("Dealer - has promotion").
        tier_source = fetch if fetch_arm == "tier-ask" else payload
        lane_item = {
            **answer_mod.access_level_choice_message(tier_source, parser=parser),
            # `tag-access-choice`
            "branch_kind": "access_choice",
        }
        fragments["access_choice"] = lane_item

    elif exit_kind == "offer":
        # The gate rendered its own picker (incoming / customer). It is already the answer.
        # Live sends both pickers through `build-suggest-offer` into `tag-not-found`, so the
        # kind is `not_found` here too and the catalog reads the picker's own
        # `escalate_message` off the `incoming_picker` fragment below.
        # Confirmed a no-op on this arm's roster fields against a real capture:
        # tests/chatbot/test_s6c_engine_paths.py::TestOfferArmSkipsBuildSuggestOfferSafely
        lane_item = {**dict(payload), "branch_kind": "not_found"}
        fragments["incoming_picker"] = lane_item

    elif fetch_arm == "error" and fetch.get("outcome") == "access_denied":
        # D6 (PLAN-chatbot-last-purchase-cost.md, review B1): the whole-domain grant
        # gate in `run_fetch` refused the turn before any tool ran. The customer gets
        # the SAME registered `access_denied` template the head-level agent-denial
        # renders, but the SUBJECT is the FEATURE ("purchase cost"), never the parser's
        # `suggested_agent` - a field-reveal refusal is not an agent refusal, and
        # `canned.access_denied_text` stays untouched (its own callers still want the
        # agent). `outcome_fragment["escalate-catalog"]` overrides `build-outcome`'s own
        # key VERBATIM (RS-6.1c's mechanism, the same one `lanes/ideate.py` uses), so
        # the tail's compile ladder renders this response with NO branch_kind of its
        # own and therefore no catalog switch to teach a tenth arm to.
        from app.services.chatbot import copy as copy_mod
        from app.services.chatbot.lanes import canned as canned_lanes

        db_session = session_factory()
        try:
            canned = copy_mod.resolve(db_session)
        finally:
            db_session.close()
        denial_text = canned_lanes.field_grant_denied_text(canned, "purchase cost")
        lane_item = {
            "outcome_fragment": {
                "escalate-catalog": {
                    "response": denial_text,
                    "manualResponse": True,
                    "includeResponse": True,
                    "is_escalate_offer": False,
                }
            }
        }

    elif fetch_arm == "error" and fetch.get("outcome") != "not_found":
        # An INFRASTRUCTURE failure, not an absence: the MCP call raised or the tool
        # returned an error envelope. Rendering the miss lane here would
        # tell the customer "I could not find anything" about a read that never ran - the
        # same assertion `crossdomain-render`'s "positive facts only" rule refuses to make.
        # Live agrees: `Call 'sub-get-results'` carries `onError: continueErrorOutput` and
        # its ERROR output goes to `set-ran-query-formulator`, whose whole body is
        # `output.response = 'There is some error encountered by the AI: ${...error}'`,
        # sent straight out - it never reaches `not-found-error-message`.
        #
        # The engine decides this at `looked_up` and does not call this function for it, so
        # reaching here means a caller went round that gate; raising puts the turn on the
        # lane's own failure path (generic error reply, `failed`, R4 manual retry) instead
        # of quietly answering with the wrong words.
        raise RuntimeError(
            "fetch failed before an answer existed: "
            f"{jsc.nullish_str(fetch.get('error'), 'unknown fetch error')}"
        )

    elif exit_kind == "not_found" or fetch_arm == "error":
        lane_item = _run_miss_half(
            payload,
            granted_keys=(ctx.get("access") or {}).get("attributes"),
            parser=parser,
            resolved=resolved,
            gate=gate,
            services=services,
            contact_id=contact_id,
            space_id=space_id,
            execution_id=turn_id,
            fragments=fragments,
            miss_mod=miss_mod,
            answer_mod=answer_mod,
            dry_run=dry_run,
            # No fetch ran on this arm (the gate failed BEFORE it), so nothing came back -
            # which is exactly what `_sibling_gate`'s fourth condition asks
            # (`has_result is False`). Omitting it left `build_result` at `None` and the
            # gate short-circuited, so a partially-typed variant code never got its
            # sibling-family offer even though every other condition held.
            build_result={"has_result": False},
        )

    else:
        # The ANSWER half proper. `validator` stamps `is_valid` (and rewrites the response
        # on the demand-quantity arm), `promo-picker` owns the promotion ordering / pick /
        # roster, the cross-domain trio asks the OTHER domain about a code that came back
        # empty, and `build-result` is the `result` contract every later reader keys on.
        structured = fetch.get("result") if isinstance(fetch.get("result"), dict) else fetch
        validated = answer_mod.validator(
            dict(structured),
            semantic_parser=(ctx.get("parse") or {}),
            not_allowed_check_stock=bool(payload.get("not_allowed_check_stock")),
        )
        promo = answer_mod.promo_picker(
            validated, parser=parser, resolved=resolved, gate=gate
        )
        # n8n feeds `crossdomain-zeroset` the PROMO-PICKER's output; this feeds it the
        # VALIDATOR item. Equivalent only because `promo_picker` returns its input
        # unchanged off the promotion domain and the zeroset gate accepts inventory /
        # incoming only - so if that early return ever starts reshaping the item, this
        # call has to take `promo` instead.
        crossdomain = answer_mod.run_crossdomain(
            validated,
            parser=parser,
            resolved=resolved,
            session_block=session_block,
            entities_names=entities_names,
            services=services,
            contact_id=contact_id,
            space_id=space_id,
            dry_run=dry_run,
            crossdomain_ladder=crossdomain_ladder,
            trace=trace,
            # the contact's granted field-reveal keys - the PO rung needs
            # `purchase_orders.placed` (8 Sep 2026); same set the field drop reads
            granted=(
                (ctx.get("access") or {}).get("attributes")
                if isinstance(ctx.get("access"), dict)
                else None
            ),
        )
        result_item = answer_mod.build_result(
            promo,
            validator=validated,
            promo=promo,
            zeroset=crossdomain.get("zeroset"),
            tool=(fetch.get("tool") if isinstance(fetch.get("tool"), dict) else None),
            tier_probe=fetch.get("tier_probe"),
            crossdomain_render=crossdomain.get("render"),
        )
        # `build-result`'s WHOLE item, not its inner `result` object: the tail reads the
        # cross-domain block through `$('build-result').first().json.result.xd.block`, so
        # the double `result` is the shape it expects (`tail/compose._cross_domain_block`,
        # and `test_replay._run_crossdomain_compose` hands it the same thing). Passing the
        # inner object made every answered turn fail in the tail.
        fragments["result"] = result_item
        fragments["crossdomain_render"] = crossdomain.get("render")

        if answer_mod.dispatch(result_item.get("result")) == "sub_answer":
            lane_item = _run_answer_half(
                result_item,
                parser=parser,
                resolved=resolved,
                gate=gate,
                services=services,
                contact_id=contact_id,
                space_id=space_id,
                fragments=fragments,
                sub_answer_mod=sub_answer_mod,
                miss_mod=miss_mod,
            )
        else:
            # `Aggregate1` collects `response_intro` off the item before the miss lane
            # runs, and the miss renderer's payload is what carries it forward.
            miss_payload = {
                **result_item,
                "response_intro": answer_mod.aggregate_response_intro(
                    result_item.get("result")
                ),
            }
            lane_item = _run_miss_half(
                miss_payload,
                granted_keys=(ctx.get("access") or {}).get("attributes"),
                parser=parser,
                resolved=resolved,
                gate=gate,
                services=services,
                contact_id=contact_id,
                space_id=space_id,
                execution_id=turn_id,
                fragments=fragments,
                miss_mod=miss_mod,
                answer_mod=answer_mod,
                dry_run=dry_run,
                build_result=result_item.get("result"),
            )

    # The row was closed `delegated` at `routed` by the caller before this function ran
    # (`engine.close_turn_for_tail`), which is the state `complete_turn` refuses to run
    # without and the state the turn is genuinely in while the tail has not folded the
    # lane's result in yet.
    completed = engine_mod.complete_turn(
        turn_id,
        {**fragments, "item": lane_item},
        session_factory=session_factory,
        # This lane is finishing the turn, so the caller needs the send to execute (D9).
        # It cannot be built before the tail the way S4's clarifier builds its own: the
        # words do not exist until the tail has composed them.
        compose_send_action=True,
        # A9: the events THIS lane recorded (the cross-domain probes, the field reveals)
        # were added after the head closed the row, so `complete_turn`'s resume-from-row
        # cannot see them. Handed over explicitly, or they never reach the column.
        lane_trace=trace,
    )
    return {
        "reply": completed.reply,
        "actions": completed.actions,
        "session_patch": completed.session_patch,
        "status": completed.status,
        "stage": completed.stage,
    }


def _run_answer_half(
    result_item: dict[str, Any],
    *,
    parser: dict[str, Any],
    resolved: dict[str, Any],
    gate: dict[str, Any],
    services: Any,
    contact_id: Any,
    space_id: str | None,
    fragments: dict[str, Any],
    sub_answer_mod: Any,
    miss_mod: Any,
) -> dict[str, Any]:
    """`sub-answer`, in process: central-exchange, the miss roster, the partial did-you-mean."""
    trigger = {"item": result_item}
    entered = sub_answer_mod.answer_input(trigger)
    central = sub_answer_mod.central_exchange(entered)
    build_result_block = result_item.get("result") or {}

    checked = sub_answer_mod.miss_roster_check(
        central, build_result=build_result_block, parser=parser
    )
    member_offer = None
    roster_plan = None
    if checked.get("_offer") is True:
        roster_plan = sub_answer_mod.miss_roster_plan(
            checked,
            build_result=build_result_block,
            parser=parser,
            gate=gate,
            central_exchange=central,
        )
        member_offer = sub_answer_mod.build_miss_member_offer(
            checked, central_exchange=central, roster_plan=roster_plan
        )

    # The PARTIAL did-you-mean: the answer came back, but some of the tokens the customer
    # named resolved to nothing. Same planner as the miss lane's, deployed on this arm.
    plan = sub_answer_mod.dym_transform_partial(
        member_offer if member_offer is not None else central,
        parser=parser,
        gate=gate,
        resolved=resolved,
        central_exchange=central,
    )
    annotated = None
    if plan.get("probe_needed") is True:
        try:
            probe = services.mcp_probe(
                plan.get("probe_tool"),
                miss_mod._probe_args(
                    plan.get("dym_probe_entities") or [],
                    parser=parser,
                    contact_id=contact_id,
                    space_id=space_id,
                ),
            )
        except Exception:  # noqa: BLE001 - fail OPEN: no annotation, today's bare offer
            logger.warning("chatbot: partial did-you-mean probe did not run", exc_info=True)
            probe = {"error": "probe failed"}
        annotated = sub_answer_mod.dym_annotate_partial(
            probe,
            payload=member_offer if member_offer is not None else central,
            transform=plan,
        )

    answer = sub_answer_mod.answer_result(
        annotated if annotated is not None else (member_offer or central),
        central_exchange=central,
        member_offer=member_offer,
        dym_annotate_partial=annotated,
    )
    fragments["answer"] = answer
    return answer


def _run_miss_half(
    payload: dict[str, Any],
    *,
    parser: dict[str, Any],
    resolved: dict[str, Any],
    gate: dict[str, Any],
    services: Any,
    contact_id: Any,
    space_id: str | None,
    execution_id: str,
    fragments: dict[str, Any],
    miss_mod: Any,
    answer_mod: Any,
    dry_run: bool,
    build_result: Any = None,
    granted_keys: Any = None,
) -> dict[str, Any]:
    """`not-found-error-message` -> `sub-miss-suggest` -> `build-suggest-offer` ->
    `tag-not-found`.

    The tag is the last node on this edge in the live spine and it is not decoration:
    `escalate-catalog` only runs on an item that carries a `branch_kind`, and it is what
    turns the offer's `escalate_message` into the customer's `response`. The FRAGMENT keeps
    the composer's own output untagged, so what the tail grades is unchanged.
    """
    not_found = answer_mod.not_found_error_message(
        payload, parser=parser, resolved=resolved, gate=gate, granted_keys=granted_keys
    )
    fragments["not_found"] = not_found

    offer = miss_mod.run_miss_lane(
        not_found,
        parser=parser,
        resolved=resolved,
        gate=gate,
        services=services,
        build_result=build_result,
        contact_id=contact_id,
        space_id=space_id,
        execution_id=execution_id,
        dry_run=dry_run,
    )
    fragments["suggest_offer"] = offer
    return {**offer, "branch_kind": "not_found"}
