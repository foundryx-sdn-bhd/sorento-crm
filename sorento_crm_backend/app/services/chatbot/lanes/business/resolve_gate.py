"""Port of `sub-resolve-and-gate` (RS-8): 28 nodes, one function, four exits.

`run()` walks the sub's graph in edge order and returns the item ONE of the four
`resolve-exit-*` arms would have emitted - `_exit_kind` plus the six named contract
fields plus that arm's own item, spread FIRST (LESSONS 94: each arm carries a different
item, and the spread order is what keeps `not-found-error-message`'s base intact).

n8n SNAPSHOTS every node's output. `$('resolve-entity').first().json` on the offer arm
does NOT show the keys `disallowed-entity-gate` added to its own input object, even
though within one JavaScript execution those are the same object - the run data is
serialized when the node finishes. Reproduced with `deepcopy` at every hand-off; the
`resolve-exit-offer` captures are what prove it (`resolved` carries the resolver's ten
keys and not one gate key).

Nodes reproduced, in edge order::

    build-ctx / item      the two carriers, with their contract throws
    entry-gate            entry === 'access_check'
    get-access-types      services.access_types
    Aggregate             {name: [...]} across the returned rows
    tier-gate             tier_gate.tier_gate()
    If4                   $json.name.length > 0
    resolve-entity        services.resolve_entity, body byte-equal to the n8n jsonBody
    disallowed-entity-gate gate.run_gate()
    build-ctx-resolved    {...gate, ctx: {...ctx, resolved, entities, gate}}
    If3                   the three-clause miss gate
    If-incoming-picker    require_specific && domain === 'incoming'
    If-customer-picker    customer_probe_entities.length > 0
    probe-*               services.probe
    annotate-*-picker     pickers.annotate_incoming / annotate_customer
    resolve-exit-*        the four arms
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from app.services.chatbot import jsc
from app.services.chatbot.contracts import EXIT_CONTRACT_FIELDS
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business import pickers
from app.services.chatbot.lanes.business.gate import run_gate
from app.services.chatbot.lanes.business.predicate import derive_predicate_words, derive_require
from app.services.chatbot.lanes.business.services import ResolveGateServices
from app.services.chatbot.lanes.business.tier_gate import tier_gate as run_tier_gate
from app.services.ledger_family import ledger_family_key, ledger_family_label

logger = logging.getLogger(__name__)

# `v.replace(/[-\s]+/g, '')` from the resolve-entity body's product-token fold.
_PRODUCT_FOLD = re.compile(r"[-\s]+")

# exec 12053189: a product code typed with a MINUS SIGN (U+2212, what Excel/Sheets emit
# on paste) or an EN DASH (U+2013, what Word autocorrect emits) missed the resolver's
# exact match, because `_PRODUCT_FOLD` is an ASCII-only, byte-graded port of n8n's own
# `[-\s]+` and must stay that way (it is checked against real captures). The fold that
# used to catch this ran in `head/output_exchange.py` before the resolver ever saw the
# token; deleted in the S3 rewrite with no equivalent (AC-1592 test triage). Folded to
# ASCII hyphen HERE, one step ahead of `_PRODUCT_FOLD`, so the graded regex still only
# ever runs against what it was captured against.
_UNICODE_DASH_FOLD: dict[str, str] = {
    "\u2212": "-",  # MINUS SIGN
    "\u2013": "-",  # EN DASH
}

# The two `sub-get-results` tools the pickers probe with, from the probe nodes' own
# `tool` parameters. Not a registry: two literals, named where they are used.
INCOMING_PROBE_TOOL = "crm_incoming_stock_list"
CUSTOMER_PROBE_TOOL = "crm_order_management_orders_list"
# The per-PRODUCT reads behind the promotion and purchase-order roster stamps
# (owner hand pass 3, rows 1 and 7).
PROMOTION_PROBE_TOOL = "crm_marketing_promotion_products_list"
PURCHASE_ORDER_PROBE_TOOL = "crm_procurement_po_placed_list"

#: R20 (owner round 7, 13 Sep 2026), extended by PLAN-chatbot-sales-report.md S4
#: wiring point 8: the `order_status` values whose customer picker must not offer a
#: delivery hint - see the `If-customer-picker` arm. The three scope words come from
#: `fetch.ORDER_STATUS_TO_SCOPE` rather than being spelled again, bare `outstanding`
#: is added because that table deliberately omits it (the field-reveal gate resolves
#: it), and `sales_report` for the SAME reason as the outstanding asks: the probe
#: measures DELIVERED DOs, a population this report does not read at all.
#: `top_selling` joins for the same reason (PLAN-chatbot-top-x-hot-selling-24sep.md S4
#: point 10): the ranking reads sales order lines, never delivered DOs.
OUTSTANDING_ORDER_STATUS: frozenset[str] = frozenset(
    {"outstanding", "sales_report", "top_selling", *fetch_mod.ORDER_STATUS_TO_SCOPE}
)

# The probe's injected default window, from `probe-customer-orders`' semantic_input
# expression (`$now.minus({days: 90})`). `annotate-customer-picker` mirrors this rule to
# decide whether its miss claim says "no recent delivery" or "no delivery"; the two are
# two halves of one sentence.
CUSTOMER_PROBE_DEFAULT_WINDOW_DAYS = pickers.CUSTOMER_PROBE_WINDOW_DAYS


def default_probe_start() -> str:
    """`$now.minus({days: 90}).toFormat('yyyy-MM-dd')` - the customer probe's window.

    n8n's `$now` is the workflow timezone (Malaysia), not UTC, so a turn taken in the
    eight hours after UTC midnight would otherwise probe a window one day wider than live
    does. Passed INTO `run()` rather than read there, so a replay is deterministic.
    """
    from datetime import datetime, timedelta, timezone

    now_myt = datetime.now(timezone.utc) + timedelta(hours=8)
    return (now_myt - timedelta(days=CUSTOMER_PROBE_DEFAULT_WINDOW_DAYS)).strftime("%Y-%m-%d")


class ResolveGateContractError(ValueError):
    """`build-ctx` / `item` refused the trigger payload, with the sub's own wording."""


def _snapshot(value: Any) -> Any:
    """What n8n's run data does to a node's output before the next node reads it."""
    return deepcopy(value)


# --------------------------------------------------------------------------- #
# Carriers
# --------------------------------------------------------------------------- #


def build_ctx(trigger: dict[str, Any]) -> dict[str, Any]:
    """`build-ctx` - the ctx carrier, with its contract throw.

    Returns `t.ctx` VERBATIM, which is the CARRIER object `{ctx: <the real ctx>}`, not the
    ctx itself: `Call 'sub-resolve-and-gate'` sends `ctx: {{ $('build-ctx').first().json }}`
    from `sub-main-processing`, whose own `build-ctx` emits `{ctx}`. That is why every
    reader in this sub writes `$('build-ctx').first().json.ctx.<key>` and why `run()` takes
    the INNER value.
    """
    ctx = trigger.get("ctx")
    if ctx is None or not isinstance(ctx, dict):
        raise ResolveGateContractError(
            "sub-resolve-and-gate: the trigger carried no `ctx` object - the contract is "
            "{ ctx, entry, item, is_test }"
        )
    return ctx


def carry_item(trigger: dict[str, Any]) -> dict[str, Any]:
    """`item` - re-emits the REAL item the caller was flowing, unchanged."""
    item = trigger.get("item")
    if item is None or not isinstance(item, dict):
        raise ResolveGateContractError(
            "sub-resolve-and-gate: the trigger carried no `item` object - the contract is "
            "{ ctx, entry, item, is_test }"
        )
    return item


def _parser_output(ctx: dict[str, Any]) -> dict[str, Any]:
    """`ctx.parse.output ?? {}`."""
    output = jsc.get(jsc.get(ctx, "parse"), "output")
    return output if isinstance(output, dict) else {}


def _prev_variables(ctx: dict[str, Any]) -> dict[str, Any]:
    """The previous turn's session variables, both shapes (`head/route._prev_variables`)."""
    session = jsc.get(ctx, "session")
    nested = jsc.get(jsc.get(session, "session_vars"), "variables")
    if jsc.truthy(nested):
        return nested if isinstance(nested, dict) else {}
    flat = jsc.get(session, "variables")
    return flat if isinstance(flat, dict) else {}


def _matched_types_by_token(resolved: Any) -> dict[str, set[str]]:
    """`{lower(token): {entity_type, ...}}` from a resolver payload's `resolutions`."""
    out: dict[str, set[str]] = {}
    for res in jsc.array(jsc.get(resolved, "resolutions")):
        token = jsc.nullish_str(jsc.get(res, "token")).strip().lower()
        if not token:
            continue
        types = out.setdefault(token, set())
        for match in jsc.array(jsc.get(res, "matches")):
            entity_type = jsc.nullish_str(jsc.get(match, "entity_type")).strip().lower()
            if entity_type:
                types.add(entity_type)
    return out


_WORD_RE = re.compile(r"[a-z0-9]+")


def _incoming_named_in_message(message: Any) -> bool:
    """Did the customer's OWN words say incoming this turn?

    `turn.policy.domain_switch_words(default_policy())` is the table (AC-1594: was
    `contracts.DOMAIN_SWITCH_WORDS`), read rather than copied: it is already the
    inventoried vocabulary that decides a this-turn domain switch, and a second list of
    the same words is how two readers of one question start disagreeing.
    """
    from app.services.chatbot.turn.policy import default_policy, domain_switch_words

    switch_words = domain_switch_words(default_policy())
    # ANY token, where `output_exchange`'s switch reader (its ~line 1125) demands EVERY
    # remaining content token name the same domain. Different questions: the switch asks
    # "is this message nothing but a domain word", this asks "did the customer say incoming
    # at all", and one incoming word anywhere is enough to keep the domain theirs.
    text = jsc.nullish_str(message).lower()
    return any(switch_words.get(tok) == "incoming" for tok in _WORD_RE.findall(text))


def retype_shipment_miss(
    parser: dict[str, Any], resolved: Any, *, carried_domain: Any, message: Any = None
) -> bool:
    """A container-hinted token that is ONLY a product is a product. Mutates `parser`.

    Owner console pass 4, item F (live turn ace4cec6). The customer typed "srtwc287" with
    a stock question already in play; the parser hinted the token `inbound_shipment` and
    emitted `check_incoming` / `incoming`, so `domain_signal_source` was `intent_explicit`,
    AC-816 rule 4's bare-entity inheritance was bypassed, and the reply led with "No
    incoming stock (ETA) found for SRTWC287" - while the RESOLVER's answer for that same
    token was two products (SRTWC287, SRTWC287-LID) and no shipment at all.

    Nothing at parse time can tell a container number from a product code, and guessing at
    the shape of one is the prompt's job, not this lane's (noted in the PR body). This
    reads resolver output and nothing else (D11):

    * ZERO `inbound_shipment` matches AND at least one `product` match for the token -
      both halves required, so a real container is untouched even when a product happens
      to share its token, and a genuine miss stays a miss rather than being answered as
      the wrong thing.
    * The DOMAIN is dropped only when its sole support was that entity: exactly one entity
      in scope, `domain_signal_source == "intent_explicit"`, and NO incoming word anywhere
      in the customer's own message. That last condition is the one the captures forced.
      `domain_signal_source` is stamped for ANY decisive intent plus a domain, said aloud
      or invented, so "M90ss any eta" (capture `sub-resolve-and-gate-rs/rg-15123789`) and
      the owner's bare "srtwc287" are IDENTICAL in structured state and differ only in that
      one of them says ETA. A product legitimately HAS incoming stock - the incoming picker
      lists product codes - so the entity retype is right in both and only the domain needs
      the customer's own word. The vocabulary is `contracts.DOMAIN_SWITCH_WORDS`,
      imported, not copied.
    * With the domain dropped, the CARRIED business domain applies - which is what rule 4
      would have done had the invented domain not bypassed it. With nothing carried the
      domain stands: inventing one would be a second guess on top of the first.

    Returns True when any entity was retyped, and stamps `shipment_hint_retyped` with the
    raw tokens, so the trace says why the turn changed shape.
    """
    entities = jsc.array(jsc.get(parser, "entities"))
    if not entities:
        return False
    by_token = _matched_types_by_token(resolved)

    retyped: list[str] = []
    for entity in entities:
        if not jsc.truthy(entity):
            continue
        if jsc.lower_or_empty(jsc.get(entity, "hint")) != "inbound_shipment":
            continue
        raw = jsc.nullish_str(jsc.get(entity, "raw")).strip()
        types = by_token.get(raw.lower())
        if not types or "inbound_shipment" in types or "product" not in types:
            continue
        entity["hint"] = "product"
        retyped.append(raw)

    if not retyped:
        return False
    parser["shipment_hint_retyped"] = retyped  # diagnostic

    sole_support = len(entities) == 1 and len(retyped) == 1
    if (
        sole_support
        and not _incoming_named_in_message(message)
        and jsc.get(parser, "domain_signal_source") == "intent_explicit"
        and jsc.lower_or_empty(jsc.get(parser, "domain_hint")) == "incoming"
        and jsc.truthy(carried_domain)
        and jsc.lower_or_empty(carried_domain) != "incoming"
    ):
        parser["domain_hint"] = carried_domain
        parser["intent_hint"] = None
        parser["domain_dropped_with_shipment_hint"] = "incoming"  # diagnostic
    return True


_BARE_MEMBER_OFFER_TYPES = ("product", "customer")
_BARE_REPLY_WORD_RE = re.compile(r"[a-z0-9]+")


def _hidden_spec_keys_from_ctx(ctx: dict[str, Any]) -> list[str]:
    """`ctx.access.hidden_spec_keys`, the same key `check_access` sets (PLAN-
    spec-visibility-policy.md "Spec fallback"). `[]` when access was never
    resolved this turn - the resolve route treats an absent/empty list as
    inert, so this is never a widening default."""
    access = jsc.get(ctx, "access")
    hidden = jsc.get(access, "hidden_spec_keys") if jsc.truthy(access) else None
    return list(hidden) if jsc.is_array(hidden) else []


def _contact_id_from_ctx(ctx: dict[str, Any]) -> str | None:
    """`ctx.contact.id`, the SAME read `check_access`'s own caller uses
    (`run.py`'s `contact_respond_id`) - sent alongside `hidden_spec_keys` so
    the resolve route can resolve this contact's policy itself rather than
    trusting only the caller-supplied list (security review B/S2).

    Stringified like every sibling read of this id in this file: the Respond.io
    contact id arrives on the wire as a JSON INTEGER, and the schema field it
    lands in (`ResolveReferenceRequest.contact_id`) is a string."""
    contact = jsc.get(ctx, "contact")
    value = jsc.get(contact, "id") if jsc.truthy(contact) else None
    return jsc.js_string(value) if jsc.truthy(value) else None


def resolve_bare_reply_under_member_offer(
    parser: dict[str, Any],
    *,
    ctx: dict[str, Any],
    services: ResolveGateServices,
    space_id: str | None = None,
    dry_run: bool = False,
) -> bool:
    """A bare reply the parser extracted NOTHING from, under an open `member_offer`,
    narrows the carried pair by asking the RESOLVER - never by pattern-matching the
    customer's own text. Mutates `parser` (== `ctx.parse.output`) in place.

    Owner console pass 5, item B2 (H80/AC-829, closed per review round 1 with #698's own
    rule restated: "the whole message goes to the resolver as one token, the resolver
    types it"). Turn 6ea9fd1a: "rpacc" after a working "last month" (hanlim/srtwc286)
    resolves to NOTHING in `ctx.parse.output.entities` (`[]`) - the parser has no verb,
    no code shape it recognises, nothing - so nothing downstream of the parser can invent
    a product entity for it, and the carried pair rides through the entity-op executor
    unchanged (`output_exchange`'s own filter-modification arm, `member_offer_filter_
    modification`). Production's real "RPACC" is a literal product code (two rows, one
    per company, same-code cross-company duplicates - `d7eeb622-...` Mocha,
    `c18fa9ea-...` Sorento), NOT a suffix of any code the offer's own
    `routing_companies[].codes` carries (measured: review round 1, B3 - the first cut of
    this fix invented a "SRTWC286-SH-RPACC" shape no capture's `routing_companies` has).
    Only the resolver can tell a real code from junk text, so this asks it - one extra
    probe call, `allowed_entity_types=["product", "customer"]` (the two the carried pair
    spans), OR-mode, `fallback_to_all_types=True` (the resolver decides, same contract
    every other caller of it gets).

    * A single entity_type / canonical_code across every match (same-code cross-company
      duplicates collapse to ONE answer, the same rule `gate.py`'s own OR-mode classifier
      uses for a normal token) REPLACES the carried entity of that type, `current_message:
      True`, and promotes `message_type` to `business_query` if it was `casual` - the
      SAME promotion the pre-existing "bare entity under an open member roster" block
      (AC-816 rule 3) already makes, and for the same reason: a `casual` message_type left
      in place routes the rest of the turn through the tail's `escalate-catalog` /
      `cs-offer-gate` as an UNANSWERED offer, even once the query has just been answered.
    * Anything else (no match, a genuinely ambiguous match, a type outside the pair) -
      changes NOTHING. The carried pair rides through as it always did, and the EXISTING
      pipeline answers not-found off it - never `offer_hold`, because `output_exchange`'s
      own ladder already decided this turn is a filter modification (a working date
      window survives from "last month") before `resolve_gate` ever runs; `offer_hold`'s
      own precondition (NO date, NO entity at all) is a different, narrower shape, tested
      as a regression guard in `test_pass5_item2_...py::TestB3...`.

    Bare = the whole message, capped at 4 words (`co_company_pick`'s own bound, above) -
    a longer message is a real sentence, not a code, and is left to the ladder's other
    arms. Never restricted to a single-word CODE SHAPE: the customer's own words decide
    nothing here, the resolver does.
    """
    from app.services.chatbot.session_state import offer_is_open

    prev = _prev_variables(ctx)
    if jsc.get(prev, "selection_context") != "member_offer" or not offer_is_open(prev):
        return False
    entities = jsc.array(parser.get("entities"))
    if any(jsc.truthy(e) and jsc.get(e, "current_message") is True for e in entities):
        return False  # this turn named something of its own - not a bare reply
    message = jsc.get(jsc.get(jsc.get(ctx, "text"), "message"), "message")
    raw = jsc.js_string(jsc.get(message, "text") or "").strip()
    words = _BARE_REPLY_WORD_RE.findall(raw.lower())
    if not raw or not words or len(words) > 4:
        return False

    body: dict[str, Any] = {
        "query": raw,
        "tokens": [raw],
        "match_mode": "or",
        "allowed_entity_types": list(_BARE_MEMBER_OFFER_TYPES),
        "access_levels": [],
        "domain": jsc.get(parser, "domain_hint") if jsc.truthy(jsc.get(parser, "domain_hint")) else "",
        "fallback_to_all_types": True,
        "limit": 15,
        "spec_fallback": True,
        # Fix round 10 on PR #833 ("for #833 yeah exact only"): exact values, no ranking guess.
        "exact_match": True,
        "understand_phrase": True,
        # AC-18 (PLAN-spec-visibility-policy.md "Spec fallback"): this contact's
        # hidden keys ride along so the resolve route can neither rank on one nor
        # print it in a candidate's specifications. `contact_id` + `space_id`
        # ride along too (security review B/S2), so the route resolves the
        # policy itself rather than trusting only this list.
        "hidden_spec_keys": _hidden_spec_keys_from_ctx(ctx),
        "contact_id": _contact_id_from_ctx(ctx),
        "space_id": space_id,
    }
    if dry_run:
        body["dry_run"] = True
    result = services.resolve_entity(body)
    resolutions = jsc.get(result, "resolutions")
    matches = jsc.array(jsc.get(resolutions[0], "matches")) if jsc.is_array(resolutions) and resolutions else []
    if not matches:
        return False

    # Same-code collapse, gate.py's own rule for a normal token: several matches that
    # agree on BOTH entity_type and canonical_code are cross-company duplicates of ONE
    # answer, not a choice. Anything else (a genuine choice, or a mix of types) is left
    # alone - guessing which one the customer meant is not this function's job.
    types = {jsc.js_string(jsc.get(m, "entity_type")).lower() for m in matches}
    codes = {jsc.js_string(jsc.get(m, "canonical_code") or "").lower() for m in matches}
    if len(types) != 1 or len(codes) != 1:
        return False
    matched_type = next(iter(types))
    if matched_type not in _BARE_MEMBER_OFFER_TYPES or not next(iter(codes)):
        return False

    canonical = jsc.get(matches[0], "canonical_code")
    new_entity = {
        "raw": raw,
        "hint": matched_type,
        "canonical_code": canonical,
        "current_message": True,
        "confident": True,
    }
    out_entities: list[Any] = []
    replaced = False
    for e in entities:
        if not replaced and jsc.lower_or_empty(jsc.get(e, "hint")) == matched_type:
            out_entities.append(new_entity)
            replaced = True
        else:
            out_entities.append(e)
    if not replaced:
        out_entities.append(new_entity)
    parser["entities"] = out_entities
    parser["bare_member_offer_entity_resolved"] = matched_type  # diagnostic
    if parser.get("message_type") == "casual":
        parser["message_type"] = "business_query"
    return True


# --------------------------------------------------------------------------- #
# get-access-types -> Aggregate
# --------------------------------------------------------------------------- #


def aggregate_names(rows: Any) -> dict[str, Any]:
    """The `Aggregate` node over field `name`.

    n8n's Aggregate skips an item that does not carry the field (`keepMissing` is off),
    which is also what makes the `alwaysOutputData` empty item on a zero-row read produce
    `{name: []}` rather than `{name: [undefined]}` - and `If4` then takes its FALSE leg.
    """
    return {"name": [row["name"] for row in jsc.array(rows) if isinstance(row, dict) and "name" in row]}


# --------------------------------------------------------------------------- #
# resolve-entity
# --------------------------------------------------------------------------- #


def _query_text(ctx: dict[str, Any]) -> str:
    """The `query` half of the jsonBody, including its own try/catch fallback to ''."""
    try:
        text = jsc.get(ctx, "text")
        inner = jsc.get(jsc.get(text, "message"), "message")
        value = jsc.get(inner, "text")
        if not jsc.truthy(value):
            value = jsc.get(jsc.get(inner, "attachment"), "description")
        return jsc.js_string(value) if jsc.truthy(value) else ""
    except Exception:  # noqa: BLE001 - `catch (_err) { return ''; }`
        return ""


#: The entity hints whose value IS a code the customer typed, as opposed to a word that
#: describes a class of them. `inbound_shipment` is here because the parser hands the SAME
#: typed product code either hint ("srtwt7202-new" came back `product` on one live run and
#: `inbound_shipment` on the next).
_CODE_BEARING_HINTS = ("product", "inbound_shipment")

#: PICKER-NO-CAP (owner, 2 Oct 2026; crew-tester chat pass on PR #1436): the lookup's
#: `limit` - the route cuts every token's matches to it BEFORE any roster cap is read,
#: so it must be at least the widest roster a picker may print. 50 is that ceiling
#: (`chatbot_entity_kinds.roster_cap`'s default and its S3 maximum). At 15, "incoming
#: AMS" with 20 matches listed 15.
_LOOKUP_LIMIT = 50


def _names_a_typed_code(parse_output: dict[str, Any]) -> bool:
    """Did this turn name a product CODE, rather than only words that describe a set?

    `gate._is_a_described_word` is the rule, called and never copied: a token with a digit
    that is code-shaped is a code ("srtwc286"), anything else is a description ("bidet").
    """
    from app.services.chatbot.lanes.business.gate import _is_a_described_word

    for entity in parse_output.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        if jsc.nullish_str(entity.get("hint")).strip().lower() not in _CODE_BEARING_HINTS:
            continue
        raw = jsc.nullish_str(entity.get("canonical_code")).strip() or jsc.nullish_str(
            entity.get("raw")
        ).strip()
        if raw and not _is_a_described_word(raw):
            return True
    return False


def _token_of(entity: Any) -> Any:
    """`String(x.canonical_code ?? '').trim() || (x.raw ?? '')`, product-folded.

    The fold is what makes "mfg6651-gm" reach the resolver as "mfg6651gm", and the
    dropped-filter gate's separator-insensitive comparison exists precisely because of it.
    A non-string `raw` on a product entity throws here exactly as it does in n8n.
    """
    value = jsc.nullish_str(jsc.get(entity, "canonical_code")).strip()
    if value == "":
        raw = jsc.get(entity, "raw")
        value = raw if raw is not None else ""
    if jsc.lower_or_empty(jsc.get(entity, "hint")) == "product":
        for bad, good in _UNICODE_DASH_FOLD.items():
            value = value.replace(bad, good)
        return _PRODUCT_FOLD.sub("", value)
    return value


def resolve_entity_body(
    ctx: dict[str, Any],
    *,
    space_id: str | None = None,
    dry_run: bool = False,
    tier_gate: dict[str, Any] | None = None,
    excluded_entity_ids: frozenset | None = None,
) -> dict[str, Any]:
    """The `resolve-entity` httpRequest jsonBody, key for key.

    `entity_pins` (H38) is OMITTED in AND mode and when nothing is pinned, which is what
    the n8n expression's `Object.keys(_pins).length ? ... : ''` does. Sending it in AND
    mode is a 400 by the route's own rule, so the omission is load-bearing, not tidiness.

    `dry_run` is the ONE key n8n's body does not have, and it is not a resolution input:
    it tells the endpoint to skip its `ai_assistant_usage_logs` row, which is the last
    write a D14 test turn could otherwise reach through this lane. The resolution is
    identical either way, so shadow parity is unaffected; it is omitted entirely on a live
    turn so the body stays byte-equal to n8n's there.

    R25/AC-1349 (round 3 re-check, security count-level): `tier_gate` is `run()`'s own
    `tier_gate_out` (the `entry == "access_check"` step's output) - when it carries a
    NON-EMPTY `access_levels_recomposed`, the body's `access_levels` is that list, never
    the parser's bare tokens. `needs_tier_ask` only fires for a contact entitled to MORE
    than one tier, so a single-tier contact never gets asked and reaches here with the
    tier gate having run and recomposed exactly one name - without this, the promotion
    leg counted whatever `_access_level_codes` made of the parser's own RAW token
    (which a brand-qualified code can translate wrong), running effectively unrestricted
    rather than the contact's actual, single entitled tier. `tier_gate=None` (it never
    ran) or an empty recomposed list (nothing to state) both fall back to today's
    behaviour unchanged.

    `excluded_entity_ids` (#1262 slice 9 F1a, security review round 2, 26 Sep 2026) is
    `run()`'s own `resolver_excluded_entity_ids` - a live-brand entity `turn_runtime.
    resolve_kinds` intercepted, dropped from the TOKEN LIST this body builds (`tokens`,
    `allowed_entity_types`, `entity_pins`) only. `parse_output.get("entities")` itself
    is read UNFILTERED everywhere else in this function (`scope_terms` below) - the
    entity still exists on `ctx.parse.output`, just never sent to the shared resolver.
    """
    parse_output = _parser_output(ctx)
    entities = parse_output.get("entities")
    if not isinstance(entities, list):
        # `_ents.map(...)` with no `?? []` - n8n throws here and the turn fails. Kept a
        # failure rather than softened to `[]`: a parser that emitted no entities array is
        # a broken understanding, and answering it unscoped is worse than saying so.
        raise TypeError(
            "resolve-entity: ctx.parse.output.entities is not an array, so the token map "
            "cannot be built (n8n throws on the same read)"
        )
    # Fix round 8 on PR #833: a grounded `specification` entity (`head/grounding.py`) is
    # not a record to look up. It travels as a registry binding (`extracted_specs`), and
    # a value the registry does not know as `unknown_values`, said back before anything
    # is counted; the resolver's token map never sees it.
    from app.services.chatbot.head.grounding import specification_entities

    grounded = specification_entities(entities)
    entities = [e for e in entities if not any(e is g for g in grounded)]
    if excluded_entity_ids:
        entities = [e for e in entities if id(e) not in excluded_entity_ids]

    match_mode = parse_output.get("match_mode")
    match_mode = match_mode if jsc.truthy(match_mode) else "and"
    # CHATBOT-QUEUE-FIX (crew browser pass, prod turn f0a2 "Srtswt3001 / Srtswt3001-gm
    # stock"): two or more PRODUCT codes name that many products, and one product cannot
    # be two codes, so an AND intersection over them only ever folds one code onto the
    # other's variant (the prefix probe for `srtswt3001` also matches SRTSWT3001-GM) and
    # drops the first product with no unresolved token and no miss. AND stays for a
    # product scoped by another kind ("SRTWT7445 for HANLIM").
    product_tokens = [
        x
        for x in entities
        if jsc.nullish_str(jsc.get(x, "hint")).strip().lower() == "product"
    ]
    if len(product_tokens) >= 2 and len(product_tokens) == len(entities):
        match_mode = "or"
    tier_gate_dict = tier_gate if isinstance(tier_gate, dict) else None
    recomposed_access_levels = (
        tier_gate_dict.get("access_levels_recomposed") if tier_gate_dict is not None else None
    )
    body: dict[str, Any] = {
        "query": _query_text(ctx),
        "match_mode": match_mode,
        "tokens": [_token_of(x) for x in entities],
        "allowed_entity_types": [jsc.get(x, "hint") for x in entities],
        "access_levels": (
            recomposed_access_levels
            if isinstance(recomposed_access_levels, list) and recomposed_access_levels
            else (
                parse_output.get("access_levels")
                if jsc.truthy(parse_output.get("access_levels"))
                else []
            )
        ),
        "domain": parse_output.get("domain_hint") if jsc.truthy(parse_output.get("domain_hint")) else "",
        "fallback_to_all_types": True,
        # ACCOUNT-LEDGER: "Soon Heng account 1" narrows the customer rows by level AFTER
        # the resolver answers, so the rows must not be cut first (31 "Soon Heng" rows on
        # the copy, the level-one ledgers past row 15). The route's own maximum.
        "limit": 200 if any(_asks_an_account(x) for x in entities) else _LOOKUP_LIMIT,
        "spec_fallback": True,
        # Fix round 10 on PR #833 ("for #833 yeah exact only"): exact values, no ranking guess.
        "exact_match": True,
        "understand_phrase": True,
        # AC-18 (PLAN-spec-visibility-policy.md "Spec fallback"): see the sibling
        # body builder above for the reasoning.
        "hidden_spec_keys": _hidden_spec_keys_from_ctx(ctx),
        "contact_id": _contact_id_from_ctx(ctx),
        "space_id": space_id,
    }
    if dry_run:
        body["dry_run"] = True
    grounded_specs = [
        {"key": g.get("spec_key"), "value": g.get("spec_value"), "evidence": g.get("raw"), "grounded": True}
        for g in grounded
        if g.get("spec_key") and g.get("spec_value") is not None
    ]
    if grounded_specs:
        body["extracted_specs"] = grounded_specs
    unknown_values = [
        {
            "key": g.get("spec_key") or "",
            "label": g.get("spec_label") or "",
            "said": g.get("raw") or "",
            "known": list(g.get("spec_known") or []),
        }
        for g in grounded
        if g.get("spec_value") is None and (g.get("raw") or "").strip()
    ]
    if unknown_values:
        body["unknown_values"] = unknown_values
    if jsc.js_string(match_mode).lower() != "and":
        pins: dict[str, Any] = {}
        for x in entities:
            if jsc.truthy(x) and jsc.truthy(jsc.get(x, "uuid")):
                token = _token_of(x)
                if jsc.truthy(token):
                    pins[token] = jsc.get(x, "uuid")
        if pins:
            body["entity_pins"] = pins

    # Shape B (attribute-first asks, work item B2, AC-1304): a `require` predicate
    # derived mechanically from what the parser already emitted, never from a second
    # read of the message text. Added ONLY when there is one - every other key above
    # is untouched, so a turn with no leg stays byte-identical to today.
    #
    # Gated on `REQUIRE_LEGS` (imported, never a second copy of the leg list): S1
    # ships four legs, `check_incoming` -> `{"incoming": true}` (AC-1303) is a real
    # `derive_require` mapping today even though the `incoming` leg itself is S2
    # (work item D1). Sending it anyway 422s `resolve_product_set` on "Unknown
    # require key(s): incoming" for every ordinary incoming turn - the exact
    # AC-1322 invariant this lane must not break. A leg lands the day its
    # `REQUIRE_LEGS` entry does, with no change needed here.
    from app.services.product_predicate_service import REQUIRE_LEGS

    require = derive_require(parse_output, message_text=_query_text(ctx))
    if require is not None and not set(require) <= set(REQUIRE_LEGS):
        require = None
    # The class word the PARSER named, forwarded as a value (turn re-architecture,
    # D11): a `product_type` / `category` entity IS "which taps", and reading it off
    # the verdict is what lets a HAS turn be described by something other than this
    # turn's own raw text.
    scope_terms: list[str] = []
    for entity in parse_output.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        if jsc.nullish_str(entity.get("hint")).strip().lower() not in ("product_type", "category"):
            continue
        raw = jsc.nullish_str(entity.get("raw")).strip()
        if raw and raw not in scope_terms:
            scope_terms.append(raw)
    # A CODE is not a description (F8 re-check, 20 Sep 2026). `derive_require` maps off
    # the INTENT alone, so `check_product_attachment` / `check_incoming` / `check_stock`
    # carried a leg on every turn - including a bare code lookup that describes no set at
    # all. With no class word to scope by, `resolve_product_set` then answers the leg over
    # the WHOLE catalogue and `references._emit_spec_matches` emits that population as a
    # third, whole-query resolution of ordinary product matches (the HAS branch passes no
    # `attach_to`). Measured on the clone for "SRTWT165-FT CERT": 200 products nobody
    # named on `gate.compatible_entities`, a 131 KB fetch envelope, and a did-you-mean
    # probe over 206 entities whose answer came back at the tool's 50-row page cap - which
    # `miss_suggest._annotate` correctly refuses to attribute (`page_saturated`,
    # `ok: false`), so the three real neighbours lost their has/no stamps and the reply
    # fell back to the bare inline sentence (live turns 790d43c3 and 85e536be).
    #
    # This is the SAME rule `turn_runtime.set_page_carry` already applies one seam later
    # and for the same measured incident ("an empty `scope_terms` describes 'every product
    # that has stock'", turns 92d565a5 / b383d402 / 2e7ca929): a spec tier reached with
    # nothing to scope by is a miss, not a set. Applied here it stops the population being
    # READ at all rather than only refusing to page it. A described ask ("which taps have
    # a cert") names a `product_type` / `category` word and is untouched; a turn naming
    # both a code and a class word keeps its scope term and is untouched too.
    if require is not None and not scope_terms and not grounded_specs and _names_a_typed_code(parse_output):
        require = None
    if require is not None:
        body["require"] = require
        body["predicate_words"] = derive_predicate_words(parse_output, require, message_text=_query_text(ctx))
        # Fix round 9 on PR #833: the reply's list limit, so a longer set comes back with
        # its breakdown and a zero set with its described products (read at call time).
        from app.services.chatbot.lanes.business import answer as answer_mod

        body["set_list_max"] = int(answer_mod.SET_LIST_MAX)
        if scope_terms:
            body["scope_terms"] = scope_terms
    return body


# --------------------------------------------------------------------------- #
# build-ctx-resolved
# --------------------------------------------------------------------------- #


def build_ctx_resolved(
    gate_item: dict[str, Any],
    *,
    ctx: dict[str, Any],
    resolved: dict[str, Any],
    aggregate: dict[str, Any] | None,
) -> dict[str, Any]:
    """`{...gate, ctx: {...ctx, resolved, entities, gate}}`.

    THE ITEM IS `{...gate, ctx}`, NOT `{ctx}`: `If3` forwards it and
    `not-found-error-message` uses it as the base of its own output, so replacing the
    gate's item with a bare `{ctx}` would rewrite that whole arm.

    `entities` is NULLABLE and that is a MEASUREMENT, not a default: `Aggregate` sits on
    the promotion lane only (55 of 542 captures reached the gate having run it), and every
    reader's `.isExecuted` guard is repointed to `!== null`.
    """
    return {
        **gate_item,
        "ctx": {**ctx, "resolved": resolved, "entities": aggregate, "gate": gate_item},
    }


# --------------------------------------------------------------------------- #
# The three Ifs
# --------------------------------------------------------------------------- #


def _report_ask_has_product_subject(parser: dict[str, Any], compatible: Any) -> bool:
    """Second-defect fix A (captain brief, 19 Sep 2026, general seam): a REPORT
    ask (`order_status` in `OUTSTANDING_ORDER_STATUS` - both the outstanding
    statuses and `sales_report` share this hole, the same population R20
    already names for the customer-picker probe) accepts EITHER a customer OR
    a product as its subject (AC-1119 / AC-1626 / S7). Clause 3 below exists
    for an order-domain turn in general, where a customer that failed to
    resolve really is nothing to answer with - but a report ask has an
    ALTERNATE subject that clause does not know about: "dealer Srt5674-N
    August total sale quantity" parsed "Srt5674-N" as a customer (the word
    "dealer" sits in front of it), the resolver's own `fallback_to_all_types`
    then found it as a PRODUCT instead, and clause 3 answered a miss with NO
    TOOL CALL AT ALL even though the report was fully answerable off that
    product. This is checked HERE, at the one seam that already knows both
    `order_status` and `compatible_entities`, rather than teaching the tool-
    pick dispatch (`run_fetch`) to re-derive a decision this gate already
    made."""
    order_status = jsc.js_string(parser.get("order_status") or "")
    if order_status not in OUTSTANDING_ORDER_STATUS:
        return False
    return any(
        jsc.truthy(c) and jsc.lower_or_empty(jsc.get(c, "entity_type")) == "product"
        for c in jsc.array(compatible)
    )


def if3_miss(ctx_resolved_ctx: dict[str, Any], *, parser: dict[str, Any]) -> bool:
    """`If3` - the miss gate, three OR'd clauses, verbatim.

    Clause 3 is the "customer resolved to nothing" case the first two cannot see: the
    domain accepts a customer, the parser named one, and nothing customer-shaped survived
    the gate. `_report_ask_has_product_subject` (second-defect fix A, 19 Sep 2026) is a
    FOURTH, AND'd exception on clause 3 alone - clauses 1 and 2 are untouched.
    """
    gate = jsc.get(ctx_resolved_ctx, "gate") or {}
    resolved = jsc.get(ctx_resolved_ctx, "resolved") or {}

    if jsc.get(gate, "gate_passed") is False:
        return True

    unresolved = jsc.get(resolved, "unresolved_tokens")
    unresolved = unresolved if jsc.truthy(unresolved) else []
    compatible = jsc.get(gate, "compatible_entities")
    compatible = compatible if jsc.truthy(compatible) else []
    if len(jsc.array(unresolved)) > 0 and len(jsc.array(compatible)) == 0:
        return True

    gate_debug = jsc.get(gate, "gate_debug")
    gate_debug = gate_debug if jsc.truthy(gate_debug) else {}
    allowed_lookup = jsc.get(gate_debug, "allowed_lookup")
    allowed_lookup = allowed_lookup if jsc.truthy(allowed_lookup) else []
    parser_entities = parser.get("entities")
    parser_entities = parser_entities if jsc.truthy(parser_entities) else []
    return (
        "customer" in jsc.array(allowed_lookup)
        and any(
            jsc.truthy(e) and jsc.lower_or_empty(jsc.get(e, "hint")) == "customer"
            for e in jsc.array(parser_entities)
        )
        and not any(
            jsc.truthy(c) and jsc.lower_or_empty(jsc.get(c, "entity_type")) == "customer"
            for c in jsc.array(compatible)
        )
        and not _report_ask_has_product_subject(parser, compatible)
    )


def if_incoming_picker(gate: dict[str, Any]) -> bool:
    """`If-incoming-picker` - `require_specific` true AND `gate_debug.domain` == 'incoming'.

    Strict type validation on both conditions, so a non-boolean `require_specific` or a
    non-string domain takes the FALSE leg rather than coercing.
    """
    gate_debug = jsc.get(gate, "gate_debug") or {}
    return jsc.get(gate, "require_specific") is True and jsc.get(gate_debug, "domain") == "incoming"


def if_customer_picker(gate: dict[str, Any]) -> bool:
    """`If-customer-picker` - `(gate.customer_probe_entities || []).length > 0`."""
    probe_entities = jsc.get(gate, "customer_probe_entities")
    probe_entities = probe_entities if jsc.truthy(probe_entities) else []
    return len(jsc.array(probe_entities)) > 0


# --------------------------------------------------------------------------- #
# The probes' inputs (the executeWorkflow parameter expressions)
# --------------------------------------------------------------------------- #


def _semantic_input(
    ctx: dict[str, Any],
    *,
    aggregate: dict[str, Any] | None,
    default_start: str | None,
    space_id: str | None,
) -> dict[str, Any]:
    """The probes' shared `semantic_input`, with the access-level intersection.

    `aggExecuted` is `ctx.entities !== null` on `build-ctx-resolved`'s ctx, which is why
    `Aggregate`'s three-state value had to survive as a key rather than as a guard.
    """
    parser = _parser_output(ctx)
    parser_levels = parser.get("access_levels") if jsc.truthy(parser.get("access_levels")) else []
    if aggregate is not None:
        names = sorted(jsc.array(jsc.get(aggregate, "name")), key=jsc.js_string)
        access_levels = [a for a in names if a in jsc.array(parser_levels)]
    else:
        access_levels = list(jsc.array(parser_levels))
    contact_id = jsc.get(jsc.get(ctx, "contact"), "id")
    start = parser.get("date_filter_start") if "date_filter_start" in parser else None
    end = parser.get("date_filter_end") if "date_filter_end" in parser else None
    return {
        "message_type": parser.get("message_type") if parser.get("message_type") is not None else None,
        "intent_hint": parser.get("intent_hint") if parser.get("intent_hint") is not None else None,
        "domain_hint": parser.get("domain_hint") if parser.get("domain_hint") is not None else None,
        "user_goal": parser.get("user_goal") if parser.get("user_goal") is not None else None,
        "access_levels": access_levels,
        "contact_id": jsc.js_string(contact_id) if contact_id is not None else None,
        "space_id": space_id,
        "date_mode": parser.get("date_mode") if parser.get("date_mode") is not None else None,
        "date_filter_start": (
            default_start if (start is None and end is None and default_start is not None) else start
        ),
        "date_filter_end": end,
        "is_active": parser.get("is_active") if parser.get("is_active") is not None else None,
        "order_status": parser.get("order_status") if parser.get("order_status") is not None else None,
        "requested_attributes": parser.get("requested_attributes")
        if parser.get("requested_attributes") is not None
        else [],
    }


def _user_prompt(
    ctx: dict[str, Any],
    *,
    entities: Any,
    access_levels: Any,
    date_start: Any,
    space_id: str | None,
) -> str:
    """The probes' `user_prompt` text block, field for field."""
    import json as _json

    parser = _parser_output(ctx)
    contact_id = jsc.get(jsc.get(ctx, "contact"), "id")
    return (
        f"message_type: {jsc.js_string(parser.get('message_type'))}  \n"
        f"intent_hint: {jsc.js_string(parser.get('intent_hint'))}  \n"
        f"domain_hint: {jsc.js_string(parser.get('domain_hint'))}  \n"
        f"user_goal: {jsc.js_string(parser.get('user_goal'))}  \n"
        # `ensure_ascii=False`: `JSON.stringify` emits the character, Python's default
        # emits a `\uXXXX` escape, and a customer name with an accent in it would reach the
        # probe's prompt as escaped gibberish where n8n sent the letter.
        f"entities: {_json.dumps(entities, separators=(',', ':'), ensure_ascii=False)} \n"
        f"access level: "
        f"{_json.dumps(access_levels, separators=(',', ':'), ensure_ascii=False)} \n"
        f"contact_id: {jsc.js_string(contact_id)} \n"
        f"space_id: {jsc.js_string(space_id)}\n"
        f"date_mode: {jsc.js_string(parser.get('date_mode'))} \n"
        f"date_filter_start: {jsc.js_string(date_start)}  \n"
        f"date_filter_end: {jsc.js_string(parser.get('date_filter_end'))}  \n"
        f"is_active: {jsc.js_string(parser.get('is_active'))}  \n"
        f"order_status: {jsc.js_string(parser.get('order_status'))}  "
    )


# --------------------------------------------------------------------------- #
# The four exits
# --------------------------------------------------------------------------- #


def exit_item(
    input_item: dict[str, Any],
    *,
    exit_kind: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """`{ ...$input.first().json, ..._fields, _exit_kind }` - the spread order matters."""
    out = {**input_item}
    for name in EXIT_CONTRACT_FIELDS:
        out[name] = fields.get(name)
    out["_exit_kind"] = exit_kind
    return out


# --------------------------------------------------------------------------- #
# Account level narrowing (ACCOUNT-LEDGER)
# --------------------------------------------------------------------------- #


def _folded(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def _account_list(levels: list[int]) -> str:
    names = [f"Account {n}" for n in levels]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def narrow_by_account(
    parser: dict[str, Any],
    resolved: dict[str, Any],
    levels_of: Callable[[list[str]], dict[str, int | None]],
    *,
    exact_names: bool = True,
) -> str | None:
    """Staff: "Soon Heng account 1". For each customer word this message typed with an
    `account`, drop the resolved customer matches whose `customers.account_level` differs
    (read by uuid, one query). Returns the refusal line when a word is left with none,
    else None. A word with no account, and every non-customer match, is untouched.

    `exact_names=False` (an ENFORCED contact) turns the exact trading name rule off: its
    own linked ledger can carry the typed word inside a longer name ("ABC TRADING
    ENTERPRISE") while a foreign ledger IS the word ("ABC TRADING"), and the rule would
    drop the contact's own row as a sibling. The scope screen after the resolver keeps
    only the contact's links anyway."""
    asks = [
        (_folded(_token_of(e)), str(e.get("raw")).strip(), e["account"])
        for e in parser.get("entities") or []
        if _asks_an_account(e)
    ]
    resolutions = [r for r in resolved.get("resolutions") or [] if isinstance(r, dict)]
    and_shaped = not resolutions and isinstance(resolved.get("intersection"), list)
    if and_shaped:
        # The AND answer (the live default `match_mode`) has no per-token `resolutions`:
        # its rows sit in `intersection`, and every row there matched EVERY token, so the
        # customer rows belong to each customer word typed. One stand-in resolution with
        # no token carries them; the `dropped` sweep below rewrites `intersection` and
        # `by_entity_type` from it.
        resolutions = [{"token": None, "matches": list(resolved["intersection"])}]
    if not asks or not resolutions:
        return None
    # The AND answer flags a list cut at the body's `limit` on `token_coverage`, not on
    # the rows (the OR rows carry `display.truncated_more_available` instead).
    and_cut = and_shaped and any(
        isinstance(c, dict) and str(c.get("entity_type") or "").lower() == "customer" and c.get("truncated")
        for t in resolved.get("token_coverage") or []
        if isinstance(t, dict)
        for c in t.get("coverage") or []
    )
    if and_shaped and len(asks) > 1:
        # Several customer words share the AND rows and none of them can be told apart:
        # narrow once by the SET of levels asked, never refuse (one word's narrowing must
        # not leave the next word refusing off what is left).
        return _narrow_and_by_levels(resolved, {account for _, _, account in asks}, levels_of)

    def _customers(resolution: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            m for m in resolution.get("matches") or []
            if isinstance(m, dict) and str(m.get("entity_type") or "").lower() == "customer"
        ]

    levels = levels_of(
        [str(m.get("uuid")) for r in resolutions for m in _customers(r) if m.get("uuid")]
    )
    dropped: set[str] = set()
    refusal: str | None = None
    for folded, typed, account in asks:
        for resolution in resolutions:
            if resolution.get("token") is not None and _folded(resolution.get("token")) != folded:
                continue
            customers = _customers(resolution)
            if not customers:
                continue
            # A word that IS one trading name ("Soon Heng Trading") speaks for that name
            # alone (crew ruling, 2 Oct 2026): the names merely carrying its words (SOON
            # GUAN HENG TRADING) are dropped for this word, so neither their levels nor
            # their rows answer it. No match of that exact name -> every match counts.
            exact = (
                [m for m in customers if ledger_family_key(_match_name(m)) == ledger_family_key(typed)]
                if exact_names
                else []
            )
            if exact:
                siblings = [m for m in customers if m not in exact]
                dropped |= {str(m.get("uuid")) for m in siblings}
                resolution["matches"] = [m for m in resolution["matches"] if m not in siblings]
                customers = exact
            kept = [m for m in customers if levels.get(str(m.get("uuid"))) == account]
            if kept:
                dropped |= {str(m.get("uuid")) for m in customers if m not in kept}
                resolution["matches"] = [m for m in resolution["matches"] if m not in customers or m in kept]
                continue
            if and_cut or any((m.get("display") or {}).get("truncated_more_available") for m in customers):
                # The resolver cut its list: the level may sit past the cut, so never
                # refuse (or narrow) off an incomplete list.
                continue
            dropped |= {str(m.get("uuid")) for m in customers}
            resolution["matches"] = [m for m in resolution["matches"] if m not in customers]
            if refusal is None:
                refusal = _no_such_account(typed, account, customers, levels)
    if dropped:
        for key in ("intersection",):
            if isinstance(resolved.get(key), list):
                resolved[key] = [m for m in resolved[key] if not _is_dropped(m, dropped)]
        by_type = resolved.get("by_entity_type")
        if isinstance(by_type, dict):
            resolved["by_entity_type"] = {
                k: [m for m in v if not _is_dropped(m, dropped)] if isinstance(v, list) else v
                for k, v in by_type.items()
            }
    return refusal


def _narrow_and_by_levels(
    resolved: dict[str, Any], asked: set[int], levels_of: Callable[[list[str]], dict[str, int | None]]
) -> None:
    """The AND rows' customer matches whose level is one of `asked`; the rest are dropped.
    Left alone when that would drop every customer row (nothing to refuse from here)."""
    customers = [
        m for m in resolved.get("intersection") or []
        if isinstance(m, dict) and str(m.get("entity_type") or "").lower() == "customer"
    ]
    levels = levels_of([str(m.get("uuid")) for m in customers if m.get("uuid")])
    dropped = {str(m.get("uuid")) for m in customers if levels.get(str(m.get("uuid"))) not in asked}
    if not dropped or len(dropped) == len({str(m.get("uuid")) for m in customers}):
        return None
    resolved["intersection"] = [m for m in resolved["intersection"] if not _is_dropped(m, dropped)]
    by_type = resolved.get("by_entity_type")
    if isinstance(by_type, dict):
        resolved["by_entity_type"] = {
            k: [m for m in v if not _is_dropped(m, dropped)] if isinstance(v, list) else v
            for k, v in by_type.items()
        }
    return None


def _match_name(match: dict[str, Any]) -> str:
    """A customer match's name: the row's `customer_name`, else its code."""
    return str((match.get("display") or {}).get("customer_name") or match.get("canonical_code") or "")


def _asks_an_account(entity: Any) -> bool:
    """A customer word typed THIS message with an `account` N >= 1 ("Soon Heng account 1")."""
    return (
        isinstance(entity, dict)
        and entity.get("hint") == "customer"
        and entity.get("current_message") is True
        and jsc.truthy(entity.get("raw"))
        and isinstance(entity.get("account"), int)
        and not isinstance(entity.get("account"), bool)
        and entity["account"] >= 1
    )


def _is_dropped(match: Any, dropped: set[str]) -> bool:
    return (
        isinstance(match, dict)
        and str(match.get("entity_type") or "").lower() == "customer"
        and str(match.get("uuid")) in dropped
    )


def _no_such_account(
    typed: str, account: int, customers: list[dict[str, Any]], levels: dict[str, int | None]
) -> str:
    """Q4: one trading name says which levels it has; several names say the word has none."""
    names = [_match_name(m) for m in customers]
    if len({ledger_family_key(n) for n in names}) > 1:
        return f'None of the customers matching "{typed}" has Account {account}.'
    have = sorted({lvl for m in customers if (lvl := levels.get(str(m.get("uuid")))) is not None})
    line = f"{ledger_family_label(names[0])} has no Account {account}."
    return f"{line} It has {_account_list(have)}." if have else line


# --------------------------------------------------------------------------- #
# The walk
# --------------------------------------------------------------------------- #


def run(
    ctx: dict[str, Any],
    entry: Any,
    item: dict[str, Any],
    *,
    services: ResolveGateServices,
    space_id: str | None = None,
    probe_default_start: str | None = None,
    dry_run: bool = False,
    roster_caps: Mapping[str, int] | None = None,
    resolver_excluded_entity_ids: frozenset | None = None,
    account_levels: Callable[[list[str]], dict[str, int | None]] | None = None,
    account_exact_names: bool = True,
) -> dict[str, Any]:
    """One pass through `sub-resolve-and-gate`. Returns the exit arm's item.

    `account_levels` (ACCOUNT-LEDGER) reads `customers.account_level` by uuid; given, a
    customer word typed with an `account` keeps only the matches at that level, and a word
    left with none puts the refusal line on `resolved["account_refusal"]`.

    `space_id` is the default respond workspace's (D5), and it reaches the probes' own
    `semantic_input` where n8n hard-codes `364817`. `probe_default_start` is the
    `$now.minus({days: 90})` the customer probe injects, passed in rather than computed so
    a replay is deterministic. `roster_caps` (PLAN-chatbot-answer-half-reattach.md
    "Roster cap") is handed straight through to `gate.run_gate`.

    `resolver_excluded_entity_ids` (#1262 slice 9 F1a, security review round 2, 26 Sep
    2026) is `turn_runtime.resolve_kinds`'s own live-brand intercept - a set of
    `id(entity dict)` to drop from the TOKEN LIST `resolve_entity_body` builds for the
    shared resolver only. `ctx` itself is never touched here: `parser = _parser_output
    (ctx)` two lines down is the SAME object `tier_gate` (the `access_check` arm,
    right below) and `gate.run_gate` (further down) both read - excluding an entity
    from THAT would blind their own, on-purpose brand reads (`tier_gate.py`'s
    `query_brands` fallback, `gate.py`'s brand-grouping) exactly the bug this
    parameter exists to avoid.
    """
    # The two carriers' contract throws, against the values this function was handed.
    # `build_ctx` / `carry_item` themselves take the TRIGGER and are what `run_from_trigger`
    # and the `build-ctx` / `item` node replays use.
    carrier = build_ctx({"ctx": {"ctx": ctx}})
    carried_item = carry_item({"item": item})
    ctx = jsc.get(carrier, "ctx")
    if not isinstance(ctx, dict):
        raise ResolveGateContractError(
            "sub-resolve-and-gate: `ctx` carried no inner ctx object - every reader in "
            "this sub indexes `$('build-ctx').first().json.ctx`"
        )
    parser = _parser_output(ctx)

    aggregate: dict[str, Any] | None = None
    tier_gate_out: dict[str, Any] | None = None

    # ── entry-gate: `entry === 'access_check'` (strict string equals) ────────
    if entry == "access_check":
        rows = services.access_types(
            contact_id=jsc.js_string(jsc.get(jsc.get(ctx, "contact"), "id")),
            space_id=space_id,
        )
        aggregate = aggregate_names(rows)
        tier_gate_out = run_tier_gate(
            _snapshot(aggregate), parser=parser, item=_snapshot(carried_item)
        )
        # ── If4: `$json.name.length > 0` on tier-gate's OUTPUT ───────────────
        if len(jsc.array(tier_gate_out.get("name"))) == 0:
            return exit_item(
                _snapshot(tier_gate_out),
                exit_kind="access_ask",
                fields={
                    "aggregate": _snapshot(aggregate),
                    "tier_gate": _snapshot(tier_gate_out),
                },
            )

    # ── a bare reply under an open member offer narrows the carried pair (item B2) ────
    # Placed BEFORE resolve-entity, deliberately: it MUTATES `parser["entities"]`, and the
    # main resolve-entity call two lines down reads that same object to build its own
    # tokens - so a narrowed product/customer rides the ONE round trip the rest of the
    # turn makes, exactly as a customer's own explicit entity would have.
    resolve_bare_reply_under_member_offer(
        parser, ctx=ctx, services=services, space_id=space_id, dry_run=dry_run
    )

    # ── resolve-entity ──────────────────────────────────────────────────────
    # R25/AC-1349: the tier gate's own recomposed access_levels, when it ran
    # and produced any - see `resolve_entity_body`'s own docstring.
    resolved = services.resolve_entity(
        resolve_entity_body(
            ctx,
            space_id=space_id,
            dry_run=dry_run,
            tier_gate=tier_gate_out,
            excluded_entity_ids=resolver_excluded_entity_ids,
        )
    )

    if account_levels is not None and isinstance(resolved, dict):
        account_refusal = narrow_by_account(
            parser, resolved, account_levels, exact_names=account_exact_names
        )
        if account_refusal:
            resolved["account_refusal"] = account_refusal

    # ── a container-hinted token that is ONLY a product is a product (item F) ─
    # Placed HERE, between the resolver and the gate, because this is the first point in
    # the turn where the evidence exists: the parser cannot tell a container number from a
    # product code, and `output_exchange` runs before anything has been resolved. `parser`
    # IS `ctx.parse.output`, so the correction reaches the gate, the answer lane and the
    # persisted state through the one object they all read.
    retype_shipment_miss(
        parser,
        resolved,
        carried_domain=jsc.get(_prev_variables(ctx), "domain_hint"),
        message=jsc.get(jsc.get(jsc.get(jsc.get(ctx, "text"), "message"), "message"), "text"),
    )

    resolved_snapshot = _snapshot(resolved)

    # ── disallowed-entity-gate. Its input IS resolve-entity's item, and it MUTATES
    #    it - so the gate gets its own copy and `resolved` keeps the pre-gate snapshot,
    #    which is what `$('resolve-entity')` returns downstream.
    gate_input = _snapshot(resolved)
    gate_item = run_gate(
        gate_input,
        parser=parser,
        resolver=gate_input,
        session=jsc.get(ctx, "session"),
        tier_gate=tier_gate_out,
        aggregate=aggregate,
        roster_caps=roster_caps,
    )
    gate_snapshot = _snapshot(gate_item)

    ctx_resolved_item = build_ctx_resolved(
        gate_snapshot,
        ctx=ctx,
        resolved=resolved_snapshot,
        aggregate=_snapshot(aggregate),
    )
    ctx_resolved_snapshot = _snapshot(ctx_resolved_item)

    base_fields = {
        "resolved": resolved_snapshot,
        "gate": gate_snapshot,
        "ctx_resolved": ctx_resolved_snapshot,
        "aggregate": _snapshot(aggregate),
        "tier_gate": _snapshot(tier_gate_out),
    }

    # ── If3 ─────────────────────────────────────────────────────────────────
    if not if3_miss(ctx_resolved_snapshot["ctx"], parser=parser):
        return exit_item(_snapshot(ctx_resolved_item), exit_kind="continue", fields=base_fields)

    picker_gate = jsc.get(ctx_resolved_snapshot["ctx"], "gate") or {}

    # ── If-incoming-picker ──────────────────────────────────────────────────
    if if_incoming_picker(picker_gate):
        entities = jsc.get(picker_gate, "compatible_entities")
        probe = _run_probe(
            services,
            ctx=ctx,
            tool=INCOMING_PROBE_TOOL,
            entities=entities,
            aggregate=aggregate,
            default_start=None,
            space_id=space_id,
        )
        annotated = pickers.annotate_incoming(_snapshot(picker_gate), probe=probe)
        return exit_item(
            _snapshot(annotated),
            exit_kind="offer",
            fields={**base_fields, "annotate_incoming": _snapshot(annotated)},
        )

    # ── If-customer-picker ──────────────────────────────────────────────────
    if if_customer_picker(picker_gate):
        entities = jsc.get(picker_gate, "customer_probe_entities")
        # R20 (owner round 7, 13 Sep 2026): an OUTSTANDING ask does not probe, and so
        # gets no delivery hint. `CUSTOMER_PROBE_TOOL` measures orders with an
        # `actual_delivery_date` - DELIVERED DOs, the owner's own 6 Sep ruling for
        # delivery enquiries - which is the OPPOSITE population from the outstanding
        # report's DO block (DOs not yet delivered). So the picker stamped "- no DO" on
        # every line and "None of these have a matching DO.", and the report two turns
        # later showed a DO with 5 outstanding: "it is still kinda strange for me though,
        # to say no DO, then later when i get the summary, there is DO." The hint cannot
        # be made true for this ask by rewording it, and there is nothing here worth
        # measuring, so neither happens. Decided on the ask's OWN `order_status`, not on
        # the domain: every other order-domain picker keeps today's hint.
        outstanding_ask = (
            jsc.js_string(jsc.get(parser, "order_status") or "").strip() in OUTSTANDING_ORDER_STATUS
        )
        probe = (
            None
            if outstanding_ask
            else _run_probe(
                services,
                ctx=ctx,
                tool=CUSTOMER_PROBE_TOOL,
                entities=entities,
                aggregate=aggregate,
                default_start=probe_default_start,
                space_id=space_id,
            )
        )
        annotated = pickers.annotate_customer(
            _snapshot(picker_gate), probe=probe, parser=parser
        )
        if outstanding_ask:
            # The annotator's UNPROBED arm renders exactly what R20 wants - the bare
            # picker, no suffixes, no closing claim - so its wording is untouched. Only
            # its reason is, because "probe_unavailable" would tell the operator a probe
            # failed when one was deliberately not run.
            annotated["customer_probe_skip_reason"] = "outstanding_ask"
        # `annotate_incoming` stays NULL on this arm: the customer annotator is not the
        # incoming one, and `sub-main-processing`'s `annotate-incoming-gate` reads exactly
        # that key to decide whether its stand-in executes.
        return exit_item(_snapshot(annotated), exit_kind="offer", fields=base_fields)

    return exit_item(_snapshot(ctx_resolved_item), exit_kind="not_found", fields=base_fields)


def probe_incoming(
    services: ResolveGateServices,
    *,
    ctx: dict[str, Any],
    entities: Any,
    aggregate: dict[str, Any] | None,
    space_id: str | None,
) -> Any:
    """The incoming picker's own probe, for a caller outside the miss arm.

    The arm above runs it only when the gate could not pin a single product
    (`if_incoming_picker`: `require_specific`), which is the only way the n8n graph could
    ever reach a picker. The re-architected narrower asks the same roster from the other
    side - a domain switch that CARRIES ten settled variants ("incoming", after a stock
    answer about them) is not ambiguous to the gate at all, so it never reached this
    probe and the roster printed without the has/no-incoming stamps the same roster shows
    when the customer names the family themselves (browser pass 3, turn 2). One probe,
    one builder for its inputs, two callers.
    """
    return _run_probe(
        services,
        ctx=ctx,
        tool=INCOMING_PROBE_TOOL,
        entities=entities,
        aggregate=aggregate,
        default_start=None,
        space_id=space_id,
    )


def probe_customer(
    services: ResolveGateServices,
    *,
    ctx: dict[str, Any],
    entities: Any,
    aggregate: dict[str, Any] | None,
    default_start: str | None,
    space_id: str | None,
) -> Any:
    """The customer picker's own probe, for a caller outside the miss arm.

    The `if_customer_picker` arm above runs it only when the gate could not pin a single
    customer. The re-architected narrower builds the same roster from the other side (a
    settled carry, a family widened by the resolver), so it never reached this probe and
    printed a roster with no has/no-DO stamps at all - the owner's hand pass 2, item 2.
    Same shape, and the same reason, as `probe_incoming` below.
    """
    return _run_probe(
        services,
        ctx=ctx,
        tool=CUSTOMER_PROBE_TOOL,
        entities=entities,
        aggregate=aggregate,
        default_start=default_start,
        space_id=space_id,
    )


def probe_promotion(
    services: ResolveGateServices,
    *,
    ctx: dict[str, Any],
    entities: Any,
    aggregate: dict[str, Any] | None,
    space_id: str | None,
) -> Any:
    """"Does this product have a promotion?", per candidate - the promotion roster's own
    probe (owner hand pass 3, row 1).

    A twin of `probe_incoming`, for a roster the n8n graph never had a picker for at all:
    the promotion domain narrows on TIER, so a product family under it was printed bare
    while the same family under incoming carried has/no incoming. `crm_marketing_
    promotion_products_list` is the per-PRODUCT read (`crm_marketing_promotions_list`
    returns promotions, which cannot be attributed back to a candidate).
    """
    return _run_probe(
        services,
        ctx=ctx,
        tool=PROMOTION_PROBE_TOOL,
        entities=entities,
        aggregate=aggregate,
        default_start=None,
        space_id=space_id,
    )


def probe_purchase_order(
    services: ResolveGateServices,
    *,
    ctx: dict[str, Any],
    entities: Any,
    aggregate: dict[str, Any] | None,
    space_id: str | None,
) -> Any:
    """"Does this product have a PO placed?", per candidate (owner hand pass 3, row 7).

    The same shape and the same reason as `probe_promotion` above. The picker stays: the
    owner keeps the roster and wants the stamp on it, not the roster replaced.
    """
    return _run_probe(
        services,
        ctx=ctx,
        tool=PURCHASE_ORDER_PROBE_TOOL,
        entities=entities,
        aggregate=aggregate,
        default_start=None,
        space_id=space_id,
    )


def _run_probe(
    services: ResolveGateServices,
    *,
    ctx: dict[str, Any],
    tool: str,
    entities: Any,
    aggregate: dict[str, Any] | None,
    default_start: str | None,
    space_id: str | None,
) -> Any:
    """One picker probe. A failure returns None, which is the annotators' UNPROBED arm.

    `probe-customer-orders` carries `onError: continueRegularOutput` for exactly this
    reason: a transient MCP failure must arrive as "we did not measure", never as an empty
    answer set, or every line renders a confident miss on evidence nobody gathered.
    `probe-incoming` has no such setting and its failure ends the turn in n8n - the port
    logs and returns None instead, which the incoming annotator renders as today's
    "None of these have incoming stock right now."
    """
    semantic_input = _semantic_input(
        ctx, aggregate=aggregate, default_start=default_start, space_id=space_id
    )
    user_prompt = _user_prompt(
        ctx,
        entities=entities,
        access_levels=semantic_input["access_levels"],
        date_start=semantic_input["date_filter_start"],
        space_id=space_id,
    )
    try:
        return services.probe(
            tool=tool,
            contact_id=jsc.get(jsc.get(ctx, "contact"), "id"),
            entities=entities,
            semantic_input=semantic_input,
            user_prompt=user_prompt,
        )
    except Exception:  # noqa: BLE001 - an unprobed picker is a documented arm, not a failure
        logger.warning("chatbot: picker probe %s did not run", tool, exc_info=True)
        return None


def run_from_trigger(
    trigger: dict[str, Any],
    *,
    services: ResolveGateServices,
    space_id: str | None = None,
    probe_default_start: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """`run()` from the sub's own `{ctx, entry, item, is_test}` trigger payload.

    The trigger's `ctx` is the CARRIER (`{ctx: ...}`) - see `build_ctx`. This unwraps it
    exactly as every node in the sub does, so a captured execution can be replayed whole.
    """
    carrier = build_ctx(trigger)
    item = carry_item(trigger)
    inner = jsc.get(carrier, "ctx")
    if not isinstance(inner, dict):
        raise ResolveGateContractError(
            "sub-resolve-and-gate: `ctx` carried no inner ctx object - every reader in "
            "this sub indexes `$('build-ctx').first().json.ctx`"
        )
    return run(
        inner,
        trigger.get("entry"),
        item,
        services=services,
        space_id=space_id,
        probe_default_start=probe_default_start,
        dry_run=dry_run,
    )
