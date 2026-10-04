"""Port of `disallowed-entity-gate.js` (sub-resolve-and-gate, 1,001 lines).

The domain <-> entity-type compatibility gate, the ambiguity pickers, the pinned-pick
rules, the document-class narrowing, the dropped-filter gate, the multi-company routing
axes and the Q23 access notice. It is the single largest node on the turn path and every
block below keeps the JS's own section header so the two can be read side by side.

Faithfulness rules this file obeys, because a "tidier" rewrite is how a port drifts:

* JS truthiness, `String()`, `Number()` and `Array.isArray` come from `jsc`, never from
  Python's own semantics (`[]` is TRUTHY in JS).
* Every regex is transcribed character for character, with `$` rewritten as `\\Z`
  (Python's `$` also matches before a trailing newline, JavaScript's does not) and `\\d`
  rewritten as `[0-9]` (Python's `\\d` matches every Unicode decimal).
* Key ORDER on the output object is the JS's assignment order, because the fixtures are
  compared after a JSON round trip and a reader may print them.
* `entities` iterates only the VALUES of `by_entity_type` (H16). The keys are entity-type
  names; treating them as data is how a metadata key would reach a customer's reply.

Two by-name reads become parameters: `parser` is `ctx.parse.output` and `session` is
`ctx.session`. `tier_gate` / `aggregate` are the `.isExecuted` three-state reads - the
node's item when it ran, `None` when it did not.

`resolver` and `item` are the SAME object in n8n (the gate's input IS `resolve-entity`'s
output, and n8n hands back one object, so `out.resolutions[i].resolved = true` is visible
through `resolver` too). They are separate parameters here because the capture harness
stubs them from two independent JSON parses, which is how every fixture was graded. The
aliasing is behaviourally inert: the only reader downstream of the mutation
(`_df_missed`) keys on `r.matches.length > 0`, and both mutations leave a non-empty
`matches`. `run()` passes one dict for both, so production keeps n8n's aliasing.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cmp_to_key
from typing import Any

from app.services.chatbot import jsc
from app.services.ledger_family import apart_from_group, customer_group_of, group_key, ledger_family_key

# PLAN-chatbot-answer-half-reattach.md "Roster cap" (owner ruling 20 Sep 2026):
# `roster_caps=None` (the parameter never supplied at all) means the CALLER predates
# `chatbot_entity_kinds.roster_cap` entirely - a raw `disallowed-entity-gate` port-
# replay fixture (`tests/chatbot/test_replay.py`) or a hand-built low-level test with
# no opinion on the feature - and gets `legacy_default` back: 10 for the customer
# picker (`test_rearch_r3_roster_cap.py::test_a_missing_customer_key_means_50_and_none_means_10`
# - a widening from the old literal 8, never a narrowing, so no recorded capture with
# 8 or fewer real matches moves), uncapped for the product/attachment one (that arm
# had NO ceiling at all before this column existed, and one port-replay capture in
# the corpus - `exec-14213018` - genuinely has 13 real candidates; capping it by
# default would be a port-fidelity regression `test_replay.py` has no signed
# divergence for). A caller that DOES supply a mapping - `resolve_gate.run`, reached
# from the real turn engine, which always builds one from every seeded
# `chatbot_entity_kinds` row - gets that mapping honoured for real, `50` (the
# column's own server default since PICKER-NO-CAP, owner 2 Oct 2026, and the S3
# ceiling) for any kind missing from it.
_DEFAULT_ROSTER_CAP = 50


def _roster_cap(
    roster_caps: Mapping[str, int] | None, kind: str, *, legacy_default: int | None
) -> int | None:
    if roster_caps is None:
        return legacy_default
    value = roster_caps.get(kind)
    return int(value) if isinstance(value, int) and value > 0 else _DEFAULT_ROSTER_CAP

# --------------------------------------------------------------------------- #
# The matrices, verbatim.
# --------------------------------------------------------------------------- #

ALLOWED: dict[str, list[str]] = {
    "master_products": ["product", "category", "brand"],
    "product_attachment": [
        "product",
        "attachment",
        "attachment_type",
        "category",
        "brand",
        "certificate",
    ],
    "promotion": ["product", "promotion", "category", "brand"],
    "inventory": ["product", "warehouse", "category", "brand"],
    # S4 point 7 (PLAN-chatbot-outstanding-report.md, D5): a location word narrows
    # crm_outstanding_report the same way it already narrows inventory/spo_allocation.
    # #1262 slice 9 (F1a): "brand" joins the matrix - resolved inside the chatbot
    # against the live `brands` table (`turn_runtime.active_brands`,
    # `resolve_gate.py`'s own pre-resolver intercept), never sent through the
    # shared resolver's order-domain fan-out to customer/transporter.
    "order": ["order", "customer_order", "transporter", "customer", "product", "warehouse", "brand"],
    "incoming": ["product", "inbound_shipment", "category", "brand"],
    "forms": ["form"],
    "portal_link": [],
    # 8 Sep 2026 (chatbot-warehouse-entity-and-last-in): replaces the old unscoped
    # pass-through (no row = "domain not in matrix", see `run_gate` below) now that the
    # tool takes `warehouse_ids`. Side effect: `ALLOWS_EMPTY` carries no `spo_allocation`
    # row, so a bare "last in" with ZERO entities now fails the gate instead of passing
    # through unscoped, as it used to. Per the plan, the owner accepted this matrix as
    # written; not adding an `ALLOWS_EMPTY` row is deliberate, not an oversight.
    "spo_allocation": ["product", "warehouse", "category", "brand"],
    # PLAN-chatbot-last-purchase-cost.md, 12 Sep 2026: same shape as `spo_allocation`
    # above, and the same "no ALLOWS_EMPTY row" ruling - a bare "last purchase cost"
    # with no product fails the gate and asks, same as `spo_allocation`.
    "purchase_cost": ["product", "warehouse", "category", "brand"],
    # AC-1592 test triage (review S1, live exec 11818957): NO row here used to mean
    # "domain not in matrix, pass every entity through unscoped" - which let an
    # ordinary-looking warehouse code (HOLD, DISPLAY, REPAIR) reach a document-list
    # fetch as a filter, even though none of this domain's three tools
    # (`crm_resource_attachments_list`/`_catalogue`/`_current_stock_list`) takes a
    # `warehouse_ids` parameter at all (confirmed: `fetch.TYPE_TO_PARAM`'s own
    # `"warehouse"` comment names the four tools that DO, and none of these three is
    # among them). `attachment_type` and `attachment` are the entity kinds these tools
    # actually filter on (`fetch.TYPE_TO_PARAM` maps them to `attachment_type_ids` /
    # `attachment_ids`, and `NARROWING_PARAMS` carries both). `attachment` was missing
    # from the first cut of this row, and it is the kind the resolver returns for a file
    # the customer NAMES ("catalog", "stock list"), so a named-file ask built no filter
    # at all and `ENTITY_FILTER_REQUIRED_TOOLS` refused the whole turn as not_found -
    # measured on replay case `console/case-024`, whose live call carried
    # `attachment_ids` with two real uuids.
    "resource_attachment": ["attachment_type", "attachment"],
    # PLAN-po-spo-warehouse-29sep W1 (29 Sep 2026): replaces the no-row pass-through now that
    # the PO tool takes `warehouse_ids`; a customer or transporter token no longer rides into
    # it. Supersedes PLAN-chatbot-warehouse-entity-and-last-in line 47 ("purchase_order
    # stays as is"). `ALLOWS_EMPTY` below keeps a bare "PO" listing the open book.
    "purchase_order": ["product", "warehouse", "category", "brand"],
}

#: PLAN-low-stock-report S6 (owner ruling, console round 2, 14 Sep 2026): INTENTS that
#: permit a scope-less ask even though their DOMAIN does not. `inventory` is
#: `ALLOWS_EMPTY: False` on purpose - a bare "stock?" must ask which product - but a bare
#: "low stock report" is a WHOLE-BOOK question by definition: it runs every site-pool
#: warehouse and every admitted product, which is the journey's own first phrasing.
#: Keyed on the intent rather than by flipping the domain, so the plain stock ask keeps
#: its narrowing prompt.
INTENTS_ALLOWING_EMPTY: frozenset[str] = frozenset({"low_stock_report"})

# S1 (promotion-picker): a promotion cannot be answered by a general search. Flipping
# `promotion` to true routes a scope-less promotion ask past the `needsScope` renderer.
ALLOWS_EMPTY: dict[str, bool] = {
    "promotion": False,
    "incoming": True,
    "forms": True,
    "portal_link": True,
    "master_products": False,
    "product_attachment": False,
    "inventory": False,
    "order": False,
    # PLAN-po-spo-warehouse-29sep W1: a bare "PO" lists the open book, as the old pass-through did.
    "purchase_order": True,
}

# Domains that need a SPECIFIC type present to be scopable (beyond compatibility).
REQUIRED_TYPES: dict[str, list[str]] = {"product_attachment": ["attachment_type"]}
TYPE_PROMPT = {
    "attachment_type": "product image, technical drawing, 3D model, or certificate",
}

# Domains where an ambiguous token must be disambiguated by the user before we can scope.
REQUIRE_SPECIFIC_DOMAINS = frozenset({"incoming", "product_attachment"})

# Types the fetch step maps to no `*_ids` param, so a row of one names a company we never
# actually queried. A DENY list, kept byte-identical to `entity-ids-transformer`'s own.
NO_TOOL_ID = frozenset({"brand", "category"})

VALID_BRANDS = ("sorento", "cabana", "mocha")

# --------------------------------------------------------------------------- #
# Transcribed regexes. `\Z`, not `$` (see the module docstring).
# --------------------------------------------------------------------------- #

_ACCOUNT_SUFFIX_DASH = re.compile(r"\s*-\s*\[[^\]]*\]\s*\Z")
_ACCOUNT_SUFFIX = re.compile(r"\s*\[[^\]]*\]\s*\Z")
_BRACKET_OR_PAREN = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_LEGAL_FORM = re.compile(r"\bSDN\.?\s*BHD\.?\b|\bSDN\b|\bBHD\b")
_NON_ALNUM_UPPER = re.compile(r"[^A-Z0-9]+")
_BRACKET_ANY = re.compile(r"\[[^\]]*\]")
_TRAILING_PAREN = re.compile(r"\s*-?\s*\([^)]*\)\s*\Z")
_WS_RUN = re.compile(r"\s+")
_TRAILING_WS_DASH = re.compile(r"[\s-]+\Z")
_SYNTHETIC_CODE = re.compile(r"^(dbr-|[0-9a-f]{8}-[0-9a-f]{4}-)", re.IGNORECASE)
_DC_NON_ALNUM = re.compile(r"[^a-z0-9]")
_DF_SEPARATORS = re.compile(r"[^a-z0-9]+")
_HAS_DIGIT = re.compile(r"[0-9]")
# `‐-―` is the range U+2010..U+2015; the other three are U+2212, U+FE58, U+FE63, U+FF0D.
# #1262 slice 3 (F5): ONE leading letter, not two - "M210-GM" (a real Mocha code) is
# one letter then digits, and the two-letter rule read it as a DESCRIPTION word
# ("bidet", "tap"), which is what let a bare "M210-GM" stock ask sweep the whole
# catalogue unscoped (`require={"stock": true}` with nothing to withhold it). A
# genuine description word still stays described - `_is_a_described_word`'s own
# `_HAS_DIGIT` guard reads "tap"/"basin" as described regardless of this shape at
# all, since neither carries a digit.
_CODE_SHAPED = re.compile(
    "^[A-Za-z][A-Za-z0-9._/\\-‐-―−﹘﹣－]*\\Z"
)


# --------------------------------------------------------------------------- #
# Small helpers the JS declares inline.
# --------------------------------------------------------------------------- #


def _is_a_described_word(token: Any) -> bool:
    """Not a code the customer typed - the SAME rule the dropped-filter gate below
    already uses (`_df_not_code_shaped`, inlined there), promoted here for C4's own
    need: a token with no digit at all, or one that is not code-shaped, is a
    DESCRIPTION word ("bidet", "tap") rather than a real product code ("srtwc286").

    C4 (attribute-first asks, AC-1326/AC-1319) needs exactly this distinction for
    its zero-qualifying carve-out: "which sorento bidet has cert" naming zero
    qualifying products must reach the miss flow, never the picker, but "cert for
    srtwc286" - a real, still-ambiguous product CODE - must keep going through the
    existing disambiguation picker (issue #750) even when none of its candidates
    happen to qualify. The predicate mechanism cannot tell the two apart from
    match_tier alone once neither produced a `spec_search` match; this is the same
    ordinary-language distinction `_df_not_code_shaped` already draws for the same
    reason on a different gate.
    """
    v = jsc.nullish_str(token).strip()
    return len(v) > 0 and not (bool(_HAS_DIGIT.search(v)) and bool(_CODE_SHAPED.match(v)))


def _norm(value: Any) -> str:
    """`s => String(s || '').toLowerCase().trim()`."""
    return jsc.lower_or_empty(value).strip()


def _lower_trim_nullish(value: Any) -> str:
    """`s => String(s ?? '').trim().toLowerCase()`."""
    return jsc.nullish_str(value).strip().lower()


def _locale_compare(a: str, b: str) -> int:
    """`String(a).localeCompare(String(b))`, approximated for ASCII company names.

    ICU's root collation is case-insensitive at primary strength and puts LOWERCASE
    before uppercase at tertiary strength, which is the opposite of a code-point sort.
    Reproduced as (casefold, swapcase): identical to ICU for the ASCII company names the
    resolver returns, and the 213 gate captures are what prove it. A name differing only
    by accent would need real collation; none exists in the corpus, and the day one does
    this comparison is where it lands.
    """
    primary_a, primary_b = a.casefold(), b.casefold()
    if primary_a != primary_b:
        return -1 if primary_a < primary_b else 1
    tertiary_a, tertiary_b = a.swapcase(), b.swapcase()
    if tertiary_a == tertiary_b:
        return 0
    return -1 if tertiary_a < tertiary_b else 1


def _flatten_by_entity_type(by_entity_type: Any) -> list[Any]:
    """`Object.values(resolver.by_entity_type ?? {}).flat()` - H16, VALUES only.

    `.flat()` flattens one level and passes a non-array value straight through, so a
    malformed map cannot silently drop a row. The KEYS are entity-type names and are
    never data: iterating them is what put a metadata key into a customer's reply.
    """
    out: list[Any] = []
    if not isinstance(by_entity_type, dict):
        return out
    for value in by_entity_type.values():
        if isinstance(value, list):
            out.extend(value)
        else:
            out.append(value)
    return out


def _warehouse_only_uuids(parser: Any, resolver: Any) -> set[str]:
    """R18: the resolved uuids that ONLY a WAREHOUSE-hinted token matched.

    The parser says what each word IS; the resolver says what each word FOUND. A uuid
    that only a warehouse word found is not evidence about any other kind of thing,
    whatever type the resolver stamped on it - "BRW" finding a customer called "STOCK
    TRANSFER - BRW TO SORENTO" is the resolver doing a text search, not the customer
    naming a company.

    Fails OPEN in both directions: a uuid the customer word found as well is absent from
    this set (the customer word is what put it on the list), and so is one reached
    through `intersection` / `by_entity_type`, which carry no token to attribute a match
    to at all.
    """
    warehouse_tokens = {
        jsc.js_string(jsc.get(e, "raw") or "").strip().casefold()
        for e in jsc.array(jsc.get(parser, "entities"))
        if jsc.lower_or_empty(jsc.get(e, "hint")) == "warehouse"
    }
    warehouse_tokens.discard("")
    if not warehouse_tokens:
        return set()

    def _uuid(m: Any) -> str:
        return jsc.js_string(jsc.get(m, "uuid") or "") if jsc.truthy(m) else ""

    from_warehouse: set[str] = set()
    from_elsewhere: set[str] = set()
    for resolution in jsc.array(jsc.get(resolver, "resolutions")):
        token = jsc.js_string(jsc.get(resolution, "token") or "").strip().casefold()
        bucket = from_warehouse if token in warehouse_tokens else from_elsewhere
        for m in jsc.array(jsc.get(resolution, "matches")):
            if _uuid(m):
                bucket.add(_uuid(m))
    for m in [
        *jsc.array(jsc.get(resolver, "intersection")),
        *_flatten_by_entity_type(jsc.get(resolver, "by_entity_type")),
    ]:
        if _uuid(m):
            from_elsewhere.add(_uuid(m))
    return from_warehouse - from_elsewhere


def _cust_name(match: Any) -> str:
    """`_custName` - the legal name with the ACCOUNT suffix stripped, nothing else."""
    display = jsc.get(match, "display") or {}
    raw = jsc.js_string(
        jsc.get(display, "customer_name") or jsc.get(display, "debtor_name") or ""
    ).strip()
    raw = _ACCOUNT_SUFFIX_DASH.sub("", raw)
    raw = _ACCOUNT_SUFFIX.sub("", raw)
    return raw.strip()


def _display_name(match: Any) -> str | None:
    """The resolver's own human label for this record, or `None` when it gave none.

    A CRM addition to the ported node's entity shape, and the reason is a defect the
    customer reads. `canonical_code` for a customer is the ACCOUNT code
    (`entity_resolver._probe_customer`) or the denormalised `debtor_name`
    (`_probe_customer_debtor_name`), and the two land on DIFFERENT `customers` rows for
    one trading name - so the per-uuid de-dupe below keeps both, and the not-found line
    printed "300-H070" at a customer who has never seen that string. The resolver already
    knows the name; the gate was simply dropping it.

    Customers only, deliberately: they are the one type whose `canonical_code` is not what
    the customer typed. A product code IS the product's name to this audience.
    """
    display = jsc.get(match, "display")
    for key in ("customer_name", "debtor_name"):
        value = jsc.get(display, key)
        if jsc.truthy(value):
            return jsc.js_string(value).strip()
    return None


def _cust_base(match: Any) -> str:
    """`_custBase` - the family GROUPING KEY, never customer copy."""
    name = _cust_name(match) or jsc.js_string(jsc.get(match, "canonical_code") or "")
    # A customer group the turn holds for this name wins over the name rule.
    group = customer_group_of(name)
    if group is not None:
        return group_key(group)
    base = name.upper()
    base = _BRACKET_OR_PAREN.sub(" ", base)
    base = _LEGAL_FORM.sub(" ", base)
    base = _NON_ALNUM_UPPER.sub(" ", base)
    # An ungrouped row beside a group of the same key: apart or joined, by the ONE switch.
    return apart_from_group(base.strip())


def run_gate(  # noqa: PLR0912, PLR0915 - one JS node, one function; splitting it hides the order
    item: dict[str, Any],
    *,
    parser: dict[str, Any] | None,
    resolver: dict[str, Any] | None = None,
    session: Any = None,
    tier_gate: dict[str, Any] | None = None,
    aggregate: dict[str, Any] | None = None,
    roster_caps: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """`disallowed-entity-gate`'s output item. `item` is mutated and returned, as in JS.

    `roster_caps` (PLAN-chatbot-answer-half-reattach.md "Roster cap", owner ruling
    20 Sep 2026) is `{entity kind: chatbot_entity_kinds.roster_cap}`, read at the two
    rosters this gate builds: the ambiguous-customer picker (kind "customer") and the
    ambiguous-product/attachment picker (kind "product"). A missing kind or `None`
    means 10, the column's own server default.
    """
    parser = parser if isinstance(parser, dict) else {}
    # Annotated `Any` deliberately: `domain` indexes the matrices above, and every one of
    # those lookups is `ALLOWED[domain]` in the JS, where a null key is a plain miss.
    domain: Any = parser.get("domain_hint")  # `parser.domain_hint ?? null`
    resolver = resolver if isinstance(resolver, dict) else item
    resolver = resolver if isinstance(resolver, dict) else {}
    # C4 (attribute-first asks, AC-1326): a resolver result carrying a `predicate`
    # block AT ALL - any `qualifying_total`, zero included - already answered the
    # gate's own question ("does this turn scope to real records"): the described
    # set is exactly the qualifying matches the resolver put in `resolutions[]`.
    # Nothing here needs to disambiguate a spec-search match against a sibling one
    # (they are not a customer's ambiguous typing, they are the honest count), and
    # nothing needs to refuse an unresolved product raw as a missed subject - the
    # predicate already carries that outcome. Bypasses the ambiguity picker
    # (REQUIRE_SPECIFIC_DOMAINS) and the product_attachment "subject did not
    # resolve" block below; a zero-qualifying predicate falls through unblocked
    # into `if3_miss`, which is the existing miss flow (AC-1319).
    predicate_bypass = isinstance(resolver.get("predicate"), dict)

    # ── Flatten + de-dupe resolver matches ──────────────────────────────────
    flat: list[Any] = []
    for resolution in jsc.array(resolver.get("resolutions")):
        flat.extend(jsc.array(jsc.get(resolution, "matches")))
    flat.extend(jsc.array(resolver.get("intersection")))
    flat.extend(_flatten_by_entity_type(resolver.get("by_entity_type")))

    by_uuid: dict[Any, dict[str, Any]] = {}
    for m in flat:
        if jsc.truthy(m) and jsc.truthy(jsc.get(m, "uuid")):
            entity = {
                "uuid": jsc.get(m, "uuid"),
                "entity_type": jsc.get(m, "entity_type"),
                "code": jsc.get(m, "canonical_code"),
            }
            # Written only when there IS one, so every entity the resolver gave no name
            # for keeps exactly the three keys the JS emits. Registered against the
            # capture corpus as `CAPTURE_BODY_ADDITIONS["disallowed-entity-gate"]`.
            display_name = _display_name(m)
            if display_name:
                entity["display_name"] = display_name
            by_uuid[jsc.get(m, "uuid")] = entity
    entities = list(by_uuid.values())

    allowed = ALLOWED.get(domain)  # `ALLOWED[domain] ?? null`
    gate_passed = True
    gate_reason = "ok"
    gate_clarification = ""
    compatible_entities: list[dict[str, Any]] = entities
    # D10 (owner console pass, 8 Sep 2026, turn 69d9900e "srtwc8610-sh hav incoming?"):
    # per token, the incompatible types it ONLY matched - never populated for a token that
    # also carries an allowed-type match. `token -> [types]`, so `miss_resolutions`
    # (`miss_suggest.py`) can force such a token past its own "already resolved" guard
    # WITHOUT this file knowing anything about the did-you-mean machinery it feeds.
    incompatible_only: dict[str, list[str]] = {}

    if allowed is None:
        # `${domain}` in a JS template literal, where `domain` is `parser.domain_hint ??
        # null`. Template interpolation of `null` is the string "null", NOT "" - so a turn
        # with no domain reads `domain 'null' not in matrix`, which is what the captures
        # show and what the port must reproduce.
        gate_reason = f"domain '{jsc.js_string(domain)}' not in matrix; passing through unscoped"
    else:
        compatible_entities = [e for e in entities if e["entity_type"] in allowed]
        # Computed UNCONDITIONALLY (not only on the all-incompatible branch below): B1
        # (turn 72146a1e "srtwc8610-sh certificate") needs it on a turn that otherwise
        # PASSES - an attachment_type token resolved fine, so `compatible_entities` is
        # non-empty and this domain's blanket "types [...] incompatible" branch never
        # fires, yet the PRODUCT token's only match is still a product_set. Per-token
        # (OR-mode `resolutions`) only; an AND-mode intersection has no single token to
        # blame a miss on.
        for resolution in jsc.array(resolver.get("resolutions")):
            matches = jsc.array(jsc.get(resolution, "matches"))
            if not matches:
                continue
            types = [jsc.get(m, "entity_type") for m in matches if jsc.truthy(m)]
            if types and not any(t in allowed for t in types):
                token = jsc.get(resolution, "token")
                if jsc.truthy(token):
                    incompatible_only[jsc.js_string(token)] = list(dict.fromkeys(types))
        if len(entities) == 0:
            intent = jsc.js_string(parser.get("intent_hint") or "")
            intent_allows_empty = intent in INTENTS_ALLOWING_EMPTY
            gate_passed = ALLOWS_EMPTY.get(domain) is True or intent_allows_empty
            if intent_allows_empty and ALLOWS_EMPTY.get(domain) is not True:
                # Its own reason string, so a trace says WHY a domain that normally
                # demands a filter let this one through.
                gate_reason = f"no entities; intent '{intent}' permits broad query"
            else:
                gate_reason = (
                    f"no entities; '{domain}' permits broad query"
                    if gate_passed
                    else f"no entities and '{domain}' requires a scoping entity"
                )
        elif len(compatible_entities) == 0:
            got = ", ".join(dict.fromkeys(jsc.js_string(e["entity_type"]) for e in entities))
            gate_passed = False
            gate_reason = f"types [{got}] incompatible with '{domain}'"

    # ── Required-type check (only if still passing) ─────────────────────────
    # Looks in resolver output AND raw parser hints, since an attachment_type may be a
    # filter the parser carried, not a UUID-resolved record.
    if gate_passed and isinstance(REQUIRED_TYPES.get(domain), list):  # `Array.isArray(...)`
        have_types = {e["entity_type"] for e in entities}
        have_types |= {jsc.get(e, "hint") for e in jsc.array(parser.get("entities"))}
        # R34/AC-1359: a `certificate` (or `attachment_type`) LEG in the
        # resolver's own predicate IS the document-type answer - the same
        # principle as the AC-1326 `predicate_bypass` above, narrowed to just
        # this requirement. "any tap has PPS cert" drops BOTH the category
        # and the attachment_type entity (the head normalises an entity with
        # no `canonical_code` away entirely), yet `derive_require`/
        # `recover_certificate_scheme` still recovered `{"certificate":
        # {"scheme": "PPS"}}` off the message text and the resolver genuinely
        # qualified a row - the gate must not then ask again for what the
        # predicate already answered.
        predicate_require = jsc.get(resolver.get("predicate"), "require") if predicate_bypass else None
        if isinstance(predicate_require, dict) and (
            jsc.truthy(predicate_require.get("certificate"))
            or jsc.truthy(predicate_require.get("attachment_type"))
        ):
            have_types.add("attachment_type")
        missing = [t for t in REQUIRED_TYPES[domain] if t not in have_types]
        if len(missing) > 0:
            gate_passed = False
            gate_reason = f"'{domain}' requires [{', '.join(missing)}] but none resolved"
            subject_row = jsc.find(
                parser.get("entities"), lambda e: jsc.get(e, "hint") == "product"
            )
            subject = jsc.get(subject_row, "raw")
            subject = subject if jsc.truthy(subject) else "this product"
            gate_clarification = " ".join(
                (
                    f"Please specify which type of attachment you need for {subject}, "
                    f"e.g. {TYPE_PROMPT['attachment_type']}."
                )
                if t == "attachment_type"
                else f"Please specify the {t}."
                for t in missing
            )

    # ── B1 attachment-subject-gate ──────────────────────────────────────────
    # The named subject product MISSED. A carried certificate / attachment_type must not
    # be allowed to scope the lookup on its own: certificate_ids alone satisfies the
    # tool's narrowing tuple (OR semantics) and returns every product carrying it.
    # Resolver-derived on purpose - NOT `current_message`, a known-corrupted signal.
    #
    # D10c (turn 72146a1e "srtwc8610-sh certificate"): a genuinely-unresolved token is one
    # shape of "missed"; a token whose only match is `incompatible_only` (a flyer/set code
    # hitting `product_set`) is the SAME shape from the customer's chair - AND-mode's OWN
    # gate never trips on it (the attachment_type token resolved fine, so `compatible_
    # entities` is non-empty and the blanket incompatible-types branch above never runs),
    # so this is the one place that catches it before the fetch scopes on the certificate
    # alone and answers about products that were never the one asked about.
    if gate_passed and domain == "product_attachment" and not predicate_bypass:
        unresolved = [_lower_trim_nullish(t) for t in jsc.array(resolver.get("unresolved_tokens"))]
        unresolved += [_lower_trim_nullish(t) for t in incompatible_only]
        product_raws = {
            _lower_trim_nullish(jsc.get(e, "raw"))
            for e in jsc.array(parser.get("entities"))
            if jsc.lower_or_empty(jsc.get(e, "hint")) == "product"
        }
        missed_subject = any(t in product_raws for t in unresolved)
        have_product = any(e["entity_type"] == "product" for e in compatible_entities)
        if missed_subject and not have_product:
            gate_passed = False
            gate_reason = (
                "'product_attachment' subject product did not resolve; refusing to scope "
                "on carried entities"
            )

    # ── AMBIGUITY -> REQUIRE SPECIFIC SELECTION ─────────────────────────────
    require_specific = False
    specific_options: list[dict[str, Any]] = []
    exact_entities: list[dict[str, Any]] = []
    collapsed_tokens: list[str] = []

    if domain in REQUIRE_SPECIFIC_DOMAINS:
        allowed_types = ALLOWED.get(domain)
        tokens = jsc.array(resolver.get("tokens"))
        typed_tokens = {_norm(t) for t in tokens}

        def is_compatible(m: Any) -> bool:
            return allowed_types is None or jsc.get(m, "entity_type") in allowed_types

        def is_prod(m: Any) -> bool:
            return jsc.js_string(jsc.get(m, "entity_type")).lower() == "product"

        # OR-mode: per-token resolutions[]. AND-mode: flat intersection[].
        or_resolutions = (
            resolver.get("resolutions")
            if isinstance(resolver.get("resolutions"), list)
            else None
        )
        if isinstance(resolver.get("intersection"), list):
            and_matches: list[Any] | None = resolver["intersection"]
        elif jsc.truthy(resolver.get("by_entity_type")):
            and_matches = _flatten_by_entity_type(resolver.get("by_entity_type"))
        else:
            and_matches = None

        if or_resolutions is not None:
            # ── OR-MODE ──
            # FIX D: the disambiguation choose-list is PRODUCT-ONLY. Non-product matches
            # resolve straight through and are NEVER offered as a choice.
            still_ambiguous: list[dict[str, Any]] = []
            for r in or_resolutions:
                matches = [m for m in jsc.array(jsc.get(r, "matches")) if is_compatible(m)]
                if len(matches) == 0:
                    continue
                products = [m for m in matches if is_prod(m)]
                non_products = [m for m in matches if not is_prod(m)]

                if len(non_products) > 0:
                    np_exact = [m for m in non_products if jsc.get(m, "match_tier") == "exact"]

                    def _np_key(m: Any) -> str:
                        code = jsc.get(m, "canonical_code")
                        if not jsc.truthy(code):
                            display = jsc.get(m, "display")
                            field = jsc.get(m, "match_field")
                            code = (
                                jsc.get(display, field)
                                if (jsc.truthy(display) and jsc.truthy(field))
                                else ""
                            )
                        return jsc.js_string(code if jsc.truthy(code) else "").lower().strip()

                    # exec 14206178: one container number resolved to TWO exact shipment
                    # rows (Mocha + Sorento). Same-key twins are ONE thing in N companies,
                    # not a choice - resolve every exact row.
                    np_same_key = (
                        len(np_exact) > 1
                        and len({_np_key(m) for m in np_exact}) == 1
                        and _np_key(np_exact[0]) != ""
                    )
                    if len(np_exact) == 1:
                        picks = [np_exact[0]]
                    elif np_same_key:
                        picks = np_exact
                    else:
                        picks = [non_products[0]]
                    for m in picks:
                        exact_entities.append(
                            {
                                "uuid": jsc.get(m, "uuid"),
                                "entity_type": jsc.get(m, "entity_type"),
                                "code": jsc.get(m, "canonical_code"),
                            }
                        )

                if len(products) > 0:
                    exacts = [m for m in products if jsc.get(m, "match_tier") == "exact"]

                    def _same_code(arr: list[Any]) -> bool:
                        return (
                            len(arr) > 0
                            and len(
                                {
                                    jsc.lower_or_empty(jsc.get(m, "canonical_code")).strip()
                                    for m in arr
                                }
                            )
                            == 1
                        )

                    if _same_code(exacts):
                        # mc-label-n8n: N exact hits sharing ONE code are the same product
                        # in several companies, not a choice.
                        for m in exacts:
                            exact_entities.append(
                                {
                                    "uuid": jsc.get(m, "uuid"),
                                    "entity_type": jsc.get(m, "entity_type"),
                                    "code": jsc.get(m, "canonical_code"),
                                }
                            )
                    elif len(exacts) == 0 and _same_code(products):
                        # mc-prefix-collapse (exec 13002464): the SAME collapse for a
                        # non-exact tier - two prefix hits on ONE code in two companies.
                        for m in products:
                            exact_entities.append(
                                {
                                    "uuid": jsc.get(m, "uuid"),
                                    "entity_type": jsc.get(m, "entity_type"),
                                    "code": jsc.get(m, "canonical_code"),
                                }
                            )
                        collapsed_tokens.append(jsc.nullish_str(jsc.get(r, "token")).strip().lower())
                    elif len(products) == 1:
                        m = products[0]
                        exact_entities.append(
                            {
                                "uuid": jsc.get(m, "uuid"),
                                "entity_type": jsc.get(m, "entity_type"),
                                "code": jsc.get(m, "canonical_code"),
                            }
                        )
                    elif predicate_bypass and all(
                        jsc.get(m, "match_tier") == "spec_search" for m in products
                    ):
                        # C4 (AC-1326): a `spec_search`-tier group is the described set
                        # HAS already qualified, not a customer's ambiguous typing - the
                        # answer to "which products have X" IS several products. Every
                        # OTHER token's ordinary ambiguity (issue #750: "srtwc286" prefix-
                        # matching ten variants) is untouched - only a group whose matches
                        # are ALL `spec_search` skips the picker.
                        for m in products:
                            exact_entities.append(
                                {
                                    "uuid": jsc.get(m, "uuid"),
                                    "entity_type": jsc.get(m, "entity_type"),
                                    "code": jsc.get(m, "canonical_code"),
                                }
                            )
                    elif (
                        predicate_bypass
                        and jsc.get(resolver.get("predicate"), "qualifying_total") == 0
                        and _is_a_described_word(jsc.get(r, "token"))
                    ):
                        # C4 / AC-1319 (zero-qualifying HAS ask): nothing satisfies the
                        # predicate, so these ordinary LOOKUP-tier matches for a
                        # DESCRIBED-SET word never earned a `spec_search` replacement
                        # (`_emit_spec_matches` only fires on a non-empty outcome) - but
                        # they still must not become a disambiguation picker over a class
                        # word nobody could ever "pick one of". Contributes nothing, same
                        # as an empty match list; `not_found_error_message` reads the
                        # checked codes straight off `resolved.resolutions`, not off this
                        # gate's own compatible-entities.
                        pass
                    else:
                        still_ambiguous.append({"token": jsc.get(r, "token"), "products": products})

            if len(still_ambiguous) > 0:
                specific_options = [
                    option
                    for option in (
                        {
                            "token": o["token"],
                            "candidates": [
                                {
                                    "uuid": jsc.get(m, "uuid"),
                                    "label": (
                                        jsc.get(m, "canonical_code")
                                        if jsc.truthy(jsc.get(m, "canonical_code"))
                                        else (
                                            jsc.get(jsc.get(m, "display"), "product_name")
                                            if jsc.truthy(
                                                jsc.get(jsc.get(m, "display"), "product_name")
                                            )
                                            else jsc.get(m, "uuid")
                                        )
                                    ),
                                    "entity_type": jsc.get(m, "entity_type"),
                                    "company": jsc.get(m, "company_name") or None,
                                }
                                for m in o["products"]
                            ],
                        }
                        for o in still_ambiguous
                    )
                    if len(option["candidates"]) > 0
                ]

        elif and_matches is not None:
            # ── AND-MODE: tier is uninformative; exact = canonical_code EQUALS a token ──
            compat_matches = [m for m in and_matches if jsc.truthy(m) and is_compatible(m)]
            non_products = [m for m in compat_matches if not is_prod(m)]
            for m in non_products:
                exact_entities.append(
                    {
                        "uuid": jsc.get(m, "uuid"),
                        "entity_type": jsc.get(m, "entity_type"),
                        "code": jsc.get(m, "canonical_code"),
                    }
                )
            products = [m for m in compat_matches if is_prod(m)]
            prod_exacts = [
                m for m in products if _norm(jsc.get(m, "canonical_code")) in typed_tokens
            ]
            same_code_exacts = (
                len(prod_exacts) >= 1
                and len(
                    {jsc.lower_or_empty(jsc.get(m, "canonical_code")).strip() for m in prod_exacts}
                )
                == 1
            )
            same_code_products = (
                len(prod_exacts) == 0
                and len(products) > 1
                and len(
                    {jsc.lower_or_empty(jsc.get(m, "canonical_code")).strip() for m in products}
                )
                == 1
            )
            if same_code_exacts:
                for m in prod_exacts:
                    exact_entities.append(
                        {
                            "uuid": jsc.get(m, "uuid"),
                            "entity_type": jsc.get(m, "entity_type"),
                            "code": jsc.get(m, "canonical_code"),
                        }
                    )
            elif same_code_products:
                for m in products:
                    exact_entities.append(
                        {
                            "uuid": jsc.get(m, "uuid"),
                            "entity_type": jsc.get(m, "entity_type"),
                            "code": jsc.get(m, "canonical_code"),
                        }
                    )
            elif len(products) > 1:
                specific_options = [
                    {
                        "token": ", ".join(jsc.js_string(t) for t in tokens),
                        "candidates": [
                            {
                                "uuid": jsc.get(m, "uuid"),
                                "label": (
                                    jsc.get(m, "canonical_code")
                                    if jsc.truthy(jsc.get(m, "canonical_code"))
                                    else (
                                        jsc.get(jsc.get(m, "display"), "product_name")
                                        if jsc.truthy(
                                            jsc.get(jsc.get(m, "display"), "product_name")
                                        )
                                        else jsc.get(m, "uuid")
                                    )
                                ),
                                "entity_type": jsc.get(m, "entity_type"),
                                "company": jsc.get(m, "company_name") or None,
                            }
                            for m in products
                        ],
                    }
                ]
            elif len(products) == 1:
                m = products[0]
                exact_entities.append(
                    {
                        "uuid": jsc.get(m, "uuid"),
                        "entity_type": jsc.get(m, "entity_type"),
                        "code": jsc.get(m, "canonical_code"),
                    }
                )

        # ── shared: if we built options, require the user to pick ──
        if len(specific_options) > 0:
            typed = [t for t in (_norm(t) for t in jsc.array(resolver.get("tokens"))) if t]
            # keep only candidates whose CODE relates to a typed token
            if len(typed) > 0:
                filtered: list[dict[str, Any]] = []
                for o in specific_options:
                    candidates = [
                        c
                        for c in o["candidates"]
                        if any(
                            t in _norm(c.get("code") or c.get("canonical_code") or c.get("label"))
                            or _norm(c.get("code") or c.get("canonical_code") or c.get("label"))
                            in t
                            for t in typed
                        )
                    ]
                    if len(candidates) > 0:
                        filtered.append({**o, "candidates": candidates})
                specific_options = filtered

        # FIX A: drop candidates already covered by an exact code-resolution (descriptor
        # noise), then drop any token group left empty.
        if len(specific_options) > 0 and len(exact_entities) > 0:
            exact_uuids = {e["uuid"] for e in exact_entities}
            specific_options = [
                option
                for option in (
                    {**o, "candidates": [c for c in o["candidates"] if c["uuid"] not in exact_uuids]}
                    for o in specific_options
                )
                if len(option["candidates"]) > 0
            ]

        if len(specific_options) > 0:
            require_specific = True
            gate_passed = False
            gate_reason = f"'{domain}' ambiguous (no single exact match); user must pick"
            # F16 (RS-9 round 4+5): append the company to ONLY the duplicated-code lines,
            # and ONLY on product_attachment. `incoming`'s annotator re-joins the rendered
            # lines to the probe BY EXACT CODE TEXT, so a suffixed line would miss that
            # set and render a confident FALSE "- no incoming" (measured 2/2).
            if domain == "product_attachment":
                code_companies: dict[str, list[Any]] = {}
                for o in specific_options:
                    for c in o["candidates"]:
                        ck = jsc.lower_or_empty(c.get("label")).strip()
                        if not ck:
                            continue
                        bucket = code_companies.setdefault(ck, [])
                        if c.get("company") and c["company"] not in bucket:
                            bucket.append(c["company"])
                specific_options = [
                    {
                        **o,
                        "candidates": [
                            (
                                {**c, "label": f"{c['label']} ({c['company']})"}
                                if (
                                    c.get("company")
                                    and code_companies.get(
                                        jsc.lower_or_empty(c.get("label")).strip()
                                    )
                                    is not None
                                    and len(
                                        code_companies[jsc.lower_or_empty(c.get("label")).strip()]
                                    )
                                    > 1
                                )
                                else c
                            )
                            for c in o["candidates"]
                        ],
                    }
                    for o in specific_options
                ]
            # Roster cap (owner ruling 20 Sep 2026): trimmed HERE, on `specific_options`
            # itself, so the printed roster and the `compatible_entities` the FIX A block
            # below derives from the SAME `specific_options` never disagree on which
            # candidates are actually pickable. `legacy_default=None`: this arm was
            # UNCAPPED before the column existed, so a caller with no `roster_caps`
            # opinion (the raw port-replay corpus) keeps that exactly.
            product_cap = _roster_cap(roster_caps, "product", legacy_default=None)
            if product_cap is not None:
                capped_options: list[dict[str, Any]] = []
                kept = 0
                for o in specific_options:
                    if kept >= product_cap:
                        break
                    candidates = o["candidates"][: product_cap - kept]
                    kept += len(candidates)
                    capped_options.append({**o, "candidates": candidates})
                specific_options = capped_options
            flat_labels = [c["label"] for o in specific_options for c in o["candidates"]]
            numbered = "\n".join(f"{i + 1}. {label}" for i, label in enumerate(flat_labels))
            # mc-prefix-collapse: say what DID resolve. A header line, never a numbered
            # one - downstream annotators key on /^\d+\.\s/ and must not see it as a pick.
            found_codes = list(
                dict.fromkeys(
                    code
                    for code in (
                        jsc.nullish_str(e.get("code")).strip()
                        for e in exact_entities
                        if jsc.js_string(e["entity_type"]).lower() == "product"
                    )
                    if code
                )
            )
            found_line = f"Found: {', '.join(found_codes)}.\n" if found_codes else ""
            # ATTACHMENT-MULTI (owner ruling 2 Oct 2026, Q3 (a)): one plain question, the
            # customer picker's own wording, never the domain key ("product_attachment
            # search needs to be more specific" reached customers 41 times on dev).
            gate_clarification = f"{found_line}Which product do you mean? Please choose:\n{numbered}"

        # FIX A: when prompting, the selectable set comes from the token-filtered,
        # exact-deduped `specific_options` - NOT from the unfiltered `entities` union.
        if require_specific:
            opt_uuids = {c["uuid"] for o in specific_options for c in o["candidates"]}
            compatible_entities = [e for e in entities if e["uuid"] in opt_uuids]
        elif len(exact_entities) > 0:
            compatible_entities = exact_entities

    cust_probe_entities: list[dict[str, Any]] | None = None
    cust_families: dict[str, list[str]] | None = None

    # R18 (owner round 5, 13 Sep 2026): the uuids only a WAREHOUSE word found. Computed
    # once, read twice below - by the ambiguous-customer picker (which must not list
    # them) and by the scope drop after it (which must not SEARCH them either, or a
    # report the customer asked for one company silently covers two).
    warehouse_only = _warehouse_only_uuids(parser, resolver)

    # ── AMBIGUOUS CUSTOMER -> ASK WHICH COMPANY ─────────────────────────────
    # A fuzzy customer token can resolve to several UNRELATED companies (exec 13207261:
    # "4 smart" -> 15 accounts across 6 companies, answered with 16 orders from three of
    # them). If3's miss gate cannot catch it - the customer DID resolve, just to too many.
    cust_pin_kept = False
    if not require_specific and "customer" in (ALLOWED.get(domain) or []):
        pick_applied = (
            parser.get("dym_pick_applied") is True
            or (
                not jsc.is_nan(jsc.js_number(parser.get("dym_partial_pick")))
                and jsc.js_number(parser.get("dym_partial_pick")) > 0
            )
            or (
                isinstance(parser.get("reference_positions"), list)
                and len(parser["reference_positions"]) > 0
            )
        )
        # R18 (owner round 5, 13 Sep 2026): a WAREHOUSE word is never a customer
        # candidate. Live, "outstanding dealer quantity for hanlim" carried a stale
        # `hint: "warehouse"` token ("BRW") beside the customer word; the resolver
        # answered that token with customer rows too (`customers.customer_name ilike
        # 'STOCK TRANSFER%BRW%'` is a real family on the prod copy), and this loop, which
        # keys ONLY on the resolved `entity_type`, turned one real family into seven lines
        # of "Which customer do you mean?". The parser already said what the word IS, so
        # the uuids that ONLY a warehouse-hinted token matched are dropped here - a uuid
        # the customer word matched as well is untouched, because then the customer word
        # is what put it on the list.
        bases: dict[str, Any] = {}
        for m in flat:
            if (
                not jsc.truthy(m)
                or not jsc.truthy(jsc.get(m, "uuid"))
                or jsc.js_string(jsc.get(m, "entity_type")).lower() != "customer"
                or jsc.js_string(jsc.get(m, "uuid")) in warehouse_only
            ):
                continue
            b = _cust_base(m)
            if b and b not in bases:
                bases[b] = m  # first row wins: the resolver ranks by similarity

        # R2: a customer the user ALREADY picked stays PINNED; re-ask only when they name
        # a new one (exec 13633742: typing a PRODUCT was answered with "Which customer?").
        ents = jsc.array(parser.get("entities"))

        def _is_cust(e: Any) -> bool:
            return jsc.truthy(e) and jsc.lower_or_empty(jsc.get(e, "hint")) == "customer"

        if any(_is_cust(e) and jsc.get(e, "current_message") is True for e in ents):
            cust_pinned = False
        else:
            cust_pinned = any(
                _is_cust(e)
                and jsc.get(e, "current_message") is not True
                and (jsc.truthy(jsc.get(e, "uuid")) or jsc.truthy(jsc.get(e, "canonical_code")))
                for e in ents
            )
        if cust_pinned:
            cust_pin_kept = True
        if not pick_applied and not cust_pinned and len(bases) > 1:
            # `legacy_default=10`: a widening from the old hard-coded eight-item slice,
            # per `test_a_missing_customer_key_means_50_and_none_means_10`.
            reps = list(bases.values())[: _roster_cap(roster_caps, "customer", legacy_default=10)]
            # FORWARD PROBE INPUT: keep a merged list - the candidates PLUS everything
            # else that resolved - so the probe can ask "does this customer have a
            # matching delivery?" under the SAME filters. Send the WHOLE ACCOUNT FAMILY,
            # because a representative uuid alone probes the wrong rows (exec 13250405).
            rep_bases = {b for b in (_cust_base(m) for m in reps) if b}
            fam_rows_all = [
                m
                for m in flat
                if jsc.truthy(m)
                and jsc.truthy(jsc.get(m, "uuid"))
                and jsc.js_string(jsc.get(m, "entity_type")).lower() == "customer"
                and _cust_base(m) in rep_bases
            ]
            # Remember WHICH uuids each rendered candidate stands for: the pick turn
            # re-resolves only the label it was given, and a label matching ONE account
            # had nothing to expand (execs 13256193 / 13256248).
            cust_families = {}
            for m in fam_rows_all:
                b = _cust_base(m)
                if not b or b not in rep_bases:
                    continue
                cust_families.setdefault(b, []).append(jsc.js_string(jsc.get(m, "uuid")))
            fam_seen: set[str] = set()
            probe_rows: list[dict[str, Any]] = []
            for m in fam_rows_all:
                key = jsc.js_string(jsc.get(m, "uuid"))
                if key in fam_seen:
                    continue
                fam_seen.add(key)
                probe_rows.append(
                    {
                        "uuid": jsc.get(m, "uuid"),
                        "entity_type": "customer",
                        "code": jsc.get(m, "canonical_code"),
                    }
                )
            cust_probe_entities = probe_rows + [
                c
                for c in compatible_entities
                if jsc.js_string(jsc.get(c, "entity_type")).lower() != "customer"
            ]

            # ── the option label names WHAT THE PICK COVERS ──────────────────
            # A MULTI-account option renders the FAMILY name (the representative minus
            # exactly the marker classes `_custBase` deletes to group the family) while
            # KEEPING the real casing and the legal form. A SINGLE-account option keeps
            # its exact account label - there the marker IS the identity.
            def _fam_size_of(m: Any) -> int:
                fam = (cust_families or {}).get(_cust_base(m))
                return len(set(fam)) if isinstance(fam, list) else 0

            def _rep_label(m: Any) -> str:
                name = _cust_name(m)
                if not name:
                    return jsc.js_string(jsc.get(m, "canonical_code") or "")
                if _fam_size_of(m) <= 1:
                    display = jsc.get(m, "display") or {}
                    exact = jsc.js_string(
                        jsc.get(display, "customer_name")
                        or jsc.get(display, "debtor_name")
                        or ""
                    ).strip()
                    return exact or name
                s = _BRACKET_ANY.sub(" ", name)
                while True:
                    prev = s
                    s = _TRAILING_PAREN.sub("", s)
                    if s == prev:
                        break
                s = _TRAILING_WS_DASH.sub("", _WS_RUN.sub(" ", s)).strip()
                return s or name

            # ── the option line NAMES THE LEDGER(S) the family sits in ──────
            # Owner ruling, console pass 3 (6 Sep 2026): "N. <name> (<company code>)".
            # A family is keyed by NAME (`_cust_base`, n8n's own grouping), so one line can
            # stand for accounts in more than one ledger - captures rg-15114061 (JYL JUBIN
            # under Mocha and Sorento) and rg-15125764 (YI HONG TILING under both) are
            # exactly that, and live rendered each as ONE line whose pick carried both
            # ledgers' uuids. The suffix therefore names EVERY ledger the family spans, in
            # first-seen order ("(SRT, MOCHA)"), never only the representative's - a single
            # code over a two-ledger family would say the pick reaches one ledger when it
            # reaches both (review of #706, S1). The resolver stamps `company_code` on every
            # company-scoped match (`_attach_company_info`); a row with none - a shared row,
            # an older payload - contributes nothing, and a family with no code at all
            # prints as it does today.
            def _company_suffix(m: Any) -> str:
                fam_key = _cust_base(m)
                codes: list[str] = []
                for row in fam_rows_all:
                    if _cust_base(row) != fam_key:
                        continue
                    code = jsc.js_string(jsc.get(row, "company_code") or "").strip()
                    if code and code not in codes:
                        codes.append(code)
                return f" ({', '.join(codes)})" if codes else ""

            # computed ONCE and indexed by i in BOTH renders, so the printed line and the
            # roster `title` are byte-equal by construction.
            rep_labels = [_rep_label(m) + _company_suffix(m) for m in reps]
            require_specific = True
            gate_passed = False
            gate_reason = (
                f"'{domain}' customer token matches {len(bases)} different companies; "
                "user must pick"
            )
            gate_clarification = "Which customer do you mean? Please choose:\n" + "\n".join(
                f"{i + 1}. {rep_labels[i]}" for i in range(len(reps))
            )
            # The roster must be the SAME rows in the SAME order as the numbered lines or
            # the positional pick misresolves. `title` is what compile-current-state
            # labels the row with, so a reply by name resolves as well as one by number -
            # which is why the company code goes into `rep_labels` itself rather than only
            # into the printed line: a customer who types the whole line back must resolve
            # to the row they read.
            compatible_entities = [
                {
                    "uuid": jsc.get(m, "uuid"),
                    "entity_type": "customer",
                    "code": jsc.get(m, "canonical_code"),
                    "title": rep_labels[i],
                }
                for i, m in enumerate(reps)
            ]

    # ── A PINNED PICK WINS OVER FUZZY RE-RESOLUTION ─────────────────────────
    # An entity carrying a uuid came from a roster pick. The resolver still re-resolves
    # its NAME as text and that sprays siblings (exec 13212841). Fails open: applied only
    # when every pinned uuid survived resolution.
    if not require_specific:
        # Gate entry on CARRIED pins too (exec 13705266): a customer picked two turns ago
        # comes back with current_message:false, and keying entry on this-turn pins alone
        # skipped both the re-seat and the family widening. #715 (H77/AC-825) finished
        # the job: every reader below was widened from a this-turn-only `pins` /
        # `pin_uuids` (now removed - dead once `pin_types`, `pin_bases`, `pin_codes` and
        # `_keep`'s own uuid check all moved to `pins_all` / `pin_uuids_all`) to the
        # same carried-or-current set the entry gate already used.
        pins_all = [
            e for e in jsc.array(parser.get("entities")) if jsc.truthy(e) and jsc.truthy(jsc.get(e, "uuid"))
        ]
        pin_uuids_all = {jsc.js_string(jsc.get(e, "uuid")) for e in pins_all}
        if len(pin_uuids_all) > 0:
            # A PICK IS AUTHORITATIVE - do not make it survive text re-resolution. Re-seat
            # any pinned row the resolver dropped (exec 13245182).
            allowed_pin = ALLOWED.get(domain)  # `ALLOWED[domain] ?? null`
            for e in pins_all:
                u = jsc.js_string(jsc.get(e, "uuid"))
                if any(jsc.js_string(jsc.get(c, "uuid")) == u for c in compatible_entities):
                    continue
                t = jsc.lower_or_empty(jsc.get(e, "hint"))
                if not t or (allowed_pin is not None and t not in allowed_pin):
                    continue
                # A picked customer's canonical_code is often a synthetic debtor id, which
                # the miss/answer renderers print verbatim. The entity's raw IS the roster
                # label we showed; products keep their canonical code.
                synthetic = bool(
                    _SYNTHETIC_CODE.search(jsc.js_string(jsc.get(e, "canonical_code") or ""))
                )
                if synthetic:
                    label = jsc.get(e, "raw") or jsc.get(e, "canonical_code")
                else:
                    label = jsc.get(e, "canonical_code") or jsc.get(e, "raw")
                label = label if jsc.truthy(label) else None
                compatible_entities = [*compatible_entities, {"uuid": u, "entity_type": t, "code": label}]

            # Issue #715 (H77/AC-825, 7 Sep 2026): built from `pins_all`, not `pins`. A
            # customer picked on an EARLIER turn re-enters this whole block on its
            # CARRIED pin alone (the `pin_uuids_all` entry gate above, exec 13705266's
            # own fix) - but `pin_types` stayed scoped to THIS-turn pins, so a carried
            # pick left it EMPTY, and `_keep` below (`t not in pin_types: return True`)
            # then kept every type "untouched", including every one of the resolver's
            # own re-resolved customer rows. The re-seat half of this mechanism (using
            # `pins_all` already) stopped the "which company" ASK; this is the half that
            # was supposed to stop the ANSWER widening and did not - production debtor
            # code 301-C001 is shared by 99 customer rows, the resolver returns up to
            # its own limit (15) of them for a bare re-resolved text search, and a
            # customer who had already picked ONE of them got orders from all 15.
            pin_types = {t for t in (jsc.lower_or_empty(jsc.get(e, "hint")) for e in pins_all) if t}
            # Gated on ALL pins: the re-seat loop above already put every pinned uuid back,
            # so checking the wider set just confirms it did its job before the family widens.
            all_present = all(
                any(jsc.js_string(jsc.get(c, "uuid")) == u for c in compatible_entities)
                for u in pin_uuids_all
            )
            fam_added: set[str] = set()
            if all_present:
                # A picked CUSTOMER selects its whole ACCOUNT FAMILY, never just the pinned
                # row. This turn's resolver only sees the label it was handed, so read the
                # family remembered from the picker turn.
                fam_mem = None
                variables = jsc.get(jsc.get(session, "session_vars"), "variables")
                if not jsc.truthy(variables):
                    variables = jsc.get(session, "variables")
                if not jsc.truthy(variables):
                    variables = {}
                if isinstance(jsc.get(variables, "picker_families"), dict) and jsc.truthy(
                    jsc.get(variables, "picker_families")
                ):
                    fam_mem = variables["picker_families"]
                if fam_mem:
                    have = {jsc.js_string(jsc.get(c, "uuid")) for c in compatible_entities}
                    for e in pins_all:
                        if jsc.lower_or_empty(jsc.get(e, "hint")) != "customer":
                            continue
                        b = _cust_base(
                            {
                                "display": {},
                                "canonical_code": jsc.get(e, "raw") or jsc.get(e, "canonical_code"),
                            }
                        )
                        fam = jsc.get(fam_mem, b)
                        if not isinstance(fam, list):
                            continue
                        for u in fam:
                            key = jsc.js_string(u)
                            if key in have:
                                continue
                            compatible_entities = [
                                *compatible_entities,
                                {
                                    "uuid": key,
                                    "entity_type": "customer",
                                    "code": jsc.get(e, "raw") or jsc.get(e, "canonical_code"),
                                },
                            ]
                            have.add(key)
                            fam_added.add(key)

                row_by_uuid = {
                    jsc.js_string(jsc.get(m, "uuid")): m
                    for m in flat
                    if jsc.truthy(m) and jsc.truthy(jsc.get(m, "uuid"))
                }
                # #715: `pin_uuids_all`, not `pin_uuids` - see the `pin_types` note above.
                # A carried pin's own row is usually ABSENT from `row_by_uuid` (the
                # resolver's bare-text re-search does not happen to return it among its
                # limit), so this stays empty for exactly the shape #715 is about - which
                # is right: no OTHER base to widen to, so only the pinned uuid itself
                # (re-seated above) survives `_keep` below.
                pin_bases = {
                    b
                    for b in (
                        _cust_base(row_by_uuid.get(u))
                        for u in pin_uuids_all
                        if jsc.truthy(row_by_uuid.get(u))
                        and jsc.js_string(jsc.get(row_by_uuid.get(u), "entity_type")).lower()
                        == "customer"
                    )
                    if b
                }
                # FIX C: a PRODUCT pin gets the same base-equivalence a customer pin gets,
                # or the filter drops the SIBLING COMPANY'S ROW for the same canonical code
                # (exec-13488926: routing_companies collapsed 2 -> 1 and stamped a company
                # nobody picked). Built ONLY from NON-customer pinned rows, because a
                # customer's canonical_code is a debtor code.
                pin_codes = {
                    c
                    for c in (
                        jsc.js_string(jsc.get(row_by_uuid.get(u), "canonical_code") or "").upper()
                        for u in pin_uuids_all
                        if jsc.truthy(row_by_uuid.get(u))
                        and jsc.js_string(jsc.get(row_by_uuid.get(u), "entity_type")).lower()
                        != "customer"
                    )
                    if c
                }

                def _keep(c: dict[str, Any]) -> bool:
                    t = jsc.js_string(jsc.get(c, "entity_type")).lower()
                    if t not in pin_types:
                        return True  # other types untouched
                    if jsc.js_string(jsc.get(c, "uuid")) in pin_uuids_all:
                        return True  # the pinned row itself, this turn or carried
                    if jsc.js_string(jsc.get(c, "uuid")) in fam_added:
                        return True  # remembered family of the pick
                    if t != "customer":  # FIX C: same-code twins survive
                        row = row_by_uuid.get(jsc.js_string(jsc.get(c, "uuid")))
                        return bool(row) and (
                            jsc.js_string(jsc.get(row, "canonical_code") or "").upper() in pin_codes
                        )
                    if not pin_bases:
                        return False  # customer pin with no base -> exact
                    row = row_by_uuid.get(jsc.js_string(jsc.get(c, "uuid")))
                    return bool(row) and _cust_base(row) in pin_bases

                kept = [c for c in compatible_entities if _keep(c)]
                if len(kept) > 0:
                    compatible_entities = kept

        # ── R18: A WAREHOUSE WORD IS NOT A CUSTOMER, IN SCOPE EITHER ────────
        # The picker above stopped LISTING them; this stops the turn SEARCHING them. A
        # customer row that only the warehouse word found is not a company the customer
        # named, so a report or an order list scoped by it answers a wider question than
        # the one asked. Never empties the scope: with nothing else left, the fail-open
        # rule is to keep what resolved and let the rest of the gate judge it.
        if warehouse_only:
            kept_cust = [
                c
                for c in compatible_entities
                if jsc.js_string(jsc.get(c, "entity_type")).lower() != "customer"
                or jsc.js_string(jsc.get(c, "uuid")) not in warehouse_only
            ]
            if kept_cust:
                compatible_entities = kept_cust

        # ── A RE-SEATED PIN IS IN SCOPE ─────────────────────────────────────
        # R16 (owner round 5, 13 Sep 2026): `gate_passed` was decided further up, on the
        # RESOLVER's rows alone, and the re-seat above runs after it. A turn whose only
        # subject is the pick - "1" against a customer picker - therefore failed the
        # "requires a scoping entity" test with the picked customer sitting in
        # `compatible_entities`, and the customer read "I need at least one filter" over
        # a choice they had just made. The pin IS the scope, by this block's own rule
        # ("A PICK IS AUTHORITATIVE"), so the verdict is re-taken on what is actually in
        # scope. Narrow on purpose: only where the resolver returned NOTHING at all, so
        # every other reason a gate fails is untouched.
        if (
            not gate_passed
            and len(entities) == 0
            and len(compatible_entities) > 0
            and not jsc.truthy(gate_clarification)
        ):
            gate_passed = True
            gate_reason = (
                f"'{domain}' scoped by a pinned pick; the resolver re-resolved nothing"
            )

    # ── document-class precision (container-status S1) ──────────────────────
    # "container status list" returns Packing List (word:list), Stock_List (word:list) AND
    # container_status (word:status), so a contact granted container status is handed three
    # document types (exec 11661198). The parser already named the class it meant.
    # FAIL-OPEN: named nothing, or nothing matches, keeps the full set.
    def _dc_norm(value: Any) -> str:
        return _DC_NON_ALNUM.sub("", jsc.nullish_str(value).lower())

    dc_wanted = {
        w
        for w in (
            _dc_norm(jsc.get(e, "canonical_code"))
            for e in jsc.array(parser.get("entities"))
            if jsc.lower_or_empty(jsc.get(e, "hint")) in ("attachment", "attachment_type")
        )
        if w
    }
    dc_sole_uuid = None
    if len(dc_wanted) > 0:
        dc_type_matches = [e for e in compatible_entities if e.get("entity_type") == "attachment_type"]
        if len(dc_type_matches) > 1:
            # `code` is the slug; display.type_name is the human label. Either may carry
            # the class - Packing List and Stock_List have code null and only a type_name.
            dc_name_by_uuid: dict[Any, Any] = {}
            for m in flat:
                if jsc.truthy(m) and jsc.truthy(jsc.get(m, "uuid")):
                    dc_name_by_uuid[jsc.get(m, "uuid")] = jsc.get(jsc.get(m, "display"), "type_name")
            def _dc_named(e: dict[str, Any]) -> bool:
                return (
                    _dc_norm(e.get("code")) in dc_wanted
                    or _dc_norm(dc_name_by_uuid.get(e.get("uuid"))) in dc_wanted
                )

            # ATTACHMENT-MULTI (2 Oct 2026): judged PER CUSTOMER WORD. A word that resolved to
            # exactly one class already named it and keeps it: judged across every word at
            # once, "photo and technical specifications" kept only the specs - "photo" is not
            # spelt "Product Photos" - so the fetch never asked for the photo. A word that
            # matched several classes keeps only the ones named, and none when it named none
            # ("list" beside "container status"), which is the old rule. A type row no
            # resolution claims shares one group, which is the old whole-set rule exactly.
            dc_token_by_uuid: dict[Any, str] = {}
            for res in jsc.array(resolver.get("resolutions")):
                for m in jsc.array(jsc.get(res, "matches")):
                    if jsc.truthy(m) and jsc.truthy(jsc.get(m, "uuid")):
                        dc_token_by_uuid.setdefault(
                            jsc.get(m, "uuid"), jsc.nullish_str(jsc.get(res, "token")).strip().lower()
                        )
            dc_groups: dict[Any, list[dict[str, Any]]] = {}
            for e in dc_type_matches:
                dc_groups.setdefault(dc_token_by_uuid.get(e.get("uuid")), []).append(e)
            dc_keep = []
            dc_any_named = False
            for group in dc_groups.values():
                named = [e for e in group if _dc_named(e)]
                dc_any_named = dc_any_named or len(named) > 0
                dc_keep.extend(named if named else (group if len(group) == 1 else []))
            if not dc_any_named:
                dc_keep = []
            if len(dc_keep) > 0:
                dc_keep_uuids = {e["uuid"] for e in dc_keep}
                compatible_entities = [
                    e
                    for e in compatible_entities
                    if e.get("entity_type") != "attachment_type" or e.get("uuid") in dc_keep_uuids
                ]
                labels = ", ".join(
                    jsc.js_string(
                        e.get("code")
                        if jsc.truthy(e.get("code"))
                        else dc_name_by_uuid.get(e.get("uuid"))
                    )
                    for e in dc_keep
                )
                gate_reason += f"; document-class narrowed to [{labels}]"
                if len(dc_keep) == 1:
                    dc_sole_uuid = dc_keep[0]["uuid"]

    out = item

    # ── record the narrowing in `resolutions`, not just in `compatible_entities` ──
    # dym-transform picks its did-you-mean candidates straight off `resolutions`, so an
    # un-stamped token gets the file AND "couldn't find it, did you mean ..." in one
    # message. Only claimed when the narrowing was UNAMBIGUOUS.
    if len(collapsed_tokens) > 0 and isinstance(out.get("resolutions"), list):
        for res in out["resolutions"]:
            if not jsc.truthy(res) or jsc.get(res, "resolved") is True:
                continue
            if jsc.nullish_str(jsc.get(res, "token")).strip().lower() not in collapsed_tokens:
                continue
            res["resolved"] = True
            res["ambiguous"] = False
            res["resolved_by"] = "same-code-collapse"
    if dc_sole_uuid and isinstance(out.get("resolutions"), list):
        for res in out["resolutions"]:
            if not jsc.truthy(res) or jsc.get(res, "resolved") is True:
                continue
            ms = jsc.array(jsc.get(res, "matches"))
            hit = jsc.find(ms, lambda m: jsc.truthy(m) and jsc.get(m, "uuid") == dc_sole_uuid)
            if not jsc.truthy(hit):
                continue
            res["resolved"] = True
            res["ambiguous"] = False
            res["matches"] = [hit]  # the class we asked for, not the word-tier spray
            res["resolved_by"] = "document-class-narrowing"

    # ── DROPPED-FILTER GATE ─────────────────────────────────────────────────
    # A question naming two things, where ONE does not exist, was answered as though that
    # filter had never been typed (exec 13626807). SCOPED TO THE PICKER LANE on live: R3
    # says when several things are wrong, say them in the SAME turn. The blocking half
    # (flipping gate_passed on a passing turn) belongs to whoever owns the miss lane.
    if require_specific:
        def _df_n(value: Any) -> str:
            return jsc.nullish_str(value).strip().lower()

        def _df_s(value: Any) -> str:
            # resolve-entity strips separators for product-hint tokens, so a literal
            # comparison against the parser raw decides the user never typed it.
            return _DF_SEPARATORS.sub("", _df_n(value))

        df_typed = [t for t in (_df_n(jsc.get(e, "raw")) for e in jsc.array(parser.get("entities"))) if t]
        df_unres = {_df_n(t) for t in jsc.array(resolver.get("unresolved_tokens"))}

        def _is_missed(r: Any) -> bool:
            t = _df_n(jsc.get(r, "token"))
            if not t or t not in df_unres:
                return False
            if isinstance(jsc.get(r, "matches"), list) and len(jsc.get(r, "matches")) > 0:
                return False  # has candidates -> DYM's job
            ts = _df_s(t)
            for raw in df_typed:
                if raw == t or t in raw or raw in t:
                    return True
                rs = _df_s(raw)
                if rs and ts and (rs == ts or ts in rs or rs in ts):
                    return True
            return False

        df_missed = [
            jsc.js_string(jsc.get(r, "token"))
            for r in jsc.array(resolver.get("resolutions"))
            if _is_missed(r)
        ]
        # A miss only matters if the FILTER was actually lost. When the same axis already
        # carries a resolved entity the user's filter IS applied (exec 13628845).
        df_satisfied = {
            t for t in (_df_n(jsc.get(e, "entity_type")) for e in jsc.array(compatible_entities)) if t
        }

        def _df_ents_of(token: Any) -> list[Any]:
            """EVERY parser entity that spells this token, not the first (clone 14012304).

            A CARRIED duplicate of the same phrase under a different hint decided the
            answer, and first-wins refused a turn that had just been answered correctly.
            """
            t, ts = _df_n(token), _df_s(token)
            out_ents = []
            for e in jsc.array(parser.get("entities")):
                r, rs = _df_n(jsc.get(e, "raw")), _df_s(jsc.get(e, "raw"))
                if r == t or t in r or r in t or (rs and ts and (rs == ts or ts in rs or rs in ts)):
                    out_ents.append(e)
            return out_ents

        def _df_hint_of(token: Any) -> str:
            ents = _df_ents_of(token)
            return _df_n(jsc.get(ents[0], "hint")) if ents else _df_n(None)

        def _df_raw_of(token: Any) -> Any:
            ents = _df_ents_of(token)
            raw = jsc.get(ents[0], "raw") if ents else None
            return raw if jsc.truthy(raw) else token

        def _df_satisfied_by(token: Any) -> bool:
            for e in _df_ents_of(token):
                h = _df_n(jsc.get(e, "hint"))
                if h and h in df_satisfied:
                    return True
            return False

        # An answered DESCRIPTIVE PHRASE is not "an entity that does not exist".
        # resolve-entity squashes a phrase into one letters-then-digits token, i.e.
        # indistinguishable from a real product code, so judge the CUSTOMER'S RAW and only
        # stand down on an OUTCOME: the ranker answered (a) AND the spelling is not
        # code-shaped (b). An unknown CODE still blocks - `mfg6651-gm` fails both.
        df_spec_answered = (
            isinstance(resolver.get("spec_candidates"), list)
            and len(resolver["spec_candidates"]) > 0
        )

        def _df_not_code_shaped(raw: Any) -> bool:
            v = jsc.nullish_str(raw).strip()
            return len(v) > 0 and not (
                bool(_HAS_DIGIT.search(v)) and bool(_CODE_SHAPED.match(v))
            )

        df_lost = [
            tok
            for tok in df_missed
            if not _df_satisfied_by(tok)
            and not (df_spec_answered and _df_not_code_shaped(_df_raw_of(tok)))
        ]
        if len(df_lost) > 0:
            out["dropped_filter_tokens"] = df_lost
            # R3: when a picker is ALREADY being shown, the miss rides in the SAME message.
            if jsc.truthy(gate_clarification):
                df_lines = []
                for t in df_lost:
                    h = _df_hint_of(t)
                    df_lines.append(f'"{jsc.js_string(_df_raw_of(t))}"' + (f" ({h.replace('_', ' ')})" if h else ""))
                gate_clarification = f"Couldn't find: {', '.join(df_lines)}.\n\n{gate_clarification}"
                out["gate_clarification"] = gate_clarification

    if cust_pin_kept:
        out["customer_pin_kept"] = True
    out["gate_passed"] = gate_passed
    out["require_specific"] = require_specific
    # RS-9 Fix 5: expose the array `gate_clarification`'s numbered lines were flattened
    # from, UNCHANGED by anything after that render, so a reader can correlate "line N" to
    # "the Nth candidate's uuid". `compatible_entities` cannot serve this - when
    # `require_specific` it is a separately-filtered copy of `entities`, not the picker's
    # own render order.
    out["specific_options"] = specific_options

    # ── #9 multi-company routing ────────────────────────────────────────────
    # Derive the escalation team from the RESOLVED ENTITY'S company instead of guessing
    # from access levels. #16: Cabana is a BRAND under the Sorento COMPANY, so brand names
    # a team first and company is the fallback.
    #
    # `_brand_tok` is INERT until the CRM emits brand on a product row: surveyed 35
    # resolutions across every match path, not one carries `display.brand`.
    def _brand_tok(m: Any) -> str | None:
        b = jsc.get(jsc.get(m, "display"), "brand")
        if not jsc.truthy(b):
            return None
        if isinstance(b, dict):
            s = f"{jsc.get(b, 'brand_name') or ''} {jsc.get(b, 'brand_code') or ''}".lower()
        else:
            s = jsc.js_string(b).lower()
        return next((v for v in VALID_BRANDS if v in s), None)  # unknown brand -> company

    # Promotion rows carry no `brand`, so an all-Cabana list fell through to company - and
    # company is "Sorento" for every Cabana product by definition. The parser already
    # identifies the brand as an ENTITY. Order: row brand > customer-named brand > company.
    parser_brand = None
    for e in jsc.array(parser.get("entities")):
        if jsc.lower_or_empty(jsc.get(e, "hint")) != "brand":
            continue
        v = next(
            (x for x in VALID_BRANDS if x in jsc.lower_or_empty(jsc.get(e, "raw"))),
            None,
        )
        if v:
            parser_brand = v
            break
    brands_seen: list[str] = []
    for m in flat:
        tok = (
            _brand_tok(m)
            or parser_brand
            or next(
                (v for v in VALID_BRANDS if v in jsc.lower_or_empty(jsc.get(m, "company_name"))),
                None,
            )
        )
        if tok and tok not in brands_seen:
            brands_seen.append(tok)
    # Only when UNAMBIGUOUS - a mixed-company set must not collapse to a first match.
    out["resolved_company"] = brands_seen[0] if len(brands_seen) == 1 else None
    # F-R4-3: migration 371 MERGED the brand-suffixed T1 rows, so
    # `marketing_promotion_<brand>` names no team the CRM can resolve. Collapsed at source;
    # the brand axis survives on routing_brand / routing_companies below.
    out["company_team"] = (
        "marketing_promotion" if (domain == "promotion" and len(brands_seen) == 1) else None
    )
    out["resolved_companies"] = brands_seen

    # ── brand-company-routing: routing axes for roster + assignment ──
    # WE OFFER THE TEAMS OF THE COMPANIES WE SAID WE SEARCHED (exec 13743718). The axis is
    # the SAME set the miss renderer's `_searchedCos` renders - every compatible entity
    # that becomes a tool id - minus the two types that never do. The two lists must stay
    # identical; `routing-axis-live-spine.test.js` asserts that byte-identity.
    compat_uuids = {e.get("uuid") for e in compatible_entities}
    rows = [
        m
        for m in flat
        if jsc.truthy(m)
        and jsc.truthy(jsc.get(m, "uuid"))
        and jsc.get(m, "uuid") in compat_uuids
        and jsc.truthy(jsc.get(m, "company_id"))
        and jsc.js_string(jsc.get(m, "entity_type")) not in NO_TOOL_ID
    ]

    def _bc(m: Any) -> str | None:
        b = jsc.get(jsc.get(m, "display"), "brand")
        c = (jsc.get(b, "brand_code") if isinstance(b, dict) else b) if jsc.truthy(b) else None
        return jsc.js_string(c).strip().lower() if jsc.truthy(c) else None

    # A CUSTOMER-FACING LABEL FOR EACH ROUTING SUBJECT: `codes` is canonical_code, right
    # for a product and wrong for a customer, where it is an internal debtor code (exec
    # 13687248). Still INERT on live - nothing reads `.labels` - and kept as evidence.
    def _co_label(m: Any) -> str:
        display = jsc.get(m, "display") or {}
        if jsc.js_string(jsc.get(m, "entity_type")).lower() == "customer":
            name = jsc.js_string(
                jsc.get(display, "customer_name") or jsc.get(display, "debtor_name") or ""
            ).strip()
            return name or jsc.js_string(jsc.get(m, "canonical_code") or "")
        return jsc.js_string(jsc.get(m, "canonical_code") or "")

    by_co: dict[Any, dict[str, Any]] = {}
    for m in rows:
        company_id = jsc.get(m, "company_id")
        if not jsc.truthy(company_id):
            continue
        group = by_co.get(company_id) or {
            "company_id": company_id,
            "company_name": jsc.get(m, "company_name") or None,
            "brands": [],
            "codes": [],
            "labels": [],
        }
        b = _bc(m)
        if b and b not in group["brands"]:
            group["brands"].append(b)
        code = jsc.get(m, "canonical_code")
        if jsc.truthy(code) and code not in group["codes"]:
            group["codes"].append(code)
        label = _co_label(m)
        if label and label not in group["labels"]:
            group["labels"].append(label)
        by_co[company_id] = group

    all_brands = list(dict.fromkeys(b for b in (_bc(m) for m in rows) if b))
    # Brand unknown stays unknown: the resolved rows' brand only when unambiguous, else
    # the customer's OWN stated brand only when they named exactly one. No access-level
    # guess - a null brand makes the CRM resolve from the wider company-bounded pool.
    qb = (
        jsc.js_string(parser["query_brands"][0]).lower()
        if isinstance(parser.get("query_brands"), list) and len(parser["query_brands"]) == 1
        else None
    )
    out["routing_brand"] = all_brands[0] if len(all_brands) == 1 else (qb or None)
    out["routing_brand_source"] = (
        "resolved" if len(all_brands) == 1 else ("stated" if qb else None)
    )
    # per-company brand = that company's OWN row brand; the global routing_brand is only
    # inherited when there is a single company.
    cos = list(by_co.values())
    out["routing_companies"] = sorted(
        [
            {
                "company_id": g["company_id"],
                "company_name": g["company_name"],
                "brand_code": (
                    g["brands"][0]
                    if len(g["brands"]) == 1
                    else ((out["routing_brand"] or None) if len(cos) == 1 else None)
                ),
                "codes": list(g["codes"]),
                "labels": list(g["labels"]),
            }
            for g in cos
        ],
        key=cmp_to_key(
            lambda a, b: _locale_compare(
                jsc.js_string(a["company_name"]), jsc.js_string(b["company_name"])
            )
        ),
    )
    out["routing_company"] = (
        out["routing_companies"][0]["company_id"] if len(out["routing_companies"]) == 1 else None
    )

    # ── Q23 - a stated access level the contact does not hold ───────────────
    # Say so, then still show what they DO have. F5: `Aggregate` (and `tier_gate`)
    # only run on the promotion lane (`resolve_gate.run`'s own `entry == "access_
    # check"` step, which only ever fires for `branch_kind == "check_promotion"`) -
    # `tg is not None` IS that same condition, read directly off whether the node
    # actually ran, rather than off `domain == "promotion"` (review round 2, 26 Sep
    # 2026, security follow-up): a MIXED order+promotion turn sends `domain_hint:
    # "order"` (the order half won the parser's own domain read) through this exact
    # entry regardless, and the literal-string check discarded `tier_gate`'s own,
    # correctly-computed `brand_gate_empty` for that shape - the SAME "You don't
    # have access to Cabana promotions" the bare-promotion turn gets, silently
    # replaced with "gate closed, no notice at all". A turn where tier_gate never
    # ran (`tg is None`, the ELSE branch below) is unaffected either way.
    tg = tier_gate if isinstance(tier_gate, dict) else None
    if tg is not None:
        stated_t = tg["tier_stated"] if isinstance(tg.get("tier_stated"), list) else []
        ent_t = tg["entitled_tiers"] if isinstance(tg.get("entitled_tiers"), list) else []
        held_t = [t for t in stated_t if t in ent_t]
        tier_label = {"dealer": "dealer", "office": "office", "end_user": "end user"}
        out["access_denied_levels"] = stated_t if (len(stated_t) > 0 and len(held_t) == 0) else []
        out["brand_gate_empty"] = tg.get("brand_gate_empty") is True
        # F1: NOTICE keys on brand_unheld, SUPPRESSION on brand_gate_empty. One flag
        # answering both told a brandless contact what they have and then showed nothing.
        out["brand_unheld"] = tg.get("brand_unheld") is True
        if len(out["access_denied_levels"]) > 0:
            names = ", ".join(tier_label.get(t, jsc.js_string(t)) for t in stated_t)
            out["access_notice"] = (
                f"You don't have access to {names} promotions. Here's what you do have:"
            )
        elif out["brand_unheld"]:
            # R5/TA-11: tier-gate sent [] to get-results (FAIL-CLOSED), so the not-found
            # path renders and prepends this notice, which is the WHY. F5: a flat denial is
            # the ONLY correct copy - brand_unheld now implies the gate closed.
            brands = tg["query_brands"] if isinstance(tg.get("query_brands"), list) else []
            out["access_notice"] = (
                f"You don't have access to {', '.join(jsc.js_string(b) for b in brands)} promotions."
            )
        else:
            out["access_notice"] = ""
    else:
        agg_ok = aggregate is not None
        entitled = jsc.get(aggregate, "name") or [] if agg_ok else []
        stated = (
            [
                s
                for s in (
                    jsc.js_string(a if jsc.truthy(a) else "").strip()
                    for a in jsc.array(parser.get("access_levels"))
                )
                if s
            ]
            if (domain == "promotion" and agg_ok)
            else []
        )
        lc = [jsc.js_string(a).lower() for a in jsc.array(entitled)]
        held = [a for a in stated if a.lower() in lc]
        out["access_denied_levels"] = stated if (len(stated) > 0 and len(held) == 0) else []
        out["access_notice"] = (
            f"You don't have access to {', '.join(stated)} promotions. Here's what you do have:"
            if len(out["access_denied_levels"]) > 0
            else ""
        )
        out["brand_gate_empty"] = False
        out["brand_unheld"] = False

    out["gate_reason"] = gate_reason
    out["gate_clarification"] = gate_clarification  # '' when nothing to ask
    out["compatible_entities"] = compatible_entities
    if cust_probe_entities and len(cust_probe_entities) > 0:
        out["customer_probe_entities"] = cust_probe_entities
    if cust_families and len(cust_families) > 0:
        out["picker_families"] = cust_families
    # `{domain, allowed_lookup: ALLOWED[domain], entities_count}`. An unmapped domain
    # makes `allowed_lookup` UNDEFINED, and `JSON.stringify` DROPS an undefined value
    # rather than writing null - so the key is ABSENT on those turns, which 3 of the 213
    # gate captures show. Reproduced by omission, not by a null.
    gate_debug: dict[str, Any] = {"domain": domain}
    if domain in ALLOWED:
        gate_debug["allowed_lookup"] = ALLOWED[domain]
    gate_debug["entities_count"] = len(entities)
    if incompatible_only:
        gate_debug["incompatible_only"] = incompatible_only
    out["gate_debug"] = gate_debug
    return out
