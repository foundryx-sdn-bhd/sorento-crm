"""Port of the business lane's ANSWER half (S6c, AC-607 to AC-609).

The spine's own nodes between the fetch and the tail: `validator`, `promo-picker`, the
three cross-domain nodes, `build-result`, `If6`/`Aggregate1`, and the two miss-lane
renderers (`not-found-error-message`, `access-level-choice-message`) plus the composer
`build-suggest-offer`. `sub_answer.py` and `miss_suggest.py` hold the two sub-workflows
this file dispatches into.

**Source of truth is the LIVE SPINE's copy of each body**, not the clone's and not
`sub-main-processing`'s: every capture the corpus grades these against came from
`live-spine-sorento-consume-main`, and the three copies genuinely differ (measured:
`build-suggest-offer` is 710 / 729 / 944 lines across them). There are TWO named
exceptions: `build-result` lives only in `sub-main-processing-live` and `sub-answer`, and
the tester's runner passes the sub's five producers, so the 88-line version is the one
ported; and `crossdomain-zeroset`'s domain guard is taken from the same sub's 151-line
body, which makes it a divergence from the shipping spine rather than parity - the shas
are cited at the block and the entry is `divergences.CROSSDOMAIN_DYM_OFFER_DOMAIN_GUARD`.

**Nothing here holds a database session.** Every function here is pure over plain dicts,
and the two seams that do I/O (`AnswerServices.mcp_probe`, `.family_fetch`) are injected
by the caller.

**D11.** Everything after the parser works on structured state. Where a ported body
sniffs raw text - and a few do - the line carries `# D11-reproduced` naming the n8n site,
so a new fuzzy match cannot slip in beside a parity one.
"""
from __future__ import annotations

import logging
import re
from functools import cmp_to_key
from typing import Any, Literal

from app.services.chatbot import jsc
from app.services.chatbot.lanes.business.fetch import DATE_PARAMS, space_id_or_default
from app.services.ledger_family import customer_group_of, customer_header_words
from app.services.product_spec_registry import SPEC_ACRONYMS
from app.services.chatbot.tail.scope_block import live_brand_words
from app.services.chatbot.turn import refer

# The did-you-mean helpers the JS carries in BOTH bodies with a "keep in lockstep" note.
# `miss_suggest` owns them because that is where their node lives; this file imports them
# rather than re-deriving them, which is the drift those JS notes are warning about. The
# leading underscores are the JS's own names, kept so a reader can grep one identifier
# across the port and the body; the `_ms_` prefixes only disambiguate from this module's
# same-named locals. The dependency runs ONE way at import time (answer -> miss_suggest);
# the composer `build_suggest_offer` goes back the other way, which is why THAT import is
# deferred to call time and documented at its site.
from app.services.chatbot.lanes.business.miss_suggest import (
    _PRODUCT_CODE_RE as _PRODUCT_CODE_LABEL_RE,
    _cap3,
    _is_exact as _ms_is_exact,
    _is_uuid as _ms_is_uuid,
    _norm as _ms_norm,
    human_label as _ms_human_label,
    miss_resolutions as _ms_miss_resolutions,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# `If6` and `Aggregate1` - the dispatch between the answer and the miss lane.
# --------------------------------------------------------------------------- #

Lane = Literal["sub_answer", "miss_suggest"]

# "the caller said nothing", which is not the same as "the caller said None".
_UNSET: Any = object()


def dispatch(result: dict[str, Any] | None) -> Lane:
    """`If6`: `validator.has_result` AND `validator.is_valid`, both STRICT booleans.

    Strict type validation on both conditions, so anything that is not literally `True`
    takes the FALSE leg into the miss lane. That is the safe direction: a turn that cannot
    prove it has an answer goes to the lane that says so.
    """
    body = result if isinstance(result, dict) else {}
    return (
        "sub_answer"
        if body.get("has_result") is True and body.get("is_valid") is True
        else "miss_suggest"
    )


def aggregate_response_intro(result: dict[str, Any] | None) -> list[Any]:
    """`Aggregate1` (`fieldsToAggregate: [response_intro]`) on the miss branch.

    n8n's Aggregate SKIPS an item that does not carry the field, which is why a null
    `response_intro` collects to `[]` rather than `[None]` - and why the miss lane must be
    handed the list, not the raw value.
    """
    body = result if isinstance(result, dict) else {}
    value = body.get("response_intro")
    return [] if value is None else [value]


# --------------------------------------------------------------------------- #
# H45 - a row already in the answer is never offered again.
#
# There is no code-keyed "already shown" predicate here, and that is deliberate. The
# live exclusion is `build-suggest-offer.js:288-323`'s answered-token OUTCOME rule: a
# candidate whose UUID is in `gate.compatible_entities` WAS QUERIED this turn, so the
# answer already covers it and offering it back is a dead end. It is ported verbatim in
# `build_suggest_offer` below (the `queried` block), keyed by uuid over the same
# `_QUERIED_TYPES` list the transformer maps to CRM params, and it is what AC-609 /
# H45 are graded on. A second, code-keyed predicate beside it would be an improvement
# with no counterpart in any shipping body, which D8 puts after parity, not before it.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The per-lane completion switch lives in ONE place and it is not here: the engine
# reads `system_settings.chatbot_completed_lanes` once per turn (`engine._enabled_lanes`)
# and asks `delegate.delegate_for` whether this arm completes. This lane used to carry
# thin wrappers over both; nothing in `app/` called them, and a second surface for a
# decision with one real caller is how the two drift.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# validator
# --------------------------------------------------------------------------- #


def validator(
    result: dict[str, Any] | None,
    *,
    semantic_parser: dict[str, Any] | None,
    not_allowed_check_stock: bool = False,
) -> dict[str, Any]:
    """`validator`: stamps `is_valid`, and on the stock-denial arm rewrites `response`.

    `is_valid` is set to TRUE unconditionally - the name is aspirational, and reproducing
    that is the point: `If6` reads it, so a port that made it conditional would reroute
    turns n8n answers today.

    The `not_allowed_check_stock` block is the demand-quantity answer: group the returned
    rows by product, sum `stock_qty`, and say per product whether the demanded quantity can
    be fulfilled. It is reached only from the arm S6a's `Edit Fields2` stamps.
    """
    output = result if isinstance(result, dict) else {}
    output["is_valid"] = True
    if not not_allowed_check_stock:
        return output

    parser_node = semantic_parser if isinstance(semantic_parser, dict) else {}
    parser_output = parser_node.get("output")
    if not isinstance(parser_output, dict):
        parser_output = parser_node
    # `Number(parserOutput.demand_qty ?? 0)` - the coercion is over the VALUE, not over
    # its string form: `Number(true)` is 1 where `Number(String(true))` is NaN.
    raw_demand = parser_output.get("demand_qty")
    demand_qty = jsc.js_number(0 if raw_demand is None else raw_demand)

    answers = output.get("answers") if isinstance(output.get("answers"), list) else []
    grouped: dict[str, dict[str, Any]] = {}
    for item in answers:
        product = jsc.get(item, "product") or "UNKNOWN_PRODUCT"
        stock_qty = jsc.js_number(
            jsc.get(item, "stock_qty") if jsc.get(item, "stock_qty") is not None else 0
        )
        group = grouped.setdefault(
            product, {"product": product, "total_stock_qty": 0, "rows": []}
        )
        group["total_stock_qty"] += stock_qty
        group["rows"].append(item)

    intro_lines = []
    for group in grouped.values():
        if demand_qty <= group["total_stock_qty"]:
            intro_lines.append(
                f"Quantity of {jsc.js_string(demand_qty)} for product "
                f"{group['product']} can be fulfilled."
            )
        else:
            intro_lines.append(
                f"Quantity of {jsc.js_string(demand_qty)} for product "
                f"{group['product']} cannot be fulfilled. Total available quantity is "
                f"{jsc.js_string(group['total_stock_qty'])}."
            )
    output["response"] = "\n".join(intro_lines)
    return output


# --------------------------------------------------------------------------- #
# build-result
# --------------------------------------------------------------------------- #


def build_result(
    item: dict[str, Any] | None,
    *,
    validator: dict[str, Any] | None,
    promo: dict[str, Any] | None,
    zeroset: dict[str, Any] | None,
    tool: Any = None,
    tier_probe: Any = None,
    crossdomain_render: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`build-result`: the `result` contract every later reader keys on.

    EVERY value-bearing key is a named producer's output VERBATIM - no reshaping and no
    defaults, except where the producer is itself NULLABLE (`tier_probe`, `xd.block`), in
    which case the null IS the contract.
    """
    body = item if isinstance(item, dict) else {}
    validator_out = validator if isinstance(validator, dict) else {}
    zs = zeroset if isinstance(zeroset, dict) else {}
    block = None
    if isinstance(crossdomain_render, dict):
        block = crossdomain_render.get("_xdBlock") or None
    xd_active = bool(jsc.truthy(zs.get("_xd")) and jsc.get(zs.get("_xd"), "active"))
    return {
        **body,
        "result": {
            # The whole CRM answer envelope, forward-looking: no reader needs it yet.
            "rows": validator_out,
            "has_result": validator_out.get("has_result"),
            "is_valid": validator_out.get("is_valid"),
            "tool": tool,
            "promo": promo,
            "xd": {"active": xd_active, "block": block},
            "tier_probe": tier_probe if tier_probe is not None else None,
        },
    }


# --------------------------------------------------------------------------- #
# access-level-choice-message
# --------------------------------------------------------------------------- #

TIER_DISPLAY = {"office": "Office", "dealer": "Dealer", "end_user": "End user"}
ASK_ORDER = ("office", "dealer", "end_user")

# Friendlier labels than the raw `domain_hint`, verbatim.
DOMAIN_LABELS = {
    "promotion": "promotions",
    "master_products": "product information",
    "product_attachment": "product attachments",
    "inventory": "stock",
    "order": "orders",
    "incoming": "incoming stock",
    "forms": "forms",
    "portal_link": "this request",
    # ATTACHMENT-MULTI: the four ALLOWED domains this map lacked, in `turn/policy_rows.py`'s
    # own words, so the fallback below never prints a domain key.
    "spo_allocation": "last in",
    "purchase_cost": "last purchase cost",
    "resource_attachment": "resource attachments",
    "purchase_order": "outstanding purchase orders",
}


def access_level_choice_message(
    item: dict[str, Any] | None,
    *,
    parser: dict[str, Any] | None,
    tier_availability: Any = _UNSET,
) -> dict[str, Any]:
    """The tier ask, reached on `If4` FALSE: either no access at all, or a pick is needed.

    A NUMBERED TYPED LIST, never WhatsApp quick-reply buttons (D3), offering ONLY the tiers
    this contact holds. The roster is persisted by the tail with
    `selection_context = 'tier_offer'`, and the parser resolves a numbered or tier-word
    reply against it - never to entities.
    """
    out = item if isinstance(item, dict) else {}
    names = out.get("name") if isinstance(out.get("name"), list) else []
    q = parser if isinstance(parser, dict) else {}
    domain = q.get("domain_hint") or "this enquiry"
    domain_label = DOMAIN_LABELS.get(q.get("domain_hint")) or _plain_words(domain)

    entitled = out.get("entitled_tiers") if isinstance(out.get("entitled_tiers"), list) else []
    held = [t for t in ASK_ORDER if t in entitled]
    is_clarification = False

    if len(names) == 0:
        escalate_message = f"You have no access levels configured to get {domain_label}."
    elif len(held) == 0:
        # Entitlement holds no mappable tier: fall back to the legacy compound prompt
        # rather than an empty numbered list. Defensive - tier-gate proceeds in this case.
        escalate_message = (
            f"Please specify which access level you'd like to use for {domain_label}:"
        )
        is_clarification = True
    else:
        # D14 availability annotation, styled like every other picker in the system
        # ("1. CODE - has incoming"), with a plain hyphen. `tier_availability` null means
        # "not determined" and renders exactly as before, with no annotation. The
        # all-empty case never reaches here: `if-tier-has-any` sends it down the answer
        # lane so the customer gets the real not-found instead of a list of dead options.
        # n8n reads `$('tier-probe-collect').tier_availability`. On this lane that value is
        # ALREADY on the item: `tier_probe_collect` writes it and `fetch-result` spreads it
        # through, which is why the by-name read has an item-level equivalent at all. The
        # parameter stays for a caller holding the collect node's own output; unset means
        # "read it off the item", and only an explicit None means "not determined".
        resolved_avail = (
            out.get("tier_availability") if tier_availability is _UNSET else tier_availability
        )
        avail = resolved_avail if jsc.truthy(resolved_avail) else None
        # D16: name the thing being asked about, so the question cannot read as being
        # about nothing. Echo what the customer TYPED, never a canonical code - one raw
        # token can resolve to two products, and naming one would claim we searched
        # something they did not ask for. Past 60 chars the echo is DROPPED rather than
        # truncated, because half a promotion name reads like a different promotion.
        raws: list[str] = []
        for e in jsc.array(q.get("entities")):
            v = jsc.js_string(jsc.get(e, "raw") or "").strip()
            if v and not any(x.lower() == v.lower() for x in raws):
                raws.append(v)
        scope_label = ", ".join(raws[:3])
        if len(scope_label) > 60:
            scope_label = ""
        scope_suffix = f" for {scope_label}" if scope_label else ""
        lines = "\n".join(
            f"{i + 1}. {TIER_DISPLAY[t]}"
            + ("" if avail is None else (" - has promotion" if jsc.get(avail, t) else " - no promotion"))
            for i, t in enumerate(held)
        )
        escalate_message = (
            f"Which access level do you need{scope_suffix}?\n{lines}\n"
            'Reply with the number(s), e.g. "1", "1 and 2", or "all".'
        )
        is_clarification = True
        out["tier_offer"] = True
        out["tier_last_result_set"] = [
            {
                "idx": i + 1,
                "label": TIER_DISPLAY[t],
                "value": t,
                "tier": t,
                "uuid": None,
                "entity_type": "access_tier",
                "product": None,
                "filename": None,
            }
            for i, t in enumerate(held)
        ]

    out["escalate_message"] = escalate_message
    out["is_clarification"] = is_clarification
    # D3: the ask is a numbered TYPED list, never quick-reply buttons.
    out["quick_reply"] = ""
    return out


# --------------------------------------------------------------------------- #
# crossdomain-zeroset / -probe / -render
# --------------------------------------------------------------------------- #

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# The MCP presenter renders an EMPTY field value as an em dash, and both cross-domain
# nodes test for that exact character when they decide whether a product code is real.
# Written as an escape, not the character: the repo forbids an em dash in anything WE
# write, and this is the presenter's literal, not prose. Same treatment as S6b's renderer.
_EMPTY_VALUE = "\u2014"


def _norm_code(value: Any) -> str:
    return jsc.js_string(value).strip().upper()


def _envelope_items(env: Any) -> list[Any]:
    """`answers`, else `items`, else nothing - the CRM envelope has carried both."""
    if isinstance(jsc.get(env, "answers"), list):
        return env["answers"]
    if isinstance(jsc.get(env, "items"), list):
        return env["items"]
    return []


def _field_val(item: Any, label: str) -> Any:
    """`fields.find(f => f.label.trim().toLowerCase() === label)`."""
    field = jsc.find(
        jsc.get(item, "fields") or [],
        lambda x: jsc.js_string(jsc.get(x, "label") or "").strip().lower() == label,
    )
    return jsc.get(field, "value") if jsc.truthy(field) else None


def _field_by_key(it: Any, k: str) -> Any:
    f = jsc.find(jsc.get(it, "fields") or [], lambda x: jsc.has(x, "key") and x["key"] == k)
    return jsc.get(f, "value") if jsc.truthy(f) else None


def _field_pref(it: Any, k: str, *labels: str) -> Any:
    v = _field_by_key(it, k)
    if v is not None:
        return v
    for label in labels:
        lv = _field_val(it, label)
        if lv is not None:
            return lv
    return None


_ON_HAND_WITH_OUTSTANDING = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*\(O/S:")


def _on_hand_number(value: Any) -> Any:
    """The ON-HAND part of a compact Total that already carries its outstanding suffix.

    MEASURED, hand pass 11 defect 1 (live turn cfee5933, contact 437264483 on the
    0921 clone): `_stock_compact` (sorento_crm_mcp/presenters.py) publishes the
    compact per-product total as `{"key": "total_on_hand", "value": 0,
    "granted_value": "0 (O/S: 0)"}` and restricts it behind `inventory.sellable`, and
    `fetch.py::_keep_field` SWAPS the granted string INTO `value` (`f["value"] =
    f.pop("granted_value")`) for a contact that holds the grant. So by the time any
    reader here sees the row, a granted compact total's `value` is the STRING
    `"0 (O/S: 0)"`, not the number `0` - `Number("0 (O/S: 0)")` is NaN, which made
    `_rows_all_zero` below answer False for a reply that plainly read "Total: 0" and
    the cross-domain ladder never probed. The DETAILED row is unaffected (its
    `quantity_on_hand` is never restricted; its outstanding is a separate
    `open_so_qty` field), which is why the ladder has always worked for a detailed
    zero reply and never for a compact granted one.

    The leading number IS the on-hand total in that string, so reading it is the same
    fact the ungranted reader gets from `value` verbatim - not a second definition of
    "on hand". Anything else passes through untouched, so a plain numeric value keeps
    `jsc.js_number`'s own reading exactly.
    """
    if isinstance(value, str):
        m = _ON_HAND_WITH_OUTSTANDING.match(value)
        if m:
            return m.group(1)
    return value


def _row_qty(it: Any) -> float:
    """A row's own quantity, `NaN` when it carries none at all.

    Reads `quantity_on_hand` / "quantity on hand" (the DETAILED stock row's own field)
    first, then `total_on_hand` / "Total" (the COMPACT presenter's per-product total,
    owner ruling 11 Sep 2026, second ruling, R2 fix round) - a compact reply's own
    "Total: 0" is exactly as zero as a detailed row reading 0 at every location. AVAILABLE
    mode carries no quantity field at all, by design (the point of that mode is never
    stating one), so it stays unreachable here on purpose - `value` there is always the
    plain number even under `include_sellable`. The COMPACT total's own "(O/S: n)"
    suffix arrives IN `value` (never as `granted_value`, which the field drop has
    already consumed by now) for a contact holding `inventory.sellable`, so
    `_on_hand_number` above unwraps it back to the on-hand figure first.

    `?? NaN`, not `?? 0`: `fieldPref` returns `None` when every key/label tried is
    ABSENT, and `Number(None)` is 0 in JS - which would make "some row has a quantity"
    true for a row that carries none, sorting an incoming-only reply (ETA rows, no
    quantity field at all) by a phantom zero instead of by ETA. `jsc.js_number` reads the
    same `UNDEFINED` sentinel as `NaN`, matching that JS behaviour exactly.
    """
    value = _field_pref(it, "quantity_on_hand", "quantity on hand")
    if value is None:
        value = _field_pref(it, "total_on_hand", "Total")
    n = jsc.js_number(jsc.UNDEFINED if value is None else _on_hand_number(value))
    return float("nan") if jsc.is_nan(n) else float(n)


def _rows_all_zero(rows: list[Any]) -> bool:
    """Owner ruling 11 Sep 2026, second ruling, R2: at least one row carries a parseable
    quantity, and every parseable quantity reads exactly 0. A row set with no parseable
    quantity at all (an availability-mode row, or an incoming row with no
    `quantity_on_hand` field) is never "zero" - it simply says nothing about quantity."""
    parseable = [q for q in (_row_qty(it) for it in (rows or [])) if not jsc.is_nan(q)]
    return bool(parseable) and all(q == 0 for q in parseable)


def crossdomain_zeroset(
    item: dict[str, Any] | None,
    *,
    parser: dict[str, Any] | None,
    resolved: dict[str, Any] | None,
    session_block: Any = None,
) -> dict[str, Any]:
    """The SINGLE source of truth for "asked but returned nothing".

    `session_block` is `ctx.session` - `get-session-vars`' own response shape - and NEVER a
    database session. The name says so because the lane's own guard scans every public
    signature here for a `db` / `session` parameter, and a read-only lane holding neither
    is the property that guard exists to keep.

    Passes the validator item through UNTOUCHED except for one namespaced key `_xd`, so
    `If6` and the whole miss path see exactly what they see today.
    """
    passthrough = item if isinstance(item, dict) else {}
    out = {**passthrough}

    def off(why: str) -> dict[str, Any]:
        out["_xd"] = {"active": False, "why": why}
        return out

    # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner
    # ruling 24 Sep 2026) for chatbot-stock-ask-v2 S3, D17, review round 9 (finding
    # 7): a DEALER availability reply answers in the dealer's own terms and the
    # verdict line is the whole answer for that product, so the ladder does not run
    # at all when the reply carries an availability block - it must never state OUR
    # stock/incoming/PO figures beside the dealer's own yes/no.
    env_probe: Any = passthrough
    if jsc.truthy(env_probe) and isinstance(jsc.get(env_probe, "output"), dict):
        env_probe = env_probe["output"]
    availability = jsc.get(env_probe, "stock_availability")
    if isinstance(availability, list) and availability:
        return off("stock_availability")
    # PR #1329 fix round: the same for a dealer's incoming reply - the product code and
    # its ETAs are the whole answer, and a dealer is referred to their salesperson. Its
    # lines carry the code in the title, never a field, so the probe below would read
    # every code as missing; the presenter flags each line instead.
    # An EMPTY dealer reply has no line to carry the flag, so the envelope's own
    # `result_type` says it (fix round 2: "No incoming, no stock and nothing on order"
    # and a purchasing offer reached a dealer's miss).
    if jsc.get(env_probe, "result_type") == "incoming_dealer" or any(
        jsc.get(jsc.get(it, "flags"), "dealer_view") is True
        for it in _envelope_items(env_probe)
    ):
        return off("dealer_view")

    qf = parser if isinstance(parser, dict) else {}
    dh = qf.get("domain_hint")
    if dh != "inventory" and dh != "incoming":
        return off("domain")
    if qf.get("message_type") != "business_query":
        return off("message_type")

    other_tool = "crm_inventory_stock_balance_list" if dh == "incoming" else "crm_incoming_stock_list"
    team = "purchasing" if dh == "incoming" else "warehouse"

    # The RETURNED set. The tail's own reader prefers `central-exchange`, which does not
    # exist yet at this point in the chain, so this reads the validator item and
    # REPLICATES that unwrap. Parity is proven by the shadow gate, never assumed.
    env: Any = passthrough
    if jsc.truthy(env) and isinstance(jsc.get(env, "output"), dict):
        env = env["output"]
    items = _envelope_items(env)
    returned_codes: set[str] = set()
    # Owner ruling 11 Sep 2026, second ruling, R2: the PRIMARY reply's own rows, grouped
    # by code, so a stock-origin turn can tell "returned but every row reads 0 on hand"
    # from "genuinely found" below.
    by_code: dict[str, list[Any]] = {}
    for it in items:
        v = _field_val(it, "product code")
        if v is not None and jsc.js_string(v).strip() not in ("", _EMPTY_VALUE):
            code = _norm_code(v)
            returned_codes.add(code)
            by_code.setdefault(code, []).append(it)

    # The REQUESTED set: TYPED-exact UNION DYM-PICKED. Deliberately NOT
    # `compatible_entities`, which drops `match_tier` and carries resolver-expanded
    # siblings the customer never typed.
    rz = resolved if isinstance(resolved, dict) else {}

    def is_prod(m: Any) -> bool:
        return jsc.truthy(m) and jsc.js_string(jsc.get(m, "entity_type")).lower() == "product"

    requested: list[dict[str, Any]] = []
    seen: set[str] = set()

    def uuid_list(u: Any) -> list[Any]:
        if u is None:
            return []
        return [x for x in u if jsc.truthy(x)] if isinstance(u, list) else [u]

    def add(code: Any, uuid: Any, strict: bool) -> None:
        """KEEP EVERY UUID PER CODE, not the first (mc-label).

        One typed code can exist in more than one company, and the resolver returns one
        match per company with the SAME canonical_code. Deduped by CODE, so first-wins
        silently dropped the other company's uuid and the cross-probe then reported that
        company as plainly absent. `uuid` keeps first-add semantics; `uuids` is the union.
        """
        if code is None or code == "":
            return
        n = _norm_code(code)
        if not n:
            return
        us = uuid_list(uuid)
        if n in seen:
            existing = jsc.find(requested, lambda x: x["_n"] == n)
            if jsc.truthy(existing):
                if strict:
                    existing["strict"] = True
                for u in us:
                    if u not in existing["uuids"]:
                        existing["uuids"].append(u)
                # NO backfill of `uuid`: promoting an entry into `probeable` that way
                # started a cross-domain probe that does not run today.
            return
        seen.add(n)
        requested.append(
            # `uuid: us[0] || null` - a FALSY first uuid (an empty string is the only one
            # that reaches here) is stored as null, and `_xd.missing[].uuid` is persisted
            # into turn state, so the emitted value has to be the one live emits.
            {
                "_n": n,
                "code": code,
                "uuid": us[0] if (us and jsc.truthy(us[0])) else None,
                "uuids": list(us),
                "strict": bool(strict),
            }
        )

    or_resolutions = rz.get("resolutions") if isinstance(rz.get("resolutions"), list) else None
    uuid_by_code: dict[str, list[Any]] = {}
    for r in or_resolutions or []:
        for m in jsc.array(jsc.get(r, "matches")):
            if not is_prod(m) or not jsc.truthy(jsc.get(m, "canonical_code")) or not jsc.truthy(
                jsc.get(m, "uuid")
            ):
                continue
            k = _norm_code(jsc.get(m, "canonical_code"))
            bucket = uuid_by_code.setdefault(k, [])
            if jsc.get(m, "uuid") not in bucket:
                bucket.append(jsc.get(m, "uuid"))

    if or_resolutions is not None:
        for r in or_resolutions:
            prods = [m for m in jsc.array(jsc.get(r, "matches")) if is_prod(m)]
            if not prods:
                continue
            exacts = [m for m in prods if jsc.get(m, "match_tier") == "exact"]
            if exacts:
                for m in exacts:
                    if jsc.truthy(jsc.get(m, "canonical_code")):
                        add(jsc.get(m, "canonical_code"), jsc.get(m, "uuid"), False)
            elif len(prods) == 1 and jsc.truthy(jsc.get(prods[0], "canonical_code")):
                add(jsc.get(prods[0], "canonical_code"), jsc.get(prods[0], "uuid"), False)
    else:
        # SEPARATOR-INSENSITIVE, and that is the whole of issue #736. The RESOLVER strips
        # dashes and spaces off a product token before it resolves it, so a customer's
        # "SRTWT7445-LV-NEW" arrives here as the token `SRTWT7445LVNEW` while the match it
        # resolved to carries `canonical_code: "SRTWT7445-LV-NEW"`. `_norm_code` only
        # strips and upper-cases, so the membership test below could never be true for a
        # code with a separator in it: `requested` stayed empty, `missing` with it,
        # `active` came out False, and `run_crossdomain` returned before probing anything.
        # Foundre's rule was therefore OFF for every hyphenated code - most of the
        # catalogue - while it worked for `CB2904`, whose token and canonical code are the
        # same string. Measured on two live turns whose traces are otherwise identical
        # field for field (console-check-1788789839).
        #
        # `_type_norm` is the key the rest of this file already compares these two sides
        # through, and its own docstring names this exact mismatch; this call site was
        # simply the one that did not use it. Applied to BOTH sides of the test only - the
        # `_n` key that reaches persisted state and the `by_code` lookup against the tool's
        # own output still use `_norm_code`, so no emitted value changes shape.
        tokens = {_type_norm(t) for t in jsc.array(rz.get("tokens"))}
        if isinstance(rz.get("intersection"), list):
            intersection: list[Any] = rz["intersection"]
        elif jsc.truthy(rz.get("by_entity_type")):
            intersection = []
            for value in (rz.get("by_entity_type") or {}).values():
                intersection.extend(value if isinstance(value, list) else [value])
        else:
            intersection = []
        # Hand pass 12 round 2: the whole pool's own normalised codes, read once - what
        # `_token_requests` needs to tell "this token names a REAL code exactly" from
        # "this token merely shares a prefix with several", over the SAME intersection
        # a single AND-mode/single-token resolve call returns (M2's own measurement).
        all_norm_codes = {
            _type_norm(jsc.get(m, "canonical_code"))
            for m in intersection
            if is_prod(m) and jsc.truthy(jsc.get(m, "canonical_code"))
        }
        for m in intersection:
            if is_prod(m) and jsc.truthy(jsc.get(m, "canonical_code")) and _token_requests(
                _type_norm(jsc.get(m, "canonical_code")), tokens, all_norm_codes
            ):
                add(jsc.get(m, "canonical_code"), jsc.get(m, "uuid"), False)

    # DYM-PICKED (strict): prior cumulative picks plus this turn's pick.
    variables = jsc.get(jsc.get(session_block, "session_vars"), "variables")
    if not jsc.truthy(variables):
        variables = jsc.get(session_block, "variables")
    dym_offer = jsc.get(variables, "dym_offer") if jsc.truthy(variables) else None
    dym_offer = dym_offer if isinstance(dym_offer, dict) else None
    # DOMAIN GUARD (H22 / H23). The offer records WHOSE picks these are, and a pick made
    # under an ORDER or PROMOTION offer is not a requested PRODUCT: carrying it printed
    # "No stock records found for: CG-202608-051." on an inventory turn (exec 14769923).
    # Picks carry only when the offer's OWN domain is in this feature's stock / incoming
    # family; an offer with no `domain` field - a pre-lifecycle session - keeps today's
    # behaviour. THIS turn's own pick needs no guard: a pick forces the turn into the
    # offer's domain, and the domain gate at the top of this node has already restricted
    # us to inventory / incoming.
    #
    # PROVENANCE, because the two live copies of this node disagree: the guard is in
    # `sub-main-processing-live`'s 151-line body (sha256 fb9d41cf64ea320b, workflow
    # version 53ca1c6b-a6b3-48ed-b094-2cddafb3185c) and NOT in the ACTIVE spine's
    # 143-line one (sha256 a880d01e3629538b, version c9fe3e68-b732-460d-b968-c1b4a5e5f038),
    # which is the body answering turns today. So against the shipping path this is a CRM
    # divergence, registered as `divergences.CROSSDOMAIN_DYM_OFFER_DOMAIN_GUARD` and
    # tested by `TestH22H23DymOfferDomainCleared`, not silent parity.
    offer_domain = jsc.get(dym_offer, "domain") if dym_offer is not None else None
    offer_domain_ok = (
        dym_offer is None
        or not jsc.truthy(offer_domain)
        or offer_domain == "inventory"
        or offer_domain == "incoming"
    )
    prev = (
        jsc.get(dym_offer, "picked")
        if (offer_domain_ok and isinstance(jsc.get(dym_offer, "picked"), list))
        else []
    )
    for c in prev:
        add(c, uuid_by_code.get(_norm_code(c)), True)
    if jsc.truthy(qf.get("dym_offer_pick_code")):
        add(
            qf["dym_offer_pick_code"],
            uuid_by_code.get(_norm_code(qf["dym_offer_pick_code"])),
            True,
        )

    # MISSING: a TYPED code is satisfied by a prefix-family member, a PICKED one only
    # exactly. Deliberately does NOT bail when `returned_codes` is empty - that total-miss
    # case is exactly what this feature exists for.
    missing: list[dict[str, Any]] = []
    for rq in requested:
        if rq["strict"]:
            matched_codes = [rq["_n"]] if rq["_n"] in returned_codes else []
        else:
            matched_codes = [rc for rc in returned_codes if rc == rq["_n"] or rc.startswith(rq["_n"])]
        ok = bool(matched_codes)
        if ok:
            # Owner ruling 11 Sep 2026, second ruling, R2: stock-origin only (`dh ==
            # "inventory"`) - a code the primary reply DID return, but whose own rows all
            # read 0 on hand, is not genuinely "found"; it becomes a `missing` entry too
            # (keys otherwise as a normal miss, `zero: True` added), so the ladder still
            # probes for it while it stays in `returned_codes` (the primary render did
            # echo it, and still does).
            if dh == "inventory":
                matched_rows = [it for rc in matched_codes for it in by_code.get(rc, [])]
                if _rows_all_zero(matched_rows):
                    miss = {
                        "code": rq["code"], "uuid": rq["uuid"], "_n": rq["_n"],
                        "entity_type": "product", "zero": True,
                    }
                    if len(rq["uuids"]) > 1:
                        miss["uuids"] = list(rq["uuids"])
                    missing.append(miss)
            continue
        miss = {"code": rq["code"], "uuid": rq["uuid"], "_n": rq["_n"], "entity_type": "product"}
        # Added ONLY when the code really spans companies, so a single-company turn's
        # `_xd.missing` keeps exactly the keys it has today.
        if len(rq["uuids"]) > 1:
            miss["uuids"] = list(rq["uuids"])
        missing.append(miss)
    probeable = [m for m in missing if jsc.truthy(m["uuid"])]

    probe_entities: list[dict[str, Any]] = []
    for m in probeable:
        us = m["uuids"] if isinstance(m.get("uuids"), list) and m["uuids"] else [m["uuid"]]
        probe_entities.extend(
            {"uuid": u, "entity_type": "product", "code": m["code"]} for u in us
        )

    out["_xd"] = {
        "active": len(probeable) > 0,
        "origin_domain": dh,
        "other_tool": other_tool,
        "team": team,
        "requested": [r["code"] for r in requested],
        "returned_codes": list(returned_codes),
        "missing": missing,
        "probe_entities": probe_entities,
    }
    return out


def crossdomain_probe_args(
    zeroset: dict[str, Any] | None,
    *,
    parser: dict[str, Any] | None,
    entities_names: Any,
    contact_id: Any,
    space_id: Any = None,
    granted: Any = None,
) -> dict[str, Any]:
    """`crossdomain-probe`'s `sub-get-results` inputs, key for key.

    `access_levels` is the SORTED entitlement intersected with what the parser stated, and
    only when the entitlement read ran at all - otherwise the parser's own list, unchanged.

    `space_id` goes through the SAME fallback the fetch and the did-you-mean probes use
    (`fetch.space_id_or_default`), so an install with no default respond workspace row
    cannot have this probe send `null` while the other three send n8n's literal.

    `granted` (owner ruling 11 Sep 2026, second ruling, R1): the contact's granted
    field-reveal keys, same set `_apply_crossdomain_rung` already reads. A non-empty
    list/tuple/set stamps `"access": {"attributes": [...]}` on the args, which
    `entity_ids_transformer` (`fetch.py`) reads to set `include_sellable` - so the FIRST
    cross-domain probe's stock rows carry Outstanding exactly like a direct stock ask.
    Omitted entirely when `granted` is empty or not one of those types, so every existing
    call site's args stay byte-identical.
    """
    xd = zeroset if isinstance(zeroset, dict) else {}
    qf = parser if isinstance(parser, dict) else {}
    parser_levels = qf.get("access_levels") if isinstance(qf.get("access_levels"), list) else []
    if entities_names is not None:
        names = sorted(jsc.array(entities_names), key=jsc.js_string)
        access_levels = [a for a in names if a in parser_levels]
    else:
        access_levels = list(parser_levels)
    codes = ", ".join(jsc.js_string(jsc.get(e, "code")) for e in jsc.array(xd.get("probe_entities")))
    args: dict[str, Any] = {
        "tool": xd.get("other_tool"),
        "contact_id": contact_id,
        "entities": xd.get("probe_entities"),
        "semantic_input": {
            "message_type": qf.get("message_type") if qf.get("message_type") is not None else None,
            "intent_hint": qf.get("intent_hint") if qf.get("intent_hint") is not None else None,
            "domain_hint": qf.get("domain_hint") if qf.get("domain_hint") is not None else None,
            "user_goal": qf.get("user_goal") if qf.get("user_goal") is not None else None,
            "access_levels": access_levels,
            "contact_id": jsc.js_string(contact_id) if contact_id is not None else None,
            "space_id": space_id_or_default(space_id),
            "is_active": qf.get("is_active") if qf.get("is_active") is not None else None,
        },
        "user_prompt": (
            f"cross-domain probe ({jsc.js_string(xd.get('origin_domain'))} -> "
            f"{jsc.js_string(xd.get('other_tool'))}) for: {codes}"
        ),
    }
    if isinstance(granted, (list, tuple, set, frozenset)) and len(granted) > 0:
        args["access"] = {"attributes": list(granted)}
    return args


def _fmt_xd_value(v: Any) -> str:
    """`crossdomain-render`'s own `fmtValue` - a PLAIN hyphen, and date-only timestamps."""
    if v is None or v == "":
        return "-"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str) and _ISO_RE.match(v):  # D11-reproduced: crossdomain-render's ISO_RE
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)  # D11-reproduced: same node's Date read
        if match:
            return f"{match.group(3)}/{match.group(2)}/{match.group(1)}"
    if isinstance(v, list):
        return ", ".join(_fmt_xd_value(x) for x in v)
    return jsc.js_string(v)


def _field_render_value(f: Any) -> Any:
    """`granted_value` when a field carries one, else `value` - owner ruling 11 Sep 2026,
    second ruling (R1 fix round). The compact stock presenter puts a contact's "(O/S: n)"
    Outstanding suffix on `granted_value`, never on `value` (`_stock_compact`,
    sorento_crm_mcp/presenters.py), because that field is normally RESTRICTED and an
    ungranted caller of the tool directly is meant to read the plain `value` instead.
    This render has no field drop of its own to apply that restriction selectively, so it
    is safe here ONLY because `granted_value` never reaches a row unless the SAME probe
    already asked `include_sellable` - which `crossdomain_probe_args` (R1) only ever does
    for a contact that holds the grant. Any future `include_*` added to
    `crossdomain_probe_args` must gate on the grant the same way, or a value meant for one
    contact could render here for another through this otherwise-ungated path.
    """
    v = jsc.get(f, "granted_value")
    return v if v is not None else jsc.get(f, "value")


def crossdomain_render(
    probe_result: dict[str, Any] | None,
    *,
    zeroset: dict[str, Any] | None,
    validator: dict[str, Any] | None,
) -> dict[str, Any]:
    """Render the other domain's rows under the miss, POSITIVE FACTS ONLY.

    A missing code with no probed row is simply not mentioned: saying nothing beats
    asserting an absence the probe did not establish.
    """
    zs = zeroset if isinstance(zeroset, dict) else {}
    passthrough = validator if isinstance(validator, dict) else {}
    out: dict[str, Any] = {**passthrough, "_xd": zs}

    env: Any = probe_result if jsc.truthy(probe_result) else {}
    if jsc.truthy(env) and isinstance(jsc.get(env, "output"), dict):
        env = env["output"]
    has_envelope = (
        isinstance(jsc.get(env, "answers"), list)
        or isinstance(jsc.get(env, "items"), list)
        or isinstance(jsc.get(env, "has_result"), bool)
    )
    if not has_envelope or jsc.truthy(jsc.get(env, "error")):
        out["_xdBlock"] = {
            "block": "",
            "any": False,
            "degraded": True,
            "reason": "probe_error" if jsc.truthy(jsc.get(env, "error")) else "no_envelope",
        }
        return out

    items = _envelope_items(env)

    by_code: dict[str, list[Any]] = {}
    for it in items:
        c = jsc.nullish_str(_field_val(it, "product code")).strip()
        if not c or c == _EMPTY_VALUE:
            continue
        by_code.setdefault(c.upper(), []).append(it)

    # Owner ruling 11 Sep 2026, second ruling, R2: needed inside the missing-loop below,
    # not only for the sentence built after it.
    origin_incoming = zs.get("origin_domain") == "incoming"

    blocks: list[str] = []
    # Hand pass 11 defect 1 (owner retest, live turn 27f60a71): a probe row is printed
    # ONCE per block, however many `missing` entries claim it. The zero-entry lookup
    # below deliberately matches a prefixed sibling's rows (finding 7, so a typed family
    # prefix still finds its members on the other side), so for "check stock srtwc6022"
    # the ONE incoming row the probe returned - SRTWC6022-SH-UF-NEW, container
    # IAAU1697450 - was collected by SRTWC6022-SH-UF's entry AND by its own, and the
    # customer read the same container twice off a tool envelope holding one item.
    # Identity, not rendered text: `by_code` holds the probe's own row dicts, so the
    # same row reached through two entries is the same object, while two genuinely
    # different rows that happen to render alike stay two rows.
    seen_rows: set[int] = set()
    # Codes that came back empty on BOTH sides. Owner ruling (6 Sep 2026): name them and
    # offer an escalation, rather than dropping them so the reply lists only the codes that
    # had something to show. "Positive facts only" still holds for a code the probe never
    # ASKED about - one with no uuid was never probed, so "no incoming" would be an absence
    # nothing established - so only a PROBED code earns the negative line.
    nothing: list[str] = []
    # A7: the FULL `missing` entries behind `nothing` (code, uuid, uuids) - kept alongside
    # the label list so `run_crossdomain` can build the next ladder rung's probe entities
    # without re-deriving which codes qualify.
    nothing_missing: list[dict[str, Any]] = []
    # Owner console pass 4, item G (6 Sep 2026): codes the OTHER domain answered, which the
    # primary one did not. Turn 858c9c54 named MSK11A-QT only inside "But there is INCOMING
    # stock (ETA) ...", so a stock question came back as two codes' stock and then an
    # incoming fact about a third, leaving the customer to infer the thing they had asked.
    # Say it, and say it above the incoming lead. Same evidence and same guard as `nothing`
    # below it - this is the only place that knows both that the primary render did not echo
    # the code and that the other domain was actually probed for it.
    only_other: list[str] = []
    for m in jsc.array(zs.get("missing")):
        n = jsc.get(m, "_n")
        zero = jsc.truthy(jsc.get(m, "zero"))
        # Finding 7 (11 Sep 2026, second ruling fix round): a ZERO-flagged entry's rows
        # are looked up the SAME way `crossdomain_zeroset` matched it in the first place -
        # every `by_code` key equal to OR prefixed by `_n`, not the exact key alone (a
        # typed prefix code, e.g. "SRTWC8517" flagged zero from its own "-PJ" sibling's
        # rows, must find that SAME sibling's rows on the OTHER side too). A plain entry
        # keeps the exact lookup - pre-existing, untouched.
        if zero:
            rows = [it for code_key, its in by_code.items() if code_key == n or code_key.startswith(n) for it in its]
        else:
            rows = list(by_code.get(n, []))
        if not rows:
            code = jsc.get(m, "code") or jsc.get(m, "_n")
            if jsc.truthy(jsc.get(m, "uuid")) and jsc.truthy(code) and not _ms_is_uuid(code):
                label = jsc.js_string(code)
                if label not in nothing:
                    nothing.append(label)
                    # Nit 11: copy, same as the R2(b) branch below - `m` is still `zs`'s
                    # own entry, and a caller here must not mutate it by aliasing.
                    nothing_missing.append(dict(m))
            continue
        code = jsc.get(m, "code") or jsc.get(m, "_n")
        # Owner ruling 11 Sep 2026, second ruling, R2(b)/nit 14 (fix round): incoming-
        # origin only - the OTHER domain (stock) DID answer, but every row reads 0 on
        # hand, which is not really "found" either. The rows still render below; the code
        # ALSO climbs, same as a genuine miss, stamped on a COPY so the original `missing`
        # entry (still `zs`'s own) is untouched. A zero code (either kind) never earns
        # AC-820's own "no {primary} for X" only-other line either way - the zero sentence
        # two paragraphs later already says the same thing, so printing both would be a
        # duplicate.
        if origin_incoming and not zero and _rows_all_zero(rows):
            zero = True
            if jsc.truthy(jsc.get(m, "uuid")) and jsc.truthy(code) and not _ms_is_uuid(code):
                label = jsc.js_string(code)
                if label not in nothing:
                    nothing.append(label)
                    nothing_missing.append({**m, "zero": True})
        if not zero and jsc.truthy(code) and not _ms_is_uuid(code):
            label = jsc.js_string(code)
            if label not in only_other:
                only_other.append(label)

        def eta(it: Any) -> str:
            return jsc.nullish_str(
                _field_pref(it, "estimated_arrival_date", "eta", "estimated arrival date")
            )

        if any(not jsc.is_nan(_row_qty(it)) for it in rows):
            rows.sort(key=lambda it: -(0 if jsc.is_nan(_row_qty(it)) else _row_qty(it)))
        elif any(eta(it) for it in rows):
            rows.sort(key=eta)
        for it in rows:
            if id(it) in seen_rows:
                continue
            field_lines = "\n".join(
                f"*{jsc.get(f, 'label')}:* {_fmt_xd_value(_field_render_value(f))}"
                for f in (jsc.get(it, "fields") or [])
            )
            if not field_lines:
                continue
            line = f"- {field_lines}"
            flags = jsc.get(it, "flags") or {}
            if jsc.truthy(jsc.get(flags, "discontinued")):
                line += "\n⚠️  *(PRODUCT DISCONTINUED)*"
            if jsc.truthy(jsc.get(flags, "expired")):
                line += "\n⚠️  *(PROMO EXPIRED)*"
            if jsc.truthy(jsc.get(flags, "unallocated")):
                line += "\n\U0001f6a9  *(PENDING ALLOCATION)*"
            elif jsc.truthy(jsc.get(flags, "partially_allocated")):
                line += "\n\U0001f6a9  *(PARTIAL ALLOCATION)*"
            blocks.append(line)
            seen_rows.add(id(it))

    lead = (
        "But here are the stock details for the requested products:"
        if zs.get("origin_domain") == "incoming"
        else "But there is INCOMING stock (ETA) for the requested products:"
    )
    # D2 (12 Sep 2026 owner finding): no "I have attached the file(s) below." sentence.
    # `compose.crossdomain_compose` folds `block["block"]` (TEXT ONLY) into the reply, and
    # the send lane reads `envelope.attachments` off the PRIMARY answer alone
    # (`engine._attachments_src`) - the cross-domain probe's own envelope never reaches a
    # send, so the sentence was never true. `xd_files` still feeds `_xdBlock["attachments"]`
    # below - review fix round: NOT dropped after all (see the reviewer note below the
    # `_xdBlock` literal) - the key is graded byte-for-byte by the crossdomain-render
    # corpus replay (`test_s6c_answer_lane.py`/`test_s6c_engine_paths.py`, six registered
    # captures), so removing it turns 12 green replays red; only the claim that the files
    # were SENT is gone.
    xd_files = env["attachments"] if isinstance(jsc.get(env, "attachments"), list) else []

    silent_note = ""
    lookup_cos = (
        env["lookup_companies"] if isinstance(jsc.get(env, "lookup_companies"), list) else []
    )
    if blocks and len(lookup_cos) > 1:
        shown = {
            c
            for c in (jsc.nullish_str(_field_val(it, "company")).strip() for it in items)
            if c
        }
        silent = [
            n
            for n in (jsc.nullish_str(jsc.get(c, "name")).strip() for c in lookup_cos)
            if n and n not in shown
        ]
        if shown and silent:
            codes = list(
                dict.fromkeys(
                    c
                    for c in (
                        jsc.nullish_str(_field_val(it, "product code")).strip() for it in items
                    )
                    if c
                )
            )
            what = ("stock" if zs.get("origin_domain") == "incoming" else "incoming") + " records" + (
                f" for {', '.join(codes)}" if codes else ""
            )
            silent_note = "\n\n" + "\n".join(f"*{n}:* no {what}." for n in silent)

    # Same shape as `silent_note` above: a trailing paragraph on the same block, so one
    # message carries both what WAS found and what was not.
    # `missing` means "the PRIMARY render did not echo this code", and that is only the same
    # statement as "this code has nothing" when the render is product-keyed (some row named
    # a product code) or when it came back empty altogether. A warehouse breakdown and a
    # demand-quantity verdict both answer ABOUT the code without ever printing it, and
    # "no stock" underneath the stock just printed is a worse defect than the silence this
    # note exists to fix.
    named_codes = [c for c in jsc.array(zs.get("returned_codes")) if jsc.truthy(c)]
    can_state_absence = bool(named_codes) or jsc.get(passthrough, "has_result") is not True

    primary_word = "incoming" if origin_incoming else "stock"
    other_word = "stock" if origin_incoming else "incoming"

    # The one-sided line: the primary domain has nothing for these codes, and the block
    # below is about to say what the OTHER one has. No escalation offer - something IS
    # being shown - and `can_state_absence` gates it exactly as it gates the both-empty
    # sentence, so a render that answered ABOUT the code without printing it (a warehouse
    # breakdown, a demand verdict) never gets "no stock" underneath the stock it just showed.
    only_other_note = ""
    if only_other and can_state_absence:
        only_other_note = f"No {primary_word} for {', '.join(only_other)}."

    # NO OFFER SENTENCE HERE (8 Sep 2026, turns 0184d84d / 5f73ddb0 / 90a1637a): the block
    # used to end "...Would you like me to escalate to X team?" and `tail/compose.
    # crossdomain_compose` appended the LOCKED phrase again from `block["team"]`, so the
    # customer read the question twice. Compose is the one writer of the offer, on the
    # partial-answer branch from `team` below and on the total-miss branch from the miss
    # sentence it slots this block above; this render only states what is absent.
    #
    # Owner ruling 11 Sep 2026, second ruling, R2(d): `nothing` splits into a PLAIN group
    # (genuinely absent on both sides) and a ZERO group (stock reads 0 everywhere) so each
    # gets its own wording - "no stock" is not honest about a code that DOES have a row,
    # just not one with anything on it. `zero_labels`/`plain_labels` are parallel to
    # `nothing`/`nothing_missing` (same append order, same length), so a zip is enough.
    zero_labels = [c for c, m in zip(nothing, nothing_missing) if jsc.truthy(jsc.get(m, "zero"))]
    plain_labels = [c for c, m in zip(nothing, nothing_missing) if not jsc.truthy(jsc.get(m, "zero"))]
    nothing_note = ""
    if can_state_absence:
        sentences: list[str] = []
        if plain_labels:
            sentences.append(f"No {primary_word} and no {other_word} for {', '.join(plain_labels)}.")
        if zero_labels:
            sentences.append(
                f"No incoming and stock is 0 at every location for {', '.join(zero_labels)}."
                if origin_incoming
                else f"Stock is 0 at every location and no incoming for {', '.join(zero_labels)}."
            )
        nothing_note = " ".join(sentences)

    body = (lead + "\n\n" + "\n\n".join(blocks) + silent_note) if blocks else ""
    if body and only_other_note:
        body = f"{only_other_note}\n\n{body}"
    if nothing_note:
        body = f"{body}\n\n{nothing_note}" if body else nothing_note

    out["_xdBlock"] = {
        "block": body,
        "any": bool(blocks) or bool(nothing_note),
        "attachments": xd_files,
        "team": zs.get("team") or None,
        "origin": zs.get("origin_domain") or None,
        "probed_rows": len(items),
        "rendered_rows": len(blocks),
        # A7: the codes with NOTHING on either side, and the sentence built for them - so
        # `run_crossdomain` can try a NEXT ladder rung (e.g. purchase_order) for exactly
        # these codes and, if that rung answers, swap this sentence for its own without
        # re-deriving which codes it is even about. Additive - nothing here reads them yet
        # when the ladder has no further rung, so this render's own wording is unchanged.
        #
        # GATED ON `can_state_absence`, exactly as `nothing_note` and `only_other_note`
        # are, and the first cut of A7 was not (review, blocker 2). "Missing" means the
        # PRIMARY render did not ECHO the code, which is only the same statement as "this
        # code has nothing" when the render is product-keyed or empty. A warehouse
        # breakdown answers about the code without ever printing it, so an ungated list
        # let the ladder append "No stock and no incoming for X, but a PO is placed"
        # underneath the stock it had just shown - the exact defect `can_state_absence`
        # exists to prevent, reintroduced one rung further along. Empty here means the
        # rung never runs, which is the right answer: there is nothing we can honestly
        # say is absent.
        "nothing_codes": list(nothing) if can_state_absence else [],
        "nothing_note": nothing_note,
        # Same gate, same reason: the rung reads this to build its probe entities, so
        # leaving it populated while `nothing_codes` is empty would only invite the
        # next reader to make the mistake again.
        "nothing_missing": list(nothing_missing) if can_state_absence else [],
        # Owner ruling 11 Sep 2026, second ruling, R2(c): which of `nothing_codes` were
        # zero-at-every-location rather than genuinely absent - same gate, so a reader of
        # `nothing_codes` that ignores this key still sees exactly today's list.
        "zero_codes": list(zero_labels) if can_state_absence else [],
    }
    return out


#: A7: rungs beyond the hard-coded inventory<->incoming pair. Keyed by the rung NAME as it
#: appears in `system_settings.chatbot_crossdomain_ladder` (a JSON list of strings, admin
#: editable) - "incoming" is not here because that rung is the EXISTING hard probe above,
#: never a second lookup. Only "purchase_order" exists today; a ladder entry naming
#: anything else is simply never reached (no tool to call), which is the same "widen only
#: with an entry" shape `_CHATBOT_COLUMN_DEFAULTS` uses elsewhere.
_CROSSDOMAIN_RUNG_TOOL: dict[str, str] = {"purchase_order": "crm_procurement_po_placed_list"}
#: Item 5 (8 Sep 2026): the rung's tool returns PO lines AND unshipped SPO allocations
#: (`kind` "po" / "spo", carried on the item's own top-level `kind` key since the 11 Sep
#: 2026 ruling - no rendered Source field), so its sentences speak of what is ON ORDER
#: rather than of a document type - `_CROSSDOMAIN_RUNG_WORD` ("no PO for X") went with
#: that.
#: The field-reveal key a contact must hold for the rung to run at all (8 Sep 2026). A rung
#: with no row here is ungated.
_CROSSDOMAIN_RUNG_GRANT: dict[str, str] = {"purchase_order": "purchase_orders.placed"}
#: PLAN-chatbot-last-purchase-cost.md D6: the WHOLE `purchase_cost` domain is refused
#: without this grant, checked in `run_fetch` before any tool call - a PO row without
#: its cost is a different answer from the one asked, so a per-field drop is not enough
#: (that drop still runs too, belt and braces: AC-18). A domain with no row here is
#: ungated, same convention as `_CROSSDOMAIN_RUNG_GRANT` above.
DOMAIN_GRANT_REQUIRED: dict[str, str] = {"purchase_cost": "purchase_orders.cost"}
#: What the refusal CALLS the domain it just refused, for the one registered
#: `access_denied` template (`canned.field_grant_denied_text`). A plain-language FEATURE
#: name, not the domain's own label ("last purchase cost") and not the parser's agent
#: guess: the customer asked for a thing, and the sentence names that thing. Beside
#: `DOMAIN_GRANT_REQUIRED` because it is the same per-domain fact wearing its other half,
#: and it is the wording `complete_answer` used to hand out before the turn package
#: became the one that answers.
DOMAIN_GRANT_SUBJECT: dict[str, str] = {"purchase_cost": "purchase cost"}
#: The shipped ladder (migration 491, D7): stock -> incoming -> PO from either side. The
#: DATABASE row is where the default lives; `engine._crossdomain_ladder` hands this out
#: only for a settings row that carries no usable ladder (a `create_all` schema), never
#: for a direct `run_crossdomain` call with none (H52 keeps that the pre-A7 single pair).
DEFAULT_CROSSDOMAIN_LADDER: dict[str, list[str]] = {
    "inventory": ["incoming", "purchase_order"],
    "incoming": ["inventory", "purchase_order"],
}


def _next_crossdomain_rung(origin_domain: Any, *, ladder: dict[str, list[str]] | None) -> str | None:
    """The rung AFTER the hard-coded inventory<->incoming probe, or None.

    `ladder` is `None` when the caller passed none (H52: no ladder configured = the
    pre-A7 single hard pair, unchanged - `TestCrossdomainProbe::
    test_zeroset_active_triggers_exactly_one_probe...` pins this for a direct
    `run_crossdomain` call with no `crossdomain_ladder` argument). In production
    `engine._crossdomain_ladder` reads the REAL row, which carries the shipped default
    (migration 489) the moment a settings row exists - so this function owns no default
    of its own; the DATABASE row is the one place the default lives.

    `ladder[origin][0]` is always the domain the hard probe above already asked (AC-923: a
    tenant configuring `{"inventory": ["incoming"]}` has no second entry, so this returns
    None and the PO rung never runs). Only the first name after it is tried - one further
    rung per turn, same as the existing probe.
    """
    if not isinstance(ladder, dict):
        return None
    rungs = ladder.get(jsc.js_string(origin_domain))
    if not isinstance(rungs, list) or len(rungs) < 2:
        return None
    for name in rungs[1:]:
        if name in _CROSSDOMAIN_RUNG_TOOL:
            return name
    return None


def _crossdomain_rung_probe_args(
    missing: list[dict[str, Any]],
    *,
    rung: str,
    parser: dict[str, Any] | None,
    contact_id: Any,
    space_id: Any,
) -> dict[str, Any]:
    """Same shape as `crossdomain_probe_args`, over the codes still `nothing` after the
    first rung - never the full `missing` set, so a code the incoming probe already
    answered is not re-asked about."""
    qf = parser if isinstance(parser, dict) else {}
    entities: list[dict[str, Any]] = []
    for m in missing:
        us = m["uuids"] if isinstance(m.get("uuids"), list) and m["uuids"] else (
            [m["uuid"]] if jsc.truthy(m.get("uuid")) else []
        )
        entities.extend({"uuid": u, "entity_type": "product", "code": m.get("code")} for u in us)
    codes = ", ".join(jsc.js_string(m.get("code")) for m in missing)
    tool = _CROSSDOMAIN_RUNG_TOOL[rung]
    return {
        "tool": tool,
        "contact_id": contact_id,
        "entities": entities,
        "semantic_input": {
            "message_type": qf.get("message_type") if qf.get("message_type") is not None else None,
            "intent_hint": qf.get("intent_hint") if qf.get("intent_hint") is not None else None,
            "domain_hint": qf.get("domain_hint") if qf.get("domain_hint") is not None else None,
            "user_goal": qf.get("user_goal") if qf.get("user_goal") is not None else None,
            "contact_id": jsc.js_string(contact_id) if contact_id is not None else None,
            "space_id": space_id_or_default(space_id),
        },
        "user_prompt": f"cross-domain probe (crossdomain -> {rung}) for: {codes}",
    }


def _crossdomain_rung_rows(
    probe_result: Any, *, missing: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Which of `missing`'s codes the rung answered, and the rung row per row.

    Returns `{CODE: [row, ...]}` - only codes the rung actually found rows for, each row
    `kind` "po" or "spo" (item 5; a row with no top-level `kind` key is a PO row, today's
    shape - owner ruling 11 Sep 2026). Never renders `supplier`: the field template
    simply does not name it, which is what keeps a dealer from ever seeing it here
    without threading the field-reveal grant into this probe.
    """
    env: Any = probe_result if jsc.truthy(probe_result) else {}
    if jsc.truthy(env) and isinstance(jsc.get(env, "output"), dict):
        env = env["output"]
    items = _envelope_items(env)
    by_code: dict[str, list[Any]] = {}
    for it in items:
        c = jsc.nullish_str(_field_val(it, "product code")).strip()
        if not c or c == _EMPTY_VALUE:
            continue
        by_code.setdefault(c.upper(), []).append(it)

    def field_by_key(it: Any, k: str) -> Any:
        f = jsc.find(jsc.get(it, "fields") or [], lambda x: jsc.has(x, "key") and x["key"] == k)
        return jsc.get(f, "value") if jsc.truthy(f) else None

    out: dict[str, list[dict[str, Any]]] = {}
    for m in missing:
        code = jsc.js_string(m.get("code") or m.get("_n"))
        rows = by_code.get(code.upper(), [])
        if not rows:
            continue
        out[code] = [_crossdomain_rung_row(it, field_by_key) for it in rows]
    return out


def _crossdomain_rung_row(it: Any, field_by_key: Any) -> dict[str, Any]:
    """One rung row as `{kind, number, product_code, ordered_qty, qty, po_date,
    location}` - `kind` "po" or "spo". Owner ruling (11 Sep 2026): `kind` reads ONLY the
    ITEM's own top-level key (the presenter no longer renders a Source field at all, so
    there is nothing left to fall back to); a row with no top-level `kind` is a PO row,
    today's shape. Rendering is `_crossdomain_rung_text`, which lays out one field per
    line per row."""
    kind = "spo" if jsc.js_string(jsc.get(it, "kind") or "").strip().upper() == "SPO" else "po"
    return {
        "kind": kind,
        "number": field_by_key(it, "po_number"),
        "product_code": field_by_key(it, "product_code"),
        "ordered_qty": field_by_key(it, "ordered_qty"),
        "qty": field_by_key(it, "outstanding_qty"),
        "po_date": field_by_key(it, "po_date"),
        "location": field_by_key(it, "location"),
    }


def _crossdomain_rung_text(rows: list[dict[str, Any]]) -> str:
    """D3 (12 Sep 2026 owner finding): one field per line per row - `*Product Code:*`,
    `*Ordered:*`, `*Outstanding:*`, `*PO date:*`, `*Location:*`, bold labels like every
    other field line in the reply - rows separated by ONE blank line. A null/empty
    `ordered_qty`, `po_date` or `location` OMITS that line entirely (never a placeholder,
    never `_fmt_xd_value`'s own "-"); `Product Code` and `Outstanding` always print. No
    per-document heading naming the PO/SPO number, and no "pcs":

        *Product Code:* SRTWC191-G3
        *Ordered:* 30
        *Outstanding:* 30
        *PO date:* 2026-08-10
        *Location:* KL-WH
    """
    blocks: list[str] = []
    for row in rows:
        lines = [f"*Product Code:* {_fmt_xd_value(row.get('product_code'))}"]
        ordered_qty = row.get("ordered_qty")
        if ordered_qty not in (None, ""):
            lines.append(f"*Ordered:* {_fmt_xd_value(ordered_qty)}")
        lines.append(f"*Outstanding:* {_fmt_xd_value(row.get('qty'))}")
        po_date = row.get("po_date")
        if po_date not in (None, ""):
            lines.append(f"*PO date:* {_fmt_xd_value(po_date)}")
        location = row.get("location")
        if location not in (None, ""):
            lines.append(f"*Location:* {_fmt_xd_value(location)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _apply_crossdomain_rung(
    render: dict[str, Any],
    *,
    xd: dict[str, Any],
    parser: dict[str, Any] | None,
    services: Any,
    contact_id: Any,
    space_id: Any,
    ladder: dict[str, list[str]] | None,
    trace: Any = None,
    granted: Any = None,
) -> None:
    """Mutates `render["_xdBlock"]` in place: tries the ladder's next rung for the codes
    the first probe found NOTHING for, and swaps the "no X and no Y" sentence for the
    rung's own wording when it answers (AC-921/AC-922).

    A no-op (H62/AC-924 kept byte-identical) when: the origin has no further rung
    (AC-923), or the first probe found something for every requested code
    (`nothing_codes` empty), or the rung probe itself fails - the SAME degrade-not-crash
    contract the first probe already has.
    """
    block = render.get("_xdBlock") if isinstance(render, dict) else None
    if not isinstance(block, dict):
        return
    nothing_codes = block.get("nothing_codes") or []
    if not nothing_codes:
        return
    rung = _next_crossdomain_rung(xd.get("origin_domain"), ladder=ladder)
    if rung is None:
        return
    # PER-CONTACT GATE (8 Sep 2026): on-order information is a field reveal, key
    # `purchase_orders.placed`, granted on Contacts > Access. `granted` is the contact's
    # granted key list (`ctx["access"]["attributes"]`, the same set `fetch.py`'s field drop
    # reads; None is the empty set, as there). Without the grant the rung does not run
    # at all - no probe, no PO lines - and the block stays the ladder-off shape. The
    # DIRECT PO ask is not gated by this key; it keeps its supplier-only gating.
    need = _CROSSDOMAIN_RUNG_GRANT.get(rung)
    granted_set = set(granted) if isinstance(granted, (list, tuple, set, frozenset)) else set()
    if need and need not in granted_set:
        if trace is not None:
            trace.add("crossdomain", {"rung": rung, "skipped": "not_granted", "needs": need})
        return
    missing = block.get("nothing_missing") or []
    args = _crossdomain_rung_probe_args(
        missing, rung=rung, parser=parser, contact_id=contact_id, space_id=space_id
    )
    try:
        probe_result = services.mcp_probe(args["tool"], args)
    except Exception:  # noqa: BLE001 - degrades to the existing nothing_note, never a dead turn
        logger.warning("chatbot: cross-domain %s rung probe did not run", rung, exc_info=True)
        return
    lines_by_code = _crossdomain_rung_rows(
        probe_result if isinstance(probe_result, dict) else {}, missing=missing
    )
    # D7: the wording follows the customer's own climb - from an incoming ask the first
    # absence is "incoming", then "stock"; from a stock ask the reverse.
    origin_incoming = xd.get("origin_domain") == "incoming"
    first_word, second_word = ("incoming", "stock") if origin_incoming else ("stock", "incoming")

    # Owner ruling 11 Sep 2026, second ruling, R2: `nothing_codes` splits by the SAME
    # `zero` flag `crossdomain_render` stamped on the parallel `nothing_missing` list, so
    # each group earns its own wording - a zero-everywhere code was never really "found",
    # but "no stock" is not honest about a code that DOES have a row, just not one with
    # anything on it.
    zero_by_code = {
        jsc.js_string(jsc.get(m, "code") or jsc.get(m, "_n")): jsc.truthy(jsc.get(m, "zero"))
        for m in missing
    }
    plain_codes = [c for c in nothing_codes if not zero_by_code.get(c)]
    zero_codes = [c for c in nothing_codes if zero_by_code.get(c)]

    def _group_parts(codes: list[str], *, lead: str, trail: str) -> list[str]:
        """One group's paragraph(s): a found sentence (`{lead} and {trail} for X,
        {header}:` plus the rung's own block) when the rung answered any of `codes`, a
        still-nothing sentence (`{lead}, {trail} and nothing on order for X.`) for the
        rest - AC-922's wording, one step further than the first probe's "no X and no Y".
        Item 5: "nothing on order" - PO lines and unshipped SPO allocations alike."""
        if not codes:
            return []
        found = [c for c in codes if c in lines_by_code]
        still_nothing = [c for c in codes if c not in lines_by_code]
        parts: list[str] = []
        if found:
            found_rows = [row for c in found for row in lines_by_code[c]]
            po_lines = _crossdomain_rung_text(found_rows)
            # The header names what the rows ARE: "PO is placed" (D2: no article, the
            # owner's wording) when any row is a PO line, "stock is on order from the
            # supplier" when every row is an unshipped SPO allocation (item 5).
            header = (
                "but stock is on order from the supplier"
                if found_rows and all(r.get("kind") == "spo" for r in found_rows)
                else "but PO is placed"
            )
            parts.append(f"{lead} and {trail} for {', '.join(found)}, {header}:\n{po_lines}")
        if still_nothing:
            parts.append(f"{lead}, {trail} and nothing on order for {', '.join(still_nothing)}.")
        return parts

    # D7's own pair, for the plain group - "No incoming and no stock" from an incoming
    # ask. The zero group always names "stock is 0 at every location" for the stock half
    # and "no incoming"/"No incoming" for the other, in the SAME lead/trail order D7 gives
    # the plain group.
    zero_lead, zero_trail = (
        ("No incoming", "stock is 0 at every location")
        if origin_incoming
        else ("Stock is 0 at every location", "no incoming")
    )
    parts = _group_parts(plain_codes, lead=f"No {first_word}", trail=f"no {second_word}")
    parts += _group_parts(zero_codes, lead=zero_lead, trail=zero_trail)
    new_note = "\n\n".join(parts)

    old_note = block.get("nothing_note") or ""
    old_block_text = block.get("block") or ""
    if old_note and old_block_text.endswith(old_note):
        new_block_text = old_block_text[: -len(old_note)] + new_note
    elif old_block_text:
        new_block_text = f"{old_block_text}\n\n{new_note}"
    else:
        new_block_text = new_note
    block["block"] = new_block_text
    block["any"] = True
    block["nothing_note"] = new_note
    block["rung"] = rung
    # Owner ruling 22 Sep 2026, R6 (AC-EQ-5..9): a stock-origin ask is ALWAYS suggested to
    # the warehouse team, no PO-rung override - the review's should-fix 8 (8 Sep 2026) had
    # this rewrite `block["team"]` / `parser["routing"]["suggested_team"]` to "purchasing"
    # whenever the PO rung ran, which is now the wrong team for a stock question: the
    # offer and the routing already agree, both name whatever `crossdomain_zeroset`
    # (`answer.py:489`) set for the question's OWN origin domain - "warehouse" for
    # inventory, "purchasing" for incoming - and the rung that happens to answer it does
    # not change who picks it up.
    if trace is not None:
        # A9: the ladder's OWN probe, over exactly the codes the first probe found
        # nothing for - `run_crossdomain` records the first (hard-coded) probe
        # itself, so this is the second `crossdomain` event when it fires.
        row_count = sum(len(v) for v in lines_by_code.values())
        trace.add(
            "crossdomain",
            {
                "rung": rung,
                "tool": args.get("tool"),
                "args": args,
                "rows": row_count,
                "rendered": new_note,
            },
        )


# NOT ON THE TURN PATH since the re-architecture: its only caller was `complete_answer`,
# which `run_turn` no longer reaches. Contract 3 and 125's rung walk is
# `turn/fetch.py::_climb` now, through the same fan-out fetch every domain uses. Kept
# only because the KEPT-node replay corpus still drives it directly.
def run_crossdomain(
    validator_result: dict[str, Any] | None,
    *,
    parser: dict[str, Any] | None,
    resolved: dict[str, Any] | None,
    session_block: Any,
    entities_names: Any,
    services: Any,
    contact_id: Any,
    space_id: Any = None,
    dry_run: bool = False,
    crossdomain_ladder: dict[str, list[str]] | None = None,
    trace: Any = None,
    granted: Any = None,
) -> dict[str, Any]:
    """`crossdomain-zeroset -> crossdomain-gate -> crossdomain-probe -> crossdomain-render`,
    then A7's further ladder rung (`_apply_crossdomain_rung`) when the first probe still
    left codes with nothing on either side.

    D14: a dry run makes the SAME probe(s). The read is what a test turn has to reproduce,
    or console and clone testing prove nothing about production; the writes are what D14
    suppresses, and this lane has none.

    `trace` (A9, chatbot-growth-r1): the turn's live `TurnTrace`, optional - `None` is a
    no-op, same contract as `run_fetch`'s own `trace` parameter.
    """
    zeroset = crossdomain_zeroset(
        validator_result, parser=parser, resolved=resolved, session_block=session_block
    )
    xd = zeroset.get("_xd") or {}
    if xd.get("active") is not True:
        return {"zeroset": zeroset, "render": None}
    args = crossdomain_probe_args(
        xd,
        parser=parser,
        entities_names=entities_names,
        contact_id=contact_id,
        space_id=space_id,
        granted=granted,
    )
    try:
        probe_result = services.mcp_probe(args["tool"], args)
    except Exception:  # noqa: BLE001 - a failed probe renders as `degraded`, never a dead turn
        logger.warning("chatbot: cross-domain probe did not run", exc_info=True)
        probe_result = None
    render = crossdomain_render(
        probe_result if isinstance(probe_result, dict) else {},
        zeroset=xd,
        validator=validator_result,
    )
    if trace is not None:
        block = render.get("_xdBlock") if isinstance(render, dict) else {}
        block = block if isinstance(block, dict) else {}
        trace.add(
            "crossdomain",
            {
                "rung": xd.get("other_tool"),  # the hard-coded first probe names its rung by tool
                "tool": args.get("tool"),
                "args": args,
                "rows": block.get("probed_rows"),
                "rendered": block.get("block"),
            },
        )
    _apply_crossdomain_rung(
        render,
        xd=xd,
        parser=parser,
        services=services,
        contact_id=contact_id,
        space_id=space_id,
        ladder=crossdomain_ladder,
        trace=trace,
        granted=granted,
    )
    return {"zeroset": zeroset, "render": render}


# --------------------------------------------------------------------------- #
# promo-picker (583 lines, the LIVE SPINE's copy)
# --------------------------------------------------------------------------- #

_PROMO_ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATA_LAST_UPDATED_RE = re.compile(r"_Data last updated:[^\n]*_")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_LEADING_NEWLINES_RE = re.compile(r"^\n+")


def _pretty_team(value: Any) -> str:
    """`String(_t == null ? '' : _t).replace(/_/g, ' ').trim()`.

    DISPLAY ONLY: the raw slug still reaches routing and persistence through
    `gate.company_team` and `parser.routing.suggested_team`. Same convention as the tail's
    `_prettyKey` - lowercase, no title-caser.
    """
    return jsc.js_string("" if value is None else value).replace("_", " ").strip()


def _promo_norm(value: Any) -> str:
    return jsc.nullish_str(value).strip().lower()


def _promo_loose(value: Any) -> str:
    """`String(s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')` - alphanumerics only."""
    return _NON_ALNUM_RE.sub("", jsc.nullish_str(value).lower())


def _promo_field_of(answer: Any, label: str) -> str:
    fields = jsc.get(answer, "fields") if jsc.truthy(answer) else None
    fields = fields if isinstance(fields, list) else []
    field = jsc.find(fields, lambda x: _promo_norm(jsc.get(x, "label")) == label)
    return jsc.nullish_str(jsc.get(field, "value")).strip() if jsc.truthy(field) else ""


def _promo_date_key(answer: Any, label: str) -> str | None:
    """ISO `yyyy-mm-dd` sorts correctly as a STRING; anything unparseable sorts LAST rather
    than silently landing at the top as an empty string would under a naive compare."""
    value = _promo_field_of(answer, label)
    return value if _PROMO_ISO_DATE_RE.match(value) else None


def _promo_label_of(answer: Any, index: int) -> str:
    fields = jsc.get(answer, "fields")
    fields = fields if isinstance(fields, list) else []
    field = jsc.find(fields, lambda x: _promo_norm(jsc.get(x, "label")) == "promotion")
    title = jsc.get(answer, "title")
    value = jsc.get(field, "value") if jsc.truthy(field) else None
    chosen = title if jsc.truthy(title) else (value if jsc.truthy(value) else f"promotion {index + 1}")
    return jsc.js_string(chosen).strip()


def _promo_render_blocks(rows: list) -> str:
    """`response` is the PRE-RENDERED customer string, so filtering `answers` alone is
    invisible to the customer - this node has to own the text too."""
    blocks = []
    for index, answer in enumerate(rows):
        fields = jsc.get(answer, "fields")
        fields = fields if isinstance(fields, list) else []
        lines = "\n".join(
            f"*{jsc.js_string(jsc.get(f, 'label'))}:* {jsc.js_string(jsc.get(f, 'value'))}"
            for f in fields
        )
        blocks.append(f"{index + 1}. {lines}")
    return "\n\n".join(blocks)


def _promo_reintro(response: Any, old_intro: Any, new_intro: str) -> str | None:
    """Swap ONLY the leading intro paragraph, leaving the LLM's own rendering intact. If the
    head is not the intro we expected, return None so the caller rebuilds instead."""
    text = jsc.js_string(response if jsc.truthy(response) else "")
    head = text.split("\n\n")[0]
    if jsc.truthy(old_intro) and head.strip() == jsc.js_string(old_intro).strip():
        parts = [new_intro, _LEADING_NEWLINES_RE.sub("", text[len(head):])]
        return "\n\n".join(p for p in parts if jsc.truthy(p))
    return None


def _promo_matches_of(resolved: Any) -> list:
    """Every `promotion` match anywhere, for the via_product fallback signal."""
    out: list = []

    def push(arr: Any) -> None:
        for match in (arr if isinstance(arr, list) else []):
            if jsc.truthy(match) and jsc.get(match, "entity_type") == "promotion":
                out.append(match)

    push(jsc.get(resolved, "intersection"))
    push(jsc.get(jsc.get(resolved, "by_entity_type"), "promotion"))
    for res in jsc.array(jsc.get(resolved, "resolutions")):
        push(jsc.get(res, "matches"))
    return out


def promo_picker(
    item: dict[str, Any] | None,
    *,
    parser: dict[str, Any] | None,
    resolved: dict[str, Any] | None,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`promo-picker`: the promotion answer's ordering, pick, roster and strict miss.

    S4 (list-then-pick) is REMOVED (owner-locked, "send them all"): every promotion answer
    ATTACHES its files immediately, and the tier ask upstream is what bounds the count. The
    roster is still published so a follow-up number keeps addressing the list the customer
    sees, and S5's positional pick stays as the VESTIGIAL lane for sessions holding an older
    roster.

    Positions and not `promotion_ids`, deliberately: `answers[]` carry NO uuid at this layer
    (verified on a real execution - every promotion row's uuid is null), so the pick turn
    re-runs the SAME scoped query and selects from the fresh result set.

    `gate` is the three-state `isExecuted` read of `disallowed-entity-gate` (the Q23 access
    notice, the D10 brand gate and the company team). It is OPTIONAL because the node's own
    reads of it are all wrapped, and `None` reproduces the not-executed arm exactly.
    """
    j = dict(item) if isinstance(item, dict) else {}
    q = parser if isinstance(parser, dict) else {}
    r = resolved if isinstance(resolved, dict) else {}
    g = gate if isinstance(gate, dict) else None

    notice = jsc.js_string(jsc.get(g, "access_notice") or "") if g is not None else ""

    def with_notice(text: Any) -> str:
        return f"{notice}\n\n{text}" if jsc.truthy(notice) else jsc.js_string(text)

    # HOISTED (D10): the brand-gate guard has to render an offer BEFORE any exit path,
    # including the unrecognised-shape one.
    company_team = jsc.get(g, "company_team") if g is not None else None
    if jsc.truthy(company_team):
        esc_source: Any = company_team
    else:
        routing = jsc.get(q, "routing")
        suggested = jsc.get(routing, "suggested_team") if jsc.truthy(routing) else None
        esc_source = suggested if jsc.truthy(suggested) else "marketing_promotion_sorento"
    esc_team = _pretty_team(esc_source)

    domain_hint = q.get("domain_hint") if jsc.has(q, "domain_hint") else None
    if domain_hint != "promotion":
        return j

    # N1 - ENVELOPE SHAPE. The downstream cross-domain node reads the item flat but
    # `central-exchange` unwraps `input.output`. A wrapped envelope reaching here would make
    # `env.answers` undefined, this node would no-op and EVERY attachment would flow on
    # untouched - a fail-OPEN failure that looks like success. Tolerate both shapes.
    output = j.get("output")
    env: dict[str, Any] = output if isinstance(output, dict) else j

    raw_answers = env.get("answers") if isinstance(env.get("answers"), list) else None
    atts = env.get("attachments") if isinstance(env.get("attachments"), list) else []

    # D10 - THE BRAND GATE FAILS CLOSED HERE. The customer named a brand they hold no
    # entitlement for. `tier-gate` already sent `access_levels: []` to the CRM, but under
    # replay a non-empty CRM response rendered the answer and emitted six Sorento PDFs
    # against a Cabana ask, under a notice saying the customer has no Cabana access. An
    # access boundary may not be enforced by another system's empty-filter semantics.
    if g is not None and jsc.get(g, "brand_gate_empty") is True:
        deny = (
            f"{notice or 'You do not have access to the brand you asked about.'}\n\n"
            f"Would you like me to escalate to {esc_team} team?"
        )
        env["answers"] = []
        env["attachments"] = []
        env["response"] = deny
        env["response_intro"] = deny
        # no roster: a stray "1" must not pick a row the customer was never allowed to see
        env["suggest_last_result_set"] = []
        env["suggest_selection_context"] = None
        j["_brand_gate_closed"] = True
        return j

    if raw_answers is None:
        # Shape not understood. Falling through would send every PDF at once, which is
        # precisely what this node exists to prevent - so fail CLOSED and say so.
        if len(atts) > 1:
            env["attachments"] = []
            env["response_intro"] = (
                "I found several promotions but could not list them. Please narrow your search."
            )
            env["response"] = env["response_intro"]
        j["_promo_picker_shape"] = "unrecognised"
        return j
    answers = raw_answers

    # S4b - deterministic order: latest END DATE first. Sorting HERE is load-bearing twice
    # over: `attachments[i]` is index-paired with `answers[i]`, so the permutation must be
    # applied to both or every pick sends the wrong file; and the pick lane re-runs the query
    # and indexes into `answers`, so both lanes have to see the same order.
    def _compare(x: int, y: int) -> int:
        ex, ey = _promo_date_key(answers[x], "end date"), _promo_date_key(answers[y], "end date")
        if ex != ey:
            if ex is None:
                return 1
            if ey is None:
                return -1
            return -1 if ex > ey else 1
        sx, sy = _promo_date_key(answers[x], "start date"), _promo_date_key(answers[y], "start date")
        if sx != sy:
            if sx is None:
                return 1
            if sy is None:
                return -1
            return -1 if sx > sy else 1
        return x - y  # stable: equal dates keep the CRM's own order

    order = sorted(range(len(answers)), key=cmp_to_key(_compare))
    reordered = any(src != dst for dst, src in enumerate(order))
    pairable = len(atts) == len(answers)
    if reordered:
        sorted_answers = [answers[i] for i in order]
        answers[:] = sorted_answers
        if pairable:
            atts[:] = [atts[i] for i in order]
    j["_promo_sort"] = {"reordered": reordered, "pairable": pairable, "order": order}

    # Per-product itemisation. The linkage is already on the wire: every promotion match
    # carries `display.products`, so for each product the customer NAMED, ask whether any
    # promotion we are about to show lists it. Join on the DESCRIPTION, the only key the
    # answer rows and the resolver matches share.
    shown_names = {
        name
        for name in (
            _promo_norm(
                jsc.get(a, "title")
                if jsc.truthy(jsc.get(a, "title"))
                else jsc.get(
                    jsc.find(
                        jsc.get(a, "fields") or [],
                        lambda x: _promo_norm(jsc.get(x, "label")) == "promotion",
                    ),
                    "value",
                )
            )
            for a in answers
        )
        if name
    }

    all_promo = _promo_matches_of(r)

    def _unmatched_products() -> list:
        tokens = [
            jsc.nullish_str(t).strip()
            for t in jsc.array(jsc.get(r, "tokens"))
            if jsc.truthy(jsc.nullish_str(t).strip())
        ]
        per_token: dict[str, list] = {}
        for res in jsc.array(jsc.get(r, "resolutions")):
            if not jsc.truthy(res) or not jsc.truthy(jsc.get(res, "token")):
                continue
            per_token[_promo_norm(jsc.get(res, "token"))] = [
                m
                for m in jsc.array(jsc.get(res, "matches"))
                if jsc.truthy(m) and jsc.get(m, "entity_type") == "promotion"
            ]

        def contributed(token: Any) -> bool:
            key = _promo_norm(token)
            own = per_token.get(key) or []
            if any(
                _promo_norm(jsc.get(jsc.get(m, "display"), "description")) in shown_names
                for m in own
            ):
                return True
            # reached THROUGH this product token (`display.products` names it)
            for m in all_promo:
                products = jsc.get(jsc.get(m, "display"), "products")
                products = products if isinstance(products, list) else []
                if any(_promo_norm(p) == key for p in products) and (
                    _promo_norm(jsc.get(jsc.get(m, "display"), "description")) in shown_names
                ):
                    return True
            return False

        unmet = [t for t in tokens if not contributed(t)]
        # if NOTHING contributed, this is not a partial miss - the not-found path owns it
        return [] if len(unmet) == len(tokens) else unmet

    unmatched_products = _unmatched_products()

    # DISJOINT UNION. Every named token contributed rows, yet NO row satisfies all of them,
    # so the list is the union of disjoint per-token sets and answers none of them. An EMPTY
    # `intersection` beside non-empty per-token matches is exactly that signal, and it was on
    # the wire the whole time.
    def _disjoint_tokens() -> list:
        tokens = [
            jsc.nullish_str(t).strip()
            for t in jsc.array(jsc.get(r, "tokens"))
            if jsc.truthy(jsc.nullish_str(t).strip())
        ]
        if len(tokens) < 2:
            return []  # a lone token cannot be disjoint with itself
        own: dict[str, int] = {}
        for res in jsc.array(jsc.get(r, "resolutions")):
            if not jsc.truthy(res) or not jsc.truthy(jsc.get(res, "token")):
                continue
            own[_promo_norm(jsc.get(res, "token"))] = len(
                [
                    m
                    for m in jsc.array(jsc.get(res, "matches"))
                    if jsc.truthy(m) and jsc.get(m, "entity_type") == "promotion"
                ]
            )
        # Every named token must have contributed promotions of its own; one that contributed
        # nothing is the per-item case above, which says something more specific.
        if not all((own.get(_promo_norm(t)) or 0) > 0 for t in tokens):
            return []
        inter = [
            m
            for m in jsc.array(jsc.get(r, "intersection"))
            if jsc.truthy(m) and jsc.get(m, "entity_type") == "promotion"
        ]
        return tokens if len(inter) == 0 else []

    disjoint_tokens = _disjoint_tokens()
    broadened = jsc.get(r, "fallback_applied") is True

    # S5 - positional pick. Positions are only meaningful against the roster the parser says
    # they index: on a dym pick the LLM emits the candidate's DYM slot and the parser has
    # already spent it resolving the entity, so applying it again indexed slot 2 into a
    # one-row promotion result and told the customer "reply with a number between 1 and 1".
    if jsc.get(q, "reference_target") == "dym" or jsc.get(q, "dym_pick_applied") is True:
        positions: list = []
    elif isinstance(jsc.get(q, "reference_positions"), list):
        positions = [
            n
            for n in (jsc.js_number(v) for v in q["reference_positions"])
            if jsc.is_integer(n) and n >= 1
        ]
    else:
        positions = []

    if len(positions) > 0 and len(answers) > 0:
        # Distinguisher is PROVENANCE, not set arithmetic. When the parser resolved the pick
        # into PROMOTION entities the query already came back narrowed to exactly what was
        # picked; when our scope-reuse ran instead, the answers are the full list and must be
        # filtered positionally. The parser states its own provenance - trust that flag over
        # the shape, because a list scoped BY A PROMOTION NAME also has promotion-hinted
        # entities and treating that as pre-narrowed passed the WHOLE list through.
        scope_reused = jsc.get(q, "_promo_pick_scope_reused") is True
        picked_entities = (not scope_reused) and any(
            jsc.js_string(jsc.get(e, "hint") or "").lower() == "promotion"
            for e in (q["entities"] if isinstance(jsc.get(q, "entities"), list) else [])
        )
        if picked_entities:
            env["response_intro"] = with_notice(
                "I have attached the file(s) below."
                if len(atts)
                else "No file is attached to that promotion. Here are its details."
            )
            if jsc.truthy(notice) and jsc.truthy(env.get("response")):
                env["response"] = with_notice(jsc.js_string(env["response"]))
            # F4: without republishing the roster + context, the tail's promo arm is skipped,
            # `selection_context` reverts to null and a SECOND "all" no longer expands.
            env["suggest_last_result_set"] = [
                {
                    "idx": i + 1,
                    "label": _promo_label_of(a, i),
                    "value": _promo_label_of(a, i),
                    "uuid": None,
                    "entity_type": "promotion",
                    "filename": jsc.get(atts[i] if i < len(atts) else {}, "filename"),
                }
                for i, a in enumerate(answers)
            ]
            env["suggest_selection_context"] = "suggest_offer"
            j["_promo_pick"] = {
                "positions": positions,
                "matched": len(answers),
                "files": len(atts),
                "out_of_range": [],
                "drift": [],
                "pre_narrowed": True,
            }
            return j

        keep = set(positions)
        picked_idx = [n for n in range(1, len(answers) + 1) if n in keep]
        picked_ans = [answers[n - 1] for n in picked_idx if jsc.truthy(answers[n - 1])]

        # `answers[]` and `attachments[]` are INDEX-ALIGNED - that is the contract, and
        # matching by name instead is actively wrong: the answer title is an LLM-normalised
        # copy of the filename with punctuation dropped. `loose` compares alphanumerics only,
        # so it is a real cross-check rather than a punctuation test; a mismatch is RECORDED,
        # never silently corrected.
        pick_labels = [
            _promo_loose(x)
            for x in (
                q["_promo_pick_labels"]
                if isinstance(jsc.get(q, "_promo_pick_labels"), list)
                else []
            )
        ]
        pick_labels = [x for x in pick_labels if jsc.truthy(x)]
        by_name = {}
        for att in atts:
            by_name[_promo_loose(jsc.get(att, "filename"))] = att
        drift: list = []
        if len(pick_labels) > 0:
            by_label: dict[str, int] = {}
            for i, a in enumerate(answers):
                by_label[_promo_loose(_promo_label_of(a, i))] = i
            mapped = [by_label.get(label, -1) for label in pick_labels]
            missing = len([i for i in mapped if i < 0])
            if missing == 0:
                picked_idx = [i + 1 for i in mapped]
                picked_ans = [answers[n - 1] for n in picked_idx]
            else:
                # labels no longer present: the set genuinely changed. Say so, do not guess.
                j["_promo_pick_label_miss"] = missing

        picked_atts: list = []
        for k, n in enumerate(picked_idx):
            att = atts[n - 1] if 0 <= n - 1 < len(atts) else None
            label = _promo_loose(_promo_label_of(picked_ans[k] if k < len(picked_ans) else None, k))
            if jsc.truthy(att) and _promo_loose(jsc.get(att, "filename")) == label:
                picked_atts.append(att)
                continue
            if label in by_name:
                drift.append({"idx": n, "resolved_by": "name"})
                if jsc.truthy(by_name[label]):
                    picked_atts.append(by_name[label])
                continue
            if jsc.truthy(att):
                drift.append({"idx": n, "resolved_by": "index", "label_mismatch": True})
                picked_atts.append(att)
                continue
            drift.append({"idx": n, "resolved_by": "none"})

        if len(picked_ans) == 0:
            # Every picked position is past the end of the list: say so plainly. Falling
            # through would emit "No file is attached to that promotion", which describes a
            # DIFFERENT situation and reads as though the promotion exists.
            only = (
                f"There {'is' if len(answers) == 1 else 'are'} only {len(answers)} "
                f"promotion{'' if len(answers) == 1 else 's'} in that list, "
                f"please reply with a number between 1 and {len(answers)}."
            )
            env["answers"] = []
            env["attachments"] = []
            env["response"] = with_notice(only)
            env["response_intro"] = with_notice(only)
            j["_promo_pick"] = {
                "positions": positions,
                "matched": 0,
                "files": 0,
                "out_of_range": [n for n in positions if n > len(answers)],
                "drift": [],
            }
            return j

        # Keep publishing the FULL-list roster on a pick turn. Without it the tail rebuilds
        # `last_result_set` from the FILTERED answers, the roster collapses to the one
        # promotion just sent, and every later number returns that same file.
        env["suggest_last_result_set"] = [
            {
                "idx": i + 1,
                "label": _promo_label_of(a, i),
                "value": _promo_label_of(a, i),
                "uuid": None,
                "entity_type": "promotion",
                "filename": jsc.get(atts[i] if i < len(atts) else {}, "filename"),
            }
            for i, a in enumerate(answers)
        ]
        env["suggest_selection_context"] = "suggest_offer"
        env["answers"] = picked_ans
        env["attachments"] = picked_atts
        # A picked promotion with no file still answers, with its details as text.
        pick_intro = (
            "I have attached the file(s) below."
            if len(picked_atts)
            else "No file is attached to that promotion. Here are its details."
        )
        env["response_intro"] = with_notice(pick_intro)
        # rebuilt, not sliced: the set itself changed, so the old rendering is wrong line by line
        env["response"] = with_notice(
            "\n\n".join(p for p in [pick_intro, _promo_render_blocks(picked_ans)] if jsc.truthy(p))
        )
        j["_promo_pick"] = {
            "positions": positions,
            "matched": len(picked_ans),
            "files": len(picked_atts),
            "out_of_range": [n for n in positions if n > len(answers)],
            "drift": drift,
        }
        return j

    # F6: the Q23 fallback can return exactly ONE promotion. Without this the customer who
    # asked for a level they do not hold receives a file from a different level with no
    # explanation.
    if len(answers) == 1 and jsc.truthy(notice):
        env["response_intro"] = with_notice(jsc.js_string(env.get("response_intro") or ""))
        if jsc.truthy(env.get("response")):
            env["response"] = with_notice(jsc.js_string(env["response"]))

    if len(answers) > 1:
        env["suggest_last_result_set"] = [
            {
                "idx": i + 1,
                "label": _promo_label_of(a, i),  # BARE - no numbering baked in, or the pick breaks
                "value": _promo_label_of(a, i),
                "uuid": None,  # promotions carry none at this layer
                "entity_type": "promotion",
                "filename": jsc.get(atts[i] if i < len(atts) else {}, "filename"),
            }
            for i, a in enumerate(answers)
        ]
        env["suggest_selection_context"] = "suggest_offer"

        # Scope echo. "I found 10 promotions." does not say 10 promotions for WHAT. Echo the
        # scope the customer actually typed and never a canonical code - one typed fragment
        # can resolve to two products, so naming one would say we searched something they did
        # not ask for. Past 60 characters the echo is noise and is dropped rather than
        # truncated, because a half-printed promotion name reads like a different one.
        unmet_set = {jsc.js_string(t).strip().lower() for t in unmatched_products}
        raws: list[str] = []
        for entity in (q["entities"] if isinstance(jsc.get(q, "entities"), list) else []):
            value = jsc.js_string(jsc.get(entity, "raw") or "").strip()
            if value.lower() in unmet_set:
                continue
            if value and not any(x.lower() == value.lower() for x in raws):
                raws.append(value)
        scope_label = ", ".join(raws[:3])
        if len(scope_label) > 60:
            scope_label = ""

        list_intro = (
            f"I found {len(answers)} promotions"
            f"{f' for {scope_label}' if scope_label else ''}. "
            f"I have attached the file(s) below."
        )
        # `reintro` reuses the LLM's own rendering and swaps only the leading paragraph, which
        # is correct ONLY while the rows are still in the order the LLM rendered them. Once
        # S4b permutes `answers`, that body is stale, so a reordered turn REBUILDS the body -
        # carrying the freshness stamp across verbatim, because that is the trailing matter
        # the customer actually uses.
        # D11-reproduced: `promo-picker.js:468`'s own `_tail` match, over the response
        # THIS turn built (never the customer's words).
        tail_match = _DATA_LAST_UPDATED_RE.search(jsc.js_string(env.get("response") or ""))
        tail = tail_match.group(0) if tail_match else None
        swapped = (
            None
            if reordered
            else _promo_reintro(env.get("response"), env.get("response_intro"), list_intro)
        )
        env["response"] = with_notice(
            swapped
            if swapped is not None
            else "\n\n".join(
                p
                for p in [list_intro, _promo_render_blocks(answers), tail]
                if jsc.truthy(p)
            )
        )
        env["response_intro"] = with_notice(list_intro)
        j["_promo_picker"] = {
            "count": len(answers),
            "intro_swapped": swapped is not None,
            "rebuilt": reordered,
        }

    # STRICT NOT-FOUND (owner decision, twice affirmed). When the customer's actual
    # COMBINATION has no satisfying rows, say so and stop. No "closest matches" and no
    # cross-brand suggestions: measured live, those lists read as unrelated noise. A plain
    # miss is less confusing than a helpful-looking list that does not answer.
    token_hint: dict[str, str] = {}
    for entity in (q["entities"] if isinstance(jsc.get(q, "entities"), list) else []):
        if jsc.truthy(entity) and jsc.truthy(jsc.get(entity, "raw")):
            token_hint[_promo_norm(jsc.get(entity, "raw"))] = jsc.js_string(
                jsc.get(entity, "hint") or ""
            ).lower()

    # Word-level unmet, from the CRM's own `token_coverage`. Contract traps, all honoured:
    # an ABSENT promotion entry is no claim (membership-derived rows) and is not consulted;
    # `truncated: true` means rows were trimmed or unscored, so it cannot claim.
    coverage_unmet: dict[str, list] = {}
    for tc in jsc.array(jsc.get(r, "token_coverage")):
        if not jsc.truthy(tc) or not jsc.truthy(jsc.get(tc, "token")):
            continue
        coverage = jsc.get(tc, "coverage")
        cov = jsc.find(
            coverage if isinstance(coverage, list) else [],
            lambda c: jsc.truthy(c) and jsc.get(c, "entity_type") == "promotion",
        )
        if not jsc.truthy(cov):
            continue  # absence = NO CLAIM, never "no match"
        if jsc.get(cov, "truncated") is True:
            continue
        words = [
            jsc.nullish_str(w).strip()
            for w in jsc.array(jsc.get(cov, "unmatched_words"))
        ]
        words = [w for w in words if jsc.truthy(w)]
        if words:
            coverage_unmet[_promo_norm(jsc.get(tc, "token"))] = words

    def _strict_miss() -> dict[str, Any] | None:
        if disjoint_tokens:
            return {"tokens": disjoint_tokens, "words": [], "reason": "disjoint"}
        if not unmatched_products and not coverage_unmet:
            return None
        tokens = [
            jsc.nullish_str(t).strip()
            for t in jsc.array(jsc.get(r, "tokens"))
        ]
        tokens = [t for t in tokens if jsc.truthy(t)]
        unmet = {_promo_norm(t) for t in unmatched_products}
        unmet |= set(coverage_unmet)  # word-level unmet counts as unmet
        met = [t for t in tokens if _promo_norm(t) not in unmet]
        # all-unmet with NO coverage evidence stays the not-found path's job; with coverage
        # evidence we know exactly which words failed, so this node states it.
        if not met and not coverage_unmet:
            return None
        # every met token is the brand arm, so nothing shown answers the ask
        if all(token_hint.get(_promo_norm(t)) == "brand" for t in met):
            seen_words: list = []
            for values in coverage_unmet.values():
                for word in values:
                    if word not in seen_words:
                        seen_words.append(word)
            return {
                "tokens": tokens,
                "words": seen_words,
                "reason": "coverage_unmet" if coverage_unmet else "unmet_brand_only",
            }
        return None

    strict_miss = _strict_miss()

    if strict_miss and (jsc.truthy(env.get("response")) or jsc.truthy(env.get("response_intro"))):
        ask = " ".join(jsc.js_string(t) for t in strict_miss["tokens"])
        offer = f"Would you like me to escalate to {esc_team} team?"
        # Name the failing WORDS when the coverage field told us, so the customer can
        # self-correct a typo instead of guessing why we missed.
        detail = (
            ': could not find "' + '", "'.join(strict_miss["words"]) + '"'
            if strict_miss["words"]
            else ""
        )
        message = f"No promotion found for {ask}{detail}. {offer}"
        env["response"] = with_notice(message)
        env["response_intro"] = with_notice(message)
        env["attachments"] = []  # nothing is being answered; nothing may be sent
        env["answers"] = []
        # no roster: a stray "1" after a not-found must not pick an invisible row
        env["suggest_last_result_set"] = []
        env["suggest_selection_context"] = None
        j["_promo_notfound"] = {
            "tokens": strict_miss["tokens"],
            "words": strict_miss["words"],
            "reason": strict_miss["reason"],
        }
        # `= undefined` in JS creates a key JSON.stringify then DROPS, so the empty case is
        # an ABSENT key here, not a null one.
        if disjoint_tokens:
            j["_promo_disjoint"] = disjoint_tokens
        if unmatched_products:
            j["_promo_unmatched"] = unmatched_products
    elif unmatched_products and (
        jsc.truthy(env.get("response")) or jsc.truthy(env.get("response_intro"))
    ):
        # per-item decomposition - product tokens, at least one answered with its own promos
        note = f"No promotion found for {', '.join(jsc.js_string(t) for t in unmatched_products)}."
        offer = f"Would you like me to escalate to {esc_team} team?"
        if jsc.truthy(env.get("response")):
            env["response"] = f"{env['response']}\n\n{note} {offer}"
        j["_promo_unmatched"] = unmatched_products
        j["_promo_broadened"] = broadened

    return j


# --------------------------------------------------------------------------- #
# not-found-error-message (667 lines, the LIVE SPINE's copy)
# --------------------------------------------------------------------------- #

_TYPE_NORM_RE = re.compile(r"[-\s]+")
_SNAKE_OR_KEBAB_RE = re.compile(r"[_-]")
_SNAKE_OR_KEBAB_RUN_RE = re.compile(r"[_-]+")
_TRAILING_PAREN_RE = re.compile(r"\s+\([^)]*\)\Z")
_ISO_DATE_HEAD_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})")
_SCOPING_ENTITY_RE = re.compile(r"requires a scoping entity")
_VOWEL_HEAD_RE = re.compile(r"^[aeiou]", re.IGNORECASE)

# The header describes a DELIVERY ORDER search specifically. It used to gate on "domains the
# CRM date-filters", which let it fire where it is actively wrong (a container has no
# customer and nobody date-filters incoming in practice).
_DATE_SCOPE_DOMAINS = frozenset({"order"})

_ORDER_TYPES = frozenset({"order", "customer_order", "order_number"})

# Types whose CANONICAL CODE is the identity the customer recognises, so the resolver's
# `display` name must never stand in for it (owner rule, 6 Sep 2026; prod turn 631d4b65).
#
# `product` and only `product`, and the set is one entry because that is what the evidence
# supports. Item A put `product_name` at the head of `_DISPLAY_NAME_KEYS` so a shipment
# with a null `shipment_number` could print its container, and "check eta for
# IBKS7245-NG-BL" then answered "product: Iborn. Bidet. (+7 more)" - a free-text
# description maintained for a different audience, with no way for the reader to tell it
# means the code they asked about.
#
# Every OTHER type is already right and is deliberately left alone: a customer, a
# transporter, a form and a brand have no customer-facing code (their `canonical_code` is
# an internal account or slug), a promotion's canonical_code IS its uuid, an
# `attachment_type` is known by its type name, and `spo` / `grn` / `warehouse` reach this
# ladder through display keys that are themselves codes. The trigger for a second entry is
# a measured turn where a coded type prints a name instead.
_CODE_FIRST_TYPES = frozenset({"product"})

# DENY-list, not an allow-list, ON PURPOSE. `brand` / `category` reach `compatible_entities`
# on the product domains but `entity-ids-transformer` maps neither to a tool param, so a
# category resolved in one company beside a product resolved in another would make
# "checked in A and B" a false statement about a lookup that only queried B. Every other
# allowed type does carry a param today, so if the CRM later gives `category` one this
# UNDER-claims instead of over-claiming. Silence is recoverable; a false statement is not.
_NO_TOOL_ID = frozenset({"brand", "category"})
#: Public alias (reviewer nit, hand pass 11 final): `answer_bridge._searched_companies`
#: imports this across modules and a private name should not cross that boundary.
NO_TOOL_ID = _NO_TOOL_ID

_SCOPE_WORD = {
    "order": "delivery order",
    "incoming": "incoming shipment",
    # PLAN-po-spo-warehouse-29sep W1: `purchase_order` now has a gate row, so it can miss.
    "purchase_order": "purchase order",
    "inventory": "stock",
    "promotion": "promotion",
    "goods_receive": "goods receipt",
    "master_products": "product",
    # 8 Sep 2026: `spo_allocation` reaches this branch now that its gate row requires a
    # scoping entity, and with no word here the raw domain key printed to the customer.
    "spo_allocation": "SPO line",
}


def _domain_takes_a_date_filter(domain: Any) -> bool:
    """Does any tool this domain can call accept a date range?

    `Policy.domain(domain).takes_date_filter` now (AC-1594: was derived here from the
    retired hand-written tool list against `fetch.DATE_PARAMS`); the S0 migration seeded that
    column with the SAME derivation, off `turn/policy_rows.py::DATE_PARAM_TOOLS`, so
    reading the column is not a second copy. `spo_allocation`'s only tool
    (`crm_procurement_spo_allocations_last_receipt_list`) takes no date parameter, so the
    scoping ask offered the customer a filter nothing downstream could have applied.
    """
    from app.services.chatbot.turn.policy import default_policy

    row = default_policy().domain(jsc.js_string(domain if jsc.truthy(domain) else "").lower())
    return bool(row.takes_date_filter) if row is not None else False

# `allowed_lookup` holds the resolver's INTERNAL entity types. Printing them raw asks the
# customer to speak our schema, and several are the same thing to them.
_HUMAN_SCOPE = {
    "order": "order number",
    "order_number": "order number",
    "customer_order": "order number",
    "spo": "SPO number",
    "customer": "customer",
    "transporter": "transporter",
    "product": "product code",
    "warehouse": "warehouse",
    "inbound_shipment": "container",
    "goods_receive": "goods receipt",
}

# The human-readable label on a resolver `display`, in priority order. Every key here is one
# `entity_resolver.py` actually emits: `type_name` stays ahead of `description` so an
# attachment type shows its name and not its long alias text, and `description` stays ahead of
# the canonical code so a promotion (whose code IS its uuid) still reads as a name. The
# shipment keys matter because `canonical_code` for an `inbound_shipment` is its
# `shipment_number`, and that column is null on most rows - the container number is then the
# only identifier the customer has.
_DISPLAY_NAME_KEYS: tuple[str, ...] = (
    "product_name",
    "customer_name",
    "debtor_name",
    "type_name",
    "description",
    "shipment_number",
    "shipping_container_number",
    "spo_number",
    "grn_number",
    "warehouse_name",
    "supplier_name",
    "form_name",
    "filename",
    "title",
    "name",
)


# Which axes are active comes from the GATE (`compatible_entities`), never from the parser's
# hints: a bare code is often hinted `order` and matched by the resolver as a product.
#: `axis_words`' answer for an axis that prints no header line this turn.
_NO_LINE = object()

_AXES: tuple[dict[str, Any], ...] = (
    {"label": "Customer", "types": ["customer"], "hints": ["customer"], "always": True, "allText": "all customers"},
    {"label": "Product", "types": ["product"], "hints": ["product"], "always": True, "allText": "all products"},
    {"label": "Order", "types": ["customer_order", "order", "order_number"], "hints": ["order", "customer_order", "order_number"]},
    {"label": "Transporter", "types": ["transporter"], "hints": ["transporter"]},
    {"label": "Container", "types": ["inbound_shipment"], "hints": ["inbound_shipment", "container"]},
    {"label": "Warehouse", "types": ["warehouse"], "hints": ["warehouse"]},
)


def _human_list(values: list) -> str:
    """`['product','category','brand']` -> "product, category, or brand"."""
    kept = [v for v in values if jsc.truthy(v)]
    if len(kept) == 0:
        return "a valid value"
    if len(kept) == 1:
        return jsc.js_string(kept[0])
    if len(kept) == 2:
        # "A or B", never "A, or B". A two-item list has no series to separate, so the
        # comma is a tell that a three-item helper wrote the sentence (review S5,
        # 8 Sep 2026: "Give me a product code, or warehouse, and I can look it up").
        return f"{jsc.js_string(kept[0])} or {jsc.js_string(kept[1])}"
    head = ", ".join(jsc.js_string(v) for v in kept[:-1])
    return f"{head}, or {jsc.js_string(kept[-1])}"


def _and_list(values: list) -> str:
    """"Mocha and Sorento"; "A, B and C" beyond two."""
    if len(values) <= 1:
        return jsc.js_string(values[0]) if values and jsc.truthy(values[0]) else ""
    head = ", ".join(jsc.js_string(v) for v in values[:-1])
    return f"{head} and {jsc.js_string(values[-1])}"


def _nf_norm_raw(value: Any) -> str:
    return jsc.nullish_str(value).strip().lower()


def _type_norm(value: Any) -> str:
    """`String(s ?? '').replace(/[-\\s]+/g, '').toLowerCase()`.

    The resolver strips dashes and spaces off product-hint tokens before it resolves them,
    so the customer's "SRT 2405-CR" reaches us as "srt2405cr" while the code reads
    "SRT2405-CR". This is the key both sides are compared through.
    """
    return _TYPE_NORM_RE.sub("", jsc.nullish_str(value)).lower()


# A token shorter than this never PREFIX-matches (D4/AC-7): "SRT" would otherwise request
# every product in the intersection, since most of the catalogue's codes start with it.
_TOKEN_PREFIX_MIN_LEN = 4


def _token_requests(norm_code: str, tokens: set[str], all_norm_codes: set[str]) -> bool:
    """D4 (12 Sep 2026, finding 4): does a typed token request this intersection product?

    Owner finding, 12 Sep 2026: "ETA SRTWT6236" resolved (tier `and`) to the one family
    member SRTWT6236-GY, but `crossdomain_zeroset`'s non-`resolutions` branch only asked
    "does `_type_norm(canonical_code)` EQUAL a typed token" - the prefix never matched, so
    `requested` stayed empty, `_xd.active` was False, and nothing probed the incoming or
    PO ladder at all, though SRTWT6236-GY has an open PO line. The `missing` loop two
    screens down already treats a typed code as satisfied by any `startswith` family
    member, so the two halves disagreed about what "requested" means. Fixed here: a
    product is requested when a typed token EQUALS its normalised code, OR when a token of
    at least `_TOKEN_PREFIX_MIN_LEN` characters is a PREFIX of it.

    Hand pass 12 round 2 (owner ruling, M2's own measurement, turn 0c6730a2 "Mfg6661
    eta"): the cross-domain block crosses ONLY for what the PRIMARY fetch used - a typed
    token that fold-equals a REAL code exactly (the same fold-equality `turn/narrow.py:
    372-387`'s own `typed_exact_settles` branch uses to settle the primary fetch on ONE
    product, never its siblings) must not ALSO sweep every sibling sharing its prefix
    into the crossdomain probe, the way `_TOKEN_PREFIX_MIN_LEN`'s own loose rule still
    does for a token that resolves to no exact code at all (SRTWT6236, this function's
    own founding case, is not itself a real product - only its variants are). A token
    that exactly names a real candidate SOMEWHERE in the pool (`all_norm_codes`, every
    candidate's own normalised code, computed once by the caller) refuses the prefix
    half FOR THAT TOKEN - "requested" narrows to the one thing actually typed, not its
    whole family.

    Hand pass 12 Phase 3 finding P4 (reviewer S2): the refusal used to be GLOBAL
    (`tokens & all_norm_codes: return False`) rather than scoped to the exact match's
    own token - one unrelated token being a real code ANYWHERE in the whole pool
    refused the prefix half for every OTHER candidate too, so "mfg6661 srtwt6236bl"
    (mfg6661 an exact code elsewhere in the pool) lost srtwt6236's own prefix match
    against srtwt6236bl, a different family entirely. Scoped per-token: a token that is
    itself an exact code is excluded from the PREFIX check (the `norm_code in tokens`
    line above already grants the exact candidate its own True), every OTHER token
    still gets to request a prefix match.
    """
    if norm_code in tokens:
        return True
    return any(
        len(t) >= _TOKEN_PREFIX_MIN_LEN and t not in all_norm_codes and norm_code.startswith(t)
        for t in tokens
    )


def _plain_words(value: Any) -> str:
    """A domain key said in a sentence ("purchase_order" -> "purchase order"); anything
    else is left as written. Round 4 R7 on PR #833: no snake_case reaches a reply."""
    return jsc.js_string(value).replace("_", " ")


def _prettify_type(value: Any) -> str:
    """A snake_case / kebab-ish resolver entity type, rendered for a customer.

    A plain lowercase word passes through untouched, so every hint the parser emits today
    except the snake_case ones is byte-identical.
    """
    text = jsc.nullish_str(value).strip()
    if not text:
        return ""
    if not _SNAKE_OR_KEBAB_RE.search(text) and text == text.lower():
        return text
    return _SNAKE_OR_KEBAB_RUN_RE.sub(" ", text).strip().lower()


def _js_loose_eq_bool(value: Any, target: bool) -> bool:
    """JS `x == true` / `x == false`: the BOOLEAN is coerced to a number, then compared."""
    if value is None or value is jsc.UNDEFINED:
        return False
    if isinstance(value, bool):
        return value is target
    number = jsc.js_number(value)
    if jsc.is_nan(number):
        return False
    return number == (1 if target else 0)


def _fmt_date(value: Any) -> str:
    match = _ISO_DATE_HEAD_RE.match(jsc.nullish_str(value))
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}" if match else jsc.nullish_str(value)


def _outstanding_report_text(item: Any) -> str:
    """The rendered outstanding-report reply on a MISS, or "" for every other answer.

    `fetch.output_structurer`'s `crm_outstanding_report` branch stamps
    `outstanding_report` on the same item the miss lane is handed (through `validator`
    and `build_result`, both of which pass the body through), and `response` is the text
    `sorento_crm_mcp.presenters._outstanding_report` rendered. One marker, one tool - a
    miss from anything else never matches here.
    """
    body = item if isinstance(item, dict) else {}
    if body.get("outstanding_report") is not True and isinstance(body.get("fetch"), dict):
        # PLAN-chatbot-top-x-hot-selling-24sep.md S4 (AC-1957): the rearch miss path
        # hands over the fetch fragment WRAPPED (`{"fetch": structured}`); a top selling
        # miss with no subject arrives that way, and its own header plus "No sales
        # found." is the answer, not a generic "Could not find order.".
        body = body["fetch"]
    if body.get("outstanding_report") is not True:
        return ""
    return jsc.js_string(body.get("response") or "").strip()


def _unplaced_token_has_neighbours(resolved: Any, gate: Any) -> bool:
    """True when a token the customer typed missed AND has real did-you-mean neighbours.

    AC-1703. The described-set branches below all assert something about the SET the
    question described - "I don't know 'new' as a product type", "I don't know 'Technical
    drawings' as a document type", "Couldn't find a SRTWT165-FT with a certificate". Every
    one of them is a false statement when the SUBJECT itself is a code the catalogue does
    not carry: nothing was described, a token was mistyped, and production's own answer is
    the did-you-mean for that token ('Couldn't find "X" (product). Did you mean ...').
    Three live F8 turns came back with the set answer over the top of it
    (5cde645f "Incoming srtwt7202-new", 0d4abc93 "Technical drawings sttwc286-SH",
    55ca1323 "SRTWT165-FT CERT"), and `build_suggest_offer` cannot recover any of them:
    its own `if not is_clar and not require_spec` gate means a clarify SUPPRESSES the
    did-you-mean candidates entirely, so the two can never both reach the customer.

    Read through `miss_resolutions` + `_ms_is_exact`, the same two `build_suggest_offer`'s
    own D1 arm is built on, so this guard cannot claim a did-you-mean D1 then declines to
    print. `allowed_lookup` narrows it the same way D1 does.

    Only a CODE-shaped token counts (the resolver's own `_CODE_RE`, letters and digits):
    every F8 turn above typed a code. A plain class word ("bidet", "basin") that
    substring-matches a few product codes is the described set's own word, not a typo,
    and treating its matches as neighbours silenced AC-1319's zero-qualifying answer
    ("Couldn't find a bidet with a certificate") under an unrelated did-you-mean.
    """
    from app.services.entity_resolver import _CODE_RE

    r = resolved if isinstance(resolved, dict) else {}
    g = gate if isinstance(gate, dict) else {}
    allowed_lookup = jsc.get(jsc.get(g, "gate_debug"), "allowed_lookup")
    allowed = allowed_lookup if isinstance(allowed_lookup, list) else None
    for res in _ms_miss_resolutions(r, gate=g):
        token = jsc.nullish_str(jsc.get(res, "token")).strip()
        if len(token) < 3 or not _CODE_RE.fullmatch(token):
            continue
        candidates = [
            *jsc.array(jsc.get(res, "matches")),
            *jsc.array(jsc.get(res, "alternatives")),
        ]
        for match in candidates:
            if not jsc.truthy(jsc.get(match, "canonical_code")) or _ms_is_exact(match):
                continue
            entity_type = jsc.get(match, "entity_type")
            if allowed is not None and jsc.truthy(entity_type) and entity_type not in allowed:
                continue
            return True
    return False


# F1 (attribute-first asks, AC-1319): a HAS turn's noun per leg, for the miss sentence.
# `attachment_type` has no fixed noun - its own value already IS the customer's label.
_PREDICATE_NOUN: dict[str, str] = {
    "certificate": "a certificate",
    "stock": "stock",
    "promotion": "a promotion",
    "incoming": "incoming stock",
}


def _predicate_phrase(require: dict[str, Any]) -> str:
    """"a certificate", "stock and a certificate" - the leg(s) a HAS turn asked for.

    `require`'s keys are AND'd (`product_predicate_service.resolve_product_set`'s own
    contract), so the phrase joins on "and", never "or".
    """
    parts: list[str] = []
    for key, value in (require or {}).items():
        if key == "attachment_type":
            label = jsc.js_string(value).strip()
            parts.append(f"a {label}" if label else "an attachment")
            continue
        noun = _PREDICATE_NOUN.get(key)
        if noun:
            parts.append(noun)
    return _and_list(parts) if parts else "that"


# E2 (attribute-first asks, AC-1316): the SET-ANSWER header's noun per leg - plural,
# said of the WHOLE qualifying set ("X taps HAVE certificates"), never the miss
# sentence's singular "a certificate" `_PREDICATE_NOUN` carries above.
_HEADER_PREDICATE_NOUN: dict[str, str] = {
    "certificate": "certificates",
    "stock": "stock",
    "promotion": "a promotion",
    "incoming": "incoming stock",
}


def _header_predicate_phrase(require: dict[str, Any]) -> str:
    """"certificates", "PPS certificates", "certificates and stock" - the
    header's own predicate noun, joined the same way `_predicate_phrase` joins
    the miss sentence's.

    Second console pass, AC-1316: a scheme-narrowed certificate leg
    (`{"certificate": {"scheme": "PPS"}}`) names the SCHEME - "940 products
    have PPS certificates." - never the bare "certificates" a `_HEADER_
    PREDICATE_NOUN` lookup alone would give every certificate leg regardless
    of scheme (measured live on "which item has PPS cert"). A bare
    `{"certificate": True}` require is untouched.
    """
    parts: list[str] = []
    for key, value in (require or {}).items():
        if key == "attachment_type":
            label = jsc.js_string(value).strip().lower()
            parts.append(label if label else "an attachment")
            continue
        if key == "certificate" and isinstance(value, dict):
            scheme = jsc.js_string(jsc.get(value, "scheme")).strip()
            if scheme:
                parts.append(f"{scheme} certificates")
                continue
        if key == "price":
            # A product ask about the set ("described") names no leg. A price ask does
            # (fix round 12: the opener no longer carries the tool's "Prices for").
            if value != "described":
                parts.append("prices")
            continue
        noun = _HEADER_PREDICATE_NOUN.get(key)
        if noun:
            parts.append(noun)
    if not parts and "price" in (require or {}):
        return ""
    return _and_list(parts) if parts else "that"


#: The longest counted set listed in one reply (owner ruling, 26 Sep 2026: "A counted set
#: that fits one WhatsApp message (about 50 rows) is listed in full"). Read at call time
#: (`answer.SET_LIST_MAX`) so a test can lower it rather than seed fifty products.
SET_LIST_MAX = 50


def not_understood_line(words: Any) -> str:
    """The words the set reader could not use, said rather than silently dropped."""
    terms = [jsc.js_string(w).strip() for w in jsc.array(words) if jsc.js_string(w).strip()]
    if not terms:
        return ""
    quoted = _and_list([f'"{t}"' for t in terms])
    # Fix round 9 on PR #833 (owner: "no explanations"): the "did not match" line of the
    # one reply structure, never how the search was run.
    return f"Couldn't find: {quoted}."


#: The one opener of every described-set answer, hit or miss (fix round 12 on PR #833, owner
#: ruling 28 Sep 2026: "why the fix that we applied for incoming cannot work for stock? this
#: is too fragile"). Before this a hit opened with its leg's tool sentence ("Stock summary
#: for gunmetal wash basins (2).") and only a zero set opened with this line, so the reply
#: shape followed the COUNT and the leg word, not the ask. The leg now chooses only what the
#: rows under it show (stock figures, incoming lines, files); the opener is the same.
SET_OPENER = "Here's what you want:"

#: What "But no ... matched these" names per leg: the product-code miss's own words
#: ("But no incoming matched these.").
_MISS_NOUN: dict[str, str] = {
    "certificate": "certificate",
    "stock": "stock",
    "incoming": "incoming",
    "promotion": "promotion",
}


def breakdown_lines(breakdown: Any, set_noun: str) -> list[str]:
    """"• Sorento wash basins: 120", one line per value of the key the resolver broke the
    set down by (`product_predicate_service._set_breakdown`), largest first. Fix round 9
    on PR #833: "always break it down", no cap."""
    noun = set_noun or "products"
    out = []
    for row in jsc.array(jsc.get(breakdown, "rows")):
        value = jsc.js_string(jsc.get(row, "value")).strip()
        count = int(jsc.get(row, "count") or 0)
        if value and count:
            out.append(f"• {value} {noun}: {count:,}")
    return out


def what_you_want_reply(asked: str, lines: list[str], *, missing: str = "", leg: str = "", team: Any) -> str:
    """The ONE reply a miss has, the product-code miss's own (owner, 28 Sep 2026: "you do
    like here is what you want, but no incoming, do you want me to escalate..., I want
    that structure to stay"): what was asked for on one line, what matched line by line,
    what did not match, one short escalate offer. Fix round 9 on PR #833."""
    head = "\n".join([f"Here's what you want: {asked}", *lines])
    if missing:
        tail = f"Couldn't find: {missing}."
    elif lines and leg:
        tail = f"But no {leg} matched these."
    else:
        tail = "But none matched."
    # #1301 (F8): a staff profile gets no bot-initiated offer; its caller passes no team.
    # ESCALATION-CONTROL (owner ruling Q2): a blocked contact's caller passes the salesman
    # line itself, which stands where the offer would.
    if team == refer.SALESMAN_TEAM:
        return refer.after(f"{head}\n\n{tail}", sep=" ")
    return f"{head}\n\n{tail} Would you like me to escalate to {team} team?" if team else f"{head}\n\n{tail}"


def described_members_reply(predicate: Any, team: Any) -> str | None:
    """A set that qualified nothing, in `what_you_want_reply`: the described products
    (`predicate.members`, the set without its legs) under "Here's what you want", then
    "But no incoming matched these". None when the resolver sent no members."""
    members = jsc.get(predicate, "members")
    if not isinstance(members, dict):
        return None
    require = jsc.get(predicate, "require") or {}
    labels = [jsc.js_string(c) for c in jsc.array(jsc.get(predicate, "class_labels")) if jsc.truthy(c)]
    noun = set_noun_for(labels)
    phrase = described_set_phrase(jsc.get(predicate, "description"), noun, {})
    total = int(jsc.get(members, "total") or 0)
    lines = [f"• {jsc.js_string(c)}" for c in jsc.array(jsc.get(members, "codes")) if jsc.truthy(c)]
    lines = lines or breakdown_lines(jsc.get(members, "breakdown"), noun)
    leg = next(iter(require), "")
    noun_of_leg = (
        jsc.js_string(require[leg]).strip().lower() if leg == "attachment_type" else _MISS_NOUN.get(leg, "")
    )
    return what_you_want_reply(f"{phrase} ({total:,})" if total else phrase, lines, leg=noun_of_leg, team=team)


def near_miss_reply(predicate: Any, team: Any) -> str:
    """A set none of whose described products exist ("gunmetal bathtubs"), in
    `what_you_want_reply`: the key's other values the set does hold, one line each, as
    the resolver counted them (`predicate.near_miss`), then "Couldn't find: gunmetal
    (finish or colour)". The same reply an unknown value gets."""
    near = jsc.get(predicate, "near_miss")
    labels = [jsc.js_string(c) for c in jsc.array(jsc.get(predicate, "class_labels")) if jsc.truthy(c)]
    noun = set_noun_for(labels)
    asked = described_set_phrase(jsc.get(predicate, "description"), noun, jsc.get(predicate, "require") or {})
    lines = breakdown_lines({"rows": jsc.array(jsc.get(near, "other_values"))}, set_noun_for(
        [jsc.js_string(c) for c in jsc.array(jsc.get(near, "class_labels")) if jsc.truthy(c)] or labels
    ))
    value = jsc.js_string(jsc.get(near, "value")).strip()
    label = jsc.js_string(jsc.get(near, "label")).strip().lower()
    missing = f"{_value_words(value)} ({label})" if value and label else ""
    return what_you_want_reply(asked, lines, missing=missing, team=team)


def unknown_values_reply(unknown: Any, predicate: Any, team: Any, *, subject: str = "") -> str:
    """A value the registry does not know ("pink" as a finish or colour), in
    `what_you_want_reply`: the subject with the value as asked, the subject broken down
    by that key (`predicate.members.breakdown`), then "Couldn't find: pink (finish or
    colour)". No list of the registry's choices and no typo said back."""
    members = jsc.get(predicate, "members") if isinstance(predicate, dict) else None
    labels = [jsc.js_string(c) for c in jsc.array(jsc.get(members, "class_labels")) if jsc.truthy(c)]
    noun = set_noun_for(labels)
    said = [jsc.js_string(jsc.get(u, "said")).strip() for u in jsc.array(unknown)]
    said = [w for w in said if w]
    description = [
        *jsc.array(jsc.get(members, "description")),
        *({"key": "said", "label": "", "value": w, "kind": "enum"} for w in said),
    ]
    # No described set to break down (the product route's own reading): the customer's
    # own words for the subject.
    asked = described_set_phrase(description, noun, {}) if members is not None else (subject or " ".join(said))
    missing = _and_list(
        [
            f"{w} ({jsc.js_string(jsc.get(u, 'label')).strip().lower()})"
            if jsc.js_string(jsc.get(u, "label")).strip()
            else f'"{w}"'
            for u in jsc.array(unknown)
            for w in [jsc.js_string(jsc.get(u, "said")).strip()]
            if w
        ]
    )
    lines = breakdown_lines(jsc.get(members, "breakdown"), noun) if members is not None else []
    return what_you_want_reply(asked, lines, missing=missing, team=team)


def _value_words(value: str) -> str:
    """A display value inside a sentence: lower case, an acronym or a single capital
    letter kept ("P trap", "PVC")."""
    return " ".join(w if w.isupper() or w.lower() in SPEC_ACRONYMS else w.lower() for w in value.split())


def described_set_phrase(description: Any, set_noun: str, require: dict[str, Any]) -> str:
    """The described set in one phrase: "Sorento gunmetal wash basins with incoming
    stock", "Sorento kitchen sinks with thickness 1.2 mm with stock".

    Off the resolver's own labelled bindings (`product_predicate_service.describe_set`):
    the brand, then every named choice as a word before the noun, then the noun (the
    product type when one was named, else the class), then each measurement, then what
    the set has. Fix round 8 on PR #833: this replaces the bold "*Label:* value" lines the
    set header used to carry (owner retest of round 7: "the answer should look exactly
    when i check stock by product code")."""
    brand = ""
    noun = ""
    before: list[str] = []
    after: list[str] = []
    for entry in jsc.array(description):
        key = jsc.js_string(jsc.get(entry, "key") or "").strip()
        label = jsc.js_string(jsc.get(entry, "label") or "").strip()
        value = jsc.js_string(jsc.get(entry, "value") or "").strip()
        kind = jsc.js_string(jsc.get(entry, "kind") or "").strip()
        if not value:
            continue
        if key == "brand":
            brand = value
        elif key == "class":
            noun = set_noun if " or " in value else set_noun_for([value])
        elif kind == "numeric":
            after.append(f"{label.lower()} {value}")
        elif kind == "boolean":
            before.append(label.lower() if value.lower() == "yes" else f"not {label.lower()}")
        else:
            before.append(_value_words(value))
    words = [w for w in [brand, *before, noun or set_noun] if w]
    phrase = " ".join(words)
    if after:
        phrase += f" with {_and_list(after)}"
    has = _header_predicate_phrase(require) if require else ""
    return f"{phrase} with {has}" if has else phrase


def build_set_header(
    qualifying_total: int,
    shown: int,
    set_noun: str,
    require: dict[str, Any],
    *,
    description: Any = None,
    not_understood: Any = None,
    offset: int = 0,
    previous_total: int | None = None,
    exhausted: bool = False,
    breakdown: Any = None,
) -> str:
    """A counted set's ONE intro line, the same opener a set that qualified nothing has
    (`what_you_want_reply`), with the described set, what it has and its count; the rows
    under it are exactly the product-code rows:

        Here's what you want: Sorento wash basins with stock (276, showing 1 to 10)

    Fix round 12 on PR #833: the leg's word is in the phrase and nowhere else, so a stock,
    incoming, certificate or promotion ask gets one shape and the leg picks only the rows.
    No paging (owner ruling, 26 Sep 2026): every qualifying product listed says the count
    alone; a named count says which ones; none listed (a set longer than `SET_LIST_MAX`)
    gives the breakdown by the next attribute.

    A pure string function: `qualifying_total` and `shown` are counts the caller already
    has, never re-derived here."""
    # The leg is part of the phrase ("gunmetal wash basins with stock"); a product ask
    # about a described set (`{"price": "described"}`) names no leg at all.
    phrase = described_set_phrase(description, set_noun, require or {})
    withheld = not exhausted and shown <= 0 and qualifying_total > 0
    if withheld or exhausted or shown >= qualifying_total - offset:
        count = f"{qualifying_total:,}"
    else:
        count = f"{qualifying_total:,}, showing {offset + 1} to {offset + shown}"
    header = f"{SET_OPENER} {phrase} ({count})"
    if previous_total and previous_total != qualifying_total:
        # W4: the page re-counted and the set moved since the question; say so.
        header += f". It was {previous_total:,} when you asked."
    if exhausted:
        # Round 3 W2: a count after the last page; every product was already listed.
        header += f"{'' if header.endswith('.') else '.'} That is all {qualifying_total:,}."
    elif withheld:
        # Fix round 9 on PR #833 (owner: "no cap", "always break it down"): the full count
        # and the set broken down by the next attribute, one line each, never a paging
        # question.
        lines = breakdown_lines(breakdown, set_noun)
        if lines:
            header += "\n" + "\n".join(lines)
    return header


#: The other-brands line's own phrase per leg ("Other brands with stock"), owner hand
#: test round 3 on PR #833: "with certificates" / "with incoming" / "with stock".
_OTHER_BRANDS_NOUN: dict[str, str] = {
    "certificate": "certificates",
    "stock": "stock",
    "incoming": "incoming",
    "promotion": "a promotion",
}


def other_brands_line(other_brands: Any, require: dict[str, Any]) -> str:
    """"Other brands with stock: Bravat 79, Cabana 57. Name one to see them." - the
    last line of a reply answered for the company's default brand because the customer
    named none (owner hand test round 3 on PR #833, W4). Every other brand with its full
    count, largest first; "" when there is none."""
    others = [
        f"{jsc.js_string(jsc.get(o, 'brand')).strip()} {int(jsc.get(o, 'count') or 0):,}"
        for o in jsc.array(other_brands)
        if jsc.js_string(jsc.get(o, "brand")).strip() and jsc.get(o, "count")
    ]
    if not others:
        return ""
    parts: list[str] = []
    for key, value in (require or {}).items():
        if key == "attachment_type":
            label = jsc.js_string(value).strip().lower()
            parts.append(label if label else "an attachment")
        elif key == "certificate" and isinstance(value, dict) and jsc.js_string(jsc.get(value, "scheme")).strip():
            parts.append(f"{jsc.js_string(jsc.get(value, 'scheme')).strip()} certificates")
        elif key in _OTHER_BRANDS_NOUN:
            parts.append(_OTHER_BRANDS_NOUN[key])
    phrase = f" with {_and_list(parts)}" if parts else ""
    return f"Other brands{phrase}: {', '.join(others)}. Name one to see them."


# REV-N2/AC-1337 (third console pass): the irregular endings a bare "+s" gets
# wrong - tried on the label's LAST word before the default rule.
_IRREGULAR_PLURAL_ENDINGS: dict[str, str] = {
    "accessory": "accessories",
    "jacuzzi": "jacuzzis",
}


def set_noun_for(class_labels: list[str] | None) -> str:
    """AC-1316 (work item E2): the header's noun, off the described set's class
    label(s). Exactly one class names a single noun ("Tap" -> "taps"); zero
    classes (no class bound the described set at all) or more than one (a blended
    set with no single noun) both fall back to the generic "products".

    REV-N2/AC-1337: pluralised via `_IRREGULAR_PLURAL_ENDINGS` first ("Bathroom
    Accessory" -> "bathroom accessories", never the bare "+s" rule's
    "bathroom accessorys"), the default "+s" rule otherwise.
    """
    labels = [label for label in (class_labels or []) if label and label.strip()]
    if len(labels) != 1:
        return "products"
    words = labels[0].strip().split()
    if not words:
        return "products"
    last = words[-1].lower()
    words[-1] = _IRREGULAR_PLURAL_ENDINGS.get(last, f"{last}s")
    return " ".join(w.lower() for w in words)


#: The qualifying ids one described set is counted and fetched from - the resolver's own
#: cap, so a 2,704-long set is counted in full but never carried as a list of that size.
SET_ID_CAP = 200


def unknown_values_sentence(unknown: Any) -> str:
    """"I don't know 't trap' as a trap. I know P trap and S trap." - round 4 R6 (owner
    console test on PR #833: "the water closet t trap ask, why it match s trap?"). One
    sentence pair per unknown value, off `product_spec_search.unknown_spec_values`."""
    parts = []
    for u in jsc.array(unknown):
        said = jsc.js_string(jsc.get(u, "said")).strip()
        label = jsc.js_string(jsc.get(u, "label")).strip().lower()
        known = [jsc.js_string(k) for k in jsc.array(jsc.get(u, "known")) if jsc.truthy(k)]
        if not said:
            continue
        if not label:
            # Fix round 8 on PR #833: a descriptor no specification names at all.
            parts.append(f"I don't know '{said}' as anything I can search products by.")
            continue
        line = f"I don't know '{said}' as a {label}."
        if known:
            line += f" I know {_and_list(known)}."
        parts.append(line)
    return " ".join(parts)


def near_miss_sentence(near: Any, require: dict[str, Any]) -> str:
    """Round 4 R4 (owner console test on PR #833, "so it got match or not? i have no
    visibility into whether it does the matching"): a set that qualifies nothing says
    the value it looked for and what the set holds in the key's other values:

        No gunmetal wash basins with incoming stock (I looked for Finish or colour:
        Gunmetal among wash basins). 12 wash basins have incoming stock in another
        finish or colour: Chrome 5, Matt black 4, White 3.

    `near` is the resolver's `predicate.near_miss` (`product_predicate_service._near_miss`)."""
    labels = [jsc.js_string(c).strip() for c in jsc.array(jsc.get(near, "class_labels")) if jsc.truthy(c)]
    noun = set_noun_for(labels)
    singular = labels[0].lower() if len(labels) == 1 else "product"
    label = jsc.js_string(jsc.get(near, "label")).strip()
    value = jsc.js_string(jsc.get(near, "value")).strip()
    with_what = _predicate_phrase(require)
    has_what = _header_predicate_phrase(require)
    # An acronym keeps its capitals, word by word ("No PVC pipe wash basins"; PR #833
    # round 5 N2, round 6 N-r5-2).
    named = " ".join(w if w.isupper() or w.lower() in SPEC_ACRONYMS else w.lower() for w in value.split())
    said = f"No {named} {noun} with {with_what} (I looked for {label}: {value} among {noun})."
    total = int(jsc.get(near, "other_total") or 0)
    if not total:
        return f"{said} No {noun} have {with_what} in any {label.lower()}."
    others = ", ".join(
        f"{jsc.js_string(jsc.get(o, 'value'))} {int(jsc.get(o, 'count') or 0):,}"
        for o in jsc.array(jsc.get(near, "other_values"))
    )
    counted = f"1 {singular} has" if total == 1 else f"{total:,} {noun} have"
    return f"{said} {counted} {has_what} in another {label.lower()}: {others}."


def not_found_error_message(
    item: dict[str, Any] | None,
    *,
    parser: dict[str, Any] | None,
    resolved: dict[str, Any] | None,
    gate: dict[str, Any] | None,
    entitlement_levels: Any = None,
    profile: Any = None,
    granted_keys: Any = None,
) -> dict[str, Any]:
    """`not-found-error-message`: the miss reply, its search-scope header and its bullets.

    `granted_keys` (DO-ASK-SIMPLIFY security B1): the contact's `contact_field_reveals`
    grants (`ctx["access"]["attributes"]`). The "hasn't been delivered yet" line names the
    order's current status only with `delivery_orders.status`; None is the empty set, so a
    caller that passes nothing never leaks it.

    `profile` (#1262 slice 11, F8 follow-up): the SAME staff-audience gate `turn/
    compose.py` and `answer_bridge.py`'s cross-domain ladder already apply -
    `is_staff_profile(profile)` suppresses the bot-initiated "Would you like me to
    escalate to X team?" clause this function's own `build_breakdown_msg` closure
    appends, never the rest of the miss sentence (a staff rep still reads "But no
    order matched these", just with no offer tacked on). `None` (every caller that
    predates this slice) reads as not-staff, byte-identical to before.

    H16 is structural here: `resolvedTypes` is `Object.keys(by_entity_type)` and every OTHER
    read of that object goes through `Object.values(...)`, so a metadata key on it can only
    reach the customer through the ONE arm that names the keys - which is the arm the
    contract test drives.

    `entitlement_levels` is the `Aggregate` node's `name` array (the contact's entitlement
    union), read only by the promotion entitlement-miss arm and only through an `isExecuted`
    guard - `None` reproduces the not-executed arm, which says "not available to you" rather
    than inventing a level (B9).
    """
    q = parser if isinstance(parser, dict) else {}
    r = resolved if isinstance(resolved, dict) else {}
    g = gate if isinstance(gate, dict) else {}
    from app.services.chatbot.turn.state import escalation_barred, offers_escalation

    # ESCALATION-CONTROL: staff and a barred contact alike get no offer; a barred contact's
    # miss says what was not found, then "Please refer to your salesman." (owner ruling Q2).
    is_staff = not offers_escalation(profile)
    barred = escalation_barred(profile)

    by_entity_type = jsc.get(r, "by_entity_type")
    resolved_types = list(by_entity_type.keys()) if isinstance(by_entity_type, dict) else []
    entities_list = q["entities"] if isinstance(jsc.get(q, "entities"), list) else []
    parser_hints = [jsc.get(e, "hint") for e in entities_list] if isinstance(jsc.get(q, "entities"), list) else []
    have_attachment_type = (
        "attachment_type" in resolved_types or "attachment_type" in parser_hints
    )

    gate_passed = jsc.get(g, "gate_passed") is not False
    gate_reason = jsc.get(g, "gate_reason") or ""
    allowed_lookup = jsc.get(jsc.get(g, "gate_debug"), "allowed_lookup")
    allowed_types = allowed_lookup if allowed_lookup is not None else []

    domain_hint = jsc.get(q, "domain_hint")
    # R34/AC-1359: a resolver predicate whose `require` carries `certificate`
    # or `attachment_type` already IS the document-type answer - the gate's
    # own fix (R34, `gate.py`) means `gate_passed` is normally already True
    # here too, but this stays a direct check of the SAME fact rather than
    # relying on that alone: "any tap has PPS cert" recovers the leg off the
    # message text server-side, and asking for the attachment type again
    # would discard a predicate that already qualified (or honestly missed)
    # a real row.
    predicate = jsc.get(r, "predicate")
    predicate_require = jsc.get(predicate, "require") if isinstance(predicate, dict) else None
    has_document_leg = isinstance(predicate_require, dict) and (
        jsc.truthy(predicate_require.get("certificate")) or jsc.truthy(predicate_require.get("attachment_type"))
    )
    missing_attachment_type = (
        domain_hint == "product_attachment"
        and not gate_passed
        and not have_attachment_type
        and not has_document_leg
    )
    unresolved = jsc.array(jsc.get(r, "unresolved_tokens"))
    has_unresolved = len(unresolved) > 0

    # needsScope: the gate failed for lack of scope AND the user genuinely gave nothing. A
    # token that just did not resolve is a lookup miss, not a scope gap.
    needs_scope = (
        not gate_passed
        and not missing_attachment_type
        and not has_unresolved
        # D11-reproduced: `not-found-error-message`'s own gate_reason probe - the GATE's own
        # machine-written reason string, never a customer's words.
        and bool(_SCOPING_ENTITY_RE.search(jsc.js_string(gate_reason)))
    )

    order_status = jsc.get(q, "order_status")
    status_label = (
        "outstanding "
        if (domain_hint == "order" and order_status == "outstanding")
        else ("delivered " if (domain_hint == "order" and order_status == "delivered") else "")
    )
    # Fix round 10 on PR #833: read before the branches, so the unknown-value reply below
    # has its team on every path (it failed on the needs-scope one).
    routing = jsc.get(q, "routing")
    suggested_team = jsc.get(routing, "suggested_team") if jsc.truthy(routing) else None
    # Owner ruling 22 Sep 2026, R6 (AC-EQ-12..14): a stock/incoming question with
    # NO team named at all must still get the domain's own escalation team
    # (inventory -> warehouse, incoming -> purchasing), never the generic
    # "customer_service" literal. On the `engine.run_turn` path
    # `turn_runtime.lane_parse_output` is what actually fills a null
    # `routing.suggested_team` (its own `DEFAULT_SUGGESTED_TEAM` chain, now
    # domain-aware - see that function), so `suggested_team` read off
    # `q["routing"]` here is rarely still falsy by the time a real TURN reaches
    # this composer. This is the SAME fallback anyway, kept as the direct-call
    # belt-and-braces: a caller that reaches `complete_answer` directly, bypassing
    # `run_turn`/`lane_parse_output` entirely, builds its `parser` dict with no
    # `routing` key at all - `test_s6c_answer_lane.py::TestErrorArmRendersTheMissLane
    # .test_the_error_arm_reaches_the_miss_renderer` is exactly that shape
    # (`domain_hint = "inventory"`, no `routing` key), and pins this fallback
    # directly; this composer must not hand a caller like that the generic literal
    # either.
    if not jsc.truthy(suggested_team):
        from app.services.chatbot.turn.policy import default_policy

        domain_row = default_policy().domain(
            jsc.js_string(domain_hint if jsc.truthy(domain_hint) else "").lower()
        )
        suggested_team = domain_row.escalation_team_code if domain_row is not None else None
    team = _pretty_team(suggested_team if jsc.truthy(suggested_team) else "customer_service")

    def _esc_offer(offer_team: Any = None) -> str:
        """#1262 slice 11 (F8) follow-up (coordinator round 2): the ONE place every
        "Would you like me to escalate to X team?" clause in this function is built -
        AC-S11-1 names the outstanding-report miss explicitly, and this function has
        ELEVEN such clauses (grepped, not guessed), each hand-written before this
        slice. A per-branch `if is_staff: ... else: ...` at each site is exactly the
        "one branch missed later" risk the coordinator flagged; routing every one of
        them through this closure instead means a TWELFTH branch added after this
        slice inherits the gate for free, rather than needing its own author to
        remember it. `""` for a staff profile - the caller appends it only when
        truthy, so no branch prints a dangling space or period-with-nothing-after.
        """
        if barred:
            return refer.sentence()
        t = offer_team if jsc.truthy(offer_team) else team
        return "" if is_staff else f"Would you like me to escalate to {t} team?"

    # The one reply structure's own offer (`what_you_want_reply`) under the same gate.
    offer_team = refer.SALESMAN_TEAM if barred else (None if is_staff else team)

    escalate_message: Any = None
    is_clarification = False
    # datemiss-summary: the resolved-entity bullets, exposed so `build-suggest-offer` can show
    # them on the date-relaxation offer too, not only in `escalate_message`.
    found_summary = ""

    if missing_attachment_type:
        subject = jsc.find(entities_list, lambda e: jsc.get(e, "hint") == "product")
        subject_text = (
            f"{jsc.js_string(jsc.get(subject, 'hint'))} {jsc.js_string(jsc.get(subject, 'raw'))}"
            if jsc.truthy(subject)
            else "the requested product"
        )
        escalate_message = (
            f"Please provide the attachment type for {subject_text} "
            f"- e.g. product image, technical drawing, or certificate."
        )
        is_clarification = True

    elif needs_scope:
        # The customer reaches this by CLEARING every filter one at a time and landing on a
        # request for every delivery order ever. Refusing is right; the old wording named the
        # internal entity types as the fix and never said the one useful thing - that a single
        # filter is enough to continue.
        scope_word = _SCOPE_WORD.get(
            jsc.js_string(domain_hint if jsc.truthy(domain_hint) else "").lower()
        ) or _plain_words(domain_hint if jsc.truthy(domain_hint) else "that")
        asked: list[str] = []
        for entity_type in (allowed_types if isinstance(allowed_types, list) else []):
            word = _HUMAN_SCOPE.get(
                jsc.js_string(entity_type if jsc.truthy(entity_type) else "").lower()
            )
            if word and word not in asked:
                asked.append(word)
        # The date range is one MORE option, so it belongs INSIDE the list; appending it after
        # a finished list produced "a order number, transporter, or customer, or a date range".
        # Offered only where the domain's own tool takes one - see
        # `_domain_takes_a_date_filter`.
        options = (asked[:3] if asked else ["customer", "product code"]) + (
            ["date range"] if _domain_takes_a_date_filter(domain_hint) else []
        )
        article = "an" if _VOWEL_HEAD_RE.match(options[0]) else "a"
        escalate_message = (
            f"That would search every {scope_word} we have - I need at least one filter to "
            f"narrow it down. Give me {article} {_human_list(options)}, and I can look it up."
        )
        is_clarification = True

    else:
        tokens = jsc.array(jsc.get(r, "tokens"))
        # #11: the access-level phrase arrives as a resolver TOKEN and the `access` suffix
        # already names it, so without this filter the level prints twice.
        access_levels = q["access_levels"] if isinstance(jsc.get(q, "access_levels"), list) else []
        access_set = {
            jsc.nullish_str(a).strip().lower()
            for a in access_levels
            if jsc.truthy(jsc.nullish_str(a).strip().lower())
        }

        def not_access(token: Any) -> bool:
            return jsc.nullish_str(token).strip().lower() not in access_set

        token_text = " ".join(jsc.js_string(t) for t in tokens if not_access(t))
        unresolved_text = ", ".join(jsc.js_string(t) for t in unresolved if not_access(t))

        if resolved_types and token_text:
            requested = f"{'/'.join(_prettify_type(t) for t in resolved_types)} {token_text}"
        elif token_text:
            requested = token_text
        elif unresolved_text:
            requested = unresolved_text
        else:
            kept = [e for e in entities_list if not_access(jsc.get(e, "raw"))]
            # #11: '' (not 'the requested item') so the " for ..." segment can be dropped
            # entirely - the access suffix already says what was searched for.
            # Fix round 8 on PR #833: the owner read "Could not find incoming for category
            # gunmetal basin": a set the resolver described is named as its answer would
            # name it, and an entity kind is never said as its internal key.
            described = jsc.array(jsc.get(predicate, "description")) if isinstance(predicate, dict) else []
            labels = [jsc.js_string(c) for c in jsc.array(jsc.get(predicate, "class_labels")) if jsc.truthy(c)] if described else []
            requested = (
                described_set_phrase(described, set_noun_for(labels), {}).removesuffix(" with that")
                if described
                else ", ".join(
                    jsc.js_string(jsc.get(e, "raw"))
                    if jsc.get(e, "hint") in ("specification", "category", "product_type")
                    else f"{_plain_words(jsc.get(e, 'hint') or 'item')} {jsc.js_string(jsc.get(e, 'raw'))}"
                    for e in kept
                )
                if kept
                else ""
            )

        date_start = jsc.get(q, "date_filter_start")
        date_end = jsc.get(q, "date_filter_end")
        date_range = (
            f" from {jsc.js_string(date_start)} to {jsc.js_string(date_end)}"
            if (jsc.truthy(date_start) and jsc.truthy(date_end))
            else ""
        )
        # S2: the spine sends the contact's ENTITLEMENT UNION when the customer names no
        # level, while `q.access_levels` stays empty - so an `|| 'End User'` fallback would
        # print a level that was never searched. Name a level only when the customer named one.
        access = (
            f" for {', '.join(jsc.js_string(a) for a in access_levels)}"
            if (jsc.get(q, "intent_hint") == "check_promotion" and access_levels)
            else ""
        )
        is_active = jsc.get(q, "is_active")
        active_inactive = (
            " active"
            if _js_loose_eq_bool(is_active, True)
            else (" inactive" if _js_loose_eq_bool(is_active, False) else "")
        )
        all_ents = entities_list
        by_raw: dict[str, Any] = {}
        for entity in all_ents:
            by_raw[_nf_norm_raw(jsc.get(entity, "raw"))] = entity

        # Resolved-entity breakdown from `gate.compatible_entities`, the authoritative
        # resolved set: an entity-not-found miss must read differently from an
        # entity-found-but-no-domain-data miss.
        compat = g["compatible_entities"] if isinstance(jsc.get(g, "compatible_entities"), list) else []
        compat_uuids = {jsc.get(c, "uuid") for c in compat}
        all_matches: list = []
        all_matches.extend(jsc.array(jsc.get(r, "intersection")))
        if isinstance(by_entity_type, dict):
            for value in by_entity_type.values():
                all_matches.extend(value if isinstance(value, list) else [value])
        for res in jsc.array(jsc.get(r, "resolutions")):
            matches = jsc.get(res, "matches")
            all_matches.extend(
                matches if isinstance(matches, list) else ([] if matches is None else [matches])
            )

        disp_by_uuid: dict[Any, Any] = {}
        for match in all_matches:
            if not jsc.truthy(match):
                continue
            display = jsc.get(match, "display") or {}
            if jsc.get(match, "entity_type") in _ORDER_TYPES:
                # order-ish: the user identifies by the DO / order NUMBER, so show the code
                # and add the customer for context.
                code = jsc.get(match, "canonical_code")
                customer = jsc.get(display, "customer_name")
                if jsc.truthy(code):
                    name = f"{jsc.js_string(code)} ({jsc.js_string(customer)})" if jsc.truthy(customer) else code
                else:
                    name = customer if jsc.truthy(customer) else ""
            elif jsc.get(match, "entity_type") in _CODE_FIRST_TYPES:
                # CODE FIRST. The code is what the customer typed, what is on the carton and
                # what they will type again; the name is a description for somebody else.
                # It falls back to the display name only when there is no code at all, so
                # the entity is still named rather than dropped.
                #
                # The name is deliberately NOT appended in parentheses today: on the turn
                # this rule comes from it is junk, and "IBKS7245-NG-BL (Iborn. Bidet.)"
                # publishes the junk next to the answer instead of in place of it. The
                # trigger for adding it is a measured turn where the code alone is
                # genuinely ambiguous to the reader.
                code = jsc.get(match, "canonical_code")
                if jsc.truthy(code):
                    name = code
                else:
                    name = ""
                    for key in _DISPLAY_NAME_KEYS:
                        value = jsc.get(display, key)
                        if jsc.truthy(value):
                            name = value
                            break
            else:
                # `attachment_type` shows its `type_name`, NOT the long alias description;
                # `description` stays ahead of `canonical_code` so a promotion (whose code IS
                # its uuid) still shows its name.
                name = ""
                for key in _DISPLAY_NAME_KEYS:
                    value = jsc.get(display, key)
                    if jsc.truthy(value):
                        name = value
                        break
                else:
                    code = jsc.get(match, "canonical_code")
                    name = code if jsc.truthy(code) else ""
            uuid = jsc.get(match, "uuid")
            if jsc.truthy(uuid) and jsc.truthy(name) and uuid not in disp_by_uuid:
                disp_by_uuid[uuid] = name

        # tokens that ACTUALLY produced a compatible entity must NOT be listed as "not found"
        # even if the resolver kept them in `unresolved_tokens` (a fallback-tier match stays
        # there yet resolves).
        resolved_toks: set[str] = set()
        for match in all_matches:
            if jsc.truthy(match) and jsc.get(match, "uuid") in compat_uuids:
                via_token = jsc.get(jsc.get(match, "display"), "via_token")
                if jsc.truthy(via_token):
                    resolved_toks.add(_nf_norm_raw(via_token))
                code = jsc.get(match, "canonical_code")
                if jsc.truthy(code):
                    resolved_toks.add(_nf_norm_raw(code))
        for res in jsc.array(jsc.get(r, "resolutions")):
            matches = jsc.get(res, "matches")
            matches = matches if isinstance(matches, list) else []
            if any(jsc.get(m, "uuid") in compat_uuids for m in matches):
                resolved_toks.add(_nf_norm_raw(jsc.get(res, "token")))

        # multi-company: which company each resolved entity belongs to. One typed code can
        # exist in TWO companies with the SAME canonical code, and the dedup below is on the
        # LABEL STRING, so without the qualifier the two collapse into one bullet.
        co_by_uuid: dict[Any, str] = {}
        for match in all_matches:
            company = jsc.nullish_str(jsc.get(match, "company_name")).strip()
            uuid = jsc.get(match, "uuid")
            if jsc.truthy(match) and jsc.truthy(uuid) and company and uuid not in co_by_uuid:
                co_by_uuid[uuid] = company
        # Keyed to what was ACTUALLY sent to the tool, NEVER to the caller's access list.
        searched_cos: list[str] = []
        for c in compat:
            if jsc.nullish_str(jsc.get(c, "entity_type")) in _NO_TOOL_ID:
                continue
            company = co_by_uuid.get(jsc.get(c, "uuid"))
            if jsc.truthy(company) and company not in searched_cos:
                searched_cos.append(company)
        multi_co = len(searched_cos) > 1

        by_type: dict[Any, list] = {}
        for c in compat:
            entity_type = jsc.get(c, "entity_type")
            entity_type = entity_type if jsc.truthy(entity_type) else "item"
            base = disp_by_uuid.get(jsc.get(c, "uuid"))
            if not jsc.truthy(base):
                # Hand pass 12, Group F: `disp_by_uuid` is built from the RESOLVER's own
                # matches (`r["resolutions"]`), empty on a turn that resolved nothing
                # fresh (a pure pick). The row's OWN `display_name` - `turn_runtime.
                # fill_customer_names`'s DB-resolved per-ledger name - is the same fact
                # by a different route, and outranks the code for the same reason
                # `disp_by_uuid` does: a code is not a name, and a multi-ledger pick's
                # own code names EVERY ledger it covers at once.
                base = jsc.get(c, "display_name")
            if not jsc.truthy(base):
                base = jsc.get(c, "code")
            # A uuid is not a name. When neither the resolver display nor the code yields a
            # human-readable identifier the candidate is DROPPED, never printed raw - the
            # console run rendered "inbound_shipment: ecfdaf8f-... (Mocha)" from this arm.
            if not jsc.truthy(base) or _ms_is_uuid(base):
                continue
            company = co_by_uuid.get(jsc.get(c, "uuid"))
            # Qualify ONLY in the multi-company case: one company keeps today's bare label
            # byte for byte, and a suffix there is noise about a distinction the customer has
            # no reason to care about.
            label = f"{jsc.js_string(base)} ({company})" if (multi_co and jsc.truthy(company)) else base
            arr = by_type.setdefault(entity_type, [])
            if label not in arr:
                arr.append(label)

        # #12: a code the customer typed EXACTLY must be the representative. `_compat` order
        # is arbitrary, so `codes[0]` could name a sibling variant.
        tok_set = {
            jsc.nullish_str(t).strip().lower()
            for t in tokens
            if jsc.truthy(jsc.nullish_str(t).strip().lower())
        }

        def bare_label(label: Any) -> str:
            return (
                _TRAILING_PAREN_RE.sub("", jsc.js_string(label))
                if multi_co
                else jsc.js_string(label)
            )

        for arr in by_type.values():
            index = jsc.find_index(arr, lambda l: bare_label(l).strip().lower() in tok_set)
            if index > 0:
                arr.insert(0, arr.pop(index))

        def _entitlement_miss() -> Any:
            if domain_hint != "promotion":
                return None
            promo_matches: list = []

            def push(arr: Any) -> None:
                for m in (arr if isinstance(arr, list) else []):
                    if jsc.truthy(m) and jsc.get(m, "entity_type") == "promotion":
                        promo_matches.append(m)

            push(jsc.get(r, "intersection"))
            push(jsc.get(by_entity_type, "promotion"))
            for res in jsc.array(jsc.get(r, "resolutions")):
                push(jsc.get(res, "matches"))
            if not promo_matches:
                return None
            seen: list = []
            uniq: list = []
            for m in promo_matches:
                key = jsc.get(m, "uuid") or jsc.get(m, "canonical_code")
                if not jsc.truthy(key) or key in seen:
                    continue
                seen.append(key)
                uniq.append(m)
            named = [
                jsc.get(jsc.get(m, "display"), "description")
                for m in uniq
                if jsc.truthy(jsc.get(jsc.get(m, "display"), "description"))
            ]
            if not named:
                return None
            extra = len(named) - 1
            label = jsc.js_string(named[0]) + (
                f" and {extra} other{'s' if len(named) > 2 else ''}" if len(named) > 1 else ""
            )
            # An INACTIVE promotion has ENDED rather than being withheld (B8) - blaming access
            # there would be a second false statement, not a fix for the first.
            any_active = any(
                jsc.truthy(jsc.get(m, "display")) and jsc.get(jsc.get(m, "display"), "is_active") is not False
                for m in uniq
            )
            if not any_active:
                esc = _esc_offer()
                return f"{label} has ended, so there is nothing to send." + (f" {esc}" if esc else "")
            levels = [
                jsc.js_string(x if jsc.truthy(x) else "").strip()
                for x in jsc.array(entitlement_levels)
            ]
            levels = [x for x in levels if x]
            at = f" at your access level ({', '.join(levels)})" if levels else " to you"
            esc = _esc_offer()
            return f"{label} is not available{at}." + (f" {esc}" if esc else "")

        # Called where it is used (`build_breakdown_msg`), not here: a barred contact's
        # refer line marks the turn for Customer asks (`turn/refer.py`), so it is built only
        # for the reply that sends it (CUSTOMER-ASKS-REFER-ONLY review).

        # We may summarise our OWN expansions; we may never hide something the customer asked
        # for by name. `resolutions` maps each typed token to what it matched, so a code
        # counts as TYPED when the token IS that code, not when it is a fragment we grew into
        # it. When `resolutions` is absent nothing is registered and every line falls back to
        # today's output - fail toward the shorter line, never toward a wall of codes.
        typed_order: dict[str, int] = {}
        typed_seq = 0
        for res in jsc.array(jsc.get(r, "resolutions")):
            token = _type_norm(jsc.get(res, "token"))
            if not token:
                continue
            hits: list[str] = []
            matches = jsc.get(res, "matches")
            for m in (matches if isinstance(matches, list) else []):
                if not jsc.truthy(m) or jsc.get(m, "uuid") not in compat_uuids:
                    continue
                group = _type_norm(
                    disp_by_uuid.get(jsc.get(m, "uuid"))
                    or jsc.get(m, "canonical_code")
                    or jsc.get(m, "uuid")
                )
                if group and (token == _type_norm(jsc.get(m, "canonical_code")) or token == group):
                    if group not in hits:
                        hits.append(group)
            # ONE token naming SEVERAL distinct things is the resolver expanding, not the
            # customer listing - only a token that lands on exactly one thing is a code the
            # customer asked for by name.
            if len(hits) != 1:
                continue
            group = hits[0]
            if group not in typed_order:
                typed_order[group] = typed_seq
                typed_seq += 1

        # #1262 fix lane round 2, S1 (AC-S5-4): the parser's own per-entity quantity,
        # printed beside the code it belongs to through the ONE label rule
        # (`turn/state.py::focus_row_label`, "M210-GM (x5)") - never a regex.
        from app.services.chatbot.turn.state import focus_row_label

        qty_by_code: dict[str, Any] = {}
        for entity in all_ents:
            quantity = jsc.get(entity, "quantity") if jsc.truthy(entity) else None
            if not jsc.truthy(quantity):
                continue
            for key in (jsc.get(entity, "raw"), jsc.get(entity, "canonical_code")):
                if _type_norm(key):
                    qty_by_code.setdefault(_type_norm(key), quantity)

        def with_quantity(label: Any) -> str:
            quantity = qty_by_code.get(_type_norm(bare_label(label)))
            if quantity is None:
                return jsc.js_string(label)
            return jsc.js_string(focus_row_label({"raw": jsc.js_string(label), "quantity": quantity}))

        found_lines: list[str] = []
        for entity_type, codes in by_type.items():
            # The cap is over DISTINCT CODES, not over labels: a turn that resolved eight
            # distinct products in a multi-company set would otherwise dump all eight into the
            # WhatsApp reply. Single-company is byte-identical, because `bare_label` is the
            # identity there and every label is its own group.
            order: list[str] = []
            by_code: dict[str, list] = {}
            for label in codes:
                bare = bare_label(label)
                if bare not in by_code:
                    by_code[bare] = []
                    order.append(bare)
                by_code[bare].append(label)
            typed = [b for b in order if _type_norm(b) in typed_order]
            typed.sort(key=lambda b: typed_order[_type_norm(b)])
            named_codes = typed if typed else [order[0]]
            if entity_type == "attachment_type":
                # ATTACHMENT-MULTI R1: every document type in scope is one the customer asked
                # for ("photo" resolved to Product Photos), never resolver expansion, so each
                # is named rather than "(+1 more)", in the order the customer named them
                # (the resolver's own token order).
                asked_order: list[str] = []
                for res in jsc.array(jsc.get(r, "resolutions")):
                    for m in jsc.array(jsc.get(res, "matches")):
                        if jsc.get(m, "entity_type") != "attachment_type":
                            continue
                        for spelling in (jsc.get(jsc.get(m, "display"), "type_name"), jsc.get(m, "canonical_code")):
                            if jsc.truthy(spelling) and _type_norm(spelling) not in asked_order:
                                asked_order.append(_type_norm(spelling))
                named_codes = sorted(
                    order,
                    key=lambda b: asked_order.index(_type_norm(b)) if _type_norm(b) in asked_order else len(asked_order),
                )
            extra = (
                f" (+{len(order) - len(named_codes)} more)"
                if len(order) > len(named_codes)
                else ""
            )
            rendered = ", ".join(
                ", ".join(with_quantity(l) for l in by_code[b]) for b in named_codes
            )
            # ATTACHMENT-MULTI R5: "• attachment type:", never the raw `attachment_type` key.
            found_lines.append(f"• {_prettify_type(entity_type)}: {rendered}{extra}")
        found_summary = "\n".join(found_lines)

        not_found_raw = [t for t in unresolved if _nf_norm_raw(t) not in resolved_toks]
        use_breakdown = len(found_lines) > 0

        # FIRST-wins on a normalised-key collision (a plain map built from `.map()` would keep
        # the LAST entity instead, letting this node and the tail disagree on which entity a
        # token names). Keyed under BOTH `raw` and `canonical_code`, mirroring what
        # `resolve-entity` is SENT.
        by_raw_stripped: dict[str, Any] = {}
        for entity in all_ents:
            key = _type_norm(jsc.get(entity, "raw"))
            if key not in by_raw_stripped:
                by_raw_stripped[key] = entity
            code_key = _type_norm(jsc.get(entity, "canonical_code"))
            if code_key and code_key not in by_raw_stripped:
                by_raw_stripped[code_key] = entity

        def type_of_token(token: Any) -> str:
            res = jsc.find(
                jsc.array(jsc.get(r, "resolutions")),
                lambda x: _type_norm(jsc.get(x, "token")) == _type_norm(token),
            )
            matches = jsc.get(res, "matches") if jsc.truthy(res) else None
            first = matches[0] if isinstance(matches, list) and matches else None
            if jsc.truthy(first) and jsc.truthy(jsc.get(first, "entity_type")):
                return _prettify_type(jsc.get(first, "entity_type"))
            hint = jsc.get(by_raw_stripped.get(_type_norm(token)), "hint")
            return _prettify_type(jsc.js_string(hint)) if jsc.truthy(hint) else ""

        def raw_of_token(token: Any) -> Any:
            entity = by_raw_stripped.get(_type_norm(token))
            raw = jsc.get(entity, "raw")
            return raw if jsc.truthy(raw) else token

        def label_token(token: Any) -> str:
            type_label = type_of_token(token)
            return f'"{jsc.js_string(raw_of_token(token))}"' + (
                f" ({type_label})" if type_label else ""
            )

        # State the SEARCH SCOPE, so "nothing matched" cannot be read as "nothing matched in
        # the one company you were thinking of". Empty on a single-company turn.
        co_suffix = f" - checked in {_and_list(searched_cos)}" if multi_co else ""

        def axis_words(axis: dict[str, Any]) -> str | None:
            type_set = set(axis["types"])
            rows = [
                e
                for e in compat
                if jsc.truthy(e) and _nf_norm_raw(jsc.get(e, "entity_type")) in type_set
            ]
            if not rows:
                return None  # axis never put in scope
            if axis["label"] == "Customer":
                # DO-ASK-SIMPLIFY tester pass 1 (`tail/scope_block._axis_words`): the
                # forced links of an order NUMBER ask print no Customer line, and the
                # grouped name wins over the typed word when the DB has named every row.
                if all(jsc.truthy(jsc.get(row, "scope")) for row in rows) and any(
                    jsc.truthy(e) and _nf_norm_raw(jsc.get(e, "entity_type")) in _ORDER_TYPES for e in compat
                ):
                    return _NO_LINE
                named = [jsc.nullish_str(jsc.get(row, "display_name")).strip() for row in rows]
                if all(named):
                    return customer_header_words([(n, customer_group_of(n)) for n in named])
            words: list[str] = []
            for res in jsc.array(jsc.get(r, "resolutions")):  # 1. the customer's own token
                matches = jsc.get(res, "matches")
                hits_axis = any(
                    jsc.truthy(m) and _nf_norm_raw(jsc.get(m, "entity_type")) in type_set
                    for m in (matches if isinstance(matches, list) else [])
                )
                token = jsc.nullish_str(jsc.get(res, "token")).strip()
                if hits_axis and token:
                    value = jsc.js_string(raw_of_token(token))
                    if value not in words:
                        words.append(value)
            if not words:  # 2. the parser's own hinted raw
                for entity in all_ents:
                    if not jsc.truthy(entity) or _nf_norm_raw(jsc.get(entity, "hint")) not in axis["hints"]:
                        continue
                    value = jsc.nullish_str(jsc.get(entity, "raw")).strip()
                    if value and value not in words:
                        words.append(value)
            if not words and axis["label"] == "Customer":  # 3. group names, once per company
                names = [
                    jsc.nullish_str(
                        jsc.get(row, "display_name")
                        if jsc.truthy(jsc.get(row, "display_name"))
                        else (
                            jsc.get(row, "title")
                            if jsc.get(row, "title") is not None
                            else jsc.get(row, "code")
                        )
                    ).strip()
                    for row in rows
                ]
                return customer_header_words([(n, customer_group_of(n)) for n in names if n])
            if not words:  # 3. last resort: the gate's own label
                for row in rows:
                    # Hand pass 12, Group F: a multi-ledger customer pick's own rows
                    # carry a real per-row `display_name` (`turn_runtime.
                    # fill_customer_names`, DB-resolved by uuid) - preferred over
                    # `title`/`code`, neither of which a customer row has ever set to
                    # anything but the shared account CODE, so a three-ledger pick
                    # named the same code three times instead of three ledgers.
                    display_name = jsc.get(row, "display_name")
                    title = jsc.get(row, "title")
                    value = jsc.nullish_str(
                        display_name
                        if jsc.truthy(display_name)
                        else (title if title is not None else jsc.get(row, "code"))
                    ).strip()
                    if value and value not in words:
                        words.append(value)
            return ", ".join(words)

        def build_breakdown_msg(domain_word: str, not_found_override: Any = None) -> str:
            nf = [
                label_token(t)
                for t in (not_found_override if not_found_override is not None else not_found_raw)
            ]
            parts: list[str] = []
            is_order_scope = (
                jsc.js_string(domain_hint if jsc.truthy(domain_hint) else "").lower()
                in _DATE_SCOPE_DOMAINS
            )
            if is_order_scope:
                start = date_start if jsc.truthy(date_start) else None
                end = date_end if jsc.truthy(date_end) else None
                head: list[str] = []
                for axis in _AXES:
                    words = axis_words(axis)
                    if words is _NO_LINE:
                        continue
                    if axis.get("always"):
                        head.append(f"{axis['label']}: {words or axis['allText']}")
                    elif words:
                        head.append(f"{axis['label']}: {words}")
                # #1262 fix lane round 2, B1: the brand the fetch was filtered by.
                brand_words = live_brand_words(g)
                if brand_words:
                    head.append(f"Brand: {brand_words}")
                # Dates last, and stated even when no window was set: without it "no order
                # matched these" never said whether it had looked at all dates or just a month.
                if not start and not end:
                    dates = "all dates"
                elif start and end and start == end:
                    dates = _fmt_date(start)
                else:
                    dates = (
                        f"{_fmt_date(start) if start else 'earliest'} to "
                        f"{_fmt_date(end) if end else 'today'}"
                    )
                head.append(f"Dates: {dates}")
                parts.append("\n".join(head))
            if found_lines:
                parts.append("Here's what you want:\n" + "\n".join(found_lines))
            if nf:
                parts.append(f"Couldn't find: {', '.join(nf)}.")
            # A WINDOWED MISS NAMES ITS DATES THE WAY THE HEADER DOES, AND OFFERS THE WIDEN.
            # A DO list needs a range of at most 31 days for every contact (owner, 4 Oct 2026,
            # `do_ask.range_reply`), so the invite asks for another month or dates rather
            # than 'all dates'; the frozen escalate contract is preserved by landing the
            # invite BEFORE the would-clause.
            miss_window = (
                f" from {_fmt_date(date_start)} to {_fmt_date(date_end)}"
                if (is_order_scope and jsc.truthy(date_start) and jsc.truthy(date_end))
                else date_range
            )
            # #1262 slice 11 (F8) follow-up: the widen invite (a genuinely useful next
            # step, never a bot-initiated offer) stays for every audience - `is_staff`
            # (read once, at the top of this function, the SAME flag `_esc_offer` uses)
            # only ever drops the ", or would you like me to escalate" half joined onto
            # it. Bespoke rather than routed through `_esc_offer` because this is the
            # ONE site where the clause is comma-joined mid-sentence rather than its own
            # trailing sentence - `_esc_offer`'s own phrasing covers every OTHER site.
            windowed = is_order_scope and (jsc.truthy(date_start) or jsc.truthy(date_end))
            if windowed:
                widen = "Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct)."
                esc_ask = (
                    refer.after(widen, sep=" ")
                    if barred
                    else widen
                    if is_staff
                    else f"{widen[:-1]}, or would you like me to escalate to {team} team?"
                )
            else:
                esc_ask = _esc_offer()
            miss_sentence = (
                # R7 (round 4): "But no purchase order matched", never the domain key.
                f"But no{active_inactive} {_plain_words(domain_word)}{miss_window}{access} matched these{co_suffix}."
            )
            if esc_ask:
                miss_sentence = f"{miss_sentence} {esc_ask}"
            entitlement_miss = _entitlement_miss()
            parts.append(entitlement_miss if jsc.truthy(entitlement_miss) else miss_sentence)
            return "\n\n".join(parts)

        # vague-token clarify: among UNRESOLVED tokens only, map each back to a parser entity
        # by raw and read its `confident` flag. ANY `confident: false` is a vague mash rather
        # than a clear-but-missing record, so CLARIFY with no escalate offer. Default-true:
        # only an explicit `false` fires.
        vague_unresolved = [
            t for t in unresolved if jsc.get(by_raw.get(_nf_norm_raw(t)), "confident") is False
        ]

        if len(vague_unresolved) > 0:
            is_clarification = True  # so escalate-catalog's is_escalate_offer is false
            labels = _human_list(
                [_prettify_type(t) for t in (allowed_types if isinstance(allowed_types, list) else [])]
            )
            captured = ", ".join(jsc.js_string(t) for t in vague_unresolved)
            unresolved_set = {_nf_norm_raw(t) for t in unresolved}
            resolved_ents = [
                e
                for e in all_ents
                if jsc.truthy(e)
                and jsc.truthy(jsc.get(e, "raw"))
                and _nf_norm_raw(jsc.get(e, "raw")) not in unresolved_set
            ]
            resolved_summary = ", ".join(
                x
                for x in (
                    f"{_prettify_type(jsc.get(e, 'hint') or 'item')} {jsc.js_string(jsc.get(e, 'raw'))}".strip()
                    for e in resolved_ents
                )
                if jsc.truthy(x)
            )
            if resolved_summary:
                escalate_message = (
                    f'I understood {resolved_summary}, but couldn\'t make out "{captured}" - '
                    f"is that a {labels}? Please label it, e.g. customer <name>, product <code>."
                )
            else:
                escalate_message = (
                    f'I captured "{captured}" but couldn\'t tell which part is which. '
                    f"For a {_plain_words(domain_hint)} enquiry, please give me a labeled "
                    f"specific - e.g. {labels}."
                )
        else:
            require_specific = jsc.get(g, "require_specific")
            predicate = jsc.get(g, "predicate")
            # AC-1703: the four described-set answers below all state something about the
            # SET the question described, and none of them is true when the SUBJECT is a
            # code the catalogue does not carry. A mistyped token with real neighbours is
            # production's did-you-mean, and it has to be reachable: `build_suggest_offer`
            # skips its own D1 candidates entirely whenever a clarify fired here, so the
            # set answer does not merely come FIRST, it silences the other one.
            described_set_answers = isinstance(predicate, dict) and not _unplaced_token_has_neighbours(r, g)
            if jsc.truthy(require_specific):
                escalate_message = jsc.get(g, "gate_clarification")
            elif described_set_answers and "schemes_on_file" in predicate:
                # F3 (AC-1321): a certificate SCHEME word the register cannot read -
                # names what IS on file instead of the generic "no {attach_noun}
                # matched these" below, which would say nothing about schemes at all.
                require_echo = jsc.get(predicate, "require") or {}
                scheme_word = jsc.js_string(jsc.get(require_echo, "certificate")).strip()
                schemes = jsc.array(jsc.get(predicate, "schemes_on_file"))
                schemes_text = (
                    ", ".join(jsc.js_string(s) for s in schemes) if schemes else "none on file yet"
                )
                esc = _esc_offer()
                escalate_message = (
                    f"The register has no {scheme_word or 'that'} certificates. "
                    f"Schemes on file: {schemes_text}."
                ) + (f" {esc}" if esc else "")
            elif described_set_answers and "attachment_types_on_file" in predicate:
                # R6/AC-1329 (console fix round 2): the unrecognised word is an
                # ATTACHMENT LABEL ("photo"), not a class/product_type word - a
                # document-type miss answers the wrong question with the
                # product-type sentence below. `_leg_attachment_type`'s own
                # `_UnrecognizedLabel` is the ONLY leg that carries this key, so
                # it takes priority over the generic F2 branch beneath it.
                term = jsc.js_string(
                    jsc.get(jsc.get(predicate, "require") or {}, "attachment_type")
                ).strip()
                types_on_file = jsc.array(jsc.get(predicate, "attachment_types_on_file"))
                types_text = (
                    ", ".join(jsc.js_string(t) for t in types_on_file)
                    if types_on_file
                    else "none on file yet"
                )
                escalate_message = (
                    f"I don't know '{term}' as a document type. Types I know: {types_text}."
                )
                is_clarification = True
            elif (
                described_set_answers
                and jsc.get(predicate, "qualifying_total") == 0
                and jsc.array(jsc.get(predicate, "unrecognized_terms"))
            ):
                # AC-1320 (work item F2): the described set named NOTHING this
                # catalogue can read. Fix round 9 on PR #833 (owner, 28 Sep 2026: "no
                # explanations", no typo mention): the word is the "did not match" line
                # of the one reply structure, never a "did you mean". Fix round 10 (owner,
                # 28 Sep 2026: "for #833 yeah exact only"): no product types are offered
                # as lines either; a word said as the product type is named with its kind
                # ("Couldn't find: water tap basin (product type)").
                terms = [jsc.js_string(t) for t in jsc.array(jsc.get(predicate, "unrecognized_terms")) if jsc.truthy(t)]
                type_raws = [
                    jsc.js_string(jsc.get(e, "raw")).strip()
                    for e in entities_list
                    if jsc.get(e, "hint") in ("category", "product_type") and jsc.truthy(jsc.get(e, "raw"))
                ]
                asked = " ".join(
                    jsc.js_string(jsc.get(e, "raw"))
                    for e in entities_list
                    if jsc.get(e, "hint") in ("category", "product_type", "specification") and jsc.truthy(jsc.get(e, "raw"))
                ) or terms[0]
                is_type = {raw.lower() for raw in type_raws}
                escalate_message = what_you_want_reply(
                    asked,
                    [],
                    missing=_and_list([f"{t} (product type)" if t.strip().lower() in is_type else f'"{t}"' for t in terms]),
                    team=offer_team,
                )

            elif (
                described_set_answers
                and jsc.get(predicate, "qualifying_total") == 0
                and isinstance(jsc.get(predicate, "near_miss"), dict)
            ):
                # Fix round 9 on PR #833 (owner, 28 Sep 2026: "you do like here is what
                # you want, but no incoming, do you want me to escalate..., I want that
                # structure to stay"): round 4's near-miss sentence ("I looked for ...
                # in another finish or colour") is retired for the product-code miss's
                # own structure, the described products line by line.
                escalate_message = described_members_reply(predicate, offer_team) or near_miss_reply(predicate, offer_team)
            elif (
                described_set_answers
                and jsc.get(predicate, "qualifying_total") == 0
                and not jsc.array(jsc.get(predicate, "unrecognized_terms"))
            ):
                # AC-1319: a HAS turn's described set is honest (every content word
                # bound to something, C4's gate bypass let the qualifying matches
                # through), and NONE of them satisfy the predicate. Name the set and
                # the predicate, and the codes actually checked - never the generic
                # "no {attach_noun} matched these" below, which names only the
                # predicate word and drops the set the customer actually asked about.
                # R16/AC-1340 (third console pass): a category / product_type raw
                # names the subject too, not only brand + product - "which
                # bathroom accessory has stock" carries no brand and no `hint:
                # "product"` entity at all, so the old subject_words stayed
                # empty and the literal fallback "a match" glued onto the
                # sentence's own leading "a" read "Couldn't find a a match with
                # stock.". When NO raw of any kind names it either, the
                # predicate's OWN `class_labels` (real evidence: the described
                # set's class) is the next fallback; only when THAT is also
                # empty does the sentence drop the article entirely ("any
                # product"), never "a" + a placeholder noun.
                brand_raw = jsc.get(
                    jsc.find(entities_list, lambda e: jsc.get(e, "hint") == "brand"), "raw"
                )
                product_raw_words = [
                    jsc.js_string(jsc.get(e, "raw"))
                    for e in entities_list
                    if jsc.get(e, "hint") == "product" and jsc.truthy(jsc.get(e, "raw"))
                ]
                category_or_type_raw_words = [
                    jsc.js_string(jsc.get(e, "raw"))
                    for e in entities_list
                    if jsc.get(e, "hint") in ("category", "product_type") and jsc.truthy(jsc.get(e, "raw"))
                ]
                subject_words = (
                    ([jsc.js_string(brand_raw)] if jsc.truthy(brand_raw) else [])
                    + product_raw_words
                    + category_or_type_raw_words
                )
                if not subject_words:
                    predicate_class_labels = [
                        jsc.js_string(c).strip()
                        for c in jsc.array(jsc.get(predicate, "class_labels"))
                        if jsc.truthy(c)
                    ]
                    if predicate_class_labels:
                        subject_words = [predicate_class_labels[0].lower()]
                subject_phrase = f"a {' '.join(subject_words)}" if subject_words else "any product"
                # Read straight off the RESOLVER's own resolutions, never off this
                # gate's `compatible_entities` - the zero-qualifying carve-out
                # (`gate.py`'s `_is_a_described_word` branch) deliberately keeps a
                # described-set word's matches OUT of `compatible_entities` (so the
                # miss gate still fires), which would otherwise empty this list too.
                checked_codes: list[str] = []
                for res in jsc.array(jsc.get(r, "resolutions")):
                    for m in jsc.array(jsc.get(res, "matches")):
                        if not jsc.truthy(m) or jsc.get(m, "entity_type") != "product":
                            continue
                        code = jsc.get(m, "canonical_code")
                        if jsc.truthy(code) and code not in checked_codes:
                            checked_codes.append(jsc.js_string(code))
                checked_codes = checked_codes[:5]
                checked = (
                    f" (checked {', '.join(jsc.js_string(c) for c in checked_codes)})"
                    if checked_codes
                    else ""
                )
                described = jsc.array(jsc.get(predicate, "description"))
                if described:
                    # Fix round 8 on PR #833: the miss names the described set the way
                    # its answer would have ("Sorento kitchen sinks with thickness 1.2 mm
                    # with incoming stock"), every grounded property included.
                    labels = [jsc.js_string(c) for c in jsc.array(jsc.get(predicate, "class_labels")) if jsc.truthy(c)]
                    phrase = described_set_phrase(
                        described, set_noun_for(labels), jsc.get(predicate, "require") or {}
                    )
                    escalate_message = described_members_reply(predicate, offer_team) or what_you_want_reply(
                        phrase, [], team=offer_team
                    )
                else:
                    esc = _esc_offer()
                    escalate_message = (
                        f"Couldn't find {subject_phrase} with "
                        f"{_predicate_phrase(jsc.get(predicate, 'require') or {})}{checked}."
                    ) + (f" {esc}" if esc else "")
            elif domain_hint == "product_attachment":
                # FIX B: natural, parser-driven phrasing - never leak the internal literal.
                product_raws = [
                    jsc.get(e, "raw")
                    for e in entities_list
                    if jsc.get(e, "hint") == "product" and jsc.truthy(jsc.get(e, "raw"))
                ]
                attach_raws = [
                    jsc.get(e, "raw")
                    for e in entities_list
                    if jsc.get(e, "hint") == "attachment_type" and jsc.truthy(jsc.get(e, "raw"))
                ]
                attach_ent = jsc.find(entities_list, lambda e: jsc.get(e, "hint") == "attachment_type")
                # ATTACHMENT-MULTI (owner ruling 2 Oct 2026): a several-type miss names the
                # OFFICIAL types the gate kept, `attachment_types.type_name` in the order the
                # customer named them - "Product Photos or Technical Specifications", never
                # "photo or technical specifications". A single-type miss is unchanged.
                attach_names: list[str] = []
                if len(attach_raws) > 1:
                    kept_types = {
                        jsc.get(c, "uuid")
                        for c in jsc.array(jsc.get(g, "compatible_entities"))
                        if jsc.get(c, "entity_type") == "attachment_type"
                    }
                    for res in jsc.array(jsc.get(r, "resolutions")):
                        for m in jsc.array(jsc.get(res, "matches")):
                            name = jsc.nullish_str(jsc.get(jsc.get(m, "display"), "type_name")).strip()
                            if (
                                jsc.get(m, "entity_type") == "attachment_type"
                                and jsc.get(m, "uuid") in kept_types
                                and name
                                and name not in attach_names
                            ):
                                attach_names.append(name)
                attach_words = attach_names if attach_names else attach_raws
                # Owner ruling 2 Oct 2026 (hand test "photo and cert for strwc286"): when the
                # PRODUCT itself was not found and no did-you-mean candidate exists (a
                # candidate's offer replaces this text anyway), the reply names only the
                # missing product - no "Here's what you want" types and no "no <types>
                # matched these", which read as though the product had been searched - and
                # keeps the escalate offer.
                product_keys = {_nf_norm_raw(x) for x in product_raws}
                unfound_products = [t for t in not_found_raw if _nf_norm_raw(t) in product_keys]
                # A product row with no uuid is the unplaced token the engine carries on
                # the gate (cloud browser pass 3 Oct), never a resolved product.
                product_resolved = any(
                    jsc.get(c, "entity_type") == "product" and jsc.truthy(jsc.get(c, "uuid"))
                    for c in jsc.array(jsc.get(g, "compatible_entities"))
                )
                if unfound_products and not product_resolved:
                    esc = _esc_offer()
                    escalate_message = (
                        f"Couldn't find {', '.join(label_token(t) for t in unfound_products)}."
                    ) + (f" {esc}" if esc else "")
                elif use_breakdown:
                    # combine the attachment-type qualifiers into ONE searched noun and fold
                    # them OUT of the "couldn't find" list, so they are not double-named
                    # ATTACHMENT-MULTI R4: several asked types are alternatives the miss
                    # sentence lists - "photo or technical specifications" - never one run-on.
                    attach_noun = (
                        " or ".join(jsc.js_string(x) for x in attach_words)
                        if attach_raws
                        else (
                            jsc.get(attach_ent, "raw")
                            if jsc.truthy(jsc.get(attach_ent, "raw"))
                            else "attachment"
                        )
                    )
                    attach_set = {_nf_norm_raw(x) for x in attach_raws}
                    escalate_message = build_breakdown_msg(
                        jsc.js_string(attach_noun),
                        [t for t in not_found_raw if _nf_norm_raw(t) not in attach_set],
                    )
                else:
                    prod_text = (
                        f"product {' and '.join(jsc.js_string(x) for x in product_raws)}"
                        if product_raws
                        else ""
                    )
                    attach_raw = jsc.get(attach_ent, "raw") if jsc.truthy(attach_ent) else None
                    article = "a "
                    if len(attach_raws) > 1:
                        attach_raw = " or ".join(jsc.js_string(x) for x in attach_words)
                        article = ""  # "photo or technical specifications", never "a ... specifications"
                    if jsc.truthy(attach_raw) and prod_text:
                        subject = f"{article}{jsc.js_string(attach_raw)} for {prod_text}"
                    elif jsc.truthy(attach_raw):
                        subject = f"{article}{jsc.js_string(attach_raw)}"
                    elif prod_text:
                        subject = f"attachments for {prod_text}"
                    else:
                        subject = requested if jsc.truthy(requested) else "the requested item"
                    esc = _esc_offer()
                    escalate_message = (
                        f"Could not find{active_inactive} {subject}{date_range}{access}."
                    ) + (f" {esc}" if esc else "")
            elif _outstanding_report_text(item):
                # AC-1107 / S4 point 6, and the owner's approved miss mock: an outstanding
                # report that came back empty ALREADY says what was searched and what was
                # not there, in the words the owner signed off on - its own header plus
                # `*Sales order outstanding*` / `No open sales order.` and the DO pair.
                # The generic breakdown above would print `Customer / Product / Dates` and
                # "Here's what you want" over the top of that and drop both block lines,
                # which is a different answer to the question that was asked. Only the
                # frozen escalate question is appended, unchanged, so the offer and the
                # team picker behind it work exactly as they do on every other miss.
                # THIS TOOL ONLY: no other miss reaches here, because no other answer
                # carries `outstanding_report`.
                esc = _esc_offer()
                escalate_message = f"{_outstanding_report_text(item)}" + (f"\n\n{esc}" if esc else "")
                found_summary = _outstanding_report_text(item)
            else:
                # status-filter-aware: one or more SPECIFIC orders resolved (the DO exists)
                # but the delivered / outstanding filter returned nothing, so the order is
                # not a miss, it is just not in that status. AC-1860/AC-1861/AC-1862: a
                # status-filtered ask can name several orders (`STATUS DELIVERY / A / B / C`)
                # and every resolved one must be named, not just the first - in
                # `gate.compatible_entities` order (the order the customer typed), deduped
                # by uuid since `all_matches` carries `intersection` + `by_entity_type` +
                # `resolutions`, so the same uuid can appear up to three times.
                order_uuids_in_order: list[Any] = []
                seen_order_uuids: set[Any] = set()
                for c in compat:
                    if jsc.get(c, "entity_type") not in _ORDER_TYPES:
                        continue
                    c_uuid = jsc.get(c, "uuid")
                    if c_uuid not in seen_order_uuids:
                        seen_order_uuids.add(c_uuid)
                        order_uuids_in_order.append(c_uuid)
                order_matches: list[Any] = []
                for order_uuid in order_uuids_in_order:
                    match = jsc.find(
                        all_matches,
                        lambda m, order_uuid=order_uuid: jsc.truthy(m)
                        and jsc.get(m, "uuid") == order_uuid
                        and jsc.get(m, "entity_type") in _ORDER_TYPES,
                    )
                    if jsc.truthy(match):
                        order_matches.append(match)
                if order_matches and order_status in ("delivered", "outstanding"):
                    order_lines: list[Any] = []
                    for order_match in order_matches:
                        display = jsc.get(order_match, "display") or {}
                        customer = jsc.get(display, "customer_name")
                        label = jsc.js_string(jsc.get(order_match, "canonical_code")) + (
                            f" ({jsc.js_string(customer)})" if jsc.truthy(customer) else ""
                        )
                        if order_status == "delivered":
                            status = jsc.get(display, "status")
                            status_shown = jsc.truthy(status) and "delivery_orders.status" in (
                                granted_keys if isinstance(granted_keys, (list, tuple, set, frozenset)) else ()
                            )
                            status_text = (
                                f" - current status: {jsc.js_string(status)}" if status_shown else ""
                            )
                            # Owner ruling (10 Sep 2026, reverses the 6 Sep 2026 ruling):
                            # `orders.estimated_delivery_date` is not a real promise - the
                            # import stamps it as order_date + 2 business days
                            # (`order_service.py` ~2824). The CRM UI may keep showing it, but
                            # the chatbot / turn output must not state it.
                            order_lines.append(f"Order {label} hasn't been delivered yet{status_text}.")
                        else:
                            order_lines.append(
                                f"Order {label} has no outstanding items - it looks already "
                                f"delivered or closed."
                            )
                    # AC-1863: one order must stay byte-identical to before this fix, so the
                    # escalate question is appended to the LAST line (a space, not a newline)
                    # and only the order lines themselves are newline-joined.
                    esc = _esc_offer()
                    if esc:
                        order_lines[-1] = f"{order_lines[-1]} {esc}"
                    escalate_message = "\n".join(order_lines)
                elif use_breakdown:
                    escalate_message = build_breakdown_msg(
                        f"{status_label}{jsc.js_string(domain_hint)}"
                    )
                elif not_found_raw and not found_lines:
                    # NOTHING resolved. "Could not find promotion for stwc26" states that
                    # promotions were searched and none matched - they were not: the gate
                    # dead-ends on the no-compatible-entity branch and the fetch never runs.
                    # Saying we searched sends the customer off correcting the wrong thing.
                    esc = _esc_offer()
                    escalate_message = (
                        f"Couldn't find: {', '.join(label_token(t) for t in not_found_raw)}."
                    ) + (f" {esc}" if esc else "")
                else:
                    for_requested = f" for {requested}" if jsc.truthy(requested) else ""
                    esc = _esc_offer()
                    escalate_message = (
                        f"Could not find{active_inactive} {status_label}"
                        f"{_plain_words(domain_hint)}{for_requested}{date_range}{access}."
                    ) + (f" {esc}" if esc else "")

    # R6 (round 4 on PR #833): an attribute value the registry does not know is said back
    # with the values it does, on a set ask (`predicate.unknown_values`) and a product ask
    # (`unknown_spec_values`) alike. It outranks every miss sentence above: nothing was
    # searched for it, so no "couldn't find" is true.
    unknown_values = jsc.array(jsc.get(predicate, "unknown_values")) if isinstance(predicate, dict) else []
    unknown_values = unknown_values or jsc.array(jsc.get(r, "unknown_spec_values"))
    if unknown_values:
        # Fix round 9 on PR #833: the one reply structure, the subject broken down by the
        # unknown value's key; the escalate offer stands, so it is not a clarify.
        subject = next(
            (
                words
                for hints in (("category", "product_type"), ("product",))
                for words in [
                    " ".join(
                        jsc.js_string(jsc.get(e, "raw"))
                        for e in entities_list
                        if jsc.get(e, "hint") in hints and jsc.truthy(jsc.get(e, "raw"))
                    )
                ]
                if words
            ),
            "",
        )
        escalate_message = unknown_values_reply(unknown_values, predicate, offer_team, subject=subject)
        is_clarification = False
        found_summary = ""

    # Q23: the customer named an access level they do not hold. The gate detects it; say so
    # here too, or an entitlement problem reads as an ordinary "couldn't find it".
    access_notice = jsc.get(g, "access_notice")
    if jsc.truthy(g) and jsc.truthy(access_notice) and jsc.truthy(escalate_message):
        escalate_message = f"{jsc.js_string(access_notice)}\n\n{escalate_message}"

    out = dict(item) if isinstance(item, dict) else {}
    out["escalate_message"] = escalate_message
    out["is_clarification"] = is_clarification
    out["found_summary"] = found_summary
    return out


# --------------------------------------------------------------------------- #
# build-suggest-offer (710 lines, the LIVE SPINE's copy)
# --------------------------------------------------------------------------- #

# `dym-transform` / `dym-annotate` both PASS THE NOT-FOUND PAYLOAD THROUGH and append their
# own control keys, so the composer strips them again and the object it emits is
# byte-identical to pre-change on every un-annotated path. ANY new planner output key must be
# added here in the same change that introduces it, or it leaks into this node's output.
_DYM_CTRL_KEYS = (
    "dym_probe_entities",
    "dym_candidate_codes",
    "dym_excluded_codes",
    "probe_tool",
    "probe_noun",
    "probe_predicate",
    "probe_needed",
    "probe_skip_reason",
    "probe_lane",
    "_dym_probe_input",
    "dym_available_codes",
    "dym_probe_meta",
    "dym_capped_codes",
    "probe_cap_applied",
    # #750: the two keys `dym-annotate` carries for THIS node's own uuid-to-code projection
    # and its noun. Stripped here with the rest, so the object this node emits is unchanged.
    "dym_probe_row_keys",
    "dym_probe_type_name",
    # ATTACHMENT-MULTI R2: the per-type has-sets of a several-type ask, likewise.
    "dym_has_by_type",
)

_YES = "Yes, escalate"
_NO = "No, it's okay"

_DATE_LIKE_ISO_RE = re.compile(r"^[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}\Z")
_DATE_LIKE_DMY_RE = re.compile(r"^[0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4}\Z")
_ALNUM_ANY_RE = re.compile(r"[a-z0-9]", re.IGNORECASE)
_PICKER_LINE_RE = re.compile(r"^\s*[0-9]+\.\s+(.+?)\s*\Z")
_CERT_PREFIX_RE = re.compile(r"^cert", re.IGNORECASE)
# `gate.py`'s own "CODE (Company)" suffix, appended to a picker line whose code is duplicated
# across companies (`product_attachment` only). The label is what the customer picks on, so it
# is never rewritten here - it is only read back apart, to find which twin the line names.
_COMPANY_SUFFIX_RE = re.compile(r"^\s*(.+?)\s*\(([^()]+)\)\s*\Z")


def _dym_code_space(dym_ann: Any, dym_meta: Any) -> tuple[set[str], set[str]]:
    """`(probed, has)` for a UUID-keyed probe, keyed the way the renders key: by CODE.

    `product_attachment` is stamped per uuid (Fix 4 / Fix 5) because one product code can
    belong to two companies, and the answer rows carry no product id. The renders print codes,
    so the two uuid sets are projected back through the planner's own `(uuid, code, company)`
    rows, which `dym-annotate` carries for exactly this.

    Two keys per row, both only when they name ONE uuid: the bare `code` (what D1 prints and
    what an unsuffixed picker line reads), and the `code|company` composite (what a
    company-suffixed picker line reads). A code owned by two uuids therefore has no bare key
    and renders BARE unless the line itself says which company, and an owner the annotator
    could not attribute (`dym_ambiguous_uuids` / `dym_ambiguous_codes`, F1) is dropped from
    both. Never guess: a false "has" costs the customer a dead-end pick.
    """
    probed_uuids = {_ms_norm(u) for u in jsc.array(jsc.get(dym_meta, "probed"))}
    has_uuids = {_ms_norm(u) for u in jsc.array(jsc.get(dym_ann, "dym_available_codes"))}
    ambiguous_uuids = {_ms_norm(u) for u in jsc.array(jsc.get(dym_ann, "dym_ambiguous_uuids"))}
    ambiguous_codes = {_ms_norm(c) for c in jsc.array(jsc.get(dym_ann, "dym_ambiguous_codes"))}

    owners: dict[str, set[str]] = {}
    for row in jsc.array(jsc.get(dym_ann, "dym_probe_row_keys")):
        code = _ms_norm(jsc.get(row, "code"))
        uuid = _ms_norm(jsc.get(row, "uuid"))
        if not code or not uuid or code in ambiguous_codes or uuid in ambiguous_uuids:
            continue
        owners.setdefault(code, set()).add(uuid)
        owners.setdefault(f"{code}|{_ms_norm(jsc.get(row, 'company'))}", set()).add(uuid)

    probed: set[str] = set()
    has: set[str] = set()
    for key, uuids in owners.items():
        if len(uuids) != 1:
            continue  # two owners behind one key: nothing to attribute the answer row to
        uuid = next(iter(uuids))
        if uuid not in probed_uuids:
            continue
        probed.add(key)
        if uuid in has_uuids:
            has.add(key)
    return probed, has


def _dym_lookup(label: Any, keys: set[str]) -> str | None:
    """The key `label` was probed / found under, or None.

    The bare label first, so a code-keyed turn resolves exactly as it did before this existed;
    then the `code|company` composite a "CODE (Company)" picker line implies.
    """
    text = jsc.js_string(label)
    key = _ms_norm(text)
    if key in keys:
        return key
    match = _COMPANY_SUFFIX_RE.match(text)
    if match:
        composite = f"{_ms_norm(match.group(1))}|{_ms_norm(match.group(2))}"
        if composite in keys:
            return composite
    return None

# `entity-ids-transformer`'s own TYPE_TO_PARAM keys. A type added there and not here merely
# fails OPEN (no silence), which is the safe direction.
_QUERIED_TYPES = frozenset(
    {
        "product",
        "promotion",
        "order",
        "customer_order",
        "order_number",
        "customer",
        "transporter",
        "form",
        "shipment",
        "inbound_shipment",
        "attachment_type",
        "attachment",
        "certificate",
    }
)

_D2_NOUN = {
    "inventory": "stock",
    "incoming": "incoming stock (ETA)",
    "master_products": "product info",
    "promotion": "promotion",
}


def _bso_human_list(codes: list) -> str:
    """`build-suggest-offer`'s own `humanList` - no filter and no empty case, unlike the
    miss renderer's copy. Kept separate because the two really do differ."""
    if len(codes) == 1:
        return jsc.js_string(codes[0])
    head = ", ".join(jsc.js_string(c) for c in codes[:-1])
    return f"{head}, or {jsc.js_string(codes[-1])}"


def _is_date_like(value: Any) -> bool:
    """A did-you-mean candidate must be an ENTITY-CODE correction, never a date-relaxation
    alternative - or a later date reply hijacks the pick and drops the customer."""
    text = jsc.nullish_str(value).strip()
    return bool(_DATE_LIKE_ISO_RE.match(text) or _DATE_LIKE_DMY_RE.match(text))


def _is_code_shaped(value: Any) -> bool:
    text = jsc.nullish_str(value).strip()
    return len(text) > 0 and bool(_ALNUM_ANY_RE.search(text)) and not _is_date_like(text)


def _quick_reply(values: list) -> str:
    """Comma-stripped so a label cannot split into extra respond.io buttons."""
    return ",".join(jsc.js_string(v).replace(",", "") for v in values)


def build_suggest_offer(
    item: dict[str, Any] | None,
    *,
    parser: dict[str, Any] | None,
    resolved: dict[str, Any] | None,
    gate: dict[str, Any] | None,
    dym_annotate: Any = None,
    sibling_probe: Any = None,
    sibling_transform: Any = None,
    get_results: Any = None,
    execution_id: Any = None,
    profile: Any = None,
) -> dict[str, Any]:
    """`build-suggest-offer` (D1 / D2 / D3): the miss lane's offer composer.

    ADDITIVE. It passes the not-found payload through and, when the miss carries CONCRETE
    candidates, attaches the suggestion offer the tail renders. No candidates means
    `suggest_offer` stays false and downstream is byte-identical to before this node existed.

    Three optional upstreams are the `isExecuted` three-state reads: `sibling_transform` /
    `sibling_probe` (D3's family picker), `dym_annotate` (the has-it annotation) and
    `get_results` (D2's alternatives scan). `execution_id` is `$execution.id`, the offer's
    identity - in the CRM that identity is the TURN id, which is the one permanent, already
    registered difference between this port and a captured n8n run
    (`tests/chatbot/worlds.py::WORLD_DROP_PATHS`).

    `profile` (#1262 slice 11 review round, 26 Sep 2026): a did-you-mean/sibling roster is
    a clarifying QUESTION, not an escalation offer (`pending.is_roster`, `answer_bridge.
    answer_for`'s own audience gate keeps the roster itself for staff) - but every
    `suggest_response` this function builds bakes its own "or would you like me to escalate
    to X team?" tail straight into the SENTENCE, before that gate ever runs. `_esc_clause`
    is the SAME closure pattern `not_found_error_message::_esc_offer` already uses (one
    place every such clause in a function is built, so a later branch inherits the gate for
    free) - `""` for staff, the full clause otherwise.
    """
    out = dict(item) if isinstance(item, dict) else {}
    for key in _DYM_CTRL_KEYS:
        out.pop(key, None)
    q = parser if isinstance(parser, dict) else {}
    r = resolved if isinstance(resolved, dict) else {}
    g = gate if isinstance(gate, dict) else {}

    out["suggest_offer"] = False

    # #9: prefer the resolved entity's company team, so the offer text, the not-found text
    # and the actual escalation cannot name three different teams in one turn.
    company_team = jsc.get(g, "company_team")
    if not jsc.truthy(company_team):
        routing = jsc.get(q, "routing")
        company_team = jsc.get(routing, "suggested_team") if jsc.truthy(routing) else None
    team = _pretty_team(company_team if jsc.truthy(company_team) else "customer_service")
    from app.services.chatbot.turn.state import escalation_barred, offers_escalation

    # ESCALATION-CONTROL: staff and a barred contact alike get no offer; a barred contact's
    # miss says what was not found, then "Please refer to your salesman." (owner ruling Q2).
    is_staff = not offers_escalation(profile)
    barred = escalation_barred(profile)

    def _cont(lead_in: str, escalate_suffix: str) -> str:
        """`lead_in + escalate_suffix` (the ", or ...escalate..." tail, several wordings
        across this function's own branches) - staff get `f"{lead_in}."` alone, no
        escalate offer (#1262 slice 11 review round, 26 Sep 2026)."""
        if barred:
            return refer.after(f"{lead_in}.", sep=" ")
        return f"{lead_in}." if is_staff else f"{lead_in}{escalate_suffix}"

    def mk_offer(cands: Any) -> Any:
        """id = this turn's identity (stamped onto the picked entity as its dym slot, giving
        the parser a STABLE handle across repeated picks); domain drives the domain-switch
        supersede rule; ttl / picked seed the lifecycle the tail advances each turn."""
        if not (isinstance(cands, list) and cands):
            return None
        return {
            "id": jsc.js_string(execution_id),
            "domain": jsc.get(q, "domain_hint") if jsc.truthy(jsc.get(q, "domain_hint")) else None,
            "ttl": 3,
            "candidates": cands,
            "picked": [],
        }

    # D3: the incoming sibling-family picker. Fires ONLY when `sibling-gate` routed the
    # not-found through family-fetch -> sibling-transform -> sibling-probe. Reuses the shipped
    # suggest_offer envelope so the pick / escalate round trip is the already-proven path.
    gate_domain = jsc.get(jsc.get(g, "gate_debug"), "domain")
    dom_incoming = jsc.get(q, "domain_hint") == "incoming" or gate_domain == "incoming"
    if sibling_transform is not None and sibling_probe is not None and dom_incoming:
        fam = jsc.get(sibling_transform, "siblings")
        probe = sibling_probe if isinstance(sibling_probe, dict) else {}
        if isinstance(fam, list) and fam:
            # codes WITH incoming from the probe answers - same machinery as
            # `annotate-incoming-picker`.
            answers = jsc.get(probe, "answers")
            if not isinstance(answers, list):
                items = jsc.get(probe, "items")
                answers = items if isinstance(items, list) else []
            has_inc: set[str] = set()
            for answer in answers:
                code = jsc.get(answer, "title") if jsc.truthy(answer) else None
                if not jsc.truthy(code) and jsc.truthy(answer) and isinstance(jsc.get(answer, "fields"), list):
                    field = jsc.find(
                        jsc.get(answer, "fields"),
                        # D11-reproduced: `build-suggest-offer` D3's own PRESENTER label match.
                        lambda x: bool(_PRODUCT_CODE_LABEL_RE.search(jsc.js_string(jsc.get(x, "label")))),
                    )
                    code = jsc.get(field, "value") if jsc.truthy(field) else None
                if jsc.truthy(code):
                    has_inc.add(_ms_norm(code))
            seen_codes: set[str] = set()
            sibs: list[dict[str, Any]] = []
            for entity in fam:
                code = jsc.get(entity, "code") if jsc.truthy(entity) else None
                if not jsc.truthy(code) or _ms_norm(code) in seen_codes:
                    continue
                seen_codes.add(_ms_norm(code))
                sibs.append(
                    {
                        "code": jsc.js_string(code),
                        "uuid": jsc.get(entity, "uuid") if jsc.truthy(jsc.get(entity, "uuid")) else None,
                        "has": _ms_norm(code) in has_inc,
                    }
                )
            exact_codes = {
                _ms_norm(jsc.get(e, "code"))
                for e in (g["compatible_entities"] if isinstance(jsc.get(g, "compatible_entities"), list) else [])
                if jsc.truthy(e)
                and jsc.js_string(jsc.get(e, "entity_type")).lower() == "product"
                and jsc.truthy(jsc.get(e, "code"))
            }
            extras = [s for s in sibs if _ms_norm(s["code"]) not in exact_codes]
            if len(extras) > 0:
                # ALWAYS a numbered picker whenever at least one sibling exists, regardless of
                # has-incoming (the owner reversed the earlier split). Has-incoming first, then
                # code order, NO cap.
                # `(Number(b.has) - Number(a.has)) || String(a.code).localeCompare(...)`.
                # The tiebreak is CODE-POINT order here and ICU collation there, and the
                # two disagree on punctuation and case: node orders `SRT_100, SRT-100,
                # SRT1, srt100, SRT100, SRTA` where Python orders `SRT-100, SRT1, SRT100,
                # SRTA, SRT_100, srt100`. Reproducing ICU needs PyICU, which is a new
                # dependency for a tiebreak between two codes of the SAME product family;
                # no captured turn disagrees (every graded `build-suggest-offer` replay is
                # byte-equal). Registered as `divergences.SIBLING_TIEBREAK_IS_CODE_POINT`;
                # the trigger for paying for ICU is a real family whose codes differ only
                # in punctuation or case.
                sibs.sort(key=lambda s: ((0 if s["has"] else 1), jsc.js_string(s["code"])))
                exact_list = ", ".join(c.upper() for c in exact_codes)
                numbered = "\n".join(
                    f"{i + 1}. {s['code']} - {'has incoming' if s['has'] else 'no incoming'}"
                    for i, s in enumerate(sibs)
                )
                out["suggest_offer"] = True
                out["suggest_selection_context"] = "suggest_offer"
                out["suggest_response"] = (
                    f"No incoming stock (ETA) found for {exact_list}. Related products:\n"
                    f"{numbered}\n"
                    + _cont(
                        "Reply with a number to check its incoming",
                        f", or reply 'yes' to escalate to {team} team.",
                    )
                )
                # Uncapped list means NO per-sibling buttons (respond.io's button cap);
                # numbers are typed, so Yes / No are the only buttons.
                out["suggest_quick_reply"] = _quick_reply([_YES, _NO])
                out["suggest_last_result_set"] = [
                    {
                        "idx": i + 1,
                        "label": s["code"],
                        "value": s["code"],
                        "product": s["code"],
                        "uuid": s["uuid"],
                        "entity_type": "product",
                    }
                    for i, s in enumerate(sibs)
                ]
                return out
            # extras empty means only the exact code itself: fall through to plain escalate.

    unresolved = jsc.array(jsc.get(r, "unresolved_tokens"))
    is_clar = out.get("is_clarification") is True  # preserve vague / scope clarify prompts
    require_spec = jsc.get(g, "require_specific") is True  # preserve require-specific prompts
    allowed_lookup = jsc.get(jsc.get(g, "gate_debug"), "allowed_lookup")
    allowed_types = allowed_lookup if isinstance(allowed_lookup, list) else None

    def attachment_noun() -> Any:
        """Name the doc type the user asked for (photo / certificate / drawing), not a
        generic word."""
        if jsc.get(q, "domain_hint") != "product_attachment":
            return None
        entities = jsc.get(q, "entities")
        at = (
            jsc.find(
                entities,
                lambda e: jsc.js_string(jsc.get(e, "hint") or "").lower() == "attachment_type",
            )
            if isinstance(entities, list)
            else None
        )
        raw = jsc.get(at, "raw") if jsc.truthy(at) else None
        return raw if jsc.truthy(raw) else "document"

    def token_candidates(res: Any) -> list:
        """D1's own candidate set: PER TOKEN, GENUINE-MISS ONLY.

        Never aggregate across tokens - a dead code once borrowed a sibling token's candidate
        in a multi-item order. Customer rows arrive multiply coded (the same account as debtor
        NAME, debtor CODE and a hash canonical code), so code-keyed dedup rendered one
        customer as three "codes"; they key on the display name instead, and the resolver's
        similarity order keeps the name-coded row first.
        """
        acc: list = []
        matches = jsc.get(res, "matches")
        if isinstance(matches, list):
            acc.extend(matches)
        alternatives = jsc.get(res, "alternatives")
        if isinstance(alternatives, list):
            acc.extend(alternatives)

        def cust_key(match: Any) -> str:
            display = jsc.get(match, "display")
            display = display if jsc.truthy(display) else {}
            name = (
                jsc.get(display, "debtor_name")
                or jsc.get(display, "customer_name")
                or jsc.get(match, "canonical_code")
                or ""
            )
            return "cust:" + jsc.js_string(name).strip().lower()

        seen: list = []
        keep: list = []
        for match in acc:
            code = jsc.get(match, "canonical_code")
            if not jsc.truthy(code):
                continue
            if _ms_is_exact(match):
                continue  # exact would have resolved
            entity_type = jsc.get(match, "entity_type")
            if allowed_types is not None and jsc.truthy(entity_type) and entity_type not in allowed_types:
                continue
            key = (
                cust_key(match)
                if jsc.js_string(entity_type or "").lower() == "customer"
                else code
            )
            if key in seen:
                continue
            seen.append(key)
            keep.append(match)
        return keep

    misses = _ms_miss_resolutions(r, gate=g)
    # Reviewer B2 on PR #833: a described set that qualifies nothing names its own miss
    # (`not_found_error_message`'s set branches: the honest zero of AC-1319 with the
    # codes it checked, the scheme or document type not on file of AC-1321 / R6). Its
    # class word ("tap") forward-matched a few product codes, which is the set's own
    # word and not a typo, so it offers no did-you-mean over the top of that sentence -
    # the same code-shape rule `_unplaced_token_has_neighbours` applies.
    from app.services.entity_resolver import _CODE_RE

    zero_set = jsc.get(jsc.get(g, "predicate"), "qualifying_total") == 0

    d1s: list[dict[str, Any]] = []
    if not is_clar and not require_spec:
        # dym-multitoken: accumulate EVERY genuine-miss token that carries candidates. Cap the
        # number of missed tokens shown at 5, which with cap3 per token keeps the numbered
        # list at or under 15.
        for res in misses:
            if zero_set and not _CODE_RE.fullmatch(jsc.nullish_str(jsc.get(res, "token")).strip()):
                continue
            cands = token_candidates(res)
            if cands:
                token = jsc.get(res, "token")
                if not jsc.truthy(token):
                    token = unresolved[0] if unresolved else None
                if not jsc.truthy(token):
                    entities = jsc.get(q, "entities")
                    first = entities[0] if isinstance(entities, list) and entities else None
                    token = jsc.get(first, "raw")
                if not jsc.truthy(token):
                    token = "that item"
                d1s.append({"token": token, "cands": cands})
        d1s = d1s[:5]

    # D2's alternatives scan, HOISTED above D1 so the window-exculpation block can read the
    # CRM's own `relaxed_axis` attribution. The fetch runs EXACTLY ONCE per turn; the run scan
    # is retained unchanged. Gate on `alternatives != null`, never invent.
    alts: Any = None
    axis: Any = "entity"
    if get_results is not None:
        runs = get_results if isinstance(get_results, list) else [get_results]
        for run in runs[:25]:
            items = run if isinstance(run, list) else [run]
            if not items:
                break
            hit = jsc.find(
                items,
                lambda it: jsc.truthy(it)
                and isinstance(jsc.get(it, "alternatives"), list)
                and len(jsc.get(it, "alternatives")) > 0,
            )
            if jsc.truthy(hit):
                alts = jsc.get(hit, "alternatives")
                relaxed = jsc.get(hit, "relaxed_axis")
                axis = relaxed if jsc.truthy(relaxed) else "entity"
                break

    # W1: THE WINDOW, NOT THE TOKENS, EMPTIED THIS TURN. When a date window was applied AND
    # relaxing the DATE axis is what produced rows, the entity filters as a whole matched
    # data - so a "missed" token whose candidates sit in the searched set had its filter
    # APPLIED and its did-you-mean must not fire. Per token on purpose: a token whose
    # candidates never entered the search keeps its D1 exactly as before.
    date_start = jsc.get(q, "date_filter_start")
    date_end = jsc.get(q, "date_filter_end")
    if (
        isinstance(alts, list)
        and alts
        and axis == "date"
        and (jsc.truthy(date_start) or jsc.truthy(date_end))
        and d1s
    ):
        w_compat = {
            jsc.js_string(jsc.get(c, "uuid"))
            for c in (g["compatible_entities"] if isinstance(jsc.get(g, "compatible_entities"), list) else [])
            if jsc.truthy(jsc.get(c, "uuid"))
        }
        w_kept: list = []
        w_excused: list = []
        for block in d1s:
            searched = any(
                jsc.truthy(m)
                and jsc.truthy(jsc.get(m, "uuid"))
                and jsc.js_string(jsc.get(m, "uuid")) in w_compat
                for m in block["cands"]
            )
            (w_excused if searched else w_kept).append(block)
        if w_excused:
            d1s = w_kept
            out["dym_window_excused"] = [jsc.js_string(b["token"]) for b in w_excused]
    # Every candidate-bearing token excused means the emptiness is the WINDOW's doing, and the
    # window-scoped breakdown the miss renderer already built is the honest reply. D2's date
    # override stands down WITH D1 on this shape.
    if not d1s and jsc.truthy(out.get("dym_window_excused")):
        return out

    # Answered-token OUTCOME rule. `resolved: false` is NOT "the customer got nothing for this
    # token": outside require-specific domains the gate lifts EVERY compatible match of an
    # ambiguous token into `compatible_entities` and the fetch queries them all, so the miss
    # that reaches this node is the CRM's ANSWER over those candidates, not a resolution gap.
    # Offering them back is a dead end by construction. Applied per CANDIDATE, not per token.
    queried = {
        jsc.js_string(jsc.get(c, "uuid")).lower()
        for c in (g["compatible_entities"] if isinstance(jsc.get(g, "compatible_entities"), list) else [])
        if jsc.truthy(c)
        and jsc.js_string(jsc.get(c, "entity_type") or "").lower() in _QUERIED_TYPES
        and _ms_is_uuid(jsc.get(c, "uuid"))
    }
    if queried and d1s:
        kept: list = []
        answered: list = []
        for block in d1s:
            cands = [
                m
                for m in block["cands"]
                if not (
                    jsc.truthy(m)
                    and jsc.truthy(jsc.get(m, "uuid"))
                    and jsc.js_string(jsc.get(m, "uuid")).lower() in queried
                )
            ]
            if cands:
                kept.append({**block, "cands": cands})
            else:
                answered.append(jsc.js_string(block["token"]))
        d1s = kept
        if answered:
            out["dym_answered_tokens"] = answered  # named, never a silent drop

    # dym-probe-before-offer: the has-it annotation inputs. If the annotator did not run,
    # failed, or detected an unscoped probe, `_dym_ok` is false and every render below is
    # byte-identical to pre-change. This can never dead-end a turn.
    dym_ann = dym_annotate if isinstance(dym_annotate, dict) else None
    dym_meta = jsc.get(dym_ann, "dym_probe_meta") if dym_ann is not None else None
    dym_ok = bool(jsc.truthy(dym_ann) and jsc.truthy(dym_meta) and jsc.get(dym_meta, "ok") is True)
    # #750: `product_attachment` is stamped PER UUID (`probe_uuid_keyed`, Fix 4 / Fix 5), and
    # every surface below keys by the CODE it printed, so the two sets are projected into code
    # space ONCE, here, and the three renders are untouched. A code that cannot be attributed
    # to exactly one uuid stays OUT of both sets and renders bare, which is the same promise
    # the annotator's own F1 amendment makes.
    if dym_ok and jsc.get(dym_meta, "key_mode") == "uuid":
        dym_probed, dym_has = _dym_code_space(dym_ann, dym_meta)
    elif dym_ok:
        dym_has = {_ms_norm(c) for c in jsc.array(jsc.get(dym_ann, "dym_available_codes"))}
        dym_probed = {_ms_norm(c) for c in jsc.array(jsc.get(dym_meta, "probed"))}
    else:
        dym_has = set()
        dym_probed = set()
    # Normalise the certificate family for the customer-facing suffix ONLY - `attachment_noun`
    # itself is left alone so D2's "No {noun} for {code}" text stays byte-identical.
    if dym_ok:
        noun_source = jsc.get(dym_meta, "noun")
        if not jsc.truthy(noun_source):
            # #750: the RESOLVED attachment type the probe was scoped to, before the
            # customer's own word for it. `attachment_noun()` stays the last resort, so a
            # turn whose probe carried no type entity reads exactly as it does today.
            noun_source = jsc.get(dym_ann, "dym_probe_type_name")
        # ATTACHMENT-MULTI R5 (tester re-run 2 Oct 2026, finding 3): a RESOLVED type is
        # stamped by its `type_name` ("Certification"), the word the follow-up "has no"
        # line and the found rows use too. Only the customer's own raw word ("cert") is
        # normalised to the family word; a certificate-NUMBER scope already arrives as
        # "certificate" (`miss_suggest._scoping_type_name`).
        resolved_noun = jsc.truthy(noun_source)
        noun_source = noun_source if resolved_noun else attachment_noun()
        text = jsc.nullish_str(noun_source).strip()
        dym_noun: Any = (
            "certificate" if (not resolved_noun and _CERT_PREFIX_RE.match(text)) else (text or "document")
        )
    else:
        dym_noun = None

    # ATTACHMENT-MULTI R2 (owner ruling 2 Oct 2026, Q1 (a)): a several-type ask stamps every
    # asked type on every line - "- has Product Photos, no Technical Specifications" - from
    # the annotator's per-type has-sets, projected into code space the same way as `dym_has`.
    # A line is stamped only when it was probed at all, exactly as before.
    dym_type_stamps: list[tuple[str, set[str]]] = []
    if dym_ok and jsc.get(dym_meta, "key_mode") == "uuid":
        for per_type in jsc.array(jsc.get(dym_ann, "dym_has_by_type")):
            name = jsc.nullish_str(jsc.get(per_type, "type")).strip()
            if not name:
                continue
            _probed, type_has = _dym_code_space(
                {**dym_ann, "dym_available_codes": jsc.array(jsc.get(per_type, "has"))}, dym_meta
            )
            dym_type_stamps.append((name, type_has))
        if len(dym_type_stamps) < 2:
            dym_type_stamps = []

    def _stamp(key: str) -> str:
        if dym_type_stamps:
            return " - " + ", ".join(
                f"has {noun}" if key in type_has else f"no {noun}" for noun, type_has in dym_type_stamps
            )
        return f" - has {dym_noun}" if key in dym_has else f" - no {dym_noun}"

    # 4th surface: the REQUIRE-SPECIFIC PICKER. The gate renders a numbered list into
    # `gate_clarification`, which the miss renderer copies verbatim into `escalate_message`.
    # D1 never fires on these turns, which is why this surface needs its own pass over the
    # rendered text. NO reordering: the numbers are the pick affordance, suffixes only.
    if require_spec and dym_ok and isinstance(out.get("escalate_message"), str) and out["escalate_message"]:
        lines = []
        for line in out["escalate_message"].split("\n"):
            # D11-reproduced: `build-suggest-offer.js:360`'s own numbered-line match, over
            # the picker the GATE rendered this turn.
            match = _PICKER_LINE_RE.match(line)
            if not match:
                lines.append(line)  # header / non-item line
                continue
            key = _dym_lookup(match.group(1), dym_probed)
            if key is None:
                lines.append(line)  # unprobed (e.g. multi-uuid) renders BARE
                continue
            lines.append(line + _stamp(key))
        out["escalate_message"] = "\n".join(lines)

    # THE CUSTOMER'S SPELLING. `d1.token` is the RESOLVER's echo, not what the customer typed,
    # so a canonical-coded customer miss printed the debtor code and a dashed product token
    # printed mangled. Each entity is keyed under BOTH its `raw` and its `canonical_code`
    # (mirroring what the resolver is SENT), raw before canonical, FIRST-wins on a collision.
    ent_by_tok: dict[str, Any] = {}
    for entity in (q["entities"] if isinstance(jsc.get(q, "entities"), list) else []):
        key = _type_norm(jsc.get(entity, "raw"))
        if key and key not in ent_by_tok:
            ent_by_tok[key] = entity
        code_key = _type_norm(jsc.get(entity, "canonical_code"))
        if code_key and code_key not in ent_by_tok:
            ent_by_tok[code_key] = entity

    def ent_of_tok(token: Any) -> Any:
        return ent_by_tok.get(_type_norm(token))

    def raw_of_tok(token: Any) -> Any:
        entity = ent_of_tok(token)
        raw = jsc.get(entity, "raw") if entity is not None else None
        return raw if jsc.truthy(raw) else token

    # Renderable survivors: a token whose candidates ALL drop via `humanLabel` (a bare uuid
    # with no display name) is skipped entirely - not shown, and its idx range never consumed.
    survivors: list[dict[str, Any]] = []
    for block in d1s:
        picks = [{"m": m, "label": _ms_human_label(m)} for m in _cap3(block["cands"])]
        picks = [p for p in picks if jsc.truthy(p["label"])]
        if picks:
            survivors.append({"block": block, "picks": picks})

    # Route on the SURVIVING-token count: 0 falls through to D2; 1 takes the existing
    # single-token block; more than 1 takes the numbered multi-block.
    d1 = survivors[0]["block"] if len(survivors) == 1 else None

    if len(survivors) > 1:
        # D1 (multi-token): one labelled sub-list per surviving token, global CONTIGUOUS idx.
        # Numbered mode subsumes the code / uuid split, so there is no uuid branching here.
        idx = 0
        blocks: list[str] = []
        out["suggest_last_result_set"] = []
        out["dym_candidates"] = []
        for s in survivors:
            token = s["block"]["token"]
            src_ent = ent_of_tok(token)
            # entity-type-label: resolver PRIMARY (this token's own best non-exact candidate),
            # parser hint FALLBACK, bare when neither is known.
            first_pick = s["picks"][0] if s["picks"] else None
            type_label = _prettify_type(jsc.get(jsc.get(first_pick, "m"), "entity_type")) or (
                _prettify_type(jsc.get(src_ent, "hint")) if src_ent is not None and jsc.truthy(jsc.get(src_ent, "hint")) else ""
            )
            type_sfx = f" ({jsc.js_string(type_label)})" if jsc.truthy(type_label) else ""
            cand_lines: list[str] = []
            for pick in s["picks"]:
                idx += 1
                match = pick["m"]
                is_u = _ms_is_uuid(jsc.get(match, "canonical_code"))
                # C3: annotate the RENDERED LINE ONLY. No sort is introduced, so numbering is
                # preserved by construction. The suffix never touches `p.label`, so
                # `suggest_last_result_set[].label` stays BARE and the numbered pick still
                # round-trips on idx / value. Unprobed renders BARE, never a misleading "no".
                key = _dym_lookup(jsc.get(match, "canonical_code"), dym_probed) if dym_ok else None
                sfx = ""
                if key is not None:
                    sfx = _stamp(key)
                cand_lines.append(f"  {idx}. {jsc.js_string(pick['label'])}{sfx}")
                out["suggest_last_result_set"].append(
                    {
                        "idx": idx,
                        "label": pick["label"],
                        "value": pick["label"] if is_u else jsc.get(match, "canonical_code"),
                        "product": jsc.get(match, "canonical_code"),
                        "uuid": jsc.get(match, "uuid") if jsc.truthy(jsc.get(match, "uuid")) else None,
                        "entity_type": jsc.get(match, "entity_type")
                        if jsc.truthy(jsc.get(match, "entity_type"))
                        else None,
                    }
                )
                out["dym_candidates"].append(
                    {
                        "code": jsc.get(match, "canonical_code"),
                        "uuid": jsc.get(match, "uuid") if jsc.truthy(jsc.get(match, "uuid")) else None,
                        "entity_type": jsc.get(match, "entity_type")
                        if jsc.truthy(jsc.get(match, "entity_type"))
                        else None,
                        "for_raw": token,
                        "for_hint": jsc.get(match, "entity_type")
                        if jsc.truthy(jsc.get(match, "entity_type"))
                        else (jsc.get(src_ent, "hint") if src_ent is not None and jsc.truthy(jsc.get(src_ent, "hint")) else None),
                        "for_canonical": jsc.get(src_ent, "canonical_code")
                        if src_ent is not None and jsc.truthy(jsc.get(src_ent, "canonical_code"))
                        else None,
                    }
                )
            # QUOTE THE CUSTOMER'S SPELLING. `for_raw` above stays the RESOLVER token, because
            # the pick round trip matches on it; only the rendered text changes.
            blocks.append(
                f'"{jsc.js_string(raw_of_tok(token))}"{type_sfx} - did you mean:\n'
                + "\n".join(cand_lines)
            )
        out["suggest_offer"] = True
        out["suggest_selection_context"] = "suggest_offer"
        out["suggest_response"] = (
            "Couldn't find some items:\n\n"
            + "\n".join(blocks)
            + "\n\n"
            + _cont("Reply a number to pick", f", or 'yes' to escalate to {team}.")
        )
        out["suggest_quick_reply"] = _quick_reply([_YES, _NO])
        out["dym_offer"] = mk_offer(out["dym_candidates"])
        return out

    if d1:
        picks = [{"m": m, "label": _ms_human_label(m)} for m in _cap3(d1["cands"])]
        picks = [p for p in picks if jsc.truthy(p["label"])]
        if picks:
            any_uuid = any(_ms_is_uuid(jsc.get(p["m"], "canonical_code")) for p in picks)
            src_ent = ent_of_tok(d1["token"])
            first_pick = picks[0] if picks else None
            d1_type_label = _prettify_type(jsc.get(jsc.get(first_pick, "m"), "entity_type")) or (
                _prettify_type(jsc.get(src_ent, "hint")) if src_ent is not None and jsc.truthy(jsc.get(src_ent, "hint")) else ""
            )
            d1_type_sfx = f" ({jsc.js_string(d1_type_label)})" if jsc.truthy(d1_type_label) else ""
            out["suggest_offer"] = True
            out["suggest_selection_context"] = "suggest_offer"
            if any_uuid:
                # Numbered mode: any uuid-coded (promotion) candidate means number buttons plus
                # human names in the message text; the pick round-trips by
                # `last_result_set[idx].uuid`.
                numbered = "\n".join(
                    f"{i + 1}. {jsc.js_string(p['label'])}" for i, p in enumerate(picks)
                )
                out["suggest_response"] = (
                    f'Couldn\'t pin down "{jsc.js_string(raw_of_tok(d1["token"]))}"{d1_type_sfx}. '
                    f"Here are the closest matches:\n{numbered}\n"
                    + _cont(
                        "Reply with a number to continue",
                        f", or would you like me to escalate to {team} team?",
                    )
                )
                out["suggest_quick_reply"] = _quick_reply(
                    [str(i + 1) for i in range(len(picks))] + [_YES, _NO]
                )
                out["suggest_last_result_set"] = [
                    {
                        "idx": i + 1,
                        "label": p["label"],
                        "value": p["label"],
                        "product": jsc.get(p["m"], "canonical_code"),
                        "uuid": jsc.get(p["m"], "uuid") if jsc.truthy(jsc.get(p["m"], "uuid")) else None,
                        "entity_type": jsc.get(p["m"], "entity_type")
                        if jsc.truthy(jsc.get(p["m"], "entity_type"))
                        else None,
                    }
                    for i, p in enumerate(picks)
                ]
                out["dym_candidates"] = [
                    {
                        "code": jsc.get(p["m"], "canonical_code"),
                        "uuid": jsc.get(p["m"], "uuid") if jsc.truthy(jsc.get(p["m"], "uuid")) else None,
                        "entity_type": jsc.get(p["m"], "entity_type")
                        if jsc.truthy(jsc.get(p["m"], "entity_type"))
                        else None,
                        "for_raw": d1["token"],
                        "for_hint": jsc.get(p["m"], "entity_type")
                        if jsc.truthy(jsc.get(p["m"], "entity_type"))
                        else (jsc.get(src_ent, "hint") if src_ent is not None and jsc.truthy(jsc.get(src_ent, "hint")) else None),
                        "for_canonical": jsc.get(src_ent, "canonical_code")
                        if src_ent is not None and jsc.truthy(jsc.get(src_ent, "canonical_code"))
                        else None,
                    }
                    for p in picks
                ]
                out["dym_offer"] = mk_offer(out["dym_candidates"])
            else:
                # Code mode. When the probe succeeded AND at least one offered code was
                # actually probed, SORT has-first and render one labelled line per code. The
                # sort runs BEFORE codes / roster / candidates are derived, so buttons, lines
                # and the pick round trip stay index-consistent. `suggest_quick_reply` stays
                # BARE CODES: the pick round-trips on that exact button string.
                dym_annotate_on = dym_ok and any(
                    _dym_lookup(jsc.get(p["m"], "canonical_code"), dym_probed) is not None
                    for p in picks
                )
                if dym_annotate_on:
                    # STABLE PARTITION, no tiebreak: a comparator tiebreak here would
                    # alphabetise and destroy the resolver's similarity ranking.
                    picks.sort(
                        key=lambda p: 0
                        if _dym_lookup(jsc.get(p["m"], "canonical_code"), dym_has) is not None
                        else 1
                    )
                codes = [jsc.get(p["m"], "canonical_code") for p in picks]
                if dym_annotate_on:
                    dym_lines = []
                    for i, p in enumerate(picks):
                        code = jsc.js_string(jsc.get(p["m"], "canonical_code"))
                        key = _dym_lookup(code, dym_probed)
                        sfx = ""
                        if key is not None:
                            sfx = _stamp(key)
                        dym_lines.append(f"{i + 1}. {code}{sfx}")
                    out["suggest_response"] = (
                        f'Couldn\'t find "{jsc.js_string(raw_of_tok(d1["token"]))}"{d1_type_sfx}. '
                        f"Did you mean:\n" + "\n".join(dym_lines) + "\n"
                        + _cont(
                            "Reply with a code to continue",
                            f", or would you like me to escalate to {team} team?",
                        )
                    )
                else:
                    out["suggest_response"] = (
                        f'Couldn\'t find "{jsc.js_string(raw_of_tok(d1["token"]))}"{d1_type_sfx}. '
                        f"Did you mean {_bso_human_list(codes)}? "
                        + _cont(
                            "Reply with a code to continue",
                            f", or would you like me to escalate to {team} team?",
                        )
                    )
                out["suggest_quick_reply"] = _quick_reply([*codes, _YES, _NO])
                out["suggest_last_result_set"] = [
                    {
                        "idx": i + 1,
                        "label": jsc.get(p["m"], "canonical_code"),
                        "value": jsc.get(p["m"], "canonical_code"),
                        "product": jsc.get(p["m"], "canonical_code"),
                        "uuid": jsc.get(p["m"], "uuid") if jsc.truthy(jsc.get(p["m"], "uuid")) else None,
                        "entity_type": jsc.get(p["m"], "entity_type")
                        if jsc.truthy(jsc.get(p["m"], "entity_type"))
                        else None,
                    }
                    for i, p in enumerate(picks)
                ]
                out["dym_candidates"] = [
                    {
                        "code": jsc.get(p["m"], "canonical_code"),
                        "uuid": jsc.get(p["m"], "uuid") if jsc.truthy(jsc.get(p["m"], "uuid")) else None,
                        "entity_type": jsc.get(p["m"], "entity_type")
                        if jsc.truthy(jsc.get(p["m"], "entity_type"))
                        else None,
                        "for_raw": d1["token"],
                        "for_hint": jsc.get(p["m"], "entity_type")
                        if jsc.truthy(jsc.get(p["m"], "entity_type"))
                        else (jsc.get(src_ent, "hint") if src_ent is not None and jsc.truthy(jsc.get(src_ent, "hint")) else None),
                        "for_canonical": jsc.get(src_ent, "canonical_code")
                        if src_ent is not None and jsc.truthy(jsc.get(src_ent, "canonical_code"))
                        else None,
                    }
                    for p in picks
                ]
                out["dym_offer"] = mk_offer(out["dym_candidates"])
            return out
        # All candidates dropped (a bare uuid with no display name): `suggest_offer` stays
        # false and we fall through to D2 / escalate-only. Never emit an invented label.

    # D2: the data-miss "alternatives" arm.
    if not jsc.truthy(alts):
        return out  # no alternatives on any run: keep the existing escalate behaviour

    raw_picks = _cap3(alts)
    any_uuid_alt = any(_ms_is_uuid(jsc.get(a, "value")) for a in raw_picks)

    compat = g["compatible_entities"] if isinstance(jsc.get(g, "compatible_entities"), list) else []
    first_compat = compat[0] if compat else None
    entities = q["entities"] if isinstance(jsc.get(q, "entities"), list) else []
    asked_code = (
        (jsc.get(first_compat, "code") or jsc.get(first_compat, "canonical_code"))
        if jsc.truthy(first_compat)
        else None
    )
    if not jsc.truthy(asked_code):
        asked_code = jsc.get(entities[0], "raw") if entities else "that item"

    # UUID LEAK (display only): a promotion's `askedCode` IS a uuid, and the "No {noun} for
    # {askedCode}" template printed it straight to the customer. `asked_code` itself is left
    # untouched - it is the dym-candidate-map linkage key; only the rendered label changes.
    def _asked_label() -> str:
        c0 = (
            (jsc.get(first_compat, "code") or jsc.get(first_compat, "canonical_code"))
            if jsc.truthy(first_compat)
            else None
        )
        if jsc.truthy(c0) and not _ms_is_uuid(c0):
            return jsc.js_string(c0)
        d0 = jsc.get(first_compat, "display") if jsc.truthy(first_compat) else None
        d0 = d0 if jsc.truthy(d0) else {}
        human = jsc.get(d0, "description") or jsc.get(d0, "product_name") or jsc.get(d0, "name")
        if jsc.truthy(human):
            return jsc.js_string(human)
        raw = jsc.get(entities[0], "raw") if entities else None
        return jsc.js_string(raw) if jsc.truthy(raw) else "that item"

    asked_label = _asked_label()
    noun = attachment_noun() or _D2_NOUN.get(jsc.get(q, "domain_hint")) or "result"

    def _d2_candidates() -> list:
        return [
            {
                "code": jsc.get(row, "product") or jsc.get(row, "value"),
                "uuid": jsc.get(row, "uuid") if jsc.truthy(jsc.get(row, "uuid")) else None,
                "entity_type": jsc.get(row, "entity_type")
                if jsc.truthy(jsc.get(row, "entity_type"))
                else None,
                "for_raw": asked_code,
                "for_hint": jsc.get(first_compat, "entity_type")
                if jsc.truthy(first_compat) and jsc.truthy(jsc.get(first_compat, "entity_type"))
                else None,
                "for_canonical": (
                    jsc.get(first_compat, "code") or jsc.get(first_compat, "canonical_code")
                )
                if jsc.truthy(first_compat)
                and jsc.truthy(jsc.get(first_compat, "code") or jsc.get(first_compat, "canonical_code"))
                else None,
            }
            for row in out["suggest_last_result_set"]
        ]

    if not any_uuid_alt:
        picks = raw_picks
        values = [jsc.get(a, "value") for a in picks if jsc.truthy(jsc.get(a, "value"))]
        if len(values) == 0:
            return out
        if axis == "date":
            if jsc.truthy(date_start):
                asked = (
                    f"{jsc.js_string(date_start)} to {jsc.js_string(date_end)}"
                    if (jsc.truthy(date_end) and date_end != date_start)
                    else jsc.js_string(date_start)
                )
            else:
                asked = "that date"
            cust_ent = jsc.find(
                entities, lambda e: jsc.js_string(jsc.get(e, "hint") or "").lower() == "customer"
            )
            cust = jsc.get(cust_ent, "raw") if jsc.truthy(cust_ent) else "This customer"
            near = "; ".join(
                jsc.js_string(jsc.get(a, "display") if jsc.truthy(jsc.get(a, "display")) else jsc.get(a, "value"))
                for a in picks
            )
            # datemiss-summary: lead with WHAT we resolved, so a date-relaxation offer still
            # confirms the entities it matched on.
            summary_text = jsc.nullish_str(out.get("found_summary")).strip()
            summary = f"Here's what you want:\n{summary_text}\n\n" if summary_text else ""
            text = (
                f"{summary}No delivery on {asked}. {jsc.js_string(cust)} has delivery on {near}. "
                + _cont(
                    "Reply with a date to continue",
                    f", or would you like me to escalate to {team} team?",
                )
            )
        else:
            text = (
                f"No {jsc.js_string(noun)} for {asked_label}. "
                f"Try: {', '.join(jsc.js_string(v) for v in values)}. "
                + _cont(
                    "Reply with a code to continue",
                    f", or would you like me to escalate to {team} team?",
                )
            )

        out["suggest_offer"] = True
        out["suggest_selection_context"] = "suggest_offer"
        out["suggest_response"] = text
        routing = jsc.get(q, "routing")
        is_cs_order = (
            jsc.get(routing, "suggested_team") == "customer_service"
            and jsc.get(routing, "suggested_agent") == "order_enquiries"
        )
        out["suggest_quick_reply"] = _quick_reply(
            list(values) if (axis == "date" and is_cs_order) else [*values, _YES, _NO]
        )
        out["suggest_last_result_set"] = [
            {
                "idx": i + 1,
                "label": jsc.get(a, "value"),
                "value": jsc.get(a, "value"),
                "product": jsc.get(a, "value"),
                "display": jsc.get(a, "display") if jsc.truthy(jsc.get(a, "display")) else jsc.get(a, "value"),
                "order_number": jsc.get(a, "order_number")
                if jsc.truthy(jsc.get(a, "order_number"))
                else None,
            }
            for i, a in enumerate(picks)
        ]
        # FINDING-1 FIX: build the map for CODE corrections ONLY. A date-relaxation offer
        # invites a DATE reply, and mapping those alternatives let a subsequent date reply
        # hijack the pick and DROP the customer.
        if axis != "date":
            out["dym_candidates"] = [c for c in _d2_candidates() if _is_code_shaped(c["code"])]
            out["dym_offer"] = mk_offer(out["dym_candidates"])
        return out

    # uuid-coded alternatives take numbered mode (defensive; never leak a uuid). Prefer the
    # display name; drop an alternative whose value is a uuid with no display.
    alt_picks = [
        {"a": a, "label": (jsc.get(a, "display") if _ms_is_uuid(jsc.get(a, "value")) else jsc.get(a, "value"))}
        for a in raw_picks
    ]
    alt_picks = [p for p in alt_picks if jsc.truthy(p["label"])]
    if len(alt_picks) == 0:
        return out  # nothing renderable: escalate-only

    numbered = "\n".join(f"{i + 1}. {jsc.js_string(p['label'])}" for i, p in enumerate(alt_picks))
    out["suggest_offer"] = True
    out["suggest_selection_context"] = "suggest_offer"
    out["suggest_response"] = (
        f"No {jsc.js_string(noun)} for {asked_label}. Here are the closest matches:\n{numbered}\n"
        + _cont(
            "Reply with a number to continue",
            f", or would you like me to escalate to {team} team?",
        )
    )
    out["suggest_quick_reply"] = _quick_reply(
        [str(i + 1) for i in range(len(alt_picks))] + [_YES, _NO]
    )
    out["suggest_last_result_set"] = [
        {
            "idx": i + 1,
            "label": p["label"],
            "value": p["label"],
            "product": jsc.get(p["a"], "value"),
            "uuid": jsc.get(p["a"], "uuid") if jsc.truthy(jsc.get(p["a"], "uuid")) else None,
            "display": jsc.get(p["a"], "display") if jsc.truthy(jsc.get(p["a"], "display")) else p["label"],
            "order_number": jsc.get(p["a"], "order_number")
            if jsc.truthy(jsc.get(p["a"], "order_number"))
            else None,
        }
        for i, p in enumerate(alt_picks)
    ]
    # FINDING-1 FIX (defensive): uuid alternatives are never dates, but mirror the code-only
    # scoping so a date-relaxation offer cannot produce a dym entry via this arm either.
    if axis != "date":
        out["dym_candidates"] = [c for c in _d2_candidates() if _is_code_shaped(c["code"])]
        out["dym_offer"] = mk_offer(out["dym_candidates"])
    return out
